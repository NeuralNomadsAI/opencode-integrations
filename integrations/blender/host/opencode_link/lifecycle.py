# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender's own process: what it runs (`app` host method) and relaunching it without losing work.

A relaunch first saves a recovery copy of the open document, whatever Blender says about unsaved changes
(an add-on may change the document without marking it modified). With unsaved changes it goes on only if
told to save the file first or to continue from the recovery copy. It then starts a new Blender with the
same executable, command line and environment, offering itself under the key OpenCode chose, and quits
this one once the reply has left.
"""

import os
import subprocess
import sys
import tempfile
import time

import bpy

from . import resources
from .wire import HostFailure

UNSAVED = ("refuse", "save", "recovery")
# Options followed by values (their count), not by a file to open.
_VALUED = dict.fromkeys(("-P", "--python", "--python-expr", "--python-text", "-t", "--threads", "-E", "--engine",
                         "-o", "--render-output", "-F", "--render-format", "-x", "--use-extension", "-S", "--scene",
                         "-s", "--frame-start", "-e", "--frame-end", "-f", "--render-frame", "-j", "--frame-jump",
                         "--env-system-datafiles", "--env-system-scripts", "--env-system-extensions", "--app-template",
                         "--log", "--log-file", "--log-level", "--debug-value", "--addons", "--python-exit-code",
                         "--gpu-backend"), 1)
_VALUED.update({"-p": 4, "--window-geometry": 4})
_FIRST_SCRIPT = {"-P", "--python", "--python-expr", "--python-text", "--"}


def recovery_folder():
    return os.path.join(tempfile.gettempdir(), "opencode-link-recovery")


def app_info():
    return {
        "binary_path": bpy.app.binary_path,
        "version": bpy.app.version_string,
        "background": bool(bpy.app.background),
        "file": bpy.data.filepath or None,
        "is_saved": bool(bpy.data.is_saved),
        "is_dirty": bool(bpy.data.is_dirty),
        "pid": os.getpid(),
        "recovery_folder": recovery_folder(),
        "resources": resources.snapshot(),
    }


def command_line(argv, current, target):
    """Blender's own arguments without the file it opened, with `target` opened instead."""
    args, skip = [], 0
    current = os.path.normcase(os.path.abspath(current)) if current else None
    for arg in argv[1:]:
        if skip:
            args.append(arg)
            skip -= 1
            continue
        if arg in _VALUED:
            args.append(arg)
            skip = _VALUED[arg]
            continue
        if arg.lower().endswith(".blend") and not arg.startswith("-"):
            continue  # the file it opened
        if current and os.path.normcase(os.path.abspath(arg)) == current:
            continue
        args.append(arg)
    if target:
        at = next((index for index, arg in enumerate(args) if arg in _FIRST_SCRIPT), len(args))
        args.insert(at, target)
    return args


def _spawn(command, env):
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True, "env": env, "cwd": os.getcwd()}
    if sys.platform == "win32":
        # No console window, a process of its own that outlives this Blender (and OpenCode).
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(command, creationflags=flags | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            return subprocess.Popen(command, creationflags=flags, **kwargs)
    return subprocess.Popen(command, start_new_session=True, **kwargs)


def relaunch(app, unsaved, offer_key):
    """Main thread. Returns what was saved and opened; the caller schedules the quit."""
    if unsaved not in UNSAVED:
        raise HostFailure("invalid_args", "unsaved must be refuse, save or recovery")
    if not isinstance(offer_key, str) or not offer_key.startswith("blender-launch-") or len(offer_key) != 47:
        raise HostFailure("invalid_args", "offer_key must come from OpenCode")
    path = bpy.data.filepath
    dirty = bool(bpy.data.is_dirty)
    folder = recovery_folder()
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0] if path else "untitled"
    recovery = os.path.join(folder, "%s-%s.blend" % (stem, time.strftime("%Y%m%d-%H%M%S")))
    app.with_window(lambda: bpy.ops.wm.save_as_mainfile(filepath=recovery, copy=True, check_existing=False))
    if dirty and unsaved == "refuse":
        raise HostFailure(
            "invalid_args",
            "The document has unsaved changes; a recovery copy was saved to %s. Relaunch with unsaved "
            "'save' (save the file first) or 'recovery' (continue from the recovery copy)." % recovery,
        )
    if unsaved == "save" and dirty:
        if not path:
            raise HostFailure("invalid_args", "The document was never saved: save it with a name first (execute_blender_code, "
                              "bpy.ops.wm.save_as_mainfile), or relaunch with unsaved 'recovery'. Recovery copy: %s" % recovery)
        app.with_window(lambda: bpy.ops.wm.save_mainfile())
    target = recovery if (dirty and unsaved == "recovery") or (not path and unsaved == "recovery") else (path or None)
    env = dict(os.environ, OPENCODE_LINK_OFFER_KEY=offer_key)
    command = [bpy.app.binary_path] + command_line(sys.argv, path, target)
    process = _spawn(command, env)
    return {"outcome": "relaunching", "recovery": recovery, "saved": unsaved == "save" and dirty, "opens": target,
            "was_dirty": dirty, "pid": process.pid}
