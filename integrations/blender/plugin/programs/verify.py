# SPDX-License-Identifier: GPL-3.0-or-later
# blender_verify (fixed program): renders low-resolution frames of a scene and returns the PNG with facts read
# from the pixels and the evaluated scene. By default the scene's final output (the sequencer when it is used,
# the compositor when active) through the camera that output uses; with view, from a free viewpoint (a
# temporary camera at view.location looking at view.target, lens in millimetres), the markers' cameras and the
# sequencer set aside for it. Several frames come back as one contact sheet, left to right and top to bottom.
# Render settings, cameras, markers and the current frame are restored afterwards; the temporary camera goes.
# PARAMS is prepended by the plugin: {"scene"?, "frame"?, "frames"?, "view"?, "size"?}
import base64
import math
import os
import tempfile

import bpy
from mathutils import Vector

P = PARAMS  # noqa: F821
MAX_FRAMES = 16
SHEET_SIDE = 1280  # longest side of a contact sheet: the reply stays a few hundred kilobytes
scene = bpy.data.scenes.get(P["scene"]) if P.get("scene") else bpy.context.scene
if scene is None:
    raise ValueError("no scene %r; scenes: %s" % (P.get("scene"), [s.name for s in bpy.data.scenes]))
if "frame" in P and "frames" in P:
    raise ValueError("give frame or frames, not both")
frames = [int(f) for f in P.get("frames") or [P.get("frame", scene.frame_current)]]
if not 1 <= len(frames) <= MAX_FRAMES:
    raise ValueError("frames: 1 to %d frames" % MAX_FRAMES)
size = int(P.get("size", 320))
if not 64 <= size <= 640:
    raise ValueError("size: 64 to 640 pixels")
