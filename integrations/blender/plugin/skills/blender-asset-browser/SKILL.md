---

name: blender-asset-browser
description: Browse asset libraries, mark assets and import from .blend libraries, BlenderKit, Sketchfab or Bradley presets, for "asset browser", "asset library", "mark as asset", "BlenderKit", "Sketchfab" or "Bradley preset".
---

# Blender asset browser

Discover and import assets from local asset libraries, manage asset metadata in the current file, and source assets through the BlenderKit, Sketchfab and Bradley Presets add-ons.

## Workflow

1. **Read** with `blender_context({ reads: [...] })`. Keep `script_handle` and `revision`.
2. **Change** with one `blender_apply` (exact `script_handle`, `if_revision`, new unique `command_id`, `summary`, up to 50 `{ op, args }`), applied atomically as one undo step.
3. **Re-read** (`assets.list_current_file_assets`, `scene.get_full_scene_hierarchy`) to verify.

- Replaying a `command_id` applies nothing. After `stale_revision` or `stale_handle`, read again.
- Mutations run in Object mode only. On failure the document is restored; if that fails, the user presses **Reset OpenCode Link**.
- **Network** operations ask the user for permission. **Side-effect** operations (downloads, preference changes) are not undoable and must be the only operation of their apply.
- No session attached: see the blender-scene skill. Arguments: `blender_catalog({ domain: "assets" })` and `({ domain: "addons" })`.

## Local libraries

| Read                              | Args                                              | Meaning                                     |
| --------------------------------- | ------------------------------------------------- | ------------------------------------------- |
| `assets.list_asset_libraries`     | none                                              | Libraries configured in Blender preferences |
| `assets.list_library_blend_files` | `library_name`, `recursive` (true), `limit` (200) | .blend files inside libraries               |
| `assets.list_blend_file_assets`   | `filepath`*, `library_name`, `asset_types`        | Importable datablocks of one .blend         |
| `assets.list_current_file_assets` | `asset_types`, `include_unmarked` (false)         | Assets of the current file                  |
| `assets.get_asset_info`           | `asset_type`_, `name`_                            | Metadata of one local datablock             |

| Operation (undoable)                         | Args                                                                                             | Meaning                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| `assets.import_blend_asset`                  | `filepath`_, `asset_type`_, `name`*, `library_name`, `link` (false), `link_to_scene` (true)      | Append or link one datablock                    |
| `assets.mark_asset`                          | `asset_type`_, `name`_, `description`, `author`, `tags`, `catalog_id`, `generate_preview` (true) | Mark a datablock as asset                       |
| `assets.clear_asset`                         | `asset_type`_, `name`_                                                                           | Clear asset metadata                            |
| `workflows.apply_library_material_to_object` | `object_name`_, `filepath`_, `material_name`*, `library_name`, `slot_index`, `link`              | Import a material and assign it                 |
| `workflows.scatter_library_asset_on_surface` | `surface_object_name`_, `filepath`_, `asset_name`*, ...                                          | Import and scatter (see blender-geometry-nodes) |

- Asset types: `OBJECT`, `MATERIAL`, `COLLECTION`, `NODE_GROUP`, `WORLD`.
- Append copies the datablock into the file; link references it read-only. Default is append.
- Imported names may get a `.001` suffix on collision; use the returned name.
- Keep library file paths exactly as given, including the user's separators.

## Third-party add-ons

These need the corresponding add-on installed and enabled in Blender. Check first with `addons.get_addon_status` (`addon_name` such as `blenderkit`, `sketchfab`; omit for all supported add-ons). If it is not active, tell the user to enable it in Blender preferences.

| Name                                  | Kind                                    | Args                                                                                                                                                                                                                 | Notes                                        |
| ------------------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `addons.get_addon_status`             | read                                    | `addon_name`                                                                                                                                                                                                         | Installed, active, logged in                 |
| `addons.search_blenderkit_assets`     | operation, **network**                  | `query`, `asset_type` (model), `page_size` (10), `page` (1), `free_only`, `geometry_nodes_only`, `animated`                                                                                                          | Queries the BlenderKit web API               |
| `addons.import_blenderkit_asset`      | operation, **network**, **side effect** | `asset_base_id`, `asset_id`, `query`, `asset_type`, `target_object`, `material_target_slot` (0), `resolution` (blend), `model_location`, `model_rotation`, `target_collection`, `nodegroup_mode`, `node_x`, `node_y` | Download finishes asynchronously             |
| `addons.get_blenderkit_import_status` | read                                    | `asset_base_id`, `asset_id`                                                                                                                                                                                          | Is the asset present in the file yet         |
| `addons.search_sketchfab_models`      | operation, **network**                  | `query`, `page_size` (12), `page` (1), `downloadable` (true), `animated`                                                                                                                                             | Queries the Sketchfab web API                |
| `addons.import_sketchfab_model`       | operation, **network**, **side effect** | `uid`*, `model_url`                                                                                                                                                                                                  | Download finishes asynchronously             |
| `addons.ensure_bradley_asset_library` | operation, **side effect**              | none                                                                                                                                                                                                                 | Registers the Bradley library in preferences |
| `addons.list_bradley_assets`          | read                                    | `filter_text`, `asset_kind` (ALL; NODE_GROUP, MATERIAL), `limit` (100)                                                                                                                                               | Presets in the local library                 |
| `addons.import_bradley_preset`        | operation (undoable)                    | `name`*, `asset_kind` (NODE_GROUP), `target_object`, `material_name`, `material_target_slot`, `nodegroup_mode` (MODIFIER), `node_x`, `node_y`, `model_location`, `model_rotation`                                    | Import and optionally apply                  |

Searches are operations, so their results come back from `blender_apply`; run each search as its own apply.

### BlenderKit

- Search first unless the user gave an ID; use the exact `asset_base_id` from the results.
- For materials pass `target_object` and `material_target_slot`.
- Imports are asynchronous: poll `addons.get_blenderkit_import_status` before relying on the asset. Free assets need no subscription.

### Sketchfab

- The add-on must be enabled and **logged in**; confirm with `addons.get_addon_status`.
- Search with `downloadable: true`, then import with the exact `uid`. Models arrive as collections; check the hierarchy afterwards.

### Bradley presets

- Mograph-style node groups (arrays, cloners, effectors) and shader presets from a local library.
- Run `addons.ensure_bradley_asset_library` once if the library may not be registered, then `addons.list_bradley_assets`, then `addons.import_bradley_preset` with the exact preset name.
- Apply node group presets to a named mesh object; apply shader presets to a material slot. Verify with `scene.get_object_modifiers` or `shader.get_material_info`.

## Guardrails

- Read asset metadata before importing, marking or clearing.
- Prefer the one-step workflows when the goal is import and use.
- Never combine a network or side-effect operation with other operations.
- Prefer `blender_apply` operations; `blender_execute_code` is disabled by default and must never be relied on.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 9,
  "command_id": "asset-mark-0001",
  "summary": "Publish RedPlastic",
  "operations": [
    {
      "op": "assets.mark_asset",
      "args": { "asset_type": "MATERIAL", "name": "RedPlastic", "author": "Lookdev", "tags": ["plastic", "red"] }
    }
  ],
  "expect": [{ "ref": "material:RedPlastic", "predicate": "exists" }]
}
```
