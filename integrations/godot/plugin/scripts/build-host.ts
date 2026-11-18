// The installed part's payload: the add-on folder addons/opencode_link (GDScript, read by Godot as it is) and the
// dock's sidecar (sidecar.cjs, the plugin's own OpenCode client for Node), as a stored zip with the plugin's version.
// Usage: node scripts/build-host.ts [--check]

import { createHash } from "node:crypto"
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"
import { writeStoredZip } from "../../../../packages/integration-kit/src/zip.ts"

const root = fileURLToPath(new URL("..", import.meta.url))
const host = join(root, "..", "host")
const addon = join(host, "addons", "opencode_link")
const pkgPath = join(root, "package.json")
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"))
const version = /^version="([^"]+)"$/m.exec(readFileSync(join(addon, "plugin.cfg"), "utf8"))?.[1]
if (version !== pkg.version) throw new Error("Host and plugin versions differ")
const check = process.argv.includes("--check")
const bundle = await build({
  entryPoints: [join(root, "src", "sidecar.ts")],
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node22",
  write: false,
  legalComments: "eof",
  logLevel: "warning",
})
const sidecar = Buffer.from(bundle.outputFiles?.[0]?.contents ?? [])
const walk = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.name === "sidecar.cjs" || entry.name.endsWith(".uid")
      ? []
      : entry.isDirectory()
        ? walk(join(dir, entry.name))
        : [join(dir, entry.name)],
  )
const entries = walk(addon).map((file) => ({
  name: relative(host, file).replaceAll("\\", "/"),
  data: Buffer.from(readFileSync(file, "utf8").replaceAll("\r\n", "\n")),
}))
entries.push({ name: "addons/opencode_link/sidecar.cjs", data: sidecar })
const payload = writeStoredZip(entries.sort((a, b) => (a.name < b.name ? -1 : 1)))
const name = `host/godot/opencode_link-${pkg.version}.zip`
const digest = createHash("sha256").update(payload).digest("hex")
const file = join(root, name)
if (check) {
  if (
    !existsSync(file) ||
    !readFileSync(file).equals(payload) ||
    pkg.opencodeIntegration.host.sha256 !== digest ||
    pkg.opencodeIntegration.host.payload !== name
  )
    throw new Error("Godot host payload is stale: run npm run build:host --workspace @neuralnomads/opencode-godot")
} else {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, payload)
  pkg.opencodeIntegration.host.payload = name
  pkg.opencodeIntegration.host.sha256 = digest
  writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`)
}
console.log(`Godot host ${check ? "verified" : "built"}: ${pkg.version}`)
