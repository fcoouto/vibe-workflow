**Items**

C1: CHANGE: keep all four fields as hard filters, but advertise only `excluded_models` and `allowed_efforts` — The router is advisory, so a filter adds no new way to bypass it. A coordinator can already dispatch anything. Filters only decide whether the router sees the bypass.

C2: KEEP — No coordinator has asked to pin a plan. The one real attempt (`["openai"]`) used a string that matched nothing. Pinning a harness is exactly what would defeat balancing later.

C3: CHANGE: remove only `allowed_models` from the advertised schema; keep `excluded_models` documented — "Not this model" is the only way to correct a stalled model within a session, because cards need 3 sessions. An allow-list is a pin, and a coordinator can pin by dispatching directly.

C4: KEEP — Two coordinators asked for it on their own, and it covers the main real use of `excluded_models`. The level bar still applies, so quality cannot fall below the task's level, only cost can rise. It takes a `model_ref`, so no model is named in rules.

C5: CHANGE: accept a card for any routable model dispatched under that `pick_id`, at half weight when it was not offered — The router saw only 4 of 14 builder dispatches. Half weight limits a coordinator's own preferences, on top of the ±10% cap and the 3-session minimum.

C6: KEEP — Correction within a session already exists through `excluded_models` on the next request. Constraints that carried over between requests would add state to the hot path.

C7: CHANGE: add a notice whenever constraints changed the pick, i.e. the pick with no constraints, from the same pool, would differ — This catches "exclude everything else to force a pick", the case below the level and the case of a removed lab, with one rule. It needs no extra I/O or model call.

C8: KEEP — An entry that matches nothing currently fails silently. A `model_ref` in `excluded_models` and `allowed_harnesses: ["openai"]` both failed this way in real use. Return them as `unmatched: [...]` without blocking.

**Proposal**

Being advisory means steering cannot be prevented; it can only be made visible. So the goal is not to stop coordinators but to keep their steering inside the router. A steered request inside the router keeps three things: the level bar, a valid card and, later, plan balancing among whatever is left. A dispatch outside the router keeps none of them.

The design:
- Advertise the narrow tools: `excluded_models` to say "not this one" within a session, and `reviewing` for reviewer independence.
- Keep `allowed_models` and `allowed_harnesses` working but undocumented. Pins become a deliberate choice, not a habit.
- Report entries that matched nothing.
- Add a notice when constraints, not the router, chose the model, so the coordinator knows it overrode cheapest-first.
- Accept cards for overrides at half weight, so the 10-of-14 override pattern teaches the router something instead of nothing.

When balancing ships, it reorders plans among the routes the constraints left. A coordinator that excludes everything else gets its model, a notice and a valid card. That is the router's way out, and it adds no hot-path state, polling or model call. Cards stay the only lasting signal.

**Compounding**

- C3 and C6 depend on each other. Cards are slow (3 sessions, 30-day half-life), so a documented `excluded_models` is the only fast correction. Removing it would push coordinators back to overriding outside the router, where C5 is the only remedy.
- C5 and C7 together keep forced picks honest. A card on a constrained or off-pick model counts, but the notice and the half weight stop it passing as a real router pick. Without half weight, cards could carry one coordinator's lab preference into everyone's scores (pessimistic risk). Adopting C5 at full weight would be the result-flattering choice (optimistic risk).
- C4 lowers the need for C3. Most real `excluded_models` use was reviewer independence, so after C4 any remaining heavy exclusion is a sign worth a C7 notice.
- C2 protects future balancing. A harness pin is the one constraint that fixes a plan outright, and nobody has needed it.

**Top 3**

1. C7, as changed: one notice covers forced picks, picks below the level and lab removal.
2. C5, as changed: overrides are the larger part of real dispatches, and the router currently learns nothing from them.
3. C4: reviewer independence is the most requested steering, and it should not need model names.
