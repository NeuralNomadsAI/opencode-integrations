# SPDX-License-Identifier: GPL-3.0-or-later

"""Rigged characters and their animation, from the needs of a real production.

- Duplicating a rigged hierarchy: the armature and its data copied, Armature modifiers, constraints and
  parents rewired to the copies, actions copied, shared or detached.
- Scaling an import to a height and putting it on the ground, from its evaluated bounds.
- Attaching an object to a bone without inheriting the armature's scale (Child Of without scale, its
  inverse set so that offsets are in metres), verified on the evaluated result.
- Deleting or moving existing keys of channels over a frame range, in Blender 5's layered actions.

Every refusal is a preflight (nothing has changed yet): `preflight_<operation>`.
"""

import math

import bpy
from mathutils import Euler, Matrix, Vector

from . import film

ACTIONS = ("copy", "link", "none")
KEY_EDITS = ("delete", "move")
ATTACH_NAME = "OpenCode Attach"
_POINTERS = ("object", "target", "mirror_object", "offset_object", "start_cap", "end_cap", "curve", "auxiliary_target",
             "pole_target", "origin")


def _object(name, label="Object"):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError("%s not found: %s" % (label, name))
    return obj


def _round(values, digits=4):
    return [round(float(value), digits) for value in values]


# -- duplicating a rigged hierarchy ---------------------------------------------------------------
def preflight_duplicate_hierarchy(name, actions="copy", offset=None, collection=None, suffix=None):
    del offset, suffix
    _object(name)
    if actions not in ACTIONS:
        raise ValueError("actions must be copy, link or none")
    if collection and bpy.data.collections.get(collection) is None and collection != bpy.context.scene.collection.name:
        raise ValueError("Collection not found: %s" % collection)


def _slot_identifier(data):
    slot = getattr(data, "action_slot", None)
    return slot.identifier if slot is not None else None


def _assign_action(data, action, identifier):
    data.action = action
    if action is not None and identifier and hasattr(action, "slots"):
        slot = next((slot for slot in action.slots if slot.identifier == identifier), None)
        if slot is not None:
            data.action_slot = slot


def _rewire(owner, mapping):
    """Points an object's modifiers and constraints (its bones' too) at the copies; returns how many."""
    count = 0
    holders = list(owner.modifiers) + list(owner.constraints)
    if owner.type == "ARMATURE" and owner.pose is not None:
        holders += [constraint for bone in owner.pose.bones for constraint in bone.constraints]
    for holder in holders:
        for attribute in _POINTERS:
            value = getattr(holder, attribute, None)
            if isinstance(value, bpy.types.Object) and value in mapping:
                setattr(holder, attribute, mapping[value])
                count += 1
        for target in getattr(holder, "targets", ()):  # the Armature constraint
            if getattr(target, "target", None) in mapping:
                target.target = mapping[target.target]
                count += 1
    return count


def duplicate_hierarchy(name, actions="copy", offset=None, collection=None, suffix=None):
    """Copy an object and all its children: object data copied (armatures included), parents, Armature
    modifiers and constraints pointing inside the hierarchy rewired to the copies, actions copied (each
    copy animates on its own), linked (shared) or detached; moved by `offset` in metres."""
    root = _object(name)
    originals = [root] + list(root.children_recursive)
    mapping = {}
    for source in originals:
        copy = source.copy()
        if source.data is not None:
            copy.data = source.data.copy()
        if suffix:
            copy.name = source.name + suffix
        mapping[source] = copy
    target_collection = None
    if collection:
        target_collection = bpy.data.collections.get(collection) or bpy.context.scene.collection
    rewired, copied_actions = 0, {}
    for source, copy in mapping.items():
        for group in ([target_collection] if target_collection else (source.users_collection or [bpy.context.scene.collection])):
            group.objects.link(copy)
        if source.parent in mapping:
            copy.parent = mapping[source.parent]
            copy.matrix_parent_inverse = source.matrix_parent_inverse.copy()
        rewired += _rewire(copy, mapping)
        for data in (copy.animation_data, getattr(getattr(copy.data, "shape_keys", None), "animation_data", None)):
            if data is None or data.action is None:
                continue
            identifier = _slot_identifier(data)
            if actions == "none":
                data.action = None
            elif actions == "copy":
                original = data.action
                if original.name not in copied_actions:
                    copied_actions[original.name] = original.copy()
                _assign_action(data, copied_actions[original.name], identifier)
    top = mapping[root]
    if offset:
        top.matrix_world = Matrix.Translation(Vector(offset)) @ root.matrix_world
    bpy.context.view_layer.update()
    return {
        "root": top.name,
        "objects": [{"source": source.name, "copy": copy.name, "type": copy.type} for source, copy in mapping.items()],
        "rewired": rewired,
        "actions": {"mode": actions, "copied": {name: action.name for name, action in copied_actions.items()}},
    }