cols = math.ceil(math.sqrt(len(frames)))
rows = math.ceil(len(frames) / cols)
tile = min(size, SHEET_SIDE // cols)
r = scene.render
im = r.image_settings


def vec3(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(isinstance(x, (int, float)) for x in value):
        raise ValueError("%s: expected [x, y, z]" % label)
    return Vector(value)


def view_target(value):
    if isinstance(value, str):
        value = {"object": value}
    if isinstance(value, dict):
        target = bpy.data.objects.get(value.get("object", ""))
        if target is None:
            raise ValueError("view.target: no object %r" % value.get("object"))
        if value.get("bone"):
            bone = target.pose.bones.get(value["bone"]) if target.pose else None
            if bone is None:
                raise ValueError("view.target: %r has no bone %r" % (target.name, value["bone"]))
            return target.matrix_world @ bone.head
        return target.matrix_world.translation.copy()
    return vec3(value, "view.target")


view = P.get("view")
if view is not None:
    if not isinstance(view, dict) or not {"location", "target"} <= set(view) or set(view) - {"location", "target", "lens"}:
        raise ValueError("view: {location: [x, y, z], target: [x, y, z] or an object name or {object, bone}, lens?}")
    eye = vec3(view["location"], "view.location")
    look = view_target(view["target"])
    if (look - eye).length < 1e-6:
        raise ValueError("view: location and target are the same point")
    lens = float(view.get("lens", 50.0))
    if not 1 <= lens <= 5000:
        raise ValueError("view.lens: 1 to 5000 mm")


def strips_at(sc, f):
    se = sc.sequence_editor
    if se is None:
        return []
    items = getattr(se, "strips", None) or getattr(se, "sequences", [])
    return [s for s in items if s.frame_final_start <= f < s.frame_final_end and not s.mute]


def camera_at(sc, f):
    bound = [m for m in sc.timeline_markers if m.camera is not None and m.frame <= f]
    if bound:
        m = max(bound, key=lambda m: m.frame)
        return m.camera.name, "marker %r" % m.name
    return (sc.camera.name if sc.camera else None), "scene camera"


def output_facts(f):
    """What renders at frame f, read before anything is set aside."""
    facts = {"frame": f}
    if view is not None:
        facts["camera_used"] = {"camera": None, "via": "view: from %s toward %s, %g mm" % (
            [round(x, 3) for x in eye], [round(x, 3) for x in look], lens)}
        return facts
    active = strips_at(scene, f)
    uses_sequencer = bool(r.use_sequencer and active)
    facts["sequencer"] = {"used": uses_sequencer, "strips_at_frame": [
        {"name": s.name, "type": s.type, "channel": s.channel, "blend": getattr(s, "blend_type", None),
         **({"scene": s.scene.name if s.scene else None, "scene_camera": s.scene_camera.name if s.scene_camera else None}
            if s.type == "SCENE" else {})}
        for s in sorted(active, key=lambda s: -s.channel)]}
    if uses_sequencer:
        top = max(active, key=lambda s: s.channel)
        if top.type == "SCENE" and top.scene is not None:
            cam = top.scene_camera.name if top.scene_camera else camera_at(top.scene, f - top.frame_start + top.scene.frame_start)[0]
            facts["camera_used"] = {"camera": cam, "via": "top strip %r (scene %r)" % (top.name, top.scene.name)}
            if top.scene == scene:
                facts.setdefault("warnings", []).append("the top strip shows its own scene")
        else:
            facts["camera_used"] = {"camera": None, "via": "top strip %r (%s)" % (top.name, top.type)}
    else:
        cam, via = camera_at(scene, f)
        facts["camera_used"] = {"camera": cam, "via": via}
        if cam is None:
            facts.setdefault("warnings", []).append("no camera: nothing can render")
    if r.use_compositing and getattr(scene, "compositing_node_group", None) is not None and uses_sequencer:
        facts.setdefault("warnings", []).append("compositing and sequencer are both active: compare the image with what you expect")
    return facts


group = getattr(scene, "compositing_node_group", None)
summary = {"scene": scene.name, "engine": r.engine, "film_transparent": r.film_transparent,
           "world": scene.world.name if scene.world else None,
           "compositing": {"use_compositing": r.use_compositing, "node_group": group.name if group else None}}
per_frame = [output_facts(f) for f in frames]

old_frame = scene.frame_current
saved = {"pct": r.resolution_percentage, "path": r.filepath, "fmt": im.file_format, "mode": im.color_mode,
         "media": getattr(im, "media_type", None), "camera": scene.camera, "sequencer": r.use_sequencer}
markers = [(m, m.camera) for m in scene.timeline_markers]
cycles = getattr(scene, "cycles", None)
eevee = getattr(scene, "eevee", None)
saved_samples = (getattr(cycles, "samples", None), getattr(eevee, "taa_render_samples", None))
path = os.path.join(tempfile.gettempdir(), "opencode_verify_%d.png" % os.getpid())
sheet_path = os.path.join(tempfile.gettempdir(), "opencode_verify_sheet_%d.png" % os.getpid())
camera = None
tiles = []
image_b64 = None
try:
    if view is not None:
        data = bpy.data.cameras.new("OpenCode verify view")
        data.lens = lens
        data.clip_end = max(data.clip_end, (look - eye).length * 4)
        camera = bpy.data.objects.new("OpenCode verify view", data)
        scene.collection.objects.link(camera)
        camera.location = eye
        camera.rotation_euler = (look - eye).to_track_quat("-Z", "Y").to_euler()
        for marker, _bound in markers:
            marker.camera = None
        scene.camera = camera
        r.use_sequencer = False
    r.resolution_percentage = max(1, min(100, int(100 * tile / max(r.resolution_x, r.resolution_y))))
    if saved["media"] is not None:
        im.media_type = "IMAGE"
    im.file_format = "PNG"
    im.color_mode = "RGBA"
    r.filepath = path
    if cycles is not None and saved_samples[0]:
        cycles.samples = min(saved_samples[0], 16)
    if eevee is not None and saved_samples[1]:
        eevee.taa_render_samples = min(saved_samples[1], 8)
    for facts in per_frame:
        scene.frame_set(facts["frame"])
        dg = bpy.context.evaluated_depsgraph_get()
        counts = {}
        for inst in dg.object_instances:
            if inst.is_instance and inst.parent is not None:
                key = inst.parent.original.name
                counts[key] = counts.get(key, 0) + 1
        facts["instances_by_object"] = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:20])
        facts["visible_objects"] = sum(1 for o in scene.objects if o.visible_get() and o != camera)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        img = bpy.data.images.load(path)
        try:
            w, h = img.size
            px = img.pixels[:]
        finally:
            bpy.data.images.remove(img)
        n = w * h
        lum = [0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2] for i in range(0, len(px), 4)]
        alpha = px[3::4]
        mean = sum(lum) / n
        std = (sum((x - mean) ** 2 for x in lum) / n) ** 0.5
        facts["image"] = {
            "size": [w, h], "mean_luminance": round(mean, 4), "max_luminance": round(max(lum), 4), "luminance_std": round(std, 4),
            "mean_rgb": [round(sum(px[c::4]) / n, 4) for c in range(3)],
            "mean_alpha": round(sum(alpha) / n, 4), "transparent_fraction": round(sum(1 for a in alpha if a < 0.01) / n, 4),
        }
        facts["image"]["black"] = max(lum) < 0.02
        facts["image"]["transparent"] = facts["image"]["mean_alpha"] < 0.01
        facts["image"]["uniform"] = std < 0.002
        if len(frames) == 1:
            with open(path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode("ascii")
        else:
            tiles.append((w, h, px))
    if tiles:
        # One sheet: tiles left to right, top to bottom (Blender's pixel rows run bottom-up), white gaps.
        w, h = max(t[0] for t in tiles), max(t[1] for t in tiles)
        sheet_w, sheet_h = cols * w, rows * h
        sheet = [1.0] * (sheet_w * sheet_h * 4)
        for index, (tw, th, px) in enumerate(tiles):
            col, row = index % cols, rows - 1 - index // cols
            for y in range(th):
                start = ((row * h + y) * sheet_w + col * w) * 4
                sheet[start:start + tw * 4] = px[y * tw * 4:(y + 1) * tw * 4]
        out = bpy.data.images.new("OpenCode verify sheet", sheet_w, sheet_h, alpha=True)
        try:
            out.pixels.foreach_set(sheet)
            out.filepath_raw = sheet_path
            out.file_format = "PNG"
            out.save()
        finally:
            bpy.data.images.remove(out)
        with open(sheet_path, "rb") as fh:
            image_b64 = base64.b64encode(fh.read()).decode("ascii")
        summary["sheet"] = {"size": [sheet_w, sheet_h], "columns": cols, "rows": rows,
                            "order": "left to right, top to bottom: frames %s" % frames}
finally:
    r.resolution_percentage = saved["pct"]
    r.filepath = saved["path"]
    if saved["media"] is not None:
        im.media_type = saved["media"]
    im.file_format = saved["fmt"]
    im.color_mode = saved["mode"]
    if cycles is not None and saved_samples[0]:
        cycles.samples = saved_samples[0]
    if eevee is not None and saved_samples[1]:
        eevee.taa_render_samples = saved_samples[1]
    if camera is not None:
        for marker, bound in markers:
            marker.camera = bound
        scene.camera = saved["camera"]
        r.use_sequencer = saved["sequencer"]
        data = camera.data
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(data)
    scene.frame_set(old_frame)
    for leftover in (path, sheet_path):
        if os.path.exists(leftover):
            os.remove(leftover)

if len(per_frame) > 1:
    summary["frames"] = per_frame
result = {"blender": bpy.app.version_string, "facts": dict(summary, **per_frame[0]) if len(per_frame) == 1 else summary,
          "image_base64": image_b64}
