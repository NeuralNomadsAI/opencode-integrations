import { readFileSync } from "node:fs"
import { createRequire } from "node:module"

import type { ErrorObject, ValidateFunction } from "ajv"

// ajv ships CommonJS; load its 2020-12 build through require for NodeNext.
const require = createRequire(import.meta.url)
const Ajv2020 = require("ajv/dist/2020").default as typeof import("ajv").default

const schemaDir = new URL("../../../spec/schemas/", import.meta.url)

type JsonSchema = Record<string, unknown>

export function loadSchema(name: string): JsonSchema {
  return JSON.parse(readFileSync(new URL(name, schemaDir), "utf8"))
}

// Conditional `required` clauses name properties declared at the top level.
const ajv = new Ajv2020({ allErrors: true, strict: true, strictRequired: false })

export const validateMarkerSchema: ValidateFunction = ajv.compile(loadSchema("marker.schema.json"))
export const validateCatalogSchema: ValidateFunction = ajv.compile(loadSchema("catalog.schema.json"))

export const rpcSchema = loadSchema("rpc.schema.json")
export const hostSchema = loadSchema("host.schema.json")

function definitions(schema: JsonSchema): Record<string, JsonSchema> {
  return schema.$defs as Record<string, JsonSchema>
}

export const RPC_DEFINITIONS = Object.keys(definitions(rpcSchema))

// The strict form of a reader schema: every object that names its properties
// accepts no other property. Producers of this repository emit the strict form.
export function strictSchema(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(strictSchema)
  if (typeof schema !== "object" || schema === null) return schema
  const copy: JsonSchema = {}
  for (const [key, value] of Object.entries(schema)) copy[key] = strictSchema(value)
  if (copy.properties && copy.additionalProperties === undefined) copy.additionalProperties = false
  return copy
}

const cache = new Map<string, ValidateFunction>()

function compiled(key: string, schema: () => unknown): ValidateFunction {
  let validate = cache.get(key)
  if (!validate) {
    validate = ajv.compile(schema() as JsonSchema)
    cache.set(key, validate)
  }
  return validate
}

function rpcDefinitionSchema(name: string): JsonSchema {
  const schema = definitions(rpcSchema)[name]
  if (!schema) throw new Error(`rpc.schema.json has no definition ${name}`)
  return schema
}

// Validator of one RPC definition (`status.output`, `advertise.input`, `hosts_changed.event`…).
export function rpcValidator(name: string, mode: "reader" | "strict" = "reader"): ValidateFunction {
  return compiled(`rpc:${mode}:${name}`, () =>
    mode === "strict" ? strictSchema(rpcDefinitionSchema(name)) : rpcDefinitionSchema(name),
  )
}

export function hostValidator(name: "request" | "response" | "hello"): ValidateFunction {
  return compiled(`host:${name}`, () => {
    const schema = definitions(hostSchema)[name]
    if (!schema) throw new Error(`host.schema.json has no definition ${name}`)
    return schema
  })
}

export function formatSchemaErrors(label: string, errors: ErrorObject[] | null | undefined): string[] {
  return (errors ?? []).map((error) => `${label}${error.instancePath || "/"} ${error.message ?? "is invalid"}`)
}

export function schemaErrors(label: string, validate: ValidateFunction, value: unknown): string[] {
  return validate(value) ? [] : formatSchemaErrors(label, validate.errors)
}
