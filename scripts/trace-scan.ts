// Usage: node scripts/trace-scan.ts [--tree] [--packs] [--history] [--require-fingerprints] [path...]
//   --tree     every tracked or unignored file of the checkout, archives opened
//   --packs    the `npm pack` tarball of every workspace package
//   --history  every commit of every ref (`git log --all -p`), messages and authors included
//   path...    extra files or directories
// Entra GUID fingerprints come from TRACE_SCAN_FINGERPRINTS (SHA-256 hex list);
// without it the scan is partial, and --require-fingerprints makes that fatal.
import { execFileSync, execSync } from "node:child_process"
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, relative } from "node:path"

import { parseFingerprints, TraceScanner } from "./trace-scan-lib.ts"

const args = process.argv.slice(2)
const flags = new Set(args.filter((arg) => arg.startsWith("--")))
const paths = args.filter((arg) => !arg.startsWith("--"))
for (const flag of flags) {
  if (!["--tree", "--packs", "--history", "--require-fingerprints"].includes(flag)) {
    console.error(`trace-scan: unknown option ${flag}`)
    process.exit(2)
  }
}

const scanner = new TraceScanner(parseFingerprints(process.env.TRACE_SCAN_FINGERPRINTS))
const run = (command: string, commandArgs: string[], cwd = process.cwd()) =>
  execFileSync(command, commandArgs, { cwd, maxBuffer: 1 << 30, stdio: ["ignore", "pipe", "inherit"] })

function scanPath(path: string, label = path) {
  if (statSync(path).isDirectory()) {
    for (const entry of readdirSync(path)) {
      if (entry === ".git" || entry === "node_modules") continue
      scanPath(join(path, entry), `${label}/${entry}`)
    }
  } else scanner.scanBytes(label, readFileSync(path))
}

const root = run("git", ["rev-parse", "--show-toplevel"]).toString("utf8").trim()

if (flags.has("--tree")) {
  const files = run("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], root)
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
  for (const file of files) {
    try {
      scanner.scanBytes(file, readFileSync(join(root, file)))
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error
    }
  }
}

if (flags.has("--packs")) {
  const destination = mkdtempSync(join(tmpdir(), "trace-scan-pack-"))
  try {
    // npm is a .cmd shim on Windows, which needs a shell; the only variable is our own temporary directory.
    const output = execSync(`npm pack --workspaces --json --pack-destination "${destination}"`, {
      cwd: root,
      maxBuffer: 1 << 30,
      stdio: ["ignore", "pipe", "inherit"],
    }).toString("utf8")
    const packs = JSON.parse(output) as { filename: string }[]
    for (const pack of packs) {
      const file = join(destination, pack.filename.replace(/^@/, "").replace(/\//g, "-"))
      scanner.scanBytes(`npm pack ${pack.filename}`, readFileSync(file))
    }
  } finally {
    rmSync(destination, { recursive: true, force: true })
  }
}

if (flags.has("--history")) {
  const log = run("git", ["log", "--all", "-p", "--text", "--format=fuller", "--no-color", "--no-ext-diff"], root)
  scanner.scanText("git history", log.toString("latin1"))
  const refs = run("git", ["for-each-ref", "--format=%(refname)%0a%(contents)"], root)
  scanner.scanText("git refs and tag messages", refs.toString("latin1"))
}

for (const path of paths) scanPath(path, relative(process.cwd(), path) || path)

for (const finding of scanner.findings) {
  console.error(`trace-scan: ${finding.location}:${finding.line}: ${finding.rule}`)
}
for (const error of scanner.errors) console.error(`trace-scan: ${error}`)
if (scanner.partial) {
  console.warn(
    "trace-scan: partial scan, TRACE_SCAN_FINGERPRINTS is not set: known Entra GUIDs were not checked (see CONTRIBUTING.md)",
  )
}
const missing = scanner.partial && flags.has("--require-fingerprints")
if (missing) console.error("trace-scan: --require-fingerprints: the full scan needs TRACE_SCAN_FINGERPRINTS")
const failed = scanner.findings.length > 0 || scanner.errors.length > 0 || missing
console.log(
  `trace-scan: ${failed ? "FAILED" : "ok"} (${scanner.scannedEntries} entries, ${scanner.findings.length} finding(s), ${scanner.partial ? "partial" : "full"} scan)`,
)
process.exitCode = failed ? 1 : 0
