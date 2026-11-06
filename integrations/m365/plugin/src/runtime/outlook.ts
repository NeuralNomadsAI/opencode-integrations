import { isRecord, type JsonRecord } from "./auth.ts"
import { graphResult } from "./graph.ts"
import { clampPositiveInt } from "./validators.ts"

const DEFAULT_MAIL_LIMIT = 10
const MAX_MAIL_LIMIT = 50

type AutomaticRepliesStatus = "disabled" | "alwaysEnabled" | "scheduled"
type ExternalAudience = "none" | "contactsOnly" | "all"

type MailArgs = JsonRecord & {
  query?: unknown
  limit?: unknown
  folder_id?: unknown
  force_refresh?: unknown
}

type SendMailArgs = JsonRecord & {
  recipients?: unknown
  to_recipients?: unknown
  cc_recipients?: unknown
  bcc_recipients?: unknown
  subject?: unknown
  message?: unknown
  body?: unknown
  content_type?: unknown
  save_to_sent_items?: unknown
  preview_only?: unknown
  confirm?: unknown
  force_refresh?: unknown
}

type AutomaticRepliesArgs = JsonRecord & {
  status?: unknown
  enabled?: unknown
  internal_message?: unknown
  external_message?: unknown
  external_audience?: unknown
  start_datetime?: unknown
  end_datetime?: unknown
  time_zone?: unknown
  preview_only?: unknown
  confirm?: unknown
  force_refresh?: unknown
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

function getBoolean(value: unknown) {
  return typeof value === "boolean" ? value : null
}

function getStringArray(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => getString(item)).filter((item): item is string => Boolean(item))
  }

  const single = getString(value)
  return single ? [single] : []
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

function encodePathSegment(value: string) {
  return encodeURIComponent(value)
}

