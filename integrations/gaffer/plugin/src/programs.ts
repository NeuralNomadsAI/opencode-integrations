// The fixed programs of gaffer_inspect, gaffer_verify, gaffer_render and gaffer_save (programs/*.py in the
// package). They run through the host's code execution, read only, with their parameters as JSON data: the
// plugin's own code, never code from the model, so they ask no permission.

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { programsDir } from "./paths.ts"

const read = (file: string) => readFileSync(join(programsDir, file), "utf8")
const PROGRAMS = {
  inspect: read("inspect.py"),
  verify: read("verify.py"),
  verify_saved: read("verify_saved.py"),
  save: read("save.py"),
  render: read("render.py"),
}
// The background Gaffer's script: render.py's job, and for verify_saved the verify program itself.
const JOBS: Partial<Record<keyof typeof PROGRAMS, string>> = {
  render: read("render_job.py"),
  verify_saved: PROGRAMS.verify,
}

export type ProgramName = keyof typeof PROGRAMS

export function programCode(name: ProgramName, params: unknown) {
  const job = JOBS[name] === undefined ? "" : `JOB = ${JSON.stringify(JOBS[name])}\n`
  return `import json\nPARAMS = json.loads(${JSON.stringify(JSON.stringify(params))})\n${job}${PROGRAMS[name]}`
}
