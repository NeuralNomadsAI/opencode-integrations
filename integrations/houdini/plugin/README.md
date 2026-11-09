# OpenCode for Houdini (preview)

Inspect and edit an open Houdini graph from any OpenCode V2 client, including the terminal, desktop and CodeNomad. The optional **OpenCode Link** Python panel keeps a conversation inside Houdini. Houdini 21+ with Python 3.11+ and PySide6, Node.js 22+ and OpenCode 2.2+ are required. This preview targets specification draft 0.2.

## Install in any OpenCode client

Add the integration to the **Global** OpenCode configuration only:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    {
      "package": "@neuralnomads/opencode-houdini@^0.2",
      "options": { "hostInspectionDir": "/your/Houdini/user/preferences" }
    }
  ]
}
```

Use the existing Houdini user preferences folder (`hou.getenv("HOUDINI_USER_PREF_DIR")`), for example `Documents/houdini21.0` on Windows, `~/houdini21.0` on Linux or `~/Library/Preferences/houdini/21.0` on macOS. Set `hostInspectionDir` explicitly; the integration never guesses a Houdini version or creates a preference directory. `NEURALNOMADS_HOUDINI_HOST_INSPECTION_DIR` is a fallback. Omit it to keep host state unknown until a live host advertises.

Call `houdini_install_host` and approve its named permission. A client can also invoke RPC `neuralnomads.houdini.installHost` after its explicit confirmation. Installation uses the embedded, checksummed archive offline: the stable, content-addressed payload goes under `%LOCALAPPDATA%/NeuralNomads/opencode-houdini/hosts/` on Windows, or `$XDG_STATE_HOME/neuralnomads/opencode-houdini/hosts/` (default `~/.local/state/…`) elsewhere. Houdini's `packages/neuralnomads_houdini.json` references this stable copy, never an npm cache path. Repeating installation is idempotent. WSL installation is refused.

Open Houdini and choose the **OpenCode Link** Python panel. The host starts only when you choose **Connect** (or **New**), never at plugin load, Houdini startup or panel creation. Node.js is found on PATH, with `NEURALNOMADS_HOUDINI_NODE` as an override. Start OpenCode yourself first: the panel discovers the existing authenticated shared service without starting one. Connect opens a new conversation and offers the host for other clients. Disconnecting or closing a panel closes its conversation channel and withdraws its offer. Python Panel teardown (`onDestroyInterface`), widget close and destruction share idempotent cleanup. The shared host stays available while another consenting panel remains. Disconnecting or closing the last consenting panel immediately closes the listener and invalidates its token; offers and bindings expire within 15 seconds even if withdrawal fails. Opening or clearing a HIP disconnects the panel. After service failure or explicit detachment, choose Connect again; polling never recreates a binding.

Outside the panel, use `houdini_hosts` then `houdini_attach` with permission. `houdini_context` can initially omit its handle to read the attached document's handle and revision. All mutations use `houdini_apply`, the exact revision, an idempotent command ID and 1–50 operations. Start with dry-run; changing the graph requires `dry_run: false`, `confirm: true` and `houdini_write` permission. `expect` contains preconditions checked before execution; operation results are verified afterwards. The `houdini-pipeline` reference skill is loaded by the plugin without replacing a user skill.

## In CodeNomad

Add the integration from the reviewed catalogue (preview), then use its Status section to install the application part, list user-offered hosts and attach the displayed conversation. The same status RPC contract supplies host versions, live offers and exact-session attachment authority. Installation runs on the OpenCode daemon's machine. Credentials, ports and document handles stay out of status outputs and events.

## Scope, safety and qualification

- Supported operations: `node.create`, `node.rename`, `parm.set_value` (numeric/boolean), `graph.connect`, `graph.delete`. Node creation uses the host's bounded type registry. No arbitrary code tool, parameter expressions, string writes, renders, file saving or network import operations are exposed. Existing scenes, node assets and their callbacks remain trusted application content.
- In UI mode, undo is `grouped_best_effort`: one undo group, operation verification, rollback attempt, quarantine on failed restoration. Headless mode declares `none` and refuses mutation. Actual HOM undo behaviour still needs licensed, interactive qualification.
- Command replay results are retained for the HIP lifetime. After 256 successful commands the host refuses new commands until the HIP is reloaded, preserving idempotence without eviction. Reusing a command ID for a different request is refused.
- The host binds only `127.0.0.1` on an ephemeral port, authenticates every request with a process-random 256-bit token and invalidates it on shutdown. Tokens exist only in memory and private pipes. The panel sidecar bundles its client dependencies inside the host archive; the plugin's compiled ESM keeps the OpenCode SDK external.
- System-Python tests exercise the actual listener and protocol with a stub HOM boundary, plus RPC sentinel, ownership, expiry, installation and refusal cases. Python Panel callback tests cover two consenting panels through teardown to one and zero owners, repeated teardown and widget-close fallback. Real Houdini Apprentice 22.0.429 qualifies the same sequence with isolated preferences and a private OpenCode service: offers and exact-session bindings are withdrawn, the final listener refuses connections and its old token is rejected.
- **QO5 — CI licence decision:** maintainers must choose whether to provide an already licensed self-hosted Houdini runner. Recommendation: keep public CI licence-free and the integration preview until a maintainer performs licensed read-only/interactive validation; add an opt-in self-hosted job only when credentials, licence terms and runner isolation are approved. Public CI must never download Houdini or attempt licence activation.

Publication uses the repository's manually approved OIDC workflow and `houdini-v<version>` tags. Building or installing never publishes anything.
