---

name: m365-excel
description: |
Work with Excel workbooks stored in Microsoft 365 through Microsoft Graph.

Triggers when user mentions:
- "Excel workbook"
- "read spreadsheet"
- "write cell"
- "worksheet range"

---

## Preferred tools

Use the comfortable tools first:

- `m365_excel_list_worksheets`
- `m365_excel_read_range`
- `m365_excel_write_range`

Only fall back to `m365_graph_request` when a workbook workflow is not covered by the high-level tools.

If authentication is missing, use the `m365-auth` skill first.

## Common flows

### List worksheets

Call `m365_excel_list_worksheets` with a workbook file reference.

### Read a range

Call `m365_excel_read_range` with:

- workbook file reference
- `worksheet_id` or `worksheet_name` when needed
- `range_address`

### Write a range

Call `m365_excel_write_range` with:

- workbook file reference
- `worksheet_id` or `worksheet_name` when needed
- `range_address`
- `values`

Optional:

- `formulas`
- `formulas_local`
- `number_format`

## Graph fallback paths

### List worksheets

```text
GET /drives/{drive-id}/items/{item-id}/workbook/worksheets
```

### Read a range

```text
GET /drives/{drive-id}/items/{item-id}/workbook/worksheets/{worksheet-id}/range(address='A1:C10')
```

### Update a range

```text
PATCH /drives/{drive-id}/items/{item-id}/workbook/worksheets/{worksheet-id}/range(address='A1:C10')
```

### List tables

```text
GET /drives/{drive-id}/items/{item-id}/workbook/tables
```

## Guardrails

- Confirm workbook identity before editing cells.
- Prefer explicit worksheet ids or names over ambiguous display-name guesses.
- Surface required write scopes before attempting mutations.
- If the workbook has multiple sheets, resolve the worksheet explicitly before writing.
