# Vibe Council Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `vibe-council` skill, the `/vibe-council` command and the `vibe_council_roster` tool in the `vibe-workflow` OpenCode plugin.

**Architecture:** The skill and its references carry the process as prose. A pure JavaScript roster function (`council.js`) matches seats against the host's live model list. A new Python operation (`select.py --council-roster` → `runtime/council.py`) reuses Smart Router's billing and quota checks through the existing `runRouter` spawn. Registration happens in `index.js` after the existing skills.

**Tech Stack:** JavaScript ES modules (Node `node:test`), Python 3.11 standard library (`unittest`), OpenCode V2 plugin context (`ctx.tool`, `ctx.skill`, `ctx.command`, `ctx.session.prompt`, `ctx.model.list`).

**Spec:** `docs/superpowers/specs/2026-11-21-vibe-council-design.md`

## Global Constraints

- Names: skill `vibe-council`, command `vibe-council`, tool `vibe_council_roster`, records `docs/vibe-council/sessions/<hash12>_<context>/`.
- Seats: 1 to 5, unique names (case-insensitive), `model` as `provider/id` split at the first `/`, `variant` null or a name.
- Plan providers are exactly the `provider` values in `config/routes.json`; any other provider is `provider_not_on_plan`.
- Seat statuses: `ok`, `unknown_model`, `unknown_variant`, `provider_not_on_plan`, `billing_unverified`, `quota_exhausted`, `unchecked`. `paid_overflow`: `blocked`, `enabled`, `unknown`, or null when the status is not `ok`.
- The tool never changes a seat's `model`, launches nothing, and always returns `admission`, `reservation` and `spend_permit` as `false`.
- The tool is registered regardless of the Smart Router project preference and never reads it.
- Python: standard library only, each module under 1000 lines, `runtime.` imports only from `scripts/manifest.py` `RUNTIME`.
- No machine paths in exported files (`validate.py` rejects `/Users/<name>/`).
- Do not run inference, install, publish or activate the plugin without the user's explicit authorization.
- Validate, commit, then run the four checks in `docs/development.md`.

## Review Focus

1. A seat on a paid duplicate (`opencode/gpt-6.2-astra`, `openrouter/openai/gpt-6.2-astra`) must be `provider_not_on_plan`, never matched to `openai/gpt-6.2-astra`. Test in Task 2.
2. The same model on two seats must yield two seat entries with the same status, with a single Python route. Test in Task 2.
3. A host with no model list (or an empty one) must give `ready: false`, `reason: "models_unavailable"`, every seat `unchecked`, and no spawn. Test in Task 2.
4. Python crash, timeout or malformed output must give `ready: false`, `reason: "roster_unavailable"`, with matched seats `unchecked`, and no leaked stdout. Test in Task 4.
5. `/vibe-council` with no arguments must still send "Use the vibe-council skill." with no trailing space, and must not forward `mention` fields. Test in Task 4.

---

### Task 1: Python roster check

**Files:**
- Create: `runtime/council.py`
- Modify: `scripts/select.py` (add `--council-roster`)
- Modify: `scripts/manifest.py` (add `"council"` to `RUNTIME`)
- Test: `tests/test_council.py` (new), `tests/test_package.py` (CLI cases, expected export set)

**Interfaces:**
- Produces: `runtime.council.roster(request: dict, *, state_dir: Path, source=None, now: int | None = None) -> dict`
  - Request: `{"v": 1, "models": [<entry>, ...]}` with 1 to 5 entries, each in the `routing_paths.routes` model shape (`provider`, `id`, `model`, `family`, `variants`, `context`, `released`). Extra keys in the request → `ValueError("invalid_request")`. Any entry whose provider is not on the plan → `ValueError("invalid_request")`.
  - Answer: `{"state": "checked", "seats": [{"provider", "id", "status", "paid_overflow"}], "admission": False, "reservation": False, "spend_permit": False}`. Seats follow input order, with one per input entry, duplicates included.
- Produces: CLI `select.py --state-dir <abs> --council-roster`, which reads the request on stdin and prints the answer. On any error it prints `{"state":"failed"}`.

- [ ] **Step 1: Write failing tests in `tests/test_council.py`**

Reuse a `Facts` stub shaped like the one in `tests/test_selector.py`. Tests:
- `test_ok_seats_report_paid_overflow`: `paid_crossover` values `provider_blocked`, `provider_managed` and `unknown` on three paths give `ok` with `blocked`, `enabled` and `unknown`.
- `test_billing_unverified_and_quota_exhausted`: billing returning `None` gives `billing_unverified` with `paid_overflow` None. A path in `exhausted` gives `quota_exhausted`.
- `test_order_and_duplicates`: input `[openai/x, claude-agent/opus, openai/x]` gives three seats in that order, and `acquire_billing` is called once.
- `test_invalid_requests`: `v=2`, zero or six models, an extra key, or provider `opencode` each raise `ValueError`.
- `test_authority_fields_false`.

