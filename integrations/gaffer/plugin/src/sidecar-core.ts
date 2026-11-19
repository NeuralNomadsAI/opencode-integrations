import { createPanel as createChat, type PanelDependencies } from "../../../../packages/integration-kit/src/panel.ts"

export { type Api, briefly, type PanelRequest } from "../../../../packages/integration-kit/src/panel.ts"

export const SCRIPT_MARKER = "opencode_gaffer_script"
export const createPanel = (deps: PanelDependencies) =>
  createChat(
    {
      app: "Gaffer",
      rpcID: "neuralnomads.gaffer",
      marker: SCRIPT_MARKER,
      document: "script",
      keyField: "script_key",
      nameField: "script_name",
      defaultName: "untitled",
      duplicateOffer: "scripts",
    },
    deps,
  )