# -- scale to a height, put on the ground --------------------------------------------------------
def preflight_place_on_ground(name, height=None, ground=True, children=True):
    del ground
    _object(name)
    if height is not None and not 0 < float(height) < 1e6:
        raise ValueError("height must be a positive number of metres")
    union, _ = film.bounds(film._targets([name], None, children, False))
    if union is None:
        raise ValueError("%s has no visible geometry to measure" % name)
    if height is not None and union[1].z - union[0].z < 1e-9:
        raise ValueError("%s has no height to scale from (flat)" % name)


def place_on_ground(name, height=None, ground=True, children=True):
    """Scale an object (with its children) about its origin so its evaluated height is `height` metres,
    then move it so its lowest point is at z = 0. Measured on the evaluated geometry (armature deformation,
    modifiers), so a rigged import is measured as it shows."""
    obj = _object(name)
    names = film._targets([name], None, children, False)
    before, _ = film.bounds(names)
    factor = 1.0
    if height is not None:
        factor = float(height) / (before[1].z - before[0].z)
        obj.scale = obj.scale * factor
        bpy.context.view_layer.update()
    moved = 0.0
    if ground:
        low = film.bounds(names)[0][0]
        moved = -low.z
        obj.matrix_world = Matrix.Translation(Vector((0.0, 0.0, moved))) @ obj.matrix_world
        bpy.context.view_layer.update()
    after, _ = film.bounds(names)
    animated = sorted({curve.data_path for curve in _fcurves(obj) if curve.data_path in ("location", "scale")})
    output = {
        "name": obj.name,
        "factor": round(factor, 6),
        "moved_z": round(moved, 6),
        "before": film._describe(*before),
        "after": film._describe(*after),
        "floor_z": round(after[0].z, 6),
    }
    if animated:
        output["warning"] = ("%s is animated on %s: the keyed values win at the next frame change; key the new "
                             "values or scale a parent instead." % (obj.name, ", ".join(animated)))
    return output


# -- attach to a bone ------------------------------------------------------------------------------
def preflight_attach_to_bone(name, armature, bone, offset=None, rotation=None, at="head", inherit_scale=False,
                             keep_world=False):
    del offset, rotation, inherit_scale, keep_world
    obj = _object(name)
    rig = film._armature(armature)
    if rig.pose.bones.get(bone) is None:
        raise ValueError("Bone not found: %s" % bone)
    if obj == rig:
        raise ValueError("An armature cannot be attached to its own bone")
    if obj.parent is not None:
        raise ValueError("%s has a parent (%s): the attachment replaces parenting; unparent it first "
                         "(object.parent with parent null, keep_world)" % (obj.name, obj.parent.name))
    if at not in ("head", "tail"):
        raise ValueError("at must be head or tail")


def _bone_matrix(rig, pose, inherit_scale):
    """The bone's world matrix as Child Of uses it, without the armature's scale unless inherited."""
    full = rig.matrix_world @ pose.matrix
    if inherit_scale:
        return full
    location, rotation, _ = full.decompose()
    return Matrix.Translation(location) @ rotation.to_matrix().to_4x4()


def attach_to_bone(name, armature, bone, offset=None, rotation=None, at="head", inherit_scale=False, keep_world=False):
    """Make an object follow a bone with a Child Of constraint ("OpenCode Attach"), without the armature's
    scale by default: `offset` (metres) and `rotation` (radians, XYZ) are in the bone's axes, from its head
    or tail; the object keeps its own size. With keep_world, it stays where it is and follows from there."""
    obj = _object(name)
    rig = film._armature(armature)
    pose = rig.pose.bones[bone]
    bpy.context.view_layer.update()
    parent = _bone_matrix(rig, pose, inherit_scale)
    if keep_world:
        wanted = obj.matrix_world.copy()
    else:
        start = Vector((0.0, 0.0, 0.0))
        if at == "tail":
            start = Vector((0.0, ((rig.matrix_world @ pose.tail) - (rig.matrix_world @ pose.head)).length, 0.0))
            if inherit_scale:
                start = Vector((0.0, pose.length, 0.0))
        local = Matrix.Translation(start + Vector(offset or (0.0, 0.0, 0.0))) @ Euler(rotation or (0.0, 0.0, 0.0), "XYZ").to_matrix().to_4x4()
        wanted = parent @ local @ Matrix.Diagonal(obj.matrix_world.to_scale()).to_4x4()
    constraint = obj.constraints.get(ATTACH_NAME)
    if constraint is None or constraint.type != "CHILD_OF":
        constraint = obj.constraints.new("CHILD_OF")
        constraint.name = ATTACH_NAME
    constraint.target, constraint.subtarget = rig, pose.name
    for axis in "xyz":
        setattr(constraint, "use_scale_" + axis, bool(inherit_scale))
    constraint.inverse_matrix = Matrix.Identity(4)
    obj.matrix_basis = parent.inverted() @ wanted
    bpy.context.view_layer.update()
    # Measured, not assumed: world = A @ basis for the constraint's own composition; correct once from it.
    measured = obj.matrix_world @ obj.matrix_basis.inverted()
    obj.matrix_basis = measured.inverted() @ wanted
    bpy.context.view_layer.update()
    error = (obj.matrix_world.translation - wanted.translation).length
    turned = obj.matrix_world.to_quaternion().rotation_difference(wanted.to_quaternion()).angle
    return {
        "name": obj.name,
        "constraint": constraint.name,
        "armature": rig.name,
        "bone": pose.name,
        "inherit_scale": bool(inherit_scale),
        "world_location": _round(obj.matrix_world.translation),
        "world_scale": _round(obj.matrix_world.to_scale()),
        "error_m": round(error, 6),
        "error_deg": round(math.degrees(min(turned, 2 * math.pi - turned)), 4),
    }


