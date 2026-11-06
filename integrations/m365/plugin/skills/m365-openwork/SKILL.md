---

name: m365-openwork
description: |
Prompt-first Microsoft 365 workflows for OpenWork: workspace search, batch file operations, and notifications.

Triggers when user mentions:
- "search across Microsoft 365"
- "batch rename"
- "notify the team"
- "send a follow-up"
- "prepare a bulk action"

---

## Preferred tools

Use these high-level OpenWork tools first:

- `m365_search_workspace`
- `m365_batch_drive_items`
- `m365_notify`

Use lower-level domain tools only when the user asks for a precise operation or when a high-level tool reports that the source/target needs clarification.

## Search workspace

Call `m365_search_workspace` when the user asks to find information across Microsoft 365.

Inputs:

- `query` is required
- `sources` can include `sites`, `files`, `mail`, `teams_chats`, `teams_channels`
- `limit` is per source
- optionally pass `site_name`, `library_name`, or `drive_id` to constrain file search

If one source fails because a scope is missing, the tool returns a source-level error and still returns the other sources.

## Batch file operations

Call `m365_batch_drive_items` when the user wants bulk file/folder operations.

Supported action types:

- `create_folder`
- `rename`
- `move`
- `delete`
- `share_link`

Always start with the default `dry_run: true`. Show the plan to the user before executing.

Only execute with:

- `dry_run: false`
- `confirm: true`

Example dry-run actions:

```json
[
  {
    "action": "rename",
    "item_path": "Campaigns/2026/brief-draft.docx",
    "new_name": "brief-final.docx"
  },
  {
    "action": "move",
    "item_path": "Campaigns/2026/brief-final.docx",
    "target_parent_path": "Campaigns/2026/Final"
  }
]
```

Guardrails:

- Batch delete is destructive; require explicit user confirmation.
- Move is limited to the same drive.
- Use `item_id` when paths are ambiguous or may change during the batch.

## Notify

Call `m365_notify` when the user asks to notify someone or prepare a follow-up message.

Targets:

- `chat`
- `channel`
- `email`

Default behavior is preview-only. Only send with:

- `preview_only: false`
- `confirm: true`

For email, provide:

- `recipients`
- `subject`
- `message`

For mailbox settings and out-of-office messages, use the Outlook skill and `m365_mail_set_automatic_replies`.

For Teams chat, provide one of:

- `chat_id`
- `chat_name`
- `participant_username`
- `participant_usernames`

For Teams channel, provide:

- `team_id` or `team_name`
- `channel_id` or `channel_name`

## OpenWork behavior

- Prefer planning and preview before mutation.
- Surface missing scopes instead of silently falling back to unsafe behavior.
- Keep large files out of prompt context; use small-file helpers only when explicitly useful.
- Use lower-level `m365_graph_request` only for expert or uncovered Graph workflows.
