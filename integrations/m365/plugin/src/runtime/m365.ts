import { isRecord, type JsonRecord } from "./auth.ts"
import { downloadGraphBytes, executeGraphRawRequest, graphResult } from "./graph.ts"
import { chooseSingleMatch, clampPositiveInt, filterAndSortMatches, scoreBestValue } from "./validators.ts"

const DEFAULT_SITE_LIST_LIMIT = 20
const DEFAULT_LIBRARY_LIST_LIMIT = 50
const DEFAULT_ITEM_LIST_LIMIT = 100
const DEFAULT_VERSION_LIST_LIMIT = 20
const DEFAULT_DOWNLOAD_MAX_BYTES = 5 * 1024 * 1024
const MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
const MAX_SIMPLE_UPLOAD_BYTES = 4 * 1024 * 1024

export type SiteSummary = {
  id: string | null
  displayName: string | null
  description: string | null
  webUrl: string | null
  hostname: string | null
  serverRelativePath: string | null
  createdDateTime: string | null
}

export type DriveSummary = {
  id: string | null
  name: string | null
  description: string | null
  driveType: string | null
  webUrl: string | null
  createdDateTime: string | null
  lastModifiedDateTime: string | null
  siteId: string | null
  siteName: string | null
}

export type IdentitySummary = {
  id: string | null
  displayName: string | null
  email: string | null
}

export type DriveItemSummary = {
  id: string | null
  name: string | null
  path: string | null
  parentPath: string | null
  webUrl: string | null
  createdDateTime: string | null
  lastModifiedDateTime: string | null
  size: number | null
  eTag: string | null
  cTag: string | null
  mimeType: string | null
  extension: string | null
  downloadUrl: string | null
  isFolder: boolean
  isFile: boolean
  isPackage: boolean
  folderChildCount: number | null
  driveId: string | null
  siteId: string | null
  createdBy: IdentitySummary | null
  lastModifiedBy: IdentitySummary | null
  drive: DriveSummary | null
}

export type DriveItemVersionSummary = {
  id: string | null
  lastModifiedDateTime: string | null
  size: number | null
  isCurrentVersion: boolean | null
  lastModifiedBy: IdentitySummary | null
}

export type ShareLinkSummary = {
  id: string | null
  roles: string[]
  shareId: string | null
  webUrl: string | null
  type: string | null
  scope: string | null
  preventsDownload: boolean | null
}

type SiteReferenceArgs = {
  site_id?: string
  site_name?: string
  site_url?: string
  hostname?: string
  site_path?: string
  force_refresh?: boolean
}

type DriveReferenceArgs = SiteReferenceArgs & {
  drive_id?: string
  library_name?: string
  query?: string
  limit?: number
  force_refresh?: boolean
}

type DriveItemReferenceArgs = DriveReferenceArgs & {
  item_id?: string
  item_path?: string
  force_refresh?: boolean
}

type DriveContainerReferenceArgs = DriveReferenceArgs & {
  parent_item_id?: string
  parent_path?: string
  force_refresh?: boolean
}

function getString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null
}

function requireString(value: unknown, label: string) {
  const text = getString(value)
  if (!text) {
    throw new Error(`Provide ${label}.`)
  }

  return text
}

function getNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function getBoolean(value: unknown) {
  return typeof value === "boolean" ? value : null
}

function getRecord(value: unknown) {
  return isRecord(value) ? value : null
}

function getRecordArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as JsonRecord[]
  }

  return value.filter((item): item is JsonRecord => isRecord(item))
}

function getStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as string[]
  }

  return value.map((item) => getString(item)).filter((item): item is string => Boolean(item))
}

function getCollectionItems(value: unknown) {
  if (!isRecord(value)) {
    return [] as JsonRecord[]
  }

  return getRecordArray(value.value)
}

function encodePathSegment(value: string) {
  return encodeURIComponent(value)
}

function encodeDrivePath(value: string) {
  return value
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/")
}

function encodeGraphSearchText(value: string) {
  return encodeURIComponent(value.replace(/'/g, "''"))
}

function validateDriveItemName(value: string, label: string) {
  if (/[\\/]/.test(value)) {
    throw new Error(`${label} must be a single item name, not a path.`)
  }

  return value
}

function normalizeItemPath(value: string | undefined) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "")
}

