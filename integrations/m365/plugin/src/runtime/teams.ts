import { isRecord } from "./auth.ts"
import { GRAPH_BASE_URL, graphResult } from "./graph.ts"
import {
  chooseSingleMatch,
  clampPositiveInt,
  filterAndSortMatches,
  normalizeMatchText,
  scoreBestValue,
  uniqueNonEmpty,
} from "./validators.ts"

const DEFAULT_CHAT_LIST_LIMIT = 20
const DEFAULT_TEAM_LIST_LIMIT = 50
const DEFAULT_MESSAGE_LIMIT = 20
const DEFAULT_RESOLUTION_LIMIT = 50

type CurrentUserSummary = {
  id: string | null
  displayName: string | null
  userPrincipalName: string | null
  mail: string | null
}

type IdentitySummary = {
  type: string
  id: string | null
  displayName: string | null
  userPrincipalName?: string | null
  userIdentityType?: string | null
  raw?: unknown
}

type MessageSummary = {
  id: string | null
  replyToId: string | null
  messageType: string | null
  createdDateTime: string | null
  lastModifiedDateTime: string | null
  deletedDateTime: string | null
  subject: string | null
  importance: string | null
  from: IdentitySummary | null
  contentType: string | null
  content: string
  rawContent: string | null
}

type ChatMemberSummary = {
  id: string | null
  userId: string | null
  displayName: string | null
  email: string | null
  roles: string[]
}

type ChatSummary = {
  id: string | null
  label: string
  chatType: string | null
  topic: string | null
  webUrl: string | null
  createdDateTime: string | null
  lastUpdatedDateTime: string | null
  preview: string
  members: ChatMemberSummary[]
  memberLabels: string[]
}

type TeamSummary = {
  id: string | null
  displayName: string | null
  description: string | null
  webUrl: string | null
}

type ChannelSummary = {
  id: string | null
  displayName: string | null
  description: string | null
  membershipType: string | null
  webUrl: string | null
  email: string | null
  teamId: string | null
  teamName: string | null
}

function getString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null
}

function getRecord(value: unknown) {
  return isRecord(value) ? value : null
}

function getRecordArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as Record<string, unknown>[]
  }

  return value.filter((item): item is Record<string, unknown> => isRecord(item))
}

function getCollectionItems(value: unknown) {
  const record = getRecord(value)
  return getRecordArray(record?.value)
}

function getStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [] as string[]
  }

  return value.map((item) => getString(item)).filter((item): item is string => Boolean(item))
}

function encodePathSegment(value: string) {
  return encodeURIComponent(value)
}

function decodeBasicHtmlEntities(text: string) {
  return text
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCharCode(parseInt(code, 16)))
}

function htmlToText(html: string) {
  if (!html) {
    return ""
  }

  return decodeBasicHtmlEntities(
    html
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>/gi, "\n\n")
      .replace(/<\/div>/gi, "\n")
      .replace(/<li>/gi, "- ")
      .replace(/<[^>]+>/g, ""),
  )
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function bodyToReadableText(body: unknown) {
  const record = getRecord(body)
  const content = getString(record?.content) || ""
  if (!content) {
    return ""
  }

  return getString(record?.contentType) === "html" ? htmlToText(content) : content
}

function buildMessageBody(content: string, contentType?: string) {
  const normalizedType = String(contentType || "text").toLowerCase() === "html" ? "html" : "text"
  return {
    body: {
      contentType: normalizedType,
      content,
    },
  }
}

function summarizeIdentity(identity: unknown) {
  const record = getRecord(identity)
  if (!record) {
    return null
  }

  const user = getRecord(record.user)
  if (user) {
    return {
      type: "user",
      id: getString(user.id),
      displayName: getString(user.displayName),
      userPrincipalName: getString(user.userPrincipalName),
      userIdentityType: getString(user.userIdentityType),
    } satisfies IdentitySummary
  }

  const application = getRecord(record.application)
  if (application) {
    return {
      type: "application",
      id: getString(application.id),
      displayName: getString(application.displayName),
    } satisfies IdentitySummary
  }

  return {
    type: "unknown",
    id: null,
    displayName: null,
    raw: identity,
  } satisfies IdentitySummary
}

