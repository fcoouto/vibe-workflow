Status: reading done, writing verdicts now.

R1: KEEP — follows user decision: ship check on in every onboarded project.
R2: KEEP — matches read-only `explore` reviewer; one write path.
R3: KEEP — as lesson only; router pick first, override needs named reason. Work stands.
R4: KEEP — `SHIP_PR` gate is onboarding, not git workflow.
R5: KEEP — plan line 22 and `AGENTS.md` pre-authorize `--injection` when notes change; separate commit correct.
R6: KEEP — no effect.
R7: KEEP — listing observed; running the command stays unobserved, say so in PR.
R8: KEEP — user-confirmed; probe 5 rerun passed.
R9: KEEP — faithful: three files accept raw reads, name-only search, rotate-now at once. Minor gap: tier-2 reviewer brief not told to carry secret rules.
R10: KEEP — probe showed real pay-as-you-go pick; unavailable beats unapproved spend.
R11: KEEP — polling observed, wasteful.
R12: KEEP — independent review held; fix was small.
R13: KEEP — hand steps cannot do `O_EXCL`/identity checks; tested.
R14: KEEP — visibility can change later; one rule beats per-repo judgment.
R15: KEEP — heredoc injection was real; needs one live run.
R16: KEEP — no new reader; stated in `report-storage.md`.
R17: CHANGE: refuse only other-owner or world-writable; let group-writable pass — under umask `002` an existing `.vibe-workflow` is `775`, so every report is refused on default Ubuntu/Fedora. Owner checks, `dir_fd` opens, `0700` folder and identity check already bound a swap.
R18: KEEP — correct for any path byte.
R19: CHANGE: add fallback — if resume fails, dispatch a fresh reviewer for the remaining scope or record the review unavailable — resume is untested; text has no failure path.
R20: KEEP — repo-supplied skill is real injection vector.
R21: KEEP — tested; misfire cost is one note.
R22: KEEP — correct stop; user approved after.

Compounding
- R13+R15+R17+R19 are the whole post-fix path and none is observed live. "647/469 pass" flatters it: tests prove the script, not that an agent follows `report-storage.md`. Writing outside the repository may hit `external_directory` permission.
- R17 as written cancels the user's "reports live in the folder" decision on common Linux setups; R15's refusal path then makes chat the default, and R16's transcript copy becomes the only copy.
- R10+R19 both raise "review unavailable" odds; with the settled [BLOCKED] rule that stalls PRs. Honest outcome, but R19 needs its fallback so a failed resume is not read as a finished review.
- R8+R20: `check.py` uses `is_file()`, which follows links. Skill linked from outside both roots shows `ready`, then review silently uses built-in. Record skipped partner under limits.

Top 3
1. R17 — stricter rule defeats a user decision off macOS.
2. R19 — untested resume with no failure path; could pass an incomplete tier-3 review.
3. R15 (with R13) — one live probe of agent-driven storage before [READY].

Note: brief forbids `~/.local/share/opencode/`, but the worktree lives there. I read only the brief and the repository files it names.
