# RPC status contract — contract 1, draft 0.1

Normative part of [`integrations.md`](integrations.md); the key words MUST, MUST NOT, SHOULD and MAY are to be read as in RFC 2119. Schema: [`schemas/rpc.schema.json`](schemas/rpc.schema.json), one definition per method input (`<method>.input`), output (`<method>.output`) and event (`<event>.event`).

## 1. Registration and detection

- **RPC-1** The plugin MUST register the contract with `ctx.rpc.register` under its RPC ID `neuralnomads.<app>` (the marker's `rpc`). `vendor-mcp` and `api` integrations register `status` and the event `status_changed`; `native` integrations register every method and both events below. The RPC ID is specific to the integration: OpenCode's RPC registry keeps the last registration of an ID, so no ID is shared by several integrations.
- **RPC-1a** Clients MUST detect support by capability, never by comparing versions: `rpc.unavailable` means the plugin is not active at that Location; `rpc.method_not_found` means the method is not supported.
- **RPC-1b** Readers MUST ignore unknown output fields and unknown `notices`. Producers of this repository MUST emit only the fields defined here (the conformance package checks the strict form).

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

// called by the application part, never by a client
advertise({ host: "127.0.0.1", port: number, token: string /* 64 hex */, script_handle: string,
            label: string /* ≤ 120 */, protocol: number, offer_key: string /* ≤ 64, stable per process */ })
  => { outcome: "accepted" | "renewed" | "refused" }
withdraw({ offer_key: string, token: string }) => { outcome: "withdrawn" | "none" }

// events: no payload; the reader reads status or hosts again
status_changed {}
hosts_changed {}
```

- **RPC-3** A client MUST call `installHost` only after a confirmation that it displays; it runs on the machine of the OpenCode daemon and the confirmation MUST say so. The plugin MUST NOT call it while loading ([PKG-6](integrations.md#3-package-content)). Under the WSL condition of [HST-4](host-handshake.md#2-host-state) it MUST answer `refused` with a detail saying the daemon runs in WSL.
- **RPC-4** `hosts` MUST list live offers only, each identified by an opaque `offerID` minted by the plugin. Since an announcement of another protocol is refused ([HST-11](host-handshake.md#4-advertisement-and-attachment)), `protocol: "incompatible"` means that the offer's hello derives `incompatible` for another reason ([HST-3](host-handshake.md#2-host-state)): an application version outside `appVersions` or an application part newer than `host.range`. `attachHost` on such an offer MUST answer `refused`.
- **RPC-5** `attachHost` MUST answer `gone` for an unknown or expired offer, `busy` when the session is bound to another live offer, `attached` when it binds the session (or when it is already bound to that offer). The tools of an attached session MUST act on that offer's document.
- **RPC-6** **No secret in outputs.** RPC outputs and `rpc.<id>.*` events MUST NOT contain the host token, the host port, a `script_handle`, a `binding_key` or an `offer_key`; only `hello`, between the plugin and its host, carries the script handle. Events MUST carry no payload (`{}`).
- **RPC-7** **Bounds.** `label` and `document` MUST be display text of at most 120 characters; `document` is a file name without folders, never a usable path; `detail` has at most 300 characters; none contains a path separator or a control character. Outputs MUST NOT carry a path, URL, token or secret.
- **RPC-8** `advertise` and `withdraw` MUST validate their own input against `advertise.input` and `withdraw.input`: the plugin registers them with an opaque object input, so that an invalid announcement answers `refused` (or `none`) without revealing its cause, and no validation error can echo a value. The rules of `advertise` are [HST-10 to HST-17](host-handshake.md#4-advertisement-and-attachment).

## 4. Reference implementation

`packages/integration-kit` provides `rpcDefinition(rpc, family)` (the wire schemas, equal to `rpc.schema.json`), `createIntegrationRpc(...)` (handlers, `status` assembly, bounded `installHost` details, event relay) and the offer registry of [`host-handshake.md`](host-handshake.md).
