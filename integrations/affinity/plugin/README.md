# OpenCode for Affinity

Preview, spec draft 0.2. An independent MIT OpenCode V2 plugin for the MCP server embedded in
**Affinity by Canva 3.3+**. Affinity and Canva are trademarks of Canva Pty Ltd, used nominatively.
This project is unaffiliated with Canva or OpenCode. The server ships inside Affinity; this package
contains a client, tools and a skill, and redistributes nothing from Affinity.

## Why a plugin client

Affinity's embedded server speaks MCP over the **legacy HTTP+SSE transport** (`GET /sse`, then
`POST` to the announced endpoint). OpenCode's `remote` MCP entries use Streamable HTTP only, so a
plain `mcp.servers` entry pointing at Affinity fails with HTTP 404. Instead of declaring the server,
this plugin connects to it itself, on loopback only, with no extra process, bridge or dependency.

## Install in any OpenCode client

1. In Affinity, open **Settings → Scripts**, enable scripts and **Model Context Protocol**.
2. Add `@neuralnomads/opencode-affinity@^0.1` to `plugins` in your OpenCode configuration
   (until publication, a built local package directory).

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["@neuralnomads/opencode-affinity@^0.1"]
}
```

The plugin connects lazily, at the first tool call, to `http://[::1]:6767/sse`, then
`http://127.0.0.1:6767/sse`. Affinity may be started before or after OpenCode; a dropped connection
is reopened on the next call (a call in flight is never replayed). Options, which override the
environment fallback `NEURALNOMADS_AFFINITY_URL`:

| Option          | Default            | Meaning                                                     |
| --------------- | ------------------ | ----------------------------------------------------------- |
| `url`           | the two URLs above | Affinity's SSE URL; loopback `http` only.                   |
| `callTimeoutMs` | `120000`           | Maximum wait for one Affinity call (1 000 to 3 600 000 ms). |

Remove any hand-written `mcp.servers` entry for Affinity: its tools would duplicate these.

## Tools

| Tool                      | Effect                                                                                                                  |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `affinity_context`        | Read-only snapshot: open documents, revision, size, pages, current spread object tree (`ref` = `nodeId`) and selection. |
| `affinity_apply`          | Up to 50 typed operations on refs, checked first and applied as one undo step (below).                                  |
| `affinity_docs`           | SDK documentation: topic list, or one topic paged by `offset`.                                                          |
| `affinity_hints`          | Searches Affinity's shared SDK hint pool (the query is sent to Canva's service).                                        |
| `affinity_library`        | Lists or reads Affinity's script library.                                                                               |
| `affinity_render`         | Renders a spread or the selection; the JPEG comes back as an attachment.                                                |
| `affinity_execute_script` | Runs SDK JavaScript in Affinity. Asks permission.                                                                       |

The first render after a change made outside the plugin's connection (in Affinity's interface, or
by another client) can return the previous image; `affinity_render` therefore renders twice and
returns the second image.

`affinity_context` and `affinity_render` run fixed read-only scripts inside Affinity. The plugin
reads the SDK preamble once per connection, as Affinity requires before any script.

### Typed changes: `affinity_apply`

`affinity_apply` takes `operations` (1 to 50) and optional `document`, `if_revision`, `command_id`
and `expect`, and runs one fixed program inside Affinity. The plan is validated by the plugin, then
embedded as data; no plan string is evaluated as code.

