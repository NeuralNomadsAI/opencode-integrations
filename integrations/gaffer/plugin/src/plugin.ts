import { Plugin } from "@opencode/plugin"
import { setupGaffer } from "./integration.ts"

export default Plugin.define({ id: "neuralnomads.gaffer", setup: setupGaffer })
