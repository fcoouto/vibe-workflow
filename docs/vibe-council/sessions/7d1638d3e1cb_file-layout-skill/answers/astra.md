I1: KEEP — A reusable skill answers “where does this file go?” Project notes record local decisions; `writing-plans` need not teach layout.  
I2: CHANGE: Choose placement by default; hand approved moves to `safe-refactor` — adding one file should not trigger a repository audit.  
I3: CHANGE: Follow framework requirements and the nearest comparable file first — domain grouping breaks ties; test discovery outranks source mirroring.  
I4: KEEP — Label each tree “example, not rule.” Show one placement decision per tree, including tests, docs, or shipped assets.  
I5: CHANGE: With approval, record only non-obvious decisions in existing contributor guidance — explain exceptions rather than copying a tree that will age.  
I6: CHANGE: Propose a split when distinct responsibilities make placement ambiguous — counts miss real boundaries, including framework-required single-file folders.  
I7: KEEP — Name file creation and placement in the description. Try an ordinary feature request without naming the skill before adding hooks.  
I8: KEEP — Keep the approved source-to-destination map as the move log. Stop on surprises; suspected dead files are outside scope.  
I9: CHANGE: Keep `runtime/` separate from `scripts/`; leave dev tools in place — the exact export manifest already excludes them.  
I10: CHANGE: Pilot with the next real file and its test, not a repository-wide move — seven root modules do not establish seven misplaced modules.  
I11: DEFER — Nesting requires new import validation and export rules. Revisit when a concrete domain split earns that cost; avoid interim prefix renames.  
I12: KEEP — `vibe-project-layout` names the decision without implying cleanup or permission to move files.

Compounding  
I3–I6 give agents a decision order: obey path contracts, follow local precedent, then consult examples when precedent runs out.  
I2 and I10 separate prevention from migration. A later move must preserve skill lookup, package exports, fixture copies, and test discovery.  
I7 and I10 test different claims: whether agents find the skill unprompted and whether its advice helps. Neither is verified live.

Top 3: I3 — placement order; I2 — approval boundary; I10 — pilot before migration.
