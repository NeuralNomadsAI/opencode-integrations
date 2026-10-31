# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender adapter of the host core: main-thread queue, revisions, grouped undo, expectations."""

import contextlib
import importlib
import math
import os
import queue
import secrets
import threading
import time

import bpy

from .wire import HostFailure

QUEUE_WAIT = 10.0
RUN_LIMIT = 600.0

# Data collections addressed by `kind:name` references in expectations.
KINDS = {
    "object": "objects",
    "mesh": "meshes",
    "material": "materials",
    "collection": "collections",
    "node_group": "node_groups",
    "camera": "cameras",
    "light": "lights",
    "image": "images",
    "scene": "scenes",
    "world": "worlds",
    "action": "actions",
}

# Operations whose result names a datablock they created: verified after the apply.
CREATED = {
    "scene.create_object": ("object", "name"),
    "shader.create_material": ("material", "name"),
    "shader.create_shader_material_from_template": ("material", "name"),
    "geometry_nodes.create_geometry_node_tree": ("node_group", "name"),
    "geometry_nodes.create_gn_from_template": ("node_group", "tree_name"),
    "workflows.create_string_to_curves_object": ("object", "object_name"),
}
FINGERPRINT_LIMIT = 4000


def new_script_handle():
    return "blend-" + secrets.token_hex(8)


class _Job(object):
    __slots__ = ("fn", "lock", "started", "done", "result", "error", "cancelled")

    def __init__(self, fn):
        self.fn = fn
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.cancelled = False


