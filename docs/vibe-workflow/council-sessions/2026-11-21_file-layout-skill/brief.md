# Council brief: a file-and-folder layout skill for vibe-workflow

## Role
You are one of 3 council members (Fable, Astra, Opus). Answer independently. You cannot
see the other members' answers.

## The user's standing instructions
> I want to introduce a skill to our `vibe-workflow` plugin that helps agents to keep files and folders organized. Well put among "modules" or "domains". And I mean it for all folders, including:
>   . our .js files in the project root.
>   . tests
>   . runtime and scripts (does it make sense to keep them separate?)
>   . docs
>
> As a reference, I found this skill. I want to use it as a baseline for our new skill, which should be focused on coding/developing projects only.
>
> https://github.com/ComposioHQ/awesome-claude-skills/blob/master/file-organizer/SKILL.md

> What prompted me to bring that up is that, if I look at the project right now, the JS files are scattered in the root folder. I believe they should live inside the source folder or something like that. When I look at the runtime folder, there's no organization whatsoever. The same goes for all the photos throughout the project.
>
> I'd like to see the files well structured and organized, even though it's the beginning of a project. We could organize the scenes from the start and make the project more scalable as we design.
>
> That's why I had the idea to bring a skill into our plugin

> Exactly. I want to avoid a big refactor in the future, white project is still young.
>
> But before deciding whether I move on with this skill, I want to hear from @vibe-council.
>
> I definitely want this skill to respect current state, but immediately providing agents with a structure examples as reference.

(The coordinator reads "photos" as "the other folders" and "scenes" as "structure".)

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
`vibe-workflow` is an OpenCode V2 plugin (JavaScript ES modules plus a Python 3.11+
standard-library runtime, no dependencies, no build). It ships skills under
`skills/<id>/SKILL.md` (short entry) plus `references/*.md` (the procedure). Read
`skills/vibe-pull-requests/SKILL.md` and `skills/vibe-council/SKILL.md` for the house
style, `onboarding.js` (`registerSkill`) and `index.js` lines 235-245 for how skills are
registered, `scripts/manifest.py` for the exact export list, `scripts/validate.py` for
package rules, `package.json` `files`, and `PRODUCT.md` (principle: partner with
existing skills before adding overlapping behavior). `pr-advice.js` `SKILL_NOTES` shows
how the plugin can append a short note when another skill (e.g. `writing-plans`) loads.

Baseline reference: the Composio `file-organizer` skill (URL above). It targets personal
folders: Downloads, photos by date, duplicates by hash, archiving, deletion. Its useful
core is the flow analyze → propose plan → user approves → move with a log → summarize.

Overlap: superpowers `writing-plans` already says "Files that change together should
live together. Split by responsibility, not by technical layer." and "Each file should
have one clear responsibility." Superpowers `safe-refactor` covers behavior-preserving
moves. Neither gives agents a concrete project layout or tells them where a new file
goes in an existing tree.

Current repo layout: 7 `.js` modules in the root next to `package.json`/README;
`runtime/` holds 16 flat Python modules (account readers `claude_usage`,
`opencode_account`, `codex_reset`, `chatgpt_limit`; routing `routing`, `routing_paths`,
`routing_observations`, `scores`, `selector`; plus `storage`, `processes`, `continuity`,
`native_catalog`, `subscription_billing`, `council`); `scripts/` mixes the one shipped
entry point `select.py` with dev-only `validate.py`, `package.py`, `manifest.py`,
`live_check.py`; `tests/` is flat (28 files, JS and Python mixed); `docs/` has 5 topic
files plus `docs/superpowers/{specs,plans}` (owned by the superpowers workflow) and
`docs/vibe-council/sessions/`.

## Settled rules (not up for a vote)
- The skill targets coding/development projects only.
- The skill respects a project's current state: it learns the existing pattern and keeps
  new files consistent with it, rather than pushing one preferred layout.
- The skill gives agents concrete structure examples as reference from the start.
- No file is moved, renamed or deleted without a plan the user approved.
- vibe-workflow repo rules: smallest sufficient change, stdlib only, failing test before
  nontrivial behavior changes, independent review for nontrivial work, Python modules
  stay under 1000 lines.

## Measured facts
- Root `.js` modules: 7. Each root module name appears in 7 to 13 non-spec files
  (`grep -rl "<name>.js"` over js/py/json/md, excluding `docs/superpowers`).
