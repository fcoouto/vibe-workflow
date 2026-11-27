E1: DROP — a third of hidden tests failed in every condition, so a mistake can pass the stated checks on nearly every task; with "unsure: say prose" it cancels the settled exception.
E2: DEFER — right defect class, but no measurement shows a level-2 builder makes fewer check-invisible errors; in a statistics project the examples match most tasks (10 of 14).
E3: CHANGE: no text change now; take the evidence from level-3 diff reviews (defects that passed the stated checks), not from cards — cards cannot show this.
E4: DROP — asks the plan's author to rate its own checks as weak instead of fixing them; new schema field, effect unmeasured.
E5: DROP — the rater reads only the tool description; `docs/development.md` is for this repository's developers, not for coordinators in other projects.

Proposal
Change no text. The current words already cover the case: checks must "tell success from failure", and "unsure: say prose". That text shipped in 0.11.0 and has no field observation yet; the overrides were seen under 0.10.0. Level 1 and level 3 failed the same third of hidden tests, so the plan's checks limit quality, not the builder. Fix weak checks in the plan: the plan-review note in `/Users/fcouto/.local/share/opencode/worktree/13464e/exact-council/src/skill-advice.js` already asks for "failure modes no test covers". One level-3 fix per plan costs less than level 2 on 10 of 14 tasks. Revisit E2 when level-3 diff reviews of level-1 builds report defects that passed the stated checks. On the next plan, count them by hand.

Compounding
- Cards cannot answer E3's question. A card rates 3 for "met the stated checks", is filed when the subagent reports (before the diff review), and is keyed by model, work kind and size, not by task type.
- Cards adjust models, never task levels. Bad cards on statistics tasks would demote the level-1 model for all implementation work (its margin over the bar is 2.7 points; the cap is about 6) and never send such tasks to level 2.
- Overrides sit outside the router: a card counts only for the pick or its alternatives, and cards for other models are deferred (C5). The 10-of-14 pattern leaves no record.
- E1 or E2 plus "unsure: say prose" both push one way. In a critical statistics project, most tasks become level 2 while the diff review stays level 3 either way.

Top 3
1. Drop E1: it reverses the settled exception through the definition.
2. Cards are blind to this defect class. Without diff-review counts, "revisit with evidence" never triggers.
3. Bias flags, both directions:
   - Optimistic: 67.0% vs 65.2% is 6 runs on two task families with overlapping intervals, graded by tests that cannot see what tests miss. No review catch rate is measured. I voted KEEP on level 1 in council 00e94efeee57, so this stance flatters my earlier vote.
   - Pessimistic: "subtle statistics" is a coordinator's opinion, not a measured failure. But a pick overridden on 10 of 14 tasks saves nothing there, so the measured saving is already partly unrealised on that project.
