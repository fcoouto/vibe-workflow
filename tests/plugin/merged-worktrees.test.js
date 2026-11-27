import assert from "node:assert/strict"
import { test } from "node:test"
import { busyDirectories, createSweep, parseWorktrees } from "../../src/merged-worktrees.js"

const PORCELAIN = [
  "worktree /repo", "HEAD a1", "branch refs/heads/main", "",
  "worktree /wt/merged one", "HEAD b2", "branch refs/heads/feat/one", "",
  "worktree /wt/open", "HEAD c3", "branch refs/heads/feat/open", "",
  "worktree /wt/detached", "HEAD d4", "detached", "",
  "worktree /wt/locked", "HEAD e5", "branch refs/heads/feat/locked", "locked", "",
  "worktree /wt/moved", "HEAD f6", "branch refs/heads/feat/moved", "prunable gitdir file points to non-existent location", "",
].join("\n")
const MERGED = "for-each-ref --merged refs/remotes/origin/HEAD --format=%(refname:short) refs/heads"
const ANSWERS = {
  "worktree list --porcelain": { code: 0, stdout: PORCELAIN },
  [MERGED]: { code: 0, stdout: "main\nfeat/one\nfeat/locked\nfeat/moved\n" },
  "merge-base --is-ancestor d4 refs/remotes/origin/HEAD": { code: 0, stdout: "" },
}
const SWEEP = "Sweep them per the worktree cleanup steps in vibe-pull-requests."
const EXPLAIN = "Tell your human partner once in your report: cleanup now also removes older merged worktrees and " +
  "tool caches; other files are asked about before the merge."

function gitRun(answers = ANSWERS) {
  return async (cwd, args) => {
    const key = args.join(" ")
    if (!(key in answers)) throw new Error(`unexpected git ${key}`)
    return answers[key]
  }
}

function sweep(overrides = {}) {
  return createSweep({ primary: async () => "/repo", run: gitRun(), busy: async () => null, process: { explained: true },
    ...overrides })
}

test("parseWorktrees reads paths with spaces, branches, detached, locked and prunable", () => {
  const entries = parseWorktrees(PORCELAIN)
  assert.deepEqual(entries[1], { path: "/wt/merged one", head: "b2", branch: "feat/one", locked: false, prunable: false })
  assert.equal(entries[3].branch, null)
  assert.equal(entries[4].locked, true)
  assert.equal(entries[5].prunable, true)
})

test("note names merged worktrees and skips the primary, open and locked ones", async () => {
  const note = await sweep().note({})
  assert.ok(note.startsWith("vibe-workflow: merged worktrees still on disk: "))
  assert.ok(note.includes("/wt/merged one (feat/one)"))
  assert.ok(note.includes("/wt/detached (detached)"))
  assert.ok(note.includes("/wt/moved (feat/moved, needs repair)"))
  assert.ok(note.includes(SWEEP))
  assert.ok(!note.includes("/repo ("))
  assert.ok(!note.includes("/wt/open"))
  assert.ok(!note.includes("/wt/locked"))
})

test("the explanation is sent once per process object", async () => {
  const shared = { explained: false }
  const fresh = sweep({ process: shared })
  assert.equal(await sweep({ primary: async () => null, process: shared }).note({}), null)
  assert.equal(shared.explained, false)
  const first = await fresh.note({})
  assert.ok(first.endsWith(SWEEP + " " + EXPLAIN))
  const second = await sweep({ process: shared }).note({})
  assert.ok(second.endsWith(SWEEP))
  assert.ok(!second.includes(EXPLAIN))
  assert.ok(!(await fresh.note({})).includes(EXPLAIN))
})

test("busy and excluded worktrees are left out", async () => {
  const busy = await sweep({ busy: async () => ({ complete: true, dirs: new Set(["/wt/merged one/sub"]) }) }).note({})
  assert.ok(!busy.includes("/wt/merged one"))
  assert.ok(busy.includes("/wt/detached (detached)"))
  const excluded = await sweep().note({ exclude: [null, "/wt/detached/src"] })
  assert.ok(!excluded.includes("/wt/detached ("))
  assert.ok(excluded.includes("/wt/merged one (feat/one)"))
  const unknown = await sweep({ busy: async () => null }).note({ exclude: ["/wt/detached/src"] })
  assert.ok(!unknown.includes("/wt/detached ("))
  const sibling = await sweep({ busy: async () => ({ complete: true, dirs: new Set(["/wt/merged oneX"]) }) }).note({})
  assert.ok(sibling.includes("/wt/merged one (feat/one)"))
})

test("an incomplete session list suppresses the note", async () => {
  assert.equal(await sweep({ busy: async () => ({ complete: false, dirs: new Set() }) }).note({}), null)
})

