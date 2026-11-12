# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender's console output as timestamped reports, so OpenCode sees what Blender says.

Blender's Python API gives no access to the Info log. What Blender and add-ons report is also written to
the process's standard output and error: operator reports ("Error: …", "Warning: …"), Blender's log lines
and Python tracebacks of any add-on's operators, timers and handlers. The capture duplicates file
descriptors 1 and 2 into pipes read by threads, which forward every byte to the original streams and
keeps a bounded ring of parsed entries (one pipe and reader per stream). It is process-wide (one per Blender process, kept across hot
reloads) and never blocks Blender: the reader never stops while the capture is installed, and every
failure leaves the original streams in place.

Entries carry a sequence number (the cursor), a time, a level (error, warning, info, output) and text with
secrets and the home folder redacted.
"""

import atexit
import collections
import os
import re
import sys
import threading
import time

from . import limits
from .process import state as process_state

MAX_ENTRIES = limits.value("console_entries")
MAX_TEXT = limits.value("console_text_chars")
SYNC_WAIT = limits.value("console_sync_s")
_SYNC_MARK = re.compile(rb"<<opencode-link-sync-[^>\n]*>>\n?")

_SECRET = re.compile(
    r"(?i)(bearer\s+|authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|token\s+)?|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;]+"
)
# Long hexadecimal or base64-like runs with digits and capitals (keys, tokens), not hyphenated names.
_LONG = re.compile(r"\b(?=[A-Za-z0-9_+/=]*\d)(?=[A-Za-z0-9_+/=]*[A-Z])[A-Za-z0-9_+/=]{32,}\b|\b[0-9a-f]{32,}\b")
_ERROR = re.compile(r"^(Error:|ERROR\b|Traceback \(most recent call last\)|Exception\b|Fatal\b)|\|\s*(ERROR|FATAL)\b")
_WARNING = re.compile(r"^(Warning:|WARNING\b|Warn\b)|\|\s*WARN(ING)?\b")
_INFO = re.compile(r"^(Info:|INFO\b)|\|\s*INFO\b")
_CONTINUATION = re.compile(r"^(\s|File \"|Location:|[A-Za-z_][\w.]*(Error|Exception|Warning|Interrupt|Exit)\b)")


def flush_c_streams():
    """Blender's C code writes reports through buffered stdio: flush it so they reach the pipe now."""
    try:
        import ctypes

        library = ctypes.cdll.ucrtbase if sys.platform == "win32" else ctypes.CDLL(None)
        library.fflush(None)
    except Exception:  # noqa: BLE001 - only makes reports arrive sooner
        pass


def unbuffer_c_stdout():
    """Blender's C reports go through stdio, block-buffered on a pipe: make stdout unbuffered so each report
    reaches the capture when it is written (the C runtime's own stdout; nothing else changes)."""
    try:
        import ctypes

        if sys.platform == "win32":
            library = ctypes.cdll.ucrtbase
            library.__acrt_iob_func.restype = ctypes.c_void_p
            stream = library.__acrt_iob_func(1)
        else:
            library = ctypes.CDLL(None)
            stream = ctypes.c_void_p.in_dll(library, "stdout").value
        library.setvbuf.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_size_t)
        library.setvbuf(stream, None, 4 if sys.platform == "win32" else 2, 0)  # _IONBF
    except Exception:  # noqa: BLE001 - reports then arrive when Blender flushes
        pass


def redact(text, home=os.path.expanduser("~")):
    if home and len(home) > 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    text = _SECRET.sub(lambda match: match.group(1) + "***", text)
    return _LONG.sub("***", text)


def level_of(line):
    if _ERROR.search(line):
        return "error"
    if _WARNING.search(line):
        return "warning"
    if _INFO.search(line):
        return "info"
    return "output"


