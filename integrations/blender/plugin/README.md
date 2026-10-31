# OpenCode for Blender

`@neuralnomads/opencode-blender` lets OpenCode read and change the scene open in Blender 5.2 or later. It is a `native` integration: the **OpenCode Link** add-on runs an authenticated host inside Blender, and the plugin sends it bounded, revision-checked intents. Nothing runs in Blender until you offer the session to OpenCode.

> **Status: preview (draft 0.1).** Not published to npm yet.

## Installation

### Any OpenCode client

1. Add the plugin to the `plugins` list of your **global** OpenCode configuration (`~/.config/opencode/opencode.json`). Native integrations are installed in the global configuration only.

   ```jsonc
   {
     "plugins": ["@neuralnomads/opencode-blender@^0.1"]
   }
   ```

   With options (all optional, see [Options](#options)):

   ```jsonc
   {
     "plugins": [{ "package": "@neuralnomads/opencode-blender@^0.1", "options": { "hostInspectionDir": "/path/to/extensions/user_default" } }]
   }
   ```

2. Install the add-on: ask OpenCode to run `blender_install_host` (it asks for permission), or install `host/blender/opencode_link-<version>.zip` from the package with Blender's **Preferences ▸ Get Extensions ▸ Install from Disk**. The add-on is installed on the computer that runs OpenCode, in the user extensions folder of the newest Blender 5.2 or later started there.
3. In Blender, enable **OpenCode Link** (Preferences ▸ Add-ons), then tick **Offer this session to OpenCode** (3D Viewport sidebar ▸ _OpenCode_, or the add-on preferences). The setting is off at every start.
4. In OpenCode, call `blender_hosts`, then `blender_attach` with the offer: both ask for permission. The conversation's Blender tools then act on that document.

### In CodeNomad

Open the **Integrations** extension: it lists OpenCode for Blender, shows whether the add-on is installed and up to date, installs it after a confirmation, and attaches the open Blender to the current conversation. **Status ▸ Plugins ▸ Add** also accepts the package name.

## How it works

- **Discovery and consent.** While "Offer this session to OpenCode" is ticked, the add-on listens on `127.0.0.1` on a random port with a random 256-bit token kept in memory, and offers the session to every OpenCode service registered for your user (or to one, see the add-on setting _OpenCode service_). Offers last 15 seconds and are renewed every 5 seconds; unticking the setting withdraws them and closes the listener.
- **Tools.** `blender_context` reads the document (script handle, revision, mode, and named reads); `blender_apply` applies up to 50 operations with the exact revision and a new `command_id`, after checking its `expect` clauses; `blender_catalog` lists the 30 reads and 49 operations; `blender_docs` searches the Blender API reference and manual (no Blender needed); `blender_audit` summarizes a `.blend` file with a separate background Blender; `blender_status`, `blender_hosts`, `blender_attach`, `blender_detach`, `blender_install_host`.
- **Undo.** Each apply is **one** undo step named "OpenCode: …". A failed apply is rolled back to the step before it; when the rollback itself fails, the add-on quarantines itself and refuses changes until you press **Reset OpenCode Link**.
- **Images.** Viewport, node editor and shader editor captures come back as image attachments. They need the Blender interface.
- **Skills.** `blender-scene`, `blender-geometry-nodes`, `blender-shader-editor`, `blender-asset-browser`, `blender-animation`, `blender-shot-manager`, `blender-docs`, `blender-audit`. A skill of yours with the same name wins.

[`METHODS.md`](METHODS.md) maps every method of the earlier Blender bundle to its read, operation or tool.

## Options

| Option (plugin entry) | Environment fallback                        | Default                                                   | Meaning                                                                      |
| --------------------- | ------------------------------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `hostInspectionDir`   | `NEURALNOMADS_BLENDER_HOST_INSPECTION_DIR`  | the newest Blender ≥ 5.2 `extensions/user_default` folder | Blender extensions folder that `status` inspects and `installHost` writes to |
| `blenderPath`         | `NEURALNOMADS_BLENDER_BIN`                  | `blender` on the `PATH`                                   | Executable used by `blender_audit`                                           |
| `requestTimeoutMs`    | `NEURALNOMADS_BLENDER_REQUEST_TIMEOUT_MS`   | `30000`                                                   | Limit of one request to Blender                                              |
| `allowCodeExecution`  | `NEURALNOMADS_BLENDER_ALLOW_CODE_EXECUTION` | `false`                                                   | See [Code execution](#code-execution)                                        |

Add-on settings: **Offer this session to OpenCode** (off at every start), **OpenCode service** (empty: every service found; else the name of one, the `<name>` of `service-<name>.json`, or `latest`, `dev`, `beta`, `next` for the shared registration), **OpenCode configuration folder** (only for a service started with `OPENCODE_CONFIG_DIR`: its global configuration folder; the add-on tries the default folder first), **Allow code execution** (off).

## Permissions

| Permission             | Asked by                                                                                |
| ---------------------- | --------------------------------------------------------------------------------------- |
| `blender_attach`       | `blender_hosts`, `blender_attach`: the model never attaches a Blender session by itself |
| `blender_network`      | `blender_apply` with an operation that reaches the network (BlenderKit, Sketchfab)      |
| `blender_install_host` | `blender_install_host`                                                                  |
| `blender_audit`        | `blender_audit` actions that launch Blender                                             |
| `blender_execute_code` | every `blender_execute_code` call                                                       |

All are "ask" unless your OpenCode permission rules say otherwise.

## Code execution

`blender_execute_code` runs Python code in the attached Blender. It is **dangerous** and **absent by default**: it appears only when the add-on preference **Allow code execution** and the plugin option `allowCodeExecution` are both on, and every call asks the `blender_execute_code` permission; a refusal runs nothing. Prefer `blender_apply` operations. See [SECURITY.md](../../../SECURITY.md).

## Blender limits

- Direct edits of `bpy.data` create no undo step, while each `bpy.ops` operator flagged UNDO pushes its own. To get one step, the add-on runs the mutations through `bpy.data`, then a single `bpy.ops.ed.undo_push`, with a window context in the interface; this works in interface and background modes.
- The Python API does not merge existing steps. Operations that would call undo-pushing operators are written with `bpy.data` (primitives use `bmesh`); operations that cannot be grouped (renders, downloads, vrscene conversion, preference changes) are declared not undoable and must be alone in their apply.
- The rollback undoes once to a barrier step pushed before the apply. It is fragile: it needs a window context, invalidates Python references, and you can act during the call; hence the quarantine on failure.
- Edit mode has its own undo stack: changes outside Object mode are refused (`mode_conflict`).
- A new step clears the redo stack, as usual. Repeated applies consume Blender's undo limit (32 steps by default), and each memfile step costs in proportion to the scene.
- Timers do not run during a modal operator or a blocking render: the add-on answers `busy` after 10 seconds instead of waiting.

## Earlier installation

If an earlier Blender bundle is installed, `status` reports `legacy_install` (a `plugins/blender.ts` shim in the global OpenCode configuration, or the earlier bridge answering on its fixed local port). Both versions would load together. Stop it:

1. Remove the shim `plugins/blender.ts` from your global OpenCode configuration folder, and the copied Blender skills from its `skills` folder.
2. In Blender, disable and remove the earlier bridge add-on.
3. Restart OpenCode and Blender, then install OpenCode for Blender as above.

## Development

```sh
npm run build:host --workspace @neuralnomads/opencode-blender   # catalog, vendored discovery client, payload, marker digest
npm run build --workspace @neuralnomads/opencode-blender        # dist/ (fails when the host artifacts are stale)
```

Tests live in [`../test`](../test): `BLENDER_TEST_BIN` runs the host tests in a background Blender 5.2; `OPENCODE_TEST_BIN` (OpenCode 2.2.5) with it runs the end-to-end test. Interface checks follow [`../test/MANUAL.md`](../test/MANUAL.md).

## Licence and trademarks

The plugin is MIT. The embedded **OpenCode Link** add-on is GPL-3.0-or-later, like Blender add-ons; the bundled documentation keeps its own licence ([`data/blender_docs/SOURCE.json`](data/blender_docs/SOURCE.json)).

Blender is a trademark of the Blender Foundation. This project is not affiliated with, endorsed by or sponsored by the Blender Foundation or OpenCode.
