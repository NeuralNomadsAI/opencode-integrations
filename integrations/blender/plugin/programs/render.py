# SPDX-License-Identifier: GPL-3.0-or-later
# blender_render (fixed program, runs in the open Blender): checks the request, settles unsaved changes, then
# starts a separate background Blender on the saved file (or a working copy) that renders to the EXACT output and
# writes a log the plugin follows. The open document is not changed (except "unsaved": "save", which saves it).
# As OpenCode Link 0.3.8's render.render_animation, without renaming the output.
# PARAMS: {"output", "frame"?, "frame_start"?, "frame_end"?, "scene"?, "source"?, "resolution"?, "samples"?,
#          "engine"?, "unsaved"?, "overwrite"?, "quality"?}
import json
import os
import re
import subprocess
import tempfile
import time
import uuid

import bpy

P = PARAMS  # noqa: F821
ENGINES = {"workbench": ["BLENDER_WORKBENCH"], "eevee": ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"], "cycles": ["CYCLES"]}
QUALITY = {"LOWEST": 32, "VERYLOW": 28, "LOW": 25, "MEDIUM": 23, "HIGH": 20, "PERC_LOSSLESS": 17, "LOSSLESS": 0}
RUN = re.compile(r"#+")

path = bpy.data.filepath
scene = bpy.data.scenes.get(P["scene"]) if P.get("scene") else bpy.context.scene
if scene is None:
    raise ValueError("no scene %r; scenes: %s" % (P.get("scene"), [s.name for s in bpy.data.scenes]))
base_dir = os.path.dirname(path) if path else None
output = P["output"]
if not os.path.isabs(output):
    if base_dir is None:
        raise ValueError("output %r is relative and the file was never saved: give an absolute path" % output)
    output = os.path.join(base_dir, output)
output = os.path.normpath(output)
ext = os.path.splitext(output)[1].lower()
runs = RUN.findall(os.path.basename(output))
if ext not in (".png", ".mp4"):
    raise ValueError("output must end with .png (an image, or frames with a # run such as shot_####.png) or .mp4 (H.264)")
if len(runs) > 1:
    raise ValueError("output: one run of # marks the frame number")
if "frame" in P and ("frame_start" in P or "frame_end" in P):
    raise ValueError("give frame (one image) or frame_start/frame_end (an animation), not both")
if ext == ".mp4":
    kind = "video"
    if runs:
        raise ValueError("an .mp4 output is one file: no # run")
    if "frame" in P:
        raise ValueError("an .mp4 is an animation: give frame_start/frame_end (default: the scene's range)")
elif runs:
    kind = "frames"
else:
    kind = "still"
if kind == "still":
    start = end = int(P.get("frame", scene.frame_current))
else:
    start, end = int(P.get("frame_start", scene.frame_start)), int(P.get("frame_end", scene.frame_end))
if end < start:
    raise ValueError("frame_end is before frame_start")
source = P.get("source", "auto")
if source == "sequencer":
    se = scene.sequence_editor
    if se is None or not len(getattr(se, "strips", None) or getattr(se, "sequences", [])):
        raise ValueError("source sequencer: scene %r has no strips" % scene.name)

stamp = time.strftime("%Y%m%d-%H%M%S")
# One work folder per render (several can start in the same second from the same Blender): never shared.
work = os.path.join(tempfile.gettempdir(), "opencode-blender-render", "%s-%d-%s" % (stamp, os.getpid(), uuid.uuid4().hex[:12]))
os.makedirs(work)
if kind == "frames":
    run = runs[0]
    prefix = output[: -len(ext)]
    targets = [os.path.join(os.path.dirname(output), os.path.basename(output).replace(run, str(f).zfill(len(run)))) for f in range(start, end + 1)]
else:
    prefix = os.path.join(work, "f_####")
    targets = [output]
frames = [os.path.join(work, "f_%04d.png" % f) for f in range(start, end + 1)] if kind != "frames" else targets
taken = [t for t in targets if os.path.exists(t)]
if taken and not P.get("overwrite"):
    raise ValueError("%s already exists (%d file(s)): pass overwrite true or name another output" % (taken[0], len(taken)))
os.makedirs(os.path.dirname(output), exist_ok=True)

unsaved = P.get("unsaved", "refuse")
dirty = (not path) or bpy.data.is_dirty
blend = path
working_copy = None


def with_window(fn):
    wm = bpy.context.window_manager
    if wm is not None and wm.windows and not bpy.app.background:
        with bpy.context.temp_override(window=wm.windows[0]):
            return fn()
    return fn()


if dirty:
    if unsaved == "refuse" or (unsaved == "save" and not path):
        raise ValueError(
            ("The file has unsaved changes: the render reads the file as saved on disk. Pass unsaved 'save' to save it "
             "first, or 'copy' to render a working copy of the current state.") if path else
            "The file was never saved: save it with a name (blender_save) or pass unsaved 'copy'.")
    if unsaved == "save":
        with_window(lambda: bpy.ops.wm.save_mainfile())
    else:
        working_copy = os.path.join(work, "working_copy.blend")
        with_window(lambda: bpy.ops.wm.save_as_mainfile(filepath=working_copy, copy=True, check_existing=False))
        blend = working_copy

job = {
    "kind": kind, "scene": scene.name, "start": start, "end": end, "prefix": prefix, "frames": frames, "output": output,
    "source": source, "resolution": P.get("resolution"), "samples": P.get("samples"),
    "engines": ENGINES.get(P.get("engine")) if P.get("engine") else None,
    "quality": P.get("quality", "HIGH") if P.get("quality", "HIGH") in QUALITY else "HIGH", "fps": round(scene.render.fps / (scene.render.fps_base or 1.0), 3),
}
script = os.path.join(work, "job.py")
with open(script, "w", encoding="utf-8") as fh:
    fh.write(JOB % json.dumps(job))  # noqa: F821 - JOB is prepended by the plugin
log_path = os.path.join(work, "render.log")
env = {k: v for k, v in os.environ.items() if not k.upper().startswith("BLENDER_SYSTEM_") and k != "OPENCODE_LINK_OFFER_KEY"}
env["OPENCODE_LINK_ROLE"] = "render"  # never offered to OpenCode
log = open(log_path, "wb")
kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True, "env": env}
if os.name == "nt":
    kwargs["creationflags"] = 0x08000000 | 0x00000200  # no console window, own process group
else:
    kwargs["start_new_session"] = True
process = subprocess.Popen([bpy.app.binary_path, "-b", blend, "--python-exit-code", "1", "--python", script], **kwargs)
log.close()
result = {"pid": process.pid, "log": log_path, "work": work, "kind": kind, "output": output, "targets": targets,
          "frames": frames, "start": start, "end": end, "scene": scene.name, "source": source, "blend": blend,
          **({"working_copy": working_copy} if working_copy else {}), **({"saved_first": True} if dirty and unsaved == "save" else {})}
