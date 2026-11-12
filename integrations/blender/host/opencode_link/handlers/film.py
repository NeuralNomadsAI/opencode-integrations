# SPDX-License-Identifier: GPL-3.0-or-later

"""Reads and an operation for staging shots, from the needs of a real production.

- Evaluated bounds: world boxes of the evaluated geometry (armature deformation, modifiers, instances) of
  objects, hierarchies, collections or the whole scene.
- Rig information: bones in world space, the character's forward direction, actions.
- A capture from a viewpoint or a camera, drawn off-screen: the user's view is never changed and nothing is
  added to the document.
- A contact sheet: frames through their cameras (timeline markers by default) as one image, viewport-shaded
  or rendered with an engine, every setting restored.
- Aiming a bone toward a world direction, whatever its axes, rotation mode and the armature's scale, with
  the resulting rotation and how well it is aligned; a constrained or locked bone is refused beforehand.
"""

import math
import os
import shutil
import tempfile

import bpy
from mathutils import Matrix, Vector

from .. import limits, resources
from . import pixels
from .captures import refresh_display  # noqa: F401 - the adapter reaches it here too

GEOMETRY = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL", "GPENCIL", "ARMATURE"}
DIRECTIONS = {
    "front": Vector((0, 1, 0)),  # looking along +Y: Blender's front view
    "back": Vector((0, -1, 0)),
    "left": Vector((1, 0, 0)),
    "right": Vector((-1, 0, 0)),
    "top": Vector((0, 0, -1)),
    "perspective": Vector((1, 1, -0.6)),
}
SHADINGS = ("SOLID", "MATERIAL", "WIREFRAME")
ENGINES = {"workbench": ("BLENDER_WORKBENCH",), "eevee": ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"), "cycles": ("CYCLES",)}


def _round(values):
    return [round(float(value), 4) for value in values]


# -- targets and bounds ---------------------------------------------------------------------------
def _targets(objects=None, collection=None, children=True, scene=False):
    if scene:
        return None  # every visible instance of the view layer
    found = set()
    for name in objects or ():
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError("Object not found: %s" % name)
        found.add(obj)
        if children:
            found.update(obj.children_recursive)
    if collection:
        group = bpy.data.collections.get(collection)
        if group is None:
            raise ValueError("Collection not found: %s" % collection)
        found.update(group.all_objects)
    if not found:
        raise ValueError("Name objects, a collection, or set scene")
    return {obj.name for obj in found}


def _instances(names, visible_only=True):
    """(label, evaluated object, world matrix) of the evaluated geometry of `names` (None: the whole scene)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for instance in depsgraph.object_instances:
        source = instance.instance_object if instance.is_instance else instance.object
        owner = (instance.parent if instance.is_instance else instance.object).original
        if names is not None and owner.name not in names:
            continue
        if visible_only and not owner.visible_get():
            continue
        if source.type not in GEOMETRY:
            continue
        label = owner.name if not instance.is_instance else "%s/%s" % (owner.name, source.original.name)
        yield label, source, instance.matrix_world.copy()


def _box(points):
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _describe(low, high):
    return {"min": _round(low), "max": _round(high), "size": _round(high - low), "center": _round((low + high) / 2)}


def bounds(names, visible_only=True, exact=False):
    """Per-instance world boxes and their union, from the evaluated depsgraph."""
    entries, lows, highs = [], [], []
    for label, source, matrix in _instances(names, visible_only):
        points = []
        if exact and source.type == "MESH":
            mesh = source.to_mesh()
            try:
                points = [matrix @ vertex.co for vertex in mesh.vertices]
            finally:
                source.to_mesh_clear()
        if not points:
            points = [matrix @ Vector(corner) for corner in source.bound_box]
        low, high = _box(points)
        entries.append(dict(_describe(low, high), name=label, type=source.type))
        lows.append(low)
        highs.append(high)
    if not entries:
        return None, []
    union = _box(lows + highs)
    return union, entries


def get_evaluated_bounds(objects=None, collection=None, children=True, scene=False, visible_only=True, exact=False, limit=50):
    """World bounding boxes of the evaluated geometry: armature deformation, modifiers and instances included."""
    names = _targets(objects, collection, children, scene)
    union, entries = bounds(names, visible_only, exact)
    if union is None:
        raise ValueError("No visible geometry in that selection")
    entries.sort(key=lambda entry: -max(entry["size"]))
    limit = max(1, min(int(limit), limits.value("bounds_objects_max")))
    return {
        "union": _describe(*union),
        "floor_z": round(union[0].z, 4),
        "count": len(entries),
        "objects": entries[:limit],
        "truncated": len(entries) > limit,
        "method": "exact vertices of meshes" if exact else "evaluated bounding boxes (8 corners each, world space)",
    }


# -- rigs -----------------------------------------------------------------------------------------
def _armature(name):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "ARMATURE":
        raise ValueError("Armature object not found: %s" % name)
    return obj


def _forward(rig):
    """The character's forward direction on the ground: from foot to toe bones, else the armature's -Y."""
    world = rig.matrix_world
    vectors = []
    for bone in rig.pose.bones:
        if "toe" in bone.name.lower() and bone.parent is not None:
            vectors.append((world @ bone.head) - (world @ bone.parent.head))
    method = "foot to toe bones (%d)" % len(vectors)
    if not vectors:
        for bone in rig.pose.bones:
            if "foot" in bone.name.lower():
                vectors.append((world @ bone.tail) - (world @ bone.head))
        method = "foot bones (%d)" % len(vectors)
    flat = Vector((0.0, 0.0, 0.0))
    for vector in vectors:
        flat += Vector((vector.x, vector.y, 0.0))
    if flat.length < 1e-9:
        flat = (world.to_3x3() @ Vector((0.0, -1.0, 0.0)))
        flat = Vector((flat.x, flat.y, 0.0))
        method = "armature -Y axis (no foot or toe bones; Blender's front)"
    flat.normalize()
    return {"forward": _round(flat), "yaw_deg": round(math.degrees(math.atan2(flat.y, flat.x)), 2), "method": method}


def get_rig_info(armature, bones=None, limit=200):
    """Bones of an armature in world space (current pose and frame), its forward direction and actions."""
    rig = _armature(armature)
    world = rig.matrix_world
    wanted = list(bones) if bones else [bone.name for bone in rig.pose.bones]
    entries = []
    for name in wanted[: max(1, min(int(limit), limits.value("rig_bones_max")))]:
        bone = rig.pose.bones.get(name)
        if bone is None:
            raise ValueError("Bone not found: %s" % name)
        entries.append({
            "name": bone.name,
            "parent": bone.parent.name if bone.parent else None,
            "head": _round(world @ bone.head),
            "tail": _round(world @ bone.tail),
            "rotation_mode": bone.rotation_mode,
            "constrained": any(constraint.enabled for constraint in bone.constraints),
        })
    data = rig.animation_data
    return {
        "armature": rig.name,
        "scale": _round(world.to_scale()),
        "frame": bpy.context.scene.frame_current,
        "facing": _forward(rig),
        "bone_count": len(rig.pose.bones),
        "bones": entries,
        "truncated": len(wanted) > len(entries),
        "action": data.action.name if data and data.action else None,
        "actions": [{"name": action.name, "frame_range": _round(action.frame_range)} for action in bpy.data.actions][:50],
    }


# -- off-screen captures ----------------------------------------------------------------------------
def _view_3d():
    if bpy.app.background:
        raise RuntimeError("A viewport capture needs the Blender interface (in the background, scene.contact_sheet "
                           "renders with an engine).")
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return area.spaces.active, region
    raise RuntimeError("No 3D Viewport is open: a viewport capture draws with one (it is not changed).")


def _look_at(eye, target):
    forward = (target - eye).normalized()
    up = Vector((0, 0, 1)) if abs(forward.z) < 0.999 else Vector((0, 1, 0))
    side = forward.cross(up).normalized()
    up = side.cross(forward)
    return Matrix(((side.x, side.y, side.z, -side.dot(eye)), (up.x, up.y, up.z, -up.dot(eye)),
                   (-forward.x, -forward.y, -forward.z, forward.dot(eye)), (0, 0, 0, 1)))


def _perspective(fov, aspect, near, far):
    t = math.tan(fov / 2)
    return Matrix(((1 / (aspect * t), 0, 0, 0), (0, 1 / t, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))


def _offscreen(view, projection, width, height, shading="SOLID", scene_world=True):
    """RGBA8 rows bottom-up, drawn with a 3D Viewport's settings; its shading and overlays are restored."""
    import gpu

    space, region = _view_3d()
    saved = (space.shading.type, space.shading.use_scene_world, space.shading.use_scene_lights, space.overlay.show_overlays)
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        space.shading.type = shading
        if shading == "MATERIAL":
            space.shading.use_scene_world = scene_world
            space.shading.use_scene_lights = scene_world
        space.overlay.show_overlays = False
        offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, view, projection,
                              do_color_management=True)
        buffer = offscreen.texture_color.read()
        try:
            return bytes(memoryview(buffer))
        except TypeError:
            buffer.dimensions = width * height * 4
            return bytes(int(value) for value in buffer)
    finally:
        offscreen.free()
        space.shading.type, space.shading.use_scene_world, space.shading.use_scene_lights, space.overlay.show_overlays = saved


