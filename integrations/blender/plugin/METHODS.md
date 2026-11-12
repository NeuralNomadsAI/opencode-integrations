# Method inventory

What OpenCode for Blender 0.4.0 keeps, replaces and retires from 0.2.0. The 0.2.0 sources stay in Git at
`ec99ef2` (the last `main` commit with them), from which each retired part can be reintroduced (see the end).

## Tools

| Tool (0.2.0)           | Status   | In 0.4.0                                                                                                                                          |
| ---------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blender_hosts`        | kept     | Lists the offered Blenders with their attachment and the add-on's install state; `attach` (asks `blender_attach`) and `detach` are its arguments. |
| `blender_attach`       | replaced | `blender_hosts` with `attach`; the only offered Blender nobody uses attaches itself at the first Blender call.                                    |
| `blender_detach`       | replaced | `blender_hosts` with `detach`; clients use the RPC `detachHost`.                                                                                  |
| `blender_status`       | replaced | `blender_hosts` returns the host state; the RPC `status` is unchanged.                                                                            |
| `blender_install_host` | kept     | Installs OpenCode Link 0.4.0 (asks `blender_install_host`).                                                                                       |
| `blender_context`      | kept     | The add-on's compact typed scene read (`scene`) instead of named catalog reads.                                                                   |
| `blender_apply`        | kept     | The add-on's typed edit operations (`host/ops.json`) instead of catalog operations.                                                               |
| `blender_catalog`      | retired  | The operations are in `blender_apply`'s description and `host/ops.json`.                                                                          |
| `blender_execute_code` | kept     | Listed while the add-on is enabled; every run asks `blender_execute_code` (SEC-3).                                                                |
| `blender_docs`         | retired  | `blender_inspect` reads the running Blender's own API and links the official documentation of its version; no documentation is bundled.           |
| `blender_audit`        | retired  | `blender_lab` `get_blendfile_summary_*` summarise the open file; no background Blender is started for a closed one.                               |

New in 0.4.0:

| Tool              | What it does                                                                                        |
| ----------------- | --------------------------------------------------------------------------------------------------- |
| `blender_lab`     | Blender Lab's tools, run by the add-on (their code is part of it).                                  |
| `blender_inspect` | A type, operator or live data of the running Blender, with its documentation links (fixed program). |
| `blender_verify`  | A low-resolution render of the final output with facts (fixed program).                             |
| `blender_render`  | A render in a separate background Blender to an exact output, followed as a task (fixed program).   |
| `blender_save`    | Save, save as or save a copy (fixed program).                                                       |

## Catalog reads and operations

The 30 reads and 49 operations of 0.2.0 (`methods/host`) are **retired from the plugin**: no tool sends them. The add-on still
answers 76 of them on its host methods `context` and `apply` (`test/blender/host_tests.py` exercises its whole
catalog), so a plugin can expose them again; 3 left the add-on in 0.3. In 0.4.0 the typed operations of `blender_apply` and the fixed programs cover
what the trials needed; anything else goes through `blender_execute_code`.

| Domain         | Name                                           | Kind      | In the 0.4.0 add-on |
| -------------- | ---------------------------------------------- | --------- | ------------------- |
| addons         | `addons.ensure_bradley_asset_library`          | operation | yes                 |
| addons         | `addons.get_addon_status`                      | read      | yes                 |
| addons         | `addons.get_blenderkit_import_status`          | read      | yes                 |
| addons         | `addons.import_blenderkit_asset`               | operation | yes                 |
| addons         | `addons.import_bradley_preset`                 | operation | yes                 |
| addons         | `addons.import_sketchfab_model`                | operation | yes                 |
| addons         | `addons.list_bradley_assets`                   | read      | yes                 |
| addons         | `addons.search_blenderkit_assets`              | operation | yes                 |
| addons         | `addons.search_sketchfab_models`               | operation | yes                 |
| animation      | `animation.create_turntable_animation`         | operation | yes                 |
| animation      | `animation.get_object_animation_info`          | read      | yes                 |
| animation      | `animation.keyframe_object_transform`          | operation | yes                 |
| animation      | `animation.set_timeline_settings`              | operation | yes                 |
| assets         | `assets.clear_asset`                           | operation | yes                 |
| assets         | `assets.get_asset_info`                        | read      | yes                 |
| assets         | `assets.import_blend_asset`                    | operation | yes                 |
| assets         | `assets.list_asset_libraries`                  | read      | yes                 |
| assets         | `assets.list_blend_file_assets`                | read      | yes                 |
| assets         | `assets.list_current_file_assets`              | read      | yes                 |
| assets         | `assets.list_library_blend_files`              | read      | yes                 |
| assets         | `assets.mark_asset`                            | operation | yes                 |
| geometry_nodes | `geometry_nodes.add_geometry_node`             | operation | yes                 |
| geometry_nodes | `geometry_nodes.add_node_tree_socket`          | operation | yes                 |
| geometry_nodes | `geometry_nodes.attach_geometry_node_tree`     | operation | yes                 |
| geometry_nodes | `geometry_nodes.connect_geometry_nodes`        | operation | yes                 |
| geometry_nodes | `geometry_nodes.create_geometry_node_tree`     | operation | yes                 |
| geometry_nodes | `geometry_nodes.create_gn_from_template`       | operation | yes                 |
| geometry_nodes | `geometry_nodes.disconnect_geometry_nodes`     | operation | yes                 |
| geometry_nodes | `geometry_nodes.get_geometry_node_tree`        | read      | yes                 |
| geometry_nodes | `geometry_nodes.list_available_node_types`     | read      | yes                 |
| geometry_nodes | `geometry_nodes.list_geometry_node_trees`      | read      | yes                 |
| geometry_nodes | `geometry_nodes.list_geometry_nodes_modifiers` | read      | yes                 |
| geometry_nodes | `geometry_nodes.list_gn_templates`             | read      | yes                 |
| geometry_nodes | `geometry_nodes.remove_geometry_node`          | operation | yes                 |
| geometry_nodes | `geometry_nodes.rename_geometry_node`          | operation | yes                 |
| geometry_nodes | `geometry_nodes.set_geometry_node_location`    | operation | yes                 |
| geometry_nodes | `geometry_nodes.set_modifier_input`            | operation | yes                 |
| geometry_nodes | `geometry_nodes.set_node_input`                | operation | yes                 |
| scene          | `scene.create_object`                          | operation | yes                 |
| scene          | `scene.delete_object`                          | operation | yes                 |
| scene          | `scene.get_full_scene_hierarchy`               | read      | no (removed in 0.3) |
| scene          | `scene.get_node_editor_screenshot`             | read      | yes                 |
| scene          | `scene.get_object_info`                        | read      | no (removed in 0.3) |
| scene          | `scene.get_object_modifiers`                   | read      | yes                 |
| scene          | `scene.get_scene_info`                         | read      | yes                 |
| scene          | `scene.get_viewport_screenshot`                | read      | no (removed in 0.3) |
| scene          | `scene.set_active_camera`                      | operation | yes                 |
| scene          | `scene.transform_object`                       | operation | yes                 |
| shader         | `shader.add_shader_node`                       | operation | yes                 |
| shader         | `shader.add_shader_node_group_socket`          | operation | yes                 |
| shader         | `shader.analyze_vrscene_file`                  | read      | yes                 |
| shader         | `shader.assign_material`                       | operation | yes                 |
| shader         | `shader.connect_shader_nodes`                  | operation | yes                 |
| shader         | `shader.convert_vrscene_file`                  | operation | yes                 |
| shader         | `shader.convert_vrscene_folder`                | operation | yes                 |
| shader         | `shader.create_material`                       | operation | yes                 |
| shader         | `shader.create_shader_material_from_template`  | operation | yes                 |
| shader         | `shader.delete_material`                       | operation | yes                 |
| shader         | `shader.disconnect_shader_nodes`               | operation | yes                 |
| shader         | `shader.get_material_info`                     | read      | yes                 |
| shader         | `shader.get_shader_editor_screenshot`          | read      | yes                 |
| shader         | `shader.get_shader_node_group_info`            | read      | yes                 |
| shader         | `shader.list_materials`                        | read      | yes                 |
| shader         | `shader.list_shader_node_groups`               | read      | yes                 |
| shader         | `shader.list_shader_node_types`                | read      | yes                 |
| shader         | `shader.remove_shader_node`                    | operation | yes                 |
| shader         | `shader.set_shader_node_input`                 | operation | yes                 |
| shot_manager   | `shot_manager.create_shot`                     | operation | yes                 |
| shot_manager   | `shot_manager.enable_disable_shots`            | operation | yes                 |
| shot_manager   | `shot_manager.get_shot_details`                | read      | yes                 |
| shot_manager   | `shot_manager.get_shot_list`                   | read      | yes                 |
| shot_manager   | `shot_manager.get_shot_manager_status`         | read      | yes                 |
| shot_manager   | `shot_manager.launch_batch_render`             | operation | yes                 |
| shot_manager   | `shot_manager.modify_shot`                     | operation | yes                 |
| shot_manager   | `shot_manager.set_shot_manager_render_path`    | operation | yes                 |
| workflows      | `workflows.apply_library_material_to_object`   | operation | yes                 |
| workflows      | `workflows.create_material_and_assign`         | operation | yes                 |
| workflows      | `workflows.create_string_to_curves_object`     | operation | yes                 |
| workflows      | `workflows.scatter_library_asset_on_surface`   | operation | yes                 |

## Skills and data

The skills `blender-animation`, `blender-asset-browser`, `blender-audit`, `blender-docs`, `blender-geometry-nodes`, `blender-scene`, `blender-shader-editor`, `blender-shot-manager` and the bundled Blender documentation
(`data/blender_docs`) are **retired**: they described the 0.2.0 catalog. The working method joins a conversation
when it attaches a Blender (an instructions entry, `src/method.ts`), and `blender_inspect` reads the running
Blender instead of a bundled copy of its documentation.

## Reintroducing a retired part

```sh
git checkout ec99ef2 -- integrations/blender/plugin/methods/host     # catalog reads and operations
git checkout ec99ef2 -- integrations/blender/plugin/skills           # skills (load them with ctx.skill.transform, PKG-2)
git checkout ec99ef2 -- integrations/blender/plugin/src/audit.ts     # blender_audit (and its methods/tools manifest)
```

Then give each tool manifest an executor in `src/integration.ts` and a test. The catalog's manifests must match
the add-on's `catalog.json`, which 0.4.0 keeps as a source file (it has entries 0.2.0 did not).
