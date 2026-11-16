# SPDX-License-Identifier: MIT
# The background hython's script (written next to its log by render.py, run as: hython render_job.py job.json):
# loads the working copy (or saved file), sets $HIP back to the scene's folder, overrides the output, frames and
# size in memory only, then renders the ROP's frames in one call or cooks the TOP node, and prints for the plugin:
#   OPENCODE_RENDER {...}, OPENCODE_PROGRESS {"done": n, "total": m, ...}, then OPENCODE_DONE {...} or
#   OPENCODE_FAILED "<message>".
import json
import os
import sys
import threading
import time
import traceback

import hou

with open(sys.argv[1], encoding="utf-8") as fh:
    o = json.load(fh)


def mark(key, value):
    sys.stdout.write("OPENCODE_%s %s\n" % (key, json.dumps(value)))
    sys.stdout.flush()


def set_parm(node, name, value):
    p = node.parmTuple(name) if isinstance(value, (list, tuple)) else node.parm(name)
    if p is None:
        return False
    p.set(value)
    return True


def apply_size(rop, size):
    kind = rop.type().name().split("::")[0]
    w, h = size
    if kind == "opengl":
        return set_parm(rop, "tres", True) and set_parm(rop, "res", (w, h))
    if kind == "karma":
        return set_parm(rop, "resolutionx", w) and set_parm(rop, "resolutiony", h)
    if kind == "usdrender":
        return set_parm(rop, "override_res", "specific") and set_parm(rop, "res_user", (w, h))
    if kind == "ifd":
        return set_parm(rop, "override_camerares", True) and set_parm(rop, "res_fraction", "specific") and set_parm(rop, "res_override", (w, h))
    return False


def render():
    rop = hou.node(o["node"])
    if rop is None:
        raise RuntimeError("the file read has no node %s" % o["node"])
    used = {"node": rop.path(), "type": rop.type().nameWithCategory(), "frames": [o["frames"][0], o["frames"][-1]],
            "count": len(o["frames"]), "license": str(hou.licenseCategory()).rsplit(".", 1)[-1]}
    if o["output_pattern"]:
        rop.parm(o["out_parm"]).set(o["output_pattern"])
    if o["size"]:
        used["size_applied"] = apply_size(rop, o["size"])
        used["size"] = o["size"]
    if rop.parm("trange") is not None:
        rop.parm("trange").set(1)
        step = (o["frames"][1] - o["frames"][0]) if len(o["frames"]) > 1 else 1
        rop.parmTuple("f").deleteAllKeyframes()
        rop.parmTuple("f").set((o["frames"][0], o["frames"][-1], step))
    targets = [rop.parm(o["out_parm"]).evalAtFrame(f) for f in o["frames"]] if o["out_parm"] else []
    targets = [os.path.normpath(t) for t in targets if t]
    if o["targets"] and targets != o["targets"]:
        used["targets_differ"] = {"expected": o["targets"][:3], "here": targets[:3]}
    used["targets"] = targets[:3] + (["... (%d)" % len(targets)] if len(targets) > 3 else [])
    mark("RENDER", used)
    total = len(o["frames"])
    state = {"done": 0, "events": 0}
    lock = threading.Lock()

    def progress(count, frame=None):
        with lock:
            if count > state["done"]:
                state["done"] = min(count, total)
                mark("PROGRESS", dict({"done": state["done"], "total": total}, **({"frame": frame} if frame is not None else {})))

    def on_event(node, event, when):
        # Per-frame events (OpenGL, Mantra); Karma renders through husk without them: the files say it then.
        if event == hou.ropRenderEventType.PostFrame:
            state["events"] += 1
            frame = round(hou.timeToFrame(when), 3)
            progress(state["events"], int(frame) if float(frame).is_integer() else frame)

    stop = threading.Event()
    begun = time.time()

    def watch():
        while not stop.wait(1.0):
            written = 0
            for t in targets:
                try:
                    if os.path.getmtime(t) >= begun - 1:
                        written += 1
                except OSError:
                    pass
            progress(written)

    rop.addRenderEventCallback(on_event)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    step = (o["frames"][1] - o["frames"][0]) if len(o["frames"]) > 1 else 1
    try:
        rop.render(frame_range=(o["frames"][0], o["frames"][-1], step), verbose=False, output_progress=False)
    except hou.OperationFailed as ex:
        messages = list(rop.errors())
        raise RuntimeError(("%s %s" % (ex, " ".join(messages))).strip()[:1500])
    finally:
        stop.set()
        watcher.join(2)
    if rop.errors():
        raise RuntimeError("; ".join(" ".join(m.split()) for m in rop.errors())[:1500])
    missing = [t for t in targets if not os.path.isfile(t)]
    if missing:
        raise RuntimeError("%d of %d file(s) were not written, first %s" % (len(missing), len(targets), missing[0]))
    progress(total)
    warnings = [" ".join(m.split())[:300] for m in rop.warnings()][:5]
    done = {"output": o["output_pattern"] or (rop.parm(o["out_parm"]).unexpandedString() if o["out_parm"] else None),
            "files": targets, "bytes": sum(os.path.getsize(t) for t in targets), **({"warnings": warnings} if warnings else {})}
    try:
        # The size really written (Apprentice scales larger renders down to 1920x1080).
        from PIL import Image
        with Image.open(targets[0]) as image:
            done["image_size"] = list(image.size)
        if o["size"] and done["image_size"] != list(o["size"]):
            done["size_note"] = "asked %dx%d, written %dx%d (%s)" % (o["size"][0], o["size"][1], done["image_size"][0], done["image_size"][1], used["license"])
    except Exception:  # noqa: BLE001 - not an image PIL reads (EXR, geometry, USD)
        pass
    mark("DONE", done)


