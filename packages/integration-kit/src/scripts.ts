// A script an agent asks to run from a file (an execute_code tool's `path`): read byte for byte, so its code never
// goes through a JSON or JavaScript string.

import { readFileSync, statSync } from "node:fs"
import { resolve } from "node:path"

// The longest script read from a file (900 KiB): hosts read requests of at most 1 MiB. A literal, so that a
// bundle importing the kit without reading scripts drops it.
export const SCRIPT_MAX_BYTES = 921_600

// A Python script on disk (absolute, or relative to the Location's directory), as UTF-8 text.
export function readScript(path: string, base?: string) {
  const file = resolve(base ?? process.cwd(), path)
  let bytes: Buffer
  try {
    if (statSync(file).size > SCRIPT_MAX_BYTES)
      throw new Error(`${file} is over ${SCRIPT_MAX_BYTES} bytes: split it, or have it exec() its parts.`)
    bytes = readFileSync(file)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") throw new Error(`No script ${file}.`)
    throw error
  }
  const code = new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^﻿/, "")
  return { path: file, code }
}
