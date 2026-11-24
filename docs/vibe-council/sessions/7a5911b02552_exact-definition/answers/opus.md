E1: DROP — "could pass the stated checks" is true of almost every task, so nearly all plan tasks would turn prose. Combined with the settled rule "Unsure: say prose", this undoes the exact-plan exception.

E2: DROP — It would make the coordinator's 10-of-14 overrides official without evidence. It also mixes up two things: weak checks are a fault in the plan, while `exact` asks whether the builder has anything left to decide. The fix belongs in the plan's checks.

E3: CHANGE: no text change now; ask raters to card the builder after its diff review, when one runs, and revisit on review findings, not cards alone — The card scale's "3 = met the stated checks" cannot see the defect in question. Cards filed before the review would score a subtly wrong build at 3 or 5.

E4: DEFER — It raises effort, not level, so it costs little. But it adds a new input, and there is no evidence that more effort catches subtle numerical errors. Revisit if the reviewed cards show level-1 builders failing on this kind of task.

E5: DROP — `docs/development.md` serves people working on this repository, and plugin users' agents never read it at decision time. A skill reference has the same problem. Any example belongs where the plan is written.

**Proposal**

Leave the `exact` definition unchanged. The measured evidence does not support raising the builder's level for this risk. A level-3 builder scored no better than level 1 on hidden tests (65.2% vs 67.0%), and about a third of hidden tests failed in every condition. Escalating the builder is not shown to fix subtle errors; stronger checks might. The existing `writing-plans` plan review already asks for "failure modes no test covers". Optionally add a few words there: for statistics, numerics or calibration, ask for known-answer or reference-value tests. That would trigger the pre-authorized injection live check. Change the card timing so cards show whether reviews catch subtle defects in level-1 work. Then decide E2 or E4 on that evidence.

**Compounding**

- The settled "Unsure: say prose" makes any stricter rule fire wider than written. E1 would turn nearly every task into prose, and E2 every statistics task, since the coordinator already believes "a mistake wouldn't show in a quick test".
- The exception relies on level-3 diff reviews for critical tasks, but no review catch rate was measured (stakes design, Known risks 5). Counting on that safety net is the optimistic bias here. Card timing after review is the cheapest way to measure it.
- A prose label changes nothing for critical diff reviews, which are already level 3 through `max`. Its only effect is a costlier builder, which is exactly the part the evidence says adds nothing.
- Pessimistic bias: the 10 overrides cite no observed failure. Under the old text the coordinator relabelled only 3 tasks on its own; the other 7 came from fear of hidden errors, not from any shown defect.

**Top 3**

1. E3 with the change: no text change, cards filed after the diff review, and a revisit when review findings arrive.
2. Make plan reviews ask for reference-value tests on statistics, numerics or calibration code, instead of changing the spec rule.
3. E4 deferred as the cheaper fallback, if evidence shows level-1 builders failing on weak-check tasks.
