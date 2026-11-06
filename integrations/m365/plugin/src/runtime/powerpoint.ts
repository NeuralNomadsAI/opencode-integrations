import { dirname, join, normalize } from "node:path/posix"
import JSZip from "jszip"
import { checkExpandedBudget, checkZipBudget } from "../zip-budget.ts"

import { isRecord } from "./auth.ts"
import { throwIfCancelled } from "./cancellation.ts"
import { downloadGraphBytes, executeGraphRawRequest } from "./graph.ts"
import { type DriveItemSummary, type DriveSummary, getDriveItem } from "./m365.ts"

const DEFAULT_MAX_POWERPOINT_BYTES = 50 * 1024 * 1024
const MAX_POWERPOINT_BYTES = 100 * 1024 * 1024
const DEFAULT_MAX_TEXT_CHARS = 4000
const MAX_TEXT_CHARS = 20000
const DEFAULT_POWERPOINT_BATCH_LIMIT = 10
const MAX_POWERPOINT_BATCH_LIMIT = 50
const POWERPOINT_EXTENSIONS = new Set(["potm", "potx", "ppsm", "ppsx", "pptm", "pptx"])

type DriveItemArgs = Parameters<typeof getDriveItem>[0]

type PowerPointTextPartKind = "slide" | "slide_layout" | "slide_master"

type PowerPointTextPart = {
  kind: PowerPointTextPartKind
  index: number | null
  path: string
  text: string
  text_run_count: number
  text_length: number
}

type PowerPointPlaceholderSummary = {
  index: string | null
  type: string | null
  size: string | null
  orientation: string | null
}

type PowerPointStructurePartKind = PowerPointTextPartKind

type PowerPointStructurePartSummary = {
  kind: PowerPointStructurePartKind
  index: number | null
  path: string
  name: string | null
  placeholder_count: number
  placeholders: PowerPointPlaceholderSummary[]
  text_run_count: number
  text_preview: string
}

type PowerPointMediaKind = "audio" | "image" | "media" | "ole" | "other" | "video"

type PowerPointMediaReference = {
  source_kind: string
  source_index: number | null
  source_path: string
  relationship_id: string | null
  relationship_type: string | null
  target: string
  target_mode: "Embedded" | "External"
  resolved_path: string | null
}

type PowerPointEmbeddedMedia = {
  path: string
  name: string
  extension: string | null
  media_kind: PowerPointMediaKind
  content_type: string | null
  byte_length: number
  references_count: number
  references: PowerPointMediaReference[]
}

type PowerPointExternalMedia = {
  target: string
  media_kind: PowerPointMediaKind
  relationship_type: string | null
  references_count: number
  references: PowerPointMediaReference[]
}

type ReplacementResult = {
  updatedText: string
  replacementCount: number
  countsByKey: Map<string, number>
}

type ProcessedPart = {
  kind: PowerPointTextPartKind
  index: number | null
  path: string
  textRunCount: number
  originalText: string
  updatedText: string
  replacementCount: number
  countsByKey: Map<string, number>
}

type ContentTypesMap = {
  byExtension: Map<string, string>
  byPartName: Map<string, string>
}

type PowerPointCoreProperties = {
  title: string | null
  subject: string | null
  creator: string | null
  description: string | null
  keywords: string | null
  lastModifiedBy: string | null
  created: string | null
  modified: string | null
}

function getString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null
}

function getBoolean(value: unknown) {
  return typeof value === "boolean" ? value : null
}

function readPositiveInt(value: unknown, fallback: number, max: number) {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }

  return Math.min(Math.trunc(parsed), max)
}

function encodePathSegment(value: string) {
  return encodeURIComponent(value)
}

function getStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as string[]
  }

  return value.map((item) => getString(item)).filter((item): item is string => Boolean(item))
}

function summarizeUploadedDriveItem(value: unknown, drive: DriveSummary | null = null) {
  const record = isRecord(value) ? value : null
  const file = isRecord(record?.file) ? record.file : null
  return {
    id: getString(record?.id),
    name: getString(record?.name),
    webUrl: getString(record?.webUrl),
    size: typeof record?.size === "number" && Number.isFinite(record.size) ? record.size : null,
    mimeType: getString(file?.mimeType),
    lastModifiedDateTime: getString(record?.lastModifiedDateTime),
    driveId: drive?.id || null,
    siteId: drive?.siteId || null,
    drive,
  }
}

