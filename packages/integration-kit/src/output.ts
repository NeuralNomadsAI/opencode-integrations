export function toJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export function errorToMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }

  return String(error)
}
