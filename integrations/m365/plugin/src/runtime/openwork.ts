import { isRecord, type JsonRecord } from "./auth.ts"
import { throwIfCancelled } from "./cancellation.ts"
import { graphResult } from "./graph.ts"
import {
  createDriveFolder,
  createShareLink,
  deleteDriveItem,
  getDriveItem,
  listSites,
  searchDriveItems,
  updateDriveItem,
} from "./m365.ts"
import { searchMailMessages, sendMailMessage } from "./outlook.ts"
import { listChannels, listChats, sendChannelMessage, sendChatMessage } from "./teams.ts"
import { clampPositiveInt } from "./validators.ts"

const DEFAULT_SEARCH_LIMIT = 10
const MAX_SEARCH_LIMIT = 50
const DEFAULT_BATCH_LIMIT = 25
const MAX_BATCH_LIMIT = 100

type SearchSource = "sites" | "files" | "mail" | "teams_chats" | "teams_channels"
type NotifyTarget = "chat" | "channel" | "email"
type BatchActionType = "create_folder" | "rename" | "move" | "delete" | "share_link"

type WorkspaceSearchArgs = {
  query?: string
  sources?: unknown[]
  limit?: number
  site_id?: string
  site_name?: string
  site_url?: string
  hostname?: string
  site_path?: string
  drive_id?: string
  library_name?: string
  force_refresh?: boolean
}

type BatchAction = JsonRecord & {
  action?: unknown
  item_id?: unknown
  item_path?: unknown
  parent_item_id?: unknown
  parent_path?: unknown
  folder_name?: unknown
  new_name?: unknown
  target_parent_item_id?: unknown
  target_parent_path?: unknown
}

type BatchDriveItemsArgs = {
  site_id?: string
  site_name?: string
  site_url?: string
  hostname?: string
  site_path?: string
  drive_id?: string
  library_name?: string
  actions?: unknown[]
  dry_run?: boolean
  confirm?: boolean
  force_refresh?: boolean
}

type NotifyArgs = JsonRecord & {
  target?: unknown
  message?: unknown
  subject?: unknown
  preview_only?: unknown
  confirm?: unknown
  content_type?: unknown
  recipients?: unknown
  save_to_sent_items?: unknown
}

function getString(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null
}

function requireString(value: unknown, label: string) {
  const text = getString(value)
  if (!text) {
    throw new Error(`Provide ${label}.`)
  }

  return text
}

function getStringArray(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => getString(item)).filter((item): item is string => Boolean(item))
  }

  const single = getString(value)
  return single ? [single] : []
}

function hasDriveSearchScope(args: WorkspaceSearchArgs) {
  return Boolean(args.drive_id || args.library_name || args.site_id || args.site_name || args.site_url || args.hostname)
}

function readSources(value: unknown) {
  const supported = new Set<SearchSource>(["sites", "files", "mail", "teams_chats", "teams_channels"])
  const requested = getStringArray(value)
    .map((source) => source.toLowerCase())
    .filter((source): source is SearchSource => supported.has(source as SearchSource))

  return requested.length > 0
    ? requested
    : (["sites", "files", "mail", "teams_chats", "teams_channels"] satisfies SearchSource[])
}

function getRecordArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as JsonRecord[]
  }

  return value.filter((item): item is JsonRecord => isRecord(item))
}

function getCollectionItems(value: unknown) {
  return isRecord(value) ? getRecordArray(value.value) : []
}

function summarizeGraphSearchHit(hit: unknown) {
  const record = isRecord(hit) ? hit : {}
  const resource = isRecord(record.resource) ? record.resource : {}
  const parentReference = isRecord(resource.parentReference) ? resource.parentReference : {}

  return {
    id: getString(record.hitId) || getString(resource.id),
    rank: typeof record.rank === "number" ? record.rank : null,
    summary: getString(record.summary),
    name: getString(resource.name),
    webUrl: getString(resource.webUrl),
    size: typeof resource.size === "number" ? resource.size : null,
    driveId: getString(parentReference.driveId),
    siteId: getString(parentReference.siteId),
    itemId: getString(resource.id),
    lastModifiedDateTime: getString(resource.lastModifiedDateTime),
  }
}

