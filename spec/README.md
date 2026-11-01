# Specification of OpenCode integrations (draft 0.2)

The specification defines what an OpenCode integration is: its three families (`native`, `vendor-mcp`, `api`), the package contents, the static marker, the RPC status contract, exact-session binding reads and ownership, the application part and its handshake, attachment, undo levels, security rules, naming and versioning. It stays a draft (`spec: 0`) until each family has shipped an integration, then freezes as 1.0 (`spec: 1`). This is draft **0.2**.

| File                                     | Content                                                                                         |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------- |
| [`integrations.md`](integrations.md)     | Definition, families, package content, marker, security, naming, versioning, conformance map    |
| [`rpc-contract.md`](rpc-contract.md)     | RPC status contract 1: `status` and the native methods and events                               |
| [`host-handshake.md`](host-handshake.md) | Application part: delivery, `hello`, host state, authentication, advertisement, discovery, undo |
| `schemas/marker.schema.json`             | The `opencodeIntegration` marker of `package.json`                                              |
| `schemas/catalog.schema.json`            | The reviewed `catalog.json`                                                                     |
| `schemas/rpc.schema.json`                | Inputs, outputs and events of the RPC status contract                                           |
| `schemas/host.schema.json`               | Host wire protocol 1 and the `hello` result                                                     |

Every requirement has an identifier (`FAM-1`, `PKG-5`, `RPC-6`, `HST-11`…). `packages/conformance` implements the automated checks; `integrations.md` §10 maps each requirement to its check. `packages/integration-kit` is the reference implementation of the plugin side, and `packages/integration-kit/python/opencode_service.py` the reference discovery client of headless hosts.
