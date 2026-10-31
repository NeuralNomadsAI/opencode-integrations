import type { JsonObject } from "./types.ts"

export function ensureJsonObject(input: unknown, label = "params"): JsonObject {
  if (input === undefined || input === null) {
    return {}
  }

  if (typeof input !== "object" || Array.isArray(input)) {
    throw new Error(`${label} must be a JSON object`)
  }

  return input as JsonObject
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