- [ ] **Step 2: Run and see them fail**

Run: `python3 -B -m unittest tests.test_council -v`. Expected: FAIL (`No module named 'runtime.council'`).

- [ ] **Step 3: Implement `roster` in `runtime/council.py`**

Do what `selector._select` does:
1. Dedupe routes from `routing_paths.routes(models)` by `route`.
2. Use `source` or `routing_observations.LocalSource(state_dir / 'router.json')`.
3. Call `source.acquire_billing(routes, now)`, then re-read the clock unless `now` was passed.
4. Call `routing_observations.eligible(routes, source, now)`.
5. Map each input entry back to its seat by `route`.

- [ ] **Step 4: Wire the CLI and manifest**

`select.py`: add a `--council-roster` flag that reads stdin within `MAX_BYTES`, calls `council.roster` and falls back to `dict(state="failed")`. `manifest.py`: add `"council"` to `RUNTIME`. In `tests/test_package.py`:
- add `"council"` to the expected runtime names;
- add `test_cli_council_roster_without_account_evidence`: one `openai` model gives `state: "checked"` with the seat `billing_unverified`;
- add `test_cli_council_roster_refuses_invalid_input`: bad JSON gives `{"state":"failed"}` without echoing the input.

- [ ] **Step 5: Run and see them pass**

Run: `python3 -B -m unittest tests.test_council tests.test_package -v`. Expected: all pass.

- [ ] **Step 6: Commit**

`git commit -m "feat: add the council roster check"`

### Task 2: JavaScript roster function

**Files:**
- Create: `council.js` (export `createRoster` only in this task)
- Test: `tests/council.test.js` (new)

**Interfaces:**
- Consumes: Task 1's request and answer shapes, through an injected `check`.
- Produces: `createRoster({ models, check, providers }) -> async (request, signal) -> answer`
  - `models: () => Promise<Array<{ provider, id, model, family, variants, context, released, name }>>` (the `models()` shape in `index.js` plus `name`).
  - `check: (entries, signal) => Promise<object>`. It receives the matched entries without `name` and resolves to Task 1's answer or `{ state: "failed" }`.
  - `providers: Set<string>`.
  - Answer: `{ ready: boolean, reason: null | "invalid_request" | "models_unavailable" | "roster_unavailable", seats: [{ seat, model, name, variant, status, paid_overflow, candidates }], admission: false, reservation: false, spend_permit: false }`.

Rules:
- **Validation.** Request keys are exactly `v` (1) and `seats` (1 to 5). Each seat has exactly the keys `seat`, `model` and `variant`:
  - `seat` matches `/^[A-Za-z][A-Za-z0-9 _-]{0,31}$/` and is unique case-insensitively;
  - `model` contains a `/` with text on both sides, at most 256 characters;
  - `variant` is null or matches `/^[a-z0-9_-]{1,32}$/`.

  On failure the answer is `{ ready: false, reason: "invalid_request", seats: [] }` plus the false authority fields.
- **Order of checks for each seat:**
  1. Provider not in `providers`: `provider_not_on_plan`.
  2. No listed entry with that exact `provider` and `id`: `unknown_model`.
  3. Variant set but not in the entry's `variants`: `unknown_variant`.
  4. Otherwise the seat is sent to `check`.
- **Candidates,** for `unknown_model` only:
  1. Take the last match of `/[a-z]+/g` in the lowercased seat id (`gpt-6.3-astra` gives `astra`).
  2. Keep listed models on the same provider whose lowercased `id` or `name` contains that word.
  3. Sort newest `released` first, keep at most 3, as `provider/id` strings.

  Otherwise `[]`.
- **Empty model list:** `models()` returning `[]` gives `reason: "models_unavailable"`. Every seat is `unchecked` and `check` is not called.
- **Check failure:** if `check` resolves to anything but `state: "checked"`, or to seats that don't line up with the request, the answer has `reason: "roster_unavailable"` and the sent seats become `unchecked`.
- `name` comes from the matched entry, otherwise null. `ready` is true only when every seat is `ok`.

- [ ] **Step 1: Write failing tests in `tests/council.test.js`**

