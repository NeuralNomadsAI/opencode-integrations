import {
  createPanel as createChat,
  type PanelDependencies,
  type PanelHost,
} from "../../../../packages/integration-kit/src/panel.ts"

export { type Api, briefly, type PanelRequest } from "../../../../packages/integration-kit/src/panel.ts"

export const panelHost = {
  app: "Godot",
  rpcID: "neuralnomads.godot",
  document: "project",
  duplicateOffer: "Godot editors",
  agent: true,
} satisfies PanelHost
export const createPanel = (deps: PanelDependencies) => createChat(panelHost, deps)
