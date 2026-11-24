C1: CHANGE: keep hard filters, advertise only `excluded_models` — `allowed_efforts` is a trap: it filters on the level-computed effort and cannot raise it (`tests/runtime/test_selector.py:297` gives `no_selection`).
C2: KEEP — one harness per plan in `config/routes.json`, so this is exactly the plan pin balancing must reorder; nobody asked for it.
C3: CHANGE: hide `allowed_models` only — `excluded_models` has measured use and is the only in-session correction; `allowed_models` is the one-field pin with no measured use.
C4: KEEP — two coordinators asked for it; bar first, independence second.
C5: DEFER — flatters the result: cheapest-first (`runtime/routing.py:99-108`) makes a good card on a pricier qualifying model inert, and observed overrides went up.
C6: KEEP — no session state on the hot path; repeating `excluded_models` covers in-session failures.
C7: CHANGE: below-level case only, by rewording the existing below-level notice — the lab clause is noise once C4 removes a lab by design.
C8: KEEP — a silent no-match in `excluded_models` means a coordinator believes it has independence and does not.

**Proposal**

Narrow, do not pin. A coordinator can say "not this model" for one request, and for reviews name what it is reviewing. The router then picks the cheapest qualifying model among the rest. To get one exact model, the coordinator dispatches it directly; the router adds nothing to a decision already made.

- **Schema:** advertise `excluded_models` and `reviewing` only. The runtime keeps all four fields, because `scripts/live_check.py:256` calls `select.py` directly with `allowed_harnesses`.
- **"Accepted, undocumented" is optimistic:** `constraints` has `additionalProperties: false` (`index.js:107`), so the tool will probably reject hidden fields. Say "runtime only" instead.
- **`reviewing` wording:** put it on the property description. The tool description is about 1,640 of 2,048 characters by my rough count.
- **Lab resolution:** resolve the lab the way cards already do (`_entry_identity`, `_model_identity`), so no new call.
- **Future picks:** cards only. Overrides stay unrated until data shows a cheaper override that worked, the one case where a card would change order.

One measured fact looks off. When constraints remove every candidate, the code returns `no_recommendation`, not `no_eligible_route` (`runtime/selector.py:160-162`). Either way the notice does not name the entry that matched nothing, so C8 still stands.

**Compounding**

- **Back door to C5:** a constraint that leaves one model makes it "offered", so its card passes `not_offered` (`index.js:359`). Hiding `allowed_models` adds friction but does not close it.
- **Mis-levelled cards:** cards are keyed by kind and size, not level. A forced below-level pick that fails files a 0 that lowers the model at its real level. The simplest guard is not to remember below-level picks (`index.js:331`).
- **Balancing:** an override outside the router is invisible to cards and to any future per-plan recommendation count. In-router exclusion keeps both, so C3 as written would push steering out of sight.
- **C4 shrinks the rest:** it removes the measured reason for `excluded_models` on reviews and for the `allowed_harnesses` attempt. What remains for `excluded_models` is "this model stalled, not now".

**Top 3**

C4, C2, C8.
