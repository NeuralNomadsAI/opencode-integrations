# integration-kit

Shared core of the integration plugins. Each plugin bundles it into its build, so two integrations loaded in the same OpenCode daemon cannot diverge. It is not published to npm.

| Module                       | Role                                                                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `types.ts`                   | Method manifest types (`methods/**.json`)                                                                                                    |
| `method-registry.ts`         | Loads the manifests of a plugin, sorted by name                                                                                              |
| `manifest-args.ts`           | Validates and normalizes tool arguments against a manifest                                                                                   |
| `plugin-tools.ts`            | Builds OpenCode V2 tools (`@opencode/plugin` 2.2) from manifests and registers them under the integration's namespace, honoring cancellation |
| `coherence-check.ts`         | Checks manifests, handlers and the tools mentioned by skills against each other                                                              |
| `marker.ts`                  | Marker types, the draft specification integer and ID helpers                                                                                 |
| `validators.ts`, `output.ts` | Small JSON helpers                                                                                                                           |

The RPC status contract, the loopback host client, the offer registry and skill loading join the kit with draft 0.1 of the specification and the Blender pilot.

## Origin of the modules

The kit reconciles the two OpenCode V2 migrations of the previous runtime, reviewed file by file:

| Module                                                                 | Taken from                          | Notes                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `plugin-tools.ts`                                                      | Verified V2 port (the M365 variant) | The only variant built on `@opencode/plugin` V2 `ToolEditor`/`Info`: namespaced tools, argument validation before execution, abort checks. The other variants still targeted the V1 `tool()` helper or a hand-written schema type. Checked against the 2.2.5 type definitions.                                                             |
| `manifest-args.ts`, `method-registry.ts`, `validators.ts`, `output.ts` | Shared runtime                      | Identical in every copy apart from line endings.                                                                                                                                                                                                                                                                                           |
| `coherence-check.ts`                                                   | Shared runtime                      | Unused import removed; `bundleName` renamed `integration`.                                                                                                                                                                                                                                                                                 |
| `types.ts`                                                             | Shared runtime                      | Bridge transport types removed with the host dispatcher; `requires.bridgeMethods` renamed `hostMethods`; `dangerous` flag added.                                                                                                                                                                                                           |
| host dispatcher                                                        | Not taken                           | It inlined Python scripts from the npm package and sent them to the host's code-execution primitive, which the specification forbids: `native` hosts expose a fixed method table and never import code from the package. Its V2 cancellation fix lives on in `plugin-tools.ts`: executors receive the tool context and its abort `signal`. |
