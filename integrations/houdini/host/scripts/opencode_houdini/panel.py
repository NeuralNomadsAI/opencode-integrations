from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

import hou
from PySide6 import QtCore, QtGui, QtWidgets

import opencode_houdini


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
        self._pending: list[bytes] = []
        self._request = 0

    def start(self) -> None:
        if self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            return
        bun = os.environ.get("NEURALNOMADS_HOUDINI_NODE") or shutil.which("node")
        sidecar = str(Path(__file__).resolve().parents[2] / "sidecar.cjs")
        if not bun:
            _log("bun missing")
            self.diagnostic.emit("Install Node.js 22 or set NEURALNOMADS_HOUDINI_NODE")
            return
        if not Path(sidecar).is_file():
            _log("sidecar missing: " + sidecar)
            self.diagnostic.emit(
                "OpenCode Link sidecar is missing; reinstall the application part."
            )
            return
        _log("starting sidecar bun=" + bun + " sidecar=" + sidecar + " cwd=" + str(Path(sidecar).resolve().parent.parent))
        self._process.setProgram(bun)
        self._process.setArguments([sidecar])
        self._process.setWorkingDirectory(str(Path(sidecar).resolve().parent.parent))
        self._process.start()

    def send(self, channel: str, method: str, params: dict) -> None:
        self._request += 1
        _log("send method=" + method + " channel=" + channel + " params=" + json.dumps({key: value for key, value in params.items() if key != "token"}, default=str))
        message = (
            json.dumps(
                {"id": str(self._request), "channel": channel, "method": method, "params": params},
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if self._process.state() == QtCore.QProcess.ProcessState.Running:
            self._process.write(message)
        elif len(self._pending) < 100:
            self._pending.append(message)
            self.start()
        else:
            self.diagnostic.emit("OpenCode sidecar request queue is full")

    def stop(self) -> None:
        if self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.closeWriteChannel()
            self._process.terminate()

    def _started(self) -> None:
        _log("sidecar started")
        pending, self._pending = self._pending, []
        for message in pending:
            self._process.write(message)

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
                    self.eventReceived.emit(
                        str(message.get("channel", "")),
                        str(message["event"]),
                        message.get("data") or {},
                    )
                elif message.get("error"):
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
        self.diagnostic.emit("OpenCode sidecar stopped with exit code " + str(exit_code))


class PromptEdit(QtWidgets.QPlainTextEdit):
    sendRequested = QtCore.Signal()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class OpenCodePanel(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("openCodeHoudiniPanel")
        self._channel = str(uuid.uuid4())
        self._permission_request: str | None = None
        self._busy = False
        self._connected = False
        self._streaming = False
        self._build_ui()
        self._client = JsonlClient.acquire()
        self._client.eventReceived.connect(self._event)
        self._client.diagnostic.connect(self._diagnostic)
        self.destroyed.connect(self._destroyed)
        hou.hipFile.addEventCallback(self._hip_event)
        self._status.setText("DISCONNECTED")

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
        self._new = QtWidgets.QPushButton("New")
        self._connect_button = QtWidgets.QPushButton("Connect")
        self._disconnect_button = QtWidgets.QPushButton("Disconnect")
        header.addWidget(brand)
        header.addWidget(self._session)
        header.addStretch(1)
        header.addWidget(self._status)
        header.addWidget(self._new)
        header.addWidget(self._connect_button)
        header.addWidget(self._disconnect_button)
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
        self._send.setEnabled(False)
        self._prompt.setEnabled(False)
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
        self._new.clicked.connect(self._new_session)
        self._connect_button.clicked.connect(self._connect)
        self._disconnect_button.clicked.connect(self._disconnect)
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
        return {
            **info,
            "script_handle": opencode_houdini.scriptHandle(),
            "script_key": "untitled:" + opencode_houdini.scriptHandle() if is_untitled else str(Path(file_name).resolve()),
            "script_name": hou.hipFile.basename(),
            "directory": directory,
        }

    def _connect(self) -> None:
        opencode_houdini.acquirePanel(self._channel)
        self._connected = False
        self._status.setText("CONNECTING")
        self._send.setEnabled(False)
        self._prompt.setEnabled(False)
        self._client.send(self._channel, "connect", self._connect_params())

    def _new_session(self) -> None:
        opencode_houdini.acquirePanel(self._channel)
        self._status.setText("CONNECTING")
        self._client.send(self._channel, "new_session", self._connect_params())

    def _disconnect(self) -> None:
        opencode_houdini.releasePanel(self._channel)
        self._client.send(self._channel, "close", {})
        self._connected = False
        self._send.setEnabled(False)
        self._prompt.setEnabled(False)
        self._status.setText("DISCONNECTED")

    def _send_prompt(self) -> None:
        if self._busy:
            return
        text = self._prompt.toPlainText().strip()
        if not text:
            return
        self._prompt.clear()
        self._append("user", text)
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
            self._session.setText("SESSION  " + str(data.get("id", ""))[-8:])
            self._append("system", "Resumed session" if data.get("resumed") else "New session")
        elif name == "history":
            self._transcript.clear()
            for message in data.get("messages", []):
                self._append(message.get("role", "system"), message.get("text", ""))
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
            self._busy = state in ("busy", "retry")
            self._stop.setEnabled(self._busy)
            self._send.setEnabled(self._connected and not self._busy)
            self._prompt.setEnabled(self._connected and not self._busy)
        elif name == "graph_revision":
            self._status.setToolTip("Houdini graph revision " + str(data.get("revision")))
        elif name == "error":
            self._append("error", data.get("message", "Unknown OpenCode error"))
            self._status.setText("ERROR")
            self._busy = False
            self._stop.setEnabled(False)
            self._send.setEnabled(self._connected)
            self._prompt.setEnabled(self._connected)

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
            self._disconnect()

    def _destroyed(self, unused_object=None) -> None:
        opencode_houdini.releasePanel(self._channel)
        try:
            hou.hipFile.removeEventCallback(self._hip_event)
            self._client.send(self._channel, "close", {})
            self._client.eventReceived.disconnect(self._event)
            self._client.diagnostic.disconnect(self._diagnostic)
        except RuntimeError:
            pass


def create_interface() -> OpenCodePanel:
    _log("create_interface")
    return OpenCodePanel()
