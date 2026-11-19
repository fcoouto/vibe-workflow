import { execFile } from "node:child_process"
import { realpath } from "node:fs/promises"
import { resolve } from "node:path"
import { promisify } from "node:util"

const exec = promisify(execFile)
const MAX_SESSIONS = 200
const SHELLS = new Set(["bash", "shell"])
const SEPARATORS = new Set([";", "&", "|", "(", ")", "{", "}"])

// Scanners below walk a shell command without evaluating it. Quoted text, command substitutions and
// heredoc bodies are skipped, so only words at command position can match.
function skipBackticks(text, i) {
  while (i < text.length && text[i] !== "`") i += text[i] === "\\" ? 2 : 1
  return i + 1
}

function skipDouble(text, i) {
  while (i < text.length) {
    if (text[i] === "\\") i += 2
    else if (text[i] === '"') return i + 1
    else if (text[i] === "$" && text[i + 1] === "(") i = skipSubstitution(text, i + 2)
    else if (text[i] === "`") i = skipBackticks(text, i + 1)
    else i++
  }
  return i
}

// At "<<" (not "<<<"): reads the operator and its delimiter word.
function heredocStart(text, i) {
  let j = i + 2
  const dash = text[j] === "-"
  if (dash) j++
  while (text[j] === " " || text[j] === "\t") j++
  const quote = text[j] === "'" || text[j] === '"' ? text[j] : null
  if (quote) j++
  const start = j
  while (j < text.length && (quote ? text[j] !== quote : !/[\s;&|()<>]/.test(text[j]))) j++
  return { delimiter: text.slice(start, j), dash, next: quote ? j + 1 : j }
}

// At the newline ending the operator's line: returns the index of the newline closing each body.
function skipHeredocBodies(text, i, pending) {
  for (const { delimiter, dash } of pending) {
    let start = i + 1
    for (;;) {
      if (start >= text.length) return text.length
      let end = text.indexOf("\n", start)
      if (end < 0) end = text.length
      const line = text.slice(start, end)
      if ((dash ? line.replace(/^\t+/, "") : line) === delimiter) { i = end; break }
      start = end + 1
    }
  }
  return i
}

function skipSubstitution(text, i) {
  let depth = 1
  const pending = []
  while (i < text.length) {
    const c = text[i]
    if (c === "\\") i += 2
    else if (c === "'") { const end = text.indexOf("'", i + 1); i = end < 0 ? text.length : end + 1 }
    else if (c === '"') i = skipDouble(text, i + 1)
    else if (c === "`") i = skipBackticks(text, i + 1)
    else if (c === "<" && text[i + 1] === "<" && text[i + 2] !== "<") {
      const found = heredocStart(text, i)
      pending.push(found)
      i = found.next
    } else if (c === "\n" && pending.length) { i = skipHeredocBodies(text, i, pending.splice(0)); i++ }
    else if (c === "(") { depth++; i++ }
    else if (c === ")") { depth--; i++; if (!depth) return i }
    else i++
  }
  return i
}

// A word keeps `quoted` (any quoted part) and `bare` (it began unquoted) so flags are never read from quoted text.
function simpleCommands(text) {
  const commands = [[]]
  const pending = []
  let word = null, i = 0
  const open = (quoted) => {
    word ??= { text: "", quoted: false, bare: !quoted }
    if (quoted) word.quoted = true
  }
  const flush = () => { if (word) { commands.at(-1).push(word); word = null } }
  const split = () => { flush(); if (commands.at(-1).length) commands.push([]) }
  while (i < text.length) {
    const c = text[i]
    if (c === "\\") { open(true); word.text += text[i + 1] ?? ""; i += 2 }
    else if (c === "'") {
      open(true)
      const end = text.indexOf("'", i + 1)
      word.text += text.slice(i + 1, end < 0 ? text.length : end)
      i = end < 0 ? text.length : end + 1
    } else if (c === '"') {
      open(true)
      const end = skipDouble(text, i + 1)
      word.text += text.slice(i + 1, Math.max(i + 1, end - 1))
      i = end
    } else if (c === "`") { open(true); i = skipBackticks(text, i + 1) }
    else if (c === "$" && text[i + 1] === "(") { open(true); i = skipSubstitution(text, i + 2) }
    else if (c === "$" && text[i + 1] === "{") {
      open(false)
      const end = text.indexOf("}", i + 2)
      word.text += text.slice(i, end < 0 ? text.length : end + 1)
      i = end < 0 ? text.length : end + 1
    } else if (c === "<" && text[i + 1] === "<" && text[i + 2] !== "<") {
      flush()
      const found = heredocStart(text, i)
      pending.push(found)
      i = found.next
    } else if (c === "\n") {
      split()
      if (pending.length) i = skipHeredocBodies(text, i, pending.splice(0))
      i++
    } else if (SEPARATORS.has(c)) { split(); i++ }
    else if (c === " " || c === "\t" || c === "\r") { flush(); i++ }
    else { open(false); word.text += c; i++ }
  }
  flush()
  return commands.filter((words) => words.length)
}