class Capture(object):
    """The ring of entries and the reader of the pipe. Thread-safe; one instance per process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = collections.deque(maxlen=MAX_ENTRIES)
        self._seq = 0
        self._partial = {}
        self._open = None  # the entry a traceback is still being added to
        self._marks = {}
        self._originals = {}
        self._pipes = {}
        self._readers = []
        self._atexit = False
        self.installed = False
        self.error = None

    # -- installation ----------------------------------------------------------------------------
    def install(self):
        """Duplicates fds 1 and 2 into pipes. Any failure leaves the streams untouched."""
        if self.installed:
            return True
        try:
            for stream in (sys.stdout, sys.stderr):
                if stream is not None:
                    stream.flush()
            originals = {fd: os.dup(fd) for fd in (1, 2)}
            pipes = {fd: os.pipe() for fd in (1, 2)}
        except (OSError, ValueError) as error:
            self.error = str(error)
            return False
        self._originals, self._pipes = originals, pipes
        self._readers = []
        for fd, (read, _) in pipes.items():
            reader = threading.Thread(target=self._read, args=(read, originals[fd]), name="opencode-link-console-%d" % fd,
                                      daemon=True)
            reader.start()
            self._readers.append(reader)
        if not self._atexit:
            # At exit, what is still in the pipes reaches the original streams.
            atexit.register(self.uninstall)
            self._atexit = True
        try:
            for fd, (_, write) in pipes.items():
                os.dup2(write, fd)
        except OSError as error:
            self._restore()
            self.error = str(error)
            return False
        self.installed = True
        unbuffer_c_stdout()
        return True

    def uninstall(self):
        if self.installed:
            self._restore()
            self.installed = False

    def _restore(self):
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except (OSError, ValueError):
                pass
        for fd, original in self._originals.items():
            try:
                os.dup2(original, fd)
            except OSError:
                pass
        flush_c_streams()
        for _, write in self._pipes.values():
            try:
                os.close(write)  # the reader sees the end of its pipe and stops
            except OSError:
                pass
        for reader in self._readers:
            reader.join(1.0)

    def _read(self, read, original):
        while True:
            try:
                data = os.read(read, 65536)
            except OSError:
                return
            if not data:
                return
            shown = _SYNC_MARK.sub(b"", data) if b"<<opencode-link-sync-" in data else data
            try:
                if shown:
                    os.write(original, shown)  # Blender's own console, without OpenCode's sync markers
            except OSError:
                pass
            try:
                self.feed(data.decode("utf-8", "replace"), read)
            except Exception:  # noqa: BLE001 - the reader must never stop while installed
                pass

    # -- entries ---------------------------------------------------------------------------------
    def feed(self, text, stream=0):
        with self._lock:
            text = self._partial.get(stream, "") + text.replace("\r\n", "\n")
            lines = text.split("\n")
            self._partial[stream] = lines.pop()
            for line in lines:
                self._line(line)

    def _line(self, line):
        for marker in [mark for mark in self._marks if mark in line]:
            # A sync marker, alone or after a partial line written without its newline.
            self._marks.pop(marker).set()
            line = line.replace(marker, "")
            if not line.strip():
                return
        marker = line.strip()
        if not marker:
            self._open = None
            return
        clean = redact(line.rstrip())
        if self._open is not None and _CONTINUATION.match(line) and level_of(line) == "output":
            entry = self._open
            if len(entry["text"]) < MAX_TEXT:
                entry["text"] = (entry["text"] + "\n" + clean)[:MAX_TEXT]
            return
        self._seq += 1
        entry = {"seq": self._seq, "time": time.time(), "level": level_of(line), "text": clean[:MAX_TEXT]}
        self._entries.append(entry)
        self._open = entry if entry["level"] in ("error", "warning") else None

    def cursor(self):
        with self._lock:
            return self._seq

    def sync(self):
        """Waits briefly until everything written before this call has been read: a marker round trip on
        each captured stream (stdout and stderr have their own pipe and reader)."""
        if not self.installed:
            return
        base = "<<opencode-link-sync-%d-%f" % (threading.get_ident(), time.monotonic())
        marks = {fd: ("%s-%d>>" % (base, fd), threading.Event()) for fd in self._pipes}
        with self._lock:
            self._marks.update(marks.values())
        try:
            for stream in (sys.stdout, sys.stderr):
                if stream is not None:
                    stream.flush()
            flush_c_streams()
            for fd, (marker, _) in marks.items():
                os.write(fd, (marker + "\n").encode("ascii"))
        except OSError:
            pass
        deadline = time.monotonic() + SYNC_WAIT
        for _, done in marks.values():
            done.wait(max(0.0, deadline - time.monotonic()))
        with self._lock:
            for marker, _ in marks.values():
                self._marks.pop(marker, None)

    def since(self, seq, levels=None, limit=200):
        with self._lock:
            entries = [dict(entry) for entry in self._entries if entry["seq"] > seq and (not levels or entry["level"] in levels)]
            first = self._entries[0]["seq"] if self._entries else self._seq + 1
        return entries[-limit:], first

    def read(self, since=0, levels=None, limit=200):
        self.sync()
        entries, first = self.since(since, levels, limit)
        for entry in entries:
            entry["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(entry["time"]))
        return {"entries": entries, "cursor": self.cursor(), "oldest": first, "captured": self.installed,
                "dropped": max(0, first - since - 1) if since else 0}


def capture():
    """The process-wide capture (survives hot reloads: never two readers on one process)."""
    holder = process_state()
    current = getattr(holder, "console", None)
    if current is None:
        current = holder.console = Capture()
    return current


def output_problems(*texts, limit=20):
    """Errors and warnings in a run's own captured output (operators called from Python write their
    warnings there, through Python's sys.stdout)."""
    found = []
    for text in texts:
        for line in (text or "").splitlines():
            level = level_of(line)
            if level in ("error", "warning"):
                found.append({"level": level, "text": redact(line.rstrip())[:MAX_TEXT]})
    return found[:limit]


def new_problems(mark, limit=20):
    """Errors and warnings reported since `mark`, for a result."""
    live = capture()
    live.sync()
    entries, _ = live.since(mark, ("error", "warning"), limit)
    return [{"level": entry["level"], "text": entry["text"]} for entry in entries]
