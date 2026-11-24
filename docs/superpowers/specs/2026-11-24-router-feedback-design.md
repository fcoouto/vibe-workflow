# Smart Router learns from feedback cards between models

Date: 24 November 2026. Status: draft, under independent review; not yet
approved by the user.

## Intent

Make Smart Router better the more it is used, and keep it right as new models
appear every week, without any human input. Today every pick comes from public
catalog scores and list prices, so the router repeats a bad pick as often as it
is asked. Coordinators already learn by hand ("Terra has stalled three times
today, so I'm taking its listed alternative") and lose the lesson when the
session ends.

Evidence that prompted this: a coordinator using 0.9.x on a trading project
reported a level-1 pick for a 10-14 hour multi-file build, a reviewer picked
from the same model that did the build, and a mid-level model stopping to ask
on long builds. A second coordinator overrode a pick because that model had
stalled three times that day on long multi-file documentation work.

## Goals

- Coordinators rate a finished subagent's work on an optional card, model to
  model, with no human in the loop.
- Ratings adjust each model's effective strength per kind of work, so a model
  can drop out of a level, or climb into one, for work it keeps failing or
  handling well.
- New models start from their catalog score; new versions of a known family
  start with part of the family's record.

## Non-goals

- User ratings or any human input (the user's decision).
- Sharing ratings across machines or users; nothing leaves the machine.
- Using the free-text comment for ranking.
- Usage balancing across plans; the main session's model.

## Anti-goals

- A few bad cards sinking a good model.
- A model rating itself, or a lab grading its own models up.
- Any model call, polling or background process (the user's lightweight rule).
  The card costs the rater one tool call; nothing else.

## Constraints

- Smart Router stays advisory: no launch, admission, reservation or spend.
- Rules, notes and decisions speak in levels and facts, never model names.
- The `smart_router_select` description stays under 2048 characters.
- Python standard library; `runtime/` stays flat; modules within 1000 lines.
- The `v: 2` selection contract stays compatible; new answer fields are
  additions.

## Approaches

- **Plugin tool plus the existing Python runtime (chosen, one sensible way).**
  A new `smart_router_feedback` tool in `index.js`, handled by the same
  short-lived Python process the plugin already runs for selection
  (`runRouter`, a new `feedback` operation), writing a small file next to the
  existing scores cache.
- A script raters run by hand: rejected, agents call tools, and a script needs
  shell permission and a path into the installed package.
- Inferring quality from overrides, stalls or re-dispatches without a card:
  deferred; harder to attribute to the model, and the user chose an explicit
  card.

## The decided design

### The card

- Each `smart_router_select` answer gets a `pick_id` (random, opaque). The
  plugin remembers, per session, which model, level, work kind and facts each
  `pick_id` stood for, for the session's lifetime (bounded like the notice map).
- The rater calls `smart_router_feedback` with `pick_id`, the `model_ref` it
  actually dispatched (it may be an alternative), `rating` 0-5 (integer), and
  an optional `comment` (at most 280 characters). The plugin fills in level,
  work kind and facts from the `pick_id`, so the rater cannot misreport them.
- Scale anchors, in the tool description: 0 failed or unusable; 3 did the job
  as asked; 5 exceptional, did more than asked.
- One card per session, model and kind of work (the user's rule). A repeat is
  refused with a short message.
- A model never rates itself: a card is refused when the rater's model (when
  the host exposes it) equals the rated model.
- `smart_router_select`'s description gains one sentence: after a subagent
  reports, rating it with `smart_router_feedback` is optional and improves
  later picks.

### Storage

- One local file, `router-feedback.json`, in Smart Router's state directory,
  written under the existing lock helpers. Per card: rated model key, family,
  creator, work kind, level, rating, rater model key and creator when known,
  time, session hash. Comments are stored separately and capped.
- Size is bounded: cards older than 180 days, or beyond a fixed count per
  model and kind of work, are dropped on write.

### How cards move a model

- Effective score for a model and kind of work = catalog score + adjustment.
  Eligibility at the bars and the cheapest-first order then use the effective
  score.
- Adjustment = cap * (weighted mean - 3) / 2 * n / (n + 5), where cap = 10% of
  the anchor A (about 6 points today), n = the weighted card count, so a few
  cards move it little and many consistent cards approach the cap.
- Weights: a card's weight halves every 30 days; a card from a rater of the same
  lab as the rated model counts half; when a kind of work has no cards, the
  model's cards for other kinds count at half weight; a new version of a known
  family starts with the family's cards at half weight.
- Example: a level-2 model at 51.3 with six recent cards averaging 1.5 on
  implementation moves to about 48.8, below the 49.1 level-2 bar, so
  implementation stops going to it; its reviews are unaffected.
- Every pick reports `feedback: {cards, adjustment}` so the coordinator sees why
  it moved.

### Unchanged

The anchor, the bars, the level rules, cheapest-first order, the near clause,
`model_ref`, the council's seat rule, advisory-only.

## Open for review

- Whether ratings from coordinators judging their own delegations are reliable
  enough to move eligibility, and what bias the scheme still lets through.
- Whether model plus kind of work is the right key, given the coordinator's
  feedback that task size is what the router cannot see.
- Whether the constants (cap 10% of A, n/(n+5), 30-day half-life) are sound.
- Whether coordinators will actually fill in an optional card.

## Left to the builder

File layout of the store, the exact refusal messages, test fixtures.