def cook():
    top = hou.node(o["node"])
    if top is None:
        raise RuntimeError("the file read has no node %s" % o["node"])
    mark("RENDER", {"node": top.path(), "type": top.type().nameWithCategory(), "kind": "pdg"})
    top.cookWorkItems(block=False)
    context = top.getPDGGraphContext()
    last = None
    while True:
        pdg_node = top.getPDGNode()
        items = list(pdg_node.workItems) if pdg_node is not None else []
        states = {}
        for item in items:
            key = str(item.state).rsplit(".", 1)[-1]
            states[key] = states.get(key, 0) + 1
        finished = sum(v for k, v in states.items() if k in ("CookedSuccess", "CookedFail", "CookedCancel", "CookedCache"))
        now = (finished, len(items))
        if now != last:
            mark("PROGRESS", {"done": finished, "total": len(items), "states": states})
            last = now
        if context is None or not context.cooking:
            break
        time.sleep(0.5)
    failed = [i for i in items if "Fail" in str(i.state)]
    files = []
    for item in items:
        for f in getattr(item, "outputFiles", []) or []:
            files.append(getattr(f, "path", str(f)))
    if failed or top.errors():
        first = failed[0] if failed else None
        detail = (" first failed work item %s" % first.name) if first is not None else ""
        raise RuntimeError(("%d work item(s) failed;%s %s" % (len(failed), detail, "; ".join(top.errors()))).strip()[:1500])
    mark("DONE", {"output": None, "files": files[:500], "work_items": len(items), **({"files_more": len(files) - 500} if len(files) > 500 else {})})


code = 0
try:
    hou.hipFile.load(o["hip"], suppress_save_prompt=True, ignore_load_warnings=True)
    if o.get("hip_dir"):
        hou.putenv("HIP", o["hip_dir"].replace("\\", "/"))
        hou.hscript("varchange")
    render() if o["kind"] == "render" else cook()
except Exception as ex:  # noqa: BLE001
    mark("FAILED", ("%s: %s" % (type(ex).__name__, ex))[:1500] + "\n" + traceback.format_exc()[-1500:])
    code = 1
sys.stdout.flush()
# hython can crash in its own exit handlers after a render: the outcome is written, leave at once.
os._exit(code)
