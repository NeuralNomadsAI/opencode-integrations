import {
  createPanel as createChat,
  type PanelDependencies,
  type PanelHost,
} from "../../../../packages/integration-kit/src/panel.ts"

export { type Api, briefly, type PanelRequest } from "../../../../packages/integration-kit/src/panel.ts"

export const PROJECT_MARKER = "opencode_godot_project"
export const panelHost = {
  app: "Godot",
  rpcID: "neuralnomads.godot",
  marker: PROJECT_MARKER,
  document: "project",
  keyField: "project_key",
  nameField: "project_name",
  defaultName: "project",
  duplicateOffer: "Godot editors",
  agent: true,
} satisfies PanelHost
export const createPanel = (deps: PanelDependencies) => createChat(panelHost, deps)
