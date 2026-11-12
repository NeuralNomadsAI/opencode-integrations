# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Scene reads and operations of OpenCode Link."""

import bmesh
import bpy
import mathutils

from .captures import capture_area, find_area
from .node_utils import modifier_input_get


# ---------------------------------------------------------------------------
# Scene info
# ---------------------------------------------------------------------------
def get_scene_info():
    """Get full scene information (no object limit)."""
    scene = bpy.context.scene
    objects = []
    for obj in scene.objects:
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [round(obj.location.x, 4), round(obj.location.y, 4), round(obj.location.z, 4)],
            "parent": obj.parent.name if obj.parent else None,
            "visible": obj.visible_get(),
        }
        if obj.type == "MESH" and obj.data:
            obj_info["vertices"] = len(obj.data.vertices)
            obj_info["polygons"] = len(obj.data.polygons)
        if obj.modifiers:
            obj_info["modifiers"] = [modifier.name for modifier in obj.modifiers]
        objects.append(obj_info)

    return {
        "name": scene.name,
        "object_count": len(scene.objects),
        "objects": objects,
        "materials_count": len(bpy.data.materials),
        "frame_current": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "render_engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "camera": scene.camera.name if scene.camera else None,
    }


def get_object_modifiers(name):
    """Get detailed modifier info for an object."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object not found: {name}")

    modifiers = []
    for modifier in obj.modifiers:
        modifier_info = {"name": modifier.name, "type": modifier.type}
        if modifier.type == "NODES" and modifier.node_group:
            modifier_info["node_group"] = modifier.node_group.name
            inputs = {}
            for item in modifier.node_group.interface.items_tree:
                if item.item_type == "SOCKET" and item.in_out == "INPUT":
                    identifier = item.identifier
                    try:
                        inputs[item.name] = modifier_input_get(modifier, identifier)
                    except (AttributeError, KeyError, TypeError):
                        inputs[item.name] = None
            modifier_info["inputs"] = inputs
        modifiers.append(modifier_info)

    return modifiers


# ---------------------------------------------------------------------------
# Object creation / manipulation
# ---------------------------------------------------------------------------
PRIMITIVES = ("CUBE", "SPHERE", "CYLINDER", "CONE", "PLANE", "CIRCLE", "GRID")


def _primitive_mesh(primitive, name, size=2.0):
    """A new mesh datablock built with bmesh: no operator, so no undo step of its own."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        radius = size / 2.0
        if primitive == "CUBE":
            bmesh.ops.create_cube(bm, size=size)
        elif primitive == "SPHERE":
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
        elif primitive == "CYLINDER":
            bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=radius, radius2=radius, depth=size)
        elif primitive == "CONE":
            bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=radius, radius2=0.0, depth=size)
        elif primitive == "PLANE":
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=radius)
        elif primitive == "CIRCLE":
            bmesh.ops.create_circle(bm, segments=32, radius=radius)
        elif primitive == "GRID":
            bmesh.ops.create_grid(bm, x_segments=10, y_segments=10, size=radius)
        else:
            raise ValueError(f"Unknown primitive: {primitive}")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    return mesh


def new_primitive_object(primitive, name=None, location=None, rotation=None, size=2.0):
    """Create a primitive, camera, light or empty through bpy.data and link it to the scene."""
    primitive = primitive.upper()
    label = name or primitive.capitalize()
    if primitive == "EMPTY":
        data = None
    elif primitive == "CAMERA":
        data = bpy.data.cameras.new(label)
    elif primitive == "LIGHT":
        data = bpy.data.lights.new(label, "POINT")
    elif primitive in PRIMITIVES:
        data = _primitive_mesh(primitive, label, size)
    else:
        raise ValueError(f"Unknown primitive: {primitive}")
    obj = bpy.data.objects.new(label, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = tuple(location) if location else (0.0, 0.0, 0.0)
    if rotation:
        obj.rotation_euler = tuple(rotation)
    view_layer = bpy.context.view_layer
    if view_layer is not None and obj.name in view_layer.objects:
        view_layer.objects.active = obj
    return obj


def create_object(primitive="CUBE", name=None, location=None):
    """Create a primitive, camera, light or empty."""
    obj = new_primitive_object(primitive, name=name, location=location)
    return {"name": obj.name, "type": obj.type}


def delete_object(name):
    """Delete an object by name."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object not found: {name}")

    obj_type = obj.type
    bpy.data.objects.remove(obj, do_unlink=True)

    return {"name": name, "type": obj_type, "deleted": True}


def transform_object(name, location=None, rotation=None, scale=None):
    """Set transform on an object."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object not found: {name}")

    written = None
    if location is not None:
        obj.location = tuple(location)
    if rotation is not None:
        # The rotation goes where the object's rotation mode reads it: rotation_euler alone would be
        # silently ignored by an object in quaternion or axis-angle mode (imported models often are).
        euler = mathutils.Euler(tuple(rotation), "XYZ")
        if obj.rotation_mode == "QUATERNION":
            obj.rotation_quaternion = euler.to_quaternion()
            written = "rotation_quaternion (converted from Euler XYZ)"
        elif obj.rotation_mode == "AXIS_ANGLE":
            axis, angle = euler.to_quaternion().to_axis_angle()
            obj.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
            written = "rotation_axis_angle (converted from Euler XYZ)"
        else:
            obj.rotation_euler = euler.to_matrix().to_euler(obj.rotation_mode)
            written = "rotation_euler (%s)" % obj.rotation_mode

    if scale is not None:
        obj.scale = tuple(scale)

    output = {
        "name": obj.name,
        "location": list(obj.location),
        "rotation": list(obj.matrix_basis.to_euler("XYZ")),
        "rotation_mode": obj.rotation_mode,
        "scale": list(obj.scale),
    }
    if written:
        output["rotation_written"] = written
    return output


def set_active_camera(name):
    """Set the active camera for the scene."""
    obj = bpy.data.objects.get(name)
    if not obj or obj.type != "CAMERA":
        raise ValueError(f"Camera not found: {name}")
    bpy.context.scene.camera = obj
    return {"camera": obj.name}


# ---------------------------------------------------------------------------
# Captures (images come back as attachments)
# ---------------------------------------------------------------------------
def get_node_editor_screenshot(tree_name=None, max_size=1200, fit_all=True):
    """Capture the node editor, optionally showing a given node tree."""
    area = find_area("NODE_EDITOR")
    space = area.spaces.active
    if tree_name:
        node_group = bpy.data.node_groups.get(tree_name)
        if not node_group:
            raise ValueError(f"Node tree not found: {tree_name}")
        space.node_tree = node_group
    result = capture_area(area, max_size, fit_all=fit_all)
    result["tree_name"] = space.node_tree.name if space.node_tree else None
    return result
