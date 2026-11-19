import { execFile } from "node:child_process"
import { createHash, randomUUID } from "node:crypto"
import { constants } from "node:fs"
import { lstat, mkdir, open, realpath, rename, rmdir, unlink } from "node:fs/promises"
import { isAbsolute, join, relative, sep } from "node:path"
import { promisify, TextDecoder } from "node:util"

const exec = promisify(execFile)
const limit = 65536
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value)
const same = (a, b) => a.dev === b.dev && a.ino === b.ino
const unsafe = () => new Error("unsafe project settings")
const owned = (info) => info.uid === process.getuid() && !(info.mode & 0o022)

async function findRoot(directory) {
  if (typeof directory !== "string" || !isAbsolute(directory)) throw unsafe()
  const location = await realpath(directory)
  if (!(await lstat(location)).isDirectory()) throw unsafe()
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  let stdout
  try {
    ({ stdout } = await exec("git", ["rev-parse", "--show-toplevel"], {
      cwd: location, env: { ...env, LC_ALL: "C" }, timeout: 2000, maxBuffer: 8192,
    }))
  } catch (error) {
    if (error.code === 128 && /^fatal: not a git repository \(or any (?:of the )?parent director(?:y|ies)\): \.git\s*$/.test(error.stderr)) return location
    throw error
  }
  const root = await realpath(stdout.replace(/\n$/, ""))
  const below = relative(root, location)
  if (below === ".." || below.startsWith(`..${sep}`) || isAbsolute(below)) throw unsafe()
  return root
}

async function maybeStat(path) {
  try { return await lstat(path) }
  catch (error) { if (error.code !== "ENOENT") throw error }
}

function uniqueKeys(text) {
  // JSON.parse has validated syntax but collapses duplicate keys.
  const objects = []
  for (const [token, key, colon] of text.matchAll(/("(?:[^"\\]|\\[\s\S])*")([ \t\r\n]*:)?|[{}]/g)) {
    if (token === "{") objects.push(new Set())
    else if (token === "}") objects.pop()
    else if (colon) {
      const keys = objects.at(-1), name = JSON.parse(key)
      if (keys.has(name)) return false
      keys.add(name)
    }
  }
  return true
}

async function readSettings(folder, path) {
  const parent = await maybeStat(folder)
  if (!parent) return {}
  if (!parent.isDirectory() || !owned(parent) || await realpath(folder) !== folder) throw unsafe()
  const info = await maybeStat(path)
  if (!info) return { parent }
  const safeFile = (file) => file.isFile() && file.nlink === 1 && owned(file) && file.size <= limit
  if (!safeFile(info)) throw unsafe()
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
  try {
    const actual = await file.stat()
    if (!same(info, actual) || !safeFile(actual)) throw unsafe()
    const buffer = Buffer.alloc(limit + 1)
    let size = 0
    while (size < buffer.length) {
      const { bytesRead } = await file.read(buffer, size, buffer.length - size)
      if (!bytesRead) break
      size += bytesRead
    }
    if (size > limit || !same(parent, await lstat(folder))) throw unsafe()
    const raw = buffer.subarray(0, size)
    const text = new TextDecoder("utf-8", { fatal: true }).decode(raw)
    const value = JSON.parse(text)
    if (!object(value) || value.version !== 1 || ("smart_router" in value &&
      (!object(value.smart_router) || typeof value.smart_router.enabled !== "boolean")) ||
      !uniqueKeys(text)) throw unsafe()
    return { parent, raw, value, mode: actual.mode & 0o777 }
  } finally { await file.close() }
}

function snapshot(root, raw) {
  return createHash("sha256").update(root).update(raw ? "\0present\0" : "\0absent\0")
    .update(raw ?? "").digest("hex")
}

function decimal(token) {
  const [significand, exponent = "0"] = token.toLowerCase().split("e")
  const negative = significand.startsWith("-")
  const [integer, fraction = ""] = (negative ? significand.slice(1) : significand).split(".")
  const digits = (integer + fraction).replace(/^0+/, "")
  if (!digits) return negative ? "-0" : "0"
  const significant = digits.replace(/0+$/, "")
  return `${negative ? "-" : ""}${significant}e${Number(exponent) - fraction.length + digits.length - significant.length}`
}

function numbersRoundTrip(raw) {
  // JSON.parse has already validated syntax. Skip strings, then compare decimal values
  // before and after JS serialization without requiring a particular number spelling.
  for (const [token] of raw.toString("utf8").matchAll(/"(?:[^"\\]|\\[\s\S])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g)) {
    if (token.startsWith('"')) continue
    const value = Number(token)
    if (!Number.isFinite(value) || decimal(token) !== decimal(JSON.stringify(value))) return false
  }
  return true
}

