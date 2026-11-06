import { AsyncLocalStorage } from "node:async_hooks"
import { setTimeout } from "node:timers/promises"

// One signal per dispatch, including composed calls; parallel executions never share it.
const executionSignal = new AsyncLocalStorage<AbortSignal | undefined>()

export function currentSignal() {
  return executionSignal.getStore()
}

export function throwIfCancelled() {
  currentSignal()?.throwIfAborted()
}

export function withExecutionSignal<T>(signal: AbortSignal | undefined, execute: () => Promise<T>) {
  return executionSignal.run(signal, async () => {
    throwIfCancelled()
    const result = await execute()
    throwIfCancelled()
    return result
  })
}

export async function sleep(ms: number) {
  await setTimeout(ms, undefined, { signal: currentSignal() })
}
