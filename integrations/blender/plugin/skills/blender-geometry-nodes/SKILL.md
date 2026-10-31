---

name: blender-geometry-nodes
description: Build, inspect and modify Geometry Nodes trees in the attached Blender session, for "Geometry Nodes", "node tree", "scatter on surface" or "modifier input".
---

# Blender Geometry Nodes

Create Geometry Nodes trees, edit their nodes and links, attach them to objects and drive their modifier inputs.

## Workflow

1. **Read** with `blender_context({ reads: [...] })`, e.g. `geometry_nodes.list_geometry_node_trees` or `geometry_nodes.get_geometry_node_tree`. Keep `script_handle` and `revision`.
2. **Change** with one `blender_apply`: exact `script_handle`, `if_revision`, a new unique `command_id`, a `summary`, up to 50 `{ op, args }` operations. The batch is atomic and becomes one undo step "OpenCode: <summary>".
3. **Re-read** the tree (or capture the node editor) to verify.

- Replaying a `command_id` applies nothing; use a new one per apply.
- After `stale_revision` or `stale_handle`, call `blender_context` again.
- Mutations run in Object mode only (`mode_conflict` otherwise).
- On failure the document is restored; if restoration fails, OpenCode Link quarantines itself until the user presses **Reset OpenCode Link**.
- No session attached: see the blender-scene skill (`blender_hosts`, `blender_attach`). Current arguments: `blender_catalog({ domain: "geometry_nodes" })`.

## Catalog

### Reads

| Read                                           | Args                                             | Meaning                                             |
| ---------------------------------------------- | ------------------------------------------------ | --------------------------------------------------- |
| `geometry_nodes.list_geometry_node_trees`      | none                                             | All GN trees with modifier assignments              |
| `geometry_nodes.list_geometry_nodes_modifiers` | `object_name`                                    | GN modifiers in the scene or on one object          |
| `geometry_nodes.get_geometry_node_tree`        | `tree_name`*                                     | Nodes, links, interface sockets, assigned modifiers |
| `geometry_nodes.list_available_node_types`     | `filter`                                         | Exact node type identifiers, filtered by keyword    |
| `geometry_nodes.list_gn_templates`             | none                                             | High-level templates with their parameters          |
| `scene.get_object_modifiers`                   | `name`*                                          | Modifier stack of one object with exposed inputs    |
| `scene.get_node_editor_screenshot`             | `tree_name`, `max_size` (1200), `fit_all` (true) | Node editor capture (interface only)                |

### Operations (all undoable)

| Operation                                    | Args                                                                                                                                                                                                                                                                                               | Meaning                                                             |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `geometry_nodes.create_geometry_node_tree`   | `name`*, `object_name`                                                                                                                                                                                                                                                                             | New tree, optionally added as modifier                              |
| `geometry_nodes.attach_geometry_node_tree`   | `object_name`_, `tree_name`_, `modifier_name`                                                                                                                                                                                                                                                      | Attach an existing tree to an object                                |
| `geometry_nodes.create_gn_from_template`     | `template_name`_, `tree_name`_, `object_name`, plus template parameters                                                                                                                                                                                                                            | Complete setup from a template                                      |
| `geometry_nodes.add_geometry_node`           | `tree_name`_, `node_type`_, `name`, `location`                                                                                                                                                                                                                                                     | Add a node; returns its info                                        |
| `geometry_nodes.remove_geometry_node`        | `tree_name`_, `node_name`_                                                                                                                                                                                                                                                                         | Remove a node                                                       |
| `geometry_nodes.rename_geometry_node`        | `tree_name`_, `node_name`_, `new_name`*                                                                                                                                                                                                                                                            | Rename a node                                                       |
| `geometry_nodes.set_geometry_node_location`  | `tree_name`_, `node_name`_, `location`*                                                                                                                                                                                                                                                            | Move a node to `[x, y]`                                             |
| `geometry_nodes.connect_geometry_nodes`      | `tree_name`_, `from_node`_, `from_socket`_, `to_node`_, `to_socket`*                                                                                                                                                                                                                               | Link two sockets                                                    |
| `geometry_nodes.disconnect_geometry_nodes`   | same as connect                                                                                                                                                                                                                                                                                    | Remove one link                                                     |
| `geometry_nodes.set_node_input`              | `tree_name`_, `node_name`_, `input_name`_, `value`_                                                                                                                                                                                                                                                | Default value of a node input                                       |
| `geometry_nodes.add_node_tree_socket`        | `tree_name`_, `name`_, `in_out` (INPUT), `socket_type` (NodeSocketFloat)                                                                                                                                                                                                                           | Add an exposed interface socket                                     |
| `geometry_nodes.set_modifier_input`          | `object_name`_, `modifier_name`_, `input_name`_, `value`_                                                                                                                                                                                                                                          | Set an exposed input on the object's modifier                       |
| `workflows.scatter_library_asset_on_surface` | `surface_object_name`_, `filepath`_, `asset_name`*, `asset_type` (OBJECT), `library_name`, `tree_name`, `density` (100), `instance_scale`, `align_to_normal` (true), `random_rotation` (true), `random_scale_range`, `use_realize_instances` (false), `keep_input_geometry` (true), `link` (false) | Import an object or collection from a .blend library and scatter it |
| `workflows.create_string_to_curves_object`   | `text`*, `object_name`, `tree_name`, `size`, `location`, `font_name`                                                                                                                                                                                                                               | Procedural text object                                              |

