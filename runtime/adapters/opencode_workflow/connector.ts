import type { StopForms } from "./stop.ts"
import type { Endpoint } from "@opencode/client/service"
import type { OpenCodeClient } from "@opencode/client"

const loadOfficial = async () => {
  const [{ OpenCode }, { Service }] = await Promise.all([import("@opencode/client"), import("@opencode/client/service")])
  return { OpenCode, Service }
}

// Discovery has no AbortSignal API. Discard late results; never start or repair a service.
function bounded<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted()
  return new Promise((done, fail) => {
    const abort = () => fail(new Error("connector stopped"))
    signal.addEventListener("abort", abort, { once: true })
    work.then((value) => { signal.removeEventListener("abort", abort); signal.aborted ? abort() : done(value) },
      (error) => { signal.removeEventListener("abort", abort); fail(error) })
  })
}

type Official = { OpenCode: Pick<Awaited<ReturnType<typeof loadOfficial>>["OpenCode"], "make">;
  Service: Pick<Awaited<ReturnType<typeof loadOfficial>>["Service"], "discover" | "headers"> }

export function createStopForms(directory: string, seams: { load?: () => Promise<Official>; fetch?: typeof fetch } = {}): StopForms {
  let dependency: Promise<Official> | undefined
  const invoke = async (sessionID: string, options: { signal: AbortSignal },
    operation: (forms: OpenCodeClient["session"]["form"], options: { signal: AbortSignal }) => Promise<unknown>) => {
    options.signal.throwIfAborted()
    const signal = AbortSignal.any([options.signal, AbortSignal.timeout(8000)])
    const { OpenCode, Service } = await bounded(dependency ??= (seams.load ?? loadOfficial)(), signal)
    if (typeof Service?.discover !== "function" || typeof Service?.headers !== "function"
      || typeof OpenCode?.make !== "function") throw new Error("incompatible connector")
    signal.throwIfAborted()
    const endpoint = await bounded<Endpoint | undefined>(Service.discover(), signal)
    signal.throwIfAborted()
    if (!endpoint?.auth || endpoint.auth.type !== "basic") throw new Error("authenticated service unavailable")
    const base = new URL(endpoint.url)
    if (base.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname)
      || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new Error("unsupported endpoint")
    const headers = Service.headers(endpoint)
    if (!new Headers(headers).get("authorization")) throw new Error("authentication unavailable")
    const guardedFetch: typeof fetch = async (request, init) => {
      signal.throwIfAborted()
      const url = new URL(request instanceof Request ? request.url : String(request))
      if (url.origin !== base.origin || url.username || url.password || url.hash) throw new Error("foreign endpoint")
      const response = await (seams.fetch ?? fetch)(request, { ...init, redirect: "error",
        signal: AbortSignal.any([signal, ...(init?.signal ? [init.signal] : [])]) })
      signal.throwIfAborted()
      if (response.redirected || (response.url && new URL(response.url).origin !== base.origin)) throw new Error("redirected request")
      return response
    }
    const client = OpenCode.make({ baseUrl: base.origin, headers, fetch: guardedFetch })
    if (typeof client?.server?.info !== "function" || typeof client?.session?.get !== "function"
      || (["create", "get", "cancel"] as const).some((name) => typeof client.session.form?.[name] !== "function")) {
      throw new Error("form capability unavailable")
    }
    // At most one discovery, identity and location check per owned form operation; no retries or polling.
    const info = await bounded(client.server.info({ signal }), signal)
    signal.throwIfAborted()
    if (info?.pid !== process.pid || !Array.isArray(info.urls)
      || !info.urls.some((url: string) => new URL(url).origin === base.origin)) throw new Error("service identity mismatch")
    const session = await bounded(client.session.get({ sessionID }, { signal }), signal)
    if (session?.id !== sessionID || session?.location?.directory !== directory) throw new Error("session location mismatch")
    signal.throwIfAborted()
    return bounded(operation(client.session.form, { signal }), signal)
  }
  return { create: (input, options) => invoke(input.sessionID, options,
      (forms, request) => forms.create({ ...input.form, sessionID: input.sessionID }, request)),
    get: (input, options) => invoke(input.sessionID, options, (forms, request) => forms.get(input, request)),
    cancel: (input, options) => invoke(input.sessionID, options, (forms, request) => forms.cancel(input, request)) }
}
