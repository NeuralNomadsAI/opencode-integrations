// The installed part: the add-on addons/opencode_link of a Godot project, enabled in its project.godot
// ([editor_plugins] enabled). Godot 4 has no editor-wide add-ons: an editor plugin belongs to its project.

import { createHash, randomUUID } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { readStoredZip } from "../../../../packages/integration-kit/src/zip.ts"
import type { HostInspection, InstallHostOutput, IntegrationMarker } from "./kit.ts"
import { godotProject, packageRoot } from "./paths.ts"

export const ADDON = join("addons", "opencode_link")
export const PLUGIN_CFG = "res://addons/opencode_link/plugin.cfg"
// The add-on's own plugin.cfg says it is this integration's.
const OURS = /^name="OpenCode Link"$/m

const versionOf = (cfg: string) => /^version="([^"]+)"$/m.exec(cfg)?.[1]

function readText(file: string) {
  try {
    return readFileSync(file, "utf8")
  } catch {
    return undefined
  }
}

// The [editor_plugins] section's enabled list, or null when it does not name the add-on.
export function enabledIn(projectGodot: string) {
  const section = /^\[editor_plugins\][^\n]*\n([\s\S]*?)(?=^\[|(?![\s\S]))/m.exec(projectGodot)?.[1] ?? ""
  const list = /^enabled=PackedStringArray\(([^)]*)\)/m.exec(section)?.[1] ?? ""
  return [...list.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((match) => match[1])
}

// project.godot with the add-on enabled, keeping every other line as it was.
export function enablePlugin(projectGodot: string): string {
  const eol = projectGodot.includes("\r\n") ? "\r\n" : "\n"
  const entry = `"${PLUGIN_CFG}"`
  const header = /^\[editor_plugins\][^\n]*\n/m.exec(projectGodot)
  if (!header) {
    const base = projectGodot.endsWith("\n") || !projectGodot ? projectGodot : `${projectGodot}${eol}`
    return `${base}${eol}[editor_plugins]${eol}${eol}enabled=PackedStringArray(${entry})${eol}`
  }
  const start = header.index + header[0].length
  const rest = projectGodot.slice(start)
  const end = /^\[/m.exec(rest)?.index ?? rest.length
  const section = rest.slice(0, end)
  const line = /^enabled=PackedStringArray\(([^)]*)\)/m.exec(section)
  const updated = line
    ? section.replace(line[0], `enabled=PackedStringArray(${line[1]?.trim() ? `${line[1].trim()}, ` : ""}${entry})`)
    : `${eol}enabled=PackedStringArray(${entry})${eol}${section.replace(/^(\r?\n)+/, "")}`
  return projectGodot.slice(0, start) + updated + rest.slice(end)
}

// The installed version, when the add-on is in the project and enabled there.
export function inspectHost(project?: string): HostInspection {
  const folder = godotProject(project)
  if (!folder) return { kind: "not-inspected" }
  const cfg = readText(join(folder, ADDON, "plugin.cfg"))
  const settings = readText(join(folder, "project.godot")) ?? ""
  const version = cfg && OURS.test(cfg) ? versionOf(cfg) : undefined
  if (!version || !enabledIn(settings).includes(PLUGIN_CFG)) return { kind: "not-found" }
  return { kind: "found", version }
}

export function installHost(marker: IntegrationMarker, project?: string): InstallHostOutput {
  const folder = godotProject(project)
  if (!folder)
    return {
      outcome: "refused",
      detail:
        "Name a Godot project folder (the one with project.godot): godot_install_host project, or the conversation's folder.",
    }
  const host = marker.host
  if (!host) return { outcome: "refused" }
  const archive = readFileSync(join(packageRoot, host.payload))
  if (createHash("sha256").update(archive).digest("hex") !== host.sha256)
    return { outcome: "failed", detail: "Host payload checksum mismatch." }
  const entries = readStoredZip(archive)
  const target = join(folder, ADDON)
  const cfg = readText(join(target, "plugin.cfg"))
  if (existsSync(target) && !(cfg && OURS.test(cfg)))
    return { outcome: "refused", detail: `${target} exists and is not OpenCode Link: it was left untouched.` }
  const settingsFile = join(folder, "project.godot")
  const settings = readFileSync(settingsFile, "utf8")
  const enabled = enabledIn(settings).includes(PLUGIN_CFG)
  const prefix = "addons/opencode_link/"
  const same = entries.every((entry) => {
    const existing = join(target, entry.name.slice(prefix.length))
    return existsSync(existing) && readFileSync(existing).equals(entry.data)
  })
  if (same && enabled) return { outcome: "unchanged" }
  const staging = join(folder, "addons", `.opencode_link-${randomUUID()}`)
  const previous = join(folder, "addons", `.opencode_link-old-${randomUUID()}`)
  const temporary = `${settingsFile}-${randomUUID()}`
  let movedPrevious = false
  let movedNew = false
  try {
    if (!same) {
      for (const entry of entries) {
        const file = join(staging, entry.name.slice(prefix.length))
        mkdirSync(dirname(file), { recursive: true })
        writeFileSync(file, entry.data)
      }
      if (existsSync(target)) {
        renameSync(target, previous)
        movedPrevious = true
      }
      renameSync(staging, target)
      movedNew = true
    }
    if (!enabled) {
      writeFileSync(temporary, enablePlugin(settings))
      if (readFileSync(settingsFile, "utf8") !== settings)
        throw new Error("Project settings changed during installation.")
      renameSync(temporary, settingsFile)
    }
  } catch {
    try {
      if (movedNew) rmSync(target, { recursive: true, force: true })
      if (movedPrevious) renameSync(previous, target)
    } catch {
      return {
        outcome: "failed",
        detail: "Installation failed; the previous add-on may need restoring from addons/.opencode_link-old-*.",
      }
    }
    return {
      outcome: "failed",
      detail: "Could not write the add-on or project settings. Close the editor before retrying.",
    }
  } finally {
    rmSync(staging, { recursive: true, force: true })
    rmSync(temporary, { force: true })
  }
  rmSync(previous, { recursive: true, force: true })
  return {
    outcome: cfg && !same ? "updated" : "installed",
    detail:
      "Restart this project's Godot editor if it is open: OpenCode Link offers the project to OpenCode when the editor starts.",
  }
}
