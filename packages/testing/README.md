# testing

Private helpers of this repository.

`node packages/testing/src/cli.ts [root]` (`npm run check:markers`) reads what CodeNomad and the plugins rely on ([`spec/README.md`](../../spec/README.md)) and exits non-zero on any error:

- each `integrations/<app>/plugin/package.json` has a marker in the form of section 1, whose `id`, `plugin` and `rpc` follow its package name;
- a `native` marker's payload exists with its `sha256`, and `host.range` contains the package version;
- `catalog.json` is in the form CodeNomad accepts (at most 256 KiB and 64 entries, no two entries sharing an `id`, `package`, `plugin` or `rpc`), and each entry copies its package's marker;
- each built plugin, loaded with fresh HOME and XDG folders, has the marker's plugin ID, registers its RPC under the marker's `rpc` (`status`, and for `native` `detachHost`, `advertise`, `withdraw` and both events) and answers `status` as section 2 describes.

The integrations' tests also use:

| Function                         | Use                                                                                                                                         |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `startFakeHost(options)`         | A fake application part speaking wire protocol 1                                                                                            |
| `checkHostListener(endpoint)`    | Probes a real application part: loopback only, nothing without the exact token, a valid `hello`                                             |
| `runSentinelScenario(target)`    | Runs advertise, attach, detach and withdraw against a plugin and finds no token, port, script handle or offer key in its outputs and events |
| `checkCodeExecutionGate(driver)` | Probes how a plugin and its application part expose their code-execution method                                                             |
| `checkStatusOutput`              | Reads a `status` output as CodeNomad does                                                                                                   |
