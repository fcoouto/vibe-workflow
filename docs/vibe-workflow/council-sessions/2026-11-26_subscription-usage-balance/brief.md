# Council brief: Subscription Usage Balance

## Role

You are one of three council members (Fable, Astra, Opus). Answer independently. You cannot see the other members' answers. This is a design review before implementation, not a request to write code or to choose someone's next model.

## The user's standing instructions

> We want to balance usage across their subscriptions while also being aware of windows that are close to closing. If a window is about to close, we want to use as much of it as possible because we want to optimize the usage of their subscriptions.

> For example, OpenAI is at 90% usage, and its expiry time is in two hours. On the other hand, Anthropic is at 40% usage, and its expiry time is in three days. Which one would you choose? I would say OpenAI has priority because we want to optimize usage. So we're going to use the remaining 10% as much as we can. Once Codex OpenAI resets, the balance or weight will shift to Anthropic's subscription plan.

> But that's not a simple equation, as you can see, because we don't have only two subscriptions. Users may have more. On top of that, we also want to respect the same lab rules that we've applied for a revision for a reviewer, for example.

> So, for now, let's consider that what we are retrieving from Codex is using the same account as in OpenCode OpenAI connection. And we calculate balacing/prioritization based on that Quota Usage from Codex.

> The only need we need to make sure is that we are using OpenCode OpenAI connection via Browser and not an API-key-based connection.

> I'd say no need to ask user's confirmation

## Bias

Weigh optimistic bias as well as pessimistic bias. Flag anything that flatters the result as readily as anything too cautious. An equation that predicts this one example is not evidence that it works on parallel picks or across conflicting windows. Conversely, a guardrail that prevents nearly every change to the current order does not achieve the user's objective. Prefer a first version whose decisions can be explained and tested with simple known-answer cases.

## Context

Smart Router is an advisory selector for OpenCode V2. Claude and OpenCode Go exhaustion evidence has merged in PR #406 (`022bc1f`); it filters routes before ranking but does not optimize below-limit usage. The older, unapproved design in `docs/superpowers/specs/2026-11-20-subscription-usage-balancing-design.md` proposes projected pace bands. Its provider audit predates the merged work. Current evidence and constraints are in `docs/development.md`, the Claude design alongside that draft, and `docs/chatgpt-quota-spike.md`. Inspect `runtime/routing.py` and `runtime/selector.py` for current ordering and reviewer independence. The current formula uses public price as a ranking proxy; it is not a separate per-request charge for eligible included subscription usage. This review should settle the shape of a revised design, not authorize rollout, threshold choices made by fiat, or a different provider mix.

Two approaches are on the table:

- **Expiry-first with protection.** Start from unused quota per hour until reset for a soon-closing eligible window. Bound urgency and limit its influence when another window on that account is under sustained pressure. Use the existing order for cases the new signals cannot resolve. The benefit is that soon-to-expire quota can actually move a pick. The risk is that raw division makes tiny windows dominate, or that the protection blunts the feature.
- **Pace bands with an expiry bonus.** Keep projected consumption (`used / elapsed` once enough of a window has passed), summarize the most pressured window, and add an expiry bonus. This starts from the old proposal and guards longer-term headroom. If pace bands are absolute, it fails the reference decision; an effective expiry bonus must be allowed to cross a band or change how bands are used. That makes the interaction between the band cutoffs and bonus something we would have to calibrate.

## Settled rules (not up for a vote)

- Optimize each plan's own allowance before it resets, not equal percentages or equal numbers of tasks. A percentage point is relative to its plan, not a common unit of work across providers.
- Billing, confirmed exhaustion, and existing quality/admissibility rules apply first. Do not widen the below-level or near-floor exception. Preserve the current qualified independent-reviewer preference and current fallback when no independent reviewer qualifies. A provider name is not a lab; a model on Go uses Go quota.
- The user accepts the same-account assumption for Codex and OpenCode's browser-based Sign in with ChatGPT. Reuse the existing subscription OAuth check, never use an API-key connection for this hint, and do not ask for account confirmation. This is a ranking hint, not an entitlement or exhaustion check. The OpenCode-specific app cap may differ; OpenCode's existing limit handling stays authoritative.
- No model calls to measure usage, polling, quota reservations, slots, automatic retries, or paid fallback. Free models and pay-per-use routes stay out. No invented headroom for unknown, expired, or unusable readings. When no usable usage data can change the decision, reproduce the current order.
- Use current, account-bound observations where possible, and keep freshness for a ranking hint separate from the lifetime of a confirmed pause. Leave effort, feedback adjustments, and council seat selection unchanged. No implementation is approved by a vote alone.

## Measured facts

