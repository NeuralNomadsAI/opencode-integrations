// Regenerates the host artifacts: catalog.json and opencode_service.py in the add-on, the payload
// archive, and the marker's payload name and sha256. `--check` verifies them without writing.
// Usage: node scripts/build-host.ts [--check]

import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"

import {
  addonRoot,
  addonVersion,
  catalogBytes,
  payloadBytes,
  payloadName,
  pluginRoot,
  serviceBytes,
  sha256,
} from "./host-payload.ts"

const check = process.argv.includes("--check")
const packagePath = join(pluginRoot, "package.json")
const pkg = JSON.parse(readFileSync(packagePath, "utf8"))
const version = addonVersion()
const problems: string[] = []

if (pkg.version !== version) problems.push(`package version ${pkg.version} differs from the add-on version ${version}`)

const payload = payloadBytes()
const targets: Array<[string, Buffer]> = [
  [join(addonRoot, "catalog.json"), catalogBytes()],
  [join(addonRoot, "opencode_service.py"), serviceBytes()],
  [join(pluginRoot, ...payloadName(version).split("/")), payload],
]
for (const [file, bytes] of targets) {
  const current = existsSync(file) ? readFileSync(file) : null
  if (current?.equals(bytes)) continue
  if (check) problems.push(`${file} is out of date`)
  else {
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, bytes)
  }
}

const host = pkg.opencodeIntegration.host
const digest = sha256(payload)
if (host.payload !== payloadName(version) || host.sha256 !== digest) {
  if (check) problems.push("the marker's host.payload or host.sha256 is out of date")
  else {
    host.payload = payloadName(version)
    host.sha256 = digest
    writeFileSync(packagePath, `${JSON.stringify(pkg, null, 2)}\n`)
  }
}

if (!check) {
  // Older payload archives leave the package.
  const folder = join(pluginRoot, "host", "blender")
  for (const name of existsSync(folder) ? readdirSync(folder) : []) {
    if (name.endsWith(".zip") && `host/blender/${name}` !== payloadName(version)) rmSync(join(folder, name))
  }
}

if (problems.length > 0) {
  for (const problem of problems) console.error(`build-host: ${problem}`)
  console.error("build-host: run `npm run build:host --workspace @neuralnomads/opencode-blender` and commit the result")
  process.exitCode = 1
} else {
  console.log(`build-host: ${check ? "up to date" : "written"} (OpenCode Link ${version}, sha256 ${digest})`)
}
