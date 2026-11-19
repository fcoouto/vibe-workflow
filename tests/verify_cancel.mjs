// Opt-in cancellation proof: exported plugin, real CLI, synthetic native executable.
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { watch } from "node:fs"
import { mkdir, writeFile, readFile, realpath } from "node:fs/promises"
import { join, resolve } from "node:path"
import { pathToFileURL } from "node:url"

const [exportArg, evidenceArg] = process.argv.slice(2)
assert.ok(exportArg && evidenceArg, "usage: node tests/verify_cancel.mjs EXPORT EVIDENCE")
const root = resolve(evidenceArg)
await mkdir(root, { recursive: true, mode: 0o700 })
assert.equal(await realpath(root), root)
const bin = join(root, "bin"), state = join(root, "state")
await mkdir(bin, { mode: 0o700 })
await mkdir(state, { mode: 0o700 })
await mkdir(join(root, ".vibe-workflow"), { mode: 0o700 })
await writeFile(join(root, ".vibe-workflow", "settings.json"),
  '{"version":1,"features":[{"smart_router":{"enabled":true}}]}', { mode: 0o600 })
const python = spawnSync("python3", ["-I", "-B", "-c", "import sys; print(sys.executable)"], { encoding: "utf8" })
assert.equal(python.status, 0)
const executable = python.stdout.trim()
await writeFile(join(bin, "claude"), `#!${executable}
import json, os, select, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
for line in sys.stdin:
    request = json.loads(line)
    with (root / "trace.jsonl").open("a") as out:
        out.write(json.dumps(request) + "\\n")
    if request["request"]["subtype"] == "initialize":
        print(json.dumps(dict(type="control_response", response=dict(
            request_id=request["request_id"], subtype="success", response=dict(
                account=dict(email="invented@example.invalid", apiProvider="firstParty"),
                claude_code_version="synthetic", models=[dict(value="invented-model")])))), flush=True)
    else:
        temporary = root / "ready.tmp"
        temporary.write_text(json.dumps(dict(reader=os.getpid(), cli=os.getppid())))
        temporary.replace(root / "ready.json")
        if select.select([sys.stdin], [], [], 5)[0]:
            sys.stdin.read()
        break
`, { mode: 0o700 })
await writeFile(join(bin, "opencode"), `#!${executable}
from pathlib import Path
Path(__file__).resolve().parents[1].joinpath("unexpected-opencode").write_text("unexpected")
raise SystemExit(1)
`, { mode: 0o700 })
await writeFile(join(state, "router.json"), JSON.stringify({
  version: 1, enabled: true, paths: ["opencode-claude-subscription"],
}), { mode: 0o600 })
await writeFile(join(state, "strategy-table.json"), JSON.stringify({
  schema: 2, version: 1, rows: { "review|high|high": [{
    strategy_id: "invented", provider: "claude-agent", model: "invented-model",
    effort: "medium", harness: "claude-agent-sdk via opencode",
  }] },
}), { mode: 0o600 })

// The caller starts this verifier with a stripped environment and disposable HOME.
assert.equal(process.env.HOME, root)
process.env.PATH = bin + ":" + process.env.PATH
delete process.env.VCW_TEST_HOME
let tool, registrationCleanup, timer, watcher, pending
const controller = new AbortController()
const ready = new Promise((accept, reject) => {
  watcher = watch(root, async (event, filename) => {
    if (filename !== "ready.json") return
    try { accept(JSON.parse(await readFile(join(root, "ready.json"), "utf8"))) }
    catch (error) { reject(error) }
  })
  timer = setTimeout(() => reject(Error("native acquisition did not become ready")), 5000)
})
const started = performance.now()
let result
try {
  const plugin = (await import(pathToFileURL(join(resolve(exportArg), "index.js")))).default
  registrationCleanup = await plugin.setup({
    location: { directory: root }, options: { state_dir: state },
    tool: { transform: async (apply) => apply({ add: (entry) => {
      if (entry.name === "smart_router_select") tool = entry
    } }) },
    skill: { transform: async (apply) => apply({ add: () => {} }) },
    session: { hook: async () => {} },
  })
  pending = tool.execute({ v: 1, work_kind: "review" }, { signal: controller.signal })
  const identity = await ready
  clearTimeout(timer)
  watcher.close()
  const cancelledAt = performance.now()
  controller.abort()
  const output = await pending
  const answer = JSON.parse(output.content)
  assert.equal(answer.state, "blocked")
  assert.equal(answer.route, null)
  for (const key of ["admission", "reservation", "spend_permit"]) assert.equal(answer[key], false)
  assert.ok(performance.now() - cancelledAt < 4000, "cancellation exceeded cleanup budget")
  for (const pid of Object.values(identity)) {
    assert.throws(() => process.kill(pid, 0), (error) => error.code === "ESRCH", `owned pid ${pid} remains`)
  }
  const requests = (await readFile(join(root, "trace.jsonl"), "utf8")).trim().split("\n").map(JSON.parse)
  assert.deepEqual(requests.map((item) => item.request.subtype), ["initialize", "get_usage"])
  const check = spawnSync(executable, ["-I", "-B", "-c", `
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime import claude_usage, processes
settings = Path(sys.argv[2])
assert claude_usage.account(settings, int(time.time())) is None
with processes._pool_lock(settings, "opencode-account", 0) as outer:
    with claude_usage._lock(settings, 0) as inner:
        assert outer and inner
print(json.dumps(dict(account_complete=False, locks_released=True)))
`, resolve(exportArg), join(state, "router.json")],
  { encoding: "utf8", env: process.env, timeout: 5000 })
  assert.equal(check.status, 0, check.stderr)
  await assert.rejects(readFile(join(root, "unexpected-opencode")))
  result = {
    state: "passed", identity, requests: requests.length, reply: answer.state,
    ...JSON.parse(check.stdout), caller: "fake host context with real AbortSignal",
    transport: "actual exported CLI and synthetic native subprocess",
    elapsed_seconds: (performance.now() - started) / 1000,
  }
  await writeFile(join(root, "cancellation-result.json"), JSON.stringify(result, null, 2) + "\n", { mode: 0o600 })
  console.log(JSON.stringify(result))
} finally {
  clearTimeout(timer)
  watcher?.close()
  controller.abort()
  await registrationCleanup?.()
  await pending?.catch(() => {})
}
