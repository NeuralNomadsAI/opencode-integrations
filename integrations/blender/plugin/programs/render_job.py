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


def sound_sources(scene, used, start, end):
    """What makes the rendered output audible: unmuted sound strips of the sequencer it plays, in the frame range,
    and speakers with a sound."""
    found = []
    se = scene.sequence_editor
    if used["sequencer"] and se is not None:
        every = getattr(se, "strips_all", None) or getattr(se, "sequences_all", [])
        found += ["strip %%r" %% s.name for s in every if s.type == "SOUND" and not s.mute
                  and s.frame_final_start <= end and s.frame_final_end > start]
    found += ["speaker %%r" %% o.name for o in scene.objects if o.type == "SPEAKER" and o.data.sound is not None]
    return found


def streams(path):
    """The streams the video file really holds, read back from it."""
    import aud

    out = {}
    clip = bpy.data.movieclips.load(path)
    try:
        out["video"] = {"size": list(clip.size), "frames": clip.frame_duration}
    finally:
        bpy.data.movieclips.remove(clip)
    try:
        sound = aud.Sound(path)
        rate, channels = sound.specs
        out["audio"] = {"rate": int(rate), "channels": int(channels), "seconds": round(sound.length / rate, 3)}
    except Exception:
        out["audio"] = None
    return out


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
        sources = sound_sources(scene, used, o["start"], o["end"])
        audio = {"sources": sources[:10]} if sources else {"none": "nothing audible in the rendered output"}
        if sources:
            # The montage's sound, mixed down over the rendered range, under the frames (AAC, as Blender's own
            # H.264 presets).
            wav = os.path.join(os.path.dirname(o["frames"][0]), "audio.wav")
            try:
                with bpy.context.temp_override(scene=scene):
                    bpy.ops.sound.mixdown(filepath=wav, check_existing=False, container="WAV", codec="PCM", format="S16")
                estrips.new_sound("audio", wav, 2, 1)
                er.ffmpeg.audio_codec, er.ffmpeg.audio_bitrate = "AAC", 192
                er.ffmpeg.audio_channels = "STEREO"
            except Exception as error:
                audio["error"] = "the audio could not be mixed down: %%s" %% error
        er.filepath = o["output"]
        er.use_file_extension = False
        er.use_sequencer = True
        bpy.ops.render.render(animation=True, scene=enc.name)
        if not os.path.isfile(o["output"]):
            raise RuntimeError("the video was not written to %%s" %% o["output"])
        written = streams(o["output"])
        if sources and written["audio"] is None:
            audio.setdefault("error", "the montage is audible but the video holds no audio stream")
    done = {"output": o["output"], "bytes": os.path.getsize(o["output"]) if o["kind"] != "frames" else sum(os.path.getsize(f) for f in o["frames"]), "used": used}
    if o["kind"] == "video":
        done.update(streams=written, audio=audio)
    mark("DONE", done)
except Exception:
    mark("FAILED", traceback.format_exc()[-2000:])
    sys.exit(1)