function readMaxBytes(value: unknown, fallback: number) {
  return clampPositiveInt(value, fallback, MAX_DOWNLOAD_BYTES)
}

function tryParseUrl(value: string | null) {
  if (!value) {
    return null
  }

  try {
    return new URL(value)
  } catch {
    return null
  }
}

function buildSiteLookupPath(hostname: string, serverRelativePath: string | null) {
  if (!serverRelativePath || serverRelativePath === "/") {
    return "/sites/root"
  }

  return `/sites/${encodePathSegment(hostname)}:${serverRelativePath}`
}

function buildIdentitySummary(value: unknown) {
  const record = getRecord(value)
  const user = getRecord(record?.user)
  const application = getRecord(record?.application)

  if (user) {
    return {
      id: getString(user.id),
      displayName: getString(user.displayName),
      email: getString(user.email) || getString(user.userPrincipalName),
    } satisfies IdentitySummary
  }

  if (application) {
    return {
      id: getString(application.id),
      displayName: getString(application.displayName),
      email: null,
    } satisfies IdentitySummary
  }

  if (record) {
    const displayName = getString(record.displayName)
    const id = getString(record.id)
    const email = getString(record.email) || getString(record.userPrincipalName)
    if (displayName || id || email) {
      return {
        id,
        displayName,
        email,
      } satisfies IdentitySummary
    }
  }

  return null
}

function getRelativePathFromParent(parentPath: string | null, itemName: string | null) {
  const parentText = getString(parentPath)
  if (!parentText) {
    return itemName
  }

  const marker = ":/"
  const index = parentText.indexOf(marker)
  const relativeParent = index >= 0 ? parentText.slice(index + marker.length) : ""
  const parts = [relativeParent, itemName].filter(Boolean)
  return parts.length > 0 ? parts.join("/") : null
}

function summarizeSite(value: unknown) {
  const record = getRecord(value)
  const webUrl = getString(record?.webUrl)
  const parsed = tryParseUrl(webUrl)

  return {
    id: getString(record?.id),
    displayName: getString(record?.displayName) || getString(record?.name),
    description: getString(record?.description),
    webUrl,
    hostname: parsed?.hostname || null,
    serverRelativePath: parsed?.pathname || null,
    createdDateTime: getString(record?.createdDateTime),
  } satisfies SiteSummary
}

function summarizeDrive(value: unknown, site: SiteSummary | null = null) {
  const record = getRecord(value)

  return {
    id: getString(record?.id),
    name: getString(record?.name),
    description: getString(record?.description),
    driveType: getString(record?.driveType),
    webUrl: getString(record?.webUrl),
    createdDateTime: getString(record?.createdDateTime),
    lastModifiedDateTime: getString(record?.lastModifiedDateTime),
    siteId: site?.id || null,
    siteName: site?.displayName || null,
  } satisfies DriveSummary
}

function summarizeDriveItem(value: unknown, drive: DriveSummary | null = null) {
  const record = getRecord(value)
  const file = getRecord(record?.file)
  const folder = getRecord(record?.folder)
  const packageInfo = getRecord(record?.package)
  const parentReference = getRecord(record?.parentReference)
  const parentPath = getString(parentReference?.path)
  const name = getString(record?.name)

  return {
    id: getString(record?.id),
    name,
    path: getRelativePathFromParent(parentPath, name),
    parentPath: parentPath ? getRelativePathFromParent(parentPath, null) : null,
    webUrl: getString(record?.webUrl),
    createdDateTime: getString(record?.createdDateTime),
    lastModifiedDateTime: getString(record?.lastModifiedDateTime),
    size: getNumber(record?.size),
    eTag: getString(record?.eTag),
    cTag: getString(record?.cTag),
    mimeType: getString(file?.mimeType),
    extension: name?.includes(".") ? name.split(".").pop() || null : null,
    downloadUrl: null,
    isFolder: Boolean(folder),
    isFile: Boolean(file),
    isPackage: Boolean(packageInfo),
    folderChildCount: getNumber(folder?.childCount),
    driveId: getString(parentReference?.driveId) || drive?.id || null,
    siteId: getString(parentReference?.siteId) || drive?.siteId || null,
    createdBy: buildIdentitySummary(record?.createdBy),
    lastModifiedBy: buildIdentitySummary(record?.lastModifiedBy),
    drive,
  } satisfies DriveItemSummary
}

