// Host-less audit tool: summarizes a .blend file with a separate Blender process in background mode
// (factory settings, scripts in the file never auto-run). Launching Blender asks for permission.

import { execFile } from "node:child_process"
import { existsSync, statSync } from "node:fs"
import { basename, resolve } from "node:path"

const RESULT_MARKER = "__OPENCODE_LINK_AUDIT__"
const DEFAULT_TIMEOUT_MS = 120_000

export type AuditKind = "path_info" | "datablocks" | "missing_files" | "linked_libraries"
export const AUDIT_KINDS: AuditKind[] = ["path_info", "datablocks", "missing_files", "linked_libraries"]

const SCRIPTS: Record<Exclude<AuditKind, "path_info">, string> = {
  datablocks: `
counts = {name: len(getattr(bpy.data, name)) for name in (
    "actions", "cameras", "collections", "curves", "fonts", "images", "libraries", "lights",
    "materials", "meshes", "node_groups", "objects", "scenes", "textures", "worlds")}
result = {"blend_file": bpy.data.filepath, "counts": counts,
          "scenes": [scene.name for scene in bpy.data.scenes],
          "render_engines": sorted(set(scene.render.engine for scene in bpy.data.scenes))}
`,
  missing_files: `
missing = []
def add_missing(kind, name, filepath):
    if filepath and not bpy.path.exists(filepath):
        missing.append({"type": kind, "name": name, "path": bpy.path.abspath(filepath)})
for image in bpy.data.images:
    if image.source == "FILE":
        add_missing("IMAGE", image.name, image.filepath)
for font in bpy.data.fonts:
    add_missing("FONT", font.name, font.filepath)
for sound in bpy.data.sounds:
    add_missing("SOUND", sound.name, sound.filepath)
for clip in bpy.data.movieclips:
    add_missing("MOVIE_CLIP", clip.name, clip.filepath)
for library in bpy.data.libraries:
    add_missing("LIBRARY", library.name, library.filepath)
result = {"blend_file": bpy.data.filepath, "count": len(missing), "missing_files": missing}
`,
  linked_libraries: `
libraries = []
for library in bpy.data.libraries:
    users = [{"name": block.name, "id_type": block.id_type} for block in library.users_id]
    libraries.append({"name": library.name, "filepath": bpy.path.abspath(library.filepath),
                      "users_id_count": len(users), "users_id": users})
result = {"blend_file": bpy.data.filepath, "count": len(libraries), "libraries": libraries}
`,
}

export function resolveBlendFile(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("blend_file is required")
  const file = resolve(value.trim())
  if (!file.toLowerCase().endsWith(".blend")) throw new Error("blend_file must name a .blend file")
  if (!existsSync(file) || !statSync(file).isFile()) throw new Error("The .blend file was not found")
  return file
}

export function pathInfo(file: string) {
  const stats = statSync(file)
  return {
    blend_file: file,
    basename: basename(file),
    size_bytes: stats.size,
    modified_at: stats.mtime.toISOString(),
    created_at: stats.birthtime.toISOString(),
  }
}

export async function runAudit(
  kind: Exclude<AuditKind, "path_info">,
  file: string,
  options: { blender: string; timeoutMs?: number; signal?: AbortSignal; env?: NodeJS.ProcessEnv },
): Promise<Record<string, unknown>> {
  const code = [
    "import bpy",
    "import json",
    SCRIPTS[kind],
    `print(${JSON.stringify(RESULT_MARKER)} + json.dumps(result, default=str))`,
  ].join("\n")
  const stdout = await new Promise<string>((done, fail) => {
    execFile(
      options.blender,
      ["--background", "--factory-startup", "--disable-autoexec", file, "--python-expr", code],
      {
        timeout: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
        maxBuffer: 32 * 1024 * 1024,
        signal: options.signal,
        windowsHide: true,
      },
      (error, out) =>
        error && !out.includes(RESULT_MARKER)
          ? fail(new Error(`Blender could not audit the file: ${error.message}`))
          : done(out),
    )
  })
  const index = stdout.lastIndexOf(RESULT_MARKER)
  if (index < 0) throw new Error("Blender returned no audit result")
  const line =
    stdout
      .slice(index + RESULT_MARKER.length)
      .trim()
      .split(/\r?\n/)[0] ?? ""
  return JSON.parse(line) as Record<string, unknown>
}
