---

name: blender-animation
description: Create and inspect simple object animations in the attached Blender session, for "animate this", "turntable", "keyframe" or "timeline".
---

# Blender animation

Keyframe object transforms, build turntables and set the timeline range and frame rate.

## Workflow

1. **Read** with `blender_context({ reads: [...] })`, e.g. `scene.get_scene_info` for the frame range or `animation.get_object_animation_info`. Keep `script_handle` and `revision`.
2. **Change** with one `blender_apply`: exact `script_handle`, `if_revision`, new unique `command_id`, `summary`, up to 50 `{ op, args }` operations. All keyframes of one apply form a single undo step "OpenCode: <summary>".
3. **Re-read** with `animation.get_object_animation_info` to verify.

- Replaying a `command_id` applies nothing. After `stale_revision` or `stale_handle`, read again.
- Mutations run in Object mode only (`mode_conflict` otherwise). On failure the document is restored; if that fails, the user presses **Reset OpenCode Link**.
- No session attached: see the blender-scene skill. Arguments: `blender_catalog({ domain: "animation" })`.

## Catalog

### Reads

| Read                                  | Args    | Meaning                                                        |
| ------------------------------------- | ------- | -------------------------------------------------------------- |
| `animation.get_object_animation_info` | `name`* | Animation channels of an object, including layered action data |
| `scene.get_scene_info`                | none    | Frame range, FPS and active camera                             |

### Operations (all undoable)

| Operation                              | Args                                                                                                     | Meaning                                             |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `animation.set_timeline_settings`      | `frame_start`, `frame_end`, `frame_current`, `fps`                                                       | Timeline range, current frame and playback FPS      |
| `animation.keyframe_object_transform`  | `name`_, `frame`_, `location`, `rotation`, `scale`, `interpolation`                                      | Set the supplied transforms and key them at `frame` |
| `animation.create_turntable_animation` | `object_name`*, `frame_start` (1), `frame_end` (72), `turns` (1.0), `axis` (Z), `interpolation` (LINEAR) | Full-turn rotation loop                             |

## Domain knowledge

- Rotations are Euler **radians**; one full turn is `6.28318` (2π).
- Interpolation: `LINEAR` (constant speed), `BEZIER` (ease in and out), `CONSTANT` (stepped).
- Turntables around Z are the standard product showcase.
- Blender 5 stores keyframes in layered actions; the operations handle slots and layers.
- Common frame rates: 24 (film), 25 (PAL), 30 (NTSC), 60 (smooth).
- `animation.keyframe_object_transform` only keys the channels you pass.

## Guardrails

- Set the timeline range to match the animation length, in the same apply when possible.
- Key only the channels the motion needs.
- Use `LINEAR` for mechanical or showcase turntables.
- Verify with `animation.get_object_animation_info` after keying.
- Prefer `blender_apply` operations; `blender_execute_code` is disabled by default and must never be relied on.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 3,
  "command_id": "anim-bounce-0001",
  "summary": "Bounce Ball",
  "operations": [
    { "op": "animation.set_timeline_settings", "args": { "frame_start": 1, "frame_end": 48, "fps": 24 } },
    {
      "op": "animation.keyframe_object_transform",
      "args": { "name": "Ball", "frame": 1, "location": [0, 0, 3], "interpolation": "BEZIER" }
    },
    {
      "op": "animation.keyframe_object_transform",
      "args": { "name": "Ball", "frame": 24, "location": [0, 0, 0.5], "interpolation": "BEZIER" }
    },
    {
      "op": "animation.keyframe_object_transform",
      "args": { "name": "Ball", "frame": 48, "location": [0, 0, 3], "interpolation": "BEZIER" }
    }
  ],
  "expect": [{ "ref": "object:Ball", "predicate": "exists" }]
}
```

Turntable in one operation:

```json
{
  "op": "animation.create_turntable_animation",
  "args": { "object_name": "Product", "frame_start": 1, "frame_end": 120, "turns": 1, "axis": "Z" }
}
```