def _camera(name):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "CAMERA":
        raise ValueError("Camera object not found: %s" % name)
    return obj


def _size(width, height):
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("width and height must be at least 1 pixel")
    return width, height


def capture_view(objects=None, collection=None, scene=False, camera=None, direction="front", width=800, height=450,
                 margin=1.2, shading="SOLID"):
    """An image of objects, a collection or the scene from a direction, or through a camera, drawn off-screen:
    the user's view, selection and active camera are not changed and nothing is added to the document."""
    width, height = _size(width, height)
    if shading not in SHADINGS:
        raise ValueError("shading must be one of %s" % ", ".join(SHADINGS))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    if camera:
        cam = _camera(camera)
        view = cam.matrix_world.inverted()
        projection = cam.calc_matrix_camera(depsgraph, x=width, y=height)
        viewpoint = {"camera": cam.name}
    else:
        if direction not in DIRECTIONS:
            raise ValueError("direction must be one of %s" % ", ".join(DIRECTIONS))
        union, _ = bounds(_targets(objects, collection, True, scene))
        if union is None:
            raise ValueError("No visible geometry in that selection")
        center = (union[0] + union[1]) / 2
        radius = max((union[1] - union[0]).length / 2, 0.01)
        fov = math.radians(40)
        distance = radius / math.sin(fov / 2) * max(float(margin), 1.0)
        eye = center - DIRECTIONS[direction].normalized() * distance
        view = _look_at(eye, center)
        projection = _perspective(fov if width >= height else fov, width / height, max(distance - radius * 2, distance / 1000),
                                  distance + radius * 3)
        viewpoint = {"eye": _round(eye), "target": _round(center), "direction": direction}
    # RGBA8 read back, PNG rows and compressed copy in RAM; colour and depth targets on the GPU.
    measured = resources.check("This capture (%d x %d)" % (width, height), ram_bytes=width * height * 4 * 3,
                               vram_bytes=width * height * 8)
    rgba = _offscreen(view, projection, width, height, shading)
    return {"width": width, "height": height, "viewpoint": viewpoint, "resources": measured,
            "image": pixels.as_image(pixels.encode(width, height, rgba))}


