---

name: blender-scene
description: Inspect and edit the attached Blender scene, for "Blender scene", "create an object", "move the camera", "delete an object", "list objects" or "inspect the scene".
---

# Blender scene

Read and change the objects, cameras, lights and hierarchy of the Blender session attached to this conversation.

## Connecting a session

- `blender_status()` reports whether the OpenCode Link add-on is installed, which sessions are offered and which conversations are attached.
- If no session is attached, ask the user to enable **Offer this session to OpenCode** in Blender (sidebar tab **OpenCode** in the 3D viewport, or the add-on preferences of **OpenCode Link**), then call `blender_hosts()` and `blender_attach({ offerID })`. Both ask permission.
- If the add-on is missing or outdated, `blender_install_host()` installs or updates it (asks permission; Blender 5.2 or later).
- `blender_detach()` releases the session when the user is done.
- `blender_catalog({ domain: "scene" })` lists the current reads and operations with their arguments.

## Workflow

1. **Read** with `blender_context({ reads: [...] })`. Keep `script_handle` and `revision` from the result (at most 20 reads per call).
2. **Change** with one `blender_apply` call: the exact `script_handle`, `if_revision` set to that revision, a new unique `command_id`, a short `summary` and up to 50 `operations` as `{ op, args }`. The whole batch is atomic and becomes one undo step named "OpenCode: <summary>".
3. **Re-read** with `blender_context` (or a viewport capture) to confirm the result, then report final object names and transforms.

Rules of `blender_apply`:

- Every apply needs a fresh `command_id`; replaying an ID applies nothing.
- `expect` clauses (`exists`, `absent`, `equals` with optional `path` and `value`) are checked before anything runs; use them to guard deletions and renames.
- On `stale_revision` or `stale_handle`, call `blender_context` again and rebuild the change from the new state.
- Mutations run in Object mode only; `mode_conflict` means the user must leave Edit/Sculpt/Paint mode first.
- On failure the document is restored. If restoration itself fails, OpenCode Link quarantines itself until the user presses **Reset OpenCode Link** in Blender.

## Catalog

### Reads (`blender_context`)

| Read                               | Args                                             | Meaning                                                                  |
| ---------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------ |
| `scene.get_scene_info`             | none                                             | Active scene summary: objects, camera, frame range, render settings      |
| `scene.get_full_scene_hierarchy`   | none                                             | Collection and object tree                                               |
| `scene.get_object_info`            | `name`*                                          | Transform, mesh stats, material slots, parent and children of one object |
| `scene.get_object_modifiers`       | `name`*                                          | Modifiers of one object, including Geometry Nodes exposed inputs         |
| `scene.get_viewport_screenshot`    | `max_size` (800)                                 | Capture of the 3D viewport as an image attachment                        |
| `scene.get_node_editor_screenshot` | `tree_name`, `max_size` (1200), `fit_all` (true) | Capture of the node editor, optionally showing a tree                    |

`*` = required. Captures only work when Blender runs with its interface.

### Operations (`blender_apply`)

| Operation                                  | Args                                                                                                                    | Meaning                                                                     |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `scene.create_object`                      | `primitive` (CUBE, SPHERE, CYLINDER, CONE, PLANE, CIRCLE, GRID, EMPTY, CAMERA, LIGHT; default CUBE), `name`, `location` | Add a primitive, camera, light or empty                                     |
| `scene.delete_object`                      | `name`*                                                                                                                 | Delete one object by exact name                                             |
| `scene.transform_object`                   | `name`*, `location`, `rotation`, `scale`                                                                                | Change only the supplied transform fields                                   |
| `scene.set_active_camera`                  | `name`*                                                                                                                 | Set the scene's render camera                                               |
| `workflows.create_string_to_curves_object` | `text`*, `object_name`, `tree_name`, `size` (1.0), `location`, `font_name`                                              | Mesh object whose Geometry Nodes modifier turns a string into filled curves |

All scene operations are undoable.

## Domain knowledge

- Blender is right-handed and Z-up: X is left-right, Y is depth, Z is up.
- Rotations are Euler angles in **radians**: 90° = `1.5708`, 180° = `3.14159`.
- Blender appends `.001`, `.002` on name collisions; always read back the name returned by the operation before referring to it.
- A default file contains `Cube`, `Camera` and `Light`; check `scene.get_scene_info` before adding duplicates.
- Use an `EMPTY` as a transform parent, target or group anchor.
- For procedural text, prefer `workflows.create_string_to_curves_object` over building the node tree by hand.

## Guardrails

- Inspect before mutating unless the user asks for a direct, unambiguous action. Never assume object names.
- Make the smallest targeted change; batch related operations into one apply so the user gets one undo step.
- Guard destructive operations with an `expect` clause on the object.
- Prefer `blender_apply` operations for every change. `blender_execute_code` is disabled by default and dangerous; never rely on it.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 12,
  "command_id": "scene-camera-0001",
  "summary": "Add hero camera",
  "operations": [
    { "op": "scene.create_object", "args": { "primitive": "CAMERA", "name": "HeroCam", "location": [6, -6, 4] } },
    { "op": "scene.transform_object", "args": { "name": "HeroCam", "rotation": [1.1, 0, 0.785] } },
    { "op": "scene.set_active_camera", "args": { "name": "HeroCam" } }
  ],
  "expect": [{ "ref": "object:HeroCam", "predicate": "absent" }]
}
```

Then confirm with `blender_context({ reads: [{ name: "scene.get_object_info", args: { name: "HeroCam" } }] })`.