1. **Preflight**: the document is found; `if_revision` must equal the current revision (from the
   document's undo history, `h<position>-<size>`); every `expect` clause (`{ ref, exists?, name?, type? }`)
   holds; every ref exists, is unlocked, and was not grouped, combined or deleted earlier in the plan.
2. **Build**: every command is built before the document is touched. Any error so far changes nothing.
3. **Execute**: one compound command, so the whole plan is one undo step.
4. **Verify**: the result lists created refs, changed objects (type, name, box), removed refs, the new
   revision and `undo: "one_step"` (or the observed number of history steps).

A `command_id` already applied by this plugin instance returns its first result with `replayed: true`
and applies nothing. When Affinity fails after execution started, the tool says the change may be
applied instead of claiming nothing changed.

| Operation                                 | Fields                                                                 |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `create_shape`                            | `shape`, `box {x, y, width, height}`, `fill?`                          |
| `create_text`                             | `text`, `position {x, y}` (baseline start), `size` (px), `colour?`     |
| `set_fill`                                | `refs`, `fill`                                                         |
| `set_stroke`                              | `refs`, `colour?` (or `"none"`), `width?`                              |
| `set_opacity`                             | `refs`, `opacity` (0 to 1)                                             |
| `move` / `set_box` / `rotate`             | `refs, dx, dy` / `ref, box` / `refs, degrees` (around each centre)     |
| `align`                                   | `refs`, `horizontal?`, `vertical?`, `to` (`selection` or `page`)       |
| `rename` / `set_visible` / `set_text`     | `refs, name` / `refs, visible` / `ref, text` (replaces the whole text) |
| `group` / `ungroup` / `delete` / `select` | `refs`                                                                 |
| `boolean`                                 | `refs` (2+), `mode` (`union`, `subtract`, `intersect`, `xor`)          |

`fill` is `"none"`, `{ colour: "#rrggbb[aa]" }` or `{ gradient: { stops: [{ colour, position }], angle?, type? } }`
(linear by default, `angle` 0 = left to right, 90 = top to bottom; `radial` from the centre). Gradients
are placed in the object's own frame, so they follow its rotation. Shapes: `rectangle`, `ellipse`,
`triangle`, `diamond`, `star`, `polygon`, `trapezoid`, `heart`, `cloud`, `cog`, `arrow`, `tear`,
`crescent`, `spiral`, `pie`, `segment`, `callout_rectangle`, `callout_ellipse`. Coordinates are spread
pixels. Refs of objects created by a plan are known only from its result.

The `affinity-workflows` skill covers inspection, one-undo-step changes, verification by render and
known SDK pitfalls. A user skill with the same ID is preserved.

## Permissions and safety

`affinity_execute_script` runs arbitrary JavaScript inside Affinity: it can change or delete document
content and, when Affinity's settings allow it, read and write files on the Desktop and use the
network. Every call asks the named **`affinity_execute_code`** permission (ask by default), showing
the script. A denial never runs it. Affinity's own Scripts permissions (filesystem, network, AI)
still apply on top. Approve only scripts you understand; save your documents first.

Affinity's tools that publish or persist on the user's behalf (`add_sdk_hint`, `report_sdk_issue`,
`save_script_to_library`) are not exposed.

## Status

The `neuralnomads.affinity` RPC implements `status` and `status_changed`. Reading status never opens
a connection: it is `connected` while the connection opened by a tool call is alive, `none` before
the first call or when Affinity is unreachable (closed, MCP disabled), and `error` after a protocol
failure. Opening or losing the connection emits `status_changed`. Status carries no URL, port or
error text.

## In CodeNomad

Add the package from the Plugins surface; the integrations catalog lists it as preview. Its status
section shows the connection state. There is no host part to install and no attachment step:
Affinity's own MCP switch is the consent.

## Qualification

Tested against Affinity 3.3.3.5004 on Windows: connection, preamble, context, documentation,
render, script execution, a clean shutdown, and `affinity_apply` with every operation family
(creation, gradients on created, moved and rotated objects, stroke, opacity, rotation, alignment,
rename, text replacement, grouping, ungrouping, visibility, resizing, boolean union, deletion,
selection), each plan as one undo step, plus refused expectations, stale revisions, unknown and
consumed refs, and command replay. The other boolean modes are covered by validation tests only.
macOS is unqualified.
The vendor tool names come from the server's live catalog of that version.
