import { execFileSync } from "node:child_process"
import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
execFileSync(process.execPath, [join(root, "scripts", "build-host.ts"), "--check"], {
  stdio: "inherit",
  windowsHide: true,
})
mkdirSync(join(root, "dist"), { recursive: true })
await build({
  entryPoints: [join(root, "src", "plugin.ts")],
  outfile: join(root, "dist", "index.js"),
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  external: ["@opencode/plugin", "@opencode/plugin/*"],
  banner: {
    js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  },
})
writeFileSync(
  join(root, "dist", "index.d.ts"),
  'import type { Plugin } from "@opencode/plugin"\ndeclare const plugin: Plugin.Plugin\nexport default plugin\n',
)
