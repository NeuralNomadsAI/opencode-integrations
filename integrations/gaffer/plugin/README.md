# OpenCode for Gaffer

Inspect and edit an open Gaffer graph through an authenticated loopback host. Works in every OpenCode client, including the terminal UI and CodeNomad. The optional Gaffer panel remains an OpenCode client. Preview, draft 0.2; Gaffer >=1.6.

## Install

Add the plugin to the **Global** OpenCode `plugins` list:

```jsonc
{
  "plugins": ["github:NeuralNomadsAI/opencode-integrations#main::path:integrations/gaffer/plugin"]
}
```

The npm package name is `@neuralnomads/opencode-gaffer`; npm publication is a separate maintainer-approved action. Build a Git checkout with `npm ci && npm run build` before loading its local plugin directory.

Install the embedded application part explicitly with the permission-gated `gaffer_install_host` tool (or RPC `installHost` after client confirmation). It verifies the bundled ZIP's SHA-256, copies it into the integration's content-addressed state directory, and adds its managed loader to `~/gaffer/startup/gui/neuralnomadsOpenCode.py`. Set plugin option `hostStartupDir` (environment fallback `NEURALNOMADS_GAFFER_HOST_STARTUP_DIR`) for a different native Gaffer GUI startup directory. Existing unmanaged files are refused. Restart Gaffer to load the application part. To remove it, delete only that managed loader; content-addressed payloads can be retained offline.

The state directory is `$XDG_STATE_HOME/neuralnomads/opencode-gaffer` (default `~/.local/state/neuralnomads/opencode-gaffer`), or `%LOCALAPPDATA%/NeuralNomads/opencode-gaffer` on Windows. No file references the npm cache. The host and plugin share the package version and MIT licence. Gaffer's BSD-licensed APIs are used without redistributing its source.

## Connect

Start OpenCode yourself. Open the **OpenCode for Gaffer** editor in Gaffer and press **Connect** to offer its script. **Disconnect** or closing the panel withdraws the offer. The authenticated host listens only on `127.0.0.1` at an ephemeral port; its 256-bit process token stays in memory. Offers expire after 15 seconds and renew every 5 seconds. No RPC output or event contains credentials, the port or the script handle. An application closed without inspection reports host state `unknown`.

The panel requires **Bun >=1.3 on PATH** (or `NEURALNOMADS_GAFFER_BUN` pointing to its executable). It runs the **compiled, self-contained `sidecar.js` shipped inside the host payload**, beside the Python host, rather than TypeScript in the npm cache. `NEURALNOMADS_GAFFER_SIDECAR` can explicitly override that path. The sidecar discovers a running service and never launches a daemon. The matching `@opencode/client` 2.2.5 is bundled into it. If your daemon uses a custom `OPENCODE_CONFIG_DIR`, set `NEURALNOMADS_GAFFER_GLOBAL_CONFIG_DIR` in Gaffer's environment to that daemon's Global configuration directory.

From another client, use `gaffer_hosts` and `gaffer_attach` with the offered opaque ID. Both require the named `gaffer_attach` permission. `gaffer_context` returns bounded reads and the current revision; `gaffer_apply` uses that exact revision, a unique command ID, 1–50 operations, native preflight, verification and transactional undo. Render and Viewer actions are separate side-effecting intents. There is no arbitrary-code tool. Detached conversations require an explicit reconnect; polling only renews offers, never silently reattaches them.

Service discovery validates `service.json` under `$XDG_STATE_HOME/opencode` (default `~/.local/state/opencode`) before authentication: literal HTTP `127.0.0.1` or `::1`, with matching PID and version from `/api/info`. Probes and subsequent requests bypass proxies and refuse redirects. Each Render or Viewer intent contains one operation and no postconditions; its result declares `side_effect: true` and `undoable: false`. Render previews from individual jobs and job lists are returned as image attachments.

## In CodeNomad

Install the Global plugin in Plugins, install its application part after confirmation, then select the open script in **Status › Gaffer** and attach the displayed conversation. Detach affects that conversation only. The in-app panel can also keep its own conversation attached.

## Build and checks

`npm run build:host --workspace @neuralnomads/opencode-gaffer` deterministically compiles the sidecar, archives the Python host and panel, and refreshes the marker digest. `npm run build` verifies the archive before compiling the plugin. The generic protected publication workflow resolves `gaffer`, attests its archive and supports trusted publishing; it must be approved separately.

Run `node --test integrations/gaffer/test/*.test.ts`. Set `GAFFER_TEST_BIN` to Gaffer's launcher to exercise the real installed application in a private HOME/TEMP; the normal suite tests the actual authenticated host listener under a stub application as well as the plugin's RPC handlers. See [test/MANUAL.md](../test/MANUAL.md).
