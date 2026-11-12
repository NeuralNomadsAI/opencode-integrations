# SPDX-License-Identifier: GPL-3.0-or-later
# The background render Blender's script (written next to its log by render.py): overrides in memory only,
# renders the frame range, then puts the result at the exact output: an image moved to its file, frames already
# at their pattern, or the frames encoded as an H.264 MP4 through the video sequencer (Blender 5.3: `strips`,
# FFMPEG needs media_type VIDEO). Prints OPENCODE_RENDER, OPENCODE_DONE or OPENCODE_FAILED for the plugin.
import json
import os
import shutil
import sys
import traceback

import bpy

o = json.loads(%r)


def mark(key, value):
    print("OPENCODE_%%s %%s" %% (key, json.dumps(value)), flush=True)


try:
    scene = bpy.data.scenes[o["scene"]]
    r = scene.render
    if o["resolution"]:
        r.resolution_x, r.resolution_y = o["resolution"]
        r.resolution_percentage = 100
    if o["engines"]:
        for name in o["engines"]:
            try:
                r.engine = name
                break
            except TypeError:
                pass
    if o["samples"]:
        if r.engine == "CYCLES":
            scene.cycles.samples = o["samples"]
        elif "EEVEE" in r.engine:
            scene.eevee.taa_render_samples = o["samples"]
    if o["source"] == "scene":
        r.use_sequencer = False
    elif o["source"] == "sequencer":
        r.use_sequencer = True
    s = r.image_settings
    if hasattr(s, "media_type"):
        s.media_type = "IMAGE"
    s.file_format = "PNG"
    s.color_mode = "RGBA" if r.film_transparent else "RGB"
    r.filepath = o["prefix"]
    r.use_file_extension = True
    r.use_overwrite = True
    r.use_placeholder = False
    scene.frame_start, scene.frame_end = o["start"], o["end"]
    se = scene.sequence_editor
    strips = (getattr(se, "strips", None) or getattr(se, "sequences", [])) if se else []
    used = {"engine": r.engine, "size": [r.resolution_x * r.resolution_percentage // 100, r.resolution_y * r.resolution_percentage // 100],
            "frames": [o["start"], o["end"]], "sequencer": bool(r.use_sequencer and len(strips)),
            "compositing": bool(r.use_compositing and getattr(scene, "compositing_node_group", None))}
    if r.engine == "CYCLES":
        used["samples"] = scene.cycles.samples
    elif "EEVEE" in r.engine:
        used["samples"] = scene.eevee.taa_render_samples
    mark("RENDER", used)
    bpy.ops.render.render(animation=True, scene=scene.name)
    missing = [f for f in o["frames"] if not os.path.isfile(f)]
    if missing:
        raise RuntimeError("%%d frame(s) were not written, first %%s" %% (len(missing), missing[0]))
    if o["kind"] == "still":
        shutil.move(o["frames"][0], o["output"])
    elif o["kind"] == "video":
        enc = bpy.data.scenes.new("opencode_encode")
        er = enc.render
        er.fps = max(1, round(o["fps"]))
        er.fps_base = er.fps / o["fps"]
        enc.frame_start, enc.frame_end = 1, len(o["frames"])
        editor = enc.sequence_editor_create()
        estrips = editor.strips if hasattr(editor, "strips") else editor.sequences
        strip = estrips.new_image("frames", o["frames"][0], 1, 1)
        for f in o["frames"][1:]:
            strip.elements.append(os.path.basename(f))
        strip.frame_final_duration = len(o["frames"])
        er.resolution_x, er.resolution_y, er.resolution_percentage = used["size"][0], used["size"][1], 100
        image = er.image_settings
        if hasattr(image, "media_type"):
            image.media_type = "VIDEO"
        image.file_format = "FFMPEG"
        er.ffmpeg.format, er.ffmpeg.codec, er.ffmpeg.constant_rate_factor = "MPEG4", "H264", o["quality"]
        er.ffmpeg.audio_codec = "NONE"
        er.filepath = o["output"]
        er.use_file_extension = False
        er.use_sequencer = True
        bpy.ops.render.render(animation=True, scene=enc.name)
        if not os.path.isfile(o["output"]):
            raise RuntimeError("the video was not written to %%s" %% o["output"])
    mark("DONE", {"output": o["output"], "bytes": os.path.getsize(o["output"]) if o["kind"] != "frames" else sum(os.path.getsize(f) for f in o["frames"]), "used": used})
except Exception:
    mark("FAILED", traceback.format_exc()[-2000:])
    sys.exit(1)
