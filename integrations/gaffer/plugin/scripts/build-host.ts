import { createHash } from "node:crypto"
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"
import { writeStoredZip } from "../src/zip.ts"

const root = fileURLToPath(new URL("..", import.meta.url))
const hostRoot = join(root, "..", "host")
const pkgPath = join(root, "package.json")
const pkg = JSON.parse(readFileSync(pkgPath, "utf8"))
const manifest = JSON.parse(readFileSync(join(hostRoot, "manifest.json"), "utf8"))
if (manifest.version !== pkg.version) throw new Error("Host and plugin versions must match.")
const check = process.argv.includes("--check")
const sidecar = await build({
  entryPoints: [join(root, "src", "sidecar.ts")],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  target: "node22",
  packages: "bundle",
  minify: true,
  banner: {
    js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  },
})
function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true })
    .flatMap((e) => (e.isDirectory() ? (e.name === "__pycache__" ? [] : walk(join(dir, e.name))) : [join(dir, e.name)]))
    .sort()
}
const entries = walk(hostRoot)
  .filter((f) => !f.endsWith(".pyc"))
  .map((file) => ({
    name: relative(hostRoot, file).split(sep).join("/"),
    data: Buffer.from(readFileSync(file, "utf8").replaceAll("\r\n", "\n")),
  }))
const sidecarFile = sidecar.outputFiles?.[0]
if (!sidecarFile) throw new Error("Sidecar build produced no JavaScript.")
entries.push({ name: "sidecar.js", data: Buffer.from(sidecarFile.contents) })
const bytes = writeStoredZip(entries.sort((a, b) => a.name.localeCompare(b.name, "en")))
const payload = `host/gaffer/opencode_link-${pkg.version}.zip`
const sha256 = createHash("sha256").update(bytes).digest("hex")
const file = join(root, payload)
if (check) {
  if (
    pkg.opencodeIntegration.host.payload !== payload ||
    pkg.opencodeIntegration.host.sha256 !== sha256 ||
    !existsSync(file) ||
    !readFileSync(file).equals(bytes)
  )
    throw new Error("Host payload is stale; run build:host.")
} else {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, bytes)
  pkg.opencodeIntegration.host.payload = payload
  pkg.opencodeIntegration.host.sha256 = sha256
  writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`)
}
console.log(`gaffer host: ${check ? "verified" : "built"}`)