class BlenderApp(object):
    def __init__(self, code_execution_allowed):
        self._code_execution_allowed = code_execution_allowed
        self._jobs = queue.Queue()
        self._quiet = 0
        self._revision = 0
        self._handle = new_script_handle()
        self._main = threading.main_thread()
        self.background = bool(bpy.app.background)
        # Read on the main thread only; the advertiser thread reads this copy.
        self._document = self._read_document()

    # -- identity and state ---------------------------------------------------------------------
    def app_version(self):
        return bpy.app.version_string

    def script_handle(self):
        return self._handle

    def revision(self):
        return self._revision

    @staticmethod
    def _read_document():
        # bpy.data is restricted while add-ons register.
        path = getattr(bpy.data, "filepath", "")
        return os.path.basename(path) if path else None

    def document(self):
        # Refreshed on the main thread; other threads (the advertiser) read the last copy.
        if threading.current_thread() is self._main:
            self._document = self._read_document()
        return self._document

    def saved(self):
        """save_post handler: the document's file name may have changed."""
        self._document = self._read_document()

    def mode(self):
        return bpy.context.mode or "OBJECT"

    def code_execution_allowed(self):
        return bool(self._code_execution_allowed())

    def bump_revision(self):
        self._revision += 1

    def changed(self):
        """Revision handler: any document change outside OpenCode's own calls."""
        if self._quiet == 0:
            self._revision += 1

    def loaded(self):
        """A new document: new handle, new revision."""
        self._handle = new_script_handle()
        self._document = self._read_document()
        self._revision += 1

    @contextlib.contextmanager
    def quiet(self):
        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    # -- main-thread execution ------------------------------------------------------------------
    def on_main(self, fn):
        if threading.current_thread() is self._main:
            with self.quiet():
                return fn()
        job = _Job(fn)
        self._jobs.put(job)
        if not job.started.wait(QUEUE_WAIT):
            with job.lock:
                if not job.started.is_set():
                    job.cancelled = True
                    raise HostFailure("busy", "Blender is busy (modal operator, render or dialog); try again.")
        if not job.done.wait(RUN_LIMIT):
            raise HostFailure("busy", "Blender did not finish the request in time.")
        if job.error is not None:
            raise job.error
        return job.result

    def drain(self, budget=0.05):
        """Runs queued requests on the main thread (timer in the interface, pump in background mode)."""
        deadline = time.monotonic() + budget
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            with job.lock:
                if job.cancelled:
                    continue
                job.started.set()
            try:
                with self.quiet():
                    job.result = job.fn()
            except HostFailure as failure:
                job.error = failure
            except Exception as error:  # noqa: BLE001
                job.error = HostFailure("internal", str(error))
            finally:
                job.done.set()
            if time.monotonic() > deadline:
                return

    # -- undo -----------------------------------------------------------------------------------
    def _override(self):
        wm = bpy.context.window_manager
        if self.background or wm is None or not wm.windows:
            return None
        window = wm.windows[0]
        screen = window.screen
        area = next((a for a in screen.areas if a.type == "VIEW_3D"), screen.areas[0] if screen.areas else None)
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        override = {"window": window, "screen": screen}
        if area is not None:
            override["area"] = area
        if region is not None:
            override["region"] = region
        return override

    def _with_window(self, fn):
        override = self._override()
        if override is None:
            return fn()
        with bpy.context.temp_override(**override):
            return fn()

    def undo_push(self, message):
        self._with_window(lambda: bpy.ops.ed.undo_push(message=message[:63]))

    def undo(self):
        result = self._with_window(lambda: bpy.ops.ed.undo())
        if "FINISHED" not in result:
            raise RuntimeError("undo did not finish")

    def flush(self):
        layer = bpy.context.view_layer
        if layer is not None:
            layer.update()

    def fingerprint(self):
        """A cheap description of the document, compared before an apply and after its rollback."""
        counts = tuple(len(getattr(bpy.data, name)) for name in sorted(set(KINDS.values())))
        objects = []
        for obj in list(bpy.data.objects)[:FINGERPRINT_LIMIT]:
            matrix = tuple(round(value, 5) for row in obj.matrix_world for value in row)
            objects.append((obj.name, obj.type, matrix, len(obj.modifiers), len(obj.material_slots)))
        trees = tuple(sorted((tree.name, len(tree.nodes), len(tree.links)) for tree in bpy.data.node_groups))
        materials = tuple(sorted(material.name for material in bpy.data.materials))
        return counts, tuple(sorted(objects)), trees, materials

    # -- handlers, expectations -----------------------------------------------------------------
    def handler(self, name):
        module_name, function = name.rsplit(".", 1)
        module = importlib.import_module("%s.handlers.%s" % (__package__, module_name))
        return getattr(module, function)

    def _resolve(self, ref):
        if not isinstance(ref, str) or ":" not in ref:
            raise ValueError("a reference is kind:name")
        kind, name = ref.split(":", 1)
        collection = KINDS.get(kind)
        if collection is None:
            raise ValueError("unknown reference kind %s" % kind)
        return getattr(bpy.data, collection).get(name)

    def check_expect(self, clause):
        """None when the clause holds, else a short reason."""
        if not isinstance(clause, dict):
            return "an expect clause must be an object"
        ref = clause.get("ref")
        predicate = clause.get("predicate")
        try:
            target = self._resolve(ref)
        except ValueError as error:
            return "%s: %s" % (ref, error)
        if predicate == "exists":
            return None if target is not None else "%s does not exist" % ref
        if predicate == "absent":
            return None if target is None else "%s exists" % ref
        if predicate == "equals":
            if target is None:
                return "%s does not exist" % ref
            path = clause.get("path")
            if not isinstance(path, str) or not path or path.startswith("_"):
                return "%s: equals needs a path" % ref
            value = target
            try:
                for part in path.split("."):
                    value = getattr(value, part)
            except AttributeError:
                return "%s has no %s" % (ref, path)
            return None if _same(value, clause.get("value")) else "%s.%s differs" % (ref, path)
        return "unknown predicate %s" % predicate

    def created_refs(self, op, result):
        spec = CREATED.get(op)
        if spec is None or not isinstance(result, dict) or not isinstance(result.get(spec[1]), str):
            return []
        return ["%s:%s" % (spec[0], result[spec[1]])]

    def exec_namespace(self):
        return {"bpy": bpy, "__name__": "__opencode_execute__"}


def _same(actual, expected, tolerance=1e-4):
    if hasattr(actual, "name") and isinstance(expected, str) and not isinstance(actual, str):
        return actual.name == expected
    if isinstance(expected, (list, tuple)):
        try:
            values = list(actual)
        except TypeError:
            return False
        return len(values) == len(expected) and all(_same(a, b, tolerance) for a, b in zip(values, expected))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), float(expected), abs_tol=tolerance)
    return actual == expected
