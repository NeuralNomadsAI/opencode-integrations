import { homedir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

export const packageRoot = fileURLToPath(new URL("..", import.meta.url))

export function stateDir(env = process.env, home = homedir(), platform = process.platform) {
  return platform === "win32"
    ? join(env.LOCALAPPDATA ?? join(home, "AppData", "Local"), "NeuralNomads", "opencode-houdini")
    : join(env.XDG_STATE_HOME ?? join(home, ".local", "state"), "neuralnomads", "opencode-houdini")
}
