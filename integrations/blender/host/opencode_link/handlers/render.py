# SPDX-License-Identifier: GPL-3.0-or-later

"""Animation renders in a Blender of their own, followed as a task.

The open document is never changed: a separate background Blender opens the file as saved on disk (saved
first, or a working copy of the current state, only when asked), renders with the file's own render
settings unless an override is passed (in memory only), writes the frame range as PNG frames to the output
folder (the one setting always set: the file's own output path and format are not used) and, when asked,
encodes them as an H.264 MP4 through the video sequencer. The result states the settings the render used. The task
reports frames done and total, the time per frame and the errors of the render log; it can be cancelled. The
render needs nothing from this add-on: it goes on across a hot reload, and closing Blender does not stop it
(its process and log are named in the result).

The render Blender loads the user's preferences and add-ons (GPU devices, add-on engines), like Blender
started from the desktop: its environment is this Blender's, without the BLENDER_SYSTEM_* overrides (they
would point it at other system scripts or resources than the installed Blender's) and with
OPENCODE_LINK_ROLE=render, so OpenCode Link never offers it as a session. It runs without a console window.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import bpy

from .. import limits, resources
from .. import tasks as tasks_module
from ..tasks import TaskFailure

ENGINES = {"workbench": ["BLENDER_WORKBENCH"], "eevee": ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"], "cycles": ["CYCLES"]}
QUALITIES = ("LOWEST", "VERYLOW", "LOW", "MEDIUM", "HIGH", "PERC_LOSSLESS", "LOSSLESS")
UNSAVED = ("refuse", "save", "copy")
FRAME_PREFIX = "f_"
# The frame number in an output pattern: one run of #, zero-padded to its length (Blender's own convention).
_HASHES = re.compile(r"#+")
POLL = limits.value("render_poll_s")
# Blender 5.3 logs "<time>  <category>  | <message>"; older ones the message alone.
_MESSAGE = re.compile(r"^(?:\d+:\d+\.\d+\s+\S+\s+\|\s?)?(.*)$")
_TIME = re.compile(r"^\s*Time:\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)")
_ERROR = re.compile(r"^(Error\b|ERROR\b|.*\bOut of memory\b)")
_FRAME = re.compile(r"^(?:Rendering frame (\d+)|Fra:\s*(\d+)\b.*?(\d+)\s*/\s*(\d+) samples)")
# Processes of running renders, by task (a test or a later call may look them up).
PROCESSES = {}


def environment():
    """The render Blender's environment (see the module docstring)."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("BLENDER_SYSTEM_") and key != "OPENCODE_LINK_OFFER_KEY"}
    env["OPENCODE_LINK_ROLE"] = "render"
    return env


