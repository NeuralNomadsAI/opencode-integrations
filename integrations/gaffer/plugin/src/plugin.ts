import type { Plugin } from "@opencode/plugin"
import { setupGaffer } from "./integration.ts"

const plugin: Plugin.Plugin = { id: "neuralnomads.gaffer", setup: (ctx) => setupGaffer(ctx) }
export default plugin