function summarizeDriveItemVersion(value: unknown) {
  const record = getRecord(value)

  return {
    id: getString(record?.id),
    lastModifiedDateTime: getString(record?.lastModifiedDateTime),
    size: getNumber(record?.size),
    isCurrentVersion: getBoolean(record?.isCurrentVersion),
    lastModifiedBy: buildIdentitySummary(record?.lastModifiedBy),
  } satisfies DriveItemVersionSummary
}

function summarizeSharePermission(value: unknown) {
  const record = getRecord(value)
  const link = getRecord(record?.link)

  return {
    id: getString(record?.id),
    roles: getStringArray(record?.roles),
    shareId: getString(record?.shareId),
    webUrl: getString(link?.webUrl),
    type: getString(link?.type),
    scope: getString(link?.scope),
    preventsDownload: getBoolean(link?.preventsDownload),
  } satisfies ShareLinkSummary
}

async function getSiteById(siteId: string, forceRefresh = false) {
  const value = await graphResult({
    path: `/sites/${encodePathSegment(siteId)}`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return summarizeSite(value)
}

async function getSiteByAddress(args: SiteReferenceArgs) {
  const siteUrl = getString(args.site_url)
  const parsed = tryParseUrl(siteUrl)
  const hostname = getString(args.hostname) || parsed?.hostname || null
  const serverRelativePath = getString(args.site_path) || parsed?.pathname || null

  if (!hostname) {
    throw new Error("Provide site_url, or provide both hostname and site_path to resolve a SharePoint site.")
  }

  const value = await graphResult({
    path: buildSiteLookupPath(hostname, serverRelativePath),
    method: "GET",
    force_refresh: Boolean(args.force_refresh),
  })

  return summarizeSite(value)
}

export async function listSites(args: { query?: string; limit?: number; force_refresh?: boolean }) {
  const limit = clampPositiveInt(args.limit, DEFAULT_SITE_LIST_LIMIT)
  const forceRefresh = Boolean(args.force_refresh)

  let rawSites: SiteSummary[] = []
  if (getString(args.query)) {
    const result = await graphResult({
      path: "/sites",
      method: "GET",
      query: {
        search: args.query,
      },
      force_refresh: forceRefresh,
    })
    rawSites = getCollectionItems(result).map(summarizeSite)
  } else {
    const [root, children] = await Promise.all([
      graphResult({ path: "/sites/root", method: "GET", force_refresh: forceRefresh }),
      graphResult({ path: "/sites/root/sites", method: "GET", force_refresh: forceRefresh }),
    ])

    rawSites = [summarizeSite(root), ...getCollectionItems(children).map(summarizeSite)]
  }

  const seenSiteIds = new Set<string>()
  const deduped = rawSites.filter((site) => {
    if (!site.id) {
      return true
    }

    if (seenSiteIds.has(site.id)) {
      return false
    }

    seenSiteIds.add(site.id)
    return true
  })

  const filtered = filterAndSortMatches(deduped, getString(args.query) || undefined, (site) => [
    site.displayName,
    site.webUrl,
    site.serverRelativePath,
  ]).slice(0, limit)

  return {
    count: filtered.length,
    sites: filtered,
  }
}

export async function resolveSiteReference(args: SiteReferenceArgs) {
  if (args.site_id) {
    return getSiteById(args.site_id, Boolean(args.force_refresh))
  }

  if (args.site_url || args.hostname) {
    return getSiteByAddress(args)
  }

  if (!args.site_name) {
    throw new Error(
      "Provide site_id, site_name, site_url, or hostname/site_path. Use m365_list_sites to discover sites.",
    )
  }

  const sites = (
    await listSites({
      query: args.site_name,
      limit: DEFAULT_SITE_LIST_LIMIT,
      force_refresh: args.force_refresh,
    })
  ).sites

  const match = chooseSingleMatch(
    sites,
    (site) => scoreBestValue([site.displayName, site.webUrl, site.serverRelativePath], args.site_name),
    (site) => `${site.displayName || site.serverRelativePath || site.id} [${site.id}]`,
    "site",
  )

  if (!match) {
    throw new Error("No matching site was found. Use m365_list_sites to inspect available sites.")
  }

  return match
}

async function getDriveById(driveId: string, forceRefresh = false) {
  const value = await graphResult({
    path: `/drives/${encodePathSegment(driveId)}`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return summarizeDrive(value)
}

async function listSiteDrives(site: SiteSummary, forceRefresh = false) {
  if (!site.id) {
    throw new Error("The resolved SharePoint site does not expose a valid id.")
  }

  const result = await graphResult({
    path: `/sites/${encodePathSegment(site.id)}/drives`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return getCollectionItems(result).map((item) => summarizeDrive(item, site))
}

export async function listDocumentLibraries(args: DriveReferenceArgs) {
  const site = await resolveSiteReference(args)
  const libraries = await listSiteDrives(site, Boolean(args.force_refresh))
  const filtered = filterAndSortMatches(
    libraries,
    getString(args.query) || getString(args.library_name) || undefined,
    (library) => [library.name, library.description, library.webUrl],
  ).slice(0, clampPositiveInt(args.limit, DEFAULT_LIBRARY_LIST_LIMIT))

  return {
    site,
    count: filtered.length,
    libraries: filtered,
  }
}

export async function resolveDriveReference(args: DriveReferenceArgs) {
  if (args.drive_id) {
    return getDriveById(args.drive_id, Boolean(args.force_refresh))
  }

  const site = await resolveSiteReference(args)
  const libraries = await listSiteDrives(site, Boolean(args.force_refresh))

  if (!args.library_name) {
    if (libraries.length === 1) {
      return libraries[0]!
    }

    throw new Error(
      "Provide drive_id or library_name when a site exposes multiple document libraries. Use m365_list_document_libraries first.",
    )
  }

  const match = chooseSingleMatch(
    libraries,
    (library) => scoreBestValue([library.name, library.description, library.webUrl], args.library_name),
    (library) => `${library.name || library.id} [${library.id}]`,
    "document library",
  )

  if (!match) {
    throw new Error(
      "No matching document library was found. Use m365_list_document_libraries to inspect available libraries.",
    )
  }

  return match
}

async function getDriveRoot(drive: DriveSummary, forceRefresh = false) {
  if (!drive.id) {
    throw new Error("The resolved drive does not expose a valid id.")
  }

  const value = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/root`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return summarizeDriveItem(value, drive)
}

async function getDriveItemById(drive: DriveSummary, itemId: string, forceRefresh = false) {
  if (!drive.id) {
    throw new Error("The resolved drive does not expose a valid id.")
  }

  const value = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(itemId)}`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return summarizeDriveItem(value, drive)
}

async function getDriveItemByPath(drive: DriveSummary, itemPath: string, forceRefresh = false) {
  if (!drive.id) {
    throw new Error("The resolved drive does not expose a valid id.")
  }

  const normalizedPath = normalizeItemPath(itemPath)
  if (!normalizedPath) {
    return getDriveRoot(drive, forceRefresh)
  }

  const value = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/root:/${encodeDrivePath(normalizedPath)}`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return summarizeDriveItem(value, drive)
}

export async function resolveDriveItemReference(args: DriveItemReferenceArgs) {
  const drive = await resolveDriveReference(args)
  const forceRefresh = Boolean(args.force_refresh)

  if (args.item_id) {
    return {
      drive,
      item: await getDriveItemById(drive, args.item_id, forceRefresh),
    }
  }

  return {
    drive,
    item: await getDriveItemByPath(drive, args.item_path || "", forceRefresh),
  }
}

async function resolveDriveContainerReference(args: DriveContainerReferenceArgs) {
  return resolveDriveItemReference({
    ...args,
    item_id: args.parent_item_id,
    item_path: args.parent_path,
  })
}

export async function listDriveItems(args: DriveItemReferenceArgs & { limit?: number; query?: string }) {
  const { drive, item: container } = await resolveDriveItemReference(args)
  if (!drive.id) {
    throw new Error("The resolved drive does not expose a valid id.")
  }

  if (!container.id && container.path) {
    throw new Error("The resolved container item does not expose a valid id.")
  }

  if (!container.isFolder) {
    throw new Error("The requested item is not a folder. Use m365_get_drive_item to inspect a file directly.")
  }

  const normalizedPath = normalizeItemPath(args.item_path)
  const listPath = args.item_id
    ? `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(args.item_id)}/children`
    : normalizedPath
      ? `/drives/${encodePathSegment(drive.id)}/root:/${encodeDrivePath(normalizedPath)}:/children`
      : `/drives/${encodePathSegment(drive.id)}/root/children`

  const result = await graphResult({
    path: listPath,
    method: "GET",
    query: {
      $top: clampPositiveInt(args.limit, DEFAULT_ITEM_LIST_LIMIT),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const items = getCollectionItems(result).map((value) => summarizeDriveItem(value, drive))
  const filtered = filterAndSortMatches(items, getString(args.query) || undefined, (item) => [
    item.name,
    item.path,
    item.mimeType,
    item.webUrl,
  ])

  return {
    drive,
    container,
    count: filtered.length,
    items: filtered,
  }
}

export async function getDriveItem(args: DriveItemReferenceArgs) {
  return resolveDriveItemReference(args)
}

export async function listFileVersions(args: DriveItemReferenceArgs & { limit?: number }) {
  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved file item does not expose the ids needed to read versions.")
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}/versions`,
    method: "GET",
    query: {
      $top: clampPositiveInt(args.limit, DEFAULT_VERSION_LIST_LIMIT),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const versions = getCollectionItems(result).map(summarizeDriveItemVersion)
  return {
    drive,
    item,
    count: versions.length,
    versions,
  }
}

export async function createShareLink(
  args: DriveItemReferenceArgs & {
    link_type?: string
    scope?: string
    retain_inherited_permissions?: boolean
    expiration_datetime?: string
  },
) {
  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved file item does not expose the ids needed to create a share link.")
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}/createLink`,
    method: "POST",
    body: {
      type: getString(args.link_type) || "view",
      scope: getString(args.scope) || "organization",
      ...(typeof args.retain_inherited_permissions === "boolean"
        ? { retainInheritedPermissions: args.retain_inherited_permissions }
        : {}),
      ...(getString(args.expiration_datetime) ? { expirationDateTime: args.expiration_datetime } : {}),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    drive,
    item,
    link: summarizeSharePermission(result),
  }
}

export async function searchDriveItems(
  args: Omit<DriveReferenceArgs, "query"> & {
    query: string
    limit?: number
  },
) {
  const query = requireString(args.query, "query")
  const drive = await resolveDriveReference(args)
  if (!drive.id) {
    throw new Error("The resolved drive does not expose a valid id.")
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/root/search(q='${encodeGraphSearchText(query)}')`,
    method: "GET",
    query: {
      $top: clampPositiveInt(args.limit, DEFAULT_ITEM_LIST_LIMIT),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const items = getCollectionItems(result).map((value) => summarizeDriveItem(value, drive))
  return {
    drive,
    query,
    count: items.length,
    items,
  }
}

export async function createDriveFolder(
  args: DriveContainerReferenceArgs & {
    folder_name: string
    conflict_behavior?: string
  },
) {
  const folderName = validateDriveItemName(requireString(args.folder_name, "folder_name"), "folder_name")
  const { drive, item: parent } = await resolveDriveContainerReference(args)
  if (!drive.id || !parent.id) {
    throw new Error("The resolved parent folder does not expose the ids needed to create a folder.")
  }

  if (!parent.isFolder) {
    throw new Error("The resolved parent item is not a folder.")
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(parent.id)}/children`,
    method: "POST",
    body: {
      name: folderName,
      folder: {},
      "@microsoft.graph.conflictBehavior": getString(args.conflict_behavior) || "rename",
    },
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    drive,
    parent,
    folder: summarizeDriveItem(result, drive),
  }
}

export async function downloadDriveItem(args: DriveItemReferenceArgs & { max_bytes?: number }) {
  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved file item does not expose the ids needed to download content.")
  }

  if (!item.isFile) {
    throw new Error("The requested item is not a file. Download folders as an archive outside this small-file helper.")
  }

  const maxBytes = readMaxBytes(args.max_bytes, DEFAULT_DOWNLOAD_MAX_BYTES)
  const result = await downloadGraphBytes({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}/content`,
    max_bytes: maxBytes,
    force_refresh: Boolean(args.force_refresh),
  })

  if (result.contentLength !== null && result.contentLength > maxBytes) {
    throw new Error(`File is too large to return safely (${result.contentLength} bytes > ${maxBytes} bytes).`)
  }

  if (result.bytes.byteLength > maxBytes) {
    throw new Error(`File is too large to return safely (${result.bytes.byteLength} bytes > ${maxBytes} bytes).`)
  }

  return {
    drive,
    item,
    content_type: result.contentType || item.mimeType,
    byte_length: result.bytes.byteLength,
    content_base64: Buffer.from(result.bytes).toString("base64"),
  }
}

export async function uploadSmallDriveItem(
  args: DriveContainerReferenceArgs & {
    file_name: string
    content_base64: string
    content_type?: string
    conflict_behavior?: string
  },
) {
  const fileName = validateDriveItemName(requireString(args.file_name, "file_name"), "file_name")
  const contentBase64 = requireString(args.content_base64, "content_base64")
  const bytes = Buffer.from(contentBase64, "base64")

  if (bytes.byteLength === 0) {
    throw new Error("content_base64 decoded to an empty file.")
  }

  if (bytes.byteLength > MAX_SIMPLE_UPLOAD_BYTES) {
    throw new Error(
      `File is too large for simple upload (${bytes.byteLength} bytes > ${MAX_SIMPLE_UPLOAD_BYTES} bytes). Use an upload session workflow instead.`,
    )
  }

  const { drive, item: parent } = await resolveDriveContainerReference(args)
  if (!drive.id || !parent.id) {
    throw new Error("The resolved parent folder does not expose the ids needed to upload content.")
  }

  if (!parent.isFolder) {
    throw new Error("The resolved parent item is not a folder.")
  }

  const result = await executeGraphRawRequest({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(parent.id)}:/${encodePathSegment(fileName)}:/content`,
    method: "PUT",
    query: {
      "@microsoft.graph.conflictBehavior": getString(args.conflict_behavior) || "rename",
    },
    body: bytes,
    content_type: getString(args.content_type) || "application/octet-stream",
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    drive,
    parent,
    byte_length: bytes.byteLength,
    item: summarizeDriveItem(result.result, drive),
  }
}

export async function updateDriveItem(
  args: DriveItemReferenceArgs & {
    new_name?: string
    target_parent_item_id?: string
    target_parent_path?: string
  },
) {
  const newName = getString(args.new_name)
  const targetParentItemId = getString(args.target_parent_item_id)
  const targetParentPath = getString(args.target_parent_path)

  if (!newName && !targetParentItemId && !targetParentPath) {
    throw new Error("Provide new_name, target_parent_item_id or target_parent_path.")
  }

  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved item does not expose the ids needed to update it.")
  }

  const body: JsonRecord = {}
  if (newName) {
    body.name = validateDriveItemName(newName, "new_name")
  }

  let targetParent: DriveItemSummary | null = null
  if (targetParentItemId || targetParentPath) {
    const resolvedParent = await resolveDriveContainerReference({
      ...args,
      parent_item_id: targetParentItemId || undefined,
      parent_path: targetParentPath || undefined,
    })

    if (resolvedParent.drive.id !== drive.id) {
      throw new Error("Moving items across drives is not supported by this helper.")
    }

    if (!resolvedParent.item.id || !resolvedParent.item.isFolder) {
      throw new Error("The target parent must resolve to a folder with an id.")
    }

    targetParent = resolvedParent.item
    body.parentReference = { id: resolvedParent.item.id }
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}`,
    method: "PATCH",
    body,
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    drive,
    previous_item: item,
    target_parent: targetParent,
    item: summarizeDriveItem(result, drive),
  }
}

export async function deleteDriveItem(args: DriveItemReferenceArgs & { confirm?: boolean }) {
  if (args.confirm !== true) {
    throw new Error("Set confirm=true to delete a drive item.")
  }

  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved item does not expose the ids needed to delete it.")
  }

  await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}`,
    method: "DELETE",
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ok: true,
    drive,
    deleted_item: item,
  }
}
