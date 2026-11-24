C1: CHANGE: Keep all four hard filters for compatibility, not as advertised steering controls. — Even `allowed_efforts` can pin a route by eliminating every other model’s selected effort.
C2: KEEP — No coordinator requested a plan pin. A harness filter can remove a plan before usage balancing sees it.
C3: KEEP — An allowlist of one and an exclusion list covering every alternative express the same preference, not a task requirement.
C4: CHANGE: Prefer a known different lab only among level-qualified reviewers. — Resolve the author’s `model_ref` to its catalog creator, not its provider. If independence is unavailable or unknown, return the normal pick with a notice.
C5: DEFER — Offered membership does not prove dispatch, but it bounds which models a card can affect. Before widening, establish a cheap feedback-time link between the pick, the completed child task and its catalog identity.
C6: KEEP — Rate observed work, not the decision to override. For another task this session, the coordinator can dispatch a listed alternative without changing future scores.
C7: CHANGE: Explain when filters removed the last otherwise qualifying route or emptied the candidate set. — Removing one lab need not change the pick. Do not claim it did.
C8: KEEP — Report the field and entry index against the known route inventory. Distinguish an unknown value from a known route that is unavailable.

**Proposal**

Advertise the facts the coordinator knows: task requirements and, for a review, whose work it is. Accept `reviewing` for diff reviews and plan reviews; the latter already use `work_kind: planning`. A different provider is not evidence of a different lab. Among qualifying independent reviewers, preserve normal ranking.

Keep legacy `constraints` as an opaque object in the tool schema. Runtime validation still enforces the four keys and list bounds. Merely deleting properties under `additionalProperties: false` would break existing callers. Hiding fields discourages routine pinning; it does not prevent a determined coordinator from pinning or dispatching directly.

**Compounding**

C2–C4 leave future balancing free to rank plans that meet the actual review requirement.  
C5–C6 leave unlisted outcomes unlearned. Ten overrides in fourteen tasks show disagreement, not ten failed picks or ten better replacements.  
C7–C8 distinguish “your filter ruled out every qualifying route” from “your value named no route,” without another selection call.

**Top 3**

1. C3 — Stop presenting model pins as task facts.
2. C4 — Give coordinators a direct way to request reviewer independence without sacrificing the level bar.
3. C5 — Keep the feedback boundary until dispatch attribution is verified.
