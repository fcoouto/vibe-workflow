R1: CHANGE: Define `exact` as naming files, expected behavior, and checks that distinguish success from failure — naming a test alone leaves decisions to the implementer.  
R2: CHANGE: Use `critical` when a plausible path to serious harm remains unresolved — a rounding helper feeding orders qualifies; a typo in the same repository does not.  
R3: CHANGE: Inherit the reviewed work’s risk and scope; judge exactness from its contents — calling a document a plan does not make it exact.  
R4: CHANGE: Review every written design once at level 3, combining builder questions and ranked flaws — this honors the explicit request without adding a second reviewer.  
R5: CHANGE: Review every written implementation plan once at level 3 after self-review — a missed requirement can propagate through every task and its tests.  
R6: CHANGE: Without routing, use the top available tier for design, plan, final, and critical reviews; otherwise use mid-tier — disabling routing should not weaken the requested review.  
R7: CHANGE: level 2 — two research tasks support cheaper execution, not equivalence on costly failures. Level 1 would also put critical work below otherwise identical shared work.  
R8: CHANGE: Reuse the final level-3 branch review; use level 3 earlier when later tasks depend on a critical decision — ordinary task reviews stay level 2.  
R9: KEEP — the evidence supports exact multi-file tasks. File count alone does not measure how much judgment remains.  
R10: CHANGE: Reserve `cross-system` for behavior that must agree across systems — changing the same documentation in two repositories does not create an integration risk.  
R11: KEEP — flag every below-level recommendation, notify once per session per level, and never count the fallback as satisfying a level-3 requirement.  
R12: KEEP — a unique catalog-side date match recovers a score; dropping a tier suffix risks assigning a smaller model its sibling’s score.  
R13: KEEP — inspect actual routing arguments and review decisions, not verbal promises. The typo should produce no plan; the indirect money path should still rate critical.

Compounding  
Use an explicit level-3 floor for document reviews; do not invent `critical` risk or an `open` spec to obtain it.  
Save on execution first: today’s level-2 pick costs $2 versus $16 per million blended tokens at level 3. Those prices do not measure total task cost.  
The three prompts check routing behavior, not review quality. Neither downstream defect rates nor the cost of a level-3 document review was measured.

Top 3: R5 — catch omissions before implementation; R7 — reduce repeated execution cost; R8 — catch critical mistakes before dependent work compounds them.
