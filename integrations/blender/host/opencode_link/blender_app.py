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

from .process import state as process_state
from .wire import HostFailure
from .native_refs import NativeRefs
from .native_discovery import NativeDiscovery
from .native_scene import QUALIFIED_LEGACY, NativeExecution, capture, document_entities, incoming_covered
from . import limits

QUEUE_WAIT = limits.value("queue_wait_s")
RUN_LIMIT = limits.value("run_limit_s")

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


# Catalog operations whose effects lie in their scope: the objects their arguments name (and their hierarchy
# when it moves with them), with pose channels and animation keys for pose operations.
#   name: (object arguments, hierarchy, pose and animation)
SCOPED_OPERATIONS = {
    "animation.aim_bone": (("armature",), False, True),
    "animation.edit_keyframes": (("name",), False, True),
    "animation.attach_to_bone": (("name", "armature"), False, False),
    "scene.place_on_ground": (("name",), True, False),
    "scene.duplicate_hierarchy": (("name",), True, True),
}
# Catalog operations whose effects lie in one node tree: (the argument naming a node group, the argument naming a
# material whose tree it is). Their scope is that tree, captured in detail, so a failure's restoration is proven.
TREE_OPERATIONS = {name: ("tree_name", None) for name in (
    "geometry_nodes.add_geometry_node", "geometry_nodes.remove_geometry_node", "geometry_nodes.rename_geometry_node",
    "geometry_nodes.set_geometry_node_location", "geometry_nodes.connect_geometry_nodes",
    "geometry_nodes.disconnect_geometry_nodes", "geometry_nodes.set_node_input", "geometry_nodes.add_node_tree_socket")}
TREE_OPERATIONS.update({name: ("group_name", "material_name") for name in (
    "shader.add_shader_node", "shader.remove_shader_node", "shader.connect_shader_nodes", "shader.disconnect_shader_nodes",
    "shader.set_shader_node_input", "shader.add_shader_node_group_socket")})


def tree_scope(name, args):
    """ID keys of the one tree a tree operation changes, or None when the arguments do not name it."""
    from .native_scene import _key

    group_arg, material_arg = TREE_OPERATIONS[name]
    if args.get(group_arg):
        tree = bpy.data.node_groups.get(args[group_arg])
        return None if tree is None else {_key(tree)}
    if material_arg and args.get(material_arg):
        material = bpy.data.materials.get(args[material_arg])
        if material is None or material.node_tree is None:
            return None
        return {_key(material), _key(material.node_tree)}
    return None


_LEGACY_OBJECTS = {"scene.create_object": (), "scene.delete_object": ("name",), "scene.transform_object": ("name",),
                   "scene.set_active_camera": ("name",)}


def operation_scope(checked):
    """(ID keys, pose object keys) a catalog batch can change. Unqualified operations get an empty detail
    scope: their effects are not attested anyway (complete false), so they are never refused for the
    document's size."""
    from .native_scene import _key

    scope, poses = set(), set()
    scene = bpy.context.scene

    def add(obj, hierarchy=False):
        if obj is None:
            return
        for item in [obj] + (list(obj.children_recursive) if hierarchy else []):
            scope.add(_key(item))
            if item.data is not None:
                scope.add(_key(item.data))
            for group in item.users_collection:
                scope.add(_key(group))

    for entry, args in checked:
        name = entry["name"]
        if name in TREE_OPERATIONS:
            scope.update(tree_scope(name, args) or ())
            continue
        if name in SCOPED_OPERATIONS:
            names, hierarchy, pose = SCOPED_OPERATIONS[name]
            for arg in names:
                obj = bpy.data.objects.get(args.get(arg) or "")
                add(obj, hierarchy)
                if pose and obj is not None:
                    for item in [obj] + (list(obj.children_recursive) if hierarchy else []):
                        poses.add(_key(item))
            if name == "scene.duplicate_hierarchy":
                scope.add(_key(scene.collection))
        elif name in _LEGACY_OBJECTS:
            scope.update((_key(scene), _key(scene.collection)))
            for arg in _LEGACY_OBJECTS[name]:
                obj = bpy.data.objects.get(args.get(arg) or "")
                add(obj, name == "scene.transform_object")
                if obj is not None and name == "scene.delete_object":
                    # Deletion unlinks every user: their references are part of the effect.
                    for user in bpy.data.user_map(subset={obj}).get(obj, ()):
                        scope.add(_key(user))
    return frozenset(scope), frozenset(poses)

