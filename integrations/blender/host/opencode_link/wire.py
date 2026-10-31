# SPDX-License-Identifier: GPL-3.0-or-later

"""Wire protocol 1 of native hosts (standard library only, no bpy).

One request per TCP connection: the client writes one JSON line
{"id", "token", "method", "params"} of at most 1 MiB and reads one line back,
{"id", "ok": true, "result"} or {"id", "ok": false, "error": {"code", "message"}}.
The listener binds 127.0.0.1 on an ephemeral port only, and every request
must carry the random 256-bit token of this process, compared in constant
time. The token lives in memory only: it is never logged or written.
"""

import hmac
import json
import secrets
import socket
import threading

HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_TIMEOUT = 10.0
MAX_CONNECTIONS = 8
MESSAGE_MAX = 300

ERROR_CODES = (
    "unauthorized",
    "invalid_request",
    "method_not_found",
    "method_disabled",
    "invalid_args",
    "stale_handle",
    "stale_revision",
    "expectation_failed",
    "mode_conflict",
    "busy",
    "quarantined",
    "permission_denied",
    "internal",
)


class HostFailure(Exception):
    """A protocol error answered to the client with its code and a short display message."""

    def __init__(self, code, message=""):
        super().__init__(message)
        self.code = code if code in ERROR_CODES else "internal"
        self.message = bounded_message(message)


def bounded_message(text):
    clean = "".join(" " if ord(char) < 32 or char == "\x7f" else char for char in str(text or ""))
    return " ".join(clean.split())[:MESSAGE_MAX]


class Listener(object):
    """Authenticated loopback listener; `handle(method, params)` answers requests."""

    def __init__(self, handle):
        self._handle = handle
        self._token = secrets.token_hex(32)
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind((HOST, 0))
        self._socket.listen(16)
        self._socket.settimeout(0.5)
        self.port = self._socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="OpenCodeLinkListener", daemon=True)
        self._thread.start()

    @property
    def token(self):
        return self._token

    @property
    def running(self):
        return self._running

    def __repr__(self):
        return "Listener(running=%r)" % self._running

    def close(self):
        """Closes the listener and invalidates the token."""
        self._running = False
        self._token = None
        try:
            self._socket.close()
        except OSError:
            pass
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _accept_loop(self):
        while self._running:
            try:
                connection, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._serve, args=(connection,), name="OpenCodeLinkRequest", daemon=True).start()

    def _serve(self, connection):
        try:
            connection.settimeout(READ_TIMEOUT)
            line = _read_line(connection)
            if line is None:
                return
            connection.sendall(self._respond(line))
        except OSError:
            pass
        finally:
            self._slots.release()
            try:
                connection.close()
            except OSError:
                pass

    def _respond(self, line):
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _encode({"id": "", "ok": False, "error": {"code": "invalid_request"}})
        if not isinstance(request, dict):
            return _encode({"id": "", "ok": False, "error": {"code": "invalid_request"}})
        request_id = request.get("id") if isinstance(request.get("id"), str) else ""
        token = self._token
        received = request.get("token")
        if (
            token is None
            or not isinstance(received, str)
            or not hmac.compare_digest(received.encode("utf-8"), token.encode("utf-8"))
        ):
            return _encode({"id": request_id, "ok": False, "error": {"code": "unauthorized"}})
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return _encode({"id": request_id, "ok": False, "error": {"code": "invalid_request"}})
        try:
            result = self._handle(method, params)
            body = _encode({"id": request_id, "ok": True, "result": result})
        except HostFailure as failure:
            error = {"code": failure.code}
            if failure.message:
                error["message"] = failure.message
            body = _encode({"id": request_id, "ok": False, "error": error})
        except Exception:
            body = _encode({"id": request_id, "ok": False, "error": {"code": "internal"}})
        if len(body) > MAX_RESPONSE_BYTES:
            error = {"code": "internal", "message": "The response exceeds 8 MiB; read less at once."}
            body = _encode({"id": request_id, "ok": False, "error": error})
        return body


def _encode(value):
    return (json.dumps(value, default=str, separators=(",", ":")) + "\n").encode("utf-8")


def _read_line(connection):
    chunks = []
    size = 0
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            return None
        newline = chunk.find(b"\n")
        if newline >= 0:
            if size + newline > MAX_REQUEST_BYTES:
                return None
            chunks.append(chunk[:newline])
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            return None
