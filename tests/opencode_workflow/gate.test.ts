// Run with `node --test tests/opencode_workflow/gate.test.ts` (Node strips the types); tests/test_opencode_workflow.py wraps it.
import { test } from "node:test"
import assert from "node:assert/strict"
import {
  DEFAULT_SETTINGS, Gate, MAX_ESCALATIONS, REPAIR_ROUNDS, compareRoutes, derivedSession, isCheck, parseSettings,
  resetRequest,
} from "../../runtime/adapters/opencode_workflow/gate.ts"

const R = (model: string, variant: string | null = "low") => ({ provider: "p", model, variant })
const ROOT = "ses_root"
const FA = "/w/a.ts"
const FB = "/w/b.ts"
const DERIVED = "ses_" + "0123456789abcdef".repeat(2)

function gate(rootRoute = R("m"), ladder: string[] = []) {
  const g = new Gate({ ...DEFAULT_SETTINGS, ladder })
  g.created(ROOT, null)
  g.routed(ROOT, rootRoute)
  return g
}

/** Three edits, each followed by a failure: two repair rounds and a failure pending, so the files are held. */
function trip(g: Gate, files = [FA], editor = ROOT) {
  for (let i = 0; i < 3; i++) {
    g.edited(editor, files)
    g.failed(ROOT)
  }
}

/** After a release the diagnosis counts as a failure, so two more edits and failures hold the file again. */
function retrip(g: Gate, files = [FA]) {
  for (let i = 0; i < 2; i++) {
    g.edited(ROOT, files)
    g.failed(ROOT)
  }
}

let serial = 0
/** A new, edit-free child on `route` that runs and succeeds. */
function diagnose(g: Gate, route: ReturnType<typeof R>) {
  const id = `ses_child${++serial}`
  g.created(id, ROOT)
  g.routed(id, route)
  g.finished(id)
  return id
}

const effect = (g: Gate, session = ROOT, files = [FA]) => g.editDecision(session, files).effect
const held = (g: Gate) => g.status(ROOT).held

test("first edits and one fail-fix-pass loop never hold", () => {
  const g = gate()
  g.edited(ROOT, [FA])
  g.failed(ROOT)
  g.edited(ROOT, [FA])
  assert.equal(effect(g), "allow")
  assert.deepEqual(held(g), [])
})