class BlenderApp(object):
    def __init__(self, code_execution_allowed):
        self._code_execution_allowed = code_execution_allowed
        # Set by the add-on: schedules a hot reload from disk (the `reload` host method).
        self.reload_request = None
        self._jobs = queue.Queue()
        self._quiet = 0
        # A hot reload carries the revision over: the document did not change.
        self._revision = process_state().__dict__.pop("revision", 0)
        self._evaluations = 0
        self._saves = 0
        self._saved_revision = None  # the revision at the last save or load of a file
        self._handle = new_script_handle()
        self._main = threading.main_thread()
        self.background = bool(bpy.app.background)
        # Read on the main thread only; the advertiser thread reads this copy.
        self._document, self._document_known = None, False
        self.refresh_document()
        self.native_refs = NativeRefs(document_entities)
        self.native = NativeDiscovery(self, self.native_refs)

    # -- identity and state ---------------------------------------------------------------------
    def app_version(self):
        return bpy.app.version_string

    def script_handle(self):
        return self._handle

    def revision(self):
        return self._revision

    def script_epoch(self):
        return self.native_refs.epoch

    def reset_native(self):
        self.native_refs.reset()
        self.native.reset()

    def history_changed(self):
        """Undo/redo invalidates RNA wrappers, even during our own quiet rollback."""
        self.reset_native()
        self.changed()

    def native_describe(self, query):
        return self.native.describe(query)

    def native_context(self, query):
        return self.native.context(query)

    def native_schema_hash(self):
        return self.native.schema_hash()

    def native_prepare(self, request):
        """Resolves the plan's refs, captures the document before any mutation (main thread)."""
        return NativeExecution(self.native_refs, request)

    @staticmethod
    def _read_document():
        # bpy.data is restricted while add-ons register.
        path = getattr(bpy.data, "filepath", "")
        return os.path.basename(path) if path else None

    def document(self):
        # Refreshed on the main thread; other threads (the advertiser) read the last copy.
        if threading.current_thread() is self._main:
            self.refresh_document()
        return self._document

    def refresh_document(self):
        """Main thread only. bpy.data is restricted while add-ons register: the name is known only once
        it is readable, and refreshed when an offer starts and at every drain, not only on load and save."""
        if type(bpy.data).__name__ == "_RestrictData":
            return
        self._document = self._read_document()
        self._document_known = True

    def document_known(self):
        return self._document_known

    def saved(self):
        """save_post handler: the document's file name may have changed."""
        self._saves += 1
        self._saved_revision = self._revision
        self.refresh_document()

    def refresh_display(self):
        from .handlers.captures import refresh_display

        return refresh_display()

    def rotation_snapshot(self, limit=20000):
        """Rotation channels of objects and pose bones, to tell a code run that wrote an unused one."""
        snapshot = {}
        for obj in bpy.data.objects:
            channels = [("object", obj.name, None, obj)]
            if obj.type == "ARMATURE" and obj.pose is not None:
                channels += [("bone", obj.name, bone.name, bone) for bone in obj.pose.bones]
            for kind, owner, name, item in channels:
                snapshot[(kind, owner, name)] = (item.rotation_mode, tuple(item.rotation_euler),
                                                 tuple(item.rotation_quaternion), tuple(item.rotation_axis_angle))
            if len(snapshot) > limit:
                return None
        return snapshot

    def rotation_warnings(self, before):
        """Rotation channels a run changed although the rotation mode does not read them (no visible effect)."""
        if before is None:
            return []
        after = self.rotation_snapshot()
        if after is None:
            return []
        moved = lambda old, new: any(abs(a - b) > 1e-6 for a, b in zip(old, new))  # noqa: E731
        warnings = []
        for key, (mode, euler, quaternion, axis_angle) in after.items():
            old = before.get(key)
            if old is None or old[0] != mode:
                continue
            label = ('bpy.data.objects["%s"]' % key[1]) if key[0] == "object" else (
                'bpy.data.objects["%s"].pose.bones["%s"]' % (key[1], key[2]))
            for channel, changed, used in (
                ("rotation_euler", moved(old[1], euler), mode not in ("QUATERNION", "AXIS_ANGLE")),
                ("rotation_quaternion", moved(old[2], quaternion), mode == "QUATERNION"),
                ("rotation_axis_angle", moved(old[3], axis_angle), mode == "AXIS_ANGLE"),
            ):
                if changed and not used:
                    hint = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(mode, "rotation_euler")
                    warnings.append("OpenCode: %s.%s was written but its rotation_mode is %s, so it has no effect: "
                                    "write %s, or set rotation_mode first." % (label, channel, mode, hint))
        return warnings[:20]

    def app_info(self):
        from . import lifecycle

        return lifecycle.app_info()

    def relaunch(self, unsaved, offer_key):
        from . import lifecycle

        return lifecycle.relaunch(self, unsaved, offer_key)

    def saves(self):
        """Saves seen in this session (a code run that saved must not push a step marking the file modified)."""
        return self._saves

    def mode(self):
        return bpy.context.mode or "OBJECT"

    def code_execution_allowed(self):
        return bool(self._code_execution_allowed())

    def bump_revision(self):
        self._revision += 1

    def changed(self):
        """Revision handler: any document change outside OpenCode's own calls."""
        self._evaluations += 1
        if self._quiet == 0:
            self._revision += 1

    def evaluations(self):
        """Document updates Blender reported, OpenCode's own included (did a code run change anything)."""
        return self._evaluations

    def loaded(self):
        """A new document: new handle, new revision."""
        self._handle = new_script_handle()
        self.reset_native()
        self.refresh_document()
        self._revision += 1
        self._saved_revision = self._revision if bpy.data.filepath else None

    def unsaved_changes(self):
        """Whether the document differs from its file. Blender's is_dirty, except in background mode, where it
        stays set after any undo step even across a save and a reload: there, a document unchanged since its
        last save or load counts as saved."""
        if not bpy.data.filepath:
            return True
        if not bpy.data.is_dirty:
            return False
        return not (bpy.app.background and self._saved_revision == self._revision)

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
        self.refresh_document()
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

    def with_window(self, fn):
        """Runs fn with the first window's 3D view as context in the interface (operators need one)."""
        return self._with_window(fn)

    def _with_window(self, fn):
        override = self._override()
        if override is None:
            return fn()
        with bpy.context.temp_override(**override):
            return fn()

    def undo_push(self, message):
        self._with_window(lambda: bpy.ops.ed.undo_push(message=message[:63]))

    def undo(self):
        epoch = self.script_epoch()
        result = self._with_window(lambda: bpy.ops.ed.undo())
        if epoch == self.script_epoch():
            self.history_changed()
        if "FINISHED" not in result:
            raise RuntimeError("undo did not finish")

    def flush(self):
        layer = bpy.context.view_layer
        if layer is not None:
            layer.update()

    def fingerprint(self):
        """Complete evidence of the declared structural scope, with a hard pre-count."""
        return capture()

    def native_capture(self, targets=()):
        """Read-only foundation seam for the future qualified native planner."""
        return capture(targets=targets)

    def capture_for_apply(self, checked):
        """A code run (no operations): the whole document's detail. An apply: the document's inventory and the
        detail its operations can change (operation_scope), qualified when every operation's effects lie there."""
        if not checked:
            return capture()
        reason = None
        if not all(entry["name"] in QUALIFIED_LEGACY or entry["name"] in SCOPED_OPERATIONS
                   or (entry["name"] in TREE_OPERATIONS and tree_scope(entry["name"], args) is not None)
                   for entry, args in checked):
            reason = "legacy_effect_scope_unqualified"
        elif not all(incoming_covered(bpy.data.objects.get(args.get("name")))
                     for entry, args in checked if entry["name"] == "scene.delete_object"):
            # Deletion unlinks every user of the object; only modeled references are attested.
            reason = "incoming_reference_scope_unqualified"
        scope, poses = operation_scope(checked)
        return capture(complete=reason is None, reason=reason, scope=scope, poses=poses)

    def capture_after_undo(self, before):
        return capture(targets=before.targets, complete=before.complete, reason=before.reason,
                       scope=before.scope_ids, poses=before.poses)

    # -- changes the depsgraph does not report ------------------------------------------------------
    def change_digest(self):
        """A fingerprint of what a code run may change without Blender reporting a depsgraph update (scene
        properties such as the frame range, render settings, keys; in background mode even new datablocks):
        the datablock inventory, every scene's settings and markers, object transforms, parenting and
        visibility, and the keys of every action. A run whose fingerprint differs is a change."""
        return change_digest()

    # -- thin layer (thin.py): compact context and edit batches -----------------------------------
    def thin_context(self, query):
        from . import thin

        return thin.context(query)

    def thin_precheck(self, checked):
        from . import thin

        thin.precheck(checked)

    def thin_run(self, checked):
        from . import thin

        return thin.run(checked)

    def thin_rollback(self, batch):
        from . import thin

        return thin.rollback(batch)

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