# -- editing existing keys --------------------------------------------------------------------------
def _fcurves(owner):
    """F-curves of an object's action, through its slot in Blender 5's layered actions."""
    data = getattr(owner, "animation_data", None)
    action = getattr(data, "action", None)
    if action is None:
        return []
    slot = getattr(data, "action_slot", None)
    if slot is not None:
        try:
            from bpy_extras import anim_utils

            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
        except (ImportError, AttributeError):
            bag = None
        return list(bag.fcurves) if bag is not None else []
    return list(getattr(action, "fcurves", ()))


def _bag(owner):
    data = owner.animation_data
    from bpy_extras import anim_utils

    return anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)


def _matched(name, data_path=None, index=None, bone=None):
    obj = _object(name)
    curves = _fcurves(obj)
    if not curves:
        raise ValueError("%s has no keyed channels (no action or an empty slot)" % obj.name)
    prefix = 'pose.bones["%s"].' % bone if bone else ""
    if bone and (obj.type != "ARMATURE" or obj.pose.bones.get(bone) is None):
        raise ValueError("Bone not found on %s: %s" % (obj.name, bone))
    wanted = None
    if data_path:
        wanted = data_path if data_path.startswith("pose.bones[") or not bone else prefix + data_path
    found = [curve for curve in curves
             if (wanted is None or curve.data_path == wanted) and (not prefix or curve.data_path.startswith(prefix))
             and (index is None or curve.array_index == int(index))]
    if not found:
        raise ValueError("No keyed channel matches on %s%s%s; keyed: %s" % (
            obj.name, " " + (wanted or prefix.rstrip(".")) if (wanted or prefix) else "",
            "[%d]" % index if index is not None else "", ", ".join(sorted({c.data_path for c in curves}))[:400]))
    return obj, found


def _in_range(point, start, end):
    return start - 1e-4 <= point.co.x <= end + 1e-4


def preflight_edit_keyframes(name, operation, frame_start, frame_end, offset=0.0, data_path=None, index=None, bone=None,
                             remove_empty=True):
    del remove_empty
    if operation not in KEY_EDITS:
        raise ValueError("operation must be delete or move")
    if float(frame_end) < float(frame_start):
        raise ValueError("frame_end must be at least frame_start")
    _, curves = _matched(name, data_path, index, bone)
    if operation == "move":
        if not offset:
            raise ValueError("move needs a non-zero offset (frames)")
        for curve in curves:
            moving = [point.co.x for point in curve.keyframe_points if _in_range(point, frame_start, frame_end)]
            staying = {round(point.co.x, 3) for point in curve.keyframe_points if not _in_range(point, frame_start, frame_end)}
            clash = sorted(round(x + offset, 3) for x in moving if round(x + offset, 3) in staying)
            if clash:
                raise ValueError("Moving %s[%d] by %g would put two keys on frame(s) %s: delete those first" % (
                    curve.data_path, curve.array_index, offset, ", ".join("%g" % x for x in clash[:10])))


def edit_keyframes(name, operation, frame_start, frame_end, offset=0.0, data_path=None, index=None, bone=None,
                   remove_empty=True):
    """Delete, or move by `offset` frames, the existing keys between frame_start and frame_end of an object's
    keyed channels (all, one data_path, one index, or those of one bone), in its action's slot."""
    obj, curves = _matched(name, data_path, index, bone)
    changed, removed_channels = [], []
    for curve in curves:
        points = curve.keyframe_points
        inside = [i for i, point in enumerate(points) if _in_range(point, frame_start, frame_end)]
        if operation == "delete":
            for i in reversed(inside):
                points.remove(points[i])
        else:
            for i in inside:
                point = points[i]
                point.co.x += offset
                point.handle_left.x += offset
                point.handle_right.x += offset
        curve.update()
        entry = {"data_path": curve.data_path, "index": curve.array_index, "keys": len(inside), "left": len(curve.keyframe_points)}
        if operation == "delete" and remove_empty and len(curve.keyframe_points) == 0:
            removed_channels.append(entry)
            try:
                _bag(obj).fcurves.remove(curve)
            except (AttributeError, ImportError, RuntimeError):
                obj.animation_data.action.fcurves.remove(curve)
        changed.append(entry)
    bpy.context.view_layer.update()
    return {
        "name": obj.name,
        "operation": operation,
        "range": [frame_start, frame_end],
        "offset": offset if operation == "move" else None,
        "channels": changed[:100],
        "keys": sum(entry["keys"] for entry in changed),
        "removed_channels": len(removed_channels),
    }