function getPowerPointExtension(item: DriveItemSummary) {
  return (item.extension || "").toLowerCase()
}

function ensureSupportedPowerPointFile(item: DriveItemSummary) {
  if (!item.isFile) {
    throw new Error("The resolved item is not a PowerPoint file.")
  }

  const extension = getPowerPointExtension(item)
  if (!POWERPOINT_EXTENSIONS.has(extension)) {
    throw new Error(
      `Unsupported PowerPoint file extension '${extension || "unknown"}'. ` +
        "Use .pptx, .potx, .pptm, .potm, .ppsx or .ppsm.",
    )
  }

  return extension
}

function decodeXmlText(value: string) {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
}

function decodeXmlAttribute(value: string) {
  return decodeXmlText(value)
}

function getTagText(xml: string, names: string[]) {
  for (const name of names) {
    const match = new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`, "i").exec(xml)
    if (match) {
      return decodeXmlText(match[1]!).trim() || null
    }
  }

  return null
}

function encodeXmlText(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;")
}

function truncateText(value: string, maxChars: number) {
  if (value.length <= maxChars) {
    return value
  }

  return `${value.slice(0, Math.max(0, maxChars - 1))}...`
}

function normalizeReplacements(value: unknown) {
  if (!isRecord(value)) {
    throw new Error("replacements must be a non-empty object of string-to-string mappings.")
  }

  const replacements = Object.entries(value)
    .map(([key, replacement]) => {
      const from = key.trim()
      const to = typeof replacement === "string" ? replacement : null
      if (!from || to === null) {
        return null
      }
      return [from, to] as const
    })
    .filter((entry): entry is readonly [string, string] => Boolean(entry))

  if (replacements.length === 0) {
    throw new Error("replacements must contain at least one non-empty string mapping.")
  }

  return replacements
}

function getBatchItems(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as Record<string, unknown>[]
  }

  return value.filter((item): item is Record<string, unknown> => isRecord(item))
}

function getItemReferenceLabel(value: Record<string, unknown>) {
  return getString(value.item_path) || getString(value.item_id) || "<unresolved>"
}

function parsePowerPointPart(
  path: string,
  includeTemplateParts: boolean,
): {
  kind: PowerPointTextPartKind
  index: number | null
} | null {
  const slideMatch = /^ppt\/slides\/slide(\d+)\.xml$/i.exec(path)
  if (slideMatch) {
    return {
      kind: "slide",
      index: Number.parseInt(slideMatch[1]!, 10),
    }
  }

  if (!includeTemplateParts) {
    return null
  }

  const layoutMatch = /^ppt\/slideLayouts\/slideLayout(\d+)\.xml$/i.exec(path)
  if (layoutMatch) {
    return {
      kind: "slide_layout",
      index: Number.parseInt(layoutMatch[1]!, 10),
    }
  }

  const masterMatch = /^ppt\/slideMasters\/slideMaster(\d+)\.xml$/i.exec(path)
  if (masterMatch) {
    return {
      kind: "slide_master",
      index: Number.parseInt(masterMatch[1]!, 10),
    }
  }

  return null
}

function parsePowerPointStructurePart(
  path: string,
  includeTemplateParts: boolean,
): {
  kind: PowerPointStructurePartKind
  index: number | null
} | null {
  const part = parsePowerPointPart(path, includeTemplateParts)
  if (!part) {
    return null
  }

  return part
}

function parsePowerPointSourcePart(path: string, includeTemplateParts: boolean) {
  const textPart = parsePowerPointPart(path, includeTemplateParts)
  if (textPart) {
    return textPart
  }

  const notesMatch = /^ppt\/notesSlides\/notesSlide(\d+)\.xml$/i.exec(path)
  if (notesMatch) {
    return {
      kind: "notes_slide",
      index: Number.parseInt(notesMatch[1]!, 10),
    }
  }

  const chartMatch = /^ppt\/charts\/chart(\d+)\.xml$/i.exec(path)
  if (chartMatch) {
    return {
      kind: "chart",
      index: Number.parseInt(chartMatch[1]!, 10),
    }
  }

  return {
    kind: "other",
    index: null,
  }
}

function extractTextRuns(xml: string) {
  const pattern = /<a:t[^>]*>([\s\S]*?)<\/a:t>/g
  const runs: string[] = []
  for (const match of xml.matchAll(pattern)) {
    runs.push(decodeXmlText(match[1]!))
  }
  return runs
}

function extractPlaceholders(xml: string) {
  const placeholders: PowerPointPlaceholderSummary[] = []
  const pattern = /<p:ph\b([^>]*)\/?>(?:<\/p:ph>)?/g
  for (const match of xml.matchAll(pattern)) {
    const attrs = match[1] || ""
    const index = /\bidx="([^"]+)"/i.exec(attrs)
    const type = /\btype="([^"]+)"/i.exec(attrs)
    const size = /\bsz="([^"]+)"/i.exec(attrs)
    const orientation = /\borient="([^"]+)"/i.exec(attrs)
    placeholders.push({
      index: index ? decodeXmlAttribute(index[1]!) : null,
      type: type ? decodeXmlAttribute(type[1]!) : null,
      size: size ? decodeXmlAttribute(size[1]!) : null,
      orientation: orientation ? decodeXmlAttribute(orientation[1]!) : null,
    })
  }

  return placeholders
}

function extractPartName(xml: string) {
  const cSldMatch = /<p:cSld\b[^>]*\bname="([^"]+)"/i.exec(xml)
  if (cSldMatch) {
    return decodeXmlAttribute(cSldMatch[1]!)
  }

  const nvNameMatch = /<p:cNvPr\b[^>]*\bname="([^"]+)"/i.exec(xml)
  if (nvNameMatch) {
    return decodeXmlAttribute(nvNameMatch[1]!)
  }

  return null
}

function summarizeStructurePart(
  path: string,
  kind: PowerPointStructurePartKind,
  index: number | null,
  xml: string,
  maxTextChars: number,
) {
  const textSummary = summarizeTextPart(path, kind, index, xml)
  const placeholders = extractPlaceholders(xml)
  return {
    kind,
    index,
    path,
    name: extractPartName(xml),
    placeholder_count: placeholders.length,
    placeholders,
    text_run_count: textSummary.text_run_count,
    text_preview: truncateText(textSummary.text, maxTextChars),
  } satisfies PowerPointStructurePartSummary
}

function parseCoreProperties(xml: string): PowerPointCoreProperties {
  return {
    title: getTagText(xml, ["dc:title"]),
    subject: getTagText(xml, ["dc:subject"]),
    creator: getTagText(xml, ["dc:creator"]),
    description: getTagText(xml, ["dc:description"]),
    keywords: getTagText(xml, ["cp:keywords"]),
    lastModifiedBy: getTagText(xml, ["cp:lastModifiedBy"]),
    created: getTagText(xml, ["dcterms:created"]),
    modified: getTagText(xml, ["dcterms:modified"]),
  }
}

function parsePresentationSize(xml: string) {
  const match = /<p:sldSz\b[^>]*\bcx="([^"]+)"[^>]*\bcy="([^"]+)"/i.exec(xml)
  if (!match) {
    return null
  }

  const width = Number.parseInt(match[1]!, 10)
  const height = Number.parseInt(match[2]!, 10)
  if (!Number.isFinite(width) || !Number.isFinite(height)) {
    return null
  }

  return {
    width_emu: width,
    height_emu: height,
  }
}

function parseContentTypes(xml: string): ContentTypesMap {
  const byExtension = new Map<string, string>()
  const byPartName = new Map<string, string>()

  const defaultPattern = /<Default\b[^>]*\bExtension="([^"]+)"[^>]*\bContentType="([^"]+)"[^>]*\/?>(?:<\/Default>)?/g
  for (const defaultMatch of xml.matchAll(defaultPattern)) {
    byExtension.set(defaultMatch[1]!.toLowerCase(), defaultMatch[2]!)
  }

  const overridePattern = /<Override\b[^>]*\bPartName="([^"]+)"[^>]*\bContentType="([^"]+)"[^>]*\/?>(?:<\/Override>)?/g
  for (const overrideMatch of xml.matchAll(overridePattern)) {
    byPartName.set(overrideMatch[1]!.replace(/^\/+/, ""), overrideMatch[2]!)
  }

  return {
    byExtension,
    byPartName,
  }
}

function getFileExtension(path: string) {
  const match = /\.([^./]+)$/.exec(path)
  return match ? match[1]!.toLowerCase() : null
}

function classifyMediaKind(value: {
  relationshipType?: string | null
  contentType?: string | null
  extension?: string | null
}): PowerPointMediaKind {
  const relationshipType = String(value.relationshipType || "").toLowerCase()
  const contentType = String(value.contentType || "").toLowerCase()
  const extension = String(value.extension || "").toLowerCase()

  if (
    relationshipType.includes("/image") ||
    contentType.startsWith("image/") ||
    ["png", "jpg", "jpeg", "gif", "bmp", "svg", "tif", "tiff", "wmf", "emf"].includes(extension)
  ) {
    return "image"
  }
  if (
    relationshipType.includes("/video") ||
    contentType.startsWith("video/") ||
    ["mp4", "mov", "avi", "wmv", "m4v", "mpeg", "mpg"].includes(extension)
  ) {
    return "video"
  }
  if (
    relationshipType.includes("/audio") ||
    contentType.startsWith("audio/") ||
    ["mp3", "wav", "m4a", "aac", "wma", "ogg"].includes(extension)
  ) {
    return "audio"
  }
  if (relationshipType.includes("/media")) {
    return "media"
  }
  if (
    relationshipType.includes("/oleobject") ||
    relationshipType.includes("/package") ||
    ["bin", "xls", "xlsx", "doc", "docx", "pdf"].includes(extension)
  ) {
    return "ole"
  }

  return "other"
}

function resolveRelationshipTarget(sourcePartPath: string, target: string) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(target)) {
    return null
  }
  if (target.startsWith("/")) {
    return target.replace(/^\/+/, "")
  }

  return normalize(join(dirname(sourcePartPath), target))
}

function extractMediaReferencesFromRelationships(args: {
  relsXml: string
  sourcePartPath: string
  includeTemplateParts: boolean
}) {
  const references: PowerPointMediaReference[] = []
  const sourceInfo = parsePowerPointSourcePart(args.sourcePartPath, args.includeTemplateParts)
  const relationshipPattern = /<Relationship\b([^>]*)\/?>(?:<\/Relationship>)?/g
  for (const relationshipMatch of args.relsXml.matchAll(relationshipPattern)) {
    const attrs = relationshipMatch[1] || ""
    const idMatch = /\bId="([^"]+)"/.exec(attrs)
    const typeMatch = /\bType="([^"]+)"/.exec(attrs)
    const targetMatch = /\bTarget="([^"]+)"/.exec(attrs)
    const modeMatch = /\bTargetMode="([^"]+)"/.exec(attrs)
    const relationshipType = typeMatch ? decodeXmlAttribute(typeMatch[1]!) : null
    const target = targetMatch ? decodeXmlAttribute(targetMatch[1]!) : null

    if (!target || !relationshipType) {
      continue
    }

    const mediaKind = classifyMediaKind({
      relationshipType,
      extension: getFileExtension(target),
    })
    if (mediaKind === "other") {
      continue
    }

    references.push({
      source_kind: sourceInfo.kind,
      source_index: sourceInfo.index,
      source_path: args.sourcePartPath,
      relationship_id: idMatch ? decodeXmlAttribute(idMatch[1]!) : null,
      relationship_type: relationshipType,
      target,
      target_mode:
        modeMatch && decodeXmlAttribute(modeMatch[1]!).toLowerCase() === "external" ? "External" : "Embedded",
      resolved_path: resolveRelationshipTarget(args.sourcePartPath, target),
    })
  }

  return references
}

function summarizeTextPart(path: string, kind: PowerPointTextPartKind, index: number | null, xml: string) {
  const runs = extractTextRuns(xml)
  const text = runs.join(" ").replace(/\s+/g, " ").trim()
  return {
    kind,
    index,
    path,
    text,
    text_run_count: runs.length,
    text_length: text.length,
  } satisfies PowerPointTextPart
}

function replaceLiteralOccurrences(text: string, find: string, replaceWith: string) {
  if (!find) {
    return {
      text,
      count: 0,
    }
  }

  let count = 0
  let cursor = 0
  let next = text.indexOf(find, cursor)
  if (next === -1) {
    return {
      text,
      count,
    }
  }

  let result = ""
  while (next !== -1) {
    result += text.slice(cursor, next) + replaceWith
    cursor = next + find.length
    count += 1
    next = text.indexOf(find, cursor)
  }

  result += text.slice(cursor)
  return {
    text: result,
    count,
  }
}

function replaceTextRunsInXml(xml: string, replacements: readonly (readonly [string, string])[]): ReplacementResult {
  const pattern = /<a:t([^>]*)>([\s\S]*?)<\/a:t>/g
  let lastIndex = 0
  let replacementCount = 0
  const countsByKey = new Map<string, number>()
  let updatedXml = ""
  for (const match of xml.matchAll(pattern)) {
    updatedXml += xml.slice(lastIndex, match.index)
    const attrs = match[1] || ""
    let text = decodeXmlText(match[2]!)

    for (const [find, replaceWith] of replacements) {
      const result = replaceLiteralOccurrences(text, find, replaceWith)
      text = result.text
      if (result.count > 0) {
        replacementCount += result.count
        countsByKey.set(find, (countsByKey.get(find) || 0) + result.count)
      }
    }

    updatedXml += `<a:t${attrs}>${encodeXmlText(text)}</a:t>`
    lastIndex = match.index + match[0].length
  }

  updatedXml += xml.slice(lastIndex)

  return {
    updatedText: updatedXml,
    replacementCount,
    countsByKey,
  }
}

async function loadPowerPointPackage(args: DriveItemArgs & { max_bytes?: unknown }) {
  const resolved = await getDriveItem(args)
  const { drive, item } = resolved

  if (!drive.id || !item.id) {
    throw new Error("The resolved PowerPoint file does not expose the ids needed to download content.")
  }

  const extension = ensureSupportedPowerPointFile(item)
  const maxBytes = readPositiveInt(args.max_bytes, DEFAULT_MAX_POWERPOINT_BYTES, MAX_POWERPOINT_BYTES)
  const result = await downloadGraphBytes({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}/content`,
    max_bytes: maxBytes,
    force_refresh: Boolean(args.force_refresh),
  })

  if (result.contentLength !== null && result.contentLength > maxBytes) {
    throw new Error(
      `PowerPoint file is too large to process safely (${result.contentLength} bytes > ${maxBytes} bytes).`,
    )
  }

  if (result.bytes.byteLength > maxBytes) {
    throw new Error(
      `PowerPoint file is too large to process safely (${result.bytes.byteLength} bytes > ${maxBytes} bytes).`,
    )
  }

  checkZipBudget(result.bytes)
  const zip = await JSZip.loadAsync(result.bytes)
  await checkExpandedBudget(zip)
  const contentTypesXml = await zip.file("[Content_Types].xml")?.async("string")
  const contentTypes =
    typeof contentTypesXml === "string"
      ? parseContentTypes(contentTypesXml)
      : { byExtension: new Map<string, string>(), byPartName: new Map<string, string>() }
  return {
    drive,
    item,
    extension,
    zip,
    contentTypes,
    byteLength: result.bytes.byteLength,
    contentType: result.contentType || item.mimeType || "application/octet-stream",
  }
}

