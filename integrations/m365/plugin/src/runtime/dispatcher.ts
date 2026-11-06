import { validateAndNormalizeArgs } from "../kit.ts"
import * as auth from "./auth.ts"
import { throwIfCancelled, withExecutionSignal } from "./cancellation.ts"
import * as excel from "./excel.ts"
import * as graph from "./graph.ts"
import * as files from "./m365.ts"
import { getMethodManifest, listMethodManifests } from "./method_registry.ts"
import * as openwork from "./openwork.ts"
import * as outlook from "./outlook.ts"
import * as powerpoint from "./powerpoint.ts"
import * as teams from "./teams.ts"

// The manifests validate each handler's typed arguments before dispatch.
const handlers: Record<string, (args: never) => unknown> = {
  m365_auth_status: auth.getAuthStatus,
  m365_auth_device_start: async () => {
    const { pending, autoStarted } = await auth.ensureDeviceCodeBootstrap()
    return {
      auto_started: autoStarted,
      user_code: pending.userCode,
      verification_uri: pending.verificationUri,
      expires_at: pending.expiresAt,
      interval_seconds: pending.intervalSeconds,
    }
  },
  m365_auth_device_poll: (args: { timeout_seconds?: number }) =>
    auth.pollForDeviceToken({ timeoutSeconds: Math.min(args.timeout_seconds || 60, 120) }),
  m365_auth_logout: () => {
    auth.clearAuth()
    auth.clearPendingAuth()
    return auth.getAuthStatus()
  },
  m365_graph_request: graph.executeGraphRequest,
  m365_ping: (args: { force_refresh?: boolean }) => graph.pingGraph(args.force_refresh),
  m365_list_sites: files.listSites,
  m365_get_site: files.resolveSiteReference,
  m365_list_document_libraries: files.listDocumentLibraries,
  m365_list_drive_items: files.listDriveItems,
  m365_get_drive_item: files.getDriveItem,
  m365_list_file_versions: files.listFileVersions,
  m365_create_share_link: files.createShareLink,
  m365_search_drive_items: files.searchDriveItems,
  m365_create_drive_folder: files.createDriveFolder,
  m365_download_drive_item: files.downloadDriveItem,
  m365_upload_small_drive_item: files.uploadSmallDriveItem,
  m365_update_drive_item: files.updateDriveItem,
  m365_delete_drive_item: files.deleteDriveItem,
  m365_search_workspace: openwork.searchWorkspace,
  m365_batch_drive_items: openwork.batchDriveItems,
  m365_notify: openwork.notify,
  m365_mail_search_messages: outlook.searchMailMessages,
  m365_mail_send_message: outlook.sendMailMessage,
  m365_mail_get_mailbox_settings: outlook.getMailboxSettings,
  m365_mail_set_automatic_replies: outlook.setAutomaticReplies,
  m365_excel_list_worksheets: excel.listWorkbookWorksheets,
  m365_excel_read_range: excel.readWorkbookRange,
  m365_excel_write_range: excel.writeWorkbookRange,
  m365_powerpoint_inspect_text: powerpoint.inspectPowerPointText,
  m365_powerpoint_inspect_media: powerpoint.inspectPowerPointMedia,
  m365_powerpoint_inspect_media_batch: powerpoint.inspectMultiplePowerPointMedia,
  m365_powerpoint_inspect_structure: powerpoint.inspectPowerPointStructure,
  m365_powerpoint_replace_text: powerpoint.replacePowerPointText,
  m365_teams_list_chats: teams.listChats,
  m365_teams_read_chat_messages: teams.listChatMessages,
  m365_teams_send_chat_message: teams.sendChatMessage,
  m365_teams_create_chat: teams.createChat,
  m365_teams_list_teams: teams.listTeams,
  m365_teams_list_channels: teams.listChannels,
  m365_teams_read_channel_messages: teams.readChannelMessages,
  m365_teams_send_channel_message: teams.sendChannelMessage,
}

export const listPublicMethods = () => listMethodManifests().filter((m) => m.execution.tool === m.name)
export const listHandledMethodNames = () => Object.keys(handlers).sort()

export async function dispatchDeclaredMethod(name: string, args: Record<string, unknown> = {}, signal?: AbortSignal) {
  return withExecutionSignal(signal, async () => {
    const manifest = getMethodManifest(name)
    if (!manifest || !handlers[name]) throw new Error("Unknown Microsoft 365 method.")
    const normalized = validateAndNormalizeArgs(manifest, args)
    if (manifest.requires?.auth) {
      const token = await auth.getValidAuth()
      const available = auth.getAvailableScopeList(token).map((s) => s.toLowerCase())
      const missing = (manifest.requires.scopes || []).filter(
        (s) =>
          !/^depends on /i.test(s) &&
          !s.split(/\s+or\s+/i).some((choice) => {
            const scope = choice.toLowerCase()
            return available.includes(scope) || available.includes(scope.replace(".read", ".readwrite"))
          }),
      )
      if (missing.length)
        throw new Error(
          `Missing Microsoft Graph scopes: ${missing.join(", ")}. Configure plugin scopes and sign in again.`,
        )
    }
    throwIfCancelled()
    return handlers[name]!(normalized as never)
  })
}
