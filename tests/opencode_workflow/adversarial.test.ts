// Additional boundary coverage; tests/test_opencode_workflow.py runs this suite without dependencies.
import { test } from "node:test"
import assert from "node:assert/strict"
import { DEFAULT_SETTINGS, Gate } from "../../runtime/adapters/opencode_workflow/gate.ts"
import { checkFailed } from "../../runtime/adapters/opencode_workflow/index.ts"

const route = (variant = "low") => ({ provider: "p", model: "m", variant })
const file = "/work/canary.py"

function make(...roots: string[]) {
  const gate = new Gate(DEFAULT_SETTINGS)
  for (const root of roots) {
    gate.created(root, null)
    gate.routed(root, route())
  }
  return gate
}

function trip(gate: Gate, root: string, files = [file]) {
  for (let round = 0; round < 3; round++) {
    gate.edited(root, files)
    gate.failed(root)
  }
}

test("qualification: roots editing the same absolute path do not share holds or resets", () => {
  // Catch a tree map accidentally keyed only by file, or a reset applied globally.
  const gate = make("ses_one", "ses_two")
  trip(gate, "ses_one")
  assert.equal(gate.editDecision("ses_two", [file]).effect, "allow")
  trip(gate, "ses_two")
  gate.reset("ses_one", { files: [] })
  assert.equal(gate.editDecision("ses_one", [file]).effect, "allow")
  assert.equal(gate.editDecision("ses_two", [file]).effect, "deny")
  assert.equal(gate.status("ses_two").held[0].rounds, 2)
})

test("qualification: diagnosis must outrank earlier levels, not only the last repair", () => {
  // Catch release comparing only the latest level after a worker changes effort.
  const gate = make("ses_root")
  gate.edited("ses_root", [file])
  gate.failed("ses_root")
  gate.routed("ses_root", route("high"))
  gate.edited("ses_root", [file])
  gate.failed("ses_root")
  gate.routed("ses_root", route("low"))
  gate.edited("ses_root", [file])
  gate.failed("ses_root")
  gate.created("ses_medium", "ses_root")
  gate.routed("ses_medium", route("medium"))
  gate.finished("ses_medium")
  assert.equal(gate.editDecision("ses_root", [file]).effect, "deny")
  gate.created("ses_max", "ses_root")
  gate.routed("ses_max", route("max"))
  gate.finished("ses_max")
  assert.equal(gate.editDecision("ses_root", [file]).effect, "allow")
})

test("qualification: a mixed-file denial and repeated status reads preserve the hold", () => {
  // Catch status consuming state, or a mixed edit implicitly clearing the held member.
  const gate = make("ses_root")
  trip(gate, "ses_root")
  const before = gate.status("ses_root")
  for (let read = 0; read < 50; read++) {
    assert.equal(gate.editDecision("ses_root", ["/work/free.py", file]).effect, "deny")
    assert.equal(gate.editDecision("ses_root", ["/work/free.py"]).effect, "allow")
    assert.deepEqual(gate.status("ses_root"), before)
  }
})

test("qualification: bounded state drops old trees without contaminating newest roots", () => {
  // Catch stale session references reviving evicted holds or polluting a replacement tree.
  const gate = make("ses_old")
  trip(gate, "ses_old")
  for (let i = 0; i < 100; i++) {
    gate.created(`ses_new${i}`, null)
    gate.routed(`ses_new${i}`, route())
  }
  assert.equal(gate.editDecision("ses_old", [file]).effect, "allow")
  assert.deepEqual(gate.status("ses_old").held, [])
  trip(gate, "ses_new99")
  assert.equal(gate.editDecision("ses_new99", [file]).effect, "deny")
  assert.equal(gate.editDecision("ses_new98", [file]).effect, "allow")
})

test("qualification: file saturation preserves existing holds and fails open for new paths", () => {
  // Catch unbounded insertion or saturation accidentally removing already-held files.
  const gate = make("ses_root")
  trip(gate, "ses_root")
  gate.edited("ses_root", Array.from({ length: 600 }, (_, i) => `/work/extra${i}.py`))
  trip(gate, "ses_root", ["/work/extra599.py"])
  assert.equal(gate.editDecision("ses_root", [file]).effect, "deny")
  assert.equal(gate.editDecision("ses_root", ["/work/extra599.py"]).effect, "allow")
})

test("qualification: fixed-seed interleavings preserve independent-root behavior", () => {
  // Catch global pending/round/route state by comparing one root with the same trace without noise.
  let seed = 0x51a7e
  const random = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0)
  for (let trial = 0; trial < 100; trial++) {
    const actual = make("ses_target", "ses_noise")
    const reference = make("ses_target")
    for (let step = 0; step < 100; step++) {
      const target = random() % 2 === 0
      const root = target ? "ses_target" : "ses_noise"
      const path = `/work/file${random() % 3}.py`
      const action = random() % 5
      const apply = (gate: Gate) => {
        if (action === 0 && gate.editDecision(root, [path]).effect === "allow") gate.edited(root, [path])
        if (action === 1) gate.failed(root)
        if (action === 2) gate.routed(root, route(step % 2 ? "high" : "low"))
        if (action === 3) gate.reset(root, { files: [path] })
        if (action === 4) gate.status(root)
      }
      apply(actual)
      if (target) apply(reference)
      assert.deepEqual(actual.status("ses_target"), reference.status("ses_target"), `trial=${trial} step=${step}`)
      for (let i = 0; i < 3; i++) {
        const paths = [`/work/file${i}.py`]
        assert.deepEqual(actual.editDecision("ses_target", paths), reference.editDecision("ses_target", paths))
      }
    }
  }
})

test("qualification: malformed count tokens never supplement a zero exit", () => {
  // Catch partial regex acceptance of a failure count with extra, signed or Unicode digits.
  const tokens = ["-1", "+1", "01", "1.0", "1e3", "NaN", "Infinity", "１", "١", "1 extra", "1\u0000", "1\n"]
  for (const token of tokens) {
    for (const name of ["failures", "errors", "unexpected successes"]) {
      assert.equal(checkFailed({ status: "completed", input: { command: "python3 -m unittest | tail -n 5" },
        result: { metadata: { exit: 0 }, content: `Ran 1 test in 0.001s\n\nFAILED (${name}=${token})\n` } }), false,
      `${name}=${JSON.stringify(token)}`)
    }
  }
})
