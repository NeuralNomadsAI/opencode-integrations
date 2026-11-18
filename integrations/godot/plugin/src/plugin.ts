import { Plugin } from "@opencode/plugin"
import { setupGodot } from "./integration.ts"

export default Plugin.define({ id: "neuralnomads.godot", setup: setupGodot })
