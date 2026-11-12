# SPDX-License-Identifier: GPL-3.0-or-later
# blender_verify (fixed program): renders one low-resolution frame of a scene (its final output: the
# sequencer when it is used, the compositor when active) and returns the PNG with facts read from the
# pixels and the evaluated scene. Render settings and the current frame are restored afterwards.
# PARAMS is prepended by the plugin: {"scene"?, "frame"?, "size"?}
import base64
import os
import tempfile

import bpy

P = PARAMS  # noqa: F821
scene = bpy.data.scenes.get(P["scene"]) if P.get("scene") else bpy.context.scene
if scene is None:
    raise ValueError("no scene %r; scenes: %s" % (P.get("scene"), [s.name for s in bpy.data.scenes]))
frame = int(P.get("frame", scene.frame_current))
size = int(P.get("size", 320))
r = scene.render
im = r.image_settings


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


facts = {"scene": scene.name, "frame": frame, "engine": r.engine}
active = strips_at(scene, frame)
uses_sequencer = bool(r.use_sequencer and active)
group = getattr(scene, "compositing_node_group", None)
facts["sequencer"] = {"used": uses_sequencer, "strips_at_frame": [
    {"name": s.name, "type": s.type, "channel": s.channel, "blend": getattr(s, "blend_type", None),
     **({"scene": s.scene.name if s.scene else None, "scene_camera": s.scene_camera.name if s.scene_camera else None} if s.type == "SCENE" else {})}
    for s in sorted(active, key=lambda s: -s.channel)]}
facts["compositing"] = {"use_compositing": r.use_compositing, "node_group": group.name if group else None}
if uses_sequencer:
    top = max(active, key=lambda s: s.channel)
    if top.type == "SCENE" and top.scene is not None:
        cam = top.scene_camera.name if top.scene_camera else camera_at(top.scene, frame - top.frame_start + top.scene.frame_start)[0]
        facts["camera_used"] = {"camera": cam, "via": "top strip %r (scene %r)" % (top.name, top.scene.name)}
        if top.scene == scene:
            facts.setdefault("warnings", []).append("the top strip shows its own scene")
    else:
        facts["camera_used"] = {"camera": None, "via": "top strip %r (%s)" % (top.name, top.type)}
else:
    cam, via = camera_at(scene, frame)
    facts["camera_used"] = {"camera": cam, "via": via}
    if cam is None:
        facts.setdefault("warnings", []).append("no camera: nothing can render")
if r.use_compositing and group is not None and uses_sequencer:
    facts.setdefault("warnings", []).append("compositing and sequencer are both active: compare the image with what you expect")
facts["film_transparent"] = r.film_transparent
facts["world"] = scene.world.name if scene.world else None

# Evaluated instances per instancing object, at that frame.
old_frame = scene.frame_current
scene.frame_set(frame)
dg = bpy.context.evaluated_depsgraph_get()
counts = {}
for inst in dg.object_instances:
    if inst.is_instance and inst.parent is not None:
        key = inst.parent.original.name
        counts[key] = counts.get(key, 0) + 1
facts["instances_by_object"] = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:20])
facts["visible_objects"] = sum(1 for o in scene.objects if o.visible_get())

saved = {"pct": r.resolution_percentage, "path": r.filepath, "fmt": im.file_format, "mode": im.color_mode,
         "media": getattr(im, "media_type", None)}
cycles = getattr(scene, "cycles", None)
eevee = getattr(scene, "eevee", None)
saved_samples = (getattr(cycles, "samples", None), getattr(eevee, "taa_render_samples", None))
path = os.path.join(tempfile.gettempdir(), "opencode_verify_%d.png" % os.getpid())
image_b64 = None
try:
    r.resolution_percentage = max(1, min(100, int(100 * size / max(r.resolution_x, r.resolution_y))))
    if saved["media"] is not None:
        im.media_type = "IMAGE"
    im.file_format = "PNG"
    im.color_mode = "RGBA"
    r.filepath = path
    if cycles is not None and saved_samples[0]:
        cycles.samples = min(saved_samples[0], 16)
    if eevee is not None and saved_samples[1]:
        eevee.taa_render_samples = min(saved_samples[1], 8)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    img = bpy.data.images.load(path)
    try:
        w, h = img.size
        px = img.pixels[:]
    finally:
        bpy.data.images.remove(img)
    n = len(px) // 4
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
    with open(path, "rb") as fh:
        image_b64 = base64.b64encode(fh.read()).decode("ascii")
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
    scene.frame_set(old_frame)
    if os.path.exists(path):
        os.remove(path)

result = {"blender": bpy.app.version_string, "facts": facts, "image_base64": image_b64}
