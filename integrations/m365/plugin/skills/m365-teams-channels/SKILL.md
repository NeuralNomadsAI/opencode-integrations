---

name: m365-teams-channels
description: |
Work with Microsoft Teams teams, channels and channel messages through the Microsoft 365 bundle.

Triggers when user mentions:
- "Teams channel"
- "post to a Teams channel"
- "list Teams channels"
- "read channel messages"

---

## Preferred tools

Use the comfortable tools first:

- `m365_teams_list_teams`
- `m365_teams_list_channels`
- `m365_teams_read_channel_messages`
- `m365_teams_send_channel_message`

Only fall back to `m365_graph_request` when a workflow is not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Scope note

The default scopes permit only a profile read.
For Teams channel workflows, re-authenticate with scopes including:

- `Team.ReadBasic.All`
- `Channel.ReadBasic.All`
- `ChannelMessage.Read.All`
- `ChannelMessage.Send`

## Common flows

### List joined teams

Call `m365_teams_list_teams`.

### List channels for a team

Call `m365_teams_list_channels`.

- specify `team_id` or `team_name` to scope the search
- omit both to search channels across all joined teams

### Read channel messages

Call `m365_teams_read_channel_messages` with:

- `team_id` or `team_name` when known
- `channel_id` or `channel_name`
- optional `limit`

### Send a channel message

Call `m365_teams_send_channel_message` with:

- `team_id` or `team_name`
- `channel_id` or `channel_name`
- `message`
- optional `content_type` (`text` or `html`)

## Advanced fallback

If you need a Graph path not covered above, use:

```text
m365_graph_request(method: "GET|POST", path: "/...", query: { ... }, body: { ... })
```

## Guardrails

- Confirm the target team and channel before posting.
- Read recent channel messages first when context matters.
- Surface missing Teams scopes explicitly when the tenant token is file-only.