`create_gn_from_template` accepts extra template parameters (for example density, instance object, noise scale, strength); read them from `geometry_nodes.list_gn_templates` instead of guessing.

## Domain knowledge

- Node type identifiers are internal strings such as `GeometryNodeMeshCube` or `GeometryNodeDistributePointsOnFaces`. Discover them with `geometry_nodes.list_available_node_types`.
- Socket names vary with node type and Blender version; read them from `geometry_nodes.get_geometry_node_tree` before connecting.
- `Group Input` and `Group Output` are the default names of the interface nodes.
- Modifier inputs are the tree's interface inputs shown in the Properties editor; change them with `geometry_nodes.set_modifier_input`, not by editing the tree.
- Scatter density is points per square metre: roughly 5–20 for foliage, 50–200 for grass.
- Build manually in order inside one apply: add nodes, connect them, then set input values. Later operations in the same apply can refer to node names created earlier.

## Guardrails

- Never guess node type strings or socket names.
- Prefer `geometry_nodes.create_gn_from_template` or `workflows.scatter_library_asset_on_surface` over node-by-node construction when they fit.
- Keep node names stable once the user refers to them.
- Verify every edit with `geometry_nodes.get_geometry_node_tree` or a node editor capture.
- Bradley presets live in the blender-asset-browser skill.
- Prefer `blender_apply` operations; `blender_execute_code` is disabled by default and must never be relied on.

## Example

```json
{
  "script_handle": "<from blender_context>",
  "if_revision": 7,
  "command_id": "gn-subdiv-0001",
  "summary": "Subdivide Rock",
  "operations": [
    { "op": "geometry_nodes.create_geometry_node_tree", "args": { "name": "RockGN", "object_name": "Rock" } },
    {
      "op": "geometry_nodes.add_geometry_node",
      "args": {
        "tree_name": "RockGN",
        "node_type": "GeometryNodeSubdivisionSurface",
        "name": "Subdiv",
        "location": [200, 0]
      }
    },
    {
      "op": "geometry_nodes.connect_geometry_nodes",
      "args": {
        "tree_name": "RockGN",
        "from_node": "Group Input",
        "from_socket": "Geometry",
        "to_node": "Subdiv",
        "to_socket": "Mesh"
      }
    },
    {
      "op": "geometry_nodes.connect_geometry_nodes",
      "args": {
        "tree_name": "RockGN",
        "from_node": "Subdiv",
        "from_socket": "Mesh",
        "to_node": "Group Output",
        "to_socket": "Geometry"
      }
    },
    {
      "op": "geometry_nodes.set_node_input",
      "args": { "tree_name": "RockGN", "node_name": "Subdiv", "input_name": "Level", "value": 2 }
    }
  ],
  "expect": [{ "ref": "object:Rock", "predicate": "exists" }]
}
```
