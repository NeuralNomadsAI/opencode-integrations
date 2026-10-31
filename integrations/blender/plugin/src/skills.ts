// The plugin's skills (skills/<id>/SKILL.md), loaded through ctx.skill.transform (PKG-2).

import { existsSync, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

import { skillsDir } from "./paths.ts"

export type SkillInfo = {
  id: string
  name: string
  description?: string
  autoinvoke?: boolean
  path: string
  content: string
}

// A front-matter header of `key: value` lines; `autoinvoke: false` keeps reference skills off the model's list.
export function parseSkill(id: string, file: string, source: string): SkillInfo {
  const text = source.replace(/\r\n/g, "\n")
  const header = /^---\n([\s\S]*?)\n---\n?/.exec(text)
  if (!header) throw new Error(`${id}: SKILL.md has no front-matter header`)
  const fields = new Map<string, string>()
  for (const line of (header[1] ?? "").split("\n")) {
    const match = /^([a-z][a-z0-9_-]*):\s*(.*)$/i.exec(line)
    if (match?.[1]) fields.set(match[1], (match[2] ?? "").trim())
  }
  const skill: SkillInfo = {
    id,
    name: fields.get("name") ?? id,
    path: file,
    content: text.slice(header[0].length),
  }
  const description = fields.get("description")
  if (description) skill.description = description
  if (fields.get("autoinvoke") === "false") skill.autoinvoke = false
  return skill
}

export function loadSkills(root = skillsDir): SkillInfo[] {
  if (!existsSync(root)) return []
  return readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(join(root, entry.name, "SKILL.md")))
    .map((entry) => {
      const file = join(root, entry.name, "SKILL.md")
      return parseSkill(entry.name, file, readFileSync(file, "utf8"))
    })
    .sort((a, b) => a.id.localeCompare(b.id))
}