function summarizeMessage(message: unknown) {
  const record = getRecord(message)
  const body = getRecord(record?.body)

  return {
    id: getString(record?.id),
    replyToId: getString(record?.replyToId),
    messageType: getString(record?.messageType),
    createdDateTime: getString(record?.createdDateTime),
    lastModifiedDateTime: getString(record?.lastModifiedDateTime),
    deletedDateTime: getString(record?.deletedDateTime),
    subject: getString(record?.subject),
    importance: getString(record?.importance),
    from: summarizeIdentity(record?.from),
    contentType: getString(body?.contentType),
    content: bodyToReadableText(body),
    rawContent: getString(body?.content),
  } satisfies MessageSummary
}

async function getCurrentUser(forceRefresh = false) {
  const result = await graphResult({
    path: "/me",
    method: "GET",
    query: {
      $select: "id,displayName,userPrincipalName,mail",
    },
    force_refresh: forceRefresh,
  })

  const record = getRecord(result)
  return {
    id: getString(record?.id),
    displayName: getString(record?.displayName),
    userPrincipalName: getString(record?.userPrincipalName),
    mail: getString(record?.mail),
  } satisfies CurrentUserSummary
}

function summarizeChatMember(member: unknown) {
  const record = getRecord(member)
  const user = getRecord(record?.user)

  return {
    id: getString(record?.id) || getString(record?.userId),
    userId: getString(record?.userId) || getString(user?.id),
    displayName: getString(record?.displayName) || getString(user?.displayName),
    email: getString(record?.email) || getString(user?.userPrincipalName),
    roles: getStringArray(record?.roles),
  } satisfies ChatMemberSummary
}

function buildChatLabel(chat: Record<string, unknown> | null, members: ChatMemberSummary[], me: CurrentUserSummary) {
  const topic = getString(chat?.topic)
  if (topic) {
    return topic
  }

  const labels = members
    .filter((member) => member.displayName || member.email)
    .filter((member) => member.userId !== me.id)
    .map((member) => member.displayName || member.email || "")

  if (labels.length > 0) {
    return labels.slice(0, 3).join(", ") + (labels.length > 3 ? ` +${labels.length - 3}` : "")
  }

  return getString(chat?.id) || "Unnamed chat"
}

