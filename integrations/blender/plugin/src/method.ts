// The working method, attached once to a conversation when it attaches a Blender (a native instructions
// entry, not a skill): what to check before writing code, and how to verify a step.

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

export type EntryPut = (input: { sessionID: string; key: string; value: string }) => Promise<void>

// Sessions the method is attached to, per daemon process (OpenCode loads the plugin once per Location).
const shared = globalThis as { __neuralnomadsBlenderMethod?: Set<string> }
shared.__neuralnomadsBlenderMethod ??= new Set<string>()
const attached = shared.__neuralnomadsBlenderMethod

// A failure never fails the Blender call: the method is tried again at the next one.
export function methodAttacher(put: EntryPut | undefined) {
  return async (sessionID: string) => {
    if (!put || !sessionID || attached.has(sessionID)) return
    attached.add(sessionID)
    try {
      await put({ sessionID, key: METHOD_KEY, value: METHOD_TEXT })
    } catch {
      attached.delete(sessionID)
    }
  }
}

export function resetMethodForTests() {
  attached.clear()
}