Use a stub `check` that records its arguments. Tests:
- `"all ok seats are ready and keep their model ids"`;
- `"paid duplicates are provider_not_on_plan"` (Review Focus 1);
- `"unknown model lists same-line candidates newest first"`, where seat `openai/gpt-6.3-astra` with `gpt-6.2-astra` and `gpt-6.1-sol` listed gives `["openai/gpt-6.2-astra"]`;
- `"unknown variant is not sent to check"`;
- `"same model on two seats"` (Review Focus 2);
- `"empty model list"` (Review Focus 3);
- `"check failure leaves seats unchecked"`;
- `"invalid requests"`: 0 or 6 seats, duplicate names `Opus`/`opus`, `model` without `/`, an extra key;
- `"check receives entries without name"`.

- [ ] **Step 2: Run and see them fail**

Run: `node --test tests/council.test.js`. Expected: FAIL (module not found).

- [ ] **Step 3: Implement `createRoster` in `council.js`**

- [ ] **Step 4: Run and see them pass**

Run: `node --test tests/council.test.js`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat: match council seats against the live model list"`

### Task 3: Skill content

**Files:**
- Create: `skills/vibe-council/SKILL.md`, `skills/vibe-council/references/procedure.md`, `references/brief-template.md`, `references/record-template.md`
- Modify: `scripts/manifest.py` `FILES` (the four paths); `tests/test_package.py` expected set
- Test: `tests/council-skill.test.js` (new; reads files directly, needs no registration)

**Interfaces:**
- Produces: `SKILL.md` that matches `registerSkill`'s regex exactly: `---\nname: vibe-council\ndescription: <one line>\n---\n<body>`. Description: "Use when the user asks for the council or the LLM council, or to run the council on a set of decisions or review findings." The body is about 10 lines like `skills/vibe-pull-requests/SKILL.md` and links `references/procedure.md`.

The content follows the spec's "The process", "Brief template", "Session records" and "Failure handling" sections. Procedure decisions the spec leaves open, fixed here:
- **Folder steps:** write the brief to `docs/vibe-council/sessions/draft_<context>/brief.md`, run `shasum -a 256` on it, then rename the folder to `<hash12>_<context>`. The brief never contains its own folder path.
- **Answer file names:** `answers/<seat>.md` uses the lowercased seat name with spaces turned into `-`.
- **Dry run:** `record-template.md` ends with a worked example built from three invented member answers. It covers a unanimous item, a majority with the minority noted, a 1–1–1 split, a missing line shown as "no vote", and a CHANGE majority that differs on how. This is the spec's stubbed-member dry run, checked by reading.
- **Dispatch line,** verbatim: `subagent` with `agent: "explore"`, `model: "<provider/id>"` or `"<provider/id>#<variant>"`, prompt `Read <absolute brief path> and follow it exactly. Answer in your final message.`

- [ ] **Step 1: Write failing tests in `tests/council-skill.test.js`**

- The frontmatter matches the regex above.
- `procedure.md` contains each of these phrases:
  - `vibe_council_roster`;
  - `agent: "explore"`;
  - `#<variant>`;
  - `docs/vibe-council/sessions/`;
  - `shasum -a 256`;
  - `openai/gpt-6.2-astra`, `claude-agent/opus`, `claude-agent/claude-fable-5-2`;
  - `no vote`;
  - `did not vote`;
  - `Round 2`;
  - `worktree`;
  - `paid overflow`.
- `brief-template.md` contains each of these phrases:
  - `Answer independently`;
  - `optimistic`;
  - `KEEP | CHANGE`;
  - `Compounding`;
  - `Top 3`;
  - `500 words`;
  - `Forbidden paths`;
  - `15 tool calls`.
- `record-template.md` contains each of these phrases:
  - `SHA-256`;
  - `paid overflow`;
  - `User decision`.

- [ ] **Step 2: Run and see them fail**

Run: `node --test tests/council-skill.test.js`. Expected: FAIL (ENOENT).

- [ ] **Step 3: Write the four files**

- [ ] **Step 4: Run and see them pass**

Run: `node --test tests/council-skill.test.js && python3 -B -m unittest tests.test_package -v`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat: add the vibe-council skill"`

### Task 4: Plugin wiring (tool, skill, command)

**Files:**
- Modify: `council.js` (add `registerCouncil`)
- Modify: `index.js`:
  - `models()` also returns `name` (a `text()` value or null);
  - the `smart_router_select` request strips `name`;
  - `runRouter` gets an operation `"council"`;
  - `registerCouncil` is called after `registerSkill(ctx, "vibe-pull-requests")`.
- Test: `tests/plugin.test.js` and `tests/onboarding.test.js`:
  - tool list expectations;
  - the fixture file lists at `plugin.test.js:98` and `onboarding.test.js:349` gain `council.js` and `skills/vibe-council/SKILL.md`.
