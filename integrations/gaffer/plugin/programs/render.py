# SPDX-License-Identifier: MIT
# gaffer_render (fixed program, runs in the open Gaffer): checks the request, writes a working copy of the open
# script (serialiseToFile: the open file keeps its name and its own file is untouched) or takes the saved file, then
# starts a detached background `gaffer python` running render_job.py on it: the task node (Render, ImageWriter,
# SceneWriter, any TaskNode) and its upstream tasks dispatched by a LocalDispatcher for the frames, to the exact
# output when given. Returns at once; the job writes a log the plugin follows:
#   OPENCODE_RENDER {...}                 what is rendered (once, at the start)
#   OPENCODE_PROGRESS {"done": n, "total": m, ...}
#   OPENCODE_DONE {"output": ..., "files": [...], ...}   or   OPENCODE_FAILED "<message>"
# PARAMS: {"node", "output"?, "frames"?, "size"?, "source"?: "copy" | "saved", "overwrite"?}
# JOB: render_job.py, prepended by the plugin.
import json
import os
import re
import subprocess
import tempfile
import time
import uuid

import Gaffer
import GafferDispatch
import GafferScene
import IECoreScene

P = PARAMS  # noqa: F821
S = script  # noqa: F821
RUN = re.compile(r"#+")
INTERACTIVE = ("ieDisplay",)

if not P.get("node"):
    raise ValueError("give node: a task node path, e.g. Render or ImageWriter")
current = S["fileName"].getValue()
never_saved = not current
base_dir = os.path.dirname(current) if not never_saved else None
path = str(P["node"])
node = S.descendant(path.strip().strip("/").replace("/", "."))
if node is None:
    raise ValueError("no node %r in the script" % path)
if not isinstance(node, GafferDispatch.TaskNode):
    raise ValueError("node: %s is a %s.%s, not a task node (Render, ImageWriter, SceneWriter, SystemCommand...)"
                     % (path, type(node).__module__.split(".")[0], type(node).__name__))
facts = {}


def frame_list(spec):
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        values = [spec, spec, 1]
    elif isinstance(spec, list) and 2 <= len(spec) <= 3 and all(isinstance(v, (int, float)) for v in spec):
        values = list(spec) + ([1] if len(spec) == 2 else [])
    else:
        raise ValueError("frames: a frame, [start, end] or [start, end, step]")
    if not all(float(v).is_integer() for v in values):
        raise ValueError("frames: whole frames (Gaffer dispatches integer frame lists)")
    start, end, step = (int(v) for v in values)
    if end < start or step <= 0:
        raise ValueError("frames: end before start, or a step that is not positive")
    count = (end - start) // step + 1
    if count > 10000:
        raise ValueError("frames: %d frames, at most 10000" % count)
    return [start + i * step for i in range(count)]


def at_frame(frame):
    context = Gaffer.Context(S.context())
    context.setFrame(frame)
    return context


def file_outputs(render, frame):
    """A Render node's file outputs at a frame: {name: Output} (interactive displays left out)."""
    with at_frame(frame):
        g = render["in"]["globals"].getValue()
    found = {}
    for key in g.keys():
        value = g[key]
        if key.startswith("output:") and isinstance(value, IECoreScene.Output) and value.getType() not in INTERACTIVE:
            found[key[len("output:"):]] = value
    return found


frames = frame_list(P.get("frames"))
if frames is None:
    now = S.context().getFrame()
    frames = [int(round(now))]
rendering = isinstance(node, GafferScene.Render) and node["mode"].getValue() == GafferScene.Render.Mode.RenderMode
out_kind = out_name = output = output_pattern = None
if rendering:
    outputs = file_outputs(node, frames[0])
    with at_frame(frames[0]):
        facts["renderer"] = node["resolvedRenderer"].getValue() or None
    if not facts["renderer"]:
        facts["renderer_note"] = "no renderer: set the Render node's renderer, or render:defaultRenderer in StandardOptions"
    if len(outputs) == 1:
        out_name = next(iter(outputs))
        out_kind = "output"
    patterns = {name: o.getName() for name, o in outputs.items()}
    facts["outputs"] = patterns
elif "fileName" in node and isinstance(node["fileName"], Gaffer.StringPlug):
    out_kind = "fileName"
if P.get("output"):
    output = P["output"]
    if not os.path.isabs(output):
        if base_dir is None:
            raise ValueError("output %r is relative and the script was never saved: give an absolute path" % output)
        output = os.path.join(base_dir, output)
    output = os.path.normpath(output).replace("\\", "/")
    runs = RUN.findall(os.path.basename(output))
    if len(runs) > 1:
        raise ValueError("output: one run of # marks the frame number")
    if len(frames) > 1 and not runs and "${frame}" not in output and "$frame" not in output:
        raise ValueError("output: %d frames need a frame number in the name: a # run (shot_####.exr) or ${frame}" % len(frames))
    if out_kind is None:
        raise ValueError("node: %s has %s: name the output in the script" % (
            path, "%d file outputs" % len(facts.get("outputs", {})) if rendering else "no fileName plug"))
    output_pattern = output


