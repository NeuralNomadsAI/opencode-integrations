---

name: blender-docs
description: Search the Blender Python API reference and the Blender manual, for "Blender API", "bpy docs" or "Blender manual".
autoinvoke: false
---

# Blender docs

Look up the Blender Python API reference and the Blender user manual with `blender_docs`. It works without Blender running and without an attached session.

## Workflow

1. Search first: `search_api` for Python API names, `search_manual` for user-facing features and UI.
2. Read a page with `get_api` once you know the exact identifier.
3. Use what you learn to choose the right `blender_apply` operation or its argument values, and to explain behaviour to the user.

## Actions

| Action          | Args                                                                     | Meaning                                    |
| --------------- | ------------------------------------------------------------------------ | ------------------------------------------ |
| `search_api`    | `query`, `max_results` (20, at most 100), `context_lines` (1, at most 8) | Search the Python API reference            |
| `search_manual` | `query`, `max_results`, `context_lines`                                  | Search the user manual                     |
| `get_api`       | `identifier`, `max_chars` (32768)                                        | Read one API page, e.g. `bpy.types.Object` |

## Examples

```json
{ "action": "search_api", "query": "GeometryNodeDistributePointsOnFaces", "max_results": 5 }
```

```json
{ "action": "get_api", "identifier": "bpy.types.ShaderNodeBsdfPrincipled", "max_chars": 8000 }
```

```json
{ "action": "search_manual", "query": "asset catalogs", "context_lines": 3 }
```

## Useful lookups

- Node type identifiers and their sockets (`bpy.types.GeometryNode...`, `bpy.types.ShaderNode...`), to cross-check names returned by `geometry_nodes.list_available_node_types` or `shader.list_shader_node_types`.
- Property ranges and units (for example rotation in radians, colour as RGBA 0–1).
- Manual pages on modifiers, asset libraries, layered actions and render settings.

## Search tips

- Search with the exact identifier when you have one (`bpy.types.Material`, `bpy.ops.object.modifier_add`); use plain words for concepts ("layered action slot").
- If `search_api` returns too many hits, narrow the query or lower `max_results`; raise `context_lines` to see surrounding definitions.
- API pages cover the Blender version the reference was built from; when a socket or property seems missing, check the live names with the session reads before concluding.
- Quote the identifier and the relevant lines when you explain an answer to the user.

## Guardrails

- Docs describe Blender in general, not the open file: never assume an object, material or tree exists because a page mentions it. Read the session with `blender_context`.
- Keep `max_chars` and `max_results` small enough to stay focused.
- Docs support choosing `blender_apply` operations; they are no reason to fall back to `blender_execute_code`, which is disabled by default and must never be relied on.
