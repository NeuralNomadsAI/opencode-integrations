---

name: blender-audit
description: Summarize a .blend file on disk without opening it in the attached session, for "audit blend", "missing files", "linked libraries", "preflight" or "inspect .blend".
autoinvoke: false
---

# Blender audit

Inspect `.blend` files on disk with `blender_audit`. No attached session is needed.

## How it runs

- `path_info` only reads the file from disk (path, size, timestamps); Blender is not started.
- `datablocks`, `missing_files` and `linked_libraries` launch a separate Blender in background mode and ask the user for permission.
- The Blender executable comes from the plugin option `blenderPath`, else the environment variable `NEURALNOMADS_BLENDER_BIN`, else `blender` on `PATH`. If none is found, ask the user to set one of them.

## Workflow

1. Validate the path with `path_info`.
2. Use `datablocks` for an overview of the file's composition.
3. Use `missing_files` as a render or delivery preflight.
4. Use `linked_libraries` to list linked .blend dependencies.

## Actions

| Action             | Args                                   | Meaning                                      |
| ------------------ | -------------------------------------- | -------------------------------------------- |
| `path_info`        | `blend_file`*                          | Path, size and timestamps (file only)        |
| `datablocks`       | `blend_file`*, `timeout_seconds` (120) | Counts of data-blocks by type                |
| `missing_files`    | `blend_file`*, `timeout_seconds`       | External file references that do not resolve |
| `linked_libraries` | `blend_file`*, `timeout_seconds`       | Linked library files and their status        |

## Examples

```json
{ "action": "path_info", "blend_file": "shots/sh010/sh010_lighting.blend" }
```

```json
{ "action": "missing_files", "blend_file": "shots/sh010/sh010_lighting.blend", "timeout_seconds": 300 }
```

## Reading the results

- Report missing files grouped by kind (images, libraries, caches, fonts) with the referenced paths, so the user can relink them.
- Relative paths starting with `//` are relative to the .blend file's folder.
- A linked library reported as missing usually breaks every datablock linked from it; list those first.
- Compare `datablocks` counts before and after a cleanup to show what changed.
- Run `path_info` first on a batch of files to skip wrong paths before starting background Blender for each one.

## Guardrails

- The audit reads the saved file: unsaved changes in an open Blender session are not included. Ask the user to save first when that matters, or inspect the live session with `blender_context`.
- Large production files can take long to open; raise `timeout_seconds` instead of retrying.
- Audits do not modify the file, but opening it may load add-ons and linked dependencies in the background Blender.
- Keep the user's file path exactly as given.
- Fix problems found by the audit in the attached session with `blender_apply` operations, never with `blender_execute_code`, which is disabled by default.
