I12: KEEP — the name is clear, and it fits next to `vibe-onboard-project` without clashing.

I1: CHANGE: ship the skill, but point to `writing-plans` for the principles and to `safe-refactor` for behavior-preserving moves instead of restating them — no partner gives a concrete layout or says where a new file goes, and pointing to partners follows PRODUCT.md's partnership rule.
I2: KEEP — placement is the frequent, high-value job, and restructure is the occasional one. The move log and approval step tie the two together. Hand move mechanics to `safe-refactor` when it is present.
I3: CHANGE: use domain-first only when a project has no clear pattern or is new; tests follow the existing convention (mirrored, colocated or flat) — as written it pushes one preferred layout, which breaks the settled "respect current state" rule.
I4: KEEP — three or four short trees marked "example, not rule" are cheap. They must include examples of convention-owned folders (Next.js `app/`, Python `tests/`) so the domain-first default does not read as a mandate.
I5: CHANGE: record the placement rules (where new X goes) in at most about 10 lines, not a full tree, in the project's existing agent instructions file, and update it in the same PR as any move — trees go stale, while rules stay cheap to keep current.
I6: CHANGE: phrase the numbers as hints that trigger a proposal, never an action, and allow a one-file folder when the ecosystem requires one (a package directory) — the exact thresholds give a false sense of precision.
I7: CHANGE: add the `writing-plans` `SKILL_NOTES` entry now — plans fix the file structure, so that is where drift starts. The cost is one entry plus a test using an existing mechanism. "Defer until live use shows agents skip it" has no way to measure that, so it would never be revisited.
I8: KEEP — the plan's "files needing your decision" list already lets dead files come up without ever deleting anything. Everything else in the baseline is personal-folder material.
I9: DEFER — decide this in I10's plan. `manifest.py` and `package.json` already fence off the one shipped `scripts/select.py`. Moving four dev tools churns `AGENTS.md` and docs commands plus the package and `live_check` tests for little clarity. If anything moves, move the single shipped entry point.
I10: CHANGE: keep this as a separate PR after the skill, but let the skill's own analyze → plan step set the scope; prefer a flat `src/` over `src/<domain>/` — each of the 7 root modules is already one domain, so `src/<domain>/` creates single-file folders that I6 forbids. The `onboarding.js` skill URL and the 2 fixture-copying tests are known break points. The original scope is optimistic.
I11: DEFER — decide this inside I10's plan. If `runtime/` is grouped, use real subpackages, because prefixed names are still a flat folder under another name. The 16 modules (2,445 lines) and 23 references do not justify a `validate.py`/`manifest.py` change before the skill exists.

**Compounding**
- I5 and I7 cover each other. The Layout section loads every session but exists only after the skill has run once. The `writing-plans` note covers projects without one, so dropping either leaves a gap.
- I3, I6 and I10 conflict as written: domain-first plus "no single-file folder" rules out `src/<domain>/` for 7 one-module domains. A flat `src/` satisfies both rules.
- I9, I10 and I11 are all decisions about this repo. Putting them in the skill PR inflates its scope and its review. Keep them in one approved restructure plan, checked against the measured references.
- I4 is what keeps I3 honest. Without convention-owned examples, agents read the domain-first default as a rule.

**Top 3**
1. I7 — if the skill never fires during feature work, the placement job is dead weight.
2. I3 — this is where the skill either respects the current state or quietly pushes one layout.
3. I10 — this is where the risk is: many path references, packaging boundaries and fixtures, for little user value unless the scope is cut.
