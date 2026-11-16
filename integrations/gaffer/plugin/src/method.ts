// The working method, attached once to a conversation when it attaches a Gaffer script (a native instructions
// entry, not a skill): what to check before writing code, and how to verify a step.

import { type EntryPut, methodAttacher as kitAttacher, resetMethodForTests as resetKit } from "./kit.ts"

export const METHOD_KEY = "gaffer.method"
export const METHOD_TEXT = [
  "Working on the attached Gaffer script:",
  "- Your knowledge of Gaffer's API may be older than it: read its version (gaffer_hosts, gaffer_inspect).",
  "- Inspect the exact element before coding it (gaffer_inspect search, type with its plugs, node, a Python symbol).",
  "- After an error, read its hint and inspect again; never guess twice.",
  "- Compute what you build (its output plugs) and read its errors; a node whose output fails is not done.",
  "- Render with gaffer_render: a background Gaffer runs it, so a renderer crash leaves this Gaffer open.",
  "- Turn each requirement of your task into a measured check (gaffer_verify criteria).",
  "- The saved file is what you deliver: save, then run every check on it with gaffer_verify saved: true.",
  "- Report each requirement with its measured value from the file. A failed or unchecked one is not done: say so.",
  "- Still unclear: the Gaffer documentation of THIS version (links in gaffer_inspect).",
].join("\n")

export type { EntryPut }

export const methodAttacher = (put: EntryPut | undefined) => kitAttacher(put, { key: METHOD_KEY, text: METHOD_TEXT })

export function resetMethodForTests() {
  resetKit(METHOD_KEY)
}
