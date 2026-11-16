// The working method, attached once to a conversation when it attaches a Blender (a native instructions
// entry, not a skill): what to check before writing code, and how to verify a step.

import { type EntryPut, methodAttacher as kitAttacher, resetMethodForTests as resetKit } from "./kit.ts"

export const METHOD_KEY = "blender.method"
export const METHOD_TEXT = [
  "Working on the open Blender:",
  "- Your knowledge of its Python API may be older than it: read its version (blender_hosts, blender_inspect).",
  "- Inspect the exact element before coding it (blender_inspect search, type, operator, node type in its tree, data).",
  "- After an error, read its hint and inspect again; never guess twice. A refused operator's hint names the context that passes.",
  "- Turn each requirement of your task into a check (value, comparison, frames; visible for what must be seen).",
  "- The saved file is what you deliver: save, then run every check on it with blender_verify saved: true.",
  "- Report each requirement with its measured value from the file. A failed or unchecked one is not done: say so.",
  "- Still unclear: the docs and Python API release notes of THIS version (links in blender_inspect).",
  "- Write down what you discover (a recent change, a silent failure) in your report, with the Blender version.",
].join("\n")

export type { EntryPut }

// The kit attaches it once per conversation; a failure is tried again at the next Blender call.
export const methodAttacher = (put: EntryPut | undefined) => kitAttacher(put, { key: METHOD_KEY, text: METHOD_TEXT })

export function resetMethodForTests() {
  resetKit(METHOD_KEY)
}