def targets_at(frame):
    with at_frame(frame) as context:
        if output_pattern:
            names = [context.substitute(output_pattern)]
        elif rendering:
            names = [o.getName() for o in file_outputs(node, frame).values()]
        elif out_kind == "fileName":
            names = [node["fileName"].getValue()]
        else:
            names = []
        # ImageWriter and SceneWriter keep # in their fileName for sequences: the frame is put in here.
        return [os.path.abspath(context.substitute(n)) for n in names if n]


targets = [t for f in frames for t in targets_at(f)]
output = output_pattern or (node["fileName"].getValue() if out_kind == "fileName" else
                            next(iter(facts["outputs"].values())) if rendering and len(facts.get("outputs", {})) == 1 else None)
if not targets:
    facts["targets_note"] = "the node names no output file: what it wrote is read back from the log"
taken = [t for t in targets if os.path.exists(t)]
if taken and not P.get("overwrite"):
    raise ValueError("%s already exists (%d file(s)): pass overwrite true or name another output" % (taken[0], len(taken)))
for folder in sorted({os.path.dirname(t) for t in targets})[:100]:
    os.makedirs(folder, exist_ok=True)
size = P.get("size")
if size is not None:
    if not isinstance(size, list) or len(size) != 2 or not all(isinstance(v, int) and 4 <= v <= 16384 for v in size):
        raise ValueError("size: [width, height], 4 to 16384")
    if not rendering:
        raise ValueError("size: a Render node's (in render mode); %s renders no image itself" % path)

source = P.get("source", "copy")
if source not in ("copy", "saved"):
    raise ValueError("source: copy (a working copy of the open script, the default) or saved (the file as saved)")
stamp = time.strftime("%Y%m%d-%H%M%S")
# One work folder per job (several can start in the same second from the same Gaffer): never shared.
work = os.path.join(tempfile.gettempdir(), "opencode-gaffer-render", "%s-%d-%s" % (stamp, os.getpid(), uuid.uuid4().hex[:12]))
os.makedirs(work)
working_copy = None
if source == "saved":
    if never_saved or not os.path.isfile(current):
        raise ValueError("source saved: the script was never saved (gaffer_save first, or source copy)")
    gfr = current
    if S["unsavedChanges"].getValue():
        facts["unsaved_changes"] = "the open script has changes the saved file lacks: they are not rendered"
else:
    # The copy keeps the script's file name, so ${script:name} expands as in the open script.
    working_copy = os.path.join(work, os.path.basename(current) or "untitled.gfr").replace("\\", "/")
    S.serialiseToFile(working_copy)
    if not os.path.isfile(working_copy):
        raise RuntimeError("the working copy was not written")
    gfr = working_copy

job = {"file": gfr, "node": node.relativeName(S), "frames": frames, "targets": targets, "out_kind": out_kind,
       "out_name": out_name, "output_pattern": output_pattern, "size": size, "work": work}
with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as fh:
    json.dump(job, fh)
job_script = os.path.join(work, "render_job.py")
with open(job_script, "w", encoding="utf-8") as fh:
    fh.write(JOB)  # noqa: F821 - render_job.py, prepended by the plugin
exe = os.path.join(os.environ.get("GAFFER_ROOT") or "", "bin", "gaffer.cmd" if os.name == "nt" else "gaffer")
if not os.path.isfile(exe):
    raise RuntimeError("no Gaffer launcher at %s" % exe)
log_path = os.path.join(work, "render.log")
env = {k: v for k, v in os.environ.items() if k != "OPENCODE_GAFFER_OFFER_KEY"}
env["OPENCODE_GAFFER_ROLE"] = "render"  # never offered to OpenCode
env["PYTHONUNBUFFERED"] = "1"
log = open(log_path, "wb")
# The open Gaffer's working folder: relative file names resolve as they would there.
kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True, "env": env, "cwd": os.getcwd()}
job_json = os.path.join(work, "job.json")
if os.name == "nt":
    kwargs["creationflags"] = 0x08000000 | 0x00000200  # no console window, own process group
    command = '"%s" /d /s /c ""%s" python "%s" "%s""' % (os.environ.get("ComSpec") or "cmd.exe", exe, job_script, job_json)
else:
    kwargs["start_new_session"] = True
    command = [exe, "python", job_script, job_json]
process = subprocess.Popen(command, **kwargs)
log.close()
result = {"pid": process.pid, "log": log_path, "work": work, "kind": "render", "output": output, "targets": targets,
          "frames": frames, "start": frames[0], "end": frames[-1], "node": node.relativeName(S),
          "node_type": "%s.%s" % (type(node).__module__.split(".")[0], type(node).__name__), "source": source, "file": gfr,
          "facts": facts, **({"working_copy": working_copy} if working_copy else {})}
