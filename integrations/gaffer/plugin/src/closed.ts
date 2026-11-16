// What became of a Gaffer that stopped answering a conversation (the kit's crashWatch): closed, or alive without
// renewing its offer (frozen). Gaffer writes no crash report: a crash reads as a closed process.

import { crashWatch } from "./kit.ts"

export const { crashReport, closedMessage, silentMessage } = crashWatch({
  appName: "Gaffer",
  app: "gaffer",
  part: "OpenCode Link",
  isReport: () => false,
})
