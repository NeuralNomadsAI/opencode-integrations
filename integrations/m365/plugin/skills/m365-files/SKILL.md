---

name: m365-files
description: |
Work with Microsoft 365 cloud files stored in SharePoint or OneDrive through Microsoft Graph.

Triggers when user mentions:
- "SharePoint files"
- "OneDrive file"
- "document library"
- "file version"

---

## Preferred tools

Use the comfortable tools first:

- `m365_list_document_libraries`
- `m365_list_drive_items`
- `m365_get_drive_item`
- `m365_list_file_versions`
- `m365_create_share_link`
- `m365_search_drive_items`
- `m365_create_drive_folder`
- `m365_download_drive_item`
- `m365_upload_small_drive_item`
- `m365_update_drive_item`
- `m365_delete_drive_item`

Only fall back to `m365_graph_request` for edge cases not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Common flows

### List document libraries for a site

Call `m365_list_document_libraries` with one of:

- `site_id`
- `site_name`
- `site_url`
- `hostname` + `site_path`

### List files under a folder

Call `m365_list_drive_items` with:

- `drive_id` or `library_name`
- site reference fields when not using `drive_id`
- optional `item_id` or `item_path`
- optional `query` to filter the returned items

### Inspect a single file or folder

Call `m365_get_drive_item` with:

- `drive_id` or `library_name`
- `item_id` or `item_path`

### List versions

Call `m365_list_file_versions` with a file reference.

### Search files

Call `m365_search_drive_items` with:

- `drive_id` or `library_name`
- site reference fields when not using `drive_id`
- `query`
- optional `limit`

### Create a folder

Call `m365_create_drive_folder` with:

- `drive_id` or `library_name`
- optional `parent_item_id` or `parent_path`; omitted means root
- `folder_name`
- optional `conflict_behavior` (`rename`, `replace`, `fail`)

### Download a small file

Call `m365_download_drive_item` with a file reference.

The tool returns `content_base64`, `content_type`, and `byte_length`. It defaults to a 5 MiB limit and supports `max_bytes` up to the runtime cap.

### Upload a small file

Call `m365_upload_small_drive_item` with:

- `drive_id` or `library_name`
- optional `parent_item_id` or `parent_path`; omitted means root
- `file_name`
- `content_base64`
- optional `content_type`
- optional `conflict_behavior` (`rename`, `replace`, `fail`)

Use this only for small files. Large files need an upload-session workflow, not this JSON-safe helper.

### Rename or move an item

Call `m365_update_drive_item` with a source file or folder reference and at least one of:

- `new_name`
- `target_parent_item_id`
- `target_parent_path`

This helper supports moving within the same drive only.

### Delete an item

Call `m365_delete_drive_item` with a file or folder reference and `confirm: true`.

Only delete after the user explicitly confirms the exact target item.

### Create a share link

Call `m365_create_share_link` with a file reference.

Optional:

- `link_type` (`view`, `edit`, `embed`)
- `scope` (`organization`, `anonymous`, `users`)
- `expiration_datetime`

## Graph fallback paths

### List drives for a site

```text
GET /sites/{site-id}/drives
```

### List root items in a drive

```text
GET /drives/{drive-id}/root/children
```

### Inspect an item

```text
GET /drives/{drive-id}/items/{item-id}
```

### List versions

```text
GET /drives/{drive-id}/items/{item-id}/versions
```

### Create a sharing link

```text
POST /drives/{drive-id}/items/{item-id}/createLink
```

### Upload a small file

```text
PUT /drives/{drive-id}/items/{parent-id}:/{filename}:/content
```

### Rename or move an item

```text
PATCH /drives/{drive-id}/items/{item-id}
```

### Delete an item

```text
DELETE /drives/{drive-id}/items/{item-id}
```

## Guardrails

- Confirm the target site, library or file before mutating content.
- Read the item first with `m365_get_drive_item` before rename, move or delete operations.
- Never call `m365_delete_drive_item` without explicit user confirmation and `confirm: true`.
- Keep base64 upload/download helpers for small files only.
- Prefer read flows first when the user is exploring the tenant.
- Surface missing scopes explicitly, especially for write operations.
- Prefer `item_path` when the user knows the folder structure, and `item_id` when the path may change.
