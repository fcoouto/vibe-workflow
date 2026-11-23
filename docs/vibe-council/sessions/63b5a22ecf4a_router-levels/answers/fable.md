Read the brief, routing code and evidence; council answer follows.

I1: DROP — own-best share is the wrong anchor for "good enough"; it moves with quota and releases. Keep it for levels 2–3 only.
I2: CHANGE: ratio 0.40 (24.6 today), capped at the user's own level-2 floor — 0.45 is fitted: Luna clears by 0.3, and one release at 62.1+ lifts the bar to 27.95 and drops Luna.
I3: DROP — tier detection is noisy, and a median excludes half the small class by construction.
I4: DROP — hard-wires two labs; breaks a settled rule.
I5: CHANGE: exact spec cancels only `3+`; `cross-system` still raises — evidence covers 3+ only: two task families, one repository, Haiku at 12 runs.
I6: KEEP — the format is not the fault; the anchor and the ordering are.
I7: CHANGE: cheapest-first inside each plan only; across plans, rank each plan's cheapest qualifier by score — list price compares quota within one plan, not across plans.
I8: KEEP — but this change delivers eligibility and fallback, not sharing.

**Proposal**

Two questions, two anchors. Tier 1 asks "good enough?", so it gets a public bar, the same for every user. Tiers 2–3 ask "near my best?", so they keep the user's own best.

It computes, all from the scores cache already loaded per request (no new fetch, call or state):
- `F` = median of the five highest catalog scores (61.4).
- `floor1 = min(0.40 × F, own level-2 floor)` = 24.6. The cap keeps tiers ordered on weak plans.
- `floor2`, `floor3` = 0.82 and 0.90 × best runnable; near clause unchanged.
- Level = `max(base, spec, risk, files)`, where `3+` does not count when spec is exact.
- Order: take each plan's cheapest qualifier (tie: higher score), rank those by score, then the rest by price.

The changes sit in `task_level` and `order` in `/Users/fcouto/.local/share/opencode/worktree/13464e/router-levels-council/runtime/routing.py` (lines 38 and 74).

Today, for this user:
- **Tier 1:** Haiku (Claude), Haiku (Go), Luna (ChatGPT). Luna is eligible and third.
- **Tier 2:** Sol, then Sonnet. Unchanged.
- **Tier 3:** Opus, then Sol. This is a change: today Sol wins on list price. The user should decide whether to accept it.

xAI plus DeepSeek: `floor1` is 24.6 instead of 19.5.
- **Tiers 1 and 2:** DeepSeek V4.1 Flash 32.6, then Grok 4.6 31.0, assuming each is its plan's cheapest qualifier.
- **Tier 3 (floor 32.0):** Grok 4.7, then DeepSeek V4.1 Flash.
- The tiers nearly merge, which is correct for a narrow score range.

New frontier model at 67: `F` moves to 62.1 and `floor1` to 24.8. Haiku and Luna stay; Luna survives until `F` passes 69.7. If the model is on the user's plans, floors 2–3 rise to 54.9 and 60.3, as intended.

Bias check:
- 24.6 is a stability margin, not evidence. It sits in the 20.1–26.7 gap of the listed scores.
- Below Haiku, the only evidence is Luna at 2 runs per condition. Its 4 refusals are still failed delegations.
- The margin admits MiMo 26.7 and GLM 27.8 where runnable. Strongest-first across plans keeps them from becoming the default.

**Compounding**
- I2 without I7 is unsafe. Once Go's cheap models get scored, the weakest admitted model wins every tier-1 task on a list-price gap of about $0.10. Ship both together, with a golden test.
- I5 moves multi-file plan work to tier 1, so tier-1 quality then rests on I2 and I7. It also puts all the weight on `spec`, the least checkable fact. Define `exact` in the rubric as a written plan naming files and tests.
- I7's one-per-plan shape is the slot where I8's pace reordering plugs in. A cheap first slice is "hot plan last", using the Claude and Go readings already taken per request. ChatGPT has no on-demand reading, so sharing toward Claude waits.

**Top 3**
1. I2 — public anchor with a real margin.
2. I7 — ordering; it decides whether a looser floor is safe.
3. I5 — moves the measured task class to tier 1.
