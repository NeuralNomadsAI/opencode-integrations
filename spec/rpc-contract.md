# RPC status contract — contract 1, draft 0.2

Normative part of [`integrations.md`](integrations.md); the key words MUST, MUST NOT, SHOULD and MAY are to be read as in RFC 2119. Schema: [`schemas/rpc.schema.json`](schemas/rpc.schema.json), one definition per method input (`<method>.input`), output (`<method>.output`) and event (`<event>.event`).

## 1. Registration and detection

- **RPC-1** The plugin MUST register the contract with `ctx.rpc.register` under its RPC ID `neuralnomads.<app>` (the marker's `rpc`). `vendor-mcp` and `api` integrations register `status` and the event `status_changed`; `native` integrations register every method and both events below. The RPC ID is specific to the integration: OpenCode's RPC registry keeps the last registration of an ID, so no ID is shared by several integrations.
- **RPC-1a** Clients MUST detect support by capability, never by comparing versions: `rpc.unavailable` means the plugin is not active at that Location; `rpc.method_not_found` means the method is not supported.
- **RPC-1b** Readers MUST ignore unknown output fields and unknown `notices`, except for `bindingRead`, whose complete input and output MUST match its closed schema. Producers of this repository MUST emit only the fields defined here (the conformance package checks the strict form).

## 2. `status`

Required for every family. Input `{}`.

```ts
status({}) => {
  contract: 1,
  integration: { id: string, version: string, family: "native" | "vendor-mcp" | "api", spec: 0 },
  host?: {                          // native only
    state: "absent" | "incompatible" | "outdated" | "ok" | "unknown" | "unsupported",
    partVersion?: string,           // version of the application part found
    expectedRange?: string,         // the marker's host.range
    appVersion?: string,            // application version from a live hello
    action?: "install" | "update"
  },
  connection?: {                    // native: offers and bindings; vendor-mcp: server reachable; api: account
    state: "none" | "available" | "attached" | "connected" | "signed_out" | "error",
    offers?: number,                // 0..16
    attachedSessions?: number
  },
  notices?: Array<"legacy_install">  // closed list of contract 1; readers ignore other values
}
```

- **RPC-2** `integration.id`, `integration.family` and `integration.spec` MUST equal the marker; `integration.version` MUST be the package version. `host` MUST be present for `native` and absent otherwise. `host.state` MUST follow the derivation of [HST-5](host-handshake.md#2-host-state). `action` MUST be `install` only for `absent` and `update` only for `outdated`; `unknown` and `ok` MUST NOT propose an action (the client offers "Open the application").
- **RPC-2a** For `native`, `connection.state` MUST be `attached` when a session is bound, `available` when an offer exists, else `none`. `legacy_install` reports an earlier installation of the same integration found by a read-only probe (for example an old shim in the Global configuration).

## 3. Native methods

```ts
installHost({ target?: "default" }) => { outcome: "installed" | "updated" | "unchanged" | "refused" | "failed", detail?: string }
hosts({}) => { offers: Array<{ offerID: string, label: string, document?: string, appVersion?: string, protocol: "ok" | "incompatible" }> }
attachHost({ offerID: string, sessionID: string }) => { outcome: "attached" | "busy" | "gone" | "refused" }
detachHost({ sessionID: string }) => { outcome: "detached" | "none" }
bindingRead({ sessionID: string }) =>
  | { state: "attached", authority: Authority, binding: { offerID: string, label: string } }
  | { state: "none", authority: Authority }
  | { state: "unknown", authority?: Authority }

type Authority = { serviceGeneration: string, registryGeneration: string, revision: number }

// called by the application part, never by a client
advertise({ host: "127.0.0.1", port: number, token: string /* 64 hex */, script_handle: string,
            label: string /* ≤ 120 */, protocol: number, offer_key: string /* ≤ 64, stable per process */ })
  => { outcome: "accepted" | "renewed" | "refused" }
withdraw({ offer_key: string, token: string }) => { outcome: "withdrawn" | "none" }

// events: no payload; status_changed also invalidates bindingRead
status_changed {}
hosts_changed {}
```

- **RPC-3** A client MUST call `installHost` only after a confirmation that it displays; it runs on the machine of the OpenCode daemon and the confirmation MUST say so. The plugin MUST NOT call it while loading ([PKG-6](integrations.md#3-package-content)). When the daemon runs in WSL ([HST-4](host-handshake.md#2-host-state)), `installHost` MUST answer `refused` with a detail saying the daemon runs in WSL, before invoking the installer or causing any file or host side effect, including when a validated offer is reachable through mirrored networking.
- **RPC-4** `hosts` MUST list live offers only, each identified by an opaque `offerID` minted by the plugin. Since an announcement of another protocol is refused ([HST-11](host-handshake.md#4-advertisement-and-attachment)), `protocol: "incompatible"` means that the offer's hello derives `incompatible` for another reason ([HST-3](host-handshake.md#2-host-state)): an application version outside `appVersions` or an application part newer than `host.range`. `attachHost` on such an offer MUST answer `refused`.
- **RPC-5** `attachHost` MUST answer `gone` for an unknown or expired offer, `busy` when the session is bound to another live offer, `attached` when it binds the session (or when it is already bound to that offer). The tools of an attached session MUST act on that offer's document.
- **RPC-6** **No secret in outputs.** RPC outputs and `rpc.<id>.*` events MUST NOT contain the host token, the host port, a `script_handle`, a `binding_key` or an `offer_key`; only `hello`, between the plugin and its host, carries the script handle. Events MUST carry no payload (`{}`).
- **RPC-7** **Bounds.** `label` and `document` MUST be display text of at most 120 characters; `document` is a file name without folders, never a usable path; `detail` has at most 300 characters; none contains a path separator or a control character. Outputs MUST NOT carry a path, URL, token or secret.
- **RPC-8** `advertise` and `withdraw` MUST validate their own input against `advertise.input` and `withdraw.input`: the plugin registers them with an opaque object input, so that an invalid announcement answers `refused` (or `none`) without revealing its cause, and no validation error can echo a value. The rules of `advertise` are [HST-10 to HST-17](host-handshake.md#4-advertisement-and-attachment).
- **RPC-9** **Session binding read.** `bindingRead` is a read-only native method. Its input MUST contain exactly `sessionID` (1..128 ASCII letters, digits, `_` or `-`). It MUST answer `attached` only for that exact session's current live binding, `none` only after verifying that exact session belongs to the calling Location and has no live binding, and `unknown` when that verification is unavailable, fails, or the session is missing or belongs elsewhere. Invalid inputs MUST be refused without echoing their values. `unknown` MUST NOT clear a binding. Aggregate `status.connection`, offer counts, a saved client selection and the Q8 metadata hint MUST NOT serve as binding authority.
- **RPC-10** **Authority.** `attached` and `none` MUST include `authority`; `unknown` MAY include it only when the current authority is available. Both generation strings MUST be opaque 1..128 ASCII letters, digits, `_` or `-`, carrying no path, port, credential or foreign identifier. `serviceGeneration` identifies the current daemon process lifetime and MUST change on service replacement; `registryGeneration` identifies the current shared registry instance and MUST change on registry replacement. `revision` MUST be an integer in 0..9007199254740991, monotonic within those generations, and advance on every binding-observable mutation: attach, detach, label change, revoke, expiry and replacement. A renewal with unchanged public binding data MAY retain the revision. At revision exhaustion the registry MUST receive a fresh generation before another mutation. Readers MUST compare revisions only within equal service and registry generations.
- **RPC-11** **Closed, private result.** Only `attached` MUST contain `binding`, with exactly the current `offerID` (8..64 ASCII letters, digits, `_` or `-`) and safe display `label` (1..120 Unicode code points, RPC-7). `none` and `unknown` MUST omit `binding`. Every result and each nested object MUST reject extra fields, including session/workspace IDs, paths, endpoint details and credentials. The label MUST also pass the advertised-value confidentiality checks of HST-11. The plugin MUST call the full local `parseBindingReadInput` and `parseBindingReadOutput` validation at the boundary, including all patterns; the registered JSON Schema strips patterns on the qualified native runtime.
- **RPC-12** **Freshness and bounds.** Each call MUST obtain a fresh native `ctx.session.get` snapshot for the exact requested session ID and validate the public ownership contract of HST-26. After an asynchronous permission or hello wait, an attachment/detachment operation MUST obtain another fresh exact-session snapshot, then synchronously recheck the current registry identity, both generations, revision and selected offer immediately before committing, with no intervening asynchronous wait. A `bindingRead` result MUST likewise be assembled only after the snapshot and final synchronous registry/authority/offer checks. The snapshot establishes ownership at the time of that read; a concurrent native session move or deletion can occur between the snapshot and a binding commit. Implementations MUST NOT claim an atomic native session-and-registry transaction. Every binding RPC attempt, including ownership lookups and post-wait validation, MUST be bounded by `BINDING_RPC_TIMEOUT_MS = 8000`; expiration of a read yields `unknown`, and a timed-out mutation MUST NOT commit later.
- **RPC-13** **Invalidation.** `status_changed` MUST carry exactly `{}` and invalidate cached `bindingRead` results as well as aggregate status. The plugin MUST emit it on each binding-observable mutation, including a bound offer's label change, revocation, expiry or replacement. Clients MUST reread the exact session after invalidation and after reconnecting or changing Location; events are live-only. A read MUST NOT install, attach, detach, prompt for permission or extend an offer lease.

## 4. Reference implementation

`packages/integration-kit` provides `rpcDefinition(rpc, family)` (the wire schemas of `rpc.schema.json`, registered without their `pattern` keywords because OpenCode 2.2.5 refuses to compile a registered schema that has one), the exported full local parsers `parseBindingReadInput` and `parseBindingReadOutput`, `BINDING_RPC_TIMEOUT_MS`, `createIntegrationRpc(...)` (handlers, status assembly, bounded install details, event relay), and the offer registry of [`host-handshake.md`](host-handshake.md). The plugin validates `advertise`, `withdraw` and both sides of `bindingRead` locally; conformance checks the strict output form and schema/parser parity.
