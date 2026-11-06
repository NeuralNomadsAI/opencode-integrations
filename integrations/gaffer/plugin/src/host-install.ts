import { createHash } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, isAbsolute, join } from "node:path"
import type { InstallHostOutput, IntegrationMarker } from "./kit.ts"
import { packageRoot, stateRoot } from "./paths.ts"
import { readStoredZip } from "./zip.ts"

const HEADER = "# Managed by neuralnomads.gaffer\n"
export async function installHost(
  marker: IntegrationMarker,
  options: Record<string, unknown> = {},
): Promise<InstallHostOutput> {
  const startup =
    options.hostStartupDir ??
    process.env.NEURALNOMADS_GAFFER_HOST_STARTUP_DIR ??
    join(homedir(), "gaffer", "startup", "gui")
  if (typeof startup !== "string" || !isAbsolute(startup))
    return { outcome: "refused", detail: "hostStartupDir must be an absolute Gaffer GUI startup directory." }
  const loader = join(startup, "neuralnomadsOpenCode.py")
  if (existsSync(loader) && !readFileSync(loader, "utf8").startsWith(HEADER))
    return { outcome: "refused", detail: "The existing startup file is not managed by this integration." }
  const host = marker.host
  if (!host) return { outcome: "refused" }
  const bytes = readFileSync(join(packageRoot, host.payload))
  if (createHash("sha256").update(bytes).digest("hex") !== host.sha256)
    return { outcome: "failed", detail: "Host payload checksum mismatch." }
  const entries = readStoredZip(bytes)
  const target = join(stateRoot(), "hosts", host.sha256)
  for (const { name, data } of entries) {
    const file = join(target, name)
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, data)
  }
  const source =
    HEADER +
    `import runpy\nrunpy.run_path(${JSON.stringify(join(target, "startup", "gui", "openCode.py").replaceAll("\\", "/"))}, init_globals={"application": application})\n`
  if (existsSync(loader) && readFileSync(loader, "utf8") === source) return { outcome: "unchanged" }
  const updated = existsSync(loader)
  mkdirSync(startup, { recursive: true })
  const temporary = `${loader}.${process.pid}.tmp`
  writeFileSync(temporary, source)
  renameSync(temporary, loader)
  return { outcome: updated ? "updated" : "installed" }
}
