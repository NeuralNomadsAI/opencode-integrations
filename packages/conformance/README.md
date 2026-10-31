# conformance

Reusable conformance checks of the OpenCode integrations specification (draft 0). `node packages/conformance/src/cli.ts [root]` checks a repository and exits non-zero on any error:

- every `integrations/<app>/plugin/package.json` has a marker valid against `spec/schemas/marker.schema.json`, whose `id` is the `<app>` of `@neuralnomads/opencode-<app>` and whose `plugin` and `rpc` are `neuralnomads.<app>`;
- the host payload of a `native` marker exists and matches its `sha256`;
- no two integrations share a plugin or RPC ID;
- packaging: ESM, MIT, `files`, the `opencode-integration` keyword, the optional `@opencode/plugin` peer `^2.2.0 || ^0.0.0-dev`, the `2.2.5` dev pin, no SDK or `effect` runtime dependency, no install script;
- skills are named `<app>-<topic>` and start with a front-matter header (`name`, `description`);
- no `@opencode-ai/plugin`, no `_runtime/` copy and no committed `node_modules` anywhere;
- `catalog.json` is valid against `spec/schemas/catalog.schema.json`, at most 256 KiB and 64 integrations, without duplicate `id`, `package`, `plugin` or `rpc`, and each entry copies the marker of a package of the repository.

Host handshake, RPC and loading checks are added with draft 0.1.
