# SPDX-License-Identifier: GPL-3.0-or-later
# blender_verify (fixed program): renders low-resolution frames of a scene and returns the PNG with facts read
# from the pixels and the evaluated scene. By default the scene's final output (the sequencer when it is used,
# the compositor when active) through the camera that output uses; with view, from a free viewpoint (a
# temporary camera at view.location looking at view.target, lens in millimetres), the markers' cameras and the
# sequencer set aside for it. Several frames come back as one contact sheet, left to right and top to bottom.
# Render settings, cameras, markers and the current frame are restored afterwards; the temporary camera goes.
# criteria: the agent's own checks (a read-only expression or a bpy.data path, a comparison, a value, frames),
# each measured on the evaluated scene and passed or failed. visible: whether an object shows in the render at
# a frame, with every reason found when it does not. With either, the render runs only when asked (render, view).
# PARAMS is prepended by the plugin: {"scene"?, "frame"?, "frames"?, "view"?, "size"?, "criteria"?, "visible"?,
# "render"?}
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
    # Sound strips are heard, not seen: the image comes from the others.
    visible = [s for s in active if s.type != "SOUND"]
    uses_sequencer = bool(r.use_sequencer and visible)
    facts["sequencer"] = {"used": uses_sequencer, "strips_at_frame": [
        {"name": s.name, "type": s.type, "channel": s.channel, "blend": getattr(s, "blend_type", None),
         **({"scene": s.scene.name if s.scene else None, "scene_camera": s.scene_camera.name if s.scene_camera else None}
            if s.type == "SCENE" else {})}
        for s in sorted(active, key=lambda s: -s.channel)]}
    if uses_sequencer:
        ordered = sorted(visible, key=lambda s: -s.channel)
        top = ordered[0]
        # A text, a color card or an effect over the shots: the camera is that of the first scene strip under it.
        shown = next((s for s in ordered if s.type == "SCENE" and s.scene is not None), None)
        if shown is not None:
            cam = shown.scene_camera.name if shown.scene_camera else camera_at(shown.scene, f - shown.frame_start + shown.scene.frame_start)[0]
            via = "strip %r (scene %r)" % (shown.name, shown.scene.name)
            if shown is not top:
                via += " under %s" % ", ".join("%s %r" % (s.type.lower(), s.name) for s in ordered[:ordered.index(shown)])
            facts["camera_used"] = {"camera": cam, "via": via}
            if shown.scene == scene:
                facts.setdefault("warnings", []).append("the strip %r shows its own scene" % shown.name)
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


def shown_by(target):
    """The montages (other scenes' sequencers) with a strip showing target."""
    found = []
    for other in bpy.data.scenes:
        se = other.sequence_editor
        if other == target or se is None:
            continue
        every = getattr(se, "strips_all", None) or getattr(se, "sequences_all", [])
        if any(s.type == "SCENE" and s.scene == target for s in every):
            found.append(other.name)
    return found


summary = {"scene": scene.name,
           "scene_read": "given (scene)" if P.get("scene") else "the active scene (give scene to read another)", "engine": r.engine, "film_transparent": r.film_transparent,
           "world": scene.world.name if scene.world else None,
           "compositing": {"use_compositing": r.use_compositing, "node_group": group.name if group else None}}
if len(bpy.data.scenes) > 1:
    summary["scenes"] = [s.name for s in bpy.data.scenes][:20]
    montages = [] if P.get("scene") else shown_by(scene)
    if montages:
        summary.setdefault("warnings", []).append(
            "scene %r is a shot shown by the montage of %s: its final output is there (give scene %r to see it)" % (
                scene.name, ", ".join(repr(m) for m in montages), montages[0]))
per_frame = [output_facts(f) for f in frames]


# --- criteria: the agent's own acceptance checks, each measured on the evaluated scene at its frames -----------
CRITERIA = P.get("criteria") or []
VISIBLE = P.get("visible")
RENDER = bool(P["render"]) if "render" in P else (view is not None or not (CRITERIA or VISIBLE))
OPS = ("<", "<=", ">", ">=", "==", "!=", "approx", "between", "in", "contains")
SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "min", "max", "len", "round", "sum", "any", "all", "sorted", "int", "float", "bool", "str", "list",
    "tuple", "set", "range", "zip", "enumerate", "isinstance")}
if not isinstance(CRITERIA, list) or len(CRITERIA) > 50:
    raise ValueError("criteria: a list of at most 50 {expr or path, op, value, frame or frames, label?}")


