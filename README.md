# OpenCode integrations

OpenCode integrations connect [OpenCode](https://opencode.ai) to third-party applications and services. Each integration is **one self-contained OpenCode V2 plugin**, published as one npm package `@neuralnomads/opencode-<app>` with its tools, skills, data and scripts. An application-side part (an add-on, a panel), when there is one, ships inside the package but is installed separately, by an explicit action, and is checked by version.

Integrations work in **every OpenCode client**: the terminal UI, the desktop app and [CodeNomad](https://github.com/NeuralNomadsAI/CodeNomad). CodeNomad gives the best experience (a catalog, per-integration status, one-click host install and attaching an open application to a conversation), but no integration depends on it.

> **Status: draft 0.x.** The specification and the packages are previews. Nothing is published to npm yet.

## Families

| Family       | Principle                                                                                                                                                                                                                                                        | Examples                         |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| `native`     | An authenticated loopback host inside the application owns the document state; the plugin exposes bounded `<app>_context` / `<app>_apply` intents. The conversation happens in an application panel or in any OpenCode client by attaching the open application. | Blender (pilot), Gaffer, Houdini |
| `vendor-mcp` | The plugin declares the vendor's official MCP server (never redistributed), checks its preconditions and adds tested skills and workflows.                                                                                                                       | DaVinci Resolve                  |
| `api`        | The plugin calls a remote API directly.                                                                                                                                                                                                                          | Microsoft 365                    |

## Integrations

None is released yet. Blender is the pilot; Gaffer, Houdini, DaVinci Resolve and Microsoft 365 follow. The reviewed list lives in [`catalog.json`](catalog.json).

## Installation

Any OpenCode client: add the package to the `plugin` list of your global OpenCode configuration (`~/.config/opencode/opencode.json`):

```jsonc
{
  "plugin": ["@neuralnomads/opencode-<app>@^0.1"]
}
```

OpenCode installs the package on its next start. Integrations of the `native` family are installed in the global configuration only.

### In CodeNomad

Open **Status → Plugins**, choose **Add**, and enter the package name; or use the **Integrations** extension, which lists the catalog, shows each integration's state and installs the application part.

## Repository layout

| Path                        | Content                                                                       |
| --------------------------- | ----------------------------------------------------------------------------- |
| `spec/`                     | Specification of OpenCode integrations and its JSON Schemas (`spec/schemas/`) |
| `catalog.json`              | Reviewed catalog, published as the asset of a `catalog-v<n>` release          |
| `packages/integration-kit/` | Shared core bundled into every integration plugin                             |
| `packages/conformance/`     | Conformance checks of markers, catalog and packaging                          |
| `integrations/<app>/`       | `plugin/` (the npm package), `host/` (the application part), `test/`          |
| `scripts/`                  | Repository tooling, including the trace scan                                  |

## Development

Node.js 22.18 or later runs the TypeScript sources directly.

```sh
npm ci
npm run lint
npm run typecheck
npm test
npm run check:markers
npm run build && npm run scan
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Trademarks

Application and vendor names are used only to say what an integration works with. This project is not affiliated with, endorsed by or sponsored by OpenCode, the Blender Foundation, Image Engine, SideFX, Blackmagic Design, Microsoft or any other vendor.

## License

[MIT](LICENSE)