def spawn(command, log, env=None):
    """A background Blender writing its output to `log`, without a console window."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True,
              "env": environment() if env is None else env}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: no console, not stopped by this Blender's console events.
        return subprocess.Popen(command, creationflags=0x08000000 | 0x00000200, **kwargs)
    return subprocess.Popen(command, start_new_session=True, **kwargs)


def _setup_script(options):
    """Run by the render Blender after opening the file: overrides in memory, PNG frames to the output."""
    return (
        "import bpy, json, os\n"
        "o = json.loads(%r)\n"
        "scene = bpy.context.scene\n"
        "r = scene.render\n"
        "if o['percent']: r.resolution_percentage = o['percent']\n"
        "if o['engines']:\n"
        "    for name in o['engines']:\n"
        "        try:\n"
        "            r.engine = name\n"
        "            break\n"
        "        except TypeError:\n"
        "            pass\n"
        "if o['samples']:\n"
        "    if r.engine == 'CYCLES': scene.cycles.samples = o['samples']\n"
        "    elif 'EEVEE' in r.engine: scene.eevee.taa_render_samples = o['samples']\n"
        "s = r.image_settings\n"
        "if hasattr(s, 'media_type'): s.media_type = 'IMAGE'\n"
        "s.file_format = 'PNG'\n"
        "r.filepath = o['prefix']\n"
        "r.use_file_extension = True\n"
        "r.use_overwrite = True\n"
        "r.use_placeholder = False\n"
        "used = {'engine': r.engine, 'resolution': [r.resolution_x, r.resolution_y], 'resolution_percentage': r.resolution_percentage,"
        " 'fps': round(r.fps / (r.fps_base or 1.0), 3), 'frames': 'PNG to the output folder'}\n"
        "if r.engine == 'CYCLES':\n"
        "    c = scene.cycles\n"
        "    used.update(samples=c.samples, adaptive_sampling=c.use_adaptive_sampling, adaptive_threshold=round(c.adaptive_threshold, 6),"
        " denoising=c.use_denoising, time_limit=c.time_limit, device=c.device)\n"
        "elif 'EEVEE' in r.engine:\n"
        "    used.update(samples=scene.eevee.taa_render_samples)\n"
        "print('OPENCODE_RENDER', json.dumps({'engine': r.engine, 'size': [r.resolution_x * r.resolution_percentage // 100,"
        " r.resolution_y * r.resolution_percentage // 100], 'settings': used, 'role': os.environ.get('OPENCODE_LINK_ROLE'),"
        " 'system_overrides': sorted(k for k in os.environ if k.upper().startswith('BLENDER_SYSTEM_'))}), flush=True)\n"
    ) % json.dumps(options)


def _encode_script(options):
    """Run by a factory-settings Blender: the PNG frames as an H.264 MP4 through the video sequencer
    (Blender 5.3: `strips`, and FFMPEG output needs media_type VIDEO)."""
    return (
        "import bpy, json, os\n"
        "o = json.loads(%r)\n"
        "scene = bpy.context.scene\n"
        "r = scene.render\n"
        "r.fps = max(1, round(o['fps']))\n"
        "r.fps_base = r.fps / o['fps']  # an integer rate and its divisor (23.976 = 24 / 1.001)\n"
        "scene.frame_start, scene.frame_end = 1, len(o['files'])\n"
        "editor = scene.sequence_editor or scene.sequence_editor_create()\n"
        "strips = editor.strips if hasattr(editor, 'strips') else editor.sequences\n"
        "strip = strips.new_image('frames', os.path.join(o['dir'], o['files'][0]), 1, 1)\n"
        "for name in o['files'][1:]:\n"
        "    strip.elements.append(name)\n"
        "strip.frame_final_duration = len(o['files'])\n"
        "r.resolution_x, r.resolution_y, r.resolution_percentage = o['size'][0], o['size'][1], 100\n"
        "image = r.image_settings\n"
        "if hasattr(image, 'media_type'): image.media_type = 'VIDEO'\n"
        "image.file_format = 'FFMPEG'\n"
        "r.ffmpeg.format, r.ffmpeg.codec, r.ffmpeg.constant_rate_factor = 'MPEG4', 'H264', o['quality']\n"
        "r.ffmpeg.audio_codec = 'NONE'\n"
        "r.filepath = o['path']\n"
        "r.use_file_extension = False\n"
        "bpy.ops.render.render(animation=True)\n"
        "print('OPENCODE_ENCODED', flush=True)\n"
    ) % json.dumps(options)


def _png_size(path):
    with open(path, "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise TaskFailure("%s is not a PNG frame" % path)
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


class _Log(object):
    """Reads a render log as it grows: per-frame times and error lines."""

    def __init__(self, path):
        self.path, self.offset, self.partial = path, 0, ""
        self.times, self.errors, self.info = [], [], {}
        self.trace = None  # lines of a Python traceback being read
        self.frame, self.samples = None, None

    def read(self):
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
        except OSError:
            return
        self.offset += len(data)
        lines = (self.partial + data.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.partial = lines.pop()
        for line in lines:
            if line.startswith("OPENCODE_RENDER "):
                try:
                    self.info = json.loads(line[len("OPENCODE_RENDER "):])
                except ValueError:
                    pass
                continue
            if self.trace is not None:
                if line.startswith((" ", "\t")) or not line.strip():
                    self.trace.append(line)
                    continue
                # The exception line that ends a traceback: the error worth reporting.
                self.errors.append(("%s (%s)" % (line.strip(), self.trace[-2].strip() if len(self.trace) > 1 else "")).strip()[:300])
                self.trace = None
                continue
            if line.startswith("Traceback (most recent call last)"):
                self.trace = []
                continue
            message = _MESSAGE.match(line).group(1)
            match = _TIME.match(message)
            frame = _FRAME.match(message)
            if match:
                hours, minutes, seconds = match.groups()
                self.times.append(int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds))
            elif frame:
                self.frame = int(frame.group(1) or frame.group(2))
                self.samples = "%s/%s" % (frame.group(3), frame.group(4)) if frame.group(3) else None
            elif _ERROR.search(message):
                self.errors.append(message.strip()[:300])
        del self.errors[:-20]

    def per_frame(self):
        return round(sum(self.times) / len(self.times), 2) if self.times else None


def _run(task, process_holder, command, log_path, label, cancelled_message):
    """Runs one Blender command to its end; returns its log reader. Cancelling kills the process."""
    with open(log_path, "ab") as log:
        process = spawn(command, log)
    process_holder["process"] = process
    PROCESSES[task.id] = process
    reader = _Log(log_path)
    while process.poll() is None:
        reader.read()
        yield reader
        if task.cancelled.is_set():
            break
        time.sleep(POLL)
    if task.cancelled.is_set():
        _stop(process)
        raise TaskFailure(cancelled_message())
    reader.read()
    if process.returncode != 0:
        raise TaskFailure("The %s stopped (exit code %s): %s (log: %s)" % (
            label, process.returncode, " | ".join(reader.errors[-3:]) or "no error in its log", log_path))
    yield reader


def _stop(process):
    try:
        process.terminate()
        process.wait(10)
    except Exception:  # noqa: BLE001 - already gone
        pass


def render_animation(frame_start=None, frame_end=None, output=None, unsaved="refuse", resolution_percentage=None,
                     samples=None, engine=None, video=False, fps=None, quality="HIGH", scene=None):
    """Render the scene's animation in a separate background Blender, as a task (see the module docstring)."""
    if unsaved not in UNSAVED:
        raise ValueError("unsaved must be refuse or copy")
    if engine is not None and engine not in ENGINES:
        raise ValueError("engine must be one of %s" % ", ".join(ENGINES))
    if quality not in QUALITIES:
        raise ValueError("quality must be one of %s" % ", ".join(QUALITIES))
    if resolution_percentage is not None and not 1 <= int(resolution_percentage) <= 100:
        raise ValueError("resolution_percentage must be 1 to 100")
    if samples is not None and not 1 <= int(samples) <= 65536:
        raise ValueError("samples must be 1 to 65536")
    target = bpy.data.scenes.get(scene) if scene else bpy.context.scene
    if target is None:
        raise ValueError("Scene not found: %s" % scene)
    start = int(target.frame_start if frame_start is None else frame_start)
    end = int(target.frame_end if frame_end is None else frame_end)
    if end < start:
        raise ValueError("frame_end must be at least frame_start")
    path = bpy.data.filepath
    stem = os.path.splitext(os.path.basename(path))[0] if path else "untitled"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tasks = tasks_module.current()
    pattern = None
    if output:
        out = os.path.abspath(bpy.path.abspath(output))
        base = os.path.basename(out)
        if "#" in base:
            pattern = base[:-4] if base.lower().endswith(".png") else base
            if len(_HASHES.findall(pattern)) != 1:
                raise ValueError("output: one run of # marks the frame number (renders/shot_#### writes shot_0001.png...)")
            out = os.path.dirname(out)
    elif path:
        out = os.path.join(os.path.dirname(path), "renders", "%s-%s" % (stem, stamp))
    else:
        out = os.path.join(tempfile.gettempdir(), "opencode-link-renders", "%s-%s" % (stem, stamp))
    frames = list(range(start, end + 1))
    if pattern:
        names = [_HASHES.sub(lambda run, frame=frame: "%0*d" % (len(run.group()), frame), pattern) + ".png" for frame in frames]
        taken = os.path.isdir(out) and any(os.path.isfile(os.path.join(out, name)) for name in names)
    else:
        names = ["%s%04d.png" % (FRAME_PREFIX, frame) for frame in frames]
        taken = os.path.isdir(out) and any(name.startswith(FRAME_PREFIX) and name.endswith(".png") for name in os.listdir(out))
    if taken:
        raise ValueError("%s already holds rendered frames%s: name another output" % (out, " of this pattern" if pattern else ""))
    copied = None
    unsaved_changes = tasks.app.unsaved_changes() if hasattr(tasks.app, "unsaved_changes") else (not path or bpy.data.is_dirty)
    if unsaved_changes:
        if unsaved == "refuse" or (unsaved == "save" and not path):
            raise ValueError(
                ("The open document has unsaved changes: the render reads the file as saved on disk, so it would use the "
                 "saved settings and scene, not the current ones. Pass unsaved 'save' to save the file first, or 'copy' to "
                 "render a working copy of the current state.") if path else
                ("The document was never saved: save it with a name first, or pass unsaved 'copy' to render a working "
                 "copy of the current state."))
        if unsaved == "save":
            tasks.app.with_window(lambda: bpy.ops.wm.save_mainfile())
        else:
            folder = os.path.join(tempfile.gettempdir(), "opencode-link-renders")
            os.makedirs(folder, exist_ok=True)
            copied = os.path.join(folder, "%s-%s.blend" % (stem, stamp))
            # A copy: the open document keeps its file, name and modified state; relative paths are remapped.
            tasks.app.with_window(lambda: bpy.ops.wm.save_as_mainfile(filepath=copied, copy=True, check_existing=False))
    blend = copied or path
    os.makedirs(out, exist_ok=True)
    rate = float(fps) if fps else target.render.fps / (target.render.fps_base or 1.0)
    options = {"percent": int(resolution_percentage) if resolution_percentage else None,
               "engines": ENGINES[engine] if engine else None, "samples": int(samples) if samples else None,
               "prefix": os.path.join(out, pattern or FRAME_PREFIX)}
    command = [bpy.app.binary_path, "-b", blend] + (["-S", target.name] if scene else []) + [
        "--python-exit-code", "3", "--python-expr", _setup_script(options), "-s", str(start), "-e", str(end), "-a"]
    log_path = os.path.join(out, "render.log")
    video_path = os.path.join(out, "%s.mp4" % stem) if video else None
    overridden = {key: value for key, value in (("resolution_percentage", options["percent"]), ("engine", engine),
                                                ("samples", options["samples"])) if value is not None}
    holder = {}
    disk_checked = []
    started = time.monotonic()
    start_resources = resources.snapshot()
    bpy_binary = bpy.app.binary_path

    def done_count():
        return sum(1 for name in names if os.path.isfile(os.path.join(out, name)))

    def run(task, _tasks):
        reader = None
        for reader in _run(task, holder, command, log_path, "render",
                           lambda: "Cancelled: %d of %d frames rendered in %s" % (done_count(), len(names), out)):
            done = done_count()
            per = reader.per_frame()
            left = len(names) - done
            if done and not disk_checked:
                # The first frame's size tells what the others need on disk.
                disk_checked.append(True)
                size = os.path.getsize(next(os.path.join(out, n) for n in names if os.path.isfile(os.path.join(out, n))))
                try:
                    resources.check("The remaining %d frames" % left, disk_bytes=size * left, disk_path=out)
                except ValueError as error:
                    _stop(holder["process"])
                    raise TaskFailure("%s The render was stopped after %d frames." % (error, done)) from None
            task.update("Rendering: %d of %d frames" % (done, len(names)), {
                "frames_done": done, "frames_total": len(names), "percent": round(100.0 * done / len(names), 1),
                "seconds_per_frame": per, "eta_s": round(per * left) if per else None,
                "frame": reader.frame, "samples": reader.samples, "errors": reader.errors[-3:] or None})
        done = done_count()
        result_resources = resources.snapshot()
        if done < len(names):
            raise TaskFailure("The render ended with %d of %d frames: %s (log: %s)" % (
                done, len(names), " | ".join(reader.errors[-3:]) or "no error in its log", log_path))
        result = {"output": out, "frame_files": (pattern or FRAME_PREFIX + "####") + ".png", "frames": done, "first": os.path.join(out, names[0]), "last": os.path.join(out, names[-1]),
                  "seconds_per_frame": reader.per_frame(), "elapsed_s": round(time.monotonic() - started, 1),
                  "engine": reader.info.get("engine"), "size": reader.info.get("size"),
                  "settings": reader.info.get("settings"), "overrides": overridden or None, "log": log_path,
                  "resources": result_resources}
        if reader.errors:
            result["log_errors"] = reader.errors[-5:]
        if copied:
            result["working_copy"] = copied
        if video_path:
            task.update("Encoding the video", {"frames_done": done, "frames_total": len(names), "percent": 100.0})
            width, height = _png_size(os.path.join(out, names[0]))
            encode = {"dir": out, "files": names, "fps": rate, "quality": quality, "path": video_path,
                      "size": [width - width % 2, height - height % 2]}  # H.264 needs even dimensions
            command_video = [bpy_binary, "-b", "--factory-startup", "--python-exit-code", "3",
                             "--python-expr", _encode_script(encode)]
            for _ in _run(task, holder, command_video, log_path, "video encoding",
                          lambda: "Cancelled while encoding the video; the %d frames are in %s" % (done, out)):
                pass
            if not os.path.isfile(video_path):
                # Blender may name a movie after its frame range: take the one this encoding wrote.
                made = [os.path.join(out, name) for name in os.listdir(out) if name.lower().endswith(".mp4")]
                if made:
                    os.replace(max(made, key=os.path.getmtime), video_path)
            if not os.path.isfile(video_path) or os.path.getsize(video_path) == 0:
                raise TaskFailure("The video encoding wrote no %s (log: %s)" % (video_path, log_path))
            result["video"] = video_path
        return result

    def setup(task):
        task.blocks_reload = False
        task.on_cancel = lambda: threading.Thread(target=lambda: _stop(holder["process"]) if holder.get("process") else None,
                                                  daemon=True).start()

    task = tasks.start("render", "Render %s, frames %d-%d" % (stem, start, end), run, setup=setup)
    output_info = {"task": task.snapshot(), "output": out, "frame_files": (pattern or FRAME_PREFIX + "####") + ".png",
                   "frames": [start, end], "file": blend, "log": log_path,
                   "settings": "the file's own render settings" + (", with overrides" if overridden else ""),
                   "overrides": overridden or None, "resources": start_resources}
    if unsaved_changes and unsaved == "save":
        output_info["saved_first"] = True
    if copied:
        output_info["working_copy"] = copied
    if video_path:
        output_info["video"] = video_path
    return output_info
