R1: CHANGE: define cross-system as "changes land in more than one repository or deployed service", dropping "or an external system" — any broker or API call would otherwise rate cross-system and force level 3 through R9 and R10, undoing R7. This flaw is in my own draft.
R2: KEEP — under-rating money code costs far more than one extra review; after R7, over-rating raises only reviewers and prose-spec implementers.
R3: CHANGE: design and plan reviews run at level 3 (set the model as `planning`); diff reviews keep the reviewed work's risk and files — this is the user's verbatim instruction, and stakes still decide whether the review runs (R4, R5).
R4: KEEP — stakes-based gating, five-flaw cap and one pass bound the cost. The "neutral" label flatters: today's check inherits the session model, so level 2 would be a downgrade without the R3 change.
R5: KEEP — one review per non-reversible plan, at level 3 per R3; the unit cost is unmeasured, so narrow the skip only if live use shows empty reviews.
R6: CHANGE: add the fallback to the `writing-plans` note too, top tier for design and plan reviews — that note has no fallback today, so an unrouted plan reviewer inherits the session model.
R7: KEEP — the only measured evidence is in this very repository: Haiku 67.0% against Opus 65.2% at about 5% of the cost. A higher tier bought nothing measurable.
R8: CHANGE: no exception; each per-task review takes its own task's risk (R3), so only tasks rated critical get a level-3 reviewer — R1 already limits those to money-path tasks.
R9: KEEP — the evidence tasks were multi-file plan tasks in one repository, which is exactly this case.
R10: DEFER — rare and unmeasured; the narrower R1 definition removes most false hits, and another exception erodes the `max` rule.
R11: KEEP — no silent downgrade, and one notice per session per level is the lightest honest form.
R12: KEEP — conservative; no pick changes today, and it blocks a small model scored as its larger sibling.
R13: CHANGE: add a fourth case, an ordinary shared change in non-money code (expect shared), and confirm in the critical cases that the review dispatches at level 3 — the three cases test only the extremes, nothing tests R2's inflation, and R7's condition is text-only.

Compounding:
- R7 and R9 move nearly all planned implementation to Haiku ($0.20 against 2 to 16 per million tokens). Both tiers passed about 66% of hidden tests, so my inference is that the plan caps quality, not the implementer. Spend part of the saving on the documents (R3) and on critical diffs (R8).
- R7 and R8 as drafted loosen the same money diff twice: weakest implementer, then a mid-tier per-task reviewer. The final review has one fix wave and no re-review, so late catches get the weakest fix loop. A level-3 per-task review on critical tasks costs less than R7 saves.
- R2 with broad R1 terms ("break production", "external system") inflates ratings. After R7, critical inflation costs only reviews, which is affordable. Cross-system inflation hits implementers, so narrow R1.
- Optimistic bias: the evidence is two task families, Haiku against Opus (now level 2), with overlapping intervals of about 6 points. No Terra or Fable run exists, and no review catch rate is measured. About a third of hidden tests fail in every condition, so the review net carries real load. A stateless router cannot enforce "a level-3 review follows"; R13 must show it happens.

Top 3: R7, R3, R8.
