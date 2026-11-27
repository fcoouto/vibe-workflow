D1: DROP — you said "required engine", which is D2. Removing the opt-out breaks the `PRODUCT.md` promise and your same-day "don't change Question 1". Projects without the three plans would get only notices.
D2: KEEP — already true: council and readiness run the engine with Smart Router off, and the README requires Python and Git for everyone. Keep "Smart Router" as the name of the per-project toggle only; coin no user-facing "engine".
D3: CHANGE: reuse the recorded model per path; ask the selector only when that model is no longer listed — the selector needs the host's model list, which `live_check.py` lacks, and per-path picks are unobserved.
D4: KEEP — the selector broke exactly at a host change before (the envelope `no_models` bug). Without a separate outcome, an agent would report a selector fault as lost delivery.
D5: CHANGE: authorize "one level-1 pick per path, three providers only, four prompts in total"; keep refusing other providers and any hand-edited record — spend is small and subscription-only, but the wording is your decision.

Compounding:
- D2 enables D3, and D3 forces D4 and D5; D1 is independent, and dropping it costs D2–D5 nothing.
- D3 puts the selector inside the check that detects OpenCode breakage, so both can fail at the same update. The sticky pick limits "could not select" to the day a recorded model disappears.
- Sticky picks also shrink D5: the model changes only when one is retired, and the record shows the new name in a tracked diff.
- Always re-picking would let a weekly price refresh swap in a model never tried on the quote prompt, blocking the version record for a non-OpenCode cause.

Bias flags:
- Optimistic: "the selector's existing `allowed_harnesses` constraint" understates D3. `select` requires `models` from `ctx.model.list()` (`index.js:104`), and the script runs outside the host. It needs a new OpenCode listing surface, which I could not verify (no `opencode` commands allowed) and which adds one more thing an update can break.
- Optimistic: the live level-1 result was one global pick with two alternatives, not three per-path picks. The score floor is 0.55 × the pool's best score (`runtime/routing.py:75`), so a single-path pool has a different floor. Go or ChatGPT could pick a cheaper model than the recorded one.
- Optimistic: five answered calls on one day is a small reliability sample, set against five bugs found only live.
- Pessimistic: D5's spend risk is overstated at four short prompts on the cheapest eligible models. The real cost of hard-coding is also small: `claude-agent/haiku` is an alias, and only two names can go stale.
- Pessimistic: D1's "every project pays the engine's requirements" is already true today. Its real cost is the `ROUTER_NOTE` asking for a `smart_router_select` consult before each unnamed delegation, against your "as lightweight as possible" rule.

Top 3:
1. D3 — prove the script can get the host's model list without inference before planning it; otherwise keep the names.
2. D1 — keep the opt-out; D2 delivers the "required engine" idea.
3. D5 — the standing authorization in `AGENTS.md` changes only by your word.

Files read:
- `/Users/fcouto/.local/share/opencode/worktree/13464e/council-router-engine/scripts/live_check.py`
- `/Users/fcouto/.local/share/opencode/worktree/13464e/council-router-engine/runtime/selector.py`
- `/Users/fcouto/.local/share/opencode/worktree/13464e/council-router-engine/runtime/routing.py`
- `/Users/fcouto/.local/share/opencode/worktree/13464e/council-router-engine/index.js`
- `/Users/fcouto/.local/share/opencode/worktree/13464e/council-router-engine/config/routes.json`
