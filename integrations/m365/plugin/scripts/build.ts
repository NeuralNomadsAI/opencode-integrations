import { mkdirSync, writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"

const root = new URL("../", import.meta.url)
mkdirSync(new URL("dist/", root), { recursive: true })
await build({
  entryPoints: [fileURLToPath(new URL("src/plugin.ts", root))],
  outfile: fileURLToPath(new URL("dist/index.js", root)),
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  external: ["@opencode/plugin", "@opencode/plugin/*"],
  banner: {
    js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  },
  legalComments: "eof",
})
writeFileSync(
  new URL("dist/index.d.ts", root),
  'import type { Plugin } from "@opencode/plugin"\ndeclare const plugin: Plugin.Plugin\nexport default plugin\n',
)
