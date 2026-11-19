import { execFile } from "node:child_process"
import { createHash, randomUUID } from "node:crypto"
import { constants } from "node:fs"
import { lstat, mkdir, open, realpath, rename, rmdir, unlink } from "node:fs/promises"
import { isAbsolute, join, relative, resolve, sep } from "node:path"
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
  const git = (...args) => exec("git", args, {
    cwd: location, env: { ...env, LC_ALL: "C" }, timeout: 2000, maxBuffer: 65536,
  })
  let stdout
  try {
    ({ stdout } = await git("rev-parse", "--show-toplevel"))
  } catch (error) {
    if (error.code === 128 && /^fatal: not a git repository \(or any (?:of the )?parent director(?:y|ies)\): \.git\s*$/.test(error.stderr)) {
      return { root: location, project: location }
    }
    throw error
  }
  const root = await realpath(stdout.replace(/\n$/, ""))
  const below = relative(root, location)
  if (below === ".." || below.startsWith(`..${sep}`) || isAbsolute(below)) throw unsafe()
  return { root, project: await mainWorktree(git, location).catch(() => null) ?? root }
}

// Settings stay in this checkout, but a linked worktree names its main worktree,
// listed first, as the project. Bare repositories, submodules and separate Git
// directories have no main checkout outside Git's own folder.
async function mainWorktree(git, location) {
  const [gitDir, commonDir] = (await git("rev-parse", "--absolute-git-dir", "--git-common-dir")).stdout
    .replace(/\n$/, "").split("\n")
  const common = await realpath(resolve(location, commonDir))
  if (await realpath(gitDir) === common) return null
  const [main] = (await git("worktree", "list", "--porcelain", "-z")).stdout.split("\0\0")
  const [path, ...attributes] = main.split("\0")
  if (!path.startsWith("worktree ") || attributes.includes("bare")) return null
  const project = await realpath(path.slice("worktree ".length))
  const below = relative(common, project)
  return below === ".." || below.startsWith(`..${sep}`) || isAbsolute(below) ? project : null
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

const FEATURES = { smart_router: ["enabled"], pull_requests: ["enabled", "agent_merge"] }

async function readSettings(folder, path, feature) {
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
    if (!object(value) || value.version !== 1 || "smart_router" in value || !Array.isArray(value.features) ||
      !uniqueKeys(text)) throw unsafe()
    const names = new Set()
    let featureIndex = -1
    for (const [index, entry] of value.features.entries()) {
      if (!object(entry)) throw unsafe()
      const keys = Object.keys(entry), name = keys[0]
      if (keys.length !== 1 || !name || names.has(name) || !object(entry[name])) throw unsafe()
      names.add(name)
      if (name === feature) {
        const settings = entry[name]
        if (typeof settings.enabled !== "boolean" || FEATURES[feature].some((key) =>
          key in settings && typeof settings[key] !== "boolean")) throw unsafe()
        featureIndex = index
      }
    }
    return { parent, raw, value, featureIndex, mode: actual.mode & 0o777 }
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

export function createProjectSettings(directory, feature = "smart_router", { atProject = false } = {}) {
  if (!Object.hasOwn(FEATURES, feature)) throw new Error("unsupported feature")
  const prefs = feature === "pull_requests"
  // The default feature accepts a boolean or { enabled }; pull_requests accepts { enabled?, agent_merge? }.
  // Unknown keys and non-boolean values are invalid.
  function normalize(values) {
    if (values === undefined) return {}
    if (!prefs && typeof values === "boolean") return { enabled: values }
    if (!object(values) || Object.keys(values).some((key) => !FEATURES[feature].includes(key) ||
      typeof values[key] !== "boolean")) return null
    return values
  }
  let discovery
  async function current() {
    discovery ??= findRoot(directory).catch(() => null)
    const found = await discovery
    if (!found) return { state: "blocked", reason: "project_root_unavailable", root: null, project: null, path: null }
    const { project } = found, root = atProject ? project : found.root
    const folder = join(root, ".vibe-workflow"), path = join(folder, "settings.json")
    try {
      const data = await readSettings(folder, path, feature)
      const entry = data.value?.features[data.featureIndex]?.[feature]
      const enabled = entry?.enabled ?? null
      return { state: enabled === null ? "not_onboarded" : enabled ? "on" : "off",
        reason: null, root, project, path, enabled, ...(prefs && { agent_merge: entry ? entry.agent_merge ?? false : null }),
        snapshot: snapshot(root, data.raw), folder, data }
    } catch {
      return { state: "blocked", reason: "project_settings_invalid", root, project, path }
    }
  }
  const publicState = ({ data, folder, ...state }) => state
  return {
    async inspect() { return publicState(await current()) },
    async preview(values) {
      const { data, folder, ...state } = await current()
      if (state.state === "blocked") return state
      const asked = normalize(values)
      if (!asked) return { state: "blocked", reason: "invalid_request" }
      const enabled = asked.enabled ?? state.enabled ?? true
      const agentMerge = asked.agent_merge ?? state.agent_merge ?? false
      const same = enabled === state.enabled && (!prefs || agentMerge === state.agent_merge)
      return { ...state, state: "preview", previous_enabled: state.enabled, enabled,
        ...(prefs && { previous_agent_merge: state.agent_merge, agent_merge: agentMerge }),
        change: !data.raw ? "create" : same ? "none" : "update" }
    },
    async apply(values, expectedSnapshot, { signal } = {}) {
      const asked = normalize(values)
      if (!asked || typeof asked.enabled !== "boolean" || (prefs && typeof asked.agent_merge !== "boolean") ||
          typeof expectedSnapshot !== "string" ||
          !/^[a-f0-9]{64}$/.test(expectedSnapshot)) return { state: "blocked", reason: "invalid_request" }
      const { enabled, agent_merge: agentMerge } = asked
      const initial = await current()
      const { root, project, path, folder } = initial
      const blocked = (reason) => ({ state: "blocked", reason, root, project, path, enabled: null,
        ...(prefs && { agent_merge: null }) })
      const result = (state) => ({ state, reason: null, root, project, path, enabled,
        ...(prefs && { agent_merge: agentMerge }) })
      if (initial.state === "blocked") return blocked(initial.reason)
      if (signal?.aborted) return blocked("project_settings_cancelled")
      if (initial.snapshot !== expectedSnapshot) return blocked("project_settings_changed")
      if (initial.enabled === enabled && (!prefs || initial.agent_merge === agentMerge)) return result("unchanged")
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
        const { value, featureIndex = -1 } = latest.data
        const features = [...(value?.features ?? [])]
        features[featureIndex < 0 ? features.length : featureIndex] = {
          [feature]: { ...features[featureIndex]?.[feature], enabled, ...(prefs && { agent_merge: agentMerge }) },
        }
        const body = JSON.stringify({ ...value, version: 1, features }, null, 2) + "\n"
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
        return result("saved")
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
