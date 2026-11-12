// The working method, attached once to a conversation when it attaches a Blender (a native instructions
// entry, not a skill): what to check before writing code, and how to verify a step.

export const METHOD_KEY = "blender.method"
export const METHOD_TEXT = [
  "Working on the open Blender:",
  "- Read its version first (blender_hosts, blender_inspect): your knowledge of its Python API may be older than it.",
  "- Before writing code for modifiers or Geometry Nodes inputs, the sequencer, compositing, Grease Pencil, animation",
  "  or render output, ask Blender itself with blender_inspect (type, operator or live data).",
  "- If it is still unclear, read the official docs and the Python API release notes of THIS version (links in",
  "  blender_inspect's result).",
  "- Verify each step by its result, not by the absence of an error: read back what changed, and use blender_verify",
  "  for anything that renders (black or transparent image, camera used, sequencer and compositing state).",
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