- Test: `tests/workspace-skill.test.js`, `tests/pull-request-skill.test.js` and `tests/onboarding.test.js:54` (the skill lists gain `vibe-council` fourth).
- Modify: `scripts/manifest.py` `FILES` (add `"council.js"`); `tests/test_package.py` expected export set.
- Test: `tests/test_package.py`. Its embedded node check expects tools `['smart_router_select', 'vibe_council_roster', 'vibe_workflow_onboard']` (sorted) and the fourth skill.

**Interfaces:**
- Consumes: `createRoster` (Task 2); `select.py --council-roster` (Task 1); `registerSkill` from `onboarding.js`.
- Produces: `registerCouncil(ctx, { roster }) -> Promise<void>`. It registers, in this order:
  1. the skill `vibe-council`;
  2. the tool `vibe_council_roster`, whose input schema mirrors Task 2's validation, with description "Check council seats before dispatch: exact model, plan provider, subscription billing, quota and paid overflow. Never substitutes a model and launches nothing. Follow the vibe-council skill.";
  3. when `typeof ctx.command?.transform === "function"`, the command `vibe-council`, with description "Run the council on a set of decisions." Its `execute({ sessionID, prompt, delivery })` calls `ctx.session.prompt({ ...prompt, sessionID, text, files, agents, skills, delivery })`:
     - `text` is `"Use the vibe-council skill."`, followed by a space and `prompt.text` when that is non-empty after trimming;
     - `mention` is stripped from each item of `files`, `agents` and `skills`, as `ponytail.mjs` does.
- `runRouter` `"council"` operation:
  - args `--council-roster`;
  - timeout 60000 ms;
  - fallback `{ state: "failed" }`;
  - accepts only `state === "checked"`, an array `seats` and all three authority fields `false`.
- `index.js` builds the roster with `createRoster({ models, providers, check: (entries, signal) => runRouter({ v: 1, models: entries }, { directory, stateDir: state, signal, operation: "council" }) })`. `models` is `ctx.model ? models() : async () => []`.

- [ ] **Step 1: Update the registration tests and add new ones (failing)**

New tests in `tests/plugin.test.js`:
- `"council roster spawns select.py with --council-roster and returns its seats"`: a fixture `select.py` asserts `"--council-roster" in sys.argv` and prints a `checked` answer;
- `"council roster hides malformed Python output"` (Review Focus 4): a `PRIVATE-SENTINEL` stdout gives `roster_unavailable` without the sentinel;
- `"council roster ignores the Smart Router preference"`: settings with `smart_router` off still reach the spawn;
- `"command forwards the skill line"` (Review Focus 5): with a stub `ctx.command`/`ctx.session.prompt`, empty text gives exactly `"Use the vibe-council skill."`, `"on R1-R9"` gives `"Use the vibe-council skill. on R1-R9"`, and no `mention` keys are forwarded;
- `"no command API"`: setup succeeds without `ctx.command`;
- `"smart_router_select request carries no name"`: the fixture `select.py` asserts that no model has `name`.

- [ ] **Step 2: Run and see them fail**

Run: `node --test tests/*.test.js`. Expected: the new and updated tests fail; the rest pass.

- [ ] **Step 3: Implement `registerCouncil` and the `index.js` changes**

- [ ] **Step 4: Run the full suite**

Run, in order:

```sh
python3 -B -m unittest discover -s tests
node --test tests/*.test.js
python3 -B scripts/validate.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat: register the council skill, tool and command"`

### Task 5: Documentation and export

**Files:**
- Modify: `README.md` (an "Ask the council" section), written through the `impeccable` skill as `AGENTS.md` requires
- Modify: `PRODUCT.md` (move the council into "Available now")

The README section covers:
- how to ask (skill phrase and `/vibe-council`);
- the default roster;
- that members get read-only `explore` agents;
- that seats are limited to plan providers;
- the token-free account reads;
- where records go.

At most about 12 lines.

- [ ] **Step 1: Write the README section through `impeccable` and the `PRODUCT.md` bullet**

- [ ] **Step 2: Run the four checks**

```sh
python3 -B -m unittest discover -s tests
node --test tests/*.test.js
python3 -B scripts/validate.py
python3 -B scripts/package.py --output <fresh dir>
python3 -B scripts/validate.py --root <fresh dir>
```

Expected: all pass, and the export contains `council.js`, `runtime/council.py` and the four skill files.

- [ ] **Step 3: Commit**

`git commit -m "docs: describe the council"`

- [ ] **Step 4: Live check (only with the user's explicit authorization)**

Load the fresh export in an isolated OpenCode 2.2.6 host, as `docs/development.md` records for Smart Router. Then:
1. Call `vibe_council_roster` with the default roster, and record the statuses.
2. Run `/vibe-council` once on a three-item toy brief.
3. Record what was observed in `docs/development.md`.

Without authorization, report this step as not run.
