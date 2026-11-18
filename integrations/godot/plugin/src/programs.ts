// The fixed GDScript programs of godot_inspect, godot_verify, godot_save and the working copies (programs/*.gd in the
// package). They run through OpenCode Link's code execution, read only, with their parameters as data (PARAMS): the
// plugin's own code, never code from the model, so they ask no permission. background.gd is the main loop of a
// background Godot (godot --headless --script) that reads a scene file.

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { programsDir } from "./paths.ts"

const read = (file: string) => readFileSync(join(programsDir, file), "utf8").replaceAll("\r\n", "\n")
const checks = read("checks.gd")
const PROGRAMS = {
  inspect: read("inspect.gd"),
  verify: `${read("verify.gd")}\n${checks}`,
  save: read("save.gd"),
  copy: read("copy.gd"),
}

export type ProgramName = keyof typeof PROGRAMS

export const programCode = (name: ProgramName) => PROGRAMS[name]

// The background Godot's main loop script.
export const backgroundScript = () => `extends SceneTree\n\n${read("background.gd")}\n${checks}`
