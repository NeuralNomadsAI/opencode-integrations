// Usage: node packages/conformance/src/cli.ts [repository root]
// Integration packages must be built: each one is loaded in an isolated process.
import { resolve } from "node:path"

import { SPEC_DRAFT } from "./contract.ts"
import { checkRepository, checkRepositoryLoads } from "./repository.ts"

const root = resolve(process.argv[2] ?? ".")
const report = checkRepository(root)
const errors = [...report.errors, ...(await checkRepositoryLoads(root))]
for (const error of errors) console.error(`conformance: ${error}`)
console.log(
  `conformance: ${errors.length === 0 ? "ok" : "failed"} (draft ${SPEC_DRAFT}, spec 0, ${report.integrations.length} integration package(s), ${errors.length} error(s))`,
)
process.exitCode = errors.length === 0 ? 0 : 1
