---

name: houdini-pipeline
description: Revision-checked Houdini graph work through the attached OpenCode Link host.
autoinvoke: false
---

# Houdini pipeline

List `houdini_hosts` and use `houdini_attach` with permission when the user wants to attach an offered application. Never infer an active scene.

Read `houdini_context` for the current `script_handle`, revision and narrow working set (`refs`, `match`, `radius`, `include`, `limit`). The initial read can omit the handle. Subsequent calls use the returned handle.

Use `houdini_apply` with the exact revision, a fresh `command_id`, and 1–50 operations: `node.create`, `node.rename`, `parm.set_value`, `graph.connect`, `graph.delete`. Each operation is preflighted; parameter expressions and arbitrary code are refused. Read the returned affordances and node-type registry before constructing a graph. Aliases from `bind` are referenced with `$name`.

Start with `dry_run: true`. Mutation requires `dry_run: false` and `confirm: true`, under `houdini_write` permission. Expectations are preconditions, checked before mutation. Inspect the resulting revision with `houdini_context`. An interrupted request may still finish: read first and retry the identical command ID instead of making a new one.

An apply groups undo and attempts rollback on failure; failure to restore quarantines writes until the HIP is reloaded. Headless mutations are refused. Code execution, rendering, saving files and network imports are outside the supported operation set.