function getTextBearingPartPaths(zip: JSZip, includeTemplateParts: boolean) {
  return Object.keys(zip.files)
    .filter((path) => !zip.files[path]!.dir)
    .map((path) => ({ path, meta: parsePowerPointPart(path, includeTemplateParts) }))
    .filter((entry): entry is { path: string; meta: { kind: PowerPointTextPartKind; index: number | null } } =>
      Boolean(entry.meta),
    )
    .sort((left, right) => left.path.localeCompare(right.path, undefined, { numeric: true }))
}

function summarizeReplacementMap(
  replacements: readonly (readonly [string, string])[],
  countsByKey: Map<string, number>,
) {
  return replacements.map(([find, replaceWith]) => ({
    find,
    replace_with: replaceWith,
    match_count: countsByKey.get(find) || 0,
  }))
}

function getContentTypeForPart(contentTypes: ContentTypesMap, partPath: string) {
  const direct = contentTypes.byPartName.get(partPath)
  if (direct) {
    return direct
  }

  const extension = getFileExtension(partPath)
  return extension ? contentTypes.byExtension.get(extension) || null : null
}

async function inspectPowerPointMediaInternal(
  args: DriveItemArgs & {
    include_template_parts?: boolean
    include_external_media?: boolean
    max_bytes?: number
  },
) {
  const includeTemplateParts = args.include_template_parts !== false
  const includeExternalMedia = args.include_external_media !== false
  const loaded = await loadPowerPointPackage(args)

  const embeddedReferencesByPath = new Map<string, PowerPointMediaReference[]>()
  const externalReferencesByTarget = new Map<string, PowerPointMediaReference[]>()

  const relPaths = Object.keys(loaded.zip.files)
    .filter((path) => !loaded.zip.files[path]!.dir)
    .filter((path) => /\.rels$/i.test(path))
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))

  for (const relPath of relPaths) {
    const relsXml = await loaded.zip.file(relPath)?.async("string")
    if (typeof relsXml !== "string") {
      continue
    }

    const sourcePartPath = relPath
      .replace(/^(.+)\/_rels\/([^/]+)\.rels$/i, "$1/$2")
      .replace(/^_rels\/([^/]+)\.rels$/i, "$1")

    const references = extractMediaReferencesFromRelationships({
      relsXml,
      sourcePartPath,
      includeTemplateParts,
    })

    for (const reference of references) {
      if (reference.target_mode === "External") {
        if (!includeExternalMedia) {
          continue
        }
        const key = reference.target
        externalReferencesByTarget.set(key, [...(externalReferencesByTarget.get(key) || []), reference])
        continue
      }

      if (!reference.resolved_path) {
        continue
      }
      const key = reference.resolved_path
      embeddedReferencesByPath.set(key, [...(embeddedReferencesByPath.get(key) || []), reference])
    }
  }

  const embeddedMediaPaths = Object.keys(loaded.zip.files)
    .filter((path) => !loaded.zip.files[path]!.dir)
    .filter((path) => /^ppt\/media\//i.test(path))
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))

  const embeddedMedia = await Promise.all(
    embeddedMediaPaths.map(async (path) => {
      const bytes = await loaded.zip.file(path)?.async("uint8array")
      if (!(bytes instanceof Uint8Array)) {
        return null
      }

      const references = embeddedReferencesByPath.get(path) || []
      const extension = getFileExtension(path)
      const contentType = getContentTypeForPart(loaded.contentTypes, path)
      return {
        path,
        name: path.split("/").at(-1) || path,
        extension,
        media_kind: classifyMediaKind({ contentType, extension }),
        content_type: contentType,
        byte_length: bytes.byteLength,
        references_count: references.length,
        references,
      } satisfies PowerPointEmbeddedMedia
    }),
  )

  const externalMedia = Array.from(externalReferencesByTarget.entries())
    .map(
      ([target, references]) =>
        ({
          target,
          media_kind: classifyMediaKind({
            relationshipType: references[0]?.relationship_type || null,
            extension: getFileExtension(target),
          }),
          relationship_type: references[0]?.relationship_type || null,
          references_count: references.length,
          references,
        }) satisfies PowerPointExternalMedia,
    )
    .sort((left, right) => left.target.localeCompare(right.target, undefined, { numeric: true }))

  return {
    drive: loaded.drive,
    item: loaded.item,
    file_extension: loaded.extension,
    byte_length: loaded.byteLength,
    include_template_parts: includeTemplateParts,
    include_external_media: includeExternalMedia,
    media_count: embeddedMedia.filter(Boolean).length,
    media: embeddedMedia.filter((item): item is NonNullable<typeof item> => Boolean(item)),
    external_media_count: externalMedia.length,
    external_media: externalMedia,
  }
}

