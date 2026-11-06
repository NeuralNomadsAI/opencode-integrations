import { statSync } from "node:fs"
import { isAbsolute } from "node:path"

export type ResolveOptions = { command?: string[]; studio: boolean; appVersion?: string }

export function readOptions(options: Record<string, unknown> = {}, env = process.env): ResolveOptions {
  const command =
    options.command ?? (env.NEURALNOMADS_RESOLVE_COMMAND ? JSON.parse(env.NEURALNOMADS_RESOLVE_COMMAND) : undefined)
  if (
    command !== undefined &&
    (!Array.isArray(command) ||
      command.length === 0 ||
      // biome-ignore lint/suspicious/noControlCharactersInRegex: reject control characters at the options boundary.
      !command.every((part) => typeof part === "string" && part.length > 0 && !/[\x00-\x1f]/.test(part)))
  ) {
    throw new Error("Resolve command must be a nonempty array of strings.")
  }
  const studio = options.studio ?? env.NEURALNOMADS_RESOLVE_STUDIO === "true"
  const appVersion = options.appVersion ?? env.NEURALNOMADS_RESOLVE_APP_VERSION
  if (typeof studio !== "boolean" || (appVersion !== undefined && typeof appVersion !== "string"))
    throw new Error("Invalid Resolve edition or version option.")
  return { command: command as string[] | undefined, studio, appVersion: appVersion as string | undefined }
}

// Edition/version are operator attestations; no product is launched to obtain them.
export function checkPreconditions(
  options: ResolveOptions,
  isFile = (path: string) => {
    try {
      return statSync(path).isFile()
    } catch {
      return false
    }
  },
): { ready: boolean; issues: string[] } {
  const issues: string[] = []
  if (!options.studio) issues.push("studio_required")
  const version = /^(\d+)\.(\d+)(?:\.\d+)*$/.exec(options.appVersion ?? "")
  if (!version || Number(version[1]) < 21 || (Number(version[1]) === 21 && Number(version[2]) < 1))
    issues.push("studio_21_1_required")
  const executable = options.command?.[0]
  if (!executable || !isAbsolute(executable)) issues.push("configured_command_required")
  else if (!isFile(executable)) issues.push("server_missing")
  return { ready: issues.length === 0, issues }
}
