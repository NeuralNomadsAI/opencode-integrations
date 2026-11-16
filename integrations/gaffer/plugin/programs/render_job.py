# SPDX-License-Identifier: MIT
# The background job (written next to its log by render.py, run as: gaffer python render_job.py job.json): loads
# the working copy (or saved file) into a fresh ScriptNode, overrides the output and size in memory only, then
# dispatches the task node and its upstream tasks for the frames with a LocalDispatcher in this process, and prints
# for the plugin:
#   OPENCODE_RENDER {...}, OPENCODE_PROGRESS {"done": n, "total": m, ...}, then OPENCODE_DONE {...} or
#   OPENCODE_FAILED "<message>".
import json
import os
import sys
import threading
import time
import traceback

import Gaffer
import GafferDispatch
import GafferImage
import GafferScene
import IECore
import IECoreScene
import imath

with open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(sys.argv[0]), "job.json"), encoding="utf-8") as fh:
    o = json.load(fh)


def mark(key, value):
    sys.stdout.write("OPENCODE_%s %s\n" % (key, json.dumps(value)))
    sys.stdout.flush()


def at_frame(script, frame):
    context = Gaffer.Context(script.context())
    context.setFrame(frame)
    return context


def insert_before(script, node, name, processor):
    """processor goes between the Render node and its input scene, in this process only."""
    script[name] = processor
    processor["in"].setInput(node["in"].getInput())
    node["in"].setInput(processor["out"])
    return processor


def file_outputs(script, node, frame):
    with at_frame(script, frame):
        g = node["in"]["globals"].getValue()
    return {k[len("output:"):]: g[k] for k in g.keys()
            if k.startswith("output:") and isinstance(g[k], IECoreScene.Output) and g[k].getType() != "ieDisplay"}


def render():
    script = Gaffer.ScriptNode()
    script["fileName"].setValue(o["file"].replace("\\", "/"))
    with IECore.CapturingMessageHandler() as captured:
        script.load(continueOnError=True)
    load_warnings = ["%s: %s" % (m.context, m.message) for m in captured.messages if m.level <= IECore.Msg.Level.Warning][:10]
    node = script.descendant(o["node"])
    if node is None:
        raise RuntimeError("the file read has no node %s" % o["node"])
    rendering = isinstance(node, GafferScene.Render) and node["mode"].getValue() == GafferScene.Render.Mode.RenderMode
    used = {"node": o["node"], "type": "%s.%s" % (type(node).__module__.split(".")[0], type(node).__name__),
            "frames": [o["frames"][0], o["frames"][-1]], "count": len(o["frames"]),
            # This process itself: on Windows the task's pid is the cmd.exe that started the launcher.
            "pid": os.getpid()}
    if load_warnings:
        used["load_warnings"] = [w[:300] for w in load_warnings]
    if o["output_pattern"]:
        if o["out_kind"] == "fileName":
            node["fileName"].setValue(o["output_pattern"])
        else:
            original = file_outputs(script, node, o["frames"][0])[o["out_name"]]
            drop = insert_before(script, node, "opencodeRenderNoOutput", GafferScene.DeleteOutputs())
            drop["names"].setValue(o["out_name"])
            add = insert_before(script, node, "opencodeRenderOutput", GafferScene.Outputs())
            add.addOutput(o["out_name"], IECoreScene.Output(o["output_pattern"], original.getType(), original.getData(), original.parameters()))
    if o["size"]:
        options = insert_before(script, node, "opencodeRenderSize", GafferScene.StandardOptions())
        for name, value in (("render:resolution", imath.V2i(*o["size"])), ("render:resolutionMultiplier", 1.0)):
            options["options"][name]["enabled"].setValue(True)
            options["options"][name]["value"].setValue(value)
        used["size"] = o["size"]
    targets = []
    for frame in o["frames"]:
        with at_frame(script, frame) as context:
            if rendering:
                names = [out.getName() for out in file_outputs(script, node, frame).values()]
            elif "fileName" in node and isinstance(node["fileName"], Gaffer.StringPlug):
                names = [node["fileName"].getValue()]
            else:
                names = []
            targets += [os.path.abspath(context.substitute(n)) for n in names if n]
    if rendering:
        with at_frame(script, o["frames"][0]):
            used["renderer"] = node["resolvedRenderer"].getValue()
    if o["targets"] and targets != o["targets"]:
        used["targets_differ"] = {"expected": o["targets"][:3], "here": targets[:3]}
    used["targets"] = targets[:3] + (["... (%d)" % len(targets)] if len(targets) > 3 else [])
    mark("RENDER", used)
    total = len(o["frames"])
    state = {"done": 0}
    lock = threading.Lock()

    def progress(count):
        with lock:
            if count > state["done"]:
                state["done"] = min(count, total)
                mark("PROGRESS", {"done": state["done"], "total": total})

    stop = threading.Event()
    begun = time.time()
    per_frame = max(1, len(targets) // total) if targets else 1

    def watch():
        # The files say how far it is: a LocalDispatcher in this process reports no frame events.
        while not stop.wait(1.0):
            written = sum(1 for t in targets if os.path.isfile(t) and os.path.getmtime(t) >= begun - 1)
            progress(written // per_frame)

    dispatcher = GafferDispatch.LocalDispatcher()
    dispatcher["jobsDirectory"].setValue(os.path.join(o["work"], "jobs").replace("\\", "/"))
    dispatcher["framesMode"].setValue(GafferDispatch.Dispatcher.FramesMode.CustomRange)
    dispatcher["frameRange"].setValue(",".join(str(f) for f in o["frames"]))
    dispatcher["executeInBackground"].setValue(False)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        with at_frame(script, o["frames"][0]):
            dispatcher.dispatch([node])
    finally:
        stop.set()
        watcher.join(2)
    missing = [t for t in targets if not os.path.isfile(t)]
    if missing:
        raise RuntimeError("%d of %d file(s) were not written, first %s" % (len(missing), len(targets), missing[0]))
    progress(total)
    done = {"output": o["output_pattern"] or (targets[0] if len(targets) == 1 else None), "files": targets,
            "bytes": sum(os.path.getsize(t) for t in targets)}
    try:
        reader = GafferImage.ImageReader()
        reader["fileName"].setValue(targets[0].replace("\\", "/"))
        fmt = reader["out"].format()
        done["image_size"] = [fmt.width(), fmt.height()]
        if o["size"] and done["image_size"] != list(o["size"]):
            done["size_note"] = "asked %dx%d, written %dx%d" % (o["size"][0], o["size"][1], fmt.width(), fmt.height())
    except Exception:  # noqa: BLE001 - not an image (a scene, USD, a text file) or no file named
        pass
    mark("DONE", done)


code = 0
try:
    render()
except Exception as ex:  # noqa: BLE001
    mark("FAILED", ("%s: %s" % (type(ex).__name__, ex))[:1500] + "\n" + traceback.format_exc()[-1500:])
    code = 1
sys.stdout.flush()
# Leave at once: the outcome is written, nothing in the exit handlers can lose it.
os._exit(code)
