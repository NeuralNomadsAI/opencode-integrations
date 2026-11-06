---

name: gaffer-graph
description: Inspect and edit the Gaffer graph explicitly attached to the current OpenCode session.
---

# Gaffer

Use this skill only when a Gaffer script is attached to the current session.

## Workflow

1. Read the binding metadata in the system context and pass its exact `script_handle` to every Gaffer tool call.
2. Call `gaffer_context` with a small working set. Prefer explicit refs, a narrow match, `plugs` projections and the smallest useful radius. Do not request complete node schemas when a few plug globs are sufficient.
3. Preserve the returned `revision`. Do not prepare a write from stale context.
4. Call `gaffer_apply` with that exact `if_revision`, a new stable `command_id`, and one coherent atomic intent.
5. Include postconditions in `expect` for the objects and values that determine success.
6. On `revision_conflict`, read context again and rebuild the intent instead of retrying it unchanged.

## Operations

`gaffer_apply` supports graph creation and editing plus complete batch rendering. `graph.connect_many` connects ordered `sources` to an input `ArrayPlug` `target`, growing it safely. `output.define` calls the native `GafferScene.Outputs.addOutput()` with `target`, `name`, `file_name`, `driver`, `channels`, and optional scalar `parameters`.

Launch a configured `GafferScene.Render` with a separate intent containing only `{ "op": "render.start", "target": "...", "name": "..." }`. Do not add `expect` to render intents. The result returns a job ID immediately while `LocalDispatcher` renders out of process. Poll with `gaffer_context` and `job: { "id": "job:...", "after": 0 }` until `state` is terminal. A successful result includes output existence, dimensions, channels, hash, min/max/average statistics, and a PNG preview attached directly for visual feedback. Cancel with a separate `render.cancel` intent whose `target` is the job ID.

Scalar, numeric-vector and string-vector plug values are supported. `ui.view` focuses a node and may select a registered SceneView renderer with `renderer`. For an interactive GPU-only Cycles viewport, use `{ "op": "ui.view", "target": "...", "renderer": "Cycles", "parameters": { "device": "OPTIX:*" } }`. The host verifies that an OptiX device exists, sets `renderer.cycles.device` before enabling Cycles, and does not include CPU.

Use the exact operation fields from the tool schema: `target` for `plug.set_value`, `node.rename`, `graph.delete`, and `ui.view`; `source` plus `destination` for `graph.connect`. Postconditions use `{ "subject": "...", "predicate": "exists|name_equals|value_equals|connected_to", "value": ... }`. Do not substitute `plug`, `node`, `ref`, or `kind` for these fields.

Use opaque refs returned by `gaffer_context`. A `node.create` operation may set `bind` to a simple identifier, and later operations in the same intent may reference the new node as `$identifier` or a descendant such as `$identifier.plugName`. Returned opaque refs also support descendants, for example `node:opaque.options.render:resolution.value`.

For simple nodes, `node.create` accepts a canonical `Namespace::Type`. For parameterised or plugin nodes, query `gaffer_context` with `recipe_match` and use the returned recipe `id` in the operation's `recipe` field. These recipes are discovered from the live Gaffer `NodeMenu`, so renderer plugins and shader/light variants match the current installation.

Request `include: ["capabilities"]` only when operation or node-type discovery is needed. Request `include: ["recipes"]` or supply `recipe_match` only for native creation discovery; ordinary graph reads omit the full catalogues to keep responses compact.

Gaffer may sanitise a requested node name or append a numeric suffix on collision. Read the effective `name`, `path`, and `ref` from the `created` result instead of assuming the requested name was retained.

Do not invent refs or switch scripts implicitly.