# -- contact sheet --------------------------------------------------------------------------------------
def _shots(frames, camera):
    scene = bpy.context.scene
    if frames:
        return [(int(frame), camera) for frame in frames]
    marked = sorted((marker.frame, marker.camera.name) for marker in scene.timeline_markers if marker.camera is not None)
    if marked and not camera:
        return marked
    return [(scene.frame_current, camera)]


def _set_engine(render, engine):
    """The engine's identifier differs between Blender versions (EEVEE); the render engine list is dynamic."""
    for name in ENGINES[engine]:
        try:
            render.engine = name
            return
        except TypeError:
            continue
    raise ValueError("The %s render engine is not available in this Blender" % engine)


def _engine_tile(cam, width, height, engine, samples):
    """One engine render through `cam` at width x height; every render setting is restored."""
    scene = bpy.context.scene
    render, settings = scene.render, scene.render.image_settings
    saved = {"camera": scene.camera, "engine": render.engine, "x": render.resolution_x, "y": render.resolution_y,
             "percent": render.resolution_percentage, "format": settings.file_format, "mode": settings.color_mode,
             "depth": settings.color_depth}
    cycles_samples = getattr(getattr(scene, "cycles", None), "samples", None)
    eevee_samples = getattr(getattr(scene, "eevee", None), "taa_render_samples", None)
    folder = tempfile.mkdtemp(prefix="opencode-link-")
    try:
        scene.camera = cam
        _set_engine(render, engine)
        render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
        if engine == "cycles" and cycles_samples is not None:
            scene.cycles.samples = int(samples)
        if engine == "eevee" and eevee_samples is not None:
            scene.eevee.taa_render_samples = int(samples)
        settings.file_format, settings.color_mode, settings.color_depth = "PNG", "RGBA", "8"
        bpy.ops.render.render(write_still=False)
        path = os.path.join(folder, "tile.png")
        bpy.data.images["Render Result"].save_render(path, scene=scene)
        with open(path, "rb") as handle:
            return pixels.decode(handle.read())
    finally:
        scene.camera = saved["camera"]
        render.engine = saved["engine"]
        render.resolution_x, render.resolution_y, render.resolution_percentage = saved["x"], saved["y"], saved["percent"]
        settings.file_format, settings.color_mode, settings.color_depth = saved["format"], saved["mode"], saved["depth"]
        if cycles_samples is not None:
            scene.cycles.samples = cycles_samples
        if eevee_samples is not None:
            scene.eevee.taa_render_samples = eevee_samples
        shutil.rmtree(folder, ignore_errors=True)


