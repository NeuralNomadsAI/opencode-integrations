# Contributing

## Setup

Node.js 22.18 or later (CI uses Node.js 24). The TypeScript sources run directly through Node's type stripping, so imports name their `.ts` extension.

```sh
npm ci
npm run lint        # Biome
npm run typecheck   # tsc
npm test            # node --test
npm run build
npm run check:markers
npm run scan
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

Integration plugins are published with public npm access; the repository root and shared workspaces stay private. Each plugin's repository URL exactly matches the GitHub repository URL for npm trusted publishing.

### First publication of new packages

A maintainer with publish access to the `@neuralnomads` npm scope and account-level 2FA runs one local batch in an interactive terminal:

```sh
npm login
npm run npm:bootstrap -- --dry-run
npm run npm:bootstrap
```

At the first npm web authentication prompt, tick **"skip two-factor authentication for the next 5 minutes"**. Keep the same terminal open: publish, trust and access calls share that window, instead of asking for approval per package. Very large or slow batches can require a second validation when the window expires. The script spaces registry calls by two seconds, keeps npm output and authentication URLs visible, and uses `npx --yes npm@12` when local npm is older than 11.15 (without changing the global installation).

The script runs `npm ci`, `build` and `check:markers` once before querying package and trust state, keeping that preparation outside the five-minute authentication window. It discovers public `integrations/*/plugin` workspaces and selects unpublished packages. Each new package is published at `latest` without local provenance, linked to `publish.yml` in `NeuralNomadsAI/opencode-integrations` with the `npm-publish` environment, and set to `mfa=publish`. Existing packages are skipped. To finish setup for existing packages (including a partially completed batch), run:

```sh
npm run npm:bootstrap -- --trust-only
```

This mode skips existing trust configurations, enforcing MFA even when trust is already present so a failed final access call can be retried. Combine either mode with `--dry-run` to query and print its command plan without building, publishing or changing registry settings. Query failures are reported rather than treated as unpublished packages.

New versions go through the **publish.yml** workflow below, using OIDC without maintainer npm 2FA. Configure and validate the trust relationship promptly: [npm trust configurations expire after two days unless a CI publication validates them](https://docs.npmjs.com/trusted-publishers). The [npm trust bulk usage guide](https://docs.npmjs.com/cli/v11/commands/npm-trust#bulk-usage) documents the five-minute skip window and rate-limit spacing.

### Subsequent releases

Nothing is published without the maintainer's explicit approval at the time of publication:

- **npm** (`.github/workflows/publish.yml`): started by hand, its job waits for the protected `npm-publish` environment, which requires a maintainer's approval; it publishes through trusted publishing (OIDC) with provenance.
- **Catalog** (`.github/workflows/catalog-release.yml`): started by hand, waits for the protected `catalog-release` environment, then creates an immutable `catalog-v<n>` release with `catalog.json` as its asset. Catalog releases are never marked _latest_ and use a tag prefix distinct from integration releases (`<app>-v<semver>`); CodeNomad reads the newest `catalog-*` release.

To check that publication needs approval without publishing anything: start the publish workflow, see its job wait on `npm-publish` ("Waiting for review"), cancel the run, then confirm with `npm view @neuralnomads/opencode-<app> versions` that no version appeared.
