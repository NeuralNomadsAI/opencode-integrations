// The working method, attached once to a conversation when it attaches a Houdini (a native instructions entry,
// not a skill): what to check before writing code, and how to verify a step.

import { type EntryPut, methodAttacher as kitAttacher, resetMethodForTests as resetKit } from "./kit.ts"

export const METHOD_KEY = "houdini.method"
export const METHOD_TEXT = [
  "Working on the open Houdini:",
  "- Your knowledge of hou may be older than it: read its version and license (houdini_hosts, houdini_inspect).",
  "- Inspect the exact element before coding it (houdini_inspect search, type with its parms, node, hou symbol).",
  "- After an error, read its hint and inspect again; never guess twice. A parm that does not exist returns None.",
  "- Cook what you build and read its errors and warnings; a node in error is not done.",
  "- Render and cook long jobs with houdini_render: a background hython runs them, so a renderer crash leaves this Houdini open.",
  "- Turn each requirement of your task into a measured check (houdini_verify criteria).",
  "- The saved file is what you deliver: save, then run every check on it with houdini_verify saved: true.",
  "- Report each requirement with its measured value from the file. A failed or unchecked one is not done: say so.",
  "- Still unclear: the SideFX documentation of THIS version (links in houdini_inspect).",
].join("\n")

export type { EntryPut }

export const methodAttacher = (put: EntryPut | undefined) => kitAttacher(put, { key: METHOD_KEY, text: METHOD_TEXT })

export function resetMethodForTests() {
  resetKit(METHOD_KEY)
}
