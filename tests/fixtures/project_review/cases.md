# D10c source-based native cases

Use disposable copies of `targets/`; these are raw synthetic inputs, not enrolled projects.

## Fresh shortlist and partial umbrella

User request: Review alpha and beta under Code quality. I explicitly approve detailed source-structure and agent-context suitability analysis of the three paths in `fresh-shortlist.json`. Inspect at most two candidates structurally across both repositories and take at most five minutes. Refresh freshness from the selected paths. Analysis only; no installation or source changes are approved. Return findings in chat.

Follow-up user turn: I accept this report. For a possible later change, describe the smallest formatting boundary you would consider, its sequence, risk and verification; do not implement it.

## Full Scan then stale shortlisted analysis

First user request: Review alpha and beta with Full Scan, at most five source/configuration files and five minutes. `stale-shortlist.json` is available from a prior inventory. No focused source-structure assessment has been requested. No installation or source changes are approved. Return findings in chat.

Second user turn: Now I explicitly approve detailed source-structure and agent-context suitability analysis only for alpha/src/orders/service.py and beta/src/cache/removed.py from that shortlist. Refresh current contents at those exact paths; missing paths are not approval to select replacements. Inspect at most those two candidates within this probe's original five-minute budget. I accept the resulting analysis report; this acceptance does not request implementation.

The excluded repository is outside every request. No scenario authorizes persistence, enrollment, accounting setup, actual installation or refactoring. Native observations and source preservation must be evaluated independently; fixture checks do not qualify installed discovery or native multi-select.