export function createProjectSettings(directory) {
  let discovery
  async function current() {
    discovery ??= findRoot(directory).catch(() => null)
    const root = await discovery
    if (!root) return { state: "blocked", reason: "project_root_unavailable", root: null, path: null }
    const folder = join(root, ".vibe-workflow"), path = join(folder, "settings.json")
    try {
      const data = await readSettings(folder, path)
      const enabled = data.value?.smart_router?.enabled ?? null
      return { state: enabled === null ? "not_onboarded" : enabled ? "on" : "off",
        reason: null, root, path, enabled, snapshot: snapshot(root, data.raw), folder, data }
    } catch {
      return { state: "blocked", reason: "project_settings_invalid", root, path }
    }
  }
  const publicState = ({ data, folder, ...state }) => state
  return {
    async inspect() { return publicState(await current()) },
    async preview(enabled) {
      const { data, folder, ...state } = await current()
      if (state.state === "blocked") return state
      if (enabled !== undefined && typeof enabled !== "boolean") return { state: "blocked", reason: "invalid_request" }
      const proposed = enabled ?? state.enabled ?? true
      return { ...state, state: "preview", previous_enabled: state.enabled, enabled: proposed,
        change: !data.raw ? "create" : proposed === state.enabled ? "none" : "update" }
    },
    async apply(enabled, expectedSnapshot, { signal } = {}) {
      if (typeof enabled !== "boolean" || typeof expectedSnapshot !== "string" ||
          !/^[a-f0-9]{64}$/.test(expectedSnapshot)) return { state: "blocked", reason: "invalid_request" }
      const initial = await current()
      const { root, path, folder } = initial
      const blocked = (reason) => ({ state: "blocked", reason, root, path, enabled: null })
      if (initial.state === "blocked") return blocked(initial.reason)
      if (signal?.aborted) return blocked("project_settings_cancelled")
      if (initial.snapshot !== expectedSnapshot) return blocked("project_settings_changed")
      if (initial.enabled === enabled) return { state: "unchanged", reason: null, root, path, enabled }
      if (initial.data.raw && !numbersRoundTrip(initial.data.raw)) return blocked("project_settings_lossy")
      const lockPath = join(folder, ".settings.lock")
      let lock, temporary, createdFolder = false, saved = false
      try {
        if (!initial.data.parent) {
          try { await mkdir(folder, { mode: 0o755 }); createdFolder = true }
          catch (error) { if (error.code !== "EEXIST") throw error }
        }
        const parent = await lstat(folder)
        if (!parent.isDirectory() || !owned(parent) || await realpath(folder) !== folder) throw unsafe()
        try { lock = await open(lockPath, "wx", 0o600) }
        catch (error) {
          if (error.code === "EEXIST") return blocked("project_settings_busy")
          throw error
        }
        const latest = await current()
        if (latest.state === "blocked") return blocked(latest.reason)
        if (!latest.data.parent || !same(parent, latest.data.parent) ||
            latest.snapshot !== expectedSnapshot) return blocked("project_settings_changed")
        const value = latest.data.value
        const body = JSON.stringify({ ...value, version: 1,
          smart_router: { ...value?.smart_router, enabled } }, null, 2) + "\n"
        if (Buffer.byteLength(body) > limit) return blocked("project_settings_invalid")
        const target = join(folder, `.settings-${randomUUID()}.tmp`)
        const file = await open(target, "wx", latest.data.mode ?? 0o644)
        temporary = target
        try {
          await file.chmod(latest.data.mode ?? 0o644)
          await file.writeFile(body)
          await file.sync()
        } finally { await file.close() }
        const final = await current()
        if (final.state === "blocked" || !final.data.parent || !same(parent, final.data.parent) ||
            final.snapshot !== expectedSnapshot) return blocked("project_settings_changed")
        if (signal?.aborted) return blocked("project_settings_cancelled")
        await rename(temporary, path)
        saved = true
        return { state: "saved", reason: null, root, path, enabled }
      } catch {
        return blocked("project_settings_write_failed")
      } finally {
        if (temporary && !saved) await unlink(temporary).catch(() => {})
        if (lock) {
          const identity = await lock.stat().catch(() => null)
          const onDisk = await lstat(lockPath).catch(() => null)
          if (identity && onDisk && same(identity, onDisk)) await unlink(lockPath).catch(() => {})
          await lock.close().catch(() => {})
        }
        if (createdFolder && !saved) await rmdir(folder).catch(() => {})
      }
    },
  }
}
