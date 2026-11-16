# SPDX-License-Identifier: MIT
# houdini_render (fixed program, runs in the open Houdini): checks the request, writes a working copy of the open
# scene (hscript mwrite -n: the open file keeps its name and its own file is untouched) or takes the saved file,
# then starts a detached background hython running render_job.py on it: a ROP render (frames, to the exact output
# when given) or a PDG cook of a TOP node. Returns at once; the job writes a log the plugin follows:
#   OPENCODE_RENDER {...}                 what is rendered (once, at the start)
#   OPENCODE_PROGRESS {"done": n, "total": m, ...}
#   OPENCODE_DONE {"output": ..., "files": [...], ...}   or   OPENCODE_FAILED "<message>"
# PARAMS: {"rop" | "top", "output"?, "frames"?, "size"?, "source"?: "copy" | "saved", "overwrite"?}
# JOB: render_job.py, prepended by the plugin.
import json
import os
import re
import subprocess
import tempfile
import time
import uuid

import hou

P = PARAMS  # noqa: F821
OUTPUT_PARMS = ("picture", "vm_picture", "outputimage", "sopoutput", "lopoutput", "filename", "copoutput",
                "outputfile", "file")
RUN = re.compile(r"#+")


def license_name():
    try:
        return str(hou.licenseCategory()).rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        return "unknown"


if bool(P.get("rop")) == bool(P.get("top")):
    raise ValueError("give rop (a ROP node path, e.g. /out/karma1) or top (a TOP node or TOP network path), one of them")
current = hou.hipFile.path()
never_saved = hou.hipFile.isNewFile()
base_dir = os.path.dirname(current) if not never_saved else None
kind = "render" if P.get("rop") else "pdg"
path = P.get("rop") or P.get("top")
node = hou.node(path)
if node is None:
    raise ValueError("no node %r" % path)

facts = {"license": license_name()}
if facts["license"] in ("Apprentice", "ApprenticeHD", "Education"):
    facts["license_note"] = ("%s: rendered images are at most 1920x1080 (larger sizes are scaled down, aspect kept) and "
                             "carry the Houdini logo in their bottom-right corner; files are non-commercial" % facts["license"])


def frame_list(spec):
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        values = [spec, spec, 1]
    elif isinstance(spec, list) and 2 <= len(spec) <= 3 and all(isinstance(v, (int, float)) for v in spec):
        values = list(spec) + ([1] if len(spec) == 2 else [])
    else:
        raise ValueError("frames: a frame, [start, end] or [start, end, step]")
    start, end, step = (float(v) for v in values)
    if end < start or step <= 0:
        raise ValueError("frames: end before start, or a step that is not positive")
    count = int((end - start) / step + 1e-6) + 1
    if count > 10000:
        raise ValueError("frames: %d frames, at most 10000" % count)
    return [(int(f) if float(f).is_integer() else f) for f in (start + i * step for i in range(count))]


output = output_pattern = out_parm = None
targets, frames = [], []
if kind == "render":
    if not isinstance(node, hou.RopNode):
        raise ValueError("rop: %s is a %s, not a ROP (render nodes live in /out or a ROP network)" % (path, node.type().nameWithCategory()))
    out_parm = next((n for n in OUTPUT_PARMS if node.parm(n) is not None), None)
    frames = frame_list(P.get("frames"))
    if frames is None:
        trange = node.parm("trange")
        if trange is None or trange.eval() == 0:
            frames = [int(hou.frame()) if float(hou.frame()).is_integer() else hou.frame()]
        else:
            frames = frame_list([node.evalParm("f1"), node.evalParm("f2"), node.evalParm("f3") or 1])
    if P.get("output"):
        output = P["output"]
        if not os.path.isabs(output):
            if base_dir is None:
                raise ValueError("output %r is relative and the scene was never saved: give an absolute path" % output)
            output = os.path.join(base_dir, output)
        output = os.path.normpath(output).replace("\\", "/")
        runs = RUN.findall(os.path.basename(output))
        if len(runs) > 1:
            raise ValueError("output: one run of # marks the frame number")
        if len(frames) > 1 and not runs and "$F" not in output:
            raise ValueError("output: %d frames need a frame number in the name: a # run (shot_####.exr) or $F4" % len(frames))
        if out_parm is None:
            raise ValueError("rop: %s (%s) has no output parameter this tool knows (%s)" % (path, node.type().name(), ", ".join(OUTPUT_PARMS)))
        output_pattern = output.replace(runs[0], "$F%d" % len(runs[0])) if runs else output
        targets = [hou.expandStringAtFrame(output_pattern, f) for f in frames]
    elif out_parm is not None:
        targets = [node.parm(out_parm).evalAtFrame(f) for f in frames]
        output = node.parm(out_parm).unexpandedString()
    targets = [os.path.normpath(t) for t in targets if t]
    if not targets:
        facts["targets_note"] = "the ROP names no output file here (a USD render settings prim may): the files are read back from the log"
    taken = [t for t in targets if os.path.exists(t)]
    if taken and not P.get("overwrite"):
        raise ValueError("%s already exists (%d file(s)): pass overwrite true or name another output" % (taken[0], len(taken)))
    for t in targets[:1]:
        os.makedirs(os.path.dirname(t), exist_ok=True)
    size = P.get("size")
    if size is not None and (not isinstance(size, list) or len(size) != 2 or not all(isinstance(v, int) and 4 <= v <= 16384 for v in size)):
        raise ValueError("size: [width, height], 4 to 16384")
