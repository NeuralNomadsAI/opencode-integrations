# SPDX-License-Identifier: GPL-3.0-or-later

"""The thin typed layer: a compact scene context and edit batches applied with an inverse journal.

An edit batch (thin_plan.SPEC) is validated, then resolved against the document (objects, bones,
collections, constraints that would fight an aim) before anything changes. Each operation records the
inverse of what it changes; a failure part-way runs the inverses in reverse order and re-reads every
recorded field to prove the restoration (core.py quarantines when it cannot). Nothing captures the scene:
the cost of a batch is what it touches, whatever the document's size. Success is one undo step, named by
core.py. The result lists every changed field before and after.
"""

import fnmatch
import math

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import limits
from .thin_plan import PlanError, validate  # noqa: F401 - validate is the entry point core.py uses

GEOMETRY = {"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
AXIS = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}


def _r(value):
    """A reported number: report_digits significant digits (32-bit floats hold about 7)."""
    if isinstance(value, float):
        return float("%.*g" % (limits.value("report_digits"), value)) if math.isfinite(value) else None
    if isinstance(value, (list, tuple, Vector, Euler, Quaternion)):
        return [_r(item) for item in value]
    return value


def _deg(euler):
    return [_r(math.degrees(angle)) for angle in euler]


def _obj(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise PlanError("no object %r" % name)
    if obj.library is not None:
        raise PlanError("object %r is linked from a library" % name)
    return obj


def _collection(name):
    found = bpy.data.collections.get(name)
    if found is not None:
        if found.library is not None:
            raise PlanError("collection %r is linked from a library" % name)
        return found
    scene = bpy.context.scene
    if name in (scene.collection.name, "Scene Collection"):
        return scene.collection
    raise PlanError("no collection %r" % name)


def _pose_bone(obj, name):
    if obj.type != "ARMATURE":
        raise PlanError("%r is not an armature" % obj.name)
    bone = obj.pose.bones.get(name)
    if bone is None:
        raise PlanError("armature %r has no bone %r" % (obj.name, name))
    return bone


def _hierarchy(obj, children=True):
    return [obj] + (list(obj.children_recursive) if children else [])


def world_bounds(objects):
    """Evaluated world bounds of the geometry among objects (modifiers and armature deformation included)."""
    graph = bpy.context.evaluated_depsgraph_get()
    low, high, seen = [math.inf] * 3, [-math.inf] * 3, False
    for obj in objects:
        if obj.type not in GEOMETRY:
            continue
        evaluated = obj.evaluated_get(graph)
        matrix = evaluated.matrix_world
        points = None
        if obj.type == "MESH":
            try:
                mesh = evaluated.to_mesh()
                points = [matrix @ vertex.co for vertex in mesh.vertices]
                evaluated.to_mesh_clear()
            except RuntimeError:
                points = None
        if points is None:
            points = [matrix @ Vector(corner) for corner in evaluated.bound_box]
        for point in points:
            seen = True
            for axis in range(3):
                low[axis] = min(low[axis], point[axis])
                high[axis] = max(high[axis], point[axis])
    if not seen:
        return None
    return {"min": _r(low), "max": _r(high), "size": _r([high[axis] - low[axis] for axis in range(3)])}


def _rotation(holder):
    mode = holder.rotation_mode
    if mode == "QUATERNION":
        return {"mode": mode, "quaternion": _r(holder.rotation_quaternion)}
    if mode == "AXIS_ANGLE":
        return {"mode": mode, "axis_angle": _r(holder.rotation_axis_angle)}
    return {"mode": mode, "euler_deg": _deg(holder.rotation_euler)}


def _keyed(obj, paths):
    return sorted({curve.data_path for curve in _curves(obj) if curve.data_path in paths})


# -- context -------------------------------------------------------------------------------------
def context(query):
    """A compact read of the scene: settings, counts, collections, markers and a page of objects with their
    transforms (rotation in its own mode); `ref` reads one object in detail (bounds, pose bones)."""
    if not isinstance(query, dict):
        raise PlanError("scene: expected an object")
    unknown = sorted(set(query) - {"ref", "match", "types", "include", "bone_match", "limit", "offset", "region"})
    if unknown:
        raise PlanError("scene: unknown field %s" % unknown[0])
    region = _region(query.get("region"))
    scene = bpy.context.scene
    layer = bpy.context.view_layer
    page_max = limits.value("page")
    limit = query.get("limit", limits.value("context_page"))
    offset = query.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= page_max:
        raise PlanError("limit: expected an integer from 1 to %d" % page_max)
    if type(offset) is not int or offset < 0:
        raise PlanError("offset: expected an integer of 0 or more")
    include = set(query.get("include") or ())
    if not include <= {"bounds", "bones", "parts"}:
        raise PlanError("include: expected bounds, bones and/or parts")
    detail = query.get("ref")
    if ("parts" in include or region is not None) and detail is None:
        raise PlanError("parts and region read one mesh: give ref")
    if region is not None and "parts" not in include:
        raise PlanError("region narrows parts: include parts")
    items_max = limits.value("context_items")
    if detail is not None:
        objects = [_obj(detail)]
    else:
        objects = sorted(scene.objects, key=lambda obj: obj.name)
        if query.get("match"):
            objects = [obj for obj in objects if fnmatch.fnmatchcase(obj.name, str(query["match"]))]
        if query.get("types"):
            objects = [obj for obj in objects if obj.type in set(query["types"])]
    total = len(objects)
    items = []
    for obj in objects[offset:offset + limit]:
        item = {"name": obj.name, "type": obj.type, "data": obj.data.name if obj.data is not None else None}
        if obj.parent:
            item["parent"] = obj.parent.name + ((":" + obj.parent_bone) if obj.parent_type == "BONE" else "")
        item["collections"] = [group.name for group in obj.users_collection]
        item["location"] = _r(obj.location)
        item["rotation"] = _rotation(obj)
        if any(abs(value - 1.0) > limits.value("abs_tolerance") for value in obj.scale):
            item["scale"] = _r(obj.scale)
        if not obj.visible_get(view_layer=layer):
            item["hidden"] = True
        if obj.type == "ARMATURE":
            item["bones"] = len(obj.data.bones)
        if obj.animation_data and obj.animation_data.action:
            item["action"] = obj.animation_data.action.name
        if obj.constraints:
            item["constraints"] = [constraint.type for constraint in obj.constraints]
        if "bounds" in include or detail is not None:
            item["bounds"] = world_bounds(_hierarchy(obj))
        if obj.type == "ARMATURE" and ("bones" in include or detail is not None):
            item.update(_bones(obj, query.get("bone_match")))
        if "parts" in include:
            item.update(_parts(obj, region))
        items.append(item)
    out = {
        "scene": {"name": scene.name, "frames": [scene.frame_start, scene.frame_end, scene.frame_current],
                  "fps": _r(scene.render.fps / (scene.render.fps_base or 1.0)),
                  "camera": scene.camera.name if scene.camera else None,
                  "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
                  "engine": scene.render.engine, "unit_scale": _r(scene.unit_settings.scale_length)},
        "counts": {"objects": len(scene.objects), "collections": len(bpy.data.collections),
                   "actions": len(bpy.data.actions), "materials": len(bpy.data.materials)},
        "active": layer.objects.active.name if layer.objects.active else None,
        "objects": items,
        "objects_total": total,
    }
    selected = [obj.name for obj in scene.objects if obj.select_get(view_layer=layer)]
    out["selected"] = selected[:items_max]
    if len(selected) > items_max:
        out["selected_total"] = len(selected)
    if offset + limit < total:
        out["next_offset"] = offset + limit
    if detail is None and offset == 0:
        groups = [scene.collection] + list(scene.collection.children_recursive)
        out["collections"] = [{"name": group.name, "objects": len(group.objects),
                               "children": [child.name for child in group.children]} for group in groups[:items_max]]
        markers = sorted(scene.timeline_markers, key=lambda marker: marker.frame)
        out["markers"] = [dict({"name": marker.name, "frame": marker.frame},
                               **({"camera": marker.camera.name} if marker.camera else {})) for marker in markers[:items_max]]
        for key, found in (("collections_total", len(groups)), ("markers_total", len(markers))):
            if found > items_max:
                out[key] = found
    return out


def _region(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise PlanError("region: expected {min: [x, y, z], max: [x, y, z]} in world space")
    low, high = [[float(item) for item in value[key]] for key in ("min", "max")]
    if len(low) != 3 or len(high) != 3 or any(a > b for a, b in zip(low, high)):
        raise PlanError("region: min and max are 3 numbers each, min at most max")
    return low, high


def _parts(obj, region):
    """The loose parts (islands) of a mesh as evaluated now (deformed by its armature and modifiers): for each,
    its vertex count, world bounds and centre, its main material and its dominant vertex group, largest first;
    region keeps the parts whose bounds meet that world box. A baked prop inside a body is found by its box."""
    import numpy

    if obj.type != "MESH":
        raise PlanError("%r is a %s: parts read a mesh" % (obj.name, obj.type))
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        count = len(mesh.vertices)
        co = numpy.empty(count * 3, dtype=numpy.float64)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(count, 3)
        matrix = numpy.array(evaluated.matrix_world)
        world = co @ matrix[:3, :3].T + matrix[:3, 3]
        edges = numpy.empty(len(mesh.edges) * 2, dtype=numpy.int64)
        mesh.edges.foreach_get("vertices", edges)
        parent = numpy.arange(count)

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a, b in edges.reshape(-1, 2).tolist():
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        labels = numpy.array([root(i) for i in range(count)])
        materials = numpy.zeros(len(mesh.polygons), dtype=numpy.int64)
        mesh.polygons.foreach_get("material_index", materials)
        loops = numpy.empty(len(mesh.polygons), dtype=numpy.int64)
        mesh.polygons.foreach_get("loop_start", loops)
        corner = numpy.empty(len(mesh.loops), dtype=numpy.int64)
        mesh.loops.foreach_get("vertex_index", corner)
        polygon_part = labels[corner[loops]] if len(loops) else numpy.empty(0, dtype=numpy.int64)
        same = len(obj.data.vertices) == count
        groups = {group.index: group.name for group in obj.vertex_groups}
        weights = {}
        if same and groups:
            for vertex in obj.data.vertices:
                if vertex.groups:
                    best = max(vertex.groups, key=lambda entry: entry.weight)
                    weights[vertex.index] = best.group
        slot_votes = {}
        for label, slot in zip(polygon_part.tolist(), materials.tolist()):
            votes = slot_votes.setdefault(label, {})
            votes[slot] = votes.get(slot, 0) + 1
        order = numpy.argsort(labels, kind="stable")
        ordered = labels[order]
        starts = numpy.flatnonzero(numpy.r_[True, ordered[1:] != ordered[:-1]]) if count else numpy.empty(0, dtype=int)
        ends = numpy.r_[starts[1:], count]
        low_box, high_box = (numpy.array(region[0]), numpy.array(region[1])) if region is not None else (None, None)
        parts = []
        for start, end in zip(starts.tolist(), ends.tolist()):
            members = order[start:end]
            label = int(ordered[start])
            points = world[members]
            low, high = points.min(axis=0), points.max(axis=0)
            if region is not None and (numpy.any(low > high_box) or numpy.any(high < low_box)):
                continue
            part = {"vertices": int(len(members)), "min": _r(low.tolist()), "max": _r(high.tolist()),
                    "center": _r(points.mean(axis=0).tolist())}
            votes = slot_votes.get(label)
            if votes:
                slot = max(votes, key=votes.get)
                if slot < len(obj.material_slots) and obj.material_slots[slot].material is not None:
                    part["material"] = obj.material_slots[slot].material.name
            if weights:
                tally = {}
                for i in members.tolist():
                    if i in weights:
                        tally[weights[i]] = tally.get(weights[i], 0) + 1
                if tally:
                    part["vertex_group"] = groups.get(max(tally, key=tally.get))
            parts.append(part)
    finally:
        evaluated.to_mesh_clear()
    parts.sort(key=lambda part: -part["vertices"])
    shown = parts[:limits.value("context_items")]
    out = {"parts": shown, "parts_total": len(parts)}
    return out


def _bones(obj, pattern):
    matrix = obj.matrix_world
    bones = []
    for bone in obj.pose.bones:
        if pattern and not fnmatch.fnmatchcase(bone.name, str(pattern)):
            continue
        head, tail = matrix @ bone.head, matrix @ bone.tail
        direction = tail - head
        entry = {"name": bone.name, "parent": bone.parent.name if bone.parent else None, "head": _r(head),
                 "dir": _r(direction.normalized() if direction.length else direction), "len": _r(direction.length)}
        if bone.rotation_mode != "QUATERNION" or any(abs(a - b) > limits.value("abs_tolerance")
                                                     for a, b in zip(bone.rotation_quaternion, (1, 0, 0, 0))):
            entry["rotation"] = _rotation(bone)
        if bone.constraints:
            entry["constraints"] = [constraint.type for constraint in bone.constraints]
        bones.append(entry)
    shown = bones[:limits.value("context_bones")]
    out = {"pose_bones": shown}
    if len(bones) > len(shown):
        out["pose_bones_total"] = len(bones)
    return out


# -- edit batches --------------------------------------------------------------------------------
class Batch(object):
    def __init__(self):
        self.aliases = {}
        self.changes = []
        self.created = []
        self.warnings = []
        self.journal = []  # (inverse, proof, label, key): run in reverse order on failure, then each proof re-read
        self._keys = set()
        self.deletes = []
        self.collection_deletes = []
        self.deleted = 0  # deletions cannot be inverted: a failure after one is never restored
        self.made = set()  # pointers of the datablocks this batch created: undoing it removes them

    def ref(self, name):
        if name.startswith("$"):
            if name[1:] not in self.aliases:
                raise PlanError("unknown alias %s" % name)
            return self.aliases[name[1:]]
        return _obj(name)

    def record(self, index, ref, field, before, after):
        self.changes.append({"op": index, "ref": ref, "field": field, "before": _r(before), "after": _r(after)})

    def add(self, inverse, proof, label, key=None):
        """A journal entry. Of several entries on one key (a field changed twice), only the first proves it: the
        inverses run in reverse order, so the field ends at that first record's value."""
        if key is not None and key in self._keys:
            proof = None
        elif key is not None:
            self._keys.add(key)
        self.journal.append((inverse, proof, label))

    def remember(self, holder, attr, owner=None):
        """Records holder.attr for the journal; returns its value before the change. owner: the datablock holding it,
        when holder.id_data is an embedded one (a material's node tree), whose wrapper outlives its removal."""
        before = _copy(getattr(holder, attr))
        label = "%s.%s" % (getattr(holder, "name", "?"), attr)
        owner = owner if owner is not None else getattr(holder, "id_data", None)
        made = owner is not None and _pointer(owner) in self.made

        def proof():
            # A field of a datablock this batch created is undone with it: removed is restored.
            if made and _removed(owner):
                return True
            # An animated field takes its keys' value at the frame the rollback puts back: its keys prove it.
            return _same(getattr(holder, attr), before) or _animated(holder, attr)

        def undo():
            if not (made and _removed(owner)):
                setattr(holder, attr, before)

        self.add(undo, proof, label, ("field", _pointer(holder), attr))
        return before

    def make(self, *datablocks):
        for datablock in datablocks:
            if datablock is not None:
                self.made.add(_pointer(datablock))


def _animated(holder, attr):
    try:
        path = holder.path_from_id(attr)
    except (AttributeError, ValueError, TypeError):
        return False
    return any(curve.data_path == path for curve in _curves(holder.id_data))


def _pointer(datablock):
    try:
        return datablock.as_pointer()
    except (AttributeError, ReferenceError):
        return id(datablock)


def _copy(value):
    if isinstance(value, str) or value is None:
        return value
    if isinstance(value, (Vector, Euler, Quaternion, Matrix)):
        return value.copy()
    try:
        return tuple(value)
    except TypeError:
        return value


def _same(actual, expected):
    tolerance = limits.value("abs_tolerance")
    if isinstance(expected, Matrix):
        return all(abs(a - b) <= limits.value("matrix_tolerance") * max(1.0, abs(b))
                   for row_a, row_b in zip(actual, expected) for a, b in zip(row_a, row_b))
    if isinstance(expected, (tuple, list, Vector, Euler, Quaternion)):
        return len(tuple(actual)) == len(tuple(expected)) and all(_same(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, float) or isinstance(actual, float):
        return abs(float(actual) - float(expected)) <= tolerance * max(1.0, abs(float(expected)))
    return actual == expected


def _set_rotation(holder, rotation, batch, index, label):
    """Writes a rotation in the holder's own mode. Euler angles in the holder's own order are written as given
    (a full turn stays 360 degrees, so keys interpolate through it); another order or a quaternion becomes the
    equivalent angles nearest the current ones (continuous with existing keys). A quaternion or axis-angle
    holder keeps the orientation only: a turn of 180 degrees or more is said."""
    euler = None
    if "euler_deg" in rotation:
        euler = Euler([math.radians(angle) for angle in rotation["euler_deg"]], rotation["order"])
        quaternion = euler.to_quaternion()
    else:
        quaternion = Quaternion(rotation["quaternion"]).normalized()
    mode = holder.rotation_mode
    large = euler is not None and any(abs(angle) >= 180 for angle in rotation["euler_deg"])
    if mode == "QUATERNION":
        before = batch.remember(holder, "rotation_quaternion")
        if Quaternion(before).dot(quaternion) < 0:
            quaternion.negate()  # the same orientation, on the side of the current value: keys turn the short way
        holder.rotation_quaternion = quaternion
        batch.record(index, label, "rotation_quaternion", before, holder.rotation_quaternion)
    elif mode == "AXIS_ANGLE":
        before = batch.remember(holder, "rotation_axis_angle")
        axes = [i for i, angle in enumerate(rotation.get("euler_deg") or ()) if angle]
        if euler is not None and len(axes) == 1:
            # One axis: its angle as given, a full turn included.
            axis = [0.0, 0.0, 0.0]
            axis[axes[0]] = 1.0
            holder.rotation_axis_angle = (euler[axes[0]], axis[0], axis[1], axis[2])
            large = False
        else:
            axis, angle = quaternion.to_axis_angle()
            holder.rotation_axis_angle = (angle, axis[0], axis[1], axis[2])
        batch.record(index, label, "rotation_axis_angle", before, holder.rotation_axis_angle)
    else:
        before = batch.remember(holder, "rotation_euler")
        if euler is not None and rotation["order"] == mode:
            holder.rotation_euler = euler
        else:
            holder.rotation_euler = quaternion.to_euler(mode, holder.rotation_euler)
        large = False
        batch.record(index, label, "rotation_euler_deg (%s)" % mode, _deg(before), _deg(holder.rotation_euler))
    if large:
        batch.warnings.append("%s is in %s mode, which keeps an orientation, not a number of turns: a rotation of 180 "
                              "degrees or more about an axis is the same as its remainder, and keys interpolate the short "
                              "way. Key a full turn at most 180 degrees apart, or set its rotation_mode to an Euler order "
                              "first." % (label, mode))


def _set_vector(holder, attr, value, batch, index, label, delta=False):
    before = batch.remember(holder, attr)
    setattr(holder, attr, [before[axis] + value[axis] for axis in range(3)] if delta else value)
    batch.record(index, label, attr, before, getattr(holder, attr))


def op_set_transform(args, batch, index):
    obj = batch.ref(args["ref"])
    if "location" in args:
        _set_vector(obj, "location", args["location"], batch, index, obj.name, args["delta"])
    if "rotation" in args:
        _set_rotation(obj, args["rotation"], batch, index, obj.name)
    if "scale" in args:
        _set_vector(obj, "scale", args["scale"], batch, index, obj.name)


def op_fit_height(args, batch, index):
    obj = batch.ref(args["ref"])
    objects = _hierarchy(obj, args["children"])
    scene = bpy.context.scene
    current = scene.frame_current
    frame = args.get("frame", current)
    if frame != current:
        # Measured on the pose at that frame; the current frame comes back after (and with a failing batch).
        batch.add(lambda: scene.frame_set(current), lambda: scene.frame_current == current, "%s.frame_current" % scene.name,
                  ("field", _pointer(scene), "frame_current"))
        scene.frame_set(frame)
    _fit(obj, objects, args, batch, index)
    if args["key"]:
        new_curve = _curve(obj, "location", 0) is None
        _key(batch, index, obj, obj, "location", "", None, frame, None, obj.name, "location")
        if "height" in args:
            _key(batch, index, obj, obj, "scale", "", None, frame, None, obj.name, "scale")
        if new_curve:
            batch.warnings.append("%s: its location is keyed at frame %d only, so it holds at every frame; key it at its "
                                  "other frames too (fit_height with key at each, or insert_key)." % (obj.name, frame))
    elif _keyed(obj, ("location", "scale")):
        batch.warnings.append("%s is keyed on %s: its animation overrides this at the next frame change; give key true "
                              "(with frame) or edit its keys." % (obj.name, ", ".join(_keyed(obj, ("location", "scale")))))
    batch.changes.append({"op": index, "ref": obj.name, "field": "bounds", "frame": frame, "after": world_bounds(objects)})
    if frame != current:
        scene.frame_set(current)


def _fit(obj, objects, args, batch, index):
    bpy.context.view_layer.update()
    bounds = world_bounds(objects)
    if bounds is None:
        raise PlanError("%r has no geometry to measure" % obj.name)
    if args["ground"] and obj.parent is not None:
        raise PlanError("%r has a parent: ground its root instead" % obj.name)
    if "height" in args:
        if bounds["size"][2] <= 0:
            raise PlanError("%r has no height" % obj.name)
        factor = args["height"] / bounds["size"][2]
        if args["scale_axes"] == "z":
            # The object's own Z must be the world's vertical, or a Z scale would not change the height alone.
            vertical = obj.matrix_world.to_3x3().col[2].normalized()
            if abs(abs(vertical.z) - 1.0) > limits.value("matrix_tolerance"):
                raise PlanError("%r is tilted: its own Z axis is not vertical, so scaling Z alone would not set its height. "
                                "Pass scale_axes 'uniform' (keeps its proportions) or stretch it with set_transform." % obj.name)
            scale = [obj.scale[0], obj.scale[1], obj.scale[2] * factor]
        else:
            scale = [value * factor for value in obj.scale]
        _set_vector(obj, "scale", scale, batch, index, obj.name)
        batch.changes[-1].update(axes=args["scale_axes"], factor=_r(factor))
        if args["scale_axes"] == "uniform" and abs(factor - 1.0) > limits.value("abs_tolerance"):
            batch.warnings.append("%s: X and Y were scaled too (x%s), keeping its proportions; scale_axes 'z' stretches the "
                                  "height only." % (obj.name, _r(factor)))
        bpy.context.view_layer.update()
        bounds = world_bounds(objects)
    if args["ground"]:
        _set_vector(obj, "location", [0.0, 0.0, args["ground_z"] - bounds["min"][2]], batch, index, obj.name, delta=True)
    bpy.context.view_layer.update()


def op_pose_bone(args, batch, index):
    obj = batch.ref(args["ref"])
    bone = _pose_bone(obj, args["bone"])
    label = "%s:%s" % (obj.name, bone.name)
    if "location" in args:
        _set_vector(bone, "location", args["location"], batch, index, label)
    if "rotation" in args:
        _set_rotation(bone, args["rotation"], batch, index, label)
    if "scale" in args:
        _set_vector(bone, "scale", args["scale"], batch, index, label)


def _target_point(batch, target):
    if "point" in target:
        return Vector(target["point"])
    obj = batch.ref(target["object"])
    if target.get("bone"):
        return obj.matrix_world @ _pose_bone(obj, target["bone"]).head
    return obj.matrix_world.translation.copy()


def _axis(name):
    return AXIS[name[-1]] * (-1.0 if name.startswith("-") else 1.0)


def op_aim_bone(args, batch, index):
    obj = batch.ref(args["ref"])
    bone = _pose_bone(obj, args["bone"])
    label = "%s:%s" % (obj.name, bone.name)
    bpy.context.view_layer.update()
    world = obj.matrix_world
    target = args["target"]
    wanted = Vector(target["direction"]) if "direction" in target else _target_point(batch, target) - world @ bone.head
    if wanted.length == 0:
        raise PlanError("%s: the target is at the bone's head" % label)
    want = (world.to_3x3().inverted() @ wanted).normalized()
    axis = _axis(args["axis"])
    matrix = bone.matrix.copy()
    current = (matrix.to_3x3() @ axis).normalized()
    turn = current.rotation_difference(want).to_matrix().to_4x4()
    head = matrix.translation.copy()
    for attr in ("rotation_quaternion", "rotation_euler", "rotation_axis_angle", "location", "scale"):
        batch.remember(bone, attr)
    location, scale = bone.location.copy(), bone.scale.copy()
    before = _rotation(bone)
    bone.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ matrix
    bone.location, bone.scale = location, scale  # aiming only rotates about the head
    bpy.context.view_layer.update()
    got = (obj.matrix_world.to_3x3() @ bone.matrix.to_3x3() @ axis).normalized()
    error = math.degrees(got.angle(wanted.normalized())) if got.length else None
    batch.changes.append({"op": index, "ref": label, "field": "aim (%s axis)" % args["axis"], "before": before,
                          "after": _rotation(bone), "error_deg": _r(error)})
    tolerance = args.get("tolerance_deg", limits.value("aim_tolerance_deg"))
    if error is None or error > tolerance:
        raise PlanError("%s: aim missed by %.3g degrees (tolerance %.3g)" % (label, error if error is not None else -1, tolerance))


def _world_bone(obj, bone):
    return obj.matrix_world @ bone.matrix


def _turn_world(obj, bone, rotation):
    """Gives a pose bone the world rotation `rotation` (a Quaternion), its head and scale kept."""
    location, scale = bone.location.copy(), bone.scale.copy()
    head, _rotation, size = _world_bone(obj, bone).decompose()
    bone.matrix = obj.matrix_world.inverted() @ Matrix.LocRotScale(head, rotation, size)
    bone.location, bone.scale = location, scale  # a turn about the head only
    bpy.context.view_layer.update()


def _aim_world(obj, bone, point):
    """The least rotation that points a pose bone's Y axis (head to tail) at a world point."""
    _head, rotation, _size = _world_bone(obj, bone).decompose()
    head = obj.matrix_world @ bone.head
    want = point - head
    if want.length == 0:
        return
    _turn_world(obj, bone, rotation.to_matrix().col[1].normalized().rotation_difference(want.normalized()) @ rotation)


def _goal_point(batch, goal):
    """(world point, world orientation or None) of a reach target."""
    rotation = None
    if "rotation" in goal:
        given = goal["rotation"]
        rotation = (Euler([math.radians(a) for a in given["euler_deg"]], given["order"]).to_quaternion()
                    if "euler_deg" in given else Quaternion(given["quaternion"]).normalized())
    if "point" in goal:
        return Vector(goal["point"]), rotation
    holder = batch.ref(goal["object"])
    matrix = _world_bone(holder, _pose_bone(holder, goal["bone"])) if goal.get("bone") else holder.matrix_world
    return matrix.to_translation(), rotation if rotation is not None else matrix.to_quaternion()


def op_reach(args, batch, index):
    obj = batch.ref(args["ref"])
    chain = [_pose_bone(obj, name) for name in args["chain"]]
    for child, parent in zip(chain[1:], chain):
        if child.parent != parent:
            raise PlanError("%s: %r is not the parent of %r" % (obj.name, parent.name, child.name))
    label = "%s:%s" % (obj.name, "/".join(args["chain"]))
    scene = bpy.context.scene
    current = scene.frame_current
    frame = args.get("frame", current)
    key = args.get("key", "frame" in args)
    if frame != current:
        batch.add(lambda: scene.frame_set(current), lambda: scene.frame_current == current, "%s.frame_current" % scene.name,
                  ("field", _pointer(scene), "frame_current"))
        scene.frame_set(frame)
    bpy.context.view_layer.update()
    for bone in chain:
        for attr in ("rotation_quaternion", "rotation_euler", "rotation_axis_angle", "location", "scale"):
            batch.remember(bone, attr)
    before = {bone.name: _rotation(bone) for bone in chain}
    upper, lower = chain[0], chain[1]
    world = obj.matrix_world
    shoulder = world @ upper.head
    first = (world @ upper.tail - shoulder).length
    second = (world @ lower.tail - world @ lower.head).length
    target, orientation = _goal_point(batch, args["target"])
    if "pole" in args:
        pole = args["pole"]
        if "direction" in pole:
            pole_point = shoulder + Vector(pole["direction"])
        else:
            pole_point = _target_point(batch, pole)
    else:
        pole_point = world @ lower.head  # the side the elbow bends to now
    reach = target - shoulder
    if reach.length == 0:
        raise PlanError("%s: the target is at the shoulder" % label)
    along = reach.normalized()
    distance = min(max(reach.length, abs(first - second) * 1.000001), (first + second) * 0.999999)
    cosine = max(-1.0, min(1.0, (first * first + distance * distance - second * second) / (2 * first * distance)))
    side = pole_point - shoulder
    side -= along * side.dot(along)
    if side.length < 1e-9:
        side = (world @ lower.head - shoulder)
        side -= along * side.dot(along)
    if side.length < 1e-9:
        side = along.orthogonal()
    elbow = shoulder + along * (cosine * first) + side.normalized() * (math.sqrt(1 - cosine * cosine) * first)
    _aim_world(obj, upper, elbow)
    _aim_world(obj, lower, shoulder + along * distance)
    end_error = None
    if len(chain) == 3 and orientation is not None:
        _turn_world(obj, chain[2], orientation)
        end_error = math.degrees(_world_bone(obj, chain[2]).to_quaternion().rotation_difference(orientation).angle)
        end_error = min(end_error, 360 - end_error)
    reached = obj.matrix_world @ lower.tail
    error = (reached - target).length
    change = {"op": index, "ref": label, "field": "reach", "before": before,
              "after": {bone.name: _rotation(bone) for bone in chain}, "error": _r(error), "elbow": _r(elbow),
              "reached": _r(reached), "chain_length": _r(first + second)}
    if end_error is not None:
        change["end_error_deg"] = _r(end_error)
    batch.changes.append(change)
    tolerance = args.get("tolerance", 0.001)
    if error > tolerance:
        raise PlanError("%s: missed the target by %.4g m (tolerance %.4g; the chain is %.4g m long, the target %.4g m "
                        "from %s)" % (label, error, tolerance, first + second, reach.length, upper.name))
    if end_error is not None and end_error > limits.value("aim_tolerance_deg"):
        raise PlanError("%s: %s's orientation missed by %.3g degrees (locked or constrained rotation?)" % (
            label, chain[2].name, end_error))
    if key:
        prefix = 'pose.bones["%s"].'
        for bone in chain:
            prop = _rotation_prop(bone)
            _key(batch, index, obj, bone, prop, prefix % bone.name.replace("\\", "\\\\").replace('"', '\\"'), None, frame,
                 None, "%s:%s" % (obj.name, bone.name), prop)
    elif frame != current:
        batch.warnings.append("%s: posed at frame %d without keys; going back to frame %d re-evaluates its animation." % (
            label, frame, current))
    if frame != current:
        scene.frame_set(current)


def _curves(owner):
    """The F-curves of owner's action for its slot (Blender 5 layered actions), or of a legacy action."""
    data = owner.animation_data
    if not data or not data.action:
        return []
    try:
        from bpy_extras import anim_utils

        bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
        return list(bag.fcurves) if bag else []
    except (ImportError, AttributeError):
        return list(getattr(data.action, "fcurves", []))


def _curve(owner, data_path, array_index):
    for curve in _curves(owner):
        if curve.data_path == data_path and curve.array_index == array_index:
            return curve
    return None


# Everything a keyframe holds, so a shift or a deletion can be undone exactly.
_KEY_SETTINGS = ("handle_left_type", "handle_right_type", "interpolation", "easing", "type", "back", "amplitude", "period")


def _key_state(curve):
    return [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right))
            + tuple(getattr(point, attr) for attr in _KEY_SETTINGS) for point in curve.keyframe_points]


def _restore_keys(curve, state):
    points = curve.keyframe_points
    points.clear()
    points.add(len(state))
    for point, saved in zip(points, state):
        for attr, value in zip(_KEY_SETTINGS, saved[3:]):
            setattr(point, attr, value)
        point.co, point.handle_left, point.handle_right = saved[0], saved[1], saved[2]
    curve.update()


def _key_at(curve, frame):
    if curve is None:
        return None
    for point in curve.keyframe_points:
        if abs(point.co[0] - frame) <= limits.value("abs_tolerance"):
            return point
    return None


def _owner_id(obj, owner):
    """The datablock whose animation holds a path: the object, its data, or its data's shape keys."""
    if owner == "object":
        return obj
    data = obj.data
    if data is None:
        raise PlanError("%r has no data" % obj.name)
    if owner == "data":
        return data
    keys = getattr(data, "shape_keys", None)
    if keys is None:
        raise PlanError("%r has no shape keys" % obj.name)
    return keys


def _split_path(path):
    """(parent path, last segment, is an item): 'constraints["Grab"].influence' -> ('constraints["Grab"]',
    'influence', False); '["prop"]' -> ('', 'prop', True)."""
    depth, quote, cut = 0, None, -1
    for position, char in enumerate(path):
        if quote:
            if char == "\\":
                continue
            if char == quote and path[position - 1] != "\\":
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "[":
            if depth == 0:
                cut = position
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "." and depth == 0:
            cut = position
    if cut < 0:
        return "", path, False
    if path[cut] == ".":
        return path[:cut], path[cut + 1:], False
    inner = path[cut + 1:-1] if path.endswith("]") else None
    if not inner or inner[0] not in "\"'" or inner[-1] != inner[0]:
        raise PlanError("%s: give an array element with index, not [n]" % path)
    return path[:cut], inner[1:-1], True


def _property(holder, path, label):
    """(struct, name, is_item) of path on holder, refused when it does not resolve or cannot be animated."""
    parent_path, name, item = _split_path(path)
    try:
        parent = holder.path_resolve(parent_path) if parent_path else holder
    except ValueError:
        raise PlanError("%s has no %s" % (label, parent_path)) from None
    if item:
        try:
            parent[name]
        except (KeyError, TypeError):
            raise PlanError("%s has no property %s" % (label, path)) from None
        return parent, name, True
    rna = getattr(getattr(parent, "bl_rna", None), "properties", {}).get(name)
    if rna is None:
        raise PlanError("%s has no property %s" % (label, path))
    if not rna.is_animatable:
        raise PlanError("%s: %s cannot be animated" % (label, path))
    return parent, name, False


def _read(parent, name, item):
    value = parent[name] if item else getattr(parent, name)
    return _copy(value)


def _write(parent, name, item, value):
    if item:
        parent[name] = value
    else:
        setattr(parent, name, value)


def _action_journal(batch, owner):
    """A key on an owner without an action makes one: undoing the batch removes it again."""
    had_data = owner.animation_data is not None
    if had_data and owner.animation_data.action is not None:
        return

    def drop_action():
        data = owner.animation_data
        if data is not None and data.action is not None:
            action = data.action
            data.action = None
            if action.users == 0:
                bpy.data.actions.remove(action)
        if not had_data and owner.animation_data is not None:
            owner.animation_data_clear()

    batch.add(drop_action, lambda: (owner.animation_data is None) if not had_data else owner.animation_data.action is None,
              "%s new action" % owner.name, ("action", _pointer(owner)))


def _key(batch, index, owner, holder, rel_path, prefix, indices, frame, interpolation, label, field):
    """Keys holder's rel_path (on owner's action, as prefix + rel_path) at frame, element by element in indices
    (None: the whole property); records the inverse: keys it made removed, keys it replaced restored."""
    full = prefix + rel_path
    elements = indices if indices is not None else range(max(1, _size(holder, rel_path)))
    existed = []
    for element in elements:
        curve = _curve(owner, full, element)
        point = _key_at(curve, frame)
        existed.append((element, curve is not None, tuple(point.co) if point else None,
                        point.interpolation if point else None))
    _action_journal(batch, owner)
    keyed = holder.keyframe_insert(rel_path, index=-1 if indices is None else indices[0], frame=frame)
    if not keyed:
        raise PlanError("%s: could not key %s" % (label, rel_path))
    if interpolation:
        for element, *_rest in existed:
            point = _key_at(_curve(owner, full, element), frame)
            if point is not None:
                point.interpolation = interpolation

    def undo(existed=existed):
        for element, had_curve, co, interpolation in existed:
            curve = _curve(owner, full, element)
            point = _key_at(curve, frame)
            if co is None and point is not None:
                curve.keyframe_points.remove(point)
                if not had_curve and not curve.keyframe_points:
                    _remove_curve(owner, curve)
            elif co is not None and point is not None:
                point.co = co
                point.interpolation = interpolation

    def proof(existed=existed):
        for element, _had, co, _interpolation in existed:
            point = _key_at(_curve(owner, full, element), frame)
            if (co is None) != (point is None) or (co is not None and not _same(tuple(point.co), co)):
                return False
        return True

    batch.add(undo, proof, "%s key %s" % (label, rel_path),
              ("key", _pointer(owner), full, frame, tuple(indices) if indices is not None else None))
    value = _read(*_property(holder, rel_path, label)) if field is None else getattr(holder, rel_path)
    batch.changes.append({"op": index, "ref": label, "field": "key %s" % (field or rel_path), "frame": frame,
                          "value": _r(list(value) if isinstance(value, (tuple, list, Vector, Euler, Quaternion)) else value),
                          "replaced": any(co for _e, _h, co, _i in existed)})


def _size(holder, rel_path):
    parent, name, item = _property(holder, rel_path, "")
    value = parent[name] if item else getattr(parent, name)
    try:
        return len(value) if not isinstance(value, str) else 1
    except TypeError:
        return 1


def _key_holder(obj, args):
    """(owner datablock, struct the path is relative to, owner path prefix, label)."""
    if args.get("bone"):
        bone = _pose_bone(obj, args["bone"])
        return obj, bone, 'pose.bones["%s"].' % bone.name.replace("\\", "\\\\").replace('"', '\\"'), "%s:%s" % (obj.name, bone.name)
    owner = _owner_id(obj, args.get("owner", "object"))
    label = obj.name if owner is obj else "%s (%s %s)" % (obj.name, args.get("owner"), owner.name)
    return owner, owner, "", label


def _rotation_prop(holder):
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(holder.rotation_mode, "rotation_euler")


def op_insert_key(args, batch, index):
    obj = batch.ref(args["ref"])
    frame = args["frame"]
    owner, holder, prefix, label = _key_holder(obj, args)
    interpolation = args.get("interpolation")
    if "path" in args:
        path = args["path"]
        parent, name, item = _property(holder, path, label)
        if "value" in args:
            before = _read(parent, name, item)
            wanted = args["value"]
            if "index" in args:
                array = list(before)
                if not 0 <= args["index"] < len(array):
                    raise PlanError("%s: %s has %d elements" % (label, path, len(array)))
                array[args["index"]] = wanted
                wanted = array
            try:
                _write(parent, name, item, wanted)
            except (TypeError, ValueError) as error:
                raise PlanError("%s: %s does not take %s (%s)" % (label, path, args["value"], error)) from None
            batch.add(lambda: _write(parent, name, item, before), lambda: _same(_read(parent, name, item), before),
                      "%s.%s" % (label, path), ("field", _pointer(parent), name))
            batch.record(index, label, path, before, _read(parent, name, item))
        indices = [args["index"]] if "index" in args else None
        _key(batch, index, owner, holder, path, prefix, indices, frame, interpolation, label, None)
        return
    for path in args["paths"]:
        prop = _rotation_prop(holder) if path == "rotation" else path
        _key(batch, index, owner, holder, prop, prefix, None, frame, interpolation, label, prop)


def op_delete_keys(args, batch, index):
    obj = batch.ref(args["ref"])
    owner, holder, prefix, label = _key_holder(obj, args)
    if "path" in args:
        _property(holder, args["path"], label)
        wanted = {(prefix + args["path"], args.get("index"))}

        def matches(curve):
            return any(curve.data_path == path and (element is None or curve.array_index == element)
                       for path, element in wanted)
    else:
        props = {prefix + prop for path in args["paths"] for prop in _PATH_PROPS[path]}

        def matches(curve):
            return curve.data_path in props

    tolerance = limits.value("abs_tolerance")
    first, last = args.get("frames", (-math.inf, math.inf))
    plan = []
    for curve in _curves(owner):
        if not matches(curve):
            continue
        doomed = [position for position, point in enumerate(curve.keyframe_points)
                  if first - tolerance <= point.co[0] <= last + tolerance]
        if doomed:
            plan.append((curve, doomed))
    if not plan:
        where = "" if "frames" not in args else " in frames %g-%g" % (first, last)
        raise PlanError("%s has no keys of %s%s" % (label, args.get("path") or ", ".join(args["paths"]), where))
    count = 0
    for curve, doomed in plan:
        state, path, element = _key_state(curve), curve.data_path, curve.array_index
        group = curve.group.name if curve.group else None

        def undo(path=path, element=element, state=state, group=group):
            curve = _curve(owner, path, element) or _new_curve(owner, path, element, group)
            _restore_keys(curve, state)

        batch.add(undo, lambda path=path, element=element, state=state: _curve(owner, path, element) is not None and _same(
            _key_state(_curve(owner, path, element)), state), "%s keys %s[%d]" % (label, path, element),
            ("keys", _pointer(owner), path, element))
        points = curve.keyframe_points
        for position in reversed(doomed):  # by position from the last: no reference outlives a removal
            points.remove(points[position], fast=True)
        count += len(doomed)
        curve.update()
        if not len(points):
            _remove_curve(owner, curve)
    batch.changes.append({"op": index, "ref": label, "field": "keys deleted", "curves": len(plan), "deleted": count})


def _new_curve(owner, path, element, group):
    from bpy_extras import anim_utils

    data = owner.animation_data
    bag = anim_utils.action_ensure_channelbag_for_slot(data.action, data.action_slot)
    curve = bag.fcurves.new(path, index=element)
    if group:
        curve.group = bag.groups.get(group) or bag.groups.new(group)
    return curve

def _remove_curve(obj, curve):
    try:
        from bpy_extras import anim_utils

        bag = anim_utils.action_get_channelbag_for_slot(obj.animation_data.action, obj.animation_data.action_slot)
        bag.fcurves.remove(curve)
    except (ImportError, AttributeError, RuntimeError):
        pass


def _removed(datablock):
    try:
        datablock.name
    except ReferenceError:
        return True
    return False


def op_create_object(args, batch, index):
    kind, name = args["kind"], args["name"]
    data = None
    if kind == "camera":
        data = bpy.data.cameras.new(name)
        if "lens" in args:
            data.lens = args["lens"]
    elif kind == "light":
        data = bpy.data.lights.new(name, args.get("light_type", "POINT"))
        if "energy" in args:
            data.energy = args["energy"]
    elif kind == "mesh":
        import bmesh

        data = bpy.data.meshes.new(name)
        mesh = bmesh.new()
        size = args.get("size", 1.0)
        primitive = args.get("primitive", "cube")
        # Blender's own primitive defaults (Add ▸ Mesh): 32 segments, 16 rings.
        if primitive == "cube":
            bmesh.ops.create_cube(mesh, size=size)
        elif primitive == "plane":
            bmesh.ops.create_grid(mesh, x_segments=1, y_segments=1, size=size / 2)
        elif primitive == "uv_sphere":
            bmesh.ops.create_uvsphere(mesh, u_segments=32, v_segments=16, radius=size / 2)
        else:
            top = size / 2 if primitive == "cylinder" else 0.0
            bmesh.ops.create_cone(mesh, cap_ends=True, segments=32, radius1=size / 2, radius2=top,
                                  depth=args.get("depth", size))
        mesh.to_mesh(data)
        mesh.free()
    elif kind == "text":
        data = bpy.data.curves.new(name, "FONT")
        data.body = args["body"]
        if data.body != args["body"]:
            held = len(data.body)
            bpy.data.curves.remove(data)
            raise PlanError("Blender holds %d of the body's %d characters" % (held, len(args["body"])))
        for key in ("size", "extrude", "align_x", "align_y"):
            if key in args:
                setattr(data, key, args[key])
    obj = bpy.data.objects.new(name, data)
    owner = {"camera": bpy.data.cameras, "light": bpy.data.lights, "mesh": bpy.data.meshes,
             "text": bpy.data.curves}.get(kind)

    def undo(obj=obj, data=data):
        if not _removed(obj):
            bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and not _removed(data) and data.users == 0:
            owner.remove(data)

    batch.journal.append((undo, lambda: _removed(obj) and (data is None or _removed(data)), "created %s" % name))
    batch.make(obj, data)
    (_collection(args["collection"]) if args.get("collection") else bpy.context.scene.collection).objects.link(obj)
    if "location" in args:
        obj.location = args["location"]
    if "rotation" in args:
        if "quaternion" in args["rotation"]:
            obj.rotation_mode = "QUATERNION"
        _set_rotation(obj, args["rotation"], Batch(), index, obj.name)
    if args.get("parent"):
        parent = batch.ref(args["parent"])
        bpy.context.view_layer.update()  # a parent created or moved earlier in this batch
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    if args.get("as"):
        batch.aliases[args["as"]] = obj
    if obj.name != name:
        batch.warnings.append("%r was taken: Blender named the new object %r" % (name, obj.name))
    batch.created.append({"op": index, "as": args.get("as"), "name": obj.name, "type": obj.type})


def op_rename(args, batch, index):
    obj = batch.ref(args["ref"])
    before = batch.remember(obj, "name")
    obj.name = args["name"]
    batch.record(index, before, "name", before, obj.name)
    if obj.name != args["name"]:
        batch.warnings.append("%r was taken: Blender named it %r" % (args["name"], obj.name))


def op_delete(args, batch, index):
    """Frees each object's name at once (and its data's when only it uses the data), so a later operation of the
    batch may give it to a new object; the object itself is removed once the whole batch succeeded. A collection
    is deleted empty (its objects deleted by this batch count as gone), its name freed the same way."""
    _delete_objects(args.get("refs", ()), batch, index)
    for name in args.get("collections", ()):
        group = _collection(name)
        if group == bpy.context.scene.collection:
            raise PlanError("the scene's own collection cannot be deleted")
        going = {id(obj) for _index, obj, _original, _data in batch.deletes}
        staying = [obj.name for obj in group.objects if id(obj) not in going]
        if staying or group.children:
            raise PlanError("collection %r holds %s: delete its objects in this batch (refs) or move them first" % (
                name, ", ".join(staying[:5] + [child.name + "/" for child in group.children][:5])))
        original = group.name
        group.name = "ocl-deleted-%x" % group.as_pointer()
        batch.add(lambda group=group, original=original: setattr(group, "name", original),
                  lambda group=group, original=original: group.name == original, "deleted collection %s" % original)
        batch.collection_deletes.append((index, group, original))


def _delete_objects(refs, batch, index):
    for name in refs:
        obj = batch.ref(name)
        original = obj.name
        data = obj.data if obj.data is not None and obj.data.users == 1 and obj.data.library is None else None
        data_name = data.name if data is not None else None
        aside = "ocl-deleted-%x" % obj.as_pointer()  # a temporary name, unique to this object
        obj.name = aside
        if data is not None:
            data.name = aside

        def undo(obj=obj, original=original, data=data, data_name=data_name):
            if data is not None:
                data.name = data_name
            obj.name = original

        batch.add(undo, lambda obj=obj, original=original, data=data, data_name=data_name: obj.name == original and (
            data is None or data.name == data_name), "deleted %s" % original)
        batch.deletes.append((index, obj, original, data))


def op_duplicate(args, batch, index):
    source = batch.ref(args["ref"])
    bpy.context.view_layer.update()
    originals = _hierarchy(source, args["children"])
    copies = {}  # an original's pointer: its copy
    for original in originals:
        copy = original.copy()
        data = None
        if original.data is not None and not args["linked"]:
            data = original.data.copy()
            copy.data = data

        def undo(copy=copy, data=data):
            if not _removed(copy):
                bpy.data.objects.remove(copy, do_unlink=True)
            if data is not None and not _removed(data) and data.users == 0:
                bpy.data.batch_remove([data])

        batch.journal.append((undo, lambda copy=copy, data=data: _removed(copy) and (data is None or _removed(data)),
                              "copy of %s" % original.name))
        for group in original.users_collection or (bpy.context.scene.collection,):
            group.objects.link(copy)
        copies[original.as_pointer()] = copy
        batch.make(copy, data)
    for original in originals:
        copy = copies[original.as_pointer()]
        if original is not source and original.parent is not None and original.parent.as_pointer() in copies:
            # Setting parent resets the parent inverse: the copy keeps the original's, its type and bone.
            inverse, kind, bone = copy.matrix_parent_inverse.copy(), copy.parent_type, copy.parent_bone
            copy.parent = copies[original.parent.as_pointer()]
            copy.parent_type, copy.parent_bone, copy.matrix_parent_inverse = kind, bone, inverse
        for constraint in copy.constraints:
            target = getattr(constraint, "target", None)
            if target is not None and target.as_pointer() in copies:
                constraint.target = copies[target.as_pointer()]
    for original in originals:
        copy = copies[original.as_pointer()]
        wanted = args["name"] if original is source and "name" in args else (
            original.name + args["suffix"] if "suffix" in args else None)
        if wanted is not None:
            copy.name = wanted
            if copy.name != wanted:
                raise PlanError("an object named %r exists: the copy of %r would be %r" % (wanted, original.name, copy.name))
    root = copies[source.as_pointer()]
    if "offset" in args:
        root.matrix_world = Matrix.Translation(Vector(args["offset"])) @ source.matrix_world
        batch.record(index, root.name, "location", list(source.location), root.location)
    if args.get("as"):
        batch.aliases[args["as"]] = root
    for original in originals:
        copy = copies[original.as_pointer()]
        batch.created.append(dict({"op": index, "name": copy.name, "type": copy.type, "copy_of": original.name,
                                   "data": "shared" if args["linked"] or copy.data is None else "copied"},
                                  **({"as": args.get("as")} if original is source else {})))


def _principled(material):
    """The material's Principled BSDF: the one feeding its active output's surface, else the first."""
    tree = material.node_tree
    if tree is None:
        return None
    nodes = [node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"]
    output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    if output is not None:
        for link in output.inputs["Surface"].links:
            if link.from_node in nodes:
                return link.from_node
    return nodes[0] if nodes else None


def op_set_material(args, batch, index):
    obj = batch.ref(args["ref"])
    data = obj.data
    if data is None or not hasattr(data, "materials"):
        raise PlanError("%r (%s) has no material slots" % (obj.name, obj.type))
    if data.library is not None:
        raise PlanError("%r's data is linked from a library" % obj.name)
    name = args["material"]
    material = bpy.data.materials.get(name)
    if material is not None and material.library is not None:
        raise PlanError("material %r is linked from a library" % name)
    if material is None:
        material = bpy.data.materials.new(name)
        batch.journal.append((lambda material=material: _removed(material) or bpy.data.materials.remove(material),
                              lambda material=material: _removed(material), "created material %s" % name))
        batch.make(material)
        batch.created.append({"op": index, "name": material.name, "type": "MATERIAL"})
    values = [(socket, args[key]) for key, socket in (("color", "Base Color"), ("roughness", "Roughness"),
                                                      ("metallic", "Metallic")) if key in args]
    if values:
        node = _principled(material)
        if node is None:
            raise PlanError("material %r has no Principled BSDF: assign it without color, roughness or metallic, or "
                            "edit its nodes" % material.name)
        for socket_name, value in values:
            socket = node.inputs[socket_name]
            wanted = list(value) + [1.0] if socket_name == "Base Color" else value
            before = batch.remember(socket, "default_value", owner=material)
            socket.default_value = wanted
            after = _copy(socket.default_value)
            if not _same(after, tuple(wanted) if isinstance(wanted, list) else wanted):
                raise PlanError("material %r: Blender holds %s for %s, not %s" % (material.name, _r(after), socket_name, value))
            batch.record(index, material.name, socket_name, before, after)
        if "color" in args:
            before = batch.remember(material, "diffuse_color")
            material.diffuse_color = list(args["color"]) + [1.0]
            batch.record(index, material.name, "viewport color", before, material.diffuse_color)
    _assign(obj, material, args.get("slot"), batch, index)


def _assign(obj, material, slot, batch, index):
    """Puts material in obj's slot (its only slot without slot; slot equal to the count adds one)."""
    data = obj.data
    count = len(obj.material_slots)
    if slot is None:
        if count > 1:
            raise PlanError("%r has %d material slots (%s): give slot" % (
                obj.name, count, ", ".join("%d %s" % (i, s.material.name if s.material else "empty")
                                           for i, s in enumerate(obj.material_slots))))
        slot = 0
    if slot > count:
        raise PlanError("%r has %d material slots: slot is 0 to %d (%d adds one)" % (obj.name, count, count, count))
    if slot == count:
        data.materials.append(material)
        batch.add(lambda: data.materials.pop(), lambda count=count: len(data.materials) == count,
                  "%s slot %d" % (obj.name, slot))
        before = None
    else:
        held = obj.material_slots[slot].material
        before = held.name if held else None
        batch.add(lambda: setattr(obj.material_slots[slot], "material", held),
                  lambda: obj.material_slots[slot].material == held, "%s slot %d" % (obj.name, slot),
                  ("slot", _pointer(obj), slot))
        obj.material_slots[slot].material = material
    batch.record(index, obj.name, "material slot %d" % slot, before, material.name)
    if obj.material_slots[slot].link == "DATA" and data.users > 1:
        batch.warnings.append("%s: its data %r is shared by %d objects, which all show %r in slot %d" % (
            obj.name, data.name, data.users, material.name, slot))


def _socket(sockets, name, label):
    """The socket named name (or with that identifier); among several, the first enabled one (a Mix node's A)."""
    found = [socket for socket in sockets if socket.name == name or socket.identifier == name]
    enabled = [socket for socket in found if socket.enabled]
    if not found:
        raise PlanError("%s has no socket %r (it has %s)" % (label, name, ", ".join(sorted({s.name for s in sockets if s.enabled}))))
    return (enabled or found)[0]


def _node_set(target, attr, value, batch, owner, label, journal=True):
    """Sets a node attribute or a socket's default value, journaled; a datablock attribute takes a name."""
    rna = target.bl_rna.properties.get(attr)
    if rna is None or rna.is_readonly:
        raise PlanError("%s has no settable %s" % (label, attr))
    if rna.type == "POINTER":
        collection = {"Image": "images", "ShaderNodeTree": "node_groups", "Object": "objects", "Text": "texts"}.get(
            rna.fixed_type.identifier)
        if collection is None or not isinstance(value, str):
            raise PlanError("%s.%s takes a %s, which this operation does not set" % (label, attr, rna.fixed_type.identifier))
        found = getattr(bpy.data, collection).get(value)
        if found is None:
            raise PlanError("%s.%s: no %s %r" % (label, attr, rna.fixed_type.identifier, value))
        value = found
    # A node this batch adds goes whole when it fails: its own fields need no journal.
    before = batch.remember(target, attr, owner=owner) if journal else _copy(getattr(target, attr))
    current = getattr(target, attr)
    if isinstance(value, list) and hasattr(current, "__len__") and not isinstance(current, str):
        if len(value) == 3 and len(current) == 4:
            value = value + [1.0]  # a color given as RGB
        if len(value) != len(current):
            raise PlanError("%s.%s takes %d numbers" % (label, attr, len(current)))
    try:
        setattr(target, attr, value)
    except (TypeError, ValueError) as error:
        raise PlanError("%s.%s: %s" % (label, attr, error)) from None
    after = _copy(getattr(target, attr))
    wanted = tuple(value) if isinstance(value, list) else value
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and not _same(after, float(wanted)):
        raise PlanError("%s.%s: Blender holds %s, not %s (out of its range)" % (label, attr, _r(after), value))
    if isinstance(wanted, tuple) and not _same(after, wanted):
        raise PlanError("%s.%s: Blender holds %s, not %s (out of its range)" % (label, attr, _r(after), list(value)))
    return before, after


def _ramp_state(ramp):
    return [(element.position, tuple(element.color)) for element in ramp.elements]


def _set_ramp(ramp, stops):
    elements = ramp.elements
    while len(elements) > len(stops):
        elements.remove(elements[-1])
    while len(elements) < len(stops):
        elements.new(1.0)
    # Positions in order: new() sorts by position, so each element takes its stop in turn.
    for element, (position, color) in zip(elements, sorted(stops, key=lambda stop: stop[0])):
        element.position = position
        element.color = color


def op_material_nodes(args, batch, index):
    name = args["material"]
    material = bpy.data.materials.get(name)
    if material is not None and material.library is not None:
        raise PlanError("material %r is linked from a library" % name)
    if material is None:
        material = bpy.data.materials.new(name)
        batch.journal.append((lambda material=material: _removed(material) or bpy.data.materials.remove(material),
                              lambda material=material: _removed(material), "created material %s" % name))
        batch.make(material)
        batch.created.append({"op": index, "name": material.name, "type": "MATERIAL"})
    if material.node_tree is None:
        material.use_nodes = True
    tree = material.node_tree
    made = _pointer(material) in batch.made
    for spec in args.get("nodes", ()):
        node = tree.nodes.get(spec["name"])
        label = "%s:%s" % (material.name, spec["name"])
        fresh = node is None
        if node is None:
            if "type" not in spec:
                raise PlanError("%s: no such node; a new node needs type (nodes now: %s)" % (
                    label, ", ".join(sorted(n.name for n in tree.nodes))))
            try:
                node = tree.nodes.new(spec["type"])
            except RuntimeError:
                raise PlanError("%s: %s is not a shader node type of this Blender" % (label, spec["type"])) from None
            node.name = spec["name"]
            if not made:
                batch.journal.append((lambda node=node: tree.nodes.remove(node) if tree.nodes.get(node.name) == node else None,
                                      lambda name=spec["name"]: tree.nodes.get(name) is None, "node %s" % label))
            batch.created.append({"op": index, "name": node.name, "type": node.bl_idname, "material": material.name})
        elif "type" in spec and node.bl_idname != spec["type"]:
            raise PlanError("%s is a %s, not a %s" % (label, node.bl_idname, spec["type"]))
        if "location" in spec:
            _node_set(node, "location", spec["location"], batch, material, label, not fresh)
        if "label" in spec:
            _node_set(node, "label", spec["label"], batch, material, label, not fresh)
        for attr, value in spec.get("props", {}).items():
            before, after = _node_set(node, attr, value, batch, material, label, not fresh)
            batch.record(index, label, attr, before, after)
        for socket_name, value in spec.get("inputs", {}).items():
            socket = _socket(node.inputs, socket_name, label)
            if not hasattr(socket, "default_value"):
                raise PlanError("%s: input %r has no value to set (link it)" % (label, socket_name))
            before, after = _node_set(socket, "default_value", value, batch, material, "%s.%s" % (label, socket_name), not fresh)
            batch.record(index, label, socket_name, before, after)
        if "ramp" in spec:
            ramp = getattr(node, "color_ramp", None)
            if ramp is None:
                raise PlanError("%s (%s) has no color ramp" % (label, node.bl_idname))
            state = _ramp_state(ramp)
            if not fresh:
                batch.add(lambda ramp=ramp, state=state: made and _removed(material) or _set_ramp(ramp, state),
                          lambda ramp=ramp, state=state: (made and _removed(material)) or _same(_ramp_state(ramp), state),
                          "%s ramp" % label, ("ramp", _pointer(material), node.name))
            _set_ramp(ramp, spec["ramp"])
            batch.record(index, label, "ramp", [list(stop) for stop in state], [list(stop) for stop in _ramp_state(ramp)])
    for link in args.get("links", ()):
        nodes = []
        for end, sockets in (("from", "outputs"), ("to", "inputs")):
            node = tree.nodes.get(link[end][0])
            if node is None:
                raise PlanError("%s: no node %r to link %s" % (material.name, link[end][0], end))
            nodes.append(_socket(getattr(node, sockets), link[end][1], "%s:%s" % (material.name, node.name)))
        output, socket = nodes
        old = [(l.from_node.name, l.from_socket.identifier) for l in socket.links]
        made_link = tree.links.new(output, socket)
        if not made_link.is_valid:
            raise PlanError("%s: %s.%s cannot feed %s.%s" % (material.name, link["from"][0], link["from"][1], link["to"][0],
                                                            link["to"][1]))

        def undo(socket=socket, output=output, old=old):
            if made and _removed(material):
                return
            for found in list(socket.links):
                tree.links.remove(found)
            for node_name, identifier in old:
                source = tree.nodes.get(node_name)
                if source is not None:
                    tree.links.new(next(s for s in source.outputs if s.identifier == identifier), socket)

        # A node this batch adds is removed with its links: nothing of it is left to prove.
        batch.add(undo, lambda socket=socket, old=old, to=link["to"][0]: (made and _removed(material)) or tree.nodes.get(to) is None or sorted(
            (l.from_node.name, l.from_socket.identifier) for l in socket.links) == sorted(old),
            "link into %s" % link["to"][0], ("link", _pointer(material), link["to"][0], socket.identifier))
        batch.record(index, material.name, "link %s.%s -> %s.%s" % (link["from"][0], link["from"][1], link["to"][0],
                                                                    link["to"][1]), old or None, "linked")
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if output is None or not output.inputs["Surface"].is_linked:
        batch.warnings.append("%s: nothing feeds its Material Output's Surface: it renders black or empty" % material.name)
    if args.get("ref"):
        obj = batch.ref(args["ref"])
        if obj.data is None or not hasattr(obj.data, "materials"):
            raise PlanError("%r (%s) has no material slots" % (obj.name, obj.type))
        _assign(obj, material, args.get("slot"), batch, index)


def op_scene_settings(args, batch, index):
    scene = bpy.context.scene
    start, end = args.get("frame_start", scene.frame_start), args.get("frame_end", scene.frame_end)
    if start > end:
        given = "frame_start" if "frame_start" in args else "frame_end"
        raise PlanError("%s would cross the other end of the scene's range (%d-%d): Blender would move it; give both "
                        "frame_start and frame_end" % (given, scene.frame_start, scene.frame_end))
    # Blender pushes the other end when one crosses it: set them in the order that never crosses.
    for key in (("frame_end", "frame_start") if start > scene.frame_end else ("frame_start", "frame_end")):
        if key in args:
            before = batch.remember(scene, key)
            setattr(scene, key, args[key])
            batch.record(index, scene.name, key, before, getattr(scene, key))
    if "fps" in args:
        rate = args["fps"]
        whole = abs(rate - round(rate)) <= limits.value("rel_tolerance") * rate
        fps = int(round(rate)) if whole else int(math.ceil(rate))
        render = scene.render
        before = render.fps / render.fps_base
        batch.remember(render, "fps")
        batch.remember(render, "fps_base")
        render.fps, render.fps_base = fps, 1.0 if whole else fps / rate
        effective = render.fps / render.fps_base
        if abs(effective - rate) > limits.value("rel_tolerance") * rate:
            raise PlanError("Blender holds %s frames per second, not %s (fps %d to %d)" % (
                _r(effective), _r(rate), render.bl_rna.properties["fps"].hard_min, render.bl_rna.properties["fps"].hard_max))
        batch.changes.append({"op": index, "ref": scene.name, "field": "fps", "before": _r(before), "after": _r(effective),
                              "fps": render.fps, "fps_base": _r(render.fps_base)})
    if "frame_current" in args:
        before = scene.frame_current
        batch.add(lambda: scene.frame_set(before), lambda: scene.frame_current == before, "%s.frame_current" % scene.name,
                  ("field", _pointer(scene), "frame_current"))
        scene.frame_set(args["frame_current"])
        batch.record(index, scene.name, "frame_current", before, scene.frame_current)


_PATH_PROPS = {"location": ("location",), "rotation": ("rotation_euler", "rotation_quaternion", "rotation_axis_angle"),
               "scale": ("scale",)}


def op_shift_keys(args, batch, index):
    obj = batch.ref(args["ref"])
    bone = _pose_bone(obj, args["bone"]) if args.get("bone") else None
    prefix = 'pose.bones["%s"].' % bone.name.replace("\\", "\\\\").replace('"', '\\"') if bone else ""
    label = "%s:%s" % (obj.name, bone.name) if bone else obj.name
    props = {prop for path in args.get("paths") or () for prop in _PATH_PROPS[path]} or None

    def wanted(curve):
        path = curve.data_path
        if bone is not None:
            if not path.startswith(prefix):
                return False
            path = path[len(prefix):]
        elif path.startswith("pose.bones["):
            return False
        return props is None or path in props

    tolerance = limits.value("abs_tolerance")
    first, last = args.get("frames", (-math.inf, math.inf))
    offset = args.get("offset", 0.0)
    plan = []
    for curve in (curve for curve in _curves(obj) if wanted(curve)):
        moving = [point for point in curve.keyframe_points if first - tolerance <= point.co[0] <= last + tolerance]
        if not moving:
            continue
        if not args["delete"]:
            staying = [point.co[0] for point in curve.keyframe_points if not first - tolerance <= point.co[0] <= last + tolerance]
            for point in moving:
                frame = point.co[0] + offset
                if abs(frame) > limits.value("max_frame"):
                    raise PlanError("%s: a key would move to frame %s, outside Blender's frames" % (label, _r(frame)))
                hit = next((other for other in staying if abs(other - frame) <= tolerance), None)
                if hit is not None:
                    raise PlanError("%s: the key of %s[%d] at frame %g would land on its key at frame %g, which does not "
                                    "move: widen frames or delete that key first" % (
                                        label, curve.data_path, curve.array_index, point.co[0], hit))
        plan.append((curve, moving))
    if not plan:
        where = "" if "frames" not in args else " in frames %g-%g" % (first, last)
        raise PlanError("%s has no keys%s%s" % (label, " of " + ", ".join(args["paths"]) if args.get("paths") else "", where))
    frames = sorted({point.co[0] for _, moving in plan for point in moving})
    count = sum(len(moving) for _, moving in plan)
    for curve, moving in plan:
        state = _key_state(curve)
        path, array_index = curve.data_path, curve.array_index

        def undo(path=path, array_index=array_index, state=state):
            _restore_keys(_curve(obj, path, array_index), state)

        batch.add(undo, lambda path=path, array_index=array_index, state=state: _same(
            _key_state(_curve(obj, path, array_index)), state), "%s keys %s[%d]" % (label, path, array_index),
            ("keys", _pointer(obj), path, array_index))
        if args["delete"]:
            for point in reversed(moving):  # from the last: earlier points stay where they are in the array
                curve.keyframe_points.remove(point, fast=True)
        else:
            for point in moving:
                point.co[0] += offset
                point.handle_left[0] += offset
                point.handle_right[0] += offset
        curve.update()
    change = {"op": index, "ref": label, "field": "keys", "curves": len(plan), "frames": _r([frames[0], frames[-1]])}
    change.update({"deleted": count} if args["delete"] else {"moved": count, "offset": _r(offset),
                                                             "to": _r([frames[0] + offset, frames[-1] + offset])})
    batch.changes.append(change)


def op_link(args, batch, index):
    obj, group = batch.ref(args["ref"]), _collection(args["collection"])
    if obj.name in group.objects:
        return
    group.objects.link(obj)
    batch.add(lambda: group.objects.unlink(obj), lambda: _removed(group) or obj.name not in group.objects,
              "%s in %s" % (obj.name, group.name), ("member", _pointer(obj), _pointer(group)))
    batch.record(index, obj.name, "collections", None, [found.name for found in obj.users_collection])


def op_unlink(args, batch, index):
    obj, group = batch.ref(args["ref"]), _collection(args["collection"])
    if obj.name not in group.objects:
        raise PlanError("%r is not in %r" % (obj.name, group.name))
    if len(obj.users_collection) == 1:
        raise PlanError("%r would be left in no collection: use delete" % obj.name)
    before = [found.name for found in obj.users_collection]
    group.objects.unlink(obj)
    batch.add(lambda: group.objects.link(obj), lambda: obj.name in group.objects, "%s in %s" % (obj.name, group.name),
              ("member", _pointer(obj), _pointer(group)))
    batch.record(index, obj.name, "collections", before, [found.name for found in obj.users_collection])


def op_create_collection(args, batch, index):
    parent = _collection(args["parent"]) if args.get("parent") else bpy.context.scene.collection
    group = bpy.data.collections.new(args["name"])
    parent.children.link(group)
    batch.journal.append((lambda: bpy.data.collections.remove(group), lambda: _removed(group), "created %s" % args["name"]))
    batch.make(group)
    batch.created.append({"op": index, "name": group.name, "type": "COLLECTION"})


def op_set_parent(args, batch, index):
    obj = batch.ref(args["ref"])
    # Objects created or moved earlier in this batch have stale world matrices until the view layer is evaluated.
    bpy.context.view_layer.update()
    for attr in ("parent", "parent_type", "parent_bone", "matrix_parent_inverse", "matrix_basis"):
        batch.remember(obj, attr)
    before = obj.parent.name if obj.parent else None
    world = obj.matrix_world.copy()
    if args.get("parent") is None:
        obj.parent = None
        obj.matrix_parent_inverse = Matrix.Identity(4)
        if args["keep_transform"]:
            obj.matrix_world = world
    else:
        parent = batch.ref(args["parent"])
        walker = parent
        while walker is not None:
            if walker == obj:
                raise PlanError("parenting %r to %r makes a cycle" % (obj.name, parent.name))
            walker = walker.parent
        if args.get("bone"):
            _pose_bone(parent, args["bone"])
        obj.parent = parent
        obj.parent_type = "BONE" if args.get("bone") else "OBJECT"
        obj.parent_bone = args.get("bone") or ""
        obj.matrix_parent_inverse = Matrix.Identity(4)
        if args["keep_transform"]:
            # As Blender's Parent (Keep Transform): the parent inverse absorbs the parent's matrix (object or bone),
            # the object's own location, rotation and scale stay as they were.
            bpy.context.view_layer.update()
            basis = obj.matrix_basis.copy()
            try:
                effective = obj.matrix_world @ basis.inverted()
                obj.matrix_parent_inverse = effective.inverted() @ world @ basis.inverted()
            except ValueError:  # a zero scale: no inverse; keep the world matrix through the basis instead
                obj.matrix_world = world
    bpy.context.view_layer.update()
    if args["keep_transform"] and not _same(obj.matrix_world.copy(), world):
        batch.warnings.append("%s: its world transform could not be kept exactly (a sheared or zero-scale parent)." % obj.name)
    batch.record(index, obj.name, "parent", before,
                 (obj.parent.name + ((":" + obj.parent_bone) if obj.parent_type == "BONE" else "")) if obj.parent else None)


def op_add_marker(args, batch, index):
    scene = bpy.context.scene
    count = len(scene.timeline_markers)
    marker = scene.timeline_markers.new(args["name"], frame=int(round(args["frame"])))
    if args.get("camera"):
        camera = batch.ref(args["camera"])
        if camera.type != "CAMERA":
            raise PlanError("%r is not a camera" % camera.name)
        marker.camera = camera
    batch.add(lambda: scene.timeline_markers.remove(marker), lambda: len(scene.timeline_markers) == count,
              "marker %s" % args["name"], ("markers", _pointer(scene)))
    batch.created.append({"op": index, "name": marker.name, "type": "MARKER", "frame": marker.frame})


def op_set_camera(args, batch, index):
    scene = bpy.context.scene
    camera = batch.ref(args["ref"])
    if camera.type != "CAMERA":
        raise PlanError("%r is not a camera" % camera.name)
    before = scene.camera
    batch.add(lambda: setattr(scene, "camera", before), lambda: scene.camera == before, "%s.camera" % scene.name,
              ("field", _pointer(scene), "camera"))
    scene.camera = camera
    batch.record(index, scene.name, "camera", before.name if before else None, camera.name)


def op_add_constraint(args, batch, index):
    obj = batch.ref(args["ref"])
    target = batch.ref(args["target"])
    if args.get("subtarget"):
        _pose_bone(target, args["subtarget"])
    count = len(obj.constraints)
    constraint = obj.constraints.new(args["type"])
    batch.add(lambda: obj.constraints.remove(constraint), lambda: len(obj.constraints) == count,
              "%s constraint %s" % (obj.name, args["type"]), ("constraints", _pointer(obj)))
    constraint.target = target
    if args.get("subtarget"):
        constraint.subtarget = args["subtarget"]
    if args["type"] == "TRACK_TO":
        # A camera or light looks down its -Z with +Y up: the convention of Blender's own Track To.
        constraint.track_axis, constraint.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    if args.get("name"):
        constraint.name = args["name"]
    if "influence" in args:
        constraint.influence = args["influence"]
    batch.record(index, obj.name, "constraints", None, [found.name for found in obj.constraints])


SWITCH_PREFIX = "OC parent "


def op_switch_parent(args, batch, index):
    """From frame on, obj copies the world transform of an anchor empty made under the new parent where obj is at
    that frame: no jump. Each switch adds its anchor and its Copy Transforms constraint; the influences are keyed
    CONSTANT, this switch's 1 from frame on (0 just before), every earlier switch's 0 from frame on."""
    obj = batch.ref(args["ref"])
    scene = bpy.context.scene
    frame = args["frame"]
    parent = batch.ref(args["parent"]) if args.get("parent") else None
    walker = parent
    while walker is not None:
        if walker == obj:
            raise PlanError("%r cannot follow %r, its own child" % (obj.name, parent.name))
        walker = walker.parent
    if args.get("bone"):
        _pose_bone(parent, args["bone"])
    current = scene.frame_current
    batch.add(lambda: scene.frame_set(current), lambda: scene.frame_current == current, "%s.frame_current" % scene.name,
              ("field", _pointer(scene), "frame_current"))
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    world = obj.matrix_world.copy()
    where = (parent.name + ((":" + args["bone"]) if args.get("bone") else "")) if parent else "world"
    anchor = bpy.data.objects.new("%s @ %s f%d" % (obj.name, where, frame), None)
    anchor.empty_display_size = 0.05
    anchor.hide_render = True
    batch.journal.append((lambda: _removed(anchor) or bpy.data.objects.remove(anchor, do_unlink=True),
                          lambda: _removed(anchor), "anchor of %s" % obj.name))
    batch.make(anchor)
    for group in obj.users_collection or (scene.collection,):
        group.objects.link(anchor)
    if parent is not None:
        anchor.parent = parent
        if args.get("bone"):
            anchor.parent_type, anchor.parent_bone = "BONE", args["bone"]
        bpy.context.view_layer.update()
    anchor.matrix_world = world
    bpy.context.view_layer.update()
    earlier = [c for c in obj.constraints if c.type == "COPY_TRANSFORMS" and c.name.startswith(SWITCH_PREFIX)]
    count = len(obj.constraints)
    constraint = obj.constraints.new("COPY_TRANSFORMS")
    batch.add(lambda: obj.constraints.remove(constraint), lambda: len(obj.constraints) == count,
              "%s constraint %s" % (obj.name, constraint.name), ("constraints", _pointer(obj)))
    constraint.name = "%sf%d %s" % (SWITCH_PREFIX, frame, where)
    constraint.target = anchor
    for target, before, at in [(constraint, 0.0, 1.0)] + [(c, None, 0.0) for c in earlier]:
        path = 'constraints["%s"].influence' % target.name.replace("\\", "\\\\").replace('"', '\\"')
        if target is not constraint and _curve(obj, path, 0) is None:
            batch.remember(target, "influence")  # keyed, its keys (journaled below) decide it at every frame
        if before is not None:
            target.influence = before
            _key(batch, index, obj, obj, path, "", None, frame - 1, "CONSTANT", obj.name, None)
        target.influence = at
        _key(batch, index, obj, obj, path, "", None, frame, "CONSTANT", obj.name, None)
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    error = (obj.matrix_world.translation - world.translation).length
    scene.frame_set(current)
    batch.changes.append({"op": index, "ref": obj.name, "field": "parent from frame %d" % frame, "after": where,
                          "anchor": anchor.name, "constraint": constraint.name, "jump": _r(error)})
    if error > max(1e-4, limits.value("matrix_tolerance") * world.translation.length):
        raise PlanError("%s: it would jump by %.4g at frame %d (a sheared or scaled parent)" % (obj.name, error, frame))
    if _keyed(obj, ("location", "rotation_euler", "rotation_quaternion", "scale")):
        batch.warnings.append("%s: its own keys no longer move it from frame %d on: it follows %s." % (obj.name, frame, where))


OPS = {
    "set_transform": op_set_transform, "fit_height": op_fit_height, "pose_bone": op_pose_bone,
    "aim_bone": op_aim_bone, "reach": op_reach, "insert_key": op_insert_key, "delete_keys": op_delete_keys,
    "create_object": op_create_object, "rename": op_rename, "switch_parent": op_switch_parent,
    "delete": op_delete, "link": op_link, "unlink": op_unlink, "create_collection": op_create_collection,
    "set_parent": op_set_parent, "add_marker": op_add_marker, "set_camera": op_set_camera,
    "add_constraint": op_add_constraint, "duplicate": op_duplicate, "set_material": op_set_material,
    "material_nodes": op_material_nodes,
    "scene_settings": op_scene_settings, "shift_keys": op_shift_keys,
}


def precheck(checked):
    """Every name the batch reads must exist (or be created earlier in it), every bone too; an aimed bone must
    be free to turn. Nothing has changed yet."""
    created_collections = set()
    renamed_to = {}  # a name given by a rename earlier in the batch: the object's name before the batch
    renamed_away = {}  # an old name: (its new name, the rename's index)
    taken = {}  # a rename onto a name another object holds: Blender suffixes it, so later uses are refused
    created = {}  # a name an earlier operation gives a new object (create_object, duplicate): (its index, alias)
    deleted = {}  # a name (or $alias) deleted earlier in the batch: the delete's index
    claimed = {}  # a name duplicate gives a copy: the duplicate's index (a second claim would be suffixed)

    def free(name):
        """No object holds name once the batch's earlier operations ran (a delete or rename frees it)."""
        return (bpy.data.objects.get(name) is None or name in deleted or (name in renamed_away and name not in renamed_to)) \
            and name not in claimed

    for index, (op, args) in enumerate(checked):
        where = "operations[%d] %s" % (index, op)

        def need(name, collection=False):
            if isinstance(name, str) and name in deleted and not collection:
                if name in created and created[name][0] > deleted[name]:
                    made, alias = created[name]
                    hint = ("refer to it as $%s" % alias) if alias else "give it an alias (\"as\") and refer to it by it"
                    raise PlanError("%s: %r was deleted by operations[%d] and is made again by operations[%d]: %s." % (
                        where, name, deleted[name], made, hint))
                raise PlanError("%s: %r is deleted by operations[%d]" % (where, name, deleted[name]))
            if name is None or (isinstance(name, str) and name.startswith("$")):
                return None
            if collection:
                try:
                    return None if name in created_collections else _collection(name)
                except PlanError as error:
                    raise PlanError("%s: %s" % (where, error)) from None
            if name in taken:
                raise PlanError("%s: operations[%d] renames an object to %r, which another object already has: Blender "
                                "names it %r.001. Choose a free name, or rename the other object first." % (
                                    where, taken[name], name, name))
            if name in renamed_to:
                return _obj(renamed_to[name])
            if name in renamed_away:
                raise PlanError("%s: %r was renamed to %r by operations[%d]: refer to it as %r." % (
                    where, name, renamed_away[name][0], renamed_away[name][1], renamed_away[name][0]))
            try:
                return _obj(name)
            except PlanError as error:
                if name in created:
                    made, alias = created[name]
                    hint = ("refer to it as $%s" % alias) if alias else (
                        "refer to it by name from the next apply" if alias is False else
                        "give that operation an alias (\"as\": \"x\") and refer to it as $x")
                    raise PlanError("%s: no object %r yet: operations[%d] creates it in this batch (Blender may suffix its "
                                    "name); %s." % (where, name, made, hint)) from None
                raise PlanError("%s: %s" % (where, error)) from None

        if op == "create_collection":
            need(args.get("parent"), True)
            if bpy.data.collections.get(args["name"]) is not None or args["name"] in created_collections:
                raise PlanError("%s: collection %r exists" % (where, args["name"]))
            created_collections.add(args["name"])
            continue
        for key in ("ref", "parent", "camera"):
            need(args.get(key))
        pole = args.get("pole")
        if isinstance(pole, dict) and "object" in pole:
            pole_holder = need(pole["object"])
            if pole.get("bone") and pole_holder is not None:
                try:
                    _pose_bone(pole_holder, pole["bone"])
                except PlanError as error:
                    raise PlanError("%s: %s" % (where, error)) from None
        target = args.get("target")
        holder = need(target["object"] if isinstance(target, dict) and "object" in target else target
                      if isinstance(target, str) else None)
        if isinstance(target, dict) and target.get("bone") and holder is not None:
            try:
                _pose_bone(holder, target["bone"])
            except PlanError as error:
                raise PlanError("%s: %s" % (where, error)) from None
        for name in args.get("refs", ()):
            need(name)
        for name in args.get("collections", ()):
            need(name, True)
        if args.get("collection"):
            need(args["collection"], True)
        if op == "add_constraint" and args.get("subtarget") and holder is not None:
            try:
                _pose_bone(holder, args["subtarget"])
            except PlanError as error:
                raise PlanError("%s: %s" % (where, error)) from None
        obj = need(args.get("ref"))
        if op == "create_object":
            created[args["name"]] = (index, args.get("as"))
        if op == "delete":
            for name in args.get("refs", ()):
                deleted[name] = index
        if op == "duplicate":
            names = [(args["name"], args.get("as"))] if "name" in args else []
            if "suffix" in args and obj is not None:
                names += [(member.name + args["suffix"], args.get("as") if member is obj else False)
                          for member in _hierarchy(obj, args["children"]) if member is not obj or "name" not in args]
            for name, alias in names:
                if not free(name):
                    raise PlanError("%s: an object named %r exists (or an earlier copy takes it): Blender would name the "
                                    "copy %r.001; choose another name or suffix" % (where, name, name))
                claimed[name] = index
                created[name] = (index, alias)
        if op in ("set_material", "material_nodes") and obj is not None:
            if obj.data is None or not hasattr(obj.data, "materials"):
                raise PlanError("%s: %r (%s) has no material slots" % (where, obj.name, obj.type))
            # No operation removes a slot: several now means several when it runs.
            if "slot" not in args and len(obj.material_slots) > 1:
                raise PlanError("%s: %r has %d material slots (%s): give slot" % (where, obj.name, len(obj.material_slots), ", ".join(
                    "%d %s" % (i, s.material.name if s.material else "empty") for i, s in enumerate(obj.material_slots))))
        if op == "rename" and isinstance(args.get("ref"), str):
            old, new = args["ref"], args["name"]
            source = renamed_to.pop(old, None) if old in renamed_to else (None if old.startswith("$") else old)
            holder = bpy.data.objects.get(new)
            if old.startswith("$"):
                created[new] = (index, old[1:])
            elif (holder is not None and new not in renamed_away and new not in deleted and new != source) \
                    or new in renamed_to or new in claimed:
                taken[new] = index
            elif source is not None:
                renamed_to[new] = source
                deleted.pop(new, None)  # the name now belongs to the renamed object
            if not old.startswith("$"):
                renamed_away[old] = (new, index)
            renamed_away.pop(new, None)
        if obj is not None and (op in ("pose_bone", "aim_bone") or (op in ("insert_key", "shift_keys", "delete_keys") and args.get("bone"))):
            try:
                bone = _pose_bone(obj, args["bone"])
            except PlanError as error:
                raise PlanError("%s: %s" % (where, error)) from None
            if op == "aim_bone":
                _free_to_turn(bone, where)
        if obj is not None and op == "reach":
            try:
                for name in args["chain"]:
                    _free_to_turn(_pose_bone(obj, name), where)
            except PlanError as error:
                raise PlanError(str(error) if str(error).startswith(where) else "%s: %s" % (where, error)) from None
        if op in ("set_parent", "switch_parent") and obj is not None and args.get("parent") and args.get("bone"):
            parent = need(args["parent"])
            if parent is not None:
                try:
                    _pose_bone(parent, args["bone"])
                except PlanError as error:
                    raise PlanError("%s: %s" % (where, error)) from None


def _free_to_turn(bone, where):
    active = [constraint.name for constraint in bone.constraints if not constraint.mute and constraint.influence > 0]
    if active:
        raise PlanError("%s: bone %r has active constraints (%s) that override its rotation" % (where, bone.name, ", ".join(active)))
    locked = list(bone.lock_rotation) + ([bone.lock_rotation_w] if bone.lock_rotations_4d and bone.rotation_mode in
                                          ("QUATERNION", "AXIS_ANGLE") else [])
    if any(locked):
        raise PlanError("%s: bone %r has locked rotation channels" % (where, bone.name))


def run(checked):
    """Runs a checked batch. Returns (failure, batch): failure None, or (message, failed index) with nothing
    undone yet (rollback). Deletions run last, once everything else succeeded."""
    batch = Batch()
    index = 0
    try:
        for index, (op, args) in enumerate(checked):
            try:
                OPS[op](args, batch, index)
            except PlanError:
                raise
            except Exception as error:  # noqa: BLE001 - a Blender refusal of one field
                raise PlanError("%s: %s" % (type(error).__name__, error)) from None
        for index, _obj, original, _data in batch.deletes:
            batch.changes.append({"op": index, "ref": original, "field": "deleted"})
        for index, obj, _original, data in batch.deletes:
            # Data shared with other objects stays, as with Blender's own delete; data only this object used went
            # aside with its name (so the name is free) and goes with it.
            bpy.data.objects.remove(obj, do_unlink=True)
            batch.deleted += 1
            if data is not None and data.users == 0:
                bpy.data.batch_remove([data])
        for index, group, original in batch.collection_deletes:
            batch.changes.append({"op": index, "ref": original, "field": "deleted collection"})
            bpy.data.collections.remove(group)
            batch.deleted += 1
        bpy.context.view_layer.update()
    except PlanError as error:
        where = checked[min(index, len(checked) - 1)][0]
        return ("operations[%d] %s: %s" % (index, where, error), index), batch
    return None, batch


def rollback(batch):
    """Runs the journal's inverses in reverse order, then re-reads each recorded field.
    Returns (restored, proofs): restored only when every inverse ran and every field is back."""
    ran = proved = 0
    failed, unproved = [], []
    for inverse, _proof, _label in reversed(batch.journal):
        try:
            inverse()
            ran += 1
        except Exception as reason:  # noqa: BLE001
            failed.append("%s: %s" % (type(reason).__name__, reason))
    try:
        bpy.context.view_layer.update()
    except Exception:  # noqa: BLE001
        pass
    for _inverse, proof, label in batch.journal:
        if proof is None:  # proved by the first record of the same field
            proved += 1
            continue
        try:
            ok = bool(proof())
        except Exception:  # noqa: BLE001
            ok = False
        proved += ok
        if not ok:
            unproved.append(label)
    restored = not failed and proved == len(batch.journal) and batch.deleted == 0
    proofs = {"inverses": len(batch.journal), "ran": ran, "proved": proved}
    if failed:
        proofs["errors"] = failed[:3]
    if unproved:
        proofs["unproved"] = unproved[:limits.value("change_entries")]
    if batch.deleted:
        proofs["deleted_before_the_failure"] = batch.deleted
    return restored, proofs