async function inspectPowerPointStructureInternal(
  args: DriveItemArgs & {
    include_template_parts?: boolean
    max_bytes?: number
    max_text_chars?: number
  },
) {
  const includeTemplateParts = args.include_template_parts !== false
  const maxTextChars = readPositiveInt(args.max_text_chars, DEFAULT_MAX_TEXT_CHARS, MAX_TEXT_CHARS)
  const loaded = await loadPowerPointPackage(args)

  const parts = await Promise.all(
    Object.keys(loaded.zip.files)
      .filter((path) => !loaded.zip.files[path]!.dir)
      .map((path) => ({ path, meta: parsePowerPointStructurePart(path, includeTemplateParts) }))
      .filter((entry): entry is { path: string; meta: { kind: PowerPointStructurePartKind; index: number | null } } =>
        Boolean(entry.meta),
      )
      .sort((left, right) => left.path.localeCompare(right.path, undefined, { numeric: true }))
      .map(async ({ path, meta }) => {
        const xml = await loaded.zip.file(path)?.async("string")
        if (typeof xml !== "string") {
          return null
        }
        return summarizeStructurePart(path, meta.kind, meta.index, xml, maxTextChars)
      }),
  )

  const filteredParts = parts.filter((part): part is NonNullable<typeof part> => Boolean(part))
  const slides = filteredParts.filter((part) => part.kind === "slide")
  const slideLayouts = filteredParts.filter((part) => part.kind === "slide_layout")
  const slideMasters = filteredParts.filter((part) => part.kind === "slide_master")
  const coreXml = await loaded.zip.file("docProps/core.xml")?.async("string")
  const presentationXml = await loaded.zip.file("ppt/presentation.xml")?.async("string")

  return {
    drive: loaded.drive,
    item: loaded.item,
    file_extension: loaded.extension,
    byte_length: loaded.byteLength,
    include_template_parts: includeTemplateParts,
    max_text_chars: maxTextChars,
    slide_count: slides.length,
    slide_layout_count: slideLayouts.length,
    slide_master_count: slideMasters.length,
    core_properties: typeof coreXml === "string" ? parseCoreProperties(coreXml) : null,
    presentation_size: typeof presentationXml === "string" ? parsePresentationSize(presentationXml) : null,
    slides,
    slide_layouts: slideLayouts,
    slide_masters: slideMasters,
  }
}