async function listChatMembers(chatId: string, forceRefresh = false) {
  const result = await graphResult({
    path: `/chats/${encodePathSegment(chatId)}/members`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return getCollectionItems(result)
}

async function getChatSummaryFromRaw(chat: Record<string, unknown>, me: CurrentUserSummary, forceRefresh = false) {
  const chatId = getString(chat.id)
  const members = chatId ? (await listChatMembers(chatId, forceRefresh)).map(summarizeChatMember) : []
  const memberLabels = uniqueNonEmpty(members.flatMap((member) => [member.displayName, member.email]))

  return {
    id: getString(chat.id),
    label: buildChatLabel(chat, members, me),
    chatType: getString(chat.chatType),
    topic: getString(chat.topic),
    webUrl: getString(chat.webUrl),
    createdDateTime: getString(chat.createdDateTime),
    lastUpdatedDateTime: getString(chat.lastUpdatedDateTime),
    preview: bodyToReadableText(getRecord(getRecord(chat.lastMessagePreview)?.body)),
    members,
    memberLabels,
  } satisfies ChatSummary
}

async function getChatSummaryById(chatId: string, forceRefresh = false) {
  const me = await getCurrentUser(forceRefresh)
  const chat = getRecord(
    await graphResult({
      path: `/chats/${encodePathSegment(chatId)}`,
      method: "GET",
      force_refresh: forceRefresh,
    }),
  )

  if (!chat) {
    throw new Error(`Could not read Teams chat ${chatId}.`)
  }

  return getChatSummaryFromRaw(chat, me, forceRefresh)
}

async function listChatSummaries(limit: number, forceRefresh = false) {
  const me = await getCurrentUser(forceRefresh)
  const result = await graphResult({
    path: "/me/chats",
    method: "GET",
    query: {
      $top: clampPositiveInt(limit, DEFAULT_CHAT_LIST_LIMIT),
    },
    force_refresh: forceRefresh,
  })

  const chats = await Promise.all(
    getCollectionItems(result).map((chat) => getChatSummaryFromRaw(chat, me, forceRefresh)),
  )
  return chats.sort((left, right) =>
    String(right.lastUpdatedDateTime || "").localeCompare(String(left.lastUpdatedDateTime || "")),
  )
}

function scoreChatReference(chat: ChatSummary, chatName: string | null, participantName: string | null) {
  const chatScore = scoreBestValue([chat.label, chat.topic], chatName || undefined)
  const participantScore = scoreBestValue(chat.memberLabels, participantName || undefined)
  const oneOnOneBonus = participantName && participantScore >= 1000 && chat.chatType === "oneOnOne" ? 100 : 0
  return chatScore + participantScore + oneOnOneBonus
}

async function resolveChatReference(args: Record<string, unknown>) {
  const chatId = getString(args.chat_id)
  if (chatId) {
    return getChatSummaryById(chatId, Boolean(args.force_refresh))
  }

  const chatName = getString(args.chat_name)
  const participantName = getString(args.participant_name)
  if (!chatName && !participantName) {
    throw new Error(
      "Provide chat_id, chat_name or participant_name. Use m365_teams_list_chats to discover available chats.",
    )
  }

  const chats = await listChatSummaries(
    clampPositiveInt(args.search_limit, DEFAULT_RESOLUTION_LIMIT),
    Boolean(args.force_refresh),
  )
  const match = chooseSingleMatch(
    chats,
    (chat) => scoreChatReference(chat, chatName, participantName),
    (chat) => `${chat.label} [${chat.id}]`,
    "chat",
  )

  if (!match) {
    throw new Error("No matching chat was found. Use m365_teams_list_chats to inspect available chats.")
  }

  return match
}

function getRequestedParticipantUsernames(args: Record<string, unknown>) {
  return uniqueNonEmpty([args.participant_username, ...getStringArray(args.participant_usernames)])
}

function buildUserBindUrl(userReference: string) {
  return `${GRAPH_BASE_URL}/users/${encodePathSegment(userReference)}`
}

function buildChatMemberBinding(userReference: string) {
  return {
    "@odata.type": "#microsoft.graph.aadUserConversationMember",
    roles: ["owner"],
    "user@odata.bind": buildUserBindUrl(userReference),
  }
}

async function createChatFromParticipants(args: Record<string, unknown>) {
  const forceRefresh = Boolean(args.force_refresh)
  const me = await getCurrentUser(forceRefresh)
  const requestedParticipants = getRequestedParticipantUsernames(args)

  if (requestedParticipants.length === 0) {
    throw new Error("Provide participant_username or participant_usernames to create a new Teams chat.")
  }

  const selfReferences = new Set(
    uniqueNonEmpty([me.id, me.userPrincipalName, me.mail]).map((value) => normalizeMatchText(value)),
  )
  const participants = requestedParticipants.filter((value) => !selfReferences.has(normalizeMatchText(value)))

  if (!me.id) {
    throw new Error("Could not determine the authenticated user id for Teams chat creation.")
  }

  if (participants.length === 0) {
    throw new Error("Provide at least one other participant username (UPN) or user id to create a chat.")
  }

  const chatTopic = getString(args.chat_topic)
  if (participants.length === 1 && chatTopic) {
    throw new Error("chat_topic is only supported when creating a group chat with multiple participants.")
  }

  const createdChat = getRecord(
    await graphResult({
      path: "/chats",
      method: "POST",
      body: {
        chatType: participants.length === 1 ? "oneOnOne" : "group",
        ...(participants.length > 1 && chatTopic ? { topic: chatTopic } : {}),
        members: [buildChatMemberBinding(me.id), ...participants.map((value) => buildChatMemberBinding(value))],
      },
      force_refresh: forceRefresh,
    }),
  )

  const createdChatId = getString(createdChat?.id)
  if (!createdChatId) {
    throw new Error("Microsoft Graph did not return a chat id after chat creation.")
  }

  return {
    chat: await getChatSummaryById(createdChatId, forceRefresh),
    requestedParticipants: participants,
  }
}

async function resolveChatForSending(args: Record<string, unknown>) {
  const requestedParticipants = getRequestedParticipantUsernames(args)
  const hasExistingChatReference = Boolean(
    getString(args.chat_id) || getString(args.chat_name) || getString(args.participant_name),
  )

  if (hasExistingChatReference && requestedParticipants.length > 0) {
    throw new Error(
      "Use either chat_id/chat_name/participant_name for an existing chat, or participant_username/participant_usernames to create or reuse a chat by account.",
    )
  }

  if (hasExistingChatReference) {
    return resolveChatReference(args)
  }

  if (requestedParticipants.length > 0) {
    return (await createChatFromParticipants(args)).chat
  }

  throw new Error("Provide chat_id, chat_name, participant_name, participant_username or participant_usernames.")
}

function summarizeTeam(team: unknown) {
  const record = getRecord(team)

  return {
    id: getString(record?.id),
    displayName: getString(record?.displayName),
    description: getString(record?.description),
    webUrl: getString(record?.webUrl),
  } satisfies TeamSummary
}

async function listTeamsInternal(forceRefresh = false) {
  const result = await graphResult({
    path: "/me/joinedTeams",
    method: "GET",
    force_refresh: forceRefresh,
  })

  return getCollectionItems(result).map(summarizeTeam)
}

async function resolveTeamReference(args: Record<string, unknown>) {
  const teams = await listTeamsInternal(Boolean(args.force_refresh))
  const teamId = getString(args.team_id)

  if (teamId) {
    const exact = teams.find((team) => team.id === teamId)
    if (exact) {
      return exact
    }

    throw new Error(`Unknown team_id: ${teamId}`)
  }

  const teamName = getString(args.team_name)
  if (!teamName) {
    throw new Error("Provide team_id or team_name. Use m365_teams_list_teams to inspect available teams.")
  }

  const match = chooseSingleMatch(
    teams,
    (team) => scoreBestValue([team.displayName, team.description], teamName),
    (team) => `${team.displayName} [${team.id}]`,
    "team",
  )

  if (!match) {
    throw new Error("No matching team was found. Use m365_teams_list_teams to inspect available teams.")
  }

  return match
}

function summarizeChannel(team: TeamSummary, channel: unknown) {
  const record = getRecord(channel)

  return {
    id: getString(record?.id),
    displayName: getString(record?.displayName),
    description: getString(record?.description),
    membershipType: getString(record?.membershipType),
    webUrl: getString(record?.webUrl),
    email: getString(record?.email),
    teamId: team.id,
    teamName: team.displayName,
  } satisfies ChannelSummary
}

async function listChannelsForTeam(team: TeamSummary, forceRefresh = false) {
  if (!team.id) {
    throw new Error("The resolved team does not expose a valid id.")
  }

  const result = await graphResult({
    path: `/teams/${encodePathSegment(team.id)}/channels`,
    method: "GET",
    force_refresh: forceRefresh,
  })

  return getCollectionItems(result).map((channel) => summarizeChannel(team, channel))
}

async function listChannelsAcrossTeams(forceRefresh = false) {
  const teams = await listTeamsInternal(forceRefresh)
  const channelGroups = await Promise.all(teams.map((team) => listChannelsForTeam(team, forceRefresh)))
  return channelGroups.flat()
}

async function resolveChannelReference(args: Record<string, unknown>) {
  const forceRefresh = Boolean(args.force_refresh)
  const channels =
    getString(args.team_id) || getString(args.team_name)
      ? await listChannelsForTeam(await resolveTeamReference(args), forceRefresh)
      : await listChannelsAcrossTeams(forceRefresh)

  const channelId = getString(args.channel_id)
  if (channelId) {
    const exact = channels.find((channel) => channel.id === channelId)
    if (exact) {
      return exact
    }

    throw new Error(`Unknown channel_id: ${channelId}`)
  }

  const channelName = getString(args.channel_name)
  if (!channelName) {
    throw new Error("Provide channel_id or channel_name. Use m365_teams_list_channels to inspect available channels.")
  }

  const teamName = getString(args.team_name)
  const match = chooseSingleMatch(
    channels,
    (channel) =>
      scoreBestValue([channel.displayName, channel.description], channelName || undefined) +
      scoreBestValue([channel.teamName], teamName || undefined),
    (channel) => `${channel.teamName} / ${channel.displayName} [${channel.id}]`,
    "channel",
  )

  if (!match) {
    throw new Error("No matching channel was found. Use m365_teams_list_channels to inspect available channels.")
  }

  return match
}

export async function listTeams(args: { query?: string; force_refresh?: boolean }) {
  const teams = await listTeamsInternal(Boolean(args.force_refresh))
  const filtered = filterAndSortMatches(teams, getString(args.query) || undefined, (team) => [
    team.displayName,
    team.description,
  ]).slice(0, DEFAULT_TEAM_LIST_LIMIT)

  return {
    count: filtered.length,
    teams: filtered,
  }
}

export async function listChats(args: { limit?: number; query?: string; force_refresh?: boolean }) {
  const chats = await listChatSummaries(
    clampPositiveInt(args.limit, DEFAULT_CHAT_LIST_LIMIT),
    Boolean(args.force_refresh),
  )
  const query = getString(args.query)
  const filtered = query
    ? chats
        .map((chat) => ({ chat, score: scoreChatReference(chat, query, query) }))
        .filter((entry) => entry.score > 0)
        .sort((left, right) => right.score - left.score || left.chat.label.localeCompare(right.chat.label))
        .map((entry) => entry.chat)
    : chats

  return {
    count: filtered.length,
    chats: filtered,
  }
}

export async function listChatMessages(args: Record<string, unknown>) {
  const chat = await resolveChatReference(args)
  if (!chat.id) {
    throw new Error("The resolved chat does not expose a valid id.")
  }

  const result = await graphResult({
    path: `/chats/${encodePathSegment(chat.id)}/messages`,
    method: "GET",
    query: {
      $top: clampPositiveInt(args.limit, DEFAULT_MESSAGE_LIMIT),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const messages = getCollectionItems(result).map(summarizeMessage)
  return {
    chat,
    count: messages.length,
    messages,
  }
}

export async function sendChatMessage(args: Record<string, unknown>) {
  const chat = await resolveChatForSending(args)
  if (!chat.id) {
    throw new Error("The resolved chat does not expose a valid id.")
  }

  const message = getString(args.message)
  if (!message) {
    throw new Error("message is required to send a Teams chat message.")
  }

  const result = await graphResult({
    path: `/chats/${encodePathSegment(chat.id)}/messages`,
    method: "POST",
    body: buildMessageBody(message, getString(args.content_type) || undefined),
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ok: true,
    chat,
    message: summarizeMessage(result),
  }
}

export async function createChat(args: Record<string, unknown>) {
  const result = await createChatFromParticipants(args)
  return {
    ok: true,
    chat: result.chat,
    requestedParticipants: result.requestedParticipants,
  }
}

export async function listChannels(args: {
  team_id?: string
  team_name?: string
  query?: string
  force_refresh?: boolean
}) {
  const forceRefresh = Boolean(args.force_refresh)
  const channels =
    getString(args.team_id) || getString(args.team_name)
      ? await listChannelsForTeam(await resolveTeamReference(args), forceRefresh)
      : await listChannelsAcrossTeams(forceRefresh)

  const filtered = filterAndSortMatches(channels, getString(args.query) || undefined, (channel) => [
    channel.displayName,
    channel.description,
    channel.teamName,
  ])

  return {
    count: filtered.length,
    channels: filtered,
  }
}

export async function readChannelMessages(args: Record<string, unknown>) {
  const channel = await resolveChannelReference(args)
  if (!channel.teamId || !channel.id) {
    throw new Error("The resolved channel does not expose the team/channel ids needed to read messages.")
  }

  const result = await graphResult({
    path: `/teams/${encodePathSegment(channel.teamId)}/channels/${encodePathSegment(channel.id)}/messages`,
    method: "GET",
    query: {
      $top: clampPositiveInt(args.limit, DEFAULT_MESSAGE_LIMIT),
    },
    force_refresh: Boolean(args.force_refresh),
  })

  const messages = getCollectionItems(result).map(summarizeMessage)
  return {
    channel,
    count: messages.length,
    messages,
  }
}

export async function sendChannelMessage(args: Record<string, unknown>) {
  const channel = await resolveChannelReference(args)
  if (!channel.teamId || !channel.id) {
    throw new Error("The resolved channel does not expose the team/channel ids needed to send messages.")
  }

  const message = getString(args.message)
  if (!message) {
    throw new Error("message is required to send a Teams channel message.")
  }

  const result = await graphResult({
    path: `/teams/${encodePathSegment(channel.teamId)}/channels/${encodePathSegment(channel.id)}/messages`,
    method: "POST",
    body: buildMessageBody(message, getString(args.content_type) || undefined),
    force_refresh: Boolean(args.force_refresh),
  })

  return {
    ok: true,
    channel,
    message: summarizeMessage(result),
  }
}