const flag = (words, ...names) => words.some((word) => !word.quoted && names.includes(word.text))

function titleOf(words) {
  for (const [index, word] of words.entries()) {
    if (word.bare && word.text.startsWith("--title=")) return word.text.slice("--title=".length)
    if (!word.quoted && (word.text === "-t" || word.text === "--title")) return words[index + 1]?.text ?? null
  }
  return null
}

export function classify(command) {
  const actions = []
  let moved = false
  for (const words of simpleCommands(command)) {
    while (words.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0].text)) words.shift()
    if (words[0]?.text === "rtk" && ["gh", "git"].includes(words[1]?.text)) words.shift()
    const [first, second, third] = words.map((word) => word.text)
    if (first === "cd" || first === "pushd") moved = true
    else if (first === "gh" && second === "pr") {
      const rest = words.slice(3)
      if (third === "create") actions.push({ kind: "pr_create", draft: flag(rest, "--draft", "-d"), title: titleOf(rest) })
      else if (third === "ready") actions.push({ kind: "pr_ready", undo: flag(rest, "--undo") })
      else if (third === "edit") { const title = titleOf(rest); if (title !== null) actions.push({ kind: "pr_retitle", title }) }
      else if (third === "merge") actions.push({ kind: "pr_merge", auto: flag(rest, "--auto") })
    } else if (first === "git") {
      let index = 1, dir = null
      while (words[index]?.bare && words[index].text.startsWith("-")) {
        if (words[index].text === "-C") { dir = words[index + 1]?.text ?? null; index += 2 }
        else index += words[index].text === "-c" ? 2 : 1
      }
      const sub = words[index]?.text, rest = words.slice(index + 1)
      if (sub === "push" && !flag(rest, "--dry-run", "-n")) actions.push({ kind: "push" })
      else if (sub === "checkout" || sub === "switch" || (sub === "branch" && flag(rest, "-m", "-M", "--move"))) {
        actions.push({ kind: "checkout", dir, moved })
      }
    }
  }
  return actions
}

const SUMMARY = "vibe-workflow: open pull requests as [BLOCKED] drafts, mark one [READY] only when its checks and review " +
  "cover the current head, and return it to [BLOCKED] draft after any push or base change. See the vibe-pull-requests skill."
const NOTES = {
  notDraft: "vibe-workflow: this pull request was not opened as a draft; run `gh pr ready --undo` to make it one.",
  notBlocked: 'vibe-workflow: retitle it with `gh pr edit --title "[BLOCKED] <title>"`.',
  push: "vibe-workflow: you pushed after opening or readying a pull request; if it is [READY], return it to [BLOCKED] " +
    "draft (`gh pr ready --undo`, then retitle it `[BLOCKED] ...`) until its evidence covers the new head.",
  userMerges: "vibe-workflow: this pull request is [READY]. The user merges: stop here and tell them it awaits their merge, " +
    "unless they asked you to merge this one.",
  agentMerges: "vibe-workflow: before merging, confirm the checks and review evidence cover the current head; " +
    "merge only a [READY] pull request whose evidence is current.",
}

function mergeNote(auto, agentMerge, primaryPath) {
  const where = primaryPath ? ` (${primaryPath})` : ""
  const when = auto ? "once the auto-merge completes" : "if this merge landed on main"
  return `vibe-workflow: ${when}, update the primary checkout${where}: when it is clean (ignoring ` +
    ".vibe-workflow/settings.json) and on main, run `git fetch origin main` then `git merge --ff-only origin/main` there; " +
    "otherwise report it and change nothing." +
    (agentMerge ? "" : " The user chose to merge pull requests themselves; tell them you merged this one.")
}

