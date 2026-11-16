// What became of a Blender that stopped answering a conversation (the kit's crashWatch): crashed, closed, or
// alive without renewing its offer. Blender writes "<file name>.crash.txt" to its temporary folder; every Blender
// of a user shares that folder: only the report named after this Blender's document counts.

import { crashWatch, type HostProcess, readProcess } from "./kit.ts"

export { type HostProcess, readProcess }

// The name Blender gives its crash report: the file's name, or "blender" for a file never saved.
function reportName(host: HostProcess) {
  return `${host.document ? host.document.replace(/\.blend$/i, "") : "blender"}.crash.txt`
}

export const { crashReport, closedMessage, silentMessage } = crashWatch({
  appName: "Blender",
  app: "blender",
  part: "OpenCode Link",
  isReport: (name, host) => name.toLowerCase() === reportName(host).toLowerCase(),
})
