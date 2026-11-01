# Application part, handshake and attachment — draft 0.2

Normative part of [`integrations.md`](integrations.md) for the `native` family; the key words MUST, MUST NOT, SHOULD and MAY are to be read as in RFC 2119. Schema of the wire protocol and of `hello`: [`schemas/host.schema.json`](schemas/host.schema.json). The plugin side is implemented by `packages/integration-kit` (host client, `hello` checks, host state, offer registry); the reference discovery client of headless hosts is `packages/integration-kit/python/opencode_service.py`.

## 1. Delivery and installation

- **HST-1** The application part is **embedded** in the npm package (`host/<app>/…`), in the package's version, and usable offline. The same archive is attached to the GitHub release with a provenance attestation. It is installed **only by an explicit action**, never while loading: the RPC `installHost`, called by a client after its own confirmation, or a tool `<app>_install_host` under an "ask" permission. Installation writes to the application's native location, preferably through the application's official mechanism, on the machine of the OpenCode daemon. Blender MAY also be published in a remote extension repository.
- **HST-2** Application-side configuration that references a file of the package MUST point to a **stable path** outside the npm cache: the plugin copies the payload there, addressed by content. The application part SHOULD be self-contained.

## 2. Host state

### 2.1 `hello`

- **HST-25** The host MUST answer the `hello` method with the following fields, valid against `schemas/host.schema.json#/$defs/hello`:

