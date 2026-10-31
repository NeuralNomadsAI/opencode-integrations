# conformance

Reusable conformance checks of the OpenCode integrations specification, **draft 0.1** (`SPEC_DRAFT`). `node packages/conformance/src/cli.ts [root]` checks a repository, loads every built integration package and exits non-zero on any error; its summary names the validated draft.

## Repository checks (`checkRepository`)

- every `integrations/<app>/plugin/package.json` has a marker valid against `spec/schemas/marker.schema.json`, whose `id` is the `<app>` of `@neuralnomads/opencode-<app>` and whose `plugin` and `rpc` are `neuralnomads.<app>`;
- the host payload of a `native` marker exists and matches its `sha256`, and `host.range` contains the package version (the embedded host part's version);
- no two integrations share a plugin or RPC ID;
- packaging: semver version, ESM, MIT, `files`, the `opencode-integration` keyword, the optional `@opencode/plugin` peer `^2.2.0 || ^0.0.0-dev`, the `2.2.5` dev pin, no SDK or `effect` runtime dependency, no install script;
- method manifests (`methods/**.json`): at most one `dangerous` method, named `<app>_execute_code` with risk `destructive`, and no other method named like code execution;
- skills are named `<app>-<topic>` and start with a front-matter header (`name`, `description`);
- CNF-1: the tests under `integrations/<app>/` of a `native` integration reference `checkHostListener`, `checkCodeExecutionGate` and `runSentinelScenario`;
- no `@opencode-ai/plugin`, no `_runtime/` copy and no committed `node_modules` anywhere;
- `catalog.json` is valid against `spec/schemas/catalog.schema.json`, at most 256 KiB and 64 integrations, without duplicate `id`, `package`, `plugin` or `rpc`, and each entry copies the marker of a package of the repository.

## Behaviour checks

| Function                              | Checks                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `checkPluginLoad(pkg)`                | Imports the built entry in a child process with fresh HOME and XDG folders: no file or folder written into the OpenCode configuration or the package, no process launch, listener, outbound connection or fetch while loading, plugin ID and RPC ID of the marker, the contract's methods and events, `status` equal to the marker |
| `checkStatusOutput`, `checkRpcOutput` | Strict producer form of the outputs against `spec/schemas/rpc.schema.json`, marker equality, host action rules, known notices                                                                                                                                                                                                      |
| `checkHostListener(endpoint)`         | 127.0.0.1 only, port unreachable on other interfaces, no answer without the exact token, valid `hello` and capability hash                                                                                                                                                                                                         |
| `checkCodeExecutionGate(driver)`      | The `dangerous` method: declared, absent by default, absent with one option only, refused as `method_disabled`, asked through `<app>_execute_code` on every call                                                                                                                                                                   |
| `runSentinelScenario(target)`         | Advertises sentinel token, port, script handle and offer key from a fake host (whose `document` and `app_version` also carry them), runs the native contract and finds none of them in outputs or events                                                                                                                           |
| `startFakeHost(options)`              | Reference loopback host of wire protocol 1, for tests                                                                                                                                                                                                                                                                              |

`rpcValidator(name, "reader" | "strict")` and `hostValidator(name)` expose the schema definitions to integration tests.