test("holds after two rounds and a new failure", () => {
  const g = gate()
  trip(g)
  assert.equal(REPAIR_ROUNDS, 2)
  const decision = g.editDecision(ROOT, [FA])
  assert.equal(decision.effect, "deny")
  assert.ok(decision.effect !== "allow")
  assert.match(decision.message, /^escalation gate:/)
  assert.match(decision.message, /a\.ts/)
  assert.match(decision.message, /policies\/03-agent-delegation\.md#stuck-pattern-escalation/)
  assert.equal(effect(g, ROOT, [FB]), "allow")
  assert.equal(g.holds(ROOT), true)
})

test("passing checks never clear rounds", () => {
  const g = gate()
  trip(g)
  g.edited(ROOT, [FB])
  g.routed(ROOT, R("m"))
  assert.equal(held(g)[0].rounds, 2)
  assert.equal(effect(g), "deny")
})

test("a failure without a repair in between clears rounds", () => {
  const g = gate()
  g.edited(ROOT, [FA])
  g.failed(ROOT)
  g.edited(ROOT, [FA])  // round 1
  g.failed(ROOT)
  g.failed(ROOT)  // not repaired in between: rounds cleared
  g.edited(ROOT, [FA])  // round 1 again
  g.failed(ROOT)
  assert.equal(effect(g), "allow")
  assert.deepEqual(held(g), [])
})

test("a reviewer created before the latest edit is not a failure", () => {
  const g = gate()
  for (let i = 0; i < 3; i++) {
    g.edited(ROOT, [FA])
    const reviewer = `ses_early${i}`
    g.created(reviewer, ROOT)
    g.edited(ROOT, [FA])  // the record is edited again while the reviewer runs
    g.routed(reviewer, R("m"))
    g.finished(reviewer)
  }
  g.edited(ROOT, [FA])
  assert.equal(effect(g), "allow")
  // A reviewer created after the latest edit does count.
  const h = gate()
  h.edited(ROOT, [FA])
  h.created("ses_late", ROOT)
  h.routed("ses_late", R("m"))
  h.finished("ses_late")
  h.edited(ROOT, [FA])
  h.failed(ROOT)
  h.edited(ROOT, [FA])
  h.failed(ROOT)
  assert.equal(effect(h), "deny")
})

test("holds edits in children of the same tree", () => {
  const g = gate()
  g.created("ses_a", ROOT)
  g.created("ses_a1", "ses_a")
  trip(g)
  assert.equal(effect(g, "ses_a"), "deny")
  assert.equal(effect(g, "ses_a1"), "deny")
})

test("a same-level reviewer never releases", () => {
  const g = gate()
  trip(g)
  diagnose(g, R("m"))
  diagnose(g, R("m"))
  assert.equal(effect(g), "deny")
})

test("a stronger edit-free child created after the hold releases", () => {
  const g = gate()
  trip(g)
  diagnose(g, R("m", "high"))
  assert.equal(effect(g), "allow")
  assert.deepEqual(held(g), [])
  // The first repair after the release is round 1 of the new level, not a new hold.
  g.edited(ROOT, [FA])
  g.failed(ROOT)
  assert.equal(effect(g), "allow")
})

test("a child created before the hold, or that edited, does not release", () => {
  const g = gate()
  g.created("ses_before", ROOT)
  trip(g)
  g.routed("ses_before", R("m", "high"))
  g.finished("ses_before")
  assert.equal(effect(g), "deny")
  g.created("ses_editor", ROOT)
  g.routed("ses_editor", R("m", "high"))
  g.edited("ses_editor", [FB])
  g.finished("ses_editor")
  assert.equal(effect(g), "deny")
})

test("concurrent children: only the stronger one releases", () => {
  const g = gate()
  trip(g)
  g.created("ses_same", ROOT)
  g.created("ses_strong", ROOT)
  g.routed("ses_same", R("m"))
  g.routed("ses_strong", R("m", "high"))
  g.finished("ses_same")
  assert.equal(effect(g), "deny")
  g.finished("ses_strong")
  assert.equal(effect(g), "allow")
})

test("default variant and empty ladder release through a different route", () => {
  const g = gate(R("m", "default"))
  trip(g)
  diagnose(g, R("m", "default"))
  assert.equal(effect(g), "deny")
  diagnose(g, R("n", "default"))
  assert.equal(effect(g), "allow")
})

test("three escalations lead to re-evaluation, accepted when not weaker", () => {
  const ladder = ["p/m", "p/n", "p/o", "p/q", "p/r", "p/s", "p/t"]
  const g = gate(R("m"), ladder)
  trip(g)
  for (const model of ["n", "o", "q"]) {
    assert.match(g.editDecision(ROOT, [FA]).message ?? "", /stronger/)
    diagnose(g, R(model))
    assert.equal(effect(g), "allow")
    retrip(g)
  }
  assert.equal(MAX_ESCALATIONS, 3)
  assert.equal(held(g)[0].stage, "reevaluation")
  assert.match((g.editDecision(ROOT, [FA]) as { message: string }).message, /re-evaluation/)
  diagnose(g, R("o"))  // weaker than a level: no release
  assert.equal(effect(g), "deny")
  diagnose(g, R("q"))  // as strong as the strongest level
  assert.equal(effect(g), "allow")
})

test("re-evaluation resets once; the next exhaustion asks; an approved edit clears", () => {
  const ladder = ["p/m", "p/n", "p/o", "p/q", "p/r", "p/s", "p/t"]
  const g = gate(R("m"), ladder)
  trip(g)
  for (const model of ["n", "o", "q"]) {
    diagnose(g, R(model))
    retrip(g)
  }
  diagnose(g, R("q"))  // re-evaluation accepted: count reset once
  retrip(g)
  assert.equal(held(g)[0].stage, "escalating")
  for (const model of ["r", "s", "t"]) {
    diagnose(g, R(model))
    retrip(g)
  }
  assert.equal(held(g)[0].stage, "stop")
  const decision = g.editDecision(ROOT, [FA])
  assert.equal(decision.effect, "ask")
  diagnose(g, R("t", "max"))  // no second reset
  assert.equal(effect(g), "ask")
  const before = held(g)
  g.edited(ROOT, [FA])
  assert.deepEqual(held(g), before, "native completion alone must not clear a stopped entry")
  g.edited(ROOT, [FA], g.stopped?.(ROOT, FA)?.hold)  // matching form-qualified completion
  assert.equal(effect(g), "allow")
  assert.deepEqual(held(g), [])
})

test("no ranked option above goes to re-evaluation, then asks", () => {
  const g = gate(R("m", "max"))
  trip(g)
  assert.equal(held(g)[0].stage, "reevaluation")
  diagnose(g, R("m", "max"))
  assert.equal(effect(g), "allow")
  retrip(g)
  assert.equal(held(g)[0].stage, "stop")
  assert.equal(effect(g), "ask")
})

test("a stop permit cannot clear another tree or a reset-and-reheld incarnation", () => {
  const g = gate(R("m", "max"))
  trip(g)
  diagnose(g, R("m", "max"))
  retrip(g)
  const old = g.stopped?.(ROOT, FA)
  assert.ok(old, "the gate must expose the stopped incarnation to its approval owner")
  g.created("ses_other", null)
  g.routed("ses_other", R("m", "max"))
  for (let i = 0; i < 3; i++) { g.edited("ses_other", [FA]); g.failed("ses_other") }
  g.edited("ses_other", [FA], old.hold)
  assert.equal(g.editDecision("ses_other", [FA]).effect, "deny")
  g.reset(ROOT, { files: [] })
  trip(g)
  diagnose(g, R("m", "max"))
  retrip(g)
  const current = g.stopped(ROOT, FA)!
  assert.notEqual(current.hold, old.hold)
  g.edited(ROOT, [FA], old.hold)
  assert.equal(effect(g), "ask")
  g.edited(ROOT, [FA], current.hold)
  assert.equal(effect(g), "allow")
})

test("ranking: ladder order, then variants; unranked is null", () => {
  assert.equal(compareRoutes(R("m", "high"), R("m", "low"), []), 1)
  assert.equal(compareRoutes(R("m", "low"), R("m", "low"), []), 0)
  assert.equal(compareRoutes(R("m", "low"), R("m", "max"), []), -1)
  assert.equal(compareRoutes(R("m"), R("n"), []), null)
  assert.equal(compareRoutes(R("n", "low"), R("m", "max"), ["p/m", "p/n"]), 1)
  assert.equal(compareRoutes(R("m", "max"), R("n", "low"), ["p/m", "p/n"]), -1)
  assert.equal(compareRoutes(R("m", "default"), R("m", "low"), []), null)
  assert.equal(compareRoutes(R("m", null), R("m", "low"), []), null)
  assert.equal(compareRoutes(R("x"), R("m"), ["p/m"]), null)
  assert.equal(compareRoutes({ provider: "q", model: "m", variant: "low" }, R("m"), ["p/m"]), null)
})

test("user reset clears named files or every held file", () => {
  const g = gate()
  trip(g, [FA, FB])
  assert.deepEqual(g.reset(ROOT, { files: ["a.ts"] }), [FA])
  assert.equal(effect(g, ROOT, [FA]), "allow")
  assert.equal(effect(g, ROOT, [FB]), "deny")
  assert.deepEqual(g.reset(ROOT, { files: [] }), [FB])
  assert.equal(g.holds(ROOT), false)
  assert.deepEqual(g.reset(ROOT, { files: [] }), [])
})

test("resetRequest: phrases, files and negation", () => {
  assert.deepEqual(resetRequest("please reset the counter for a.ts"), { files: ["a.ts"] })
  assert.deepEqual(resetRequest("Reset the escalation hold on src/x.py and b.ts"), { files: ["src/x.py", "b.ts"] })
  assert.deepEqual(resetRequest("that one's unrelated, reset the gate"), { files: [] })
  assert.equal(resetRequest("don't reset the gate"), null)
  assert.equal(resetRequest("Do not reset the counter. Thanks"), null)
  assert.equal(resetRequest("we should never reset counters"), null)
  assert.equal(resetRequest("reset"), null)
  assert.equal(resetRequest("reset the form fields"), null)
})

test("isCheck: segments, flags and viewers", () => {
  const p = DEFAULT_SETTINGS.checkPatterns
  assert.equal(isCheck("cd x && python3 -B scripts/validate.py", p), true)
  assert.equal(isCheck("FOO=1 pytest -q", p), true)
  assert.equal(isCheck("npm test", p), true)
  assert.equal(isCheck("python3 -m unittest discover -s tests", p), true)
  assert.equal(isCheck("python3 -B scripts/test_sharded.py test_features", p), true)
  assert.equal(isCheck("rg pytest src", p), false)
  assert.equal(isCheck("cat scripts/validate.py", p), false)
  assert.equal(isCheck("git commit -m 'run pytest'", p), false)
  assert.equal(isCheck("ls | grep pytest", p), false)
  assert.equal(isCheck("npm install", p), false)
})

test("derived-shape and unknown sessions are ignored", () => {
  assert.equal(derivedSession(DERIVED), true)
  assert.equal(derivedSession(ROOT), false)
  const g = gate()
  g.created(DERIVED, null)
  g.created("ses_under", DERIVED)
  assert.equal(g.knows(DERIVED), false)
  assert.equal(g.knows("ses_under"), false)
  trip(g, [FA], DERIVED)
  assert.equal(effect(g, DERIVED), "allow")
  assert.equal(effect(g, "ses_unknown"), "allow")
  assert.equal(g.isRoot(ROOT), true)
  assert.equal(g.isRoot("ses_unknown"), false)
  assert.deepEqual(held(g), [])
})

test("parseSettings: defaults, valid values, invalid returns null", () => {
  assert.deepEqual(parseSettings({}), DEFAULT_SETTINGS)
  assert.deepEqual(parseSettings({ check_patterns: ["make lint"], ladder: ["p/m", "q/n"] }),
    { checkPatterns: ["make lint"], ladder: ["p/m", "q/n"] })
  assert.equal(parseSettings(null), null)
  assert.equal(parseSettings({ ladder: "p/m" }), null)
  assert.equal(parseSettings({ ladder: ["no-slash"] }), null)
  assert.equal(parseSettings({ check_patterns: [1] }), null)
  assert.equal(parseSettings({ check_patterns: [""] }), null)
})

test("final review: mixed files deny when any held file still needs a diagnosis", () => {
  const g = gate(R("m", "max"))
  trip(g, [FA])
  diagnose(g, R("m", "max"))  // re-evaluation accepted for a.ts
  retrip(g, [FA])  // a.ts now at stop
  trip(g, [FB])  // b.ts held at re-evaluation
  assert.equal(effect(g, ROOT, [FA]), "ask")
  assert.equal(effect(g, ROOT, [FA, FB]), "deny")
})

test("final review: recovered sessions are not fresh diagnoses until their creation is seen", () => {
  const g = gate()
  trip(g)
  g.created("ses_old", ROOT, true)  // an existing child first seen through a lookup, after the hold
  g.routed("ses_old", R("m", "high"))
  g.finished("ses_old")
  assert.equal(effect(g), "deny")
  // A creation event for a session first seen through a lookup proves it is new.
  g.created("ses_new", ROOT, true)
  g.created("ses_new", ROOT)
  g.routed("ses_new", R("m", "high"))
  g.finished("ses_new")
  assert.equal(effect(g), "allow")
})

test("final review: a check must be what the command runs", () => {
  const p = DEFAULT_SETTINGS.checkPatterns
  assert.equal(isCheck("env rg pytest src", p), false)
  assert.equal(isCheck("test -f scripts/validate.py", p), false)
  assert.equal(isCheck("rg 'foo && pytest bar' src", p), false)
  assert.equal(isCheck("echo \"unbalanced && pytest", p), false)
  assert.equal(isCheck("time npm test", p), true)
  assert.equal(isCheck("uv run pytest -q", p), true)
  assert.equal(isCheck("env CI=1 python3 -m unittest", p), true)
})
