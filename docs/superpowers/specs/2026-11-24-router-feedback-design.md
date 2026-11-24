# Smart Router learns from feedback cards between models

Date: 24 November 2026. Status: approved by the user on 24 November 2026 after
two independent reviews (Fable and Astra, both "ready with changes") and a
filtering pass for a lightweight plugin. Items marked "coordinator's call" were
settled without the user and are listed for their check.

## Intent

Make Smart Router better the more it is used, and keep it right as new models
appear every week, without any human input. Today every pick comes from public
catalog scores and list prices, so the router repeats a bad pick as often as it
is asked. Coordinators already learn by hand ("Terra has stalled three times
today, so I'm taking its listed alternative") and lose the lesson when the
session ends. A coordinator on a trading project also reported that the router
cannot see task size: a long multi-file build and a one-hour plan task look the
same.

vibe-workflow stays a lightweight plugin: feedback is one part of Smart Router,
not a new subsystem.

## Goals

- Coordinators rate a finished subagent's work on an optional card, model to
  model, with no human in the loop.
- Cards adjust a model's effective strength per kind of work and task size, so a
  model can drop out of a level, or climb into one.
- New models start from their catalog score; new versions of a known family
  start with half of the family's record.
- The router can tell short tasks from long ones (2 agent-hours or more; 4 before 0.12.0), and
  gives long tasks more effort.

## Non-goals

- Human ratings; sharing cards across machines or users.
- Using the comment for ranking.
- Usage balancing across plans; the main session's model.

## Anti-goals

- A few cards, or one bad day, sinking a good model.
- A model rating itself.
- Feedback ever breaking a pick.
- Any model call, polling or background process. A card costs the rater one tool
  call and one short Python process, the same path a pick uses.

## Constraints

- Advisory only: no launch, admission, reservation or spend.
- Rules, notes and decisions speak in levels and facts, never model names.
- The `smart_router_select` description stays under 2048 characters.
- Python standard library; `runtime/` flat; modules within 1000 lines.
- `v: 2` stays compatible: every new field is optional.

## Approaches

- **Plugin tool plus the existing Python runtime (chosen).** A new
  `smart_router_feedback` tool in `index.js`, run through `runRouter` as a new
  `feedback` operation, writing one small file in Smart Router's state directory.
- A script raters run by hand: rejected; agents call tools, and a script needs
  shell permission and a path into the installed package.
- Inferring quality from overrides, stalls or re-dispatches: deferred; harder to
  attribute, and the user chose an explicit card.

## The decided design

### The pick

- Every `smart_router_select` answer carries a `pick_id` (random, opaque) and one
  line: rate the subagent's work with `smart_router_feedback` after it reports.
- The plugin keeps, per session in memory (bounded like the notice map), what
  each `pick_id` stood for: the pick and its alternatives, level, work kind,
  facts and size group. An unknown or forgotten `pick_id` (for example after a plugin
  restart) makes a card return `ignored`, never an error.
- Each pick shows `feedback: {sessions, adjustment}` for its model, so the
  coordinator sees why it moved.

### Task size and effort

- New optional fact `task.estimate_agentic_effort_hours`: a number from 0 to 100,
  the coordinator's estimate of how long the delegated agent will work on the
  task, in hours of agent time, not a person's time (the user's choice of field,
  so the cutoff is Smart Router's rule, not each agent's judgement).
- Threshold: 2 hours (the user's choice on 24 November 2026; it was 4 until 0.12.0). A missing estimate counts as below it.
- The estimate does not change the level. The text fix below sends whole plans
  to `prose`, which already raises them.
- Effort: at or above 2 hours, one effort step above the level's default (level 1
  medium to high, level 2 medium to high, level 3 high to xhigh), clamped to the
  model's variants as today.
- Cards are kept in two size groups, below and at or above 2 hours, so a failed
  long build does not lower a model's record on short tasks. Two groups only,
  because cards will be sparse.
- Deferred: moving the threshold from card evidence (where ratings drop).

### Text changes in the tool description

- `exact` = this request is one task of a written plan that names its files, its
  expected behavior and the checks that tell success from failure, and leaves
  the builder nothing to decide. Several plan tasks in one request, or any open
  choice, is `prose`. Unsure whether it is exact: say `prose`.