test("more than ten merged worktrees list ten then the rest as a count", async () => {
  const lines = ["worktree /repo", "HEAD a", "branch refs/heads/main", ""]
  const merged = ["main"]
  for (let i = 1; i <= 13; i++) {
    lines.push(`worktree /wt/n${i}`, `HEAD h${i}`, `branch refs/heads/n${i}`, "")
    merged.push(`n${i}`)
  }
  const answers = { ...ANSWERS, "worktree list --porcelain": { code: 0, stdout: lines.join("\n") },
    [MERGED]: { code: 0, stdout: merged.join("\n") } }
  const note = await sweep({ run: gitRun(answers) }).note({})
  assert.equal(note.match(/\/wt\/n\d+ \(/g).length, 10)
  assert.ok(note.includes("and 3 more"))
})

test("silent on failure, missing origin HEAD or nothing merged", async () => {
  const throwing = sweep({ run: async () => { throw new Error("no git") } })
  assert.equal(await throwing.note({}), null)
  const missingHead = sweep({ run: gitRun({ ...ANSWERS, [MERGED]: { code: 128, stdout: "" } }) })
  assert.equal(await missingHead.note({}), null)
  assert.equal(await sweep({ primary: async () => null }).note({}), null)
  const onlyPrimary = sweep({ run: gitRun({ ...ANSWERS, "worktree list --porcelain": { code: 0,
    stdout: "worktree /repo\nHEAD a1\nbranch refs/heads/main\n" } }) })
  assert.equal(await onlyPrimary.note({}), null)
})

test("one cancelling deadline covers every read", async () => {
  const hang = (signal) => new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))))
  const calls = []
  const run = async (cwd, args, signal) => {
    calls.push({ key: args.join(" "), signal, live: !signal.aborted })
    if (args[0] === "merge-base") return hang(signal)
    await new Promise((resolve) => setTimeout(resolve, 15))
    return ANSWERS[args.join(" ")]
  }
  const started = Date.now()
  assert.equal(await sweep({ run, deadline: 20 }).note({}), null)
  assert.ok(Date.now() - started < 60)
  assert.ok(calls.some(({ key }) => key.startsWith("merge-base")))
  assert.ok(calls.every(({ signal }) => signal === calls[0].signal))
  assert.ok(calls[0].signal.aborted)
  assert.ok(calls.every(({ live }) => live))

  const hung = []
  const hanging = async (cwd, args, signal) => {
    hung.push(args.join(" "))
    return hang(signal)
  }
  assert.equal(await sweep({ run: hanging, deadline: 20 }).note({}), null)
  assert.ok(hung.every((key) => !key.startsWith("merge-base")))
})

test("the two list reads run in parallel", async () => {
  const starts = []
  let open
  const gate = new Promise((resolve) => { open = resolve })
  const run = async (cwd, args) => {
    const key = args.join(" ")
    if (!key.startsWith("worktree") && !key.startsWith("for-each-ref")) return ANSWERS[key]
    starts.push(key)
    await gate
    return ANSWERS[key]
  }
  const note = sweep({ run }).note({})
  await new Promise((resolve) => setTimeout(resolve, 5))
  assert.equal(starts.length, 2)
  open()
  assert.notEqual(await note, null)
})

test("busyDirectories follows pages and reports completeness", async () => {
  const started = 1000
  const page = (data, next) => ({ data, ...(next && { cursor: { next } }) })
  const stub = (pages) => {
    const calls = []
    const list = async (args) => {
      calls.push(args)
      const value = pages[calls.length - 1]
      if (value instanceof Error) throw value
      return value
    }
    return { ctx: { session: { list } }, calls }
  }
  const done = { time: { busy: started - 10, idle: started - 5 }, location: { directory: "/wt/done" } }
  const running = { time: { busy: started + 10 }, location: { directory: "/wt/a" } }

  const two = stub([page([done], "c2"), page([running])])
  assert.deepEqual(await busyDirectories(two.ctx, started)(new AbortController().signal),
    { complete: true, dirs: new Set(["/wt/a"]) })
  assert.deepEqual(two.calls, [{ limit: 50, order: "desc" }, { cursor: "c2", limit: 50 }])

  const full = stub(Array.from({ length: 5 }, (_, i) => page([], `c${i + 2}`)))
  assert.deepEqual(await busyDirectories(full.ctx, started)(new AbortController().signal),
    { complete: false, dirs: new Set() })
  assert.equal(full.calls.length, 4)

  const failing = stub([new Error("list failed")])
  assert.equal(await busyDirectories(failing.ctx, started)(new AbortController().signal), null)

  const unlocated = stub([page([{ time: { busy: started + 1 } }])])
  assert.equal(await busyDirectories(unlocated.ctx, started)(new AbortController().signal), null)

  const aborted = stub([page([], "c2"), page([running])])
  const controller = new AbortController()
  controller.abort()
  assert.deepEqual(await busyDirectories(aborted.ctx, started)(controller.signal), { complete: false, dirs: new Set() })
  assert.equal(aborted.calls.length, 1)
})