function pickInheritedPowerPointArgs(args: Record<string, unknown>) {
  return {
    site_id: args.site_id,
    site_name: args.site_name,
    site_url: args.site_url,
    hostname: args.hostname,
    site_path: args.site_path,
    drive_id: args.drive_id,
    library_name: args.library_name,
    include_template_parts: args.include_template_parts,
    include_external_media: args.include_external_media,
    max_bytes: args.max_bytes,
    force_refresh: args.force_refresh,
  } satisfies Record<string, unknown>
}

async function uploadUpdatedPowerPoint(args: {
  drive: DriveSummary
  item: DriveItemSummary
  bytes: Uint8Array
  contentType: string
  force_refresh?: boolean
}) {
  if (!args.drive.id || !args.item.id || !args.item.name) {
    throw new Error("The resolved PowerPoint file does not expose the ids needed to upload content.")
  }

  const result = await executeGraphRawRequest({
    path: `/drives/${encodePathSegment(args.drive.id)}/items/${encodePathSegment(args.item.id)}/content`,
    method: "PUT",
    body: args.bytes,
    content_type: args.contentType,
    force_refresh: Boolean(args.force_refresh),
  })

  return summarizeUploadedDriveItem(result.result, args.drive)
}

export async function inspectPowerPointText(
  args: DriveItemArgs & {
    include_template_parts?: boolean
    max_bytes?: number
    max_text_chars?: number
  },
) {
  const includeTemplateParts = args.include_template_parts !== false
  const maxTextChars = readPositiveInt(args.max_text_chars, DEFAULT_MAX_TEXT_CHARS, MAX_TEXT_CHARS)
  const loaded = await loadPowerPointPackage(args)
  const parts = await Promise.all(
    getTextBearingPartPaths(loaded.zip, includeTemplateParts).map(async ({ path, meta }) => {
      const xml = await loaded.zip.file(path)?.async("string")
      if (typeof xml !== "string") {
        return null
      }
      const summary = summarizeTextPart(path, meta.kind, meta.index, xml)
      return {
        ...summary,
        text: truncateText(summary.text, maxTextChars),
      }
    }),
  )

  const filteredParts = parts.filter((part): part is NonNullable<typeof part> => Boolean(part))
  return {
    drive: loaded.drive,
    item: loaded.item,
    file_extension: loaded.extension,
    byte_length: loaded.byteLength,
    include_template_parts: includeTemplateParts,
    max_text_chars: maxTextChars,
    part_count: filteredParts.length,
    parts: filteredParts,
  }
}

