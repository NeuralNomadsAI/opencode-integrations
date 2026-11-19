from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import hou
from PySide6 import QtCore, QtGui, QtWidgets

import opencode_houdini


def panel_launcher(results, override):
    from .opencode_service import panel_launcher as choose

    return choose(results, override)


def _log(message: str) -> None:
    pass  # Conversation data and host credentials stay in memory.


class JsonlClient(QtCore.QObject):
    eventReceived = QtCore.Signal(str, str, dict)
    diagnostic = QtCore.Signal(str)
    _instance = None

    @classmethod
    def acquire(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._process = QtCore.QProcess(self)
        self._process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.started.connect(self._started)
        self._process.errorOccurred.connect(self._process_error)
        self._process.finished.connect(self._finished)
        self._stdout = b""
        self._pending: list[tuple] = []
        self._targets: dict[str, dict] = {}
        self._problem = ""
        self._request = 0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.start)
        self._timer.start(1000)

    def start(self) -> None:
        if not self._targets or self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            return
        services = opencode_houdini.panelServices()
        runtime, bun = panel_launcher(services, "NEURALNOMADS_HOUDINI_NODE")
        sidecar = str(Path(__file__).resolve().parents[2] / "sidecar.cjs")
        problem = ""
        if not Path(sidecar).is_file():
            problem = "OpenCode Link sidecar is missing; reinstall the application part."
        elif not runtime and services:
            problem = "No OpenCode runtime, NEURALNOMADS_HOUDINI_NODE executable, or Node on PATH."
        if problem and problem != self._problem:
            self.diagnostic.emit(problem)
            self._problem = problem
        if problem or not runtime:
            return
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.remove("BUN_BE_BUN")
        if bun:
            env.insert("BUN_BE_BUN", "1")
        self._process.setProcessEnvironment(env)
        self._process.setProgram(runtime)
        self._process.setArguments([sidecar])
        self._process.setWorkingDirectory(str(Path(sidecar).resolve().parent.parent))
        self._process.start()

    def send(self, channel: str, method: str, params: dict) -> None:
        if method in ("connect", "new_session"):
            previous = self._targets.get(channel, {})
            if method == "new_session" or previous.get("directory") != params.get("directory"):
                self._pending = [item for item in self._pending if item[0] != channel]
            else:
                params = {**params, "session_id": previous.get("session_id"), "draft": previous.get("draft", False)}
            self._targets[channel] = params
        elif method == "close":
            self._targets.pop(channel, None)
            self._pending = [item for item in self._pending if item[0] != channel]
        if self._process.state() == QtCore.QProcess.ProcessState.Running:
            self._write(channel, method, params)
        elif method not in ("connect", "new_session", "close"):
            if method == "interrupt":
                self._pending = [item for item in self._pending if item[0] != channel or item[1] != "prompt"]
            if len(self._pending) >= 100:
                self.diagnostic.emit("OpenCode sidecar request queue is full")
                return
            self._pending.append((channel, method, params))
        self.start()

    def _write(self, channel, method, params):
        self._request += 1
        _log("send method=" + method + " channel=" + channel + " params=" + json.dumps({key: value for key, value in params.items() if key != "token"}, default=str))
        message = (
            json.dumps(
                {"id": str(self._request), "channel": channel, "method": method, "params": params},
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self._process.write(message)

    def stop(self) -> None:
        if self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.closeWriteChannel()
            self._process.terminate()

    def _started(self) -> None:
        _log("sidecar started")
        pending, self._pending = self._pending, []
        self._problem = ""
        for channel, params in self._targets.items():
            self._write(channel, "connect", params)
        for item in pending:
            self._write(*item)

    def _read_stdout(self) -> None:
        self._stdout += bytes(self._process.readAllStandardOutput())
        while b"\n" in self._stdout:
            raw, self._stdout = self._stdout.split(b"\n", 1)
            if not raw.strip():
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
                _log("recv " + json.dumps(message, default=str)[:2000])
                if message.get("event"):
                    channel = str(message.get("channel", ""))
                    if message["event"] == "session" and channel in self._targets:
                        self._targets[channel] = {**self._targets[channel], "session_id": message["data"]["id"], "draft": message["data"].get("draft", False)}
                    self.eventReceived.emit(
                        channel,
                        str(message["event"]),
                        message.get("data") or {},
                    )
                elif message.get("error") and not message.get("id"):
                    self.diagnostic.emit(str(message["error"].get("message", message["error"])))
            except Exception:
                self.diagnostic.emit("Malformed sidecar output: " + raw[:200].decode("utf-8", "replace"))

    def _read_stderr(self) -> None:
        text = bytes(self._process.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            _log("stderr: " + text[-2000:])
            self.diagnostic.emit(text[-2000:])

    def _process_error(self, error) -> None:
        _log("process error: " + self._process.errorString())
        self.diagnostic.emit("OpenCode sidecar process error: " + self._process.errorString())

    def _finished(self, exit_code, exit_status) -> None:
        _log("sidecar finished exit=" + str(exit_code) + " status=" + str(exit_status))
        self._stdout = b""
        for channel in self._targets:
            self.eventReceived.emit(channel, "status", {"state": "connecting"})


class PromptEdit(QtWidgets.QPlainTextEdit):
    sendRequested = QtCore.Signal()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Conversations(QtWidgets.QComboBox):
    opening = QtCore.Signal()

    def showPopup(self) -> None:
        self.opening.emit()
        super().showPopup()


class OpenCodePanel(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("openCodeHoudiniPanel")
        self._channel = str(uuid.uuid4())
        self._permission_request: str | None = None
        self._busy = False
        self._connected = False
        self._streaming = False
        self._disposed = False
        self._build_ui()
        self._client = JsonlClient.acquire()
        self._client.eventReceived.connect(self._event)
        self._client.diagnostic.connect(self._diagnostic)
        self.destroyed.connect(self._destroyed)
        hou.hipFile.addEventCallback(self._hip_event)
        opencode_houdini.acquirePanel(self._channel)
        self._target = None
        self._sync()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._sync)
        self._timer.start(500)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel("OPENCODE / HOUDINI")
        brand.setObjectName("openCodeHoudiniBrand")
        self._session = QtWidgets.QLabel("SESSION  connecting")
        self._status = QtWidgets.QLabel("STARTING")
        self._status.setObjectName("openCodeHoudiniStatus")
        self._conversations = Conversations()
        self._conversations.addItem("New conversation", None)
        header.addWidget(brand)
        header.addWidget(self._session)
        header.addStretch(1)
        header.addWidget(self._status)
        header.addWidget(self._conversations)
        layout.addLayout(header)

        self._transcript = QtWidgets.QTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setObjectName("openCodeHoudiniTranscript")
        self._transcript.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self._transcript, 1)

        self._permission = QtWidgets.QWidget()
        permission_layout = QtWidgets.QHBoxLayout(self._permission)
        permission_layout.setContentsMargins(0, 0, 0, 0)
        self._permission_label = QtWidgets.QLabel()
        self._reject = QtWidgets.QPushButton("Reject")
        self._allow = QtWidgets.QPushButton("Allow once")
        permission_layout.addWidget(self._permission_label)
        permission_layout.addStretch(1)
        permission_layout.addWidget(self._reject)
        permission_layout.addWidget(self._allow)
        self._permission.hide()
        layout.addWidget(self._permission)

        prompt_row = QtWidgets.QHBoxLayout()
        self._prompt = PromptEdit()
        self._prompt.setPlaceholderText("Ask OpenCode about this Houdini HIP...  Ctrl+Enter to send")
        self._prompt.setMaximumHeight(92)
        self._stop = QtWidgets.QPushButton("Stop")
        self._send = QtWidgets.QPushButton("Send")
        self._stop.setEnabled(False)
        prompt_row.addWidget(self._prompt, 1)
        prompt_row.addWidget(self._stop)
        prompt_row.addWidget(self._send)
        layout.addLayout(prompt_row)

        self.setStyleSheet(
            "#openCodeHoudiniPanel { background: #202326; }"
            "#openCodeHoudiniBrand { color: #f39a4b; font-weight: 700; letter-spacing: 1px; }"
            "#openCodeHoudiniStatus { color: #d5a657; font-weight: 600; }"
            "#openCodeHoudiniTranscript { border: 1px solid #34393e; background: #191b1d; }"
        )
        self._conversations.activated.connect(self._choose_conversation)
        self._conversations.opening.connect(lambda: self._client.send(self._channel, "list", {}))
        self._send.clicked.connect(self._send_prompt)
        self._prompt.sendRequested.connect(self._send_prompt)
        self._stop.clicked.connect(lambda: self._client.send(self._channel, "interrupt", {}))
        self._allow.clicked.connect(lambda: self._permission_reply("once"))
        self._reject.clicked.connect(lambda: self._permission_reply("reject"))

    def _connect_params(self) -> dict:
        info = opencode_houdini.hostInfo()
        file_name = hou.hipFile.path()
        is_untitled = bool(hou.hipFile.isNewFile()) or not file_name or Path(file_name).name == "untitled.hip"
        directory = os.getcwd() if is_untitled else str(Path(file_name).resolve().parent)
        offer = opencode_houdini.offerInfo()
        return {
            **info,
            "offer_label": offer["label"],
            "script_handle": opencode_houdini.scriptHandle(),
            "directory": directory,
        }

    def _sync(self) -> None:
        if self._disposed:
            return
        params = self._connect_params()
        if params != self._target:
            self._target = params
            self._client.send(self._channel, "connect", params)

    def _new_session(self) -> None:
        if self._disposed:
            return
        self._client.send(self._channel, "new_session", self._target)

    def _choose_conversation(self, index: int) -> None:
        session = self._conversations.itemData(index)
        if session:
            self._client.send(self._channel, "open", {"session_id": session})
        else:
            self._new_session()

    def _send_prompt(self) -> None:
        if self._busy:
            return
        text = self._prompt.toPlainText().strip()
        if not text:
            return
        self._prompt.clear()
        self._client.send(self._channel, "prompt", {"text": text})

    def _permission_reply(self, reply: str) -> None:
        if self._permission_request is None:
            return
        self._client.send(
            self._channel,
            "permission_reply",
            {"request_id": self._permission_request, "reply": reply},
        )
        self._permission_request = None
        self._permission.hide()

    @QtCore.Slot(str, str, dict)
    def _event(self, channel: str, name: str, data: dict) -> None:
        if channel != self._channel:
            return
        if name == "session":
            self._connected = True
            self._session.setText("SESSION  " + str(data["id"])[-8:] if data.get("id") else "New conversation")
            self._append("system", "Resumed session" if data.get("resumed") else "New session")
        elif name == "history":
            self._transcript.clear()
            for message in data.get("messages", []):
                self._append(message.get("role", "system"), message.get("text", ""))
        elif name == "conversations":
            self._conversations.clear()
            self._conversations.addItem("New conversation", None)
            for entry in data["entries"]:
                self._conversations.addItem(entry.get("title") or "Untitled", entry["id"])
            self._conversations.setCurrentIndex(max(0, self._conversations.findData(data.get("selected"))))
        elif name == "notice":
            self._append("system", data["message"])
        elif name == "user_message":
            self._append("user", data["text"])
        elif name == "assistant_start":
            self._start_assistant()
        elif name == "assistant_delta":
            self._append_delta(data.get("text", ""))
        elif name == "assistant_end":
            self._streaming = False
            self._append_raw("\n")
        elif name == "tool":
            self._append("tool", str(data.get("name") or data.get("id", "tool")) + " / " + str(data.get("state", "")))
        elif name == "permission":
            self._permission_request = data.get("request_id")
            resources = ", ".join(data.get("resources", []))
            self._permission_label.setText("PERMISSION  " + str(data.get("action", "")) + (" / " + resources if resources else ""))
            self._permission.show()
        elif name == "status":
            state = str(data.get("state", "unknown"))
            self._status.setText(state.upper())
            if state == "connecting":
                self._connected = False
                self._permission_request = None
                self._permission.hide()
            self._busy = state in ("busy", "retry")
            self._stop.setEnabled(self._busy or state == "connecting")
            self._send.setEnabled(not self._busy)
            self._prompt.setEnabled(not self._busy)
        elif name == "graph_revision":
            self._status.setToolTip("Houdini graph revision " + str(data.get("revision")))
        elif name == "error":
            self._append("error", data.get("message", "Unknown OpenCode error"))
            self._status.setText("ERROR")
            self._busy = False
            self._stop.setEnabled(False)
            self._send.setEnabled(True)
            self._prompt.setEnabled(True)

    @QtCore.Slot(str)
    def _diagnostic(self, text: str) -> None:
        self._append("error", text)

    def _format(self, role: str) -> QtGui.QTextCharFormat:
        colors = {
            "user": "#e7e9eb",
            "assistant": "#b9ddd7",
            "tool": "#d5a657",
            "error": "#ef7777",
            "system": "#8e969c",
        }
        result = QtGui.QTextCharFormat()
        result.setForeground(QtGui.QColor(colors.get(role, colors["system"])))
        return result

    def _append(self, role: str, text: str) -> None:
        labels = {"user": "YOU", "assistant": "OPENCODE", "tool": "TOOL", "error": "ERROR", "system": "SYSTEM"}
        if self._transcript.document().characterCount() > 1:
            self._append_raw("\n")
        self._append_raw(labels.get(role, role.upper()) + "\n", self._format("tool" if role == "tool" else role))
        self._append_raw(str(text) + "\n", self._format(role))

    def _start_assistant(self) -> None:
        if self._streaming:
            return
        self._streaming = True
        if self._transcript.document().characterCount() > 1:
            self._append_raw("\n")
        self._append_raw("OPENCODE\n", self._format("assistant"))

    def _append_delta(self, text: str) -> None:
        if not self._streaming:
            self._start_assistant()
        self._append_raw(str(text), self._format("assistant"))

    def _append_raw(self, text: str, character_format: QtGui.QTextCharFormat | None = None) -> None:
        cursor = self._transcript.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if character_format is not None:
            cursor.setCharFormat(character_format)
        cursor.insertText(text)
        self._transcript.setTextCursor(cursor)
        self._transcript.ensureCursorVisible()

    def _hip_event(self, event_type: hou.hipFileEventType) -> None:
        if event_type in (
            hou.hipFileEventType.AfterClear,
            hou.hipFileEventType.AfterLoad,
        ):
            self._sync()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._connected = False
        opencode_houdini.releasePanel(self._channel)
        for cleanup in (
            lambda: hou.hipFile.removeEventCallback(self._hip_event),
            lambda: self._client.send(self._channel, "close", {}),
            lambda: self._client.eventReceived.disconnect(self._event),
            lambda: self._client.diagnostic.disconnect(self._diagnostic),
        ):
            try:
                cleanup()
            except RuntimeError:
                pass

    def _destroyed(self, unused_object=None) -> None:
        self.dispose()

    def closeEvent(self, event) -> None:
        self.dispose()
        super().closeEvent(event)


def create_interface() -> OpenCodePanel:
    _log("create_interface")
    return OpenCodePanel()
