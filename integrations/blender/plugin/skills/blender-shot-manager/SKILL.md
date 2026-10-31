---

name: blender-shot-manager
description: Manage shots and batch renders with the Shot Manager extension in the attached Blender session, for "Shot Manager", "shot list", "create a shot" or "batch render".
---

# Blender Shot Manager

Inspect and edit Shot Manager shots, set the render output root and launch batch renders. Requires the third-party **Shot Manager** extension installed and active in the scene.

## Workflow

1. **Check** with `blender_context({ reads: [{ name: "shot_manager.get_shot_manager_status", args: {} }] })`; stop and tell the user if Shot Manager is not active. Keep `script_handle` and `revision`.
2. **Inspect** with `shot_manager.get_shot_list` or `shot_manager.get_shot_details` before editing.
3. **Change** with one `blender_apply`: exact `script_handle`, `if_revision`, new unique `command_id`, `summary`, up to 50 `{ op, args }` operations, applied atomically as one undo step.
4. **Re-read** the shot list to verify.

- Replaying a `command_id` applies nothing. After `stale_revision` or `stale_handle`, read again.
- Mutations run in Object mode only. On failure the document is restored; if that fails, the user presses **Reset OpenCode Link**.
- No session attached: see the blender-scene skill. Arguments: `blender_catalog({ domain: "shot_manager" })`.

## Catalog

### Reads

| Read                                   | Args         | Meaning                               |
| -------------------------------------- | ------------ | ------------------------------------- |
| `shot_manager.get_shot_manager_status` | none         | Is the extension active in this scene |
| `shot_manager.get_shot_list`           | none         | All shots with basic info             |
| `shot_manager.get_shot_details`        | `shot_name`* | Details of one shot                   |

### Operations

| Operation                                          | Args                                                                      | Meaning                                       |
| -------------------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------------- |
| `shot_manager.create_shot`                         | `name`, `start_frame`, `end_frame`, `camera`                              | New shot                                      |
| `shot_manager.modify_shot`                         | `shot_name`*, `name`, `start_frame`, `end_frame`, `enabled`, `still_mode` | Rename, retime, enable or set still mode      |
| `shot_manager.enable_disable_shots`                | `shot_names`_, `enabled`_                                                 | Enable or disable several shots               |
| `shot_manager.set_shot_manager_render_path`        | `path`*, `redirect` (true)                                                | Alternative root output path                  |
| `shot_manager.launch_batch_render` **side effect** | none                                                                      | Render all enabled shots, writing image files |

`shot_manager.launch_batch_render` is not undoable and must be the **only** operation of its apply. Confirm with the user before launching it.

## Domain knowledge

- Each shot has a frame range, an optional camera and an enabled flag used by batch rendering.
- Batch render processes all enabled shots in order and writes to the path set by `shot_manager.set_shot_manager_render_path`.
- `still_mode` renders a single frame for the shot.

## Guardrails

- Fail fast when Shot Manager is not active.
- Read a shot before changing its frame range or enabled state.
- Keep user-provided shot names and paths exact.
- Set the render path in an earlier apply, then launch the render in its own apply.
- Prefer `blender_apply` operations; `blender_execute_code` is disabled by default and must never be relied on.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 15,
  "command_id": "shots-prep-0001",
  "summary": "Prepare shots for render",
  "operations": [
    {
      "op": "shot_manager.create_shot",
      "args": { "name": "SH040", "start_frame": 301, "end_frame": 380, "camera": "Cam_SH040" }
    },
    { "op": "shot_manager.enable_disable_shots", "args": { "shot_names": ["SH010", "SH020"], "enabled": false } },
    { "op": "shot_manager.set_shot_manager_render_path", "args": { "path": "//renders/", "redirect": true } }
  ]
}
```

Then, after the user confirms, read again and launch the render alone:

```json
{
  "script_handle": "<handle>",
  "if_revision": 16,
  "command_id": "shots-render-0001",
  "summary": "Batch render",
  "operations": [{ "op": "shot_manager.launch_batch_render", "args": {} }]
}
```
