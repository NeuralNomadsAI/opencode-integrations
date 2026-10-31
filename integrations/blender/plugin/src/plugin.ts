// Entry of @neuralnomads/opencode-blender. Plugin.define is the identity function, so the
// plugin needs no runtime import of the SDK, which the OpenCode binary provides.

import type { Plugin } from "@opencode/plugin"

import { setupBlender } from "./integration.ts"

const plugin: Plugin.Plugin = {
  id: "neuralnomads.blender",
  setup: (ctx) => setupBlender(ctx),
}

export default plugin
