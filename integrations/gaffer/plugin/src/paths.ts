import { homedir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

export const packageRoot = fileURLToPath(new URL("..", import.meta.url))
export const skillsDir = join(packageRoot, "skills")
export function stateRoot() {
  return process.platform === "win32"
    ? join(process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local"), "NeuralNomads", "opencode-gaffer")
    : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "neuralnomads", "opencode-gaffer")
}
