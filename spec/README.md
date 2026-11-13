# OpenCode integrations: how the parts talk

This page describes what an integration and CodeNomad exchange, and how an integration's plugin and the part installed in the application talk to each other. It describes the current behaviour of this repository (`spec: 0`, `contract: 1`); `packages/integration-kit` implements the plugin side. Anything not described here is left to each integration and to the review of its pull request.

An integration is one OpenCode plugin. A `native` integration also ships an **application part** (an add-on or a panel installed in the application, such as Blender's OpenCode Link add-on); `vendor-mcp` and `api` integrations have none.

## 1. Recognition: the marker and the catalog

The integration's `package.json` carries a marker under `opencodeIntegration`, readable without running the plugin:

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
    "range": "^0.4.1",
    "appVersions": ">=5.3",
    "payload": "host/blender/opencode_link-0.4.1.zip",
    "sha256": "<sha256 of the payload>"
  }
}
```

The package is `@neuralnomads/opencode-<id>`; `plugin` is the plugin ID OpenCode loads it under and `rpc` the ID of its RPC methods, both `neuralnomads.<id>`. `family` is `native`, `vendor-mcp` or `api`; only `native` has `host` (section 4).

`catalog.json` at the root of the repository copies each marker into an entry, with the package name, the npm `range` clients install, a `summary` by locale (`en` at least), `app.name` and `app.versions`, `docs`, `status` (`preview`, `stable` or `deprecated`) and optional Git `sources`. CodeNomad reads the `catalog.json` asset of the newest `catalog-v<n>` release. It recognises a configured plugin as an integration when its npm package is the entry's `package`, when OpenCode loaded it under the entry's `plugin` ID, or when its Git source starts with one of the entry's `sources.git`. `npm run check:markers` checks that each entry matches its package's marker.

## 2. State: what CodeNomad reads

CodeNomad calls the RPC method `status` with input `{}`, under the catalog's `rpc` ID, at the conversation's Location:

```ts
status({}) => {
  contract: 1,
  integration: { id, version /* the package version, semver */, family, spec: 0 },
  host?: {            // native only, section 4
    state: "absent" | "incompatible" | "outdated" | "ok" | "unknown" | "unsupported",
    partVersion?: string, expectedRange?: string, appVersion?: string, action?: "install" | "update"
  },
  connection?: {
    state: "none" | "available" | "attached" | "connected" | "signed_out" | "error",
    offers?: number /* 0..16 */, attachedSessions?: number
  },
  notices?: string[]  // for example "legacy_install"; unknown ones are ignored
}
```

CodeNomad shows the answer only when `integration.id` and `family` are the catalog's and `host.appVersion` holds no path. It ignores fields it does not know. An RPC that is not registered (`rpc.unavailable`, `rpc.method_not_found`) shows as unsupported.

The plugin emits the events `status_changed` and, for `native`, `hosts_changed`, both with payload `{}`, when its state changes; CodeNomad then reads `status` again. A `native` plugin also answers three methods for one conversation or its application part:

```ts
installHost({}) => { outcome: "installed" | "updated" | "unchanged" | "refused" | "failed", detail?: string }
bindingRead({ sessionID }) =>
    { state: "attached", authority, binding: { offerID, label } }
  | { state: "none", authority }
  | { state: "unknown", authority? }
detachHost({ sessionID }) => { outcome: "detached" | "none" }
```

`installHost` writes the embedded application part (`host.payload`) into the application on the machine where OpenCode runs; `detail` is a short human-readable reason. `bindingRead` tells whether that conversation is attached and to which offer: `label` is the offer's label (section 3), `unknown` when the plugin cannot verify the conversation. `authority` (`serviceGeneration`, `registryGeneration`, `revision`) identifies the plugin's attachment state the answer was read from. `detachHost` detaches that conversation from its application.

## 3. Plugin and application part

**Finding OpenCode.** OpenCode writes its registration to `$XDG_STATE_HOME/opencode/service.json` (or `service-<name>.json`; default folder `~/.local/state/opencode`) with its URL, `pid`, `version` and password. The application part keeps the registrations whose loopback `/api/info` answers the same `pid` and `version`, and calls the plugin with HTTP basic authentication `opencode:<password>`:

```
POST <url>/api/rpc/<rpc>/<method>?location[directory]=<global configuration directory>
{ "input": { ... } }
```

The global configuration directory is `$XDG_CONFIG_HOME/opencode` (default `~/.config/opencode`): one Location shared by every project of the daemon.

**Announcing.** The application part listens on `127.0.0.1`, on a random port, with a random token (64 hex characters) it keeps in memory, and announces itself:

```ts
advertise({ host: "127.0.0.1", port, token, script_handle, label, protocol, offer_key })
  => { outcome: "accepted" | "renewed" | "refused" }
withdraw({ offer_key, token }) => { outcome: "withdrawn" | "none" }
```

`script_handle` names the open document, `label` is what the user sees (at most 120 characters), and `offer_key` stays the same for the whole application process. Before accepting, the plugin sends `hello` (params `{ script_handle }`) with the announced token; an announcement whose `hello` fails is refused. An offer lasts 15 seconds; the application part announces itself again every 5 seconds, which renews it. A new token, port or document under the same `offer_key` replaces the offer once its `hello` succeeds. `withdraw`, or 15 seconds without renewal, removes the offer.

**Requests.** The plugin opens one TCP connection per request, writes one line of JSON and reads one line back:

```
{ "id", "token", "method", "params" }                       // at most 1 MiB
{ "id", "ok": true, "result" } | { "id", "ok": false, "error": { "code", "message"? } }   // at most 8 MiB
```

A request without the exact token gets `unauthorized`. The other error codes are `invalid_request`, `method_not_found`, `method_disabled`, `invalid_args`, `stale_handle`, `stale_revision`, `expectation_failed`, `mode_conflict`, `busy`, `quarantined`, `permission_denied` and `internal`; the plugin reads an unknown code as `internal`. `hello` answers:

```ts
{ protocol, host_version, app_version, capabilities: { methods, operations, undo, background },
  capability_hash /* sha256 of the capabilities as JSON with sorted keys and no whitespace */,
  script_handle, revision, document? }
```

**Attachment.** Each conversation is attached to at most one offer, and its tools act on that offer's document. In Blender, the only compatible offer attaches itself at the conversation's first call when no other conversation holds it; otherwise the `blender_hosts` tool lists the offers and attaches one. Removing an offer detaches its conversations.

## 4. Versions

The package embeds the application part (`host.payload`, checked against `host.sha256`) in the package's version, so `host.range` contains the package version. From a `hello` received within the last 15 seconds, the plugin derives:

| `hello` field  | Compared with      | Result                                                     |
| -------------- | ------------------ | ---------------------------------------------------------- |
| `protocol`     | `host.protocol`    | different: the announcement is refused                     |
| `host_version` | `host.range`       | below: `outdated` (action `update`); above: `incompatible` |
| `app_version`  | `host.appVersions` | outside, by its leading numbers: `incompatible`            |

Otherwise the state is `ok`; prereleases count like releases. With several offers, the least favourable state wins. Without a live `hello`, the plugin looks for the part in the application's add-on folder: not found gives `absent` (action `install`), an older or newer version `outdated` or `incompatible`, a matching one `unknown` (the application is closed). Inside WSL, the state is `unsupported` until an offer has answered `hello`.
