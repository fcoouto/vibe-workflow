I1: DROP — A frontier release does not make an unchanged model worse at following an unchanged plan.  
I2: CHANGE: Start level 1 at 25 Intelligence Index points — The proposed 27.6 floor leaves Luna only 0.3 points of headroom.  
I3: DROP — A lab’s cheapest release measures its pricing strategy, not its ability to execute a plan.  
I4: DROP — Use Haiku and Luna as test cases, not dependencies of the routing rule.  
I5: CHANGE: Ignore `3+` only for exact, reversible, single-system implementation — Preserve cross-system scope as a level-3 trigger.  
I6: CHANGE: Keep three levels and the max rule, with the I5 exception — No new caller field is needed.  
I7: CHANGE: Share bounded work, minimize price at level 2, maximize score at level 3 — Planning and execution warrant different tradeoffs.  
I8: CHANGE: Share bounded recommendations now; defer quota-aware balancing — Eligibility alone never gives Luna a turn at equal price.

**Proposal**

Call a plan `exact` only when it settles the implementation choices and names a check for completion. File count alone should not promote a mechanical change. Treat delegated plan review as planning; ordinary diff review stays level 2. Leave the main session untouched.

Reuse native routes, existing eligibility checks, and cached Artificial Analysis scores and blended prices from OpenRouter. Apply a provisional 25-point minimum at every level; return no recommendation below it. This is a policy guardrail, not a measured competence threshold.

- **Level 1, low effort:** Prefer qualifying models priced at $1/M tokens or less. Within that band, take each plan’s highest-scoring model. Draw uniformly among the distinct resulting models. Return a different model and plan as first alternative when possible. If nothing qualifies under $1, use the cheapest qualifying route. Today: Haiku or Luna; Haiku via Go does not give Haiku a second ballot.
- **Level 2, medium effort:** Pick the cheapest model clearing both 25 and 82% of the best runnable score. Break ties by score. Today: Sol over Sonnet at $4/M.
- **Level 3, high effort:** Pick the strongest runnable model; price breaks ties. Today: Fable. Leave council seating unchanged.

With supported xAI and DeepSeek routes, DeepSeek V4.1 Flash and Grok 4.6 clear level 1. Level 2 buys the cheapest score ≥29.11; level 3 picks Grok 4.7 at 35.5. Report that as best available, not frontier-equivalent. Direct subscriptions still need supported route adapters.

A new runnable frontier model can change levels 2–3 without evicting bounded executors. No extra reads, inference, or persistent counters.

**Compounding**

I5 gets plan execution into level 1; I2 gets Luna into its candidate set. Neither alone delivers the intended result.  
I7 and I8 spread recommendations, not measured quota consumption. Two plan tasks support trying this rule, not claiming model equivalence.

**Top 3:** I5, I2, I7.

Scope disclosure: I read the vault index alongside the brief, before seeing its exclusion. I opened no vault topic files.
