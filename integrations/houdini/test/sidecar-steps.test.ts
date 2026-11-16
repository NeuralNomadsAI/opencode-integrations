// The panel's Connect (sidecar.ts): its first call in a folder OpenCode is still opening waits for that Location's
// plugins ("RPC is unavailable"), and a failed Connect says which step failed and why, without a credential. Gaffer
// trial: the first Connect failed with "Connection or action failed" next to a CodeNomad conversation attached to
// the same application, and was read as that conversation holding it.

import assert from "node:assert/strict"
import { it } from "node:test"

import { briefly, findOffer, PanelError, step } from "../plugin/src/sidecar-steps.ts"

const TOKEN = "a".repeat(64)
const offers = { offers: [{ offerID: "offer_shot", label: "Houdini: shot.hipnc" }] }

it("Connect waits while OpenCode opens the panel's folder, then finds this Houdini's offer", async () => {
  let calls = 0
  const slept: number[] = []
  const offer = await findOffer(
    async () => {
      calls += 1
      if (calls <= 2) throw new Error("RPC is unavailable: neuralnomads.houdini")
      return offers
    },
    "Houdini: shot.hipnc",
    { ms: 30_000, sleep: async (ms) => void slept.push(ms) },
  )
  assert.equal(offer.offerID, "offer_shot")
  assert.equal(calls, 3)
  assert.deepEqual(slept, [1_000, 1_000])
})

it("another error, or an RPC still unavailable at the deadline, ends the search", async () => {
  await assert.rejects(
    findOffer(async () => Promise.reject(new Error("socket hang up")), "Houdini: shot.hipnc", {
      ms: 30_000,
      sleep: async () => {},
    }),
    /socket hang up/,
  )
  await assert.rejects(
    findOffer(
      async () => Promise.reject(new Error("RPC is unavailable: neuralnomads.houdini")),
      "Houdini: shot.hipnc",
      {
        ms: -1,
        sleep: async () => {},
      },
    ),
    /RPC is unavailable/,
  )
  await assert.rejects(
    findOffer(async () => ({ offers: [...offers.offers, ...offers.offers] }), "Houdini: shot.hipnc", {
      ms: -1,
      sleep: async () => {},
    }),
    (error: Error) => error instanceof PanelError && /^Several Houdinis offer Houdini: shot\.hipnc/.test(error.message),
  )
})

it("a failed Connect says which step failed and why, without a credential", async () => {
  await assert.rejects(
    step("finding this Houdini's offer", async () => {
      throw new Error("RPC is unavailable: neuralnomads.houdini")
    }),
    (error: Error) =>
      error instanceof PanelError &&
      error.message ===
        "Connect failed while finding this Houdini's offer: RPC is unavailable: neuralnomads.houdini. Check that OpenCode runs, then choose Connect.",
  )
  await assert.rejects(
    step("creating this file's conversation", async () => {
      throw new Error(`refused by 127.0.0.1:4096 with Basic ${TOKEN} on port 51396\n    at stack`)
    }),
    (error: Error) => {
      assert.equal(
        error.message,
        "Connect failed while creating this file's conversation: refused by 127.0.0.1:… with Basic … on port …. Check that OpenCode runs, then choose Connect.",
      )
      return true
    },
  )
  // The panel's own messages pass as they are.
  await assert.rejects(
    step("reaching OpenCode", async () => {
      throw new PanelError("Start OpenCode before connecting the panel.")
    }),
    (error: Error) => error instanceof PanelError && error.message === "Start OpenCode before connecting the panel.",
  )
  assert.equal(briefly(new Error(`token ${TOKEN}`)), "token ….")
})