def contact_sheet(frames=None, camera=None, width=480, columns=3, shading="MATERIAL", scene_world=True, engine=None, samples=16):
    """Frames through their cameras as one image: by default each timeline marker's frame through its bound
    camera, viewport-shaded (material preview with the scene world), or an engine render (workbench, eevee,
    cycles) at low samples. The current frame, active camera, render settings and the user's view are restored."""
    scene = bpy.context.scene
    if camera:
        _camera(camera)
    shots = _shots(frames, camera)
    if not shots:
        raise ValueError("A contact sheet needs at least one frame")
    if engine is not None and engine not in ENGINES:
        raise ValueError("engine must be one of %s" % ", ".join(ENGINES))
    if shading not in SHADINGS:
        raise ValueError("shading must be one of %s" % ", ".join(SHADINGS))
    if engine is None and bpy.app.background:
        engine = "workbench"  # no viewport to draw with in the background
    width = int(width)
    height = max(16, round(width * scene.render.resolution_y / max(scene.render.resolution_x, 1)))
    width, height = _size(width, height)
    sheet = (len(shots) * (width + 4) + 4) * (height + 8)  # pixels of the sheet, one row at worst
    measured = resources.check("This contact sheet (%d frames of %d x %d)" % (len(shots), width, height),
                               ram_bytes=len(shots) * width * height * 4 + sheet * 4 * 3,
                               vram_bytes=0 if engine else width * height * 8)
    original = scene.frame_current
    tiles, legend = [], []
    try:
        for index, (frame, name) in enumerate(shots):
            scene.frame_set(frame)
            cam = _camera(name) if name else scene.camera
            if cam is None:
                raise ValueError("Frame %d has no camera: name one, or bind cameras to timeline markers" % frame)
            if engine:
                tiles.append(_engine_tile(cam, width, height, engine, samples))
            else:
                depsgraph = bpy.context.evaluated_depsgraph_get()
                rgba = _offscreen(cam.matrix_world.inverted(), cam.calc_matrix_camera(depsgraph, x=width, y=height),
                                  width, height, shading, scene_world)
                tiles.append((width, height, pixels.flip(width, height, rgba)))
            legend.append({"index": index, "frame": frame, "camera": cam.name})
    finally:
        scene.frame_set(original)
        # Evaluate the restored settings now, inside the read: a deferred update would count as a change.
        bpy.context.view_layer.update()
    columns = max(1, min(int(columns), len(tiles)))
    total_w, total_h, canvas = pixels.tile(tiles, columns)
    return {"width": total_w, "height": total_h, "columns": columns, "tiles": legend, "resources": measured,
            "mode": ("engine %s" % engine) if engine else ("viewport %s" % shading.lower()),
            "image": pixels.as_image(pixels.encode(total_w, total_h, canvas, bottom_up=False))}


