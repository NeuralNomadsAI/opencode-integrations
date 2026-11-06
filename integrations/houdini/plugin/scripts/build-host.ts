import { createHash } from "node:crypto"
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"
import { writeStoredZip } from "../../../../packages/integration-kit/src/zip.ts"

const root = fileURLToPath(new URL("..", import.meta.url))
const host = join(root, "..", "host")
const pkgPath = join(root, "package.json")
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"))
const manifest = JSON.parse(readFileSync(join(host, "manifest.json"), "utf8"))
if (manifest.version !== pkg.version) throw new Error("Host and plugin versions differ")
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
    entry.name === "__pycache__" || entry.name.endsWith(".pyc") || entry.name === "sidecar.cjs"
      ? []
      : entry.isDirectory()
        ? walk(join(dir, entry.name))
        : [join(dir, entry.name)],
  )
const entries = walk(host).map((file) => ({
  name: relative(host, file).replaceAll("\\", "/"),
  data: Buffer.from(readFileSync(file, "utf8").replaceAll("\r\n", "\n")),
}))
entries.push({ name: "sidecar.cjs", data: sidecar })
const payload = writeStoredZip(entries.sort((a, b) => (a.name < b.name ? -1 : 1)))
const name = `host/houdini/opencode_link-${pkg.version}.zip`
const digest = createHash("sha256").update(payload).digest("hex")
const file = join(root, name)
if (check) {
  if (
    !existsSync(file) ||
    !readFileSync(file).equals(payload) ||
    pkg.opencodeIntegration.host.sha256 !== digest ||
    pkg.opencodeIntegration.host.payload !== name
  )
    throw new Error("Houdini host payload is stale: run npm run build:host --workspace @neuralnomads/opencode-houdini")
} else {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, payload)
  pkg.opencodeIntegration.host.payload = name
  pkg.opencodeIntegration.host.sha256 = digest
  writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`)
}
console.log(`Houdini host ${check ? "verified" : "built"}: ${pkg.version}`)
