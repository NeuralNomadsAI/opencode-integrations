// The fixed programs of blender_inspect, blender_verify, blender_render and blender_save (programs/*.py in
// the package). They run through the host's code execution, read only, with their parameters as JSON data:
// the plugin's own code, never code from the model, so they ask no permission (spec SEC-3).

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { programsDir } from "./paths.ts"

const read = (file: string) => readFileSync(join(programsDir, file), "utf8")
const PROGRAMS = {
  inspect: read("inspect.py"),
  verify: read("verify.py"),
  render: read("render.py"),
  save: read("save.py"),
}
// The background render Blender's script, which render.py writes next to its log.
const RENDER_JOB = read("render_job.py")

export type ProgramName = keyof typeof PROGRAMS

export function programCode(name: ProgramName, params: unknown) {
  const job = name === "render" ? `JOB = ${JSON.stringify(RENDER_JOB)}\n` : ""
  return `import json\nPARAMS = json.loads(${JSON.stringify(JSON.stringify(params))})\n${job}${PROGRAMS[name]}`
}