- **Reference decision, synthetic.** Treat both windows as 168-hour weeks for this exercise. A at 90% used with 2 hours left has 10 percentage points left, or 5.00 points/hour. B at 40% with 72 hours left has 60 points left, or 0.83 points/hour. The user wants A for otherwise comparable admissible choices. Applying the old draft's equation gives projected utilization of 91.08% for A (on pace) and 70.00% for B (spare); a hard band-first order picks B, contrary to the goal.
- **Reset case, synthetic.** If A is actually read again at 0% used and 168 hours left, its rate becomes 0.60 points/hour. Holding B's prior state fixed to isolate the reset in this test, B now leads. If A has reset but we have not read it again, its previous observation has expired; do not impute 0%. These figures come from `remaining percentage points / hours to reset`; they are illustrative, not a quota estimate for an actual user.
- **Conflicting windows, synthetic.** C has a 5-hour window at 20% used with 15 minutes left and a weekly window at 98% with 96 hours left. The same equation gives C 320 points/hour for the short window and 0.021 for the weekly window. A raw maximum ignores weekly pressure. A raw minimum can let long windows suppress every rolling-window opportunity, even when the long window is healthy. A reasonable design must distinguish those cases.
- **Current ranking, code inspection.** The normal quality floors are 0.55, 0.80 and 1.00 times the score anchor. The existing near-floor exception requires at least 0.97 times the floor and at most half the cheapest qualifying price. Current qualifying choices are ordered by price, score and deterministic tie-breakers. Independent reviews select from qualifying routes of another lab first. These are baseline behavior, not proposed new coefficients.
- **Current input availability, code and documented endpoint checks.** Claude's independent exhaustion parser validates percentages but stores exhaustion, not general-purpose independent usage percentages; the older complete snapshot can be missing when one window is unavailable. Go's selected-key usage read returned HTTP 200 with status, percentage and UTC reset for rolling, weekly and monthly windows in one authorized no-inference check. Its new store also retains exhaustion rather than a ranking percentage. Five-hour and weekly durations are known; a monthly paid-period length should not be guessed from its name. Codex's existing bounded read receives percentages and future resets but currently returns only a reset hint after a recognized limit. A ranking cache should reuse the existing acquisition windows and readers where possible; it does not exist yet.

## Items

- **R1 — Objective and equation.** Start with bounded expiry priority, not an absolute projected-pace band. Compare it against pace bands plus an expiry bonus. Recommend the simplest deterministic rule that prefers A in the reference case, switches appropriately with a fresh post-reset reading, and does not blow up near zero time. Direction: neutral. If wrong: the router either wastes expiring quota or empties a plan too early.
- **R2 — Multiple windows and multiple plans.** Allow a soon-closing window to matter, but temper its benefit when a longer window on that subscription is under serious pressure. Compare a simple pressure penalty or cap with raw min/max and explain how to handle overlapping meters and unknown durations. Direction: stricter. If wrong: a rolling reset can drain a weekly allowance or a quiet long window can suppress every short-window opportunity.
- **R3 — Influence on price and score.** Let quota genuinely change some decisions, not only exact ties. Keep existing eligibility, quality, and review independence, and start with bounded movement relative to the current price/score order rather than replacing it for every qualifying candidate. Say how you would bound it, or argue for usage-first within the admissible set. Direction: neutral. If wrong: the feature either never matters or regularly displaces materially better or cheaper choices.
- **R4 — Reading trust and mixed availability.** Use fresh, bound readings from the existing paths, and a bounded on-demand Codex cache under the accepted OAuth and same-account policy. Unknown values are neither spare nor hot. Propose how to preserve a deterministic order with one unknown plan among known plans, while expiring a window's old sample at reset and avoiding any read on every pick. Direction: stricter. If wrong: stale or missing data masquerades as capacity, or normal selection pays unnecessary latency.
- **R5 — First release boundary.** Defer synthetic per-recommendation debits, learning per-model consumption, task-size predictions, and tunable per-provider weights. Instead test a handful of known answers, including concurrent callers seeing the same cached reading; accept that ranking alone offers no guarantees under bursts. Say if one additional test or guard is essential before choosing a design, without proposing a reservation layer. Direction: stricter. If wrong: we either ship a much larger estimator than needed or hide a failure mode behind good single-pick examples.

## Output format (strict)

Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints

- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: `.env*`, `.git/`, `.superpowers/`, `.vibe-workflow/`, and all files outside the worktree. No credentials, private runtime state, or private usage records.
- Do not use network tools, make model calls, or probe the live runtime. Public facts needed for this design are in the cited files and brief.
- Use about 15 tool calls.
- Answer in your final message.
