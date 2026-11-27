E1: DROP — Every finite test suite can miss a bug. That does not make every plan task `prose`.
E2: CHANGE: “If no stated check tests a required behavior, it is prose.” — Judge the check against the requirement, not the subject’s difficulty.
E3: DROP — Waiting for cards cannot resolve this question. A wrong result that passes weak checks can earn a good rating.
E4: DROP — More effort does not supply a missing expected result. No new input is needed.
E5: DEFER — Add a worked pair if coordinators still disagree after the tool wording changes.

**Proposal**

Clarify the existing sentence at the point of selection. For example, a plan requires sample variance. A check that accepts any finite result never tests the choice of variance formula: `prose`. A check expecting `2` for `[0, 2]` distinguishes sample variance from population variance (`1`). With the other plan requirements met, that task is `exact`. The check need not catch every possible bug.

The 10 overrides show coordinator distrust, not 10 avoided failures. Conversely, 67.0% versus 65.2% on two tasks supports the cheaper builder there. It does not establish that either level catches subtle errors.

**Compounding**

Plan review can add the missing check before dispatch, preserving level-1 implementation and level-3 critical diff review.  
Keep “Unsure whether it is exact: say prose.” The four-hour effort step measures duration, not check quality.  
Cards adjust models, not plans. Skip the card when the brief caused the failure.

**Top 3**

1. Test the named behavior; do not demand exhaustive coverage.
2. Fix a weak check in the plan before paying for a stronger builder.
3. Treat overrides as questions, not outcomes. The critical-review catch rate remains unknown.

Access note: The initial parallel read included `/Users/fcouto/ai-memory-vault/MEMORY.md` before I saw the brief’s exclusion. I read no vault topic files.
