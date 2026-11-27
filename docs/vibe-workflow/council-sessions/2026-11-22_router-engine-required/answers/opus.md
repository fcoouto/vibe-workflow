**Verdicts**

D1: DROP — `PRODUCT.md:80` promises a clear opt-out, and the user has only started considering removing it. Removing it also puts `ROUTER_NOTE` on every subagent step, against the "as lightweight as possible" rule. Q1 was also just left unchanged by the user's earlier choice.

D2: KEEP — this is mostly how things already work. The council roster runs "whether or not Smart Router is on" (README, `PRODUCT.md:44-51`), and onboarding readiness already calls the engine. Keep Q1's wording as it is. The opt-out removes model advice and the router note, not the engine.

D3: CHANGE: first find a token-free way for `scripts/live_check.py` to read OpenCode's model list; until then keep the recorded list and stop with "model unavailable" when a recorded model is missing — The selector needs `request['models']`, which only `ctx.model.list()` inside the plugin provides (`index.js:104`, `runtime/selector.py:95-100`). The check runs outside the plugin. Getting the list through an `opencode run` prompt would be an extra model call, which the standing rule forbids. When such a read exists, pick per path, record the picks, and have the shell-note probe use the `claude-agent` pick.

D4: KEEP — this is required if D3 is adopted. Selection can fail for reasons unrelated to OpenCode: a ChatGPT usage-limit pause, `scores_unavailable`, `unscored` or `account_changed` (`runtime/selector.py:109-156`). Calling those delivery failures would be false. Report each path separately, record nothing, and stop and tell the user.

D5: CHANGE: limit the pre-authorization to level 1, low effort, a subscription route with paid overflow blocked, and one prompt per path. Print the picks before running and record them; stop on any pick outside these bounds — Without these limits the check could spend on a model the user never named. Note that a level-1 pick is not always the cheapest: `gpt-6.1-terra` is priced at 2 against Haiku's 0.2.

**Compounding**
- D3 needs D4 and D5 together. Without D4 the update check fails on quota state. Without D5 the check still refuses any model outside `APPROVED_MODELS` (`scripts/live_check.py:181`).
- D3 makes the check depend on the selector. The selector had five bugs that only showed up live, and the 4-of-4 success today is a small sample (optimistic-bias flag). Because the check only detects problems and never records a version on failure, the cost is a blocked update, not a false pass (pessimistic-bias flag).
- D2 makes D1 unnecessary: internal tools get the engine either way, and users keep the choice.
- The user's "no hard-coding" goal is partly met already. `claude-agent/haiku` is an alias; only the other two names are pinned.

**Top 3**
1. D3: no token-free way exists today to give the check a model list.
2. D5: this widens what the user authorized for spending, so it needs firm limits.
3. D1: it reverses a product promise and cuts against the lightweight rule.