function remember(map, key, value) {
  map.delete(key)
  map.set(key, value)
  if (map.size > MAX_SESSIONS) map.delete(map.keys().next().value)
}

export function createAdvisor({ settings, primary, checkout }) {
  const armed = new Map()
  const branches = new Map()
  return {
    clear() { armed.clear(); branches.clear() },
    async advise(event, sessionID) {
      try {
        if (!event || !SHELLS.has(event.tool) || !event.ok || typeof event.command !== "string") return null
        const actions = classify(event.command)
        if (!actions.length) return null
        const state = await settings()
        if (state?.state !== "on") return null
        const agentMerge = state.agent_merge === true, notes = []
        const arm = (value) => { if (sessionID) { if (value) remember(armed, sessionID, true); else armed.delete(sessionID) } }
        for (const action of actions) {
          if (action.kind === "pr_create") {
            notes.push(SUMMARY)
            if (!action.draft) notes.push(NOTES.notDraft)
            if (action.title !== null && !action.title.startsWith("[BLOCKED]")) notes.push(NOTES.notBlocked)
            arm(true)
          } else if (action.kind === "pr_ready" && action.undo) arm(false)
          else if (action.kind === "pr_ready" || (action.kind === "pr_retitle" && action.title.startsWith("[READY]"))) {
            notes.push(agentMerge ? NOTES.agentMerges : NOTES.userMerges)
            arm(true)
          } else if (action.kind === "pr_merge") notes.push(mergeNote(action.auto, agentMerge, await primary()))
          else if (action.kind === "pr_retitle" && /^\[(BLOCKED|SUPERSEDED)\]/.test(action.title)) arm(false)
          else if (action.kind === "push" && sessionID && armed.has(sessionID)) {
            notes.push(NOTES.push)
            arm(false)
          } else if (action.kind === "checkout" && sessionID && !action.moved) {
            const directory = action.dir ? resolve(event.directory ?? "", action.dir) : event.directory
            const [info, home] = directory ? await Promise.all([checkout(directory), primary()]) : []
            const seen = branches.get(sessionID) ?? new Set()
            if (info && home && info.top === home && info.branch !== "main" && !seen.has(info.branch)) {
              seen.add(info.branch)
              remember(branches, sessionID, seen)
              notes.push(`vibe-workflow: the primary checkout (${home}) is now ` +
                (info.branch === "HEAD" ? "detached" : `on ${info.branch}`) +
                ". Never touch the primary checkout; tell the user, and do task work in a worktree.")
            }
          }
        }
        return notes.length ? notes.join("\n") : null
      } catch { return null }
    },
  }
}

async function defaultCheckout(directory) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")))
  try {
    const { stdout } = await exec("git", ["-C", directory, "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"],
      { env: { ...env, LC_ALL: "C" }, timeout: 2000, maxBuffer: 8192 })
    const [top, branch] = stdout.trimEnd().split("\n")
    return top && branch ? { top: await realpath(top), branch } : null
  } catch { return null }
}

function appendNote(event, note) {
  try {
    const result = event.result
    if (typeof result === "string") event.result = `${result}\n\n${note}`
    else if (result && typeof result === "object") {
      for (const key of ["output", "content"]) {
        if (typeof result[key] === "string") { result[key] = `${result[key]}\n\n${note}`; return }
      }
    }
  } catch {}
}

// The hook is unverified on the installed host: every field is read defensively and a missing one keeps the note silent.
export async function registerPrAdvice(ctx, { settings, primary, checkout = defaultCheckout }) {
  const advisor = createAdvisor({ settings, primary, checkout })
  let closed = false
  const close = () => { closed = true; advisor.clear() }
  if (typeof ctx.tool?.hook !== "function") return { registered: false, close }
  try {
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (closed) return
        const input = event?.input ?? event?.args
        if (typeof input?.command !== "string") return
        const exits = [event.result?.metadata?.exit, event.result?.metadata?.exitCode, event.exitCode, event.metadata?.exit]
        const ok = event.status === "completed" && !exits.some((code) => typeof code === "number" && code !== 0)
        const note = await advisor.advise({ tool: event.tool, command: input.command, ok,
          directory: input.workdir ?? input.cwd ?? ctx.location?.directory }, event.sessionID ?? event.session?.id)
        if (note) appendNote(event, note)
      } catch {}
    })
  } catch { return { registered: false, close } }
  return { registered: true, close }
}