# UI datablocks (windows, screens, workspaces) are not the document's content.
_UI_DATA = frozenset(("window_managers", "screens", "workspaces"))
# Editing-tool state of a scene: brushes, snapping, the active tool; not the document's content either.
_SKIPPED_SETTINGS = frozenset(("rna_type", "tool_settings"))


def _settings(struct, depth, out):
    """The readable values of struct's own RNA properties; datablock pointers by name; nested settings structs
    (render, cycles, eevee, view settings...) to depth levels."""
    # A property group of a Python add-on (Cycles' scene.cycles) may compute its enum items when read: Cycles'
    # device list enumerates the GPUs (and starts the driver's helper processes). Its enums are not read.
    python_group = isinstance(struct, bpy.types.PropertyGroup)
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name in _SKIPPED_SETTINGS or prop.type == "COLLECTION" or (python_group and prop.type == "ENUM"):
            continue
        try:
            value = getattr(struct, name)
        except Exception:  # noqa: BLE001 - a context-dependent property
            continue
        if prop.type == "POINTER":
            if isinstance(value, bpy.types.ID):
                out.append((name, value.name))
            elif value is not None and depth > 0:
                out.append((name,))
                _settings(value, depth - 1, out)
            continue
        if isinstance(value, set):
            value = tuple(sorted(value))
        elif hasattr(value, "__len__") and not isinstance(value, str):
            value = tuple(value)
        out.append((name, value))
    return out