- Files referencing `runtime` by import or path: 23 (`grep -rlE 'from runtime|import runtime|runtime/'`).
- `scripts/validate.py` rejects relative imports and allows only single-level
  `runtime.<name>` imports listed in `manifest.RUNTIME`; nested Python subpackages under
  `runtime/` would require changing it and `manifest.py`.
- `onboarding.js` resolves skills by `new URL("./skills/<id>/SKILL.md", import.meta.url)`,
  so moving `onboarding.js` into `src/` changes that relative path.
- 2 JS test files copy root modules by name into fixtures (`tests/plugin.test.js`,
  `tests/onboarding.test.js`).
- Docs top level: 5 files; `docs/superpowers` holds 30 specs and plans.

## Items
- I1 — Ship this as a new plugin skill rather than only a "Layout" section in each
  project's AGENTS.md or reliance on `writing-plans`. Direction: neutral. If wrong:
  overlapping behavior and skill sprawl against the partnership principle.
- I2 — Two jobs in one skill: (a) placement, whenever an agent creates a file or folder,
  put it where the project's layout says and flag drift; (b) restructure on request:
  analyze → plan → approve → `git mv` → fix imports, manifests and package lists → run
  tests → summarize. Direction: neutral. If wrong: one job is unused or the skill bloats.
- I3 — Domain-first default (group by feature/module, e.g. `src/council/`), layers only
  inside a domain; tests mirror the source tree. Direction: stricter. If wrong: agents
  fight common framework conventions.
- I4 — Ship examples as `references/layouts.md` with a few short trees (Node/TS app,
  Python package, mixed JS+Python plugin like this repo, monorepo), each marked
  "example, not rule", and defer to ecosystem conventions (Node `src/`, Python
  `src/<pkg>/` or package dir, framework-owned folders such as Next.js `app/`).
  Direction: neutral. If wrong: examples read as mandates or go stale.
- I5 — Record the project's chosen layout in a short "Layout" section of the project's
  `AGENTS.md` (created or extended with approval), so later agents and sessions follow
  it without re-deriving it. Direction: stricter. If wrong: duplicated or stale layout
  notes.
- I6 — Split heuristics for the placement job: propose a folder when 3+ files share a
  domain, or a folder passes about 10 files from 2+ domains; never create a folder for
  one file. Direction: stricter. If wrong: folder churn or still-flat trees.
- I7 — No plugin hook or onboarding question at first: the skill triggers by its
  description only. A `SKILL_NOTES` note on `writing-plans` pointing to the skill is
  deferred until live use shows agents skip it. Direction: looser. If wrong: the skill
  never fires during normal feature work, which is where drift happens.
- I8 — Drop from the Composio baseline: duplicate hunting, date folders, archiving,
  deletion, Downloads/photos material. Keep: the analyze/plan/approve/move/summarize
  flow, the move log, and "stop and ask on surprises". Direction: neutral. If wrong:
  lose useful cleanup (e.g. dead files).
- I9 — Keep `runtime/` (import-only library that ships) and `scripts/` (command entry
  points) separate; move dev-only tools (`validate`, `package`, `manifest`,
  `live_check`) out of `scripts/` into a dev folder (e.g. `dev/` or `tools/`), leaving
  only shipped entry points. Direction: neutral. If wrong: churn for little clarity.
- I10 — This repo's restructure is a separate PR after the skill merges, driven by the
  skill as its first real test: root `.js` → `src/<domain>/`, `runtime/` → domain
  subpackages (`accounts/`, `routing/`), tests mirrored, `docs/superpowers` and
  `docs/vibe-council` left as is. Direction: optimistic. If wrong: a large risky move
  with many path references (see measured facts) and little user value.
- I11 — Python nesting for `runtime/`: real subpackages (`runtime/accounts/claude_usage.py`,
  needs `validate.py`/`manifest.py` changes) rather than flat prefixed names
  (`runtime/accounts_claude_usage.py`). Direction: stricter. If wrong: validation and
  import churn for 16 modules, or flat names that keep the mess.
- I12 — Skill id `vibe-project-layout`. Direction: neutral. If wrong: unclear trigger or
  name clash with other skills.

## Output format (strict)
Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: secrets, credentials and private data; `.env*` files; anything under
  `~/.config`, `~/.local/share/opencode` outside this repository, `~/.claude*`,
  `~/.codex`, `~/ai-memory-vault`; and `docs/vibe-council/`.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
