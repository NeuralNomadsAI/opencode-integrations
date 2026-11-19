import {
  createPanel as createChat,
  type PanelDependencies,
  type PanelHost,
} from "../../../../packages/integration-kit/src/panel.ts"

export { type Api, briefly, type PanelRequest } from "../../../../packages/integration-kit/src/panel.ts"

export const panelHost = {
  app: "Gaffer",
  rpcID: "neuralnomads.gaffer",
  document: "script",
  duplicateOffer: "scripts",
} satisfies PanelHost
export const createPanel = (deps: PanelDependencies) => createChat(panelHost, deps)
