// What became of a Houdini that stopped answering a conversation (the kit's crashWatch): crashed, closed, or
// alive without renewing its offer. A crashing Houdini writes to its temporary folder (houdini_temp, shared by every
// Houdini of the user) crash.<file>.<user>_<pid>_log.txt, and the scene as it was at the crash,
// crash.<file>.<user>_<pid>.hip(nc|lc): only the files naming this process count.

import { crashWatch } from "./kit.ts"

const SCENE = /\.hip(nc|lc)?$/i

export const { crashReport, closedMessage, silentMessage } = crashWatch({
  appName: "Houdini",
  app: "houdini",
  part: "OpenCode Link",
  isReport: (name, host) => {
    const lower = name.toLowerCase()
    return lower.startsWith("crash.") && new RegExp(`_${host.pid}(_log\\.txt|\\.hip(nc|lc)?)$`).test(lower)
  },
  crashNote: (report) =>
    SCENE.test(report)
      ? "Houdini saved the scene as it was at the crash in that file: open it to recover the changes since the last save."
      : undefined,
})
