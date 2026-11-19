import type { PanelHost } from "../../../../packages/integration-kit/src/panel.ts"

export const blenderPanel = {
  app: "Blender",
  rpcID: "neuralnomads.blender",
  marker: "opencode_blender_document",
  document: "document",
  keyField: "document_key",
  nameField: "document_name",
  defaultName: "Untitled",
  duplicateOffer: "Blender instances",
  agent: true,
  consoleEvents: true,
  // Blender has no session-specific cancel/resume host methods. Interrupt the agent via OpenCode only.
  hostControls: false,
} satisfies PanelHost