async function graphSearchDriveItems(query: string, limit: number, forceRefresh = false) {
  const result = await graphResult({
    path: "/search/query",
    method: "POST",
    body: {
      requests: [
        {
          entityTypes: ["driveItem"],
          query: { queryString: query },
          from: 0,
          size: limit,
          fields: ["id", "name", "webUrl", "size", "parentReference", "lastModifiedDateTime"],
        },
      ],
    },
    force_refresh: forceRefresh,
  })

  const hits = getCollectionItems(result)
    .flatMap((entry) => getRecordArray(entry.hitsContainers))
    .flatMap((container) => getRecordArray(container.hits))
    .map(summarizeGraphSearchHit)

  return {
    query,
    count: hits.length,
    items: hits,
  }
}

async function collectSource(source: SearchSource, args: WorkspaceSearchArgs, query: string, limit: number) {
  try {
    if (source === "sites") {
      return { source, ok: true, result: await listSites({ query, limit, force_refresh: args.force_refresh }) }
    }

    if (source === "files") {
      const result = hasDriveSearchScope(args)
        ? await searchDriveItems({ ...args, query, limit })
        : await graphSearchDriveItems(query, limit, Boolean(args.force_refresh))
      return { source, ok: true, result }
    }

    if (source === "teams_chats") {
      return { source, ok: true, result: await listChats({ query, limit, force_refresh: args.force_refresh }) }
    }

    if (source === "mail") {
      return { source, ok: true, result: await searchMailMessages({ query, limit, force_refresh: args.force_refresh }) }
    }

    return { source, ok: true, result: await listChannels({ query, force_refresh: args.force_refresh }) }
  } catch (error) {
    throwIfCancelled()
    const message = error instanceof Error ? error.message : String(error)
    return { source, ok: false, error: message }
  }
}

export async function searchWorkspace(args: WorkspaceSearchArgs) {
  const query = requireString(args.query, "query")
  const sources = readSources(args.sources)
  const limit = clampPositiveInt(args.limit, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)
  const results = await Promise.all(sources.map((source) => collectSource(source, args, query, limit)))

  return {
    query,
    sources,
    limit_per_source: limit,
    results,
  }
}

function readBatchAction(value: unknown, index: number): BatchAction & { action: BatchActionType } {
  if (!isRecord(value)) {
    throw new Error(`Batch action at index ${index} must be an object.`)
  }

  const action = getString(value.action)
  const supported = new Set<BatchActionType>(["create_folder", "rename", "move", "delete", "share_link"])
  if (!supported.has(action as BatchActionType)) {
    throw new Error(`Unsupported batch action at index ${index}: ${action || "<missing>"}.`)
  }

  return { ...value, action: action as BatchActionType }
}

function commonDriveArgs(args: BatchDriveItemsArgs) {
  return {
    site_id: args.site_id,
    site_name: args.site_name,
    site_url: args.site_url,
    hostname: args.hostname,
    site_path: args.site_path,
    drive_id: args.drive_id,
    library_name: args.library_name,
    force_refresh: args.force_refresh,
  }
}

function sourceItemArgs(args: BatchDriveItemsArgs, action: BatchAction) {
  return {
    ...commonDriveArgs(args),
    item_id: getString(action.item_id) || undefined,
    item_path: getString(action.item_path) || undefined,
  }
}

function parentItemArgs(args: BatchDriveItemsArgs, action: BatchAction) {
  return {
    ...commonDriveArgs(args),
    item_id: getString(action.parent_item_id) || undefined,
    item_path: getString(action.parent_path) || undefined,
  }
}

async function planBatchAction(
  args: BatchDriveItemsArgs,
  action: BatchAction & { action: BatchActionType },
  index: number,
) {
  if (action.action === "create_folder") {
    const parent = await getDriveItem(parentItemArgs(args, action))
    return {
      index,
      action: action.action,
      folder_name: getString(action.folder_name),
      parent,
    }
  }

  const source = await getDriveItem(sourceItemArgs(args, action))
  return {
    index,
    action: action.action,
    source,
    new_name: getString(action.new_name),
    target_parent_item_id: getString(action.target_parent_item_id),
    target_parent_path: getString(action.target_parent_path),
  }
}