- One request per delegated task. Rate risk by what a mistake would cost, not by
  the task's size; give the size in `estimate_agentic_effort_hours`.
- The card scale: 0 = failed; 3 = met the stated checks; 5 = met them with no
  rework needed. Skip the card when the brief was at fault or the provider
  failed.
- `constraints` lists its four fields in the input schema (`allowed_models`,
  `excluded_models`, `allowed_efforts`, `allowed_harnesses`, each a list of
  strings), with no rule about when to use them.

### The card

- `smart_router_feedback` takes `pick_id`, `model_ref` (what was dispatched),
  `rating` 0-5 (integer) and an optional `comment` of at most 280 characters.
- The plugin fills in level, work kind, size and facts from the `pick_id`. The
  card counts only when `model_ref` is the pick or one of its alternatives.
- The rater's model is read from the rater's own session (`ctx.session.get`
  returns `model: {providerID, id}`), resolved to a catalog key and lab the same
  way picks are.
- A model never rates itself: same key, refused. A rater from the same lab as
  the rated model counts at half weight. An unknown rater counts at half weight.
- One vote per session, model and kind of work, enforced in the stored file; a
  repeat is refused.
- Comments are stored capped and are never echoed in any answer. Wording in docs:
  "Smart Router sends nothing"; a hosted coordinator's transcript still holds
  what it typed.

### Storage

- One file, `router-feedback.json`, under the existing lock helpers.
- Per key (model, kind of work, size group): two decayed running totals (weight, and
  weight × rating) and the set of sessions that contributed; plus a bounded set
  of recent vote keys. No card list, so nothing to prune.
- The whole file is bounded (well under 1 MiB). A missing, damaged or oversized
  file means no adjustment; picks fall back to catalog scores. Writes refuse when
  over the bound.

### How cards move a model

- Effective score for a model, kind of work and size group = catalog score +
  adjustment. The bars and cheapest-first order then use the effective score.
  The council's seat pick does not.
- Adjustment = clamp(cap × d × n / (n + 5), −cap, +cap), where cap = 10% of the
  anchor A (about 6 points today), n = the decayed weight, and d = (mean − 3) / 3
  when the mean is below 3, (mean − 3) / 2 when above, so both ends reach the
  cap.
- Weights halve every 30 days.
- No adjustment until the key has cards from at least 3 different sessions.
- A model's record also includes, at half weight, the cards of older versions in
  its family (same lab and family key). A new version starts with that half
  record on top of its catalog score.
- No borrowing across kinds of work.

### Unchanged

The anchor, the bars, level rules other than the text fix, cheapest-first order,
the near clause, `model_ref`, the council's seat rule, `ROUTER_NOTE` and
`MODEL_NOTE`.

## Deferred

- Keying cards by level or file count.
- Saving `pick_id`s across plugin restarts.
- Centring ratings on the machine-wide average; tracking how many picks get
  rated.
- Inferring quality without a card (overrides, stalls, re-dispatches).

## Verification

- Unit tests for the formula (both caps, decay, the 3-session rule, family half
  weight, self-rating refusal, same-lab and unknown-rater half weight, one vote,
  damaged file falls back), the size and effort rule, the text and schema.
- A live check: one fresh session dispatches a subagent and files a card; the
  card is stored with the rater's model, and a repeat is refused.

## Left to the builder

The file layout, refusal messages, test fixtures, and the exact wording of the
text changes within the character limit.

## Amendment, 24 November 2026 (release 0.12.0)

The user decided: the default effort by level is medium, medium, high (it was low,
medium, high), so Smart Router never recommends `low` when the model offers more;
the long-task threshold is 2 agent-hours (it was 4). The estimate stays in hours of
the delegated agent's wall-clock time, not a person's. Evidence: on a 14-task
statistics-heavy plan, level-1 builds at low effort passed cleanly on fully
specified tasks but needed two fix rounds each on two tasks needing judgment about
tests and test data; the advisor evaluation behind the exact-plan rule never set
low effort. Effort is still clamped to the model's variants; the level never
changes.

## Amendment, 24 November 2026 (release 0.13.0)

The user decided: implementation, testing, review, planning and investigation start
at high effort at every level; writing keeps medium, medium, high by level. The
step for tasks of 2 or more agent-hours stays (high becomes xhigh). Effort is still
clamped to the model's variants; callers without a work kind (council seats) follow
the level table.
