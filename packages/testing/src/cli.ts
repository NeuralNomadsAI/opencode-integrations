// Usage: node packages/testing/src/cli.ts [repository root]
// Checks the markers and the catalog, then loads every built integration package and reads its status.
import { resolve } from "node:path"

import { checkRepository, checkRepositoryLoads } from "./markers.ts"

const root = resolve(process.argv[2] ?? ".")
const report = checkRepository(root)
const errors = [...report.errors, ...(await checkRepositoryLoads(root))]
for (const error of errors) console.error(`check:markers: ${error}`)
console.log(
  `check:markers: ${errors.length === 0 ? "ok" : "failed"} (${report.integrations.length} integration package(s), ${errors.length} error(s))`,
)
process.exitCode = errors.length === 0 ? 0 : 1
