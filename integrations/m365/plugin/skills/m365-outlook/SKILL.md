---

name: m365-outlook
description: |
Work with Outlook mail and mailbox settings through Microsoft Graph.

Triggers when user mentions:
- "send an email"
- "search my mail"
- "out of office"
- "automatic replies"
- "mailbox settings"

---

## Preferred tools

Use these tools for Outlook workflows:

- `m365_mail_search_messages`
- `m365_mail_send_message`
- `m365_mail_get_mailbox_settings`
- `m365_mail_set_automatic_replies`

Use `m365_notify` for generic notification flows where Teams and email are both acceptable.

## Search mail

Call `m365_mail_search_messages` with:

- optional `query` to search messages
- optional `limit`
- optional `folder_id` for a known mail folder

Without `query`, the tool lists recent messages.

## Send mail

Call `m365_mail_send_message` with:

- `to_recipients` or `recipients`
- `subject`
- `message`
- optional `cc_recipients`
- optional `bcc_recipients`
- optional `content_type` (`text` or `html`)

The default is preview-only. Only send with:

- `preview_only: false`
- `confirm: true`

## Automatic replies / out of office

Call `m365_mail_get_mailbox_settings` first when inspecting current state.

Call `m365_mail_set_automatic_replies` to prepare or apply an out-of-office configuration.

Common scheduled example:

```json
{
  "status": "scheduled",
  "start_datetime": "2026-05-01T18:00:00",
  "end_datetime": "2026-05-04T09:00:00",
  "time_zone": "Romance Standard Time",
  "internal_message": "Bonjour, je suis absent jusqu'au lundi 4 mai. Je repondrai a mon retour.",
  "external_audience": "contactsOnly",
  "external_message": "Bonjour, je suis absent jusqu'au lundi 4 mai. Je repondrai a mon retour."
}
```

Disable example:

```json
{
  "status": "disabled"
}
```

The default is preview-only. Only apply with:

- `preview_only: false`
- `confirm: true`

## Guardrails

- Always preview email and automatic replies before applying them.
- Prefer `external_audience: "none"` unless the user explicitly asks for external replies.
- Ask for exact dates, times, and timezone when scheduling automatic replies.
- Keep sensitive mail content out of broad workspace searches unless needed for the task.
