// The working method, attached once to a conversation when it attaches a Blender (a native instructions
// entry, not a skill): what to check before writing code, and how to verify a step.

export const METHOD_KEY = "blender.method"
export const METHOD_TEXT = [
  "Working on the open Blender:",
  "- Your knowledge of its Python API may be older than it: read its version (blender_hosts, blender_inspect).",
  "- Inspect the exact element before coding it: blender_inspect search (words) finds where a setting lives; then its type, operator, node type in its tree or live data.",
  "- After an error, read its hint (closest names, real signature, keys, sockets) and inspect again; never guess twice.",
  "- An operator that needs a context gets it from bpy.context.temp_override(window, area, region, id, ...).",
  "- Before finishing, turn your acceptance criteria into checks (value, comparison, frames): blender_verify criteria, and visible for what must be seen. Report measured values; a failed check is not done.",
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
