# Smart Router: narrowing without pinning, and reviewer independence

Date: 24 November 2026. Status: decided by the user after the council
`docs/vibe-council/sessions/08d7b6126f45_router-constraints/result.md`; this short
design records the decisions for the plan.

## Intent

Smart Router only advises, so it cannot stop a coordinator from choosing its own
model. It can keep steering visible: a coordinator may narrow a pick ("not this
model", "this reviews X's work") inside the router, where the level bar, feedback
cards and future plan balancing still apply. Pinning one model or one plan belongs
outside the router. Two coordinators asked for a reviewer from a different lab
than the builder or author; one used `excluded_models` with formats that matched
nothing, silently.

## Goals

- `reviewing`: a review can name the work's author; the router prefers a
  qualifying reviewer from another lab.
- `excluded_models` accepts every format a coordinator has (a pick's `model_ref`,
  a catalog key, a route name, a bare model name) and reports entries that matched
  nothing.
- Remove the constraint fields nothing needs: `allowed_models`,
  `allowed_efforts`, `allowed_harnesses`.

## Non-goals and anti-goals

- Cards for models outside the offered picks (deferred, council C5).
- Constraints that persist across requests (council C6: cards are the lasting
  channel).
- Anti-goal: independence that lowers quality below the task's level.
- Anti-goal: any new model call, polling or stored state.

## Approaches

- **Narrow, do not pin (chosen by the council and the user).** Keep
  `excluded_models`, add `reviewing`, delete the three `allowed_*` fields.
- Keep all fields but stop advertising some: rejected; the schema refuses unlisted
  fields, and fields nothing uses should not be kept.
- Keep all four as advertised filters: rejected; `allowed_harnesses` would bypass
  future plan balancing and `allowed_models` is a pin.

## Decided

1. **Delete `allowed_models`, `allowed_efforts`, `allowed_harnesses`** from the
   input schema, `routing.CONSTRAINT_FIELDS`, `routing.check_constraints` and
   `selector._constrained`. A request carrying them is `invalid_request`. The
   injection check (`scripts/live_check.py`, `pick_for_path`) passes only that
   plan's models in `models` instead of `allowed_harnesses`.
2. **`excluded_models`** matches a route when an entry equals any of: the route's
   model name; its `provider/model` with any `#variant` removed (so a pick's
   `model_ref` works); its catalog key (`pool['keys']`); its route name. Entries
   that match no runnable route (before any other filter) are returned in the
   answer as `unmatched: [...]` (only when non-empty), never blocking.
3. **`reviewing`**: a new optional top-level request field, a string `model_ref`
   (`provider/model` or `provider/model#variant`). The router resolves its lab
   from the catalog: from the request's `models` list when the provider and model
   match a runnable route (as `rater_id` does), otherwise from the id
   (`_model_identity`). Labs are canonical (a leading `~` stripped).
   - Among the qualifying ordered candidates, those from a different lab come
     first; the pick and alternatives are taken from them when any exist.
   - If none from another lab qualifies, or the lab is unknown, the normal pick is
     returned. The answer carries `reviewer_independent: true | false` whenever
     `reviewing` is given (false also for an unknown lab).
   - The plugin adds a notice once per session when it is false: "vibe-workflow:
     No reviewer from a different lab than the reviewed work reaches this task's
     level; Smart Router recommended the usual pick." (no model names).
   - It applies to any work kind, but the descriptions present it for reviews
     (`review`, and `planning` for design and plan reviews).
4. **The below-level notice** is reworded to name constraints as a possible
   cause: "vibe-workflow: Smart Router could not confirm a level N match for this
   request and recommended the strongest available; excluded models may be the
   cause. Tell your human partner."
5. **Below-level picks are not remembered** for cards: they still get a `pick_id`
   and hint, and a card for them returns `ignored`, so a forced weak pick cannot
   lower a model at its real level.
6. **Text:** the property descriptions carry the guidance (the tool description is
   near its 2048-character limit): `excluded_models` "Models to leave out for this
   request only: a pick's model_ref, a catalog key or a model name."; `reviewing`
   "For a review: the model_ref of the model whose work is reviewed; the router
   prefers a reviewer from another lab." No model names.

## Release

A minor release (0.11.0): removing three request fields is a breaking change for
any caller using them; they were first advertised in 0.10.0. The release notes say
so.

## Left to the builder

Exact refusal and test fixtures; where the lab resolution helper lives in
`runtime/selector.py` (reuse `_entry_identity` and `_model_identity`).
