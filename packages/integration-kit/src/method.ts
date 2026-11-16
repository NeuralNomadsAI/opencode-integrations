// An integration's working method, attached once to a conversation when it attaches the application (a native
// instructions entry, not a skill). The text belongs to the integration; the kit only attaches it.

export type EntryPut = (input: { sessionID: string; key: string; value: string }) => Promise<void>

// Sessions each method is attached to, per daemon process (OpenCode loads the plugin once per Location).
function attachedFor(key: string): Set<string> {
  const store = globalThis as unknown as Record<symbol, Set<string> | undefined>
  const slot = Symbol.for(`neuralnomads.method.${key}`)
  store[slot] ??= new Set<string>()
  return store[slot]
}

// A failure never fails the application call: the method is tried again at the next one.
export function methodAttacher(put: EntryPut | undefined, method: { key: string; text: string }) {
  const attached = attachedFor(method.key)
  return async (sessionID: string) => {
    if (!put || !sessionID || attached.has(sessionID)) return
    attached.add(sessionID)
    try {
      await put({ sessionID, key: method.key, value: method.text })
    } catch {
      attached.delete(sessionID)
    }
  }
}

export function resetMethodForTests(key: string) {
  attachedFor(key).clear()
}
