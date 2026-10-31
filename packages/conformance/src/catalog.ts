import { formatSchemaErrors, validateCatalogSchema } from "./schemas.ts"

// Size and count caps of the published catalog.
export const CATALOG_MAX_BYTES = 256 * 1024
export const CATALOG_MAX_INTEGRATIONS = 64

export type CatalogEntry = {
  id: string
  package: string
  range: string
  displayName: string
  family: "native" | "vendor-mcp" | "api"
  plugin: string
  rpc: string
  summary: Record<string, string>
  app: { name: string; versions?: string }
  host?: { protocol: number; range: string }
  vendor?: { server: string; minAppVersion?: string }
  docs: string
  status: "preview" | "stable" | "deprecated"
  sources?: { git?: string[] }
}

export type Catalog = { schemaVersion: 1; integrations: CatalogEntry[] }

export type CatalogResult = { catalog: Catalog | null; errors: string[] }

const UNIQUE_FIELDS = ["id", "package", "plugin", "rpc"] as const

// Validates the raw bytes of a catalog document: size cap, JSON, schema,
// uniqueness and the derived identities of each entry.
export function checkCatalogBytes(bytes: Uint8Array): CatalogResult {
  if (bytes.byteLength > CATALOG_MAX_BYTES) {
    return { catalog: null, errors: [`catalog is ${bytes.byteLength} bytes, over the ${CATALOG_MAX_BYTES} byte cap`] }
  }
  let document: unknown
  try {
    document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes))
  } catch (error) {
    return { catalog: null, errors: [`catalog is not valid UTF-8 JSON: ${(error as Error).message}`] }
  }
  if (!validateCatalogSchema(document)) {
    return { catalog: null, errors: formatSchemaErrors("catalog", validateCatalogSchema.errors) }
  }
  const catalog = document as Catalog
  const errors: string[] = []
  for (const field of UNIQUE_FIELDS) {
    const seen = new Set<string>()
    for (const entry of catalog.integrations) {
      if (seen.has(entry[field])) errors.push(`catalog has two entries with ${field} "${entry[field]}"`)
      seen.add(entry[field])
    }
  }
  for (const entry of catalog.integrations) {
    const expected = `neuralnomads.${entry.id}`
    if (entry.package !== `@neuralnomads/opencode-${entry.id}`) {
      errors.push(`catalog entry ${entry.id}: package must be @neuralnomads/opencode-${entry.id}`)
    }
    if (entry.plugin !== expected) errors.push(`catalog entry ${entry.id}: plugin must be ${expected}`)
    if (entry.rpc !== expected) errors.push(`catalog entry ${entry.id}: rpc must be ${expected}`)
  }
  return { catalog: errors.length ? null : catalog, errors }
}
