// Builds dist/: one ESM file bundling the plugin and the integration kit (the SDK stays external,
// the OpenCode binary provides it), and its type declaration. Fails when the host artifacts are stale.
// Usage: node scripts/build.ts

import { execFileSync } from "node:child_process"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const dist = join(root, "dist")

execFileSync(process.execPath, [join(root, "scripts", "build-host.ts"), "--check"], { stdio: "inherit" })

rmSync(dist, { recursive: true, force: true })
mkdirSync(dist, { recursive: true })
await build({
  entryPoints: [join(root, "src", "plugin.ts")],
  outfile: join(dist, "index.js"),
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  external: ["@opencode/plugin", "@opencode/plugin/*"],
  // semver is CommonJS: give the bundle a require for Node's built-in modules.
  banner: {
    js: 'import { createRequire as __createRequire } from "node:module";\nconst require = __createRequire(import.meta.url);',
  },
  legalComments: "eof",
  logLevel: "warning",
})
writeFileSync(
  join(dist, "index.d.ts"),
  'import type { Plugin } from "@opencode/plugin"\n\ndeclare const plugin: Plugin.Plugin\nexport default plugin\n',
)
console.log("build: dist/index.js")
