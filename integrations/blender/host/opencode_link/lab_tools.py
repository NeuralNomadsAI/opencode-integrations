# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender Lab tools bundled by the plugin's sync (lab/tools.json and lab/<module>_toolcode.py).

The host method `lab_tool` runs one of them by name with JSON arguments: the tool code is read from this
add-on, never received from the plugin. It follows Lab's calling convention (a `Params` named tuple passed
to `main`, whose result is `._asdict()`, or a `check_is_finished` callable polled until it returns a dict)
and returns Lab's envelope: {"status": "ok", "result", "stdout", "stderr"} or {"status": "error",
"message"}.
"""

import json
import os
import time
import traceback

from .wire import HostFailure
from . import limits

LAB_METHOD = "lab_tool"
# Lab's own limit for a deferred job (a render), polled on the request thread.
DEFERRED_TIMEOUT = limits.value("deferred_timeout_s")
DEFERRED_POLL = limits.value("deferred_poll_s")


def load(folder):
    path = os.path.join(folder, "lab", "tools.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {tool["name"]: tool for tool in data["tools"] if tool.get("runs") == "toolcode" and tool.get("file")}


class LabTools(object):
    def __init__(self, folder):
        self.folder = folder
        self.tools = load(folder)
        self._compiled = {}

    def find(self, params):
        name = params.get("tool")
        tool = self.tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise HostFailure("invalid_args", "Unknown Blender Lab tool %r." % (name,))
        return tool

    def code(self, tool):
        path = os.path.join(self.folder, "lab", tool["file"])
        compiled = self._compiled.get(path)
        if compiled is None:
            with open(path, encoding="utf-8") as handle:
                compiled = compile(handle.read(), path, "exec")
            self._compiled[path] = compiled
        return compiled

    def runner(self, tool, args):
        """A callable for the main thread: runs the tool code, returns its result dict or a deferred checker."""
        code = self.code(tool)

        def run():
            namespace = {"result": {}}
            exec(code, namespace)  # noqa: S102 - bundled tool code addressed by name, never supplied
            params = None
            if tool.get("call_params"):
                params = namespace["Params"](**{name: args[name] for name in tool["call_params"]})
            value = namespace["main"](params)
            if callable(value):
                return value
            return value._asdict()

        return run


def envelope(value, error, trace, stdout, stderr, strict=True):
    """Lab's response envelope for a finished run."""
    if error is not None:
        response = {"status": "error", "message": trace or error}
    elif not isinstance(value, dict):
        response = {"status": "error", "message": "The `result` variable must be a dict, not %s." % type(value).__name__}
    else:
        try:
            response = {"status": "ok", "result": json.loads(json.dumps(value) if strict else json.dumps(value, default=repr))}
        except (TypeError, ValueError) as exc:
            response = {"status": "error", "message": "The `result` value is not JSON-serializable: %s" % exc}
    if stdout:
        response["stdout"] = stdout
    if stderr:
        response["stderr"] = stderr
    return response


def poll(on_main, check, timeout=DEFERRED_TIMEOUT, interval=DEFERRED_POLL):
    """Polls a deferred checker on the main thread until it returns a dict (Lab's deferred convention)."""
    deadline = time.monotonic() + timeout

    def step():
        try:
            return check(), None
        except Exception:  # noqa: BLE001 - reported in Lab's envelope
            return None, traceback.format_exc()

    while True:
        result, trace = on_main(step)
        if trace is not None:
            return None, trace
        if result is not None:
            if not isinstance(result, dict):
                return None, "check_is_finished must return None or dict, not %s" % type(result).__name__
            return result, None
        if time.monotonic() > deadline:
            return None, "Deferred operation timed out after %.0f seconds" % timeout
        time.sleep(interval)