def read_only_expression(text):
    """An expression that reads: no assignment, no operator call, no private attribute."""
    import ast
    tree = ast.parse(text, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, (ast.NamedExpr, ast.Lambda)):
            raise ValueError("criteria: %r is not a plain read" % text)
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr == "ops"):
            raise ValueError("criteria: %r reaches %s, which is not a read" % (text, node.attr))
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("criteria: %r names %s" % (text, node.id))
    return compile(tree, "<criterion>", "eval")


class Box(object):
    """World-space bounds: min, max, size and center (Vectors)."""

    def __init__(self, points):
        self.min = Vector([min(p[i] for p in points) for i in range(3)])
        self.max = Vector([max(p[i] for p in points) for i in range(3)])
        self.size = self.max - self.min
        self.center = (self.min + self.max) / 2

    def __repr__(self):
        return "Box(min=%s, max=%s)" % (rounded(self.min), rounded(self.max))


def evaluated_points(obj, dg):
    """World-space points of obj's evaluated geometry: its mesh when it has one, its bounds otherwise, and the
    bounds of the instances it makes (Geometry Nodes, particles, collections)."""
    ev = obj.evaluated_get(dg)
    points = []
    if obj.type in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
        mesh = ev.to_mesh()
        try:
            mw = ev.matrix_world
            points = [mw @ v.co for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    elif obj.type not in ("EMPTY", "CAMERA", "LIGHT", "SPEAKER", "LIGHT_PROBE"):
        points = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    for inst in dg.object_instances:
        if inst.is_instance and inst.parent is not None and inst.parent.original == obj:
            points += [inst.matrix_world @ Vector(c) for c in inst.object.bound_box]
    return points


def namespace(dg, f):
    def ev(name):
        return bpy.data.objects[name].evaluated_get(dg)

    def bounds(name):
        points = evaluated_points(bpy.data.objects[name], dg)
        if not points:
            raise ValueError("%r has no geometry at frame %d" % (name, f))
        return Box(points)

    def world(name):
        return ev(name).matrix_world.translation.copy()

    return {"__builtins__": SAFE_BUILTINS, "bpy": bpy, "math": math, "Vector": Vector, "scene": scene,
            "frame": f, "depsgraph": dg, "ev": ev, "bounds": bounds, "world": world}


def rounded(value):
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else str(value)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if hasattr(value, "bl_rna"):
        return getattr(value, "name", None) or value.bl_rna.identifier
    if isinstance(value, Box):
        return {"min": rounded(value.min), "max": rounded(value.max)}
    try:
        return [rounded(v) for v in value][:64]
    except TypeError:
        return repr(value)[:200]


def compare(measured, op, value, tol):
    def numbers(x):
        if isinstance(x, str):
            return None
        try:
            return [float(v) for v in x]
        except (TypeError, ValueError):
            return None

    if op == "in":
        return measured in value
    if op == "contains":
        return value in measured
    if op == "between":
        lo, hi = value
        seq = numbers(measured)
        return all(lo <= v <= hi for v in seq) if seq is not None else lo <= measured <= hi
    seq = numbers(measured)
    if seq is not None and op != "==" and op != "!=" or (seq is not None and isinstance(value, (list, tuple))):
        targets = numbers(value) if isinstance(value, (list, tuple)) else [value] * len(seq)
        if targets is None or len(targets) != len(seq):
            raise ValueError("value: %d numbers to compare with %s" % (len(seq), rounded(measured)))
        return all(compare(m, op, t, tol) for m, t in zip(seq, targets))
    if op == "approx":
        return abs(measured - value) <= tol
    return {"<": measured < value, "<=": measured <= value, ">": measured > value, ">=": measured >= value,
            "==": measured == value, "!=": measured != value}[op]


def criterion_frames(c):
    if "frames" in c:
        return [int(x) for x in c["frames"]]
    return [int(c["frame"])] if "frame" in c else frames


def render_view_layers():
    return [vl for vl in scene.view_layers if vl.use] or [bpy.context.view_layer]


def depsgraph_at():
    if scene == bpy.context.scene:
        return bpy.context.evaluated_depsgraph_get()
    dg = render_view_layers()[0].depsgraph
    dg.update()
    return dg


def layer_paths(layer, target, path=()):
    """The LayerCollection chains from layer down to every collection holding target."""
    here = path + (layer,)
    if target.name in layer.collection.objects:
        yield here
    for child in layer.children:
        yield from layer_paths(child, target, here)


def material_reasons(obj):
    """Materials that render nothing: no surface or volume to the output, Alpha 0, zero density."""
    out = []
    for slot in obj.material_slots:
        m = slot.material
        if m is None or not m.node_tree:
            continue
        nodes = m.node_tree.nodes
        output = next((n for n in nodes if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output), None)
        if output is not None and not any(s.is_linked for s in output.inputs):
            out.append("material %r: nothing connected to its output" % m.name)
        for n in nodes:
            if not any(o.is_linked for o in n.outputs):
                continue  # a node feeding nothing renders nothing either way
            alpha = n.inputs.get("Alpha")
            if alpha is not None and n.bl_idname.startswith("ShaderNodeBsdf") and not alpha.is_linked and alpha.default_value <= 0.001:
                out.append("material %r: %s Alpha is 0 (transparent)" % (m.name, n.name))
            density = n.inputs.get("Density")
            if density is not None and n.bl_idname.startswith("ShaderNodeVolume") and not density.is_linked and density.default_value <= 0:
                out.append("material %r: %s Density is 0" % (m.name, n.name))
    return out


def visibility(obj, f, dg):
    """Whether obj shows in the render at frame f, and every reason found when it does not."""
    from bpy_extras.object_utils import world_to_camera_view
    reasons, notes = [], []
    if obj.name not in scene.objects:
        reasons.append("not in scene %r" % scene.name)
    layers = render_view_layers()
    rendered_in = []
    for vl in scene.view_layers:
        paths = list(layer_paths(vl.layer_collection, obj))
        if not paths:
            continue
        ok = False
        for chain in paths:
            names = "/".join(lc.name for lc in chain[1:]) or "Scene Collection"
            if any(lc.exclude for lc in chain):
                reasons.append("collection %s excluded from view layer %r" % (names, vl.name))
            elif any(lc.collection.hide_render for lc in chain):
                reasons.append("collection %s hidden in render" % names)
            elif any(lc.holdout for lc in chain):
                reasons.append("collection %s is a holdout in view layer %r (renders as a hole)" % (names, vl.name))
            else:
                ok = True
                if any(lc.indirect_only for lc in chain):
                    notes.append("collection %s is indirect only in %r (seen in reflections only, Cycles)" % (names, vl.name))
        if ok and vl in layers:
            rendered_in.append(vl.name)
        elif ok:
            reasons.append("view layer %r holding it is not rendered (Use for rendering off)" % vl.name)
    if obj.hide_render:
        reasons.append("hidden in render (hide_render)")
    if not getattr(obj, "visible_camera", True):
        reasons.append("invisible to the camera (Ray Visibility: Camera off)")
    if getattr(obj, "is_holdout", False):
        reasons.append("holdout object (renders as a hole)")
    if abs(obj.evaluated_get(dg).matrix_world.determinant()) < 1e-12:
        reasons.append("scaled to zero")
    reasons += material_reasons(obj)
    points = evaluated_points(obj, dg)
    if not points:
        reasons.append("no geometry at this frame (evaluated geometry empty)")
    cam_name, via = camera_at(scene, f)
    cam = bpy.data.objects.get(cam_name) if cam_name else None
    report = {"frame": f, "camera": cam_name, "camera_via": via}
    if cam is None:
        reasons.append("no camera")
    elif points:
        clip = (cam.data.clip_start, cam.data.clip_end)
        proj = [world_to_camera_view(scene, cam.evaluated_get(dg), p) for p in points]
        xs, ys, zs = [p.x for p in proj], [p.y for p in proj], [p.z for p in proj]
        report["screen_box"] = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]
        report["depth"] = [round(min(zs), 4), round(max(zs), 4)]
        inside = sum(1 for x, y, z in zip(xs, ys, zs) if 0 <= x <= 1 and 0 <= y <= 1 and clip[0] <= z <= clip[1])
        if max(zs) < clip[0]:
            reasons.append("behind camera %r (or nearer than its clip start %g)" % (cam_name, clip[0]))
        elif min(zs) > clip[1]:
            reasons.append("beyond camera %r's clip end %g" % (cam_name, clip[1]))
        elif not inside and (max(xs) < 0 or min(xs) > 1 or max(ys) < 0 or min(ys) > 1):
            reasons.append("out of frame of camera %r (screen box %s, the frame is 0..1)" % (cam_name, report["screen_box"]))
        elif inside < len(points):
            notes.append("partly in frame: %d of %d points" % (inside, len(points)))
        if not reasons:
            origin = cam.evaluated_get(dg).matrix_world.translation
            center = sum(points, Vector()) / len(points)
            blockers, samples = set(), [center] + points[:: max(1, len(points) // 8)][:8]
            for p in samples:
                direction = p - origin
                hit, _, _, _, hit_obj, _ = scene.ray_cast(dg, origin, direction.normalized(), distance=direction.length - 1e-3)
                if hit and hit_obj is not None and hit_obj.original != obj:
                    blockers.add(hit_obj.original.name)
                    continue
                blockers.add(None)
            if None not in blockers and blockers:
                reasons.append("hidden behind %s from camera %r" % (sorted(blockers), cam_name))
            elif len(blockers) > 1:
                notes.append("partly hidden behind %s" % sorted(b for b in blockers if b))
    report["visible"] = not reasons
    if reasons:
        report["reasons"] = list(dict.fromkeys(reasons))
    if notes:
        report["notes"] = notes
    if rendered_in:
        report["view_layers"] = rendered_in
    return report


def run_checks():
    compiled = []
    for i, c in enumerate(CRITERIA):
        if not isinstance(c, dict) or not (("expr" in c) ^ ("path" in c)) or c.get("op") not in OPS or "value" not in c:
            raise ValueError("criteria[%d]: {expr or path, op: one of %s, value, frame or frames, tol?, label?}" % (i, OPS))
        text = c["expr"] if "expr" in c else "bpy.data." + c["path"]
        compiled.append((c, text, read_only_expression(text), criterion_frames(c)))
    targets = []
    if VISIBLE:
        spec = {"object": VISIBLE} if isinstance(VISIBLE, str) else VISIBLE
        obj = bpy.data.objects.get(spec.get("object", "")) if isinstance(spec, dict) else None
        if obj is None:
            raise ValueError("visible: no object %r" % (spec.get("object") if isinstance(spec, dict) else VISIBLE))
        targets = [int(x) for x in spec.get("frames") or [spec.get("frame", frames[0])]]
    wanted = sorted({f for *_, fs in compiled for f in fs} | set(targets))
    measured = {}
    seen = []
    old = scene.frame_current
    try:
        for f in wanted:
            scene.frame_set(f)
            dg = depsgraph_at()
            for index, (c, text, code, fs) in enumerate(compiled):
                if f not in fs:
                    continue
                try:
                    value = eval(code, namespace(dg, f))  # noqa: S307 - a checked read-only expression
                    entry = {"frame": f, "measured": rounded(value),
                             "passed": bool(compare(value, c["op"], c["value"], float(c.get("tol", 1e-4))))}
                except Exception as ex:  # noqa: BLE001
                    entry = {"frame": f, "passed": False, "error": ("%s: %s" % (type(ex).__name__, ex))[:300]}
                measured.setdefault(index, []).append(entry)
            if f in targets:
                seen.append(visibility(obj, f, dg))
    finally:
        scene.frame_set(old)
    out = {}
    if compiled:
        rows = []
        for index, (c, text, _code, _fs) in enumerate(compiled):
            entries = measured.get(index, [])
            row = {"label": c.get("label") or text, "check": "%s %s %s" % (text, c["op"], c["value"]),
                   "passed": bool(entries) and all(e["passed"] for e in entries)}
            row.update(entries[0] if len(entries) == 1 else {"frames": entries})
            rows.append(row)
        out["criteria"] = rows
        out["all_passed"] = all(row["passed"] for row in rows)
        out["failed"] = [row["label"] for row in rows if not row["passed"]]
    if targets:
        out["visible"] = {"object": obj.name, "frames": seen} if len(seen) > 1 else dict(seen[0], object=obj.name)
    return out


checks = run_checks() if (CRITERIA or VISIBLE) else {}


def render_frames():
    """The renders (unchanged since 0.4.2): settings, cameras and the current frame restored; the PNG or sheet."""
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
    return image_b64



image_b64 = render_frames() if RENDER else None
if len(per_frame) > 1:
    summary["frames"] = per_frame
if len(per_frame) == 1:
    merged = dict(summary, **per_frame[0])
    if summary.get("warnings") or per_frame[0].get("warnings"):
        merged["warnings"] = summary.get("warnings", []) + per_frame[0].get("warnings", [])
result = {"blender": bpy.app.version_string, "facts": merged if len(per_frame) == 1 else summary,
          "image_base64": image_b64, **checks}
