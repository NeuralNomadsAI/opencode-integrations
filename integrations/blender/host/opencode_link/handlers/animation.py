# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

"""Animation helpers for Blender 5.x layered actions."""

import bpy


def _get_object(name):
    obj = bpy.data.objects.get(name)
    if not obj:
        raise ValueError(f"Object not found: {name}")
    return obj


def _iter_action_fcurves(action):
    if not action:
        return []

    if hasattr(action, "fcurves"):
        return list(action.fcurves)

    fcurves = []
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                if hasattr(strip, "channelbags"):
                    for channelbag in strip.channelbags:
                        if hasattr(channelbag, "fcurves"):
                            fcurves.extend(channelbag.fcurves)
    return fcurves


def _apply_interpolation(obj, interpolation):
    action = obj.animation_data.action if obj.animation_data else None
    for fcurve in _iter_action_fcurves(action):
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = interpolation


def set_timeline_settings(frame_start=None, frame_end=None, frame_current=None, fps=None):
    """Set timeline range and playback FPS."""
    scene = bpy.context.scene
    if frame_start is not None:
        scene.frame_start = int(frame_start)
    if frame_end is not None:
        scene.frame_end = int(frame_end)
    if frame_current is not None:
        scene.frame_set(int(frame_current))
    if fps is not None:
        scene.render.fps = int(fps)

    return {
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "fps": scene.render.fps,
    }


def keyframe_object_transform(name, frame, location=None, rotation=None, scale=None, interpolation=None):
    """Set transform values and insert keyframes on an object."""
    obj = _get_object(name)
    frame = int(frame)

    if location is not None:
        obj.location = tuple(location)
        obj.keyframe_insert(data_path="location", frame=frame)
    if rotation is not None:
        obj.rotation_euler = tuple(rotation)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    if scale is not None:
        obj.scale = tuple(scale)
        obj.keyframe_insert(data_path="scale", frame=frame)

    if interpolation:
        _apply_interpolation(obj, interpolation)

    return get_object_animation_info(name)


def create_turntable_animation(
    object_name,
    frame_start=1,
    frame_end=72,
    turns=1.0,
    axis="Z",
    interpolation="LINEAR",
):
    """Create a simple turntable rotation animation on an object."""
    obj = _get_object(object_name)
    axis = axis.upper()
    if axis not in {"X", "Y", "Z"}:
        raise ValueError("axis must be X, Y, or Z")

    start_rotation = list(obj.rotation_euler)
    end_rotation = list(obj.rotation_euler)
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
    end_rotation[axis_index] += 6.283185307179586 * float(turns)

    bpy.context.scene.frame_set(int(frame_start))
    obj.rotation_euler = tuple(start_rotation)
    obj.keyframe_insert(data_path="rotation_euler", frame=int(frame_start))

    bpy.context.scene.frame_set(int(frame_end))
    obj.rotation_euler = tuple(end_rotation)
    obj.keyframe_insert(data_path="rotation_euler", frame=int(frame_end))

    if interpolation:
        _apply_interpolation(obj, interpolation)

    return get_object_animation_info(object_name)


def get_object_animation_info(name):
    """Return summary information about an object's animation channels."""
    obj = _get_object(name)
    action = obj.animation_data.action if obj.animation_data else None
    fcurves = _iter_action_fcurves(action)

    return {
        "object_name": obj.name,
        "has_animation_data": bool(obj.animation_data),
        "action_name": action.name if action else None,
        "is_layered_action": bool(getattr(action, "is_action_layered", False)) if action else False,
        "fcurve_count": len(fcurves),
        "channels": [
            {
                "data_path": fcurve.data_path,
                "array_index": fcurve.array_index,
                "keyframe_count": len(fcurve.keyframe_points),
            }
            for fcurve in fcurves
        ],
    }
