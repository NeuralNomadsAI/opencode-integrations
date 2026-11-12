# SPDX-License-Identifier: GPL-3.0-or-later

"""Asynchronous work with an identifier OpenCode can follow: imports, downloads, add-on jobs, renders.

A task runs on its own daemon thread. Network work (downloads) happens there; Blender work is queued to
the main thread through the adapter. Its state is running, done, failed or cancelled, with progress, a
message, the datablocks it created (as native references) and the errors and warnings Blender reported
meanwhile. The `tasks` host method reads it, can wait a bounded time for it to finish, and cancels a task
that can be cancelled. Tasks live as long as the Blender process: a hot reload keeps them, and waits only
for those that use the add-on (a render in its own Blender does not).
"""

import collections
import secrets
import threading
import time
import traceback

from . import console, limits
from .process import state as process_state
from .wire import HostFailure, bounded_message

MAX_TASKS = limits.value("tasks")
MAX_WAIT = limits.value("task_wait_s")
MAIN_RETRY = limits.value("main_retry_s")
# Kinds of datablocks whose new members a task reports as created.
CREATED_KINDS = ("objects", "collections", "materials", "meshes", "armatures", "actions", "images", "node_groups", "worlds")


class TaskFailure(Exception):
    """A failure whose message is the whole story (no traceback)."""