function escapeMailSearchQuery(value: string) {
  return value.replace(/"/g, '\\"')
}

function summarizeEmailAddress(value: unknown) {
  const record = isRecord(value) ? value : {}
  const emailAddress = isRecord(record.emailAddress) ? record.emailAddress : record

  return {
    name: getString(emailAddress.name),
    address: getString(emailAddress.address),
  }
}

function summarizeMailMessage(value: unknown) {
  const record = isRecord(value) ? value : {}

  return {
    id: getString(record.id),
    subject: getString(record.subject),
    from: summarizeEmailAddress(record.from),
    toRecipients: getRecordArray(record.toRecipients).map(summarizeEmailAddress),
    ccRecipients: getRecordArray(record.ccRecipients).map(summarizeEmailAddress),
    receivedDateTime: getString(record.receivedDateTime),
    sentDateTime: getString(record.sentDateTime),
    importance: getString(record.importance),
    isRead: getBoolean(record.isRead),
    hasAttachments: getBoolean(record.hasAttachments),
    webLink: getString(record.webLink),
    bodyPreview: getString(record.bodyPreview),
  }
}

function buildRecipientList(value: unknown, label: string, required = false) {
  const recipients = getStringArray(value)
  if (required && recipients.length === 0) {
    throw new Error(`Provide at least one ${label}.`)
  }

  return recipients.map((address) => ({
    emailAddress: {
      address,
    },
  }))
}

function normalizeContentType(value: unknown) {
  return getString(value)?.toLowerCase() === "html" ? "HTML" : "Text"
}

function readAutomaticRepliesStatus(args: AutomaticRepliesArgs): AutomaticRepliesStatus {
  const explicit = getString(args.status)?.toLowerCase()
  if (
    explicit === "disabled" ||
    explicit === "alwaysenabled" ||
    explicit === "always_enabled" ||
    explicit === "alwaysEnabled"
  ) {
    return explicit === "disabled" ? "disabled" : "alwaysEnabled"
  }

  if (explicit === "scheduled") {
    return "scheduled"
  }

  if (args.enabled === false) {
    return "disabled"
  }

  if (getString(args.start_datetime) || getString(args.end_datetime)) {
    return "scheduled"
  }

  return "alwaysEnabled"
}

function readExternalAudience(value: unknown): ExternalAudience {
  const text = getString(value)?.toLowerCase()
  if (text === "contactsonly" || text === "contacts_only" || text === "contacts") {
    return "contactsOnly"
  }
  if (text === "all") {
    return "all"
  }
  return "none"
}

function buildGraphDateTime(dateTime: string, timeZone: string) {
  return {
    dateTime,
    timeZone,
  }
}

function buildAutomaticRepliesSetting(args: AutomaticRepliesArgs) {
  const status = readAutomaticRepliesStatus(args)
  const timeZone = getString(args.time_zone) || "UTC"
  const internalMessage = getString(args.internal_message)
  const externalMessage = getString(args.external_message)

  if (status !== "disabled" && !internalMessage && !externalMessage) {
    throw new Error("Provide internal_message or external_message when enabling automatic replies.")
  }

  const setting: JsonRecord = {
    status,
    externalAudience: readExternalAudience(args.external_audience),
  }

  if (internalMessage) {
    setting.internalReplyMessage = internalMessage
  }

  if (externalMessage) {
    setting.externalReplyMessage = externalMessage
  }

  if (status === "scheduled") {
    const start = requireString(args.start_datetime, "start_datetime")
    const end = requireString(args.end_datetime, "end_datetime")
    setting.scheduledStartDateTime = buildGraphDateTime(start, timeZone)
    setting.scheduledEndDateTime = buildGraphDateTime(end, timeZone)
  }

  return setting
}

export async function searchMailMessages(args: MailArgs) {
  const query = getString(args.query)
  const limit = clampPositiveInt(args.limit, DEFAULT_MAIL_LIMIT, MAX_MAIL_LIMIT)
  const folderId = getString(args.folder_id)
  const path = folderId ? `/me/mailFolders/${encodePathSegment(folderId)}/messages` : "/me/messages"

  const result = await graphResult({
    path,
    method: "GET",
    query: {
      $top: limit,
      $select:
        "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,hasAttachments,importance,isRead,webLink,bodyPreview",
      ...(query ? { $search: `"${escapeMailSearchQuery(query)}"` } : { $orderby: "receivedDateTime desc" }),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const messages = getCollectionItems(result).map(summarizeMailMessage)
  return {
    query,
    count: messages.length,
    messages,
  }
}

export async function sendMailMessage(args: SendMailArgs) {
  const subject = requireString(args.subject, "subject")
  const content = requireString(args.message ?? args.body, "message")
  const toRecipients = buildRecipientList(args.to_recipients ?? args.recipients, "recipient", true)
  const ccRecipients = buildRecipientList(args.cc_recipients, "cc_recipient")
  const bccRecipients = buildRecipientList(args.bcc_recipients, "bcc_recipient")
  const preview = {
    subject,
    toRecipients,
    ccRecipients,
    bccRecipients,
    body: {
      contentType: normalizeContentType(args.content_type),
      content,
    },
    saveToSentItems: args.save_to_sent_items !== false,
  }

  if (args.preview_only !== false) {
    return {
      ok: true,
      preview_only: true,
      requires_confirmation: true,
      preview,
    }
  }

  if (args.confirm !== true) {
    throw new Error("Set confirm=true and preview_only=false to send email.")
  }

  await graphResult({
    path: "/me/sendMail",
    method: "POST",
    body: {
      message: {
        subject,
        body: preview.body,
        toRecipients,
        ...(ccRecipients.length > 0 ? { ccRecipients } : {}),
        ...(bccRecipients.length > 0 ? { bccRecipients } : {}),
      },
      saveToSentItems: preview.saveToSentItems,
    },
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ok: true,
    preview_only: false,
    sent: true,
    subject,
    recipients: toRecipients.map((recipient) => recipient.emailAddress.address),
  }
}

export async function getMailboxSettings(args: { force_refresh?: boolean } = {}) {
  const result = await graphResult({
    path: "/me/mailboxSettings",
    method: "GET",
    force_refresh: Boolean(args.force_refresh),
  })

  const record = isRecord(result) ? result : {}
  return {
    timeZone: getString(record.timeZone),
    language: record.language ?? null,
    workingHours: record.workingHours ?? null,
    automaticRepliesSetting: record.automaticRepliesSetting ?? null,
    dateFormat: getString(record.dateFormat),
    timeFormat: getString(record.timeFormat),
  }
}

export async function setAutomaticReplies(args: AutomaticRepliesArgs) {
  const automaticRepliesSetting = buildAutomaticRepliesSetting(args)
  const preview = {
    automaticRepliesSetting,
  }

  if (args.preview_only !== false) {
    return {
      ok: true,
      preview_only: true,
      requires_confirmation: true,
      preview,
    }
  }

  if (args.confirm !== true) {
    throw new Error("Set confirm=true and preview_only=false to update automatic replies.")
  }

  await graphResult({
    path: "/me/mailboxSettings",
    method: "PATCH",
    body: preview,
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ok: true,
    preview_only: false,
    updated: true,
    mailboxSettings: await getMailboxSettings({ force_refresh: args.force_refresh === true }),
  }
}
