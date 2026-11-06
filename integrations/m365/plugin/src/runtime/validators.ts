const MAX_LIST_LIMIT = 100

export function clampPositiveInt(value: unknown, fallback: number, max = MAX_LIST_LIMIT) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }

  return Math.min(Math.floor(parsed), max)
}

export function uniqueNonEmpty(values: Array<unknown>) {
  const seen = new Set<string>()
  const result: string[] = []

  for (const value of values) {
    const text = String(value || "").trim()
    if (!text) {
      continue
    }

    const key = text.toLowerCase()
    if (seen.has(key)) {
      continue
    }

    seen.add(key)
    result.push(text)
  }

  return result
}

export function normalizeMatchText(value: unknown) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim()
}

function scoreSingleValue(value: unknown, query: string) {
  const normalizedValue = normalizeMatchText(value)
  const normalizedQuery = normalizeMatchText(query)
  if (!normalizedValue || !normalizedQuery) {
    return 0
  }

  if (normalizedValue === normalizedQuery) {
    return 1000
  }

  if (normalizedValue.startsWith(normalizedQuery)) {
    return 750
  }

  if (normalizedValue.includes(normalizedQuery)) {
    return 500
  }

  const queryTokens = normalizedQuery.split(" ").filter(Boolean)
  if (queryTokens.length > 1 && queryTokens.every((token) => normalizedValue.includes(token))) {
    return 300
  }

  return 0
}

export function scoreBestValue(values: Array<unknown>, query?: string) {
  if (!query) {
    return 0
  }

  let best = 0
  for (const value of values) {
    best = Math.max(best, scoreSingleValue(value, query))
  }

  return best
}

export function filterAndSortMatches<T>(
  items: T[],
  query: string | undefined,
  valuesForItem: (item: T) => Array<unknown>,
) {
  if (!query) {
    return items
  }

  return items
    .map((item) => ({ item, score: scoreBestValue(valuesForItem(item), query) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((entry) => entry.item)
}

export function chooseSingleMatch<T>(
  items: T[],
  scoreForItem: (item: T) => number,
  labelForItem: (item: T) => string,
  kind: string,
) {
  const scored = items
    .map((item) => ({ item, score: scoreForItem(item) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || labelForItem(a.item).localeCompare(labelForItem(b.item)))

  if (scored.length === 0) {
    return null
  }

  if (scored.length > 1 && scored[0]!.score === scored[1]!.score) {
    const preview = scored
      .slice(0, 5)
      .map((entry) => labelForItem(entry.item))
      .join(", ")
    throw new Error(`Ambiguous ${kind}. Matching candidates: ${preview}`)
  }

  return scored[0]!.item
}
