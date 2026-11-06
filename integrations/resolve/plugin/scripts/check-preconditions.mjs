// Read-only check; takes the same JSON options as the plugin. Never launches Resolve.
import { checkPreconditions, readOptions } from "../dist/index.js"

let result
try {
  result = checkPreconditions(readOptions(JSON.parse(process.argv[2] ?? "{}")))
} catch {
  result = { ready: false, issues: ["invalid_options"] }
}
console.log(JSON.stringify(result))
process.exitCode = result.ready ? 0 : 1
