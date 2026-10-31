import { type Dirent, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

import type { MethodRegistry } from "./method-registry.ts"

export type CoherenceCheckOptions = {
  integration: string
  // Tool name prefix used in skills (e.g. "m365_", "blender_", "unreal_").
  // Used to filter tool mentions parsed from skill SKILL.md files.
  toolPrefix: string
  // Path to <package>/skills/.
  skillsDir: string
  // Pre-loaded method registry of the integration.
  registry: MethodRegistry
  // Optional: list of method names that have a runtime handler. When provided,
  // the check verifies execution.tool of every public method maps to a handler.
  // Hostless integrations typically supply this; host-backed integrations can omit
  // it because every manifest is dispatched generically.
  handledMethodNames?: string[]
}

type SkillSummary = {
  name: string
  filePath: string
  mentionedMethods: string[]
}

function getSkillSummaries(skillsDir: string, toolPrefix: string, knownMethodNames: Set<string>): SkillSummary[] {
  const skills: SkillSummary[] = []
  let entries: Dirent[] = []
  try {
    entries = readdirSync(skillsDir, { withFileTypes: true })
  } catch {
    return skills
  }

  // Build a regex that matches `<prefix><name>` inside backticks.
  const escapedPrefix = toolPrefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const pattern = new RegExp("`(" + escapedPrefix + "[a-z0-9_]+)`", "g")

  for (const entry of entries) {
    if (!entry.isDirectory()) continue

    const filePath = join(skillsDir, entry.name, "SKILL.md")
    let content: string
    try {
      content = readFileSync(filePath, "utf8")
    } catch {
      continue
    }

    const mentioned = Array.from(content.matchAll(pattern))
      .map((match) => match[1] ?? "")
      .filter((name) => toolPrefix || knownMethodNames.has(name))
    skills.push({
      name: entry.name,
      filePath,
      mentionedMethods: Array.from(new Set(mentioned)).sort((a, b) => a.localeCompare(b)),
    })
  }

  return skills.sort((a, b) => a.name.localeCompare(b.name))
}

export type CoherenceReport = {
  ok: boolean
  integration: string
  counts: {
    manifests: number
    publicMethods: number
    handledMethods: number
    skills: number
  }
  publicMethods: string[]
  handledMethods: string[]
  skills: { name: string; methods: string[] }[]
  warnings: string[]
  errors: string[]
}

export function runCoherenceCheck(options: CoherenceCheckOptions): CoherenceReport {
  const manifests = options.registry.list()
  const publicMethods = options.registry.publicMethods()
  const manifestNames = new Set(manifests.map((m) => m.name))
  const publicMethodNames = new Set(publicMethods.map((m) => m.name))
  const skills = getSkillSummaries(options.skillsDir, options.toolPrefix, publicMethodNames)
  const mentionedMethodNames = new Set(skills.flatMap((s) => s.mentionedMethods))

  const handledMethods = options.handledMethodNames
    ? options.handledMethodNames.slice().sort((a, b) => a.localeCompare(b))
    : publicMethods.map((m) => m.name)
  const handledMethodNames = new Set(handledMethods)

  const errors: string[] = []
  const warnings: string[] = []

  for (const manifest of publicMethods) {
    if (options.handledMethodNames && !handledMethodNames.has(manifest.name)) {
      errors.push(`Public method ${manifest.name} has no runtime handler.`)
    }

    if (!mentionedMethodNames.has(manifest.name)) {
      warnings.push(`Public method ${manifest.name} is not mentioned in any skill.`)
    }

    const tool = manifest.execution.tool
    if (tool !== undefined && tool !== manifest.name) {
      errors.push(`Public method ${manifest.name} must expose execution.tool = ${manifest.name} (found '${tool}').`)
    }
  }

  if (options.handledMethodNames) {
    for (const handledMethod of handledMethods) {
      if (!manifestNames.has(handledMethod)) {
        errors.push(`Runtime handler ${handledMethod} has no matching method manifest.`)
      }
    }
  }

  for (const skill of skills) {
    if (skill.mentionedMethods.length === 0) {
      warnings.push(`Skill ${skill.name} does not mention any ${options.toolPrefix}* tools.`)
      continue
    }

    for (const methodName of skill.mentionedMethods) {
      if (!publicMethodNames.has(methodName)) {
        errors.push(`Skill ${skill.name} mentions unknown or non-public tool ${methodName}.`)
      }
    }
  }

  for (const manifest of manifests) {
    const verifyMethod =
      typeof manifest.verify?.method === "string" && manifest.verify.method.trim()
        ? manifest.verify.method.trim()
        : null
    if (verifyMethod && !manifestNames.has(verifyMethod)) {
      errors.push(`Method ${manifest.name} references missing verify.method ${verifyMethod}.`)
    }

    const executionTool =
      typeof manifest.execution?.tool === "string" && manifest.execution.tool.trim()
        ? manifest.execution.tool.trim()
        : null
    if (executionTool && options.handledMethodNames && !handledMethodNames.has(executionTool)) {
      errors.push(`Method ${manifest.name} references missing execution.tool handler ${executionTool}.`)
    }
  }

  return {
    ok: errors.length === 0,
    integration: options.integration,
    counts: {
      manifests: manifests.length,
      publicMethods: publicMethods.length,
      handledMethods: handledMethods.length,
      skills: skills.length,
    },
    publicMethods: publicMethods.map((m) => m.name),
    handledMethods,
    skills: skills.map((s) => ({ name: s.name, methods: s.mentionedMethods })),
    warnings,
    errors,
  }
}

// Prints the report and returns the process exit code. The caller supplies its
// own paths, so the kit stays free of side effects.
export function printCoherenceReport(report: CoherenceReport): number {
  if (!report.ok) {
    console.error(JSON.stringify(report, null, 2))
    return 1
  }
  console.log(JSON.stringify(report, null, 2))
  return 0
}
