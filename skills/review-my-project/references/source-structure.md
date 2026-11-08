# Focused source-structure assessment

Load only after the user explicitly approves detailed source-structure or agent-context suitability analysis, naming the selected repositories, candidates, and analysis. Ordinary structural observations remain part of Full Scan; Full Scan does not activate this deeper pass.

Recheck any supplied shortlist against the current selected checkout and candidate contents before relying on its ranking or coverage. If a candidate changed, disappeared, moved, or cannot be verified, mark it stale or unknown and ask for a refreshed shortlist when needed. Do not expand the approved repositories or candidate scope automatically.

Keep coverage separate for each Repository. Distinguish files counted as candidates from files structurally inspected, excluded, and unknown or not inspected. Group large result lists by meaningful source or ancestor folders within each Repository; grouping is for readability, not proof that every file was inspected or that repositories share authority. Rank findings only among inspected candidates and state whether coverage is partial.

For inspected candidates, describe actual responsibilities, dependencies, public interfaces, and relevant tests. Identify concrete component boundaries, a feasible sequence, dependencies between steps, material risks, and verification for any proposed follow-up. Effort or context-burden estimates may be unknown; label them honestly. Describe likely structural impact only: do not claim measured savings or workflow gains.

Do not change project files during this assessment. A report or accepted finding grants no refactoring authority; each selected change requires separate explicit approval and the project's normal development workflow. Follow the review procedure's safe-check, installation, selected-scope, unenrolled/umbrella, and retention rules. Where managed retention is unavailable, return the bounded report in chat; create no new report store.