# -- aiming bones ------------------------------------------------------------------------------------
AXES = {"Y": Vector((0, 1, 0)), "-Y": Vector((0, -1, 0)), "X": Vector((1, 0, 0)), "-X": Vector((-1, 0, 0)),
        "Z": Vector((0, 0, 1)), "-Z": Vector((0, 0, -1))}


def _aim_target(armature, bone, direction, axis):
    rig = _armature(armature)
    pose = rig.pose.bones.get(bone)
    if pose is None:
        raise ValueError("Bone not found: %s" % bone)
    if axis not in AXES:
        raise ValueError("axis must be one of %s" % ", ".join(AXES))
    target = Vector(direction)
    if len(target) != 3 or target.length < 1e-9:
        raise ValueError("direction must be a non-zero [x, y, z] world vector")
    constraints = [constraint.name for constraint in pose.constraints if constraint.enabled and constraint.influence > 0]
    if constraints:
        raise ValueError("Bone %s is constrained (%s): its rotation is overridden; aim it through the constraint target" % (bone, ", ".join(constraints)))
    if any(pose.lock_rotation) or (pose.rotation_mode in ("QUATERNION", "AXIS_ANGLE") and pose.lock_rotation_w):
        raise ValueError("Bone %s has locked rotation channels: unlock them first" % bone)
    return rig, pose, target.normalized()


def preflight_aim_bone(armature, bone, direction, axis="Y", frame=None):
    """Refusals before anything changes (the apply stays untouched)."""
    del frame
    _aim_target(armature, bone, direction, axis)


def _world_axis(rig, pose, axis):
    matrix = rig.matrix_world @ pose.matrix
    return (matrix.to_3x3() @ AXES[axis]).normalized()


def aim_bone(armature, bone, direction, axis="Y", frame=None):
    """Rotate a pose bone so its axis (Y: head to tail) points along a world direction, optionally keyed at
    a frame. Works from the evaluated world matrices, so bone axes, rotation mode and armature scale do not
    matter; returns the rotation written and how well it is aligned."""
    rig, pose, target = _aim_target(armature, bone, direction, axis)
    scene = bpy.context.scene
    original = scene.frame_current
    if frame is not None:
        scene.frame_set(int(frame))
    try:
        rotation_world = rig.matrix_world.to_quaternion()
        for _ in range(limits.value("aim_iterations")):
            current = _world_axis(rig, pose, axis)
            if current.dot(target) > 0.999999:
                break
            # The world rotation taking the axis onto the target, expressed in armature space.
            delta = rotation_world.inverted() @ current.rotation_difference(target) @ rotation_world
            head = pose.head.copy()
            pose.matrix = Matrix.Translation(head) @ delta.to_matrix().to_4x4() @ Matrix.Translation(-head) @ pose.matrix
            bpy.context.view_layer.update()
        aligned = _world_axis(rig, pose, axis).dot(target)
        path = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(pose.rotation_mode, "rotation_euler")
        if frame is not None:
            pose.keyframe_insert(data_path=path, frame=int(frame))
        return {
            "armature": rig.name,
            "bone": pose.name,
            "axis": axis,
            "direction": _round(target),
            "rotation_mode": pose.rotation_mode,
            "written": path,
            "rotation": _round(getattr(pose, path)),
            "quaternion": _round(pose.matrix_basis.to_quaternion()),
            "alignment": round(aligned, 6),
            "error_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, aligned)))), 4),
            "keyframe": int(frame) if frame is not None else None,
        }
    finally:
        if frame is not None:
            scene.frame_set(original)