class Task(object):
    def __init__(self, kind, title):
        self.id = "task-" + secrets.token_hex(6)
        self.kind = kind
        self.title = title
        self.state = "running"
        self.progress = None
        self.message = None
        self.result = None
        self.created = []
        self.started = time.time()
        self.ended = None
        self.mark = console.capture().cursor()
        self.done = threading.Event()
        # A task whose thread uses only its own process and files does not hold a hot reload back.
        self.blocks_reload = True
        self.cancelled = threading.Event()
        self.on_cancel = None  # set by a task that can be cancelled

    def update(self, message=None, progress=None):
        if message is not None:
            self.message = bounded_message(message)
        if progress is not None:
            self.progress = progress

    def cancel(self):
        """Asks the task to stop; True when it can be cancelled (it ends as cancelled)."""
        if self.on_cancel is None:
            return False
        if self.state == "running" and not self.cancelled.is_set():
            self.cancelled.set()
            self.on_cancel()
        return True

    def snapshot(self):
        out = {"task_id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
               "elapsed_s": round((self.ended or time.time()) - self.started, 1)}
        if self.on_cancel is not None and self.state == "running":
            out["cancellable"] = True
        for key in ("progress", "message", "result"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.created:
            out["created"] = self.created
        problems = console.new_problems(self.mark)
        if problems:
            out["reports"] = problems
        return out


class Tasks(object):
    def __init__(self, app):
        self.app = app
        # Process-wide, so a hot reload keeps the tasks (and their threads) of the add-on it replaces.
        holder = process_state()
        if getattr(holder, "tasks", None) is None:
            holder.tasks = (collections.OrderedDict(), threading.Lock())
        self._tasks, self._lock = holder.tasks

    # -- reading -----------------------------------------------------------------------------------
    def running(self, blocking_reload=False):
        with self._lock:
            return any(task.state == "running" and (task.blocks_reload or not blocking_reload)
                       for task in self._tasks.values())

    def read(self, params):
        task_id = params.get("task_id")
        wait = params.get("wait_seconds", 0)
        if not isinstance(wait, (int, float)) or isinstance(wait, bool) or wait < 0:
            raise HostFailure("invalid_args", "wait_seconds must be a number of seconds")
        if task_id is None:
            with self._lock:
                tasks = list(self._tasks.values())
            return {"tasks": [task.snapshot() for task in reversed(tasks)]}
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise HostFailure("invalid_args", "Unknown task %s (tasks do not survive a Blender restart)." % bounded_message(task_id)[:40])
        if params.get("cancel") is True:
            if not task.cancel():
                raise HostFailure("invalid_args", "Task %s cannot be cancelled." % task.id)
            snapshot = task.snapshot() if wait == 0 else None
            task.done.wait(min(float(wait), MAX_WAIT))
            return dict(snapshot or task.snapshot(), cancel_requested=True)
        task.done.wait(min(float(wait), MAX_WAIT))
        return task.snapshot()

    # -- running -----------------------------------------------------------------------------------
    def adopt(self, task):
        """Registers a task the caller runs itself (a local import finished within its apply)."""
        with self._lock:
            self._tasks[task.id] = task

    def start(self, kind, title, run, setup=None):
        """`run(task, tasks)` runs on the task's thread; its return value is the task's result. `setup(task)`
        runs first, on the caller's thread (a cancel callback, whether the task holds reloads back)."""
        task = Task(kind, title)
        if setup is not None:
            setup(task)
        with self._lock:
            self._tasks[task.id] = task
            while len(self._tasks) > MAX_TASKS:
                oldest = next(iter(self._tasks))
                if self._tasks[oldest].state == "running":
                    break
                self._tasks.pop(oldest)

        def body():
            try:
                task.result = run(task, self)
                task.state = "cancelled" if task.cancelled.is_set() else "done"
            except TaskFailure as error:
                task.state, task.message = ("cancelled" if task.cancelled.is_set() else "failed"), bounded_message(error)
            except Exception as error:  # noqa: BLE001 - reported on the task
                task.state = "failed"
                task.message = bounded_message("%s: %s" % (type(error).__name__, error))
                task.result = {"traceback": console.redact(traceback.format_exc()[-4000:])}
            task.ended = time.time()
            task.done.set()

        threading.Thread(target=body, name="opencode-link-" + task.id, daemon=True).start()
        return task

    def on_main(self, fn):
        """Runs `fn` on Blender's main thread, waiting while Blender is busy (a modal operator, a render)."""
        while True:
            try:
                return self.app.on_main(fn)
            except HostFailure as failure:
                if failure.code != "busy":
                    raise
                time.sleep(MAIN_RETRY)

    def watch(self, task, check, timeout, interval=1.0):
        """Polls `check()` on the main thread until it returns a value, or fails after `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.on_main(check)
            if value is not None:
                return value
            time.sleep(interval)
        raise TaskFailure("%s did not finish within %d minutes; see its reports." % (task.title, timeout // 60))

    # -- created datablocks --------------------------------------------------------------------------
    def names(self):
        """Main thread: the current datablock names of the kinds a task reports."""
        import bpy

        return {kind: {block.name for block in getattr(bpy.data, kind)} for kind in CREATED_KINDS}

    def created_since(self, before):
        """Main thread: datablocks added since `before`, with native references for objects and collections."""
        import bpy

        created = []
        for kind in CREATED_KINDS:
            for block in getattr(bpy.data, kind):
                if block.name in before.get(kind, ()):
                    continue
                entry = {"kind": kind[:-1] if kind.endswith("s") else kind, "name": block.name}
                if kind == "objects":
                    entry["type"] = block.type
                if kind in ("objects", "collections") and hasattr(self.app, "native_refs"):
                    try:
                        entry["ref"] = self.app.native_refs.ref(block)
                    except Exception:  # noqa: BLE001 - a reference is a convenience
                        pass
                created.append(entry)
        return created[:500]

    def finish_change(self, task, before, title, bump=True):
        """Main thread, after a task changed the document: one named undo step (also marking the file as
        modified, so nothing is lost on close), a new revision and the created datablocks."""
        with self.app.quiet():
            self.app.flush()
            self.app.undo_push("OpenCode: %s" % title[:50])
            if bump:
                self.app.bump_revision()
        task.created = self.created_since(before)
        return task.created


_registry = {"tasks": None}


def bind(app):
    """The add-on's registry of tasks (handlers start tasks through `current()`)."""
    _registry["tasks"] = Tasks(app)
    return _registry["tasks"]


def current():
    tasks = _registry["tasks"]
    if tasks is None:
        raise HostFailure("internal", "No task registry.")
    return tasks
