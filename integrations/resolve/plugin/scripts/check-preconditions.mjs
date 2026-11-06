// Read-only check; takes the same JSON options as the plugin. Never launches Resolve.
import { checkPreconditions, readOptions } from "../dist/index.js"

const result = checkPreconditions(readOptions(JSON.parse(process.argv[2] ?? "{}")))
console.log(JSON.stringify(result))
process.exitCode = result.ready ? 0 : 1