| Field             | Rule                                                                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `protocol`        | Integer, strictly equal to the marker's `host.protocol`.                                                                                                                                          |
| `host_version`    | Semver read from the application part's own manifest, never written in code.                                                                                                                      |
| `app_version`     | The application's version (for Blender, `bpy.app.version_string`), display text.                                                                                                                  |
| `capabilities`    | `methods` (the fixed method table, ASCII names), `operations` (`name`, `undoable`, optional `network`), `undo` ([UNDO-1](#6-undo-levels)), `background` (the application runs without interface). |
| `capability_hash` | sha256 (hex) of the canonical JSON of `capabilities`: object keys sorted, no whitespace (Python: `json.dumps(value, sort_keys=True, separators=(",", ":"))`). CI checks it against the manifests. |
| `script_handle`   | Identity of the open document, regenerated when another document is loaded.                                                                                                                       |
| `revision`        | Integer revision of the document state.                                                                                                                                                           |
| `document`        | Optional display label of the document: a file name without folders.                                                                                                                              |

- **HST-3** The plugin MUST derive the compatibility of a hello as follows: `incompatible` when `protocol` differs, when `app_version` (compared by its leading version numbers) is outside the marker's `appVersions`, or when `host_version` is above `host.range`; `outdated` when `host_version` is below `host.range`; else `ok`. Version comparisons include prereleases: a prerelease inside the range matches it. The plugin reports the state in `status` and in tool errors, so that the agent explains the problem instead of failing.

### 2.2 Derivation of `status.host.state`

- **HST-4** **WSL**: the state MUST be `unsupported` when the daemon runs on Linux with `/proc/version` containing `microsoft` or with `WSL_DISTRO_NAME` set, unless an offer was already validated by hello (mirrored networking). `installHost` MUST always answer `refused` under WSL, including with a validated offer, because installation would target the Linux application. The validated offer remains available for status and attachment.
- **HST-5** The plugin MUST apply these rules **in this order**:
  1. `unsupported`: the WSL condition of HST-4, or a platform the integration does not support.
  2. `ok`, `outdated` or `incompatible`: **only** from a live hello, the hello of an offer validated and renewed within the last 15 seconds (HST-13). With several offers, the least favourable wins (`incompatible` > `outdated` > `ok`).
  3. `absent`: **only** after an explicit inspection of the application's native add-on folder, designated by a plugin option (`hostInspectionDir`, or the documented per-platform default when the application provides one), that does not find the application part. A manifest found outside `host.range` gives `outdated` or `incompatible` with `partVersion`, without an offer.
  4. `unknown`: every other case, notably a closed application without possible inspection, or an installed matching part without a live hello.

  The inspection MUST be read-only and bounded (one folder, at most one level deep, at most 1 MiB of manifest read) and MUST NOT return the path. `action` is `install` for `absent` and `update` for `outdated`, never for `unknown`.

## 3. Authentication

- **HST-6** The host MUST listen **only** on `127.0.0.1`, on an ephemeral port (`:0`). It MUST NOT listen on another address, `0.0.0.0` or `::` included.
- **HST-7** The host MUST require a random 256-bit token per process (64 lowercase hex characters), compared in constant time, on **every** request. A request without the exact token gets `unauthorized` (or the connection is closed) and nothing else.
- **HST-8** The token MUST exist only in memory, in the host and in the plugin. It MUST NOT be written to disk, logged, or sent to a client (CodeNomad included). OpenCode 2.2.5 does not log RPC inputs and its validation errors do not echo values; the plugin still validates `advertise` itself (RPC-8).
- **HST-9** The plugin MUST refuse any host address other than exactly `127.0.0.1`.

- **HST-24** **Wire protocol 1.** Plugin and host MUST use one request per TCP connection: the client writes one line of JSON, `{ "id", "token", "method", "params" }` (at most 1 MiB), and reads one line back: `{ "id", "ok": true, "result" }` or `{ "id", "ok": false, "error": { "code", "message"? } }` (at most 8 MiB). Error codes: `unauthorized`, `invalid_request`, `method_not_found`, `method_disabled`, `invalid_args`, `stale_handle`, `stale_revision`, `expectation_failed`, `mode_conflict`, `busy`, `quarantined`, `permission_denied`, `internal`. A host that cannot serve now (a modal operator, a blocking render) MUST answer `busy` without waiting indefinitely. A client MUST check the response `id` and treat an unknown error code as `internal`. The plugin sends `{ method, args }` of the fixed method table only ([SEC-2](integrations.md#9-security)).

## 4. Advertisement and attachment

- **HST-10** **Consent.** The host MUST NOT advertise anything until the user asks for it in the application (for Blender, an add-on setting "Offer this session to OpenCode", off by default). Revoking consent calls `withdraw` on every service, closes the listener and invalidates the token; the offer disappears and its binding breaks within 15 seconds even when `withdraw` is lost (lease expiry).
- **HST-11** **Validation.** The plugin MUST answer `refused` to any announcement whose `host` is not exactly `127.0.0.1`, whose fields leave `advertise.input`, whose `protocol` differs from the marker, whose label mentions the token, the offer key, the script handle or the port, or whose `hello` sent with the announced token fails: connection refused, token rejected, other protocol or script handle, response outside the schema or with a wrong capability hash, or no answer within 2 seconds. **Only an announcement validated by hello creates an offer**: a client that calls `advertise` without a real host obtains nothing. The answer MUST NOT reveal the cause of a refusal. The plugin MUST NOT copy into its outputs a hello `document` or `app_version` that mentions the token, the offer key, the script handle or the port.
- **HST-12** **Registry.** OpenCode instantiates the plugin per Location and evaluates a locally installed module once per Location, so a module variable is not shared. The offer registry MUST live on `globalThis[Symbol.for("neuralnomads.<app>.offers")]`, shared by every Location of the daemon process (a Global-only installation, [FAM-2](integrations.md#21-native), keeps one module version). The registry MUST receive its clock as a parameter, for tests.
- **HST-13** **Lease.** An offer MUST live 15 seconds; every `advertise` with the same `offer_key`, token, port and script handle renews it (`renewed`); the host MUST renew every 5 seconds. An announcement of the same `offer_key` with another token, port or script handle replaces the offer only after a successful hello; the replaced offer's bindings break.
- **HST-14** `withdraw` (with the offer's token) or expiry MUST remove the offer and break its bindings, and the plugin MUST emit `hosts_changed` and `status_changed`.
- **HST-15** A plugin MUST hold at most 16 offers; beyond, it answers `refused`.
- **HST-16** A client lists opaque offers (`hosts`) and calls `attachHost({ offerID, sessionID })`; the plugin MUST refuse (`busy`) a session already bound to another live host.
- **HST-17** **Hosts with a panel** (Gaffer, Houdini) MUST advertise from the panel's sidecar the same way.
- **HST-26** **Session ownership.** Binding reads and mutations MUST verify the exact requested session with a fresh native `ctx.session.get` at each call and again after asynchronous permission/hello waits ([RPC-12](rpc-contract.md#3-native-methods)). The Promise-native `Location.PublicRef` intentionally exposes `directory` without `workspaceID`: the exact validated directory is the public ownership contract. A context whose safe ownership decision requires an unavailable workspace distinction MUST fail closed (`unknown` for a read, refusal for a mutation). Implementations MUST NOT infer that distinction from a shared parent directory, project, cached session inventory, the Q8 probe, or a fabricated workspace ID. A binding follows the exact session ID across a native move: the old Location MUST refuse access, and the new Location MUST verify its fresh native session ownership before observing or using that binding. A move MUST NOT automatically detach the session.
- **HST-27** **Registry authority.** The shared registry MUST expose the current service generation, registry generation and revision of RPC-10. Every final binding observation or commit MUST synchronously verify that registry instance, its generations and revision, and the live selected offer after the last fresh ownership snapshot. A replaced registry or offer, a changed label, a revoked or expired offer, or changed binding MUST invalidate an earlier observation. Ownership snapshots have the concurrent native move/delete window described in RPC-12; the Q8 probe MUST NOT supply ownership or binding authority. Host `hello.capabilities.methods` MUST continue to declare the complete fixed host method table ([HST-25](#21-hello)); `bindingRead` belongs to the plugin RPC contract and leaves that inventory intact.

## 5. Discovery by headless hosts

A host without a panel (Blender) advertises to the shared OpenCode service itself.

- **HST-18** **Discovery** MUST read only the service registrations under `$XDG_STATE_HOME/opencode` (default `~/.local/state/opencode`): `service.json`, shared by the `latest`, `dev`, `beta` and `next` channels, and `service-<name>.json` for a service named `<name>`. A registration counts when its URL is loopback HTTP (`127.0.0.1` or `::1`), it names a `pid` and a `version`, and `/api/info` answers with the same `pid` and `version`. Requests MUST NOT go through a proxy nor follow a redirect: they carry the daemon password and the host token. Temporary files and invalid registrations are ignored.
- **HST-19** **Location.** `advertise` and `withdraw` MUST target the Location whose `directory` is the daemon's **global configuration directory** (`global.config`): `$XDG_CONFIG_HOME/opencode`, default `~/.config/opencode`, home resolved like Node's `os.homedir()`. It is the only Location that loads no project configuration. The directory MUST NOT be omitted: the service changes its working directory to the home folder and would then read `~/.opencode`. Nothing is written there.
- **HST-20** **`OPENCODE_CONFIG_DIR`.** A daemon started with this variable has a global configuration directory the host cannot deduce. The host MUST try the default directory first; when that Location answers `rpc.unavailable` (or `rpc.method_not_found`, or HTTP 404), it retries with the directory of an explicit add-on setting, when set.
- **HST-21** **Service selection.** By default the host MUST advertise to **every** discovered, healthy service: one offer per service. An optional add-on setting "OpenCode service" names one service: the suffix of `service-<name>.json`, or one of the shared channel names for `service.json`; empty means every service. An unreachable service, or a port held by a process that does not speak HTTP, MUST be skipped without blocking the others.
- **HST-22** The host MUST NOT start the daemon. Network calls MUST run off the application's main thread. Results, logs and error messages MUST NOT contain the daemon password or the host token.
- **HST-23** The host reads the daemon password from the registration with the user's rights; this gives it full access to the user's OpenCode service. It is a residual risk recorded in `SECURITY.md`. The discovery path relies on a semi-public service contract, qualified on the OpenCode version recommended by CodeNomad (2.2.5).

A pairing code shown in the application and typed in the client MAY later serve as a fallback when discovery fails.

## 6. Undo levels

- **UNDO-1** Every `native` host MUST declare `hello.capabilities.undo`:

  | Value                 | Promise                                                                                                                                                                            | Applications                                            |
  | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
  | `transactional`       | One `apply` is **one** undo step, with an automatic rollback on failure; the host quarantines itself when the rollback fails.                                                      | Gaffer; Houdini (to be confirmed)                       |
  | `grouped_best_effort` | One named step per `apply` ("OpenCode: …") and a restoration attempt on failure, following the rollback model below; quarantine when it fails. Limited to the declared conditions. | Blender, in interface and background modes, Object mode |
  | `none`                | No grouping and no rollback. Mutations are refused unless an operation declares otherwise.                                                                                         | Hosts that cannot group steps                           |

- **UNDO-2** **Rollback model B.** A `grouped_best_effort` host MUST run an `apply` as: push a **barrier** step; run the mutations; on success push one named step ("OpenCode: …"); on failure push a step named for the failure, then undo once, which returns to the barrier. **Model A** (undo without pushing the partial mutations first) undoes past the barrier, into the user's own steps, and is **forbidden**. When the restoration fails, the host MUST quarantine itself (`quarantined`) until the user reloads or resets it.
- **UNDO-3** Operations with a **side effect that cannot be undone** (render, file writes, network import, application preferences) MUST be declared (`undoable: false`), MUST be the only operation of their `apply`, and MUST be announced in the result.
- **UNDO-4** **Blender limits**, written as such in the Blender README:
  - Direct edits of `bpy.data` create no undo step, while each `bpy.ops` operator flagged UNDO pushes its own.
  - The Python API does not merge existing steps. To get one step, the host runs the mutations through `bpy.data`, then a single `bpy.ops.ed.undo_push`, which needs a window context (`temp_override`) from a timer. This works in interface and in background mode.
  - Handlers that call operators (`primitive_*_add`, asset or render operators) are rewritten in `bpy.data` or declared not groupable.
  - The rollback undoes to a barrier with `ed.undo` (model B). It is fragile: it needs a window context, invalidates Python references, and the user can act during the call; hence the quarantine on failure.
  - Edit mode has its own undo stack (bmesh): mutations outside Object mode are refused (`mode_conflict`).
  - A new step clears the redo stack, as usual.
  - Repeated `apply` calls consume the user's undo limit (32 steps by default).
  - Each memfile step costs in proportion to the scene.
  - Timers do not run during a modal operator or a blocking render: the host answers `busy` without waiting indefinitely.
