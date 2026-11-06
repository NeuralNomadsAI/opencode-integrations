import { isRecord } from "./auth.ts"
import { graphResult } from "./graph.ts"
import { resolveDriveItemReference } from "./m365.ts"
import { chooseSingleMatch, scoreBestValue } from "./validators.ts"

export type WorksheetSummary = {
  id: string | null
  name: string | null
  position: number | null
  visibility: string | null
}

type WorkbookReferenceArgs = {
  drive_id?: string
  library_name?: string
  site_id?: string
  site_name?: string
  site_url?: string
  hostname?: string
  site_path?: string
  item_id?: string
  item_path?: string
  force_refresh?: boolean
}

type WorksheetReferenceArgs = WorkbookReferenceArgs & {
  worksheet_id?: string
  worksheet_name?: string
}

function getString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null
}

function getNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null
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

function encodePathSegment(value: string) {
  return encodeURIComponent(value)
}

function escapeODataStringLiteral(value: string) {
  return value.replace(/'/g, "''")
}

function normalizeMatrixValue(value: unknown) {
  if (!Array.isArray(value)) {
    return [[value]]
  }

  if (value.length === 0) {
    return [] as unknown[]
  }

  if (value.every((item) => Array.isArray(item))) {
    return value.map((row) => (Array.isArray(row) ? row : [row]))
  }

  return [value]
}

function summarizeWorksheet(value: unknown) {
  const record = getRecord(value)

  return {
    id: getString(record?.id),
    name: getString(record?.name),
    position: getNumber(record?.position),
    visibility: getString(record?.visibility),
  } satisfies WorksheetSummary
}

function buildRangePath(driveId: string, itemId: string, worksheetId: string, rangeAddress: string) {
  const encodedAddress = escapeODataStringLiteral(rangeAddress)

  return `/drives/${encodePathSegment(driveId)}/items/${encodePathSegment(itemId)}/workbook/worksheets/${encodePathSegment(worksheetId)}/range(address='${encodedAddress}')`
}

async function listWorkbookWorksheetsInternal(args: WorkbookReferenceArgs) {
  const { drive, item } = await resolveDriveItemReference(args)
  if (!drive.id || !item.id) {
    throw new Error("The resolved workbook item does not expose the ids needed to inspect worksheets.")
  }

  const result = await graphResult({
    path: `/drives/${encodePathSegment(drive.id)}/items/${encodePathSegment(item.id)}/workbook/worksheets`,
    method: "GET",
    force_refresh: Boolean(args.force_refresh),
  })

  const worksheets = getCollectionItems(result).map(summarizeWorksheet)
  return {
    drive,
    item,
    worksheets,
  }
}

async function resolveWorksheetReference(args: WorksheetReferenceArgs) {
  const workbook = await listWorkbookWorksheetsInternal(args)

  if (args.worksheet_id) {
    const exact = workbook.worksheets.find((worksheet) => worksheet.id === args.worksheet_id)
    if (exact) {
      return {
        ...workbook,
        worksheet: exact,
      }
    }

    throw new Error(`Unknown worksheet_id: ${args.worksheet_id}`)
  }

  if (args.worksheet_name) {
    const match = chooseSingleMatch(
      workbook.worksheets,
      (worksheet) => scoreBestValue([worksheet.name], args.worksheet_name),
      (worksheet) => `${worksheet.name || worksheet.id} [${worksheet.id}]`,
      "worksheet",
    )

    if (!match) {
      throw new Error("No matching worksheet was found. Use m365_excel_list_worksheets first.")
    }

    return {
      ...workbook,
      worksheet: match,
    }
  }

  if (workbook.worksheets.length === 1) {
    return {
      ...workbook,
      worksheet: workbook.worksheets[0]!,
    }
  }

  throw new Error(
    "Provide worksheet_id or worksheet_name when the workbook contains multiple worksheets. Use m365_excel_list_worksheets first.",
  )
}

export async function listWorkbookWorksheets(args: WorkbookReferenceArgs) {
  const result = await listWorkbookWorksheetsInternal(args)
  return {
    drive: result.drive,
    item: result.item,
    count: result.worksheets.length,
    worksheets: result.worksheets,
  }
}

export async function readWorkbookRange(args: WorksheetReferenceArgs & { range_address: string }) {
  const workbook = await resolveWorksheetReference(args)
  if (!workbook.drive.id || !workbook.item.id || !workbook.worksheet.id) {
    throw new Error("The resolved workbook worksheet does not expose the ids needed to read a range.")
  }

  const result = await graphResult({
    path: buildRangePath(workbook.drive.id, workbook.item.id, workbook.worksheet.id, args.range_address),
    method: "GET",
    force_refresh: Boolean(args.force_refresh),
  })

  const range = getRecord(result)
  return {
    drive: workbook.drive,
    item: workbook.item,
    worksheet: workbook.worksheet,
    range: {
      address: getString(range?.address),
      cellCount: getNumber(range?.cellCount),
      columnCount: getNumber(range?.columnCount),
      rowCount: getNumber(range?.rowCount),
      text: Array.isArray(range?.text) ? range.text : null,
      values: Array.isArray(range?.values) ? range.values : null,
      formulas: Array.isArray(range?.formulas) ? range.formulas : null,
      formulasLocal: Array.isArray(range?.formulasLocal) ? range.formulasLocal : null,
      numberFormat: Array.isArray(range?.numberFormat) ? range.numberFormat : null,
    },
  }
}

export async function writeWorkbookRange(
  args: WorksheetReferenceArgs & {
    range_address: string
    values: unknown
    formulas?: unknown
    formulas_local?: unknown
    number_format?: unknown
  },
) {
  const workbook = await resolveWorksheetReference(args)
  if (!workbook.drive.id || !workbook.item.id || !workbook.worksheet.id) {
    throw new Error("The resolved workbook worksheet does not expose the ids needed to write a range.")
  }

  const body: Record<string, unknown> = {
    values: normalizeMatrixValue(args.values),
  }

  if (args.formulas !== undefined) {
    body.formulas = normalizeMatrixValue(args.formulas)
  }

  if (args.formulas_local !== undefined) {
    body.formulasLocal = normalizeMatrixValue(args.formulas_local)
  }

  if (args.number_format !== undefined) {
    body.numberFormat = normalizeMatrixValue(args.number_format)
  }

  const result = await graphResult({
    path: buildRangePath(workbook.drive.id, workbook.item.id, workbook.worksheet.id, args.range_address),
    method: "PATCH",
    body,
    force_refresh: Boolean(args.force_refresh),
  })

  const range = getRecord(result)
  return {
    ok: true,
    drive: workbook.drive,
    item: workbook.item,
    worksheet: workbook.worksheet,
    range: {
      address: getString(range?.address),
      cellCount: getNumber(range?.cellCount),
      columnCount: getNumber(range?.columnCount),
      rowCount: getNumber(range?.rowCount),
      values: Array.isArray(range?.values) ? range.values : body.values,
      formulas: Array.isArray(range?.formulas) ? range.formulas : body.formulas || null,
      formulasLocal: Array.isArray(range?.formulasLocal) ? range.formulasLocal : body.formulasLocal || null,
      numberFormat: Array.isArray(range?.numberFormat) ? range.numberFormat : body.numberFormat || null,
    },
  }
}
