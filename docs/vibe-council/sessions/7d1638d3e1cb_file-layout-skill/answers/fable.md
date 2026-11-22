I1: KEEP — nothing existing tells an agent where a new file goes; keep it to one short `SKILL.md` plus references.
I2: CHANGE: keep both jobs, but restructure hands the moves to `safe-refactor` and `writing-plans` and adds only a layout checklist (imports, manifests, package lists, test fixtures) — avoids restating partner skills.
I3: CHANGE: domain-first only as a tiebreak when project and ecosystem are silent; test placement follows the ecosystem, not forced mirroring — otherwise it contradicts the settled "respect current state" rule.
I4: KEEP — cap at four short trees; the "example, not rule" marking is enough.
I5: KEEP — `AGENTS.md` loads every session, so it is the real placement mechanism; record rules, not a file listing.
I6: CHANGE: thresholds prompt one proposal at task end, never an automatic move; exempt convention-required folders from "never one file" — `skills/<id>/SKILL.md` is itself a one-file folder.
I7: KEEP — the Layout section from I5 points to the skill, so no hook is needed; an onboarding question breaks "keep setup short".
I8: KEEP — report suspected dead files as findings only, never delete.
I9: CHANGE: keep `runtime/` and `scripts/` separate, skip the dev-tools move, note the split in Layout — `scripts/manifest.py` and `package.json` `files` already enforce that only `scripts/select.py` ships.
I10: CHANGE: phase it: first PR moves six root modules to a flat `src/`, with `index.js` staying at root; runtime and tests come later, each in its own PR — `scripts/validate.py` pins `exports` to `./index.js`, and `src/<domain>/` for 7 files breaks I6.
I11: DEFER — real subpackages beat prefixed names, but 16 mostly small modules do not justify loosening the single-level import check yet; decide in I10's last phase.
I12: KEEP — fits the existing `vibe-*` ids; the description must name triggers (creating, moving, renaming files or folders; "organize", "restructure").

Compounding:
- I5 carries I7: without the `AGENTS.md` section, a description-only skill will not fire during feature work, and I1 loses most of its value.
- I3 and I6 as written contradict I10: strict domain-first plus "never one-file folder" cannot both hold for 7 root modules.
- I10 is flattered: the skill's author running it on the repo that inspired it is one sample and proves little. The move also touches `scripts/manifest.py`, `package.json` `files`, the `onboarding.js` skill URL, two test fixture lists and the path inventory in `docs/dependencies.md`.
- I9 and I11 are repo decisions, not skill design; tying them to the skill PR inflates scope against "smallest sufficient change".

Top 3: I5, I10, I3
