// Usage: node packages/conformance/src/cli.ts [repository root]
import { resolve } from "node:path"

import { checkRepository } from "./repository.ts"

const root = resolve(process.argv[2] ?? ".")
const report = checkRepository(root)
for (const error of report.errors) console.error(`conformance: ${error}`)
console.log(
  `conformance: ${report.ok ? "ok" : "failed"} (draft spec 0, ${report.integrations.length} integration package(s), ${report.errors.length} error(s))`,
)
process.exitCode = report.ok ? 0 : 1
