import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { createServer } from "node:http"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { OpenCode } from "@opencode/client"
import { discoverService, serviceFetch } from "../plugin/src/service.ts"

it("service discovery validates before credentials, refuses redirects and bypasses proxies for SDK requests", async () => {
  const scratch = await mkdtemp(join(tmpdir(), "gaffer-service-"))
  const file = join(scratch, "service.json")
  const received: string[] = []
  let redirect = false
  const server = createServer((req, res) => {
    received.push(req.url ?? "")
    assert.equal(req.headers.authorization, `Basic ${Buffer.from("opencode:sentinel").toString("base64")}`)
    if (redirect) {
      res.writeHead(307, { location: `${url}/leaked` }).end()
    } else if (req.url === "/hang") {
      return
    } else if (req.url === "/stream") {
      res.setHeader("content-type", "text/event-stream")
      res.write("data: first\n\n")
      setImmediate(() => res.end("data: last\n\n"))
    } else if (req.method === "POST") {
      const chunks: Buffer[] = []
      req.on("data", (chunk) => chunks.push(chunk))
      req.on("end", () => res.end(Buffer.concat(chunks)))
    } else {
      res.setHeader("content-type", "application/json")
      res.end(JSON.stringify({ pid: 123, version: "test" }))
    }
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`
  const registration = { url, pid: 123, version: "test", password: "sentinel" }
  const oldEnv = { ...process.env }
  try {
    for (const invalid of [
      "http://192.168.1.2:1234",
      "http://localhost:1234",
      "https://127.0.0.1",
      "http://127.1",
      "http://2130706433",
      "http://127.0.0.1@evil.invalid",
    ]) {
      await writeFile(file, JSON.stringify({ ...registration, url: invalid }))
      assert.equal(await discoverService(file), undefined)
      assert.throws(() => serviceFetch(invalid), /loopback/)
    }
    for (const invalid of [{ pid: undefined }, { version: undefined }, { pid: "123" }]) {
      await writeFile(file, JSON.stringify({ ...registration, ...invalid }))
      assert.equal(await discoverService(file), undefined)
    }
    assert.equal(received.length, 0)
    process.env.HTTP_PROXY = process.env.HTTPS_PROXY = process.env.ALL_PROXY = "http://127.0.0.1:1"
    process.env.NO_PROXY = ""
    process.env.no_proxy = ""
    process.env.NODE_USE_ENV_PROXY = "1"
    await writeFile(file, JSON.stringify(registration))
    const endpoint = await discoverService(file)
    assert.ok(endpoint)
    const client = OpenCode.make({ baseUrl: endpoint.url, headers: endpoint.headers, fetch: endpoint.fetch })
    assert.equal((await client.server.info()).pid, 123)
    const post = await endpoint.fetch(`${url}/advertise`, {
      method: "POST",
      headers: endpoint.headers,
      body: JSON.stringify({ token: "host-sentinel" }),
    })
    assert.deepEqual(await post.json(), { token: "host-sentinel" })
    const stream = await endpoint.fetch(`${url}/stream`, { headers: endpoint.headers })
    assert.equal(await stream.text(), "data: first\n\ndata: last\n\n")
    await assert.rejects(
      endpoint.fetch(`${url}/api/info`, {
        headers: endpoint.headers,
        signal: AbortSignal.abort(),
      }),
    )
    await assert.rejects(
      endpoint.fetch(`${url}/hang`, {
        headers: endpoint.headers,
        signal: AbortSignal.timeout(50),
      }),
    )
    assert.equal(
      (await endpoint.fetch(`${url}/api/info?location[directory]=private`, { headers: endpoint.headers })).status,
      200,
    )
    for (const mismatch of [{ pid: 456 }, { version: "other" }]) {
      await writeFile(file, JSON.stringify({ ...registration, ...mismatch }))
      assert.equal(await discoverService(file), undefined)
    }
    redirect = true
    await writeFile(file, JSON.stringify(registration))
    assert.equal(await discoverService(file), undefined)
    await assert.rejects(client.server.info(), /redirect refused/)
    await assert.rejects(endpoint.fetch("http://192.168.1.2/api/info", { headers: endpoint.headers }), /loopback/)
    await assert.rejects(endpoint.fetch("http://127.0.0.1:1/api/info", { headers: endpoint.headers }), /origin changed/)
    assert.ok(!received.includes("/leaked"))
    assert.doesNotThrow(() => serviceFetch("http://[::1]:1234"))
  } finally {
    for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "no_proxy", "NODE_USE_ENV_PROXY"]) {
      if (oldEnv[key] === undefined) delete process.env[key]
      else process.env[key] = oldEnv[key]
    }
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await rm(scratch, { recursive: true, force: true })
  }
})