def _curves(action):
    try:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    except AttributeError:
        yield from getattr(action, "fcurves", ())


def change_digest():
    import array
    import hashlib

    digest = hashlib.sha256()

    def put(*values):
        digest.update(repr(values).encode("utf-8", "replace"))

    for name in dir(bpy.data):
        collection = getattr(bpy.data, name, None)
        if name in _UI_DATA or not isinstance(collection, bpy.types.bpy_prop_collection):
            continue
        put(name, sorted(item.name for item in collection))
    for scene in bpy.data.scenes:
        put(scene.name, _settings(scene, 1, []))
        put(sorted((m.name, m.frame, m.camera.name if m.camera else None) for m in scene.timeline_markers))
    for obj in bpy.data.objects:
        put(obj.name, obj.rotation_mode, tuple(obj.location), tuple(obj.rotation_euler), tuple(obj.rotation_quaternion),
            tuple(obj.rotation_axis_angle), tuple(obj.scale), obj.parent.name if obj.parent else None, obj.parent_bone,
            obj.hide_render, obj.hide_viewport, obj.data.name if obj.data is not None else None)
    for action in bpy.data.actions:
        for curve in _curves(action):
            points = array.array("f", bytes(8 * len(curve.keyframe_points)))
            curve.keyframe_points.foreach_get("co", points)
            put(action.name, curve.data_path, curve.array_index, points.tobytes())
    return digest.hexdigest()


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
