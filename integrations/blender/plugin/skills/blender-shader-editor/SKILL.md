---

name: blender-shader-editor
description: Build, inspect and assign materials and shader graphs in the attached Blender session, for "shader editor", "material nodes", "assign a material", "vrscene" or "V-Ray material".
---

# Blender shader editor

Create materials, edit shader node graphs and node groups, assign materials to objects and convert V-Ray `.vrscene` materials.

## Workflow

1. **Read** with `blender_context({ reads: [...] })`, e.g. `shader.list_materials` or `shader.get_material_info`. Keep `script_handle` and `revision`.
2. **Change** with one `blender_apply`: exact `script_handle`, `if_revision`, a new unique `command_id`, a `summary`, up to 50 `{ op, args }` operations, applied atomically as one undo step "OpenCode: <summary>".
3. **Re-read** the material (or capture the Shader Editor) to verify.

- New `command_id` every time; a replayed one applies nothing.
- After `stale_revision` or `stale_handle`, call `blender_context` again.
- Mutations run in Object mode only (`mode_conflict` otherwise). On failure the document is restored; if that fails, OpenCode Link quarantines itself until the user presses **Reset OpenCode Link**.
- No session attached: see the blender-scene skill. Current arguments: `blender_catalog({ domain: "shader" })`.

## Catalog

### Reads

| Read                                  | Args                                                               | Meaning                                                               |
| ------------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------- |
| `shader.list_materials`               | `filter_text`, `only_node_materials` (false)                       | Materials with node usage and slot assignments                        |
| `shader.get_material_info`            | `name`*                                                            | Material settings, nodes and links                                    |
| `shader.list_shader_node_groups`      | `filter_text`                                                      | Shader node groups in the file                                        |
| `shader.get_shader_node_group_info`   | `name`*                                                            | Interface and nodes of a group                                        |
| `shader.list_shader_node_types`       | `filter`                                                           | Exact shader node type identifiers                                    |
| `shader.get_shader_editor_screenshot` | `material_name`, `group_name`, `max_size` (1200), `fit_all` (true) | Shader Editor capture (interface only)                                |
| `shader.analyze_vrscene_file`         | `filepath`*                                                        | Convertible V-Ray materials, textures, triplanar and randomizer usage |

### Operations

| Operation                                       | Args                                                                                                                                                 | Meaning                                                     |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `shader.create_material`                        | `name`*, `use_nodes` (true)                                                                                                                          | Bare material                                               |
| `shader.create_shader_material_from_template`   | `name`*, `template_name` (principled_pbr), `base_color`, `roughness`, `metallic`, `transmission`, `emission_color`, `emission_strength`, `alpha`     | Material from a high-level template                         |
| `shader.delete_material`                        | `name`*                                                                                                                                              | Delete a material                                           |
| `shader.assign_material`                        | `object_name`_, `material_name`_, `slot_index` (0)                                                                                                   | Fill or overwrite a material slot                           |
| `shader.add_shader_node`                        | `node_type`*, `material_name` or `group_name`, `name`, `location`                                                                                    | Add a node                                                  |
| `shader.remove_shader_node`                     | `node_name`*, `material_name` or `group_name`                                                                                                        | Remove a node                                               |
| `shader.connect_shader_nodes`                   | `from_node`_, `from_socket`_, `to_node`_, `to_socket`_, `material_name` or `group_name`                                                              | Link two sockets                                            |
| `shader.disconnect_shader_nodes`                | same as connect                                                                                                                                      | Remove one link                                             |
| `shader.set_shader_node_input`                  | `node_name`_, `input_name`_, `value`*, `material_name` or `group_name`                                                                               | Default value of an input                                   |
| `shader.add_shader_node_group_socket`           | `group_name`_, `name`_, `in_out` (INPUT), `socket_type` (NodeSocketFloat)                                                                            | Add a group interface socket                                |
| `workflows.create_material_and_assign`          | `name`*, `template_name`, `object_name`, `slot_index`, `mark_as_asset` (false), `description`, `author`, `tags`, plus the template colour/value args | Template material, assigned and optionally marked as asset  |
| `workflows.apply_library_material_to_object`    | `object_name`_, `filepath`_, `material_name`*, `library_name`, `slot_index` (0), `link` (false)                                                      | Import a material from a .blend library and assign it       |
| `shader.convert_vrscene_file` **side effect**   | `filepath`*, `mapping_group_name`, `group_socket_map`, `replace_existing` (false), `use_fake_user` (true)                                            | Convert one .vrscene into Blender materials                 |
| `shader.convert_vrscene_folder` **side effect** | `folder_path`*, `output_blend_path`, `mapping_group_name`, `group_socket_map`, `replace_existing` (false), `use_fake_user` (true)                    | Batch-convert a folder, optionally writing a .blend library |

Side-effect operations are not undoable and must be the **only** operation of their apply; confirm with the user first.

## Domain knowledge

- Colours are RGBA in the 0–1 range: `[1, 0, 0, 1]` is opaque red. Divide 0–255 values by 255.
- Principled BSDF sockets: `Base Color`, `Roughness`, `Metallic`, `IOR`, `Transmission Weight`, `Emission Color`, `Emission Strength`, `Alpha`.
- The output node is usually `Material Output`; its main input is `Surface`.
- Material slots are 0-indexed.
- Node type identifiers look like `ShaderNodeBsdfPrincipled`, `ShaderNodeTexImage`, `ShaderNodeMix`; discover them with `shader.list_shader_node_types`.
- Pass either `material_name` or `group_name` to node operations, never both: they select the graph being edited.
- V-Ray: run `shader.analyze_vrscene_file` before converting. `UVWGenRandomizer` and triplanar mapping are preserved faithfully only with a compatible custom mapping node group (`mapping_group_name`, `group_socket_map`); otherwise the converter falls back to standard mapping and reports warnings.

## Guardrails

- Never guess node type strings or socket names; read them first.
- Prefer `workflows.create_material_and_assign` for standard materials: one operation instead of three.
- Inspect a material before editing or deleting it.
- Keep file paths exactly as the user gave them.
- Prefer `blender_apply` operations; `blender_execute_code` is disabled by default and must never be relied on.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 4,
  "command_id": "mat-red-0001",
  "summary": "Red plastic on Cube",
  "operations": [
    {
      "op": "workflows.create_material_and_assign",
      "args": { "name": "RedPlastic", "object_name": "Cube", "base_color": [0.8, 0.05, 0.05, 1], "roughness": 0.35 }
    },
    {
      "op": "shader.add_shader_node",
      "args": {
        "material_name": "RedPlastic",
        "node_type": "ShaderNodeTexNoise",
        "name": "Grain",
        "location": [-400, 0]
      }
    }
  ],
  "expect": [{ "ref": "object:Cube", "predicate": "exists" }]
}
```
