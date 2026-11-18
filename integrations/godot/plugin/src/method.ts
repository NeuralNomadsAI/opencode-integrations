// The working method, attached once to a conversation when it attaches a Godot editor (a native instructions entry,
// not a skill): what to check before writing code, and how to verify a step.

import { type EntryPut, methodAttacher as kitAttacher, resetMethodForTests as resetKit } from "./kit.ts"

export const METHOD_KEY = "godot.method"
export const METHOD_TEXT = [
  "Working on the attached Godot project:",
  "- Your knowledge of Godot's API may be older than it: read its version (godot_hosts, godot_inspect).",
  "- Inspect the exact element before coding it (godot_inspect search, class, node, tree).",
  "- After an error, read its hint and inspect again; never guess twice.",
  "- Turn each requirement of your task into a measured check (godot_verify criteria; frames for runtime values).",
  "- The saved files are what you deliver: save, then run every check on them with godot_verify saved: true.",
  "- Report each requirement with its measured value from the files. A failed or unchecked one is not done: say so.",
  "- Still unclear: the Godot documentation of THIS version (links in godot_inspect).",
].join("\n")

export type { EntryPut }

export const methodAttacher = (put: EntryPut | undefined) => kitAttacher(put, { key: METHOD_KEY, text: METHOD_TEXT })

export function resetMethodForTests() {
  resetKit(METHOD_KEY)
}
