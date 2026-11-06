import { requestHost as request } from "./kit.ts"
export type HostBinding = { host: string; port: number; token: string; scriptHandle: string }
export async function requestHost(
  binding: HostBinding,
  method: string,
  params: Record<string, unknown> = {},
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<Record<string, unknown>> {
  return (await request(binding, method, { ...params, script_handle: binding.scriptHandle }, options)) as Record<
    string,
    unknown
  >
}
