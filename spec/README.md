# Specification of OpenCode integrations (draft 0.x)

The specification defines what an OpenCode integration is: its three families (`native`, `vendor-mcp`, `api`), the package contents, the static marker, the RPC status contract, the host handshake, undo levels, security rules and naming. It stays a draft (`spec: 0`) until each family has shipped an integration, then freezes as 1.0 (`spec: 1`).

| File                          | Content                                            |
| ----------------------------- | -------------------------------------------------- |
| `schemas/marker.schema.json`  | The `opencodeIntegration` marker of `package.json` |
| `schemas/catalog.schema.json` | The reviewed `catalog.json`                        |

`packages/conformance` validates every integration package and the catalog against these schemas, and adds the rules JSON Schema cannot express: the ID derived from the package name, the host payload digest, unique plugin and RPC IDs across the repository, unique catalog entries, the 256 KiB catalog cap and the catalog's equality with the markers.

The normative text (`integrations.md`, `rpc-contract.md`, `host-handshake.md`) and the RPC schemas are written in draft 0.1.
