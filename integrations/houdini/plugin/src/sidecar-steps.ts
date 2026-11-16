// The panel's Connect steps that do not need OpenCode's client (sidecar.ts): finding this Houdini's offer while the
// service opens the panel's Location, and an error that says which step failed, safe to show in the panel.

// A message of the sidecar's own, safe to show in the panel.
export class PanelError extends Error {}

// How long Connect waits for this Houdini's own offer (it offers itself every 5 s from its start), and for a
// Location OpenCode opens for the panel to load its plugins (tens of seconds in a trial).
export const OFFER_WAIT_MS = 30_000

// An error in one line for the panel: its first line, bounded, without a token, an authorization or a port.
export function briefly(error: unknown) {
  const text = String((error as { message?: unknown } | null)?.message ?? error ?? "unknown error")
    .split(/\r?\n/)[0]
    ?.replace(/\b[0-9a-f]{32,}\b/gi, "…")
    .replace(/(basic|bearer)\s+\S+/gi, "$1 …")
    .replace(/\b(port\s+|:)\d{2,5}\b/gi, "$1…")
    .slice(0, 240)
  return `${text || "unknown error"}${/[.!?]$/.test(text ?? "") ? "" : "."}`
}

// A failed Connect says which step failed and why, in a line (never a credential): the user knows what to check.
export async function step<T>(doing: string, work: () => Promise<T>): Promise<T> {
  try {
    return await work()
  } catch (error) {
    if (error instanceof PanelError) throw error
    throw new PanelError(
      `Connect failed while ${doing}: ${briefly(error)} Check that OpenCode runs, then choose Connect.`,
    )
  }
}

type Offer = { label: string; offerID: string }

// This Houdini's offer: the one with its label among the offered hosts (Houdini offers itself; the panel never).
export async function findOffer(
  hosts: () => Promise<{ offers?: unknown } | undefined>,
  label: string,
  wait = { ms: OFFER_WAIT_MS, sleep: (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)) },
): Promise<Offer> {
  const until = Date.now() + wait.ms
  for (;;) {
    let result: { offers?: unknown } | undefined
    try {
      result = await hosts()
    } catch (error) {
      // The first call in the panel's folder: OpenCode opens that Location and loads its plugins meanwhile, and the
      // integration's RPC is not there yet (a CodeNomad conversation's own Location is long loaded).
      if (!/RPC is unavailable/i.test(String((error as { message?: unknown })?.message ?? error)) || Date.now() > until)
        throw error
      await wait.sleep(1_000)
      continue
    }
    const offers = ((result?.offers ?? []) as Offer[]).filter((offer) => offer.label === label)
    if (offers.length > 1)
      throw new PanelError(`Several Houdinis offer ${label}: attach this conversation from OpenCode (houdini_hosts).`)
    if (offers[0]) return offers[0]
    if (Date.now() > until)
      throw new PanelError(
        "This Houdini does not offer itself to OpenCode (OPENCODE_HOUDINI_OFFER=0, or OpenCode Link not started).",
      )
    await wait.sleep(1_000)
  }
}