export async function inspectPowerPointMedia(
  args: DriveItemArgs & {
    include_template_parts?: boolean
    include_external_media?: boolean
    max_bytes?: number
  },
) {
  return inspectPowerPointMediaInternal(args)
}

export async function inspectPowerPointStructure(
  args: DriveItemArgs & {
    include_template_parts?: boolean
    max_bytes?: number
    max_text_chars?: number
  },
) {
  return inspectPowerPointStructureInternal(args)
}

export async function inspectMultiplePowerPointMedia(
  args: DriveItemArgs & {
    items?: unknown
    include_template_parts?: boolean
    include_external_media?: boolean
    max_bytes?: number
    limit?: number
  },
) {
  const inheritedArgs = pickInheritedPowerPointArgs(args as Record<string, unknown>)
  const limit = readPositiveInt(args.limit, DEFAULT_POWERPOINT_BATCH_LIMIT, MAX_POWERPOINT_BATCH_LIMIT)
  const items = getBatchItems(args.items)
  if (items.length === 0) {
    throw new Error("Provide items as a non-empty array of PowerPoint file references.")
  }

  const selectedItems = items.slice(0, limit)
  const results = [] as Array<{
    ok: boolean
    item_reference: string
    result?: unknown
    error?: string
  }>

  for (const itemArgs of selectedItems) {
    const mergedArgs = {
      ...inheritedArgs,
      ...itemArgs,
    } satisfies Record<string, unknown>
    const itemReference = getItemReferenceLabel(mergedArgs)

    try {
      const result = await inspectPowerPointMediaInternal(
        mergedArgs as DriveItemArgs & {
          include_template_parts?: boolean
          include_external_media?: boolean
          max_bytes?: number
        },
      )
      results.push({
        ok: true,
        item_reference: itemReference,
        result,
      })
    } catch (error) {
      throwIfCancelled()
      results.push({
        ok: false,
        item_reference: itemReference,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }

  return {
    count: results.length,
    truncated: items.length > selectedItems.length,
    requested_count: items.length,
    limit,
    results,
  }
}

export async function replacePowerPointText(
  args: DriveItemArgs & {
    replacements: unknown
    include_template_parts?: boolean
    preview_only?: boolean
    fail_if_missing?: boolean
    max_bytes?: number
    max_text_chars?: number
  },
) {
  const replacements = normalizeReplacements(args.replacements)
  const includeTemplateParts = args.include_template_parts !== false
  const previewOnly = getBoolean(args.preview_only) !== false
  const failIfMissing = getBoolean(args.fail_if_missing) === true
  const maxTextChars = readPositiveInt(args.max_text_chars, DEFAULT_MAX_TEXT_CHARS, MAX_TEXT_CHARS)
  const loaded = await loadPowerPointPackage(args)

  const processedParts: ProcessedPart[] = []
  const aggregateCounts = new Map<string, number>()
  for (const { path, meta } of getTextBearingPartPaths(loaded.zip, includeTemplateParts)) {
    const file = loaded.zip.file(path)
    if (!file) {
      continue
    }

    const xml = await file.async("string")
    const originalPart = summarizeTextPart(path, meta.kind, meta.index, xml)
    const replaced = replaceTextRunsInXml(xml, replacements)

    if (replaced.replacementCount === 0) {
      continue
    }

    for (const [find, count] of replaced.countsByKey.entries()) {
      aggregateCounts.set(find, (aggregateCounts.get(find) || 0) + count)
    }

    const updatedPart = summarizeTextPart(path, meta.kind, meta.index, replaced.updatedText)
    processedParts.push({
      kind: meta.kind,
      index: meta.index,
      path,
      textRunCount: originalPart.text_run_count,
      originalText: originalPart.text,
      updatedText: updatedPart.text,
      replacementCount: replaced.replacementCount,
      countsByKey: replaced.countsByKey,
    })

    if (!previewOnly) {
      loaded.zip.file(path, replaced.updatedText)
    }
  }

  if (failIfMissing) {
    const missing = replacements.filter(([find]) => !aggregateCounts.has(find)).map(([find]) => find)
    if (missing.length > 0) {
      throw new Error(`Some requested replacements were not found: ${missing.join(", ")}`)
    }
  }

  const modifiedParts = processedParts.map((part) => ({
    kind: part.kind,
    index: part.index,
    path: part.path,
    replacement_count: part.replacementCount,
    replacements: summarizeReplacementMap(replacements, part.countsByKey),
    text_before_preview: truncateText(part.originalText, maxTextChars),
    text_after_preview: truncateText(part.updatedText, maxTextChars),
  }))

  const result = {
    drive: loaded.drive,
    item: loaded.item,
    file_extension: loaded.extension,
    byte_length: loaded.byteLength,
    include_template_parts: includeTemplateParts,
    preview_only: previewOnly,
    replacement_summary: summarizeReplacementMap(replacements, aggregateCounts),
    modified_part_count: modifiedParts.length,
    modified_parts: modifiedParts,
  }

  if (previewOnly || modifiedParts.length === 0) {
    return result
  }

  const uploadedBytes = await loaded.zip.generateAsync({ type: "uint8array", compression: "DEFLATE" })
  const uploadedItem = await uploadUpdatedPowerPoint({
    drive: loaded.drive,
    item: loaded.item,
    bytes: uploadedBytes,
    contentType: loaded.contentType,
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ...result,
    updated_byte_length: uploadedBytes.byteLength,
    updated_item: uploadedItem,
  }
}
