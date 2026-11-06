---

name: m365-sites
description: |
Work with SharePoint sites and document-library roots through Microsoft Graph.

Triggers when user mentions:
- "SharePoint site"
- "site library"
- "search site"
- "site drive"

---

## Preferred tools

Use the comfortable tools first:

- `m365_list_sites`
- `m365_get_site`
- `m365_list_document_libraries`

Only fall back to `m365_graph_request` when a workflow is not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Common flows

### Search sites

Call `m365_list_sites` with `query`.

### Resolve a site exactly

Call `m365_get_site` with one of:

- `site_id`
- `site_name`
- `site_url`
- `hostname` + `site_path`

### List a site's document libraries

Call `m365_list_document_libraries` with a site reference.

## Graph fallback paths

### Search sites

```text
GET /sites?search={query}
```

### Resolve a site by hostname and path

```text
GET /sites/{hostname}:/sites/{site-path}
```

### Read a site

```text
GET /sites/{site-id}
```

### List document libraries

```text
GET /sites/{site-id}/drives
```

### Read a drive root

```text
GET /drives/{drive-id}/root
```

## Guardrails

- Prefer Graph first for site and library navigation.
- Report SharePoint-specific gaps that Microsoft Graph does not cover.
- Keep the user-facing output focused on ids, display names and next useful paths.
