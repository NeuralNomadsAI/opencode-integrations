import { readFileSync } from "node:fs"
import { createRequire } from "node:module"

import type { ErrorObject, ValidateFunction } from "ajv"

// ajv ships CommonJS; load its 2020-12 build through require for NodeNext.
const require = createRequire(import.meta.url)
const Ajv2020 = require("ajv/dist/2020").default as typeof import("ajv").default

const schemaDir = new URL("../../../spec/schemas/", import.meta.url)

function loadSchema(name: string): object {
  return JSON.parse(readFileSync(new URL(name, schemaDir), "utf8"))
}

// Conditional `required` clauses name properties declared at the top level.
const ajv = new Ajv2020({ allErrors: true, strict: true, strictRequired: false })

export const validateMarkerSchema: ValidateFunction = ajv.compile(loadSchema("marker.schema.json"))
export const validateCatalogSchema: ValidateFunction = ajv.compile(loadSchema("catalog.schema.json"))

export function formatSchemaErrors(label: string, errors: ErrorObject[] | null | undefined): string[] {
  return (errors ?? []).map((error) => `${label}${error.instancePath || "/"} ${error.message ?? "is invalid"}`)
}
