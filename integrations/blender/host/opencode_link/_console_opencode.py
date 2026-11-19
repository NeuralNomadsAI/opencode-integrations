# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender's OpenCode console language. Workers own the pipes; only the timer touches the UI."""

import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid

import bpy

from .opencode_service import registration_name

HELP = "/connect  /stop  /disconnect  /new  /status  /help  /allow  /reject"
INTERVAL = 0.05


class Lines:
    def __init__(self):
        self.pending = ""
        self.code = False

    def _line(self, text):
        if text.lstrip().startswith(("```", "~~~")):
            self.code = not self.code
            return None
        if self.code:
            return "    " + text
        return re.sub(r"^\s{0,3}#{1,6}\s+", "", text).replace("**", "").replace("__", "")

    def feed(self, text, end=False):
        parts = (self.pending + text).split("\n")
        self.pending = parts.pop()
        if end and self.pending:
            parts.append(self.pending)
        lines = [self._line(line.removesuffix("\r")) for line in parts]
        if end:
            self.pending, self.code = "", False
        return [line for line in lines if line is not None]


class Pipes:
    def __init__(self):
        self.out = queue.Queue()
        self.inbox = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        def failed(message):
            self.out.put({"event": "exit", "data": {"message": message}})

        node = shutil.which(os.environ.get("NEURALNOMADS_BLENDER_NODE") or "node")
        if not node:
            return failed("Node.js 22+ not found. Set NEURALNOMADS_BLENDER_NODE or add Node to PATH.")
        script = Path(__file__).with_name("sidecar.cjs")
        if not script.is_file():
            return failed("Console sidecar missing. Install the built OpenCode Link add-on.")
        try:
            proc = subprocess.Popen(
                [node, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return failed("Could not start Node.js.")

        def write():
            with proc.stdin:
                try:
                    while (message := self.inbox.get()) is not None:
                        proc.stdin.write(json.dumps(message) + "\n")
                        proc.stdin.flush()
                except (OSError, ValueError):
                    pass

        threading.Thread(target=write, daemon=True).start()
        with proc.stdout:
            for line in proc.stdout:
                try:
                    self.out.put(json.loads(line))
                except ValueError:
                    pass
        self.inbox.put(None)
        proc.wait()
        failed("Console sidecar exited. Use /connect to try again.")


class Chat:
    def __init__(self):
        self.pipes = None
        self.lines = Lines()
        self.permission = {}
        self.path = ""
        self.request = None
        self.state, self.agent, self.model = "Disconnected", "", ""

    def send(self, method, **params):
        if self.pipes is None:
            self.pipes = Pipes()
        request = uuid.uuid4().hex
        self.pipes.inbox.put({"id": request, "channel": "blender", "method": method, "params": params})
        return request

    def close(self):
        if self.pipes is not None:
            self.pipes.inbox.put(None)  # EOF detaches the conversation, never withdraws the offer.
            self.pipes = None
        self.__init__()

    def status(self):
        name = os.path.basename(bpy.data.filepath) or "Untitled"
        return f"OpenCode | {name} | {self.state} | agent: {self.agent or 'default'} | model: {self.model or 'default'}"

    def drain(self, append, limit=120):
        if not self.pipes:
            return
        for _ in range(limit):
            try:
                item = self.pipes.out.get_nowait()
            except queue.Empty:
                break
            self._event(item, append)
            if not self.pipes:
                break

    def _event(self, item, append):
        name, data = item.get("event"), item.get("data", {})

        def output(text="", end=False):
            for line in self.lines.feed(text, end):
                append("OUTPUT", line)

        if name == "exit":
            append("ERROR", data["message"])
            self.close()
        elif name == "error":
            append("ERROR", data["message"])
        elif item.get("id") and self.request and item["id"] == self.request[0]:
            method = self.request[1]
            self.request = None
            if method == "close":
                self.close()
                append("INFO", "Disconnected.")
            elif method in ("connect", "new_session") and item.get("error"):
                self.state = "Disconnected"
            elif method == "prompt" and item.get("error"):
                self.state = "Ready"
            elif method == "interrupt" and not item.get("error"):
                append("INFO", "Stop requested.")
        elif self.state == "Disconnecting":
            return
        elif name == "disconnected":
            self.state = "Disconnected"
            self.permission.clear()
            output(end=True)
            append("ERROR", data["message"])
        elif self.state == "Disconnected":
            return
        elif name == "session":
            self.state, self.agent, self.model = "Ready", data.get("agent"), data.get("model")
            append("INFO", "Resumed conversation." if data.get("resumed") else "New conversation attached.")
            append("INFO", self.status())
        elif name == "assistant_delta":
            output(data["text"])
        elif name == "assistant_end":
            output(end=True)
        elif name == "tool":
            elapsed = data.get("duration_ms")
            duration = "" if elapsed is None else f" ({elapsed / 1000:.2f} s)"
            append("INFO", f"Tool: {data.get('name') or 'tool'} — {data['state']}{duration}")
        elif name == "status":
            self.state = "Working" if data["state"] == "busy" else "Ready"
            if self.state == "Ready":
                output(end=True)
                self.permission.clear()
                if data.get("interrupted"):
                    append("INFO", "Interrupted.")
        elif name == "agent":
            self.agent = data.get("agent")
        elif name == "model":
            self.model = data.get("selected")
        elif name == "profile":
            self.agent, self.model = data.get("agent"), data.get("model")
        elif name == "permission":
            self.permission[data["request_id"]] = data
            resources = ", ".join(data["resources"])
            append("INFO", f"Permission requested: {data['action']} {resources} — /allow once or /reject")
        elif name == "permission_done":
            self.permission.pop(data["request_id"], None)


chat = Chat()


def _append(kind, text):
    bpy.ops.console.scrollback_append(text=text, type=kind)


def _broadcast(rows):
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "CONSOLE" or area.spaces.active.language != "opencode":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    for kind, text in rows:
                        _append(kind, text)
                area.tag_redraw()


def _drain():
    rows = []
    chat.drain(lambda kind, text: rows.append((kind, text)))
    if rows:
        _broadcast(rows)
    return INTERVAL if chat.pipes else None


def disconnect():
    if chat.pipes and chat.state != "Disconnecting":
        chat.state = "Disconnecting"
        chat.request = (chat.send("close"), "close")


def check_document():
    if chat.path and chat.path != bpy.data.filepath:
        disconnect()


def _connect(method):
    from .addon import preferences, session

    path = bpy.data.filepath
    if not path:
        return _append("ERROR", "Save this .blend before connecting.")
    host = session()
    port, token, handle, label = host.snapshot() if host else (0, None, "", "")
    if not token:
        return _append("ERROR", "OpenCode Link is not offering this Blender yet.")
    try:
        registration = registration_name(preferences().service_name.strip())
    except (ValueError, AttributeError):
        return _append("ERROR", "Invalid OpenCode service name in OpenCode Link preferences.")
    chat.path = path
    chat.state, chat.agent, chat.model = "Connecting", "", ""
    chat.permission.clear()
    chat.lines = Lines()
    chat.request = (chat.send(
        method, host="127.0.0.1", port=port, token=token, script_handle=handle, offer_label=label,
        directory=os.path.dirname(path), document_name=os.path.basename(path),
        document_key=os.path.normcase(os.path.abspath(path)), registration=registration,
    ), method)
    if not bpy.app.timers.is_registered(_drain):
        bpy.app.timers.register(_drain, first_interval=0, persistent=True)
    _append("INFO", "Connecting to OpenCode…")


def execute(context, is_interactive):
    del is_interactive
    history = context.space_data.history
    if not history or not (text := history[-1].body.strip()):
        return {"CANCELLED"}
    _append("INPUT", "> " + text)
    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
    check_document()
    if text in ("/help", "/status"):
        _append("INFO", HELP if text == "/help" else chat.status())
    elif text in ("/connect", "/new"):
        if chat.state in ("Connecting", "Working", "Disconnecting"):
            _append("INFO", "Wait for the current operation or use /stop.")
        else:
            _connect("connect" if text == "/connect" else "new_session")
    elif text == "/disconnect":
        if chat.pipes:
            disconnect()
        else:
            _append("INFO", "Disconnected.")
    elif text.startswith("/") and text not in ("/stop", "/allow", "/reject"):
        _append("ERROR", "Unknown command. Use /help.")
    elif chat.state not in ("Ready", "Working"):
        _append("INFO", "Use /connect first.")
    elif text == "/stop":
        chat.request = (chat.send("interrupt"), "interrupt")
    elif text in ("/allow", "/reject"):
        if len(chat.permission) == 1:
            request_id = next(iter(chat.permission))
            chat.send("permission_reply", request_id=request_id, reply="once" if text == "/allow" else "reject")
        elif chat.permission:
            _append("INFO", "Several permissions are pending; review them in OpenCode.")
        else:
            _append("INFO", "No permission is pending.")
    elif chat.state == "Working":
        _append("INFO", "OpenCode is working. Wait or use /stop.")
    else:
        chat.state = "Working"
        chat.request = (chat.send("prompt", text=text), "prompt")
    return {"FINISHED"}


def autocomplete(context):
    return {"CANCELLED"}


def copy_as_script(context):
    # The Console's fixed menu label says "as Script"; copy this area's visible scrollback as text.
    context.window_manager.clipboard = "\n".join(line.body for line in context.space_data.scrollback)
    return {"FINISHED"}


def banner(context):
    context.space_data.prompt = "> "
    _append("INFO", chat.status())
    _append("INFO", HELP)
    return {"FINISHED"}


def register():
    # Blender 5.3 lists loaded _console_* modules (and imports them by this name on Enter).
    sys.modules["_console_opencode"] = sys.modules[__name__]


def unregister():
    chat.close()
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    if "_console_opencode" in sys.modules:
        for screen in bpy.data.screens:
            for area in screen.areas:
                for space in area.spaces:
                    if space.type == "CONSOLE" and space.language == "opencode":
                        space.language = "python"
                        space.prompt = ">>> "
        sys.modules.pop("_console_opencode")