else:
    target = node
    if node.childTypeCategory() is not None and node.childTypeCategory().name() == "Top":
        target = node.displayNode() if hasattr(node, "displayNode") else None
        if target is None:
            outputs = [c for c in node.children() if c.type().name() == "output"]
            target = outputs[0] if outputs else None
        if target is None:
            raise ValueError("top: %s has no display or output node to cook: name a TOP node" % path)
    if target.type().category().name() != "Top":
        raise ValueError("top: %s is a %s, not a TOP node or TOP network" % (path, target.type().nameWithCategory()))
    node = target
    if P.get("output") or P.get("frames") or P.get("size"):
        raise ValueError("top: output, frames and size are a ROP's; a PDG cook writes what its nodes say")

source = P.get("source", "copy")
if source not in ("copy", "saved"):
    raise ValueError("source: copy (a working copy of the open scene, the default) or saved (the file as saved)")
stamp = time.strftime("%Y%m%d-%H%M%S")
# One work folder per job (several can start in the same second from the same Houdini): never shared.
work = os.path.join(tempfile.gettempdir(), "opencode-houdini-render", "%s-%d-%s" % (stamp, os.getpid(), uuid.uuid4().hex[:12]))
os.makedirs(work)
working_copy = None
if source == "saved":
    if never_saved or not os.path.isfile(current):
        raise ValueError("source saved: the scene was never saved (houdini_save first, or source copy)")
    hip = current
    if hou.isUIAvailable() and hou.hipFile.hasUnsavedChanges():
        facts["unsaved_changes"] = "the open scene has changes the saved file lacks: they are not rendered"
else:
    # The copy keeps the scene's file name, so $HIPNAME expands as in the open scene; the job sets $HIP back.
    working_copy = os.path.join(work, os.path.basename(current) or "untitled.hipnc").replace("\\", "/")
    out_err = hou.hscript('mwrite -n "%s"' % working_copy)
    if not os.path.isfile(working_copy):
        raise RuntimeError("the working copy was not written: %s" % " ".join(out_err).strip()[:300])
    hip = working_copy

job = {"kind": kind, "hip": hip, "node": node.path(), "out_parm": out_parm, "output_pattern": output_pattern,
       "frames": frames, "targets": targets, "size": P.get("size"), "work": work,
       # $HIP of the copy is its work folder: outputs relative to $HIP still go next to the scene.
       "hip_dir": base_dir}
with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as fh:
    json.dump(job, fh)
script = os.path.join(work, "render_job.py")
with open(script, "w", encoding="utf-8") as fh:
    fh.write(JOB)  # noqa: F821 - render_job.py, prepended by the plugin
exe = os.path.join(hou.getenv("HFS") or "", "bin", "hython.exe" if os.name == "nt" else "hython")
if not os.path.isfile(exe):
    raise RuntimeError("no hython at %s" % exe)
log_path = os.path.join(work, "render.log")
env = {k: v for k, v in os.environ.items() if k != "OPENCODE_HOUDINI_OFFER_KEY"}
env["OPENCODE_HOUDINI_ROLE"] = "render"  # never offered to OpenCode
env["PYTHONUNBUFFERED"] = "1"
log = open(log_path, "wb")
kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True, "env": env, "cwd": work}
if os.name == "nt":
    kwargs["creationflags"] = 0x08000000 | 0x00000200  # no console window, own process group
else:
    kwargs["start_new_session"] = True
process = subprocess.Popen([exe, script, os.path.join(work, "job.json")], **kwargs)
log.close()
result = {"pid": process.pid, "log": log_path, "work": work, "kind": kind, "output": output, "targets": targets,
          "frames": frames, "start": frames[0] if frames else None, "end": frames[-1] if frames else None,
          "node": node.path(), "node_type": node.type().nameWithCategory(), "source": source, "hip": hip,
          "facts": facts, **({"working_copy": working_copy} if working_copy else {})}
