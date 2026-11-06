---

name: m365-teams-chat
description: |
Read and send Microsoft Teams chat messages through the Microsoft 365 bundle.

Triggers when user mentions:
- "Teams chat"
- "send a Teams message"
- "read Teams messages"
- "direct message on Teams"

---

## Preferred tools

Use the comfortable tools first:

- `m365_teams_list_chats`
- `m365_teams_read_chat_messages`
- `m365_teams_create_chat`
- `m365_teams_send_chat_message`

Only fall back to `m365_graph_request` for edge cases not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Scope note

The default Microsoft 365 plugin scopes permit only a profile read and refresh.
For Teams chat workflows, re-authenticate with scopes including:

- `Chat.Read`
- `Chat.ReadWrite`

## Common flows

### List chats

Call `m365_teams_list_chats`.

- optional `query` to filter by chat label or participant name
- optional `limit` to inspect more recent chats

### Read chat messages

Call `m365_teams_read_chat_messages` with one of:

- `chat_id`
- `chat_name`
- `participant_name`

Optional:

- `limit`

### Send a chat message

Call `m365_teams_send_chat_message` with one of:

- `chat_id`
- `chat_name`
- `participant_name`

Or create or reuse a chat directly with:

- `participant_username`
- `participant_usernames`

Required:

- `message`

Optional:

- `content_type` (`text` or `html`)
- `chat_topic` for group chats created from `participant_usernames`

### Create a chat

Call `m365_teams_create_chat` with:

- `participant_username` for a direct chat
- `participant_usernames` for a group chat

Optional:

- `chat_topic` for group chats

## Advanced fallback

If you need a Graph path not covered above, use:

```text
m365_graph_request(method: "GET|POST|PATCH|DELETE", path: "/...", query: { ... }, body: { ... })
```

## Guardrails

- Before sending a message, confirm the exact destination chat and the final message text.
- Prefer `content_type: "html"` only when formatting matters; otherwise use `text`.
- Prefer `participant_name` for existing chats discovered from Teams.
- Use `participant_username` or `participant_usernames` only with real account identifiers such as UPN, sign-in name or user id.
