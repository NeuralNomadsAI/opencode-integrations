# OpenCode integrations — specification, draft 0.1

Status: **draft 0.1** (`spec: 0`). Normative. Companion documents: [`rpc-contract.md`](rpc-contract.md) (RPC status contract) and [`host-handshake.md`](host-handshake.md) (application part, handshake, attachment, undo). JSON Schemas: [`schemas/`](schemas/).

The key words **MUST**, **MUST NOT**, **SHOULD** and **MAY** are to be read as in RFC 2119. Every requirement has an identifier (`FAM-1`, `PKG-3`…); [§10](#10-conformance) maps each one to its check.

## 1. Definition

- **DEF-1** An **OpenCode integration** is one self-contained OpenCode V2 plugin, published as one npm package, that connects OpenCode to a third-party application or service and conforms to this specification.
- **DEF-2** An integration MAY have an **application part** (host part): code installed inside the application (add-on, package, startup script, panel). The application part ships inside the npm package, in the package's version, and is installed separately, by an explicit action only ([HST-1](host-handshake.md#1-delivery-and-installation)).
- **DEF-3** An integration MUST work in every OpenCode client (terminal UI, desktop app, CodeNomad) and MUST NOT depend on a particular client.

## 2. Families

The marker's `family` names one of three families.

| `family`     | Principle                                                                                                                                                                                                                                                                                                                               | Tools                                                                                                                                               | Application part              |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| `native`     | An **authenticated loopback host** inside the application owns the document state (script handle, revisions, preflight, undo, verification); the plugin exposes bounded intents only. The conversation happens in an application panel that is itself an OpenCode client, or in any other client by **attaching** the open application. | `<app>_context` (bounded read), `<app>_apply` (atomic, revision-checked intent), and host-less tools (`<app>_docs`, `<app>_hosts`, `<app>_attach`…) | Yes: the host, optional panel |
| `vendor-mcp` | The plugin **declares** the vendor's official MCP server (never redistributes it), checks its preconditions and adds tested skills, workflows and scripts.                                                                                                                                                                              | The vendor's MCP tools, and optional plugin tools                                                                                                   | No: it belongs to the vendor  |
| `api`        | The plugin calls a remote API directly, without a third-party process or local application.                                                                                                                                                                                                                                             | Typed `<app>_*` tools                                                                                                                               | No                            |

### 2.1 `native`

- **FAM-1** A `native` integration MUST NOT declare an MCP server and MUST NOT expose public arbitrary code execution; the only exception is the `dangerous` method of [SEC-3](#9-security).
- **FAM-2** A `native` integration MUST be installed and enabled in the **Global** OpenCode configuration only, never in a Project configuration alone. A Project copy of a different version would be a second module with a second offer registry ([HST-12](host-handshake.md#4-advertisement-and-attachment)).
- **FAM-3** Every mutation MUST go through `<app>_apply`, which requires `script_handle`, the exact `if_revision`, an idempotent `command_id` (replaying it applies nothing), at most 50 operations, a preflight with the `expect` clauses checked before execution, an execution followed by a verification, and a declared undo level ([UNDO-1](host-handshake.md#6-undo-levels)).
- **FAM-4** Images MUST come back as attachments (`{ type: "file", uri, mime }`), never as base64 in text nor as a temporary path.
- **FAM-5** A `native` integration MUST offer an attachment path in every client: the tools `<app>_hosts` and `<app>_attach`, under an OpenCode permission set to "ask" by default, so that the model never attaches by itself.

### 2.2 `vendor-mcp`

- **FAM-6** The plugin MUST declare the vendor server without overwriting a user entry: it tests `editor.get(id)` before adding it. It MUST NOT bundle or redistribute the vendor server.
- **FAM-7** When the vendor server executes arbitrary code, its execution tools MUST fall under a named OpenCode permission set to "ask" by default, and the README MUST say so.

### 2.3 `api`

- **FAM-8** An `api` integration MUST NOT hard-code an application (client) or tenant identifier; they come from configuration.
- **FAM-9** Secrets MUST be stored outside the watched OpenCode configuration ([SEC-4](#9-security)).

## 3. Package content

```text
@neuralnomads/opencode-<app>/
  package.json        "type": "module", "exports" → dist/, "files",
                      optional peer "@opencode/plugin": "^2.2.0 || ^0.0.0-dev",
                      marker "opencodeIntegration" (§4), npm keyword "opencode-integration"
  dist/               compiled ESM JavaScript (+ .d.ts): Plugin.define({ id: "neuralnomads.<app>", setup })
  methods/**.json     method or operation manifests, the source of truth of tools, when useful
  skills/<app>-<topic>/SKILL.md   front-matter header (name, description), references, scripts
  data/<set>/         reference data, each with SOURCE.json (origin, licence, version)
  host/<app>/         embedded application part, same version as the package; archive + sha256
  README.md           universal installation first, then "In CodeNomad"; non-affiliation notice
  LICENSE
```

- **PKG-1** The package MUST be published as compiled JavaScript, never raw `.ts`. `@opencode/plugin` MUST be a peer dependency with the range `^2.2.0 || ^0.0.0-dev`, declared **optional** in `peerDependenciesMeta` (the OpenCode binary provides the SDK; a required peer installs about 130 MB), and pinned as a dev dependency to the qualification version (2.2.5). The package MUST NOT bundle or depend at runtime on the SDK or `effect`, and MUST NOT depend on `@opencode-ai/plugin`.
- **PKG-2** The plugin MUST load its skills itself (`ctx.skill.transform`) without overwriting a user skill of the same name. Reference skills SHOULD be `autoinvoke: false` with a short description. Skills MUST NOT mention `~/.config/opencode/…` paths.
- **PKG-3** Each `SKILL.md` MUST start with a front-matter header whose `name` equals its folder and that has a `description`.
- **PKG-4** Settings come from the `options` of the plugin entry (`{ package, options }`); the environment variables `NEURALNOMADS_<APP>_*` are a fallback only.
- **PKG-5** The plugin MUST NOT write into the OpenCode configuration directories (`$XDG_CONFIG_HOME/opencode`, `~/.config/opencode`) nor into its own package folder (the npm cache). State goes to `ctx.storage`; long-lived files go to the integration's own state folder: `$XDG_STATE_HOME/neuralnomads/opencode-<app>/`, or `%LOCALAPPDATA%\NeuralNomads\opencode-<app>\` on Windows.
- **PKG-6** Loading MUST have no side effect: no installation of the application part, no download, no launch of the application or of the OpenCode daemon.
- **PKG-7** The plugin MUST register the RPC status contract under its RPC ID `neuralnomads.<app>` ([rpc-contract.md](rpc-contract.md)).
- **PKG-8** The package MUST NOT declare npm install scripts (`preinstall`, `install`, `postinstall`, `prepare`); OpenCode disables them.
- **PKG-9** Integrations of this repository bundle `packages/integration-kit` into their build, so that two integrations loaded in one daemon cannot diverge. No `_runtime/` copy of shared code exists.

## 4. Static marker

The `opencodeIntegration` key of `package.json`, readable without executing the plugin and copied by the catalog. Its schema is [`schemas/marker.schema.json`](schemas/marker.schema.json).

```json
"opencodeIntegration": {
  "spec": 0,
  "id": "blender",
  "displayName": "OpenCode for Blender",
  "family": "native",
  "plugin": "neuralnomads.blender",
  "rpc": "neuralnomads.blender",
  "host": {
    "app": "blender",
    "protocol": 1,
    "range": "^0.1.0",
    "appVersions": ">=5.2",
    "payload": "host/blender/opencode_link-0.1.0.zip",
    "sha256": "<sha256 of the payload>"
  }
}
```

- **MRK-1** Every integration package MUST carry the marker, valid against the marker schema.
- **MRK-2** `id` MUST equal the `<app>` of the package name `@neuralnomads/opencode-<app>`; `plugin` and `rpc` MUST both equal `neuralnomads.<id>`.
- **MRK-3** `host` MUST be present for `native` and absent for `vendor-mcp` and `api`. `vendor-mcp` MAY add `vendor: { server, minAppVersion }`, without command or path.
- **MRK-4** `host.payload` MUST exist in the package and its sha256 MUST equal `host.sha256`; `host.range` MUST contain the package version (the version of the embedded application part).
- **MRK-5** No two integrations of the repository MAY share a `plugin` or an `rpc` ID.
- **MRK-6** The active plugin MUST return the marker's `id`, `family` and `spec` in `status` ([RPC-2](rpc-contract.md#2-status)); a client marks a mismatch as non-conforming.

## 5. RPC status contract

Defined in [`rpc-contract.md`](rpc-contract.md) (`RPC-*` requirements) with the schema [`schemas/rpc.schema.json`](schemas/rpc.schema.json): `status` for every family; `installHost`, `hosts`, `attachHost`, `detachHost`, `advertise`, `withdraw` and the events `status_changed`, `hosts_changed` for `native`.

## 6. Application part

Defined in [`host-handshake.md`](host-handshake.md) (`HST-*` and `UNDO-*` requirements) with the schema [`schemas/host.schema.json`](schemas/host.schema.json): delivery and installation, the wire protocol and `hello`, the derivation of `host.state`, advertisement and attachment, discovery by headless hosts, and undo levels with the rollback model.

## 7. Naming

| Element                   | Rule                                               | Example                                     |
| ------------------------- | -------------------------------------------------- | ------------------------------------------- |
| Repository                | `NeuralNomadsAI/opencode-integrations`             | —                                           |
| npm package               | `@neuralnomads/opencode-<app>`                     | `@neuralnomads/opencode-blender`            |
| Plugin ID and RPC ID      | `neuralnomads.<app>`                               | `neuralnomads.blender`                      |
| Tools                     | `<app>_context`, `<app>_apply`, `<app>_<verb>`     | `blender_hosts`, `m365_mail_search`         |
| Skills                    | `<app>-<topic>`                                    | `blender-geometry-nodes`                    |
| Display name              | "OpenCode for &lt;App&gt;"                         | "OpenCode for Blender"                      |
| Application part          | Its own name, without the vendor name first        | Blender add-on "OpenCode Link" (proposed)   |
| State folder              | `neuralnomads/opencode-<app>`                      | —                                           |
| Environment fallback      | `NEURALNOMADS_<APP>_*`                             | `NEURALNOMADS_BLENDER_*`                    |
| Offer registry key        | `Symbol.for("neuralnomads.<app>.offers")`          | `Symbol.for("neuralnomads.blender.offers")` |
| Code-execution permission | `<app>_execute_code`                               | `blender_execute_code`                      |
| Release tag               | `<app>-v<semver>`; catalog releases `catalog-v<n>` | `blender-v0.1.0`, `catalog-v1`              |

- **NAM-1** Packages, IDs, tools, skills, display names and tags MUST follow the table above.
- **NAM-2** `<app>` matches `^[a-z][a-z0-9-]{0,31}$`.
- **NAM-3** The prefix `opencode.` and the OpenCode logo MUST NOT be used; vendor names appear only nominatively ("for Blender"), never first in a name.

## 8. Versioning

- **VER-1** The specification carries the integer `spec`: **0 for the whole 0.x draft**, 1 at the 1.0 freeze. The RPC contract carries the integer `contract` (1).
- **VER-2** During the draft, an incompatible change does not increment `spec`: it increments the draft's minor version (0.1 → 0.2); packages with status `preview` follow; the catalog and the conformance package name the draft they validate. This document is draft **0.1**.
- **VER-3** After 1.0, an added field is compatible; a removed field or a changed meaning increments the integer. Readers MUST ignore unknown fields of outputs.
- **VER-4** Packages stay `0.x` until 1.0. Clients write **ranges** (`@^0.1`, later `@^1`), never an exact version.
- **VER-5** The specification stays a draft until each family has shipped an integration (Blender and Gaffer/Houdini for `native`, DaVinci Resolve for `vendor-mcp`, Microsoft 365 for `api`), then freezes as 1.0.

## 9. Security

- **SEC-1** **Authenticated hosts**: [HST-6 to HST-9](host-handshake.md#3-authentication). No fixed port, no listener outside loopback, no token on disk, in a log or in a client response.
- **SEC-2** **No public arbitrary code execution.** No tool, host method or RPC executes code supplied by the model or a client, except the method of SEC-3. A host exposes a fixed table of methods; the plugin sends `{ method, args }` JSON only. A host never imports code from the daemon's npm cache.
- **SEC-3** **The `dangerous` method.** A `native` integration MAY declare one method that executes supplied code, under all of these conditions:
  1. it is named and declared in the method manifests with `"dangerous": true`, as the tool `<app>_execute_code`, with `"risk": "destructive"`;
  2. it is **absent by default**: the application preference defaults to `false` **and** the plugin option `allowCodeExecution` defaults to `false`; while either is false, the host method is absent from `hello.capabilities.methods`, the plugin does not register the tool, and a call is refused with `method_disabled` without executing anything;
  3. when both are true, **every call** goes through the OpenCode permission `<app>_execute_code`, set to "ask" by default; a refusal executes nothing;
  4. the README and `SECURITY.md` announce it.

  Any other method whose name suggests code execution (`execute_code`, `exec`, `eval`, `run_code`, `run_python`…) is non-conforming. For `vendor-mcp`, the vendor's execution tools fall under [FAM-7](#22-vendor-mcp).
- **SEC-4** **Secrets outside the watched configuration.** No token, password or OAuth secret under `~/.config/opencode`. Order of preference: the OpenCode credential store (`ctx.integration`) when it fits; else the OS keychain; else the integration's state folder with `0600` permissions. A one-time migration moves an older file, then deletes it.
- **SEC-5** No hard-coded organization identifier: no Entra client or tenant ID, studio path or network share.
- **SEC-6** No tool argument that bypasses configuration (for example free `client_id`/`tenant_id`), unless documented and justified.
- **SEC-7** **Named permissions** guard sensitive actions: launching executables, installing the application part, attaching a session, network access by a host method (declared in `hello.capabilities.operations[].network`).
- **SEC-8** **Supply chain**: npm publication with OIDC provenance and no long-lived token, after a manual approval; attested host archives; no npm install script ([PKG-8](#3-package-content)).
- **SEC-9** **Trademarks**: every README carries a non-affiliation notice ([NAM-3](#7-naming)).

## 10. Conformance

`packages/conformance` implements the checks; `node packages/conformance/src/cli.ts` runs the repository checks and loads every built integration package. It reports the draft it validates (`draft 0.1`).

| Requirement                              | Check                                                                                                                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MRK-1 to MRK-5, NAM-1, NAM-2             | `checkRepository`: marker schema, IDs from the package name, payload digest, host range, unique plugin and RPC IDs                                                        |
| PKG-1, PKG-3, PKG-8, PKG-9               | `checkRepository`: packaging, skill headers, no install script, no `_runtime/`, no legacy SDK                                                                             |
| PKG-5, PKG-6, PKG-7, MRK-6, RPC-1, RPC-2 | `checkPluginLoad`: isolated load (fresh HOME and XDG), no write into the configuration or the package, RPC registered under the marker's ID, `status` equal to the marker |
| SEC-3, FAM-1                             | `checkDangerousManifests` (static) and `checkCodeExecutionGate` (four settings combinations, permission answers)                                                          |
| SEC-1, HST-6 to HST-9                    | `checkHostListener`: 127.0.0.1 only, unreachable on other interfaces, no answer without the exact token, valid `hello`                                                    |
| RPC-3 to RPC-7, HST-10 to HST-17         | `runSentinelScenario`: a fake host advertises sentinel values; no output except `hello` nor any event contains one; outputs strict                                        |
| VER-1, VER-2                             | Schemas (`spec: 0`), `SPEC_DRAFT`                                                                                                                                         |
| Catalog                                  | `checkCatalogBytes` and the catalog's equality with the markers                                                                                                           |

Requirements without an automated check (FAM-3 to FAM-9, PKG-2, PKG-4, SEC-4 to SEC-9, UNDO-*) are verified by review and by each integration's own tests.
