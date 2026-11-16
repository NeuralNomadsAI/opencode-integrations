// Blender's attachments kept across an OpenCode restart: the kit's attachments (packages/integration-kit), under
// Blender's own keys.

import {
  type AttachmentStorage,
  type Attachments,
  createAttachments as createKitAttachments,
  hostDigest as kitHostDigest,
  trackAdvertise as kitTrackAdvertise,
  type OfferRegistry,
  resetAttachmentsForTests as resetKit,
} from "./kit.ts"

export { ATTACHMENT_LEASE_MS as LEASE_MS } from "./kit.ts"
export type { AttachmentStorage, Attachments }

const APP = "blender"

export const hostDigest = (offerKey: string) => kitHostDigest(offerKey, APP)

export const trackAdvertise = (registry: OfferRegistry, advertise: (input: unknown) => Promise<unknown>) =>
  kitTrackAdvertise(registry, advertise, APP)

export const createAttachments = (options: Omit<Parameters<typeof createKitAttachments>[0], "app">) =>
  createKitAttachments({ ...options, app: APP })

export function resetAttachmentsForTests() {
  resetKit(APP)
}