async function executeBatchAction(args: BatchDriveItemsArgs, action: BatchAction & { action: BatchActionType }) {
  if (action.action === "create_folder") {
    return createDriveFolder({
      ...commonDriveArgs(args),
      parent_item_id: getString(action.parent_item_id) || undefined,
      parent_path: getString(action.parent_path) || undefined,
      folder_name: requireString(action.folder_name, "folder_name"),
      conflict_behavior: getString(action.conflict_behavior) || undefined,
    })
  }

  if (action.action === "rename" || action.action === "move") {
    return updateDriveItem({
      ...sourceItemArgs(args, action),
      new_name: getString(action.new_name) || undefined,
      target_parent_item_id: getString(action.target_parent_item_id) || undefined,
      target_parent_path: getString(action.target_parent_path) || undefined,
    })
  }

  if (action.action === "delete") {
    return deleteDriveItem({
      ...sourceItemArgs(args, action),
      confirm: true,
    })
  }

  return createShareLink({
    ...sourceItemArgs(args, action),
    link_type: getString(action.link_type) || undefined,
    scope: getString(action.scope) || undefined,
    expiration_datetime: getString(action.expiration_datetime) || undefined,
  })
}

export async function batchDriveItems(args: BatchDriveItemsArgs) {
  const rawActions = Array.isArray(args.actions) ? args.actions : []
  if (rawActions.length === 0) {
    throw new Error("Provide at least one batch action.")
  }

  const maxActions = clampPositiveInt(rawActions.length, DEFAULT_BATCH_LIMIT, MAX_BATCH_LIMIT)
  const actions = rawActions.slice(0, maxActions).map(readBatchAction)
  const dryRun = args.dry_run !== false
  const destructive = actions.some((action) => action.action === "delete")

  const plan = await Promise.all(actions.map((action, index) => planBatchAction(args, action, index)))
  if (dryRun) {
    return {
      ok: true,
      dry_run: true,
      action_count: actions.length,
      requires_confirmation: actions.length > 0,
      destructive,
      plan,
    }
  }

  if (args.confirm !== true) {
    throw new Error("Set confirm=true and dry_run=false to execute a batch.")
  }

  const results = []
  for (const action of actions) {
    results.push({
      action: action.action,
      result: await executeBatchAction(args, action),
    })
  }

  return {
    ok: true,
    dry_run: false,
    action_count: actions.length,
    destructive,
    results,
  }
}

function readNotifyTarget(value: unknown) {
  const target = (getString(value) || "").toLowerCase()
  if (target === "chat" || target === "channel" || target === "email") {
    return target satisfies NotifyTarget
  }

  throw new Error("target must be one of: chat, channel, email.")
}

export async function notify(args: NotifyArgs) {
  const target = readNotifyTarget(args.target)
  const message = requireString(args.message, "message")
  const previewOnly = args.preview_only !== false
  const preview = {
    target,
    subject: getString(args.subject),
    message,
    recipients: getStringArray(args.recipients),
    chat_id: getString(args.chat_id),
    chat_name: getString(args.chat_name),
    participant_username: getString(args.participant_username),
    team_name: getString(args.team_name),
    channel_name: getString(args.channel_name),
  }

  if (previewOnly) {
    return {
      ok: true,
      preview_only: true,
      requires_confirmation: true,
      preview,
    }
  }

  if (args.confirm !== true) {
    throw new Error("Set confirm=true and preview_only=false to send a notification.")
  }

  if (target === "chat") {
    return sendChatMessage({ ...args, message })
  }

  if (target === "channel") {
    return sendChannelMessage({ ...args, message })
  }

  return sendMailMessage({
    ...args,
    message,
    to_recipients: args.to_recipients ?? args.recipients,
    preview_only: false,
    confirm: true,
  })
}
