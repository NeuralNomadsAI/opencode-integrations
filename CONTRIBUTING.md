# Contributing

## Setup

Node.js 22.18 or later (CI uses Node.js 24). The TypeScript sources run directly through Node's type stripping, so imports name their `.ts` extension.

```sh
npm ci
npm run lint        # Biome
npm run typecheck   # tsc
npm test            # node --test
npm run check:markers
npm run build && npm run scan
```

## Adding an integration

1. Create `integrations/<app>/plugin` (the npm package), `integrations/<app>/host` for a `native` application part, and `integrations/<app>/test`.
2. Follow the naming rules:

   | Element                        | Rule                                           | Example                          |
   | ------------------------------ | ---------------------------------------------- | -------------------------------- |
   | npm package                    | `@neuralnomads/opencode-<app>`                 | `@neuralnomads/opencode-blender` |
   | Plugin ID and RPC ID           | `neuralnomads.<app>`                           | `neuralnomads.blender`           |
   | Tools                          | `<app>_context`, `<app>_apply`, `<app>_<verb>` | `blender_hosts`                  |
   | Skills                         | `<app>-<topic>`                                | `blender-geometry-nodes`         |
   | Display name                   | "OpenCode for <App>"                           | "OpenCode for Blender"           |
   | State directory                | `neuralnomads/opencode-<app>`                  |                                  |
   | Fallback environment variables | `NEURALNOMADS_<APP>_*`                         |                                  |
   | Release tag                    | `<app>-v<semver>`                              | `blender-v0.2.0`                 |

3. Declare the static marker (`spec/schemas/marker.schema.json`) under `opencodeIntegration` in `package.json`, with `spec: 0` during the draft.
4. Declare the SDK as an **optional** peer and pin it for development:

   ```json
   "peerDependencies": { "@opencode/plugin": "^2.2.0 || ^0.0.0-dev" },
   "peerDependenciesMeta": { "@opencode/plugin": { "optional": true } },
   "devDependencies": { "@opencode/plugin": "2.2.5" }
   ```

   OpenCode provides the SDK itself; a required peer would make npm install it (about 130 MB). Never bundle `effect` or the SDK, and never use `@opencode-ai/plugin`.
5. Bundle `packages/integration-kit` into the plugin build; it replaces any copied runtime.
6. Publish compiled ESM JavaScript (`dist/`), never raw `.ts`. No npm install scripts.
7. Every `skills/<app>-<topic>/SKILL.md` starts with a front-matter header whose `name` equals its directory and which has a `description`.
8. Add the catalog entry in `catalog.json` in the same pull request; it must copy the marker.

`npm run check:markers` enforces points 2 to 8.

## Test builds from Git

Contributors can install an unreleased build from a Git spec with a subdirectory path, for example `github:NeuralNomadsAI/opencode-integrations#<ref>::path:integrations/<app>/plugin`. Git specs are for testing only; users install from npm.

## Trace scan

`scripts/trace-scan.ts` blocks traces of the deployment these integrations were extracted from. It scans, case-insensitively, every tracked file, every archive in the tree (opened entry by entry), the `npm pack` tarball of every workspace package (`--packs`) and, with `--history`, every commit of every ref. It looks for the former studio and NAS names, paths on the former studio drive letter, UNC network share paths, Windows user-profile paths and known Entra GUIDs. The forbidden words are assembled at runtime and never appear in the repository.

**Entra GUID fingerprints** are kept outside the repository, in the Actions secret `TRACE_SCAN_FINGERPRINTS`: a whitespace- or comma-separated list of SHA-256 hex digests, each computed over a lowercase dashed GUID. A maintainer sets it once:

```sh
node -e "console.log(require('node:crypto').createHash('sha256').update(process.argv[1].toLowerCase()).digest('hex'))" <guid>
gh secret set TRACE_SCAN_FINGERPRINTS --repo NeuralNomadsAI/opencode-integrations
```

Never commit a GUID, a fingerprint list or the secret value.

**Pull requests from forks run a partial scan.** GitHub does not expose secrets to them, so they check every pattern except the Entra GUIDs. The full scan runs again on every push to `main` and, with `--require-fingerprints`, before any npm publication or catalog release, which fail when the secret is missing.

## Releases and publication

Nothing is published without the maintainer's explicit approval at the time of publication:

- **npm** (`.github/workflows/publish.yml`): started by hand, its job waits for the protected `npm-publish` environment, which requires a maintainer's approval; it publishes through trusted publishing (OIDC) with provenance.
- **Catalog** (`.github/workflows/catalog-release.yml`): started by hand, waits for the protected `catalog-release` environment, then creates an immutable `catalog-v<n>` release with `catalog.json` as its asset. Catalog releases are never marked _latest_ and use a tag prefix distinct from integration releases (`<app>-v<semver>`); CodeNomad reads the newest `catalog-*` release.

To check that publication needs approval without publishing anything: start the publish workflow, see its job wait on `npm-publish` ("Waiting for review"), cancel the run, then confirm with `npm view @neuralnomads/opencode-<app> versions` that no version appeared.
