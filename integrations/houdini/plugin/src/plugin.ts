import { Plugin } from "@opencode/plugin"
import { setupHoudini } from "./integration.ts"

export default Plugin.define({ id: "neuralnomads.houdini", setup: setupHoudini })
