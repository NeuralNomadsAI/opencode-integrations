# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender's OpenCode console language. Workers own the pipes; only the timer touches the UI."""

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import bpy

from .opencode_service import panel_launcher, registration_name
from .process import state as process_state

HELP = "/list  /open <n>  /new  /stop  /status  /help  /allow  /reject"
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
    def __init__(self, launcher):
        self.out = queue.Queue()
        self.inbox = queue.Queue()
        threading.Thread(target=self._read, args=launcher, daemon=True).start()

    def _read(self, runtime, bun):
        def failed(message):
            self.out.put({"event": "exit", "data": {"message": message}})

        script = Path(__file__).with_name("sidecar.cjs")
        if not script.is_file():
            return failed("Console sidecar missing. Install the built OpenCode Link add-on.")
        env = os.environ.copy()
        env.pop("BUN_BE_BUN", None)
        if bun:
            env["BUN_BE_BUN"] = "1"
        try:
            proc = subprocess.Popen(
                [runtime, str(script)], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return failed("Could not start the OpenCode sidecar runtime.")

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
        failed("")


class Chat:
    def __init__(self):
        self.pipes = None
        self.lines = Lines()
        self.permission = {}
        self.target = None
        self.unsent = []
        self.entries = []
        self.numbered = []
        self.menu_time = 0
        self.redraw = False
        self.show_list = False
        self.selected = None
        self.draft = False
        self.active = False
        self.problem = ""
        self.retry_at = 0
        self.launch_at = time.monotonic() + 5
        self.state, self.agent, self.model = "Connecting", "", ""

    def send(self, method, **params):
        request = uuid.uuid4().hex
        if self.pipes:
            self.pipes.inbox.put({"id": request, "channel": "blender", "method": method, "params": params})
        elif method == "prompt":
            self.unsent.append(params)
        elif method == "interrupt":
            self.unsent.clear()
        return request

    def close(self):
        if self.pipes is not None:
            self.pipes.inbox.put(None)  # EOF detaches the conversation, never withdraws the offer.
            self.pipes = None
        self.__init__()

    def status(self):
        name = os.path.basename(bpy.data.filepath) or "Untitled"
        if self.problem:
            return f"OpenCode | {name} | {self.problem}"
        return f"OpenCode | {name} | {self.state} | agent: {self.agent or 'default'} | model: {self.model or 'default'}"

    def blocked(self, message, append):
        if message != self.problem:
            self.problem = message
            if message:
                append("INFO", self.status())

    def sync(self, target):
        if target != self.target:
            if target and self.target and target["directory"] != self.target["directory"]:
                self.unsent.clear()
                self.entries = []
                self.numbered = []
                self.selected = None
                self.draft = False
            self.target = target
            self.state, self.agent, self.model = "Connecting", "", ""
            self.lines = Lines()
            self.permission.clear()
            if target is not None:
                self.send("connect", **target)
            elif self.pipes:
                self.send("close")

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
            output(end=True)
            self.pipes = None
            self.state = "Connecting"
            self.retry_at = time.monotonic() + 2
            self.blocked(data["message"], append)
        elif name == "error":
            if self.state == "Connecting":
                self.blocked(data["message"], append)
            else:
                append("ERROR", data["message"])
        elif name == "notice":
            append("INFO", data["message"])
        elif name == "session":
            if data.get("draft") and not self.draft:
                append("CLEAR", "")
            self.selected = data["id"]
            self.draft = data.get("draft", False)
            self.state, self.agent, self.model = "Ready", data.get("agent"), data.get("model")
            self.problem = ""
            if self.selected:
                append("INFO", "Resumed conversation." if data.get("resumed") else "New conversation attached.")
                append("INFO", self.status())
        elif name == "conversations":
            self.entries = data["entries"]
            self.redraw = True
            if self.show_list:
                self.show_list = False
                self.numbered = self.entries
                for i, entry in enumerate(self.numbered, 1):
                    mark = "*" if entry["id"] == data.get("selected") else " "
                    append("INFO", f"{mark}{i}. {entry['title']}")
                if not self.numbered:
                    append("INFO", "No conversations in this folder yet.")
        elif name == "assistant_delta":
            output(data["text"])
        elif name == "assistant_end":
            output(end=True)
        elif name == "tool":
            elapsed = data.get("duration_ms")
            duration = "" if elapsed is None else f" ({elapsed / 1000:.2f} s)"
            append("INFO", f"Tool: {data.get('name') or 'tool'} — {data['state']}{duration}")
        elif name == "status":
            self.state = {"busy": "Working", "connecting": "Connecting"}.get(data["state"], "Ready")
            if self.state == "Connecting":
                output(end=True)
                self.permission.clear()
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
    if kind == "CLEAR":
        bpy.ops.console.clear(scrollback=True, history=False)
    else:
        bpy.ops.console.scrollback_append(text=text, type=kind)


def _areas():
    manager = getattr(bpy.context, "window_manager", None)
    for window in manager.windows if manager else ():
        for area in window.screen.areas:
            if area.type != "CONSOLE" or area.spaces.active.language != "opencode":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                yield window, area, region


def _drain():
    areas = list(_areas())
    chat.active = chat.active or bool(areas)
    if not chat.active:
        return 0.5
    rows = []
    def append(kind, text):
        rows.append((kind, text))
    _sync(append)
    chat.drain(append)
    if rows or chat.redraw:
        for window, area, region in areas:
            with bpy.context.temp_override(window=window, area=area, region=region):
                for kind, text in rows:
                    _append(kind, text)
            area.tag_redraw()
        chat.redraw = False
    return INTERVAL


def _sync(append):
    from .addon import preferences, session

    if time.monotonic() < chat.retry_at:
        return
    path = bpy.data.filepath
    if path:
        directory = os.path.dirname(path)
    else:
        process = process_state()
        if not getattr(process, "console_unsaved_folder", None):
            process.console_unsaved_folder = tempfile.TemporaryDirectory(prefix="opencode-blender-")
        directory = process.console_unsaved_folder.name
    host = session()
    port, token, handle, label = host.snapshot() if host else (0, None, "", "")
    try:
        prefs = preferences()
        registration = registration_name(prefs.service_name.strip()) if prefs else None
    except (ValueError, AttributeError):
        chat.sync(None)
        return chat.blocked("Invalid OpenCode service name in OpenCode Link preferences.", append)
    chat.sync(dict(
        host="127.0.0.1", port=port, token=token, script_handle=handle, offer_label=label,
        directory=directory,
        registration=registration,
    ))
    if not chat.pipes:
        services = host.advertiser.results if host and host.advertiser else []
        waiting = time.monotonic() < chat.launch_at
        launcher = panel_launcher(services, "NEURALNOMADS_BLENDER_NODE", wait=waiting)
        if launcher[0]:
            chat.pipes = Pipes(launcher)
            chat.send("connect", **chat.target, session_id=chat.selected, draft=chat.draft)
            for params in chat.unsent:
                chat.send("prompt", **params)
            chat.unsent.clear()
        elif not waiting:
            chat.blocked("No OpenCode runtime, NEURALNOMADS_BLENDER_NODE executable, or Node on PATH.", append)


def execute(context, is_interactive):
    del is_interactive
    history = context.space_data.history
    if not history or not (text := history[-1].body.strip()):
        return {"CANCELLED"}
    _append("INPUT", "> " + text)
    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
    chat.active = True
    _sync(_append)
    if text in ("/help", "/status"):
        _append("INFO", HELP if text == "/help" else chat.status())
    elif text == "/list":
        if chat.pipes:
            chat.show_list = True
            chat.send("list")
        else:
            _append("INFO", chat.status())
    elif text.startswith("/open "):
        index = text[6:].strip()
        if chat.pipes and index.isascii() and index.isdecimal() and len(index) < 8 and 1 <= int(index) <= len(chat.numbered):
            chat.send("open", session_id=chat.numbered[int(index) - 1]["id"])
        else:
            _append("INFO", "Use /list, then /open <n>.")
    elif text == "/new":
        if chat.state == "Ready" and chat.target:
            chat.send("new_session", **chat.target)
        else:
            _append("INFO", "Wait for OpenCode to be ready.")
    elif text.startswith("/") and text not in ("/stop", "/allow", "/reject"):
        _append("ERROR", "Unknown command. Use /help.")
    elif text == "/stop":
        chat.send("interrupt")
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
    elif chat.target:
        chat.send("prompt", text=text)
    else:
        _append("INFO", chat.status())
    return {"FINISHED"}


def autocomplete(context):
    return {"CANCELLED"}


def copy_as_script(context):
    # The Console's fixed menu label says "as Script"; copy this area's visible scrollback as text.
    context.window_manager.clipboard = "\n".join(line.body for line in context.space_data.scrollback)
    return {"FINISHED"}


def banner(context):
    chat.active = True
    _sync(_append)
    context.space_data.prompt = "> "
    if chat.state != "Ready" or chat.selected:
        _append("INFO", chat.status())
    _append("INFO", HELP)
    return {"FINISHED"}


def _header(self, context):
    if context.space_data.language == "opencode":
        selected = next((entry["title"] for entry in chat.entries if entry["id"] == chat.selected), "New conversation")
        self.layout.menu("OPENCODE_MT_conversations", text=selected)


def _menu_classes():
    # Blender is optional for the pipe/console unit tests.
    class OPENCODE_OT_console_conversation(bpy.types.Operator):
        bl_idname = "opencode.console_conversation"
        bl_label = "Open conversation"
        bl_description = "Switch to a conversation in this folder, or start a new one"
        session_id: bpy.props.StringProperty()

        def execute(self, _context):
            if self.session_id and chat.pipes:
                chat.send("open", session_id=self.session_id)
            elif chat.state == "Ready" and chat.target:
                chat.send("new_session", **chat.target)
            return {"FINISHED"}

    class OPENCODE_MT_conversations(bpy.types.Menu):
        bl_label = "Conversations"

        def draw(self, _context):
            now = time.monotonic()
            if chat.pipes and now - chat.menu_time >= 1:
                chat.menu_time = now
                chat.send("list")
            for entry in chat.entries:
                item = self.layout.operator(
                    "opencode.console_conversation", text=entry["title"],
                    icon="CHECKMARK" if entry["id"] == chat.selected else "NONE",
                )
                item.session_id = entry["id"]
            self.layout.operator("opencode.console_conversation", text="New conversation", icon="ADD").session_id = ""

    return OPENCODE_OT_console_conversation, OPENCODE_MT_conversations


_classes = ()


def register():
    global _classes
    # Blender 5.3 lists loaded _console_* modules (and imports them by this name on Enter).
    sys.modules["_console_opencode"] = sys.modules[__name__]
    _classes = _menu_classes()
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.CONSOLE_HT_header.append(_header)
    bpy.app.timers.register(_drain, first_interval=0, persistent=True)


def unregister(keep_language=False):
    global _classes
    chat.close()
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    if "_console_opencode" in sys.modules:
        bpy.types.CONSOLE_HT_header.remove(_header)
        for cls in reversed(_classes):
            bpy.utils.unregister_class(cls)
        _classes = ()
        for screen in (() if keep_language else bpy.data.screens):
            for area in screen.areas:
                for space in area.spaces:
                    if space.type == "CONSOLE" and space.language == "opencode":
                        space.language = "python"
                        space.prompt = ">>> "
        sys.modules.pop("_console_opencode")
