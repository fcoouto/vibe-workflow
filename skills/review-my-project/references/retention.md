# Private accounting and retention

Load only for a request to persist a review report or resolve private accounting.
Ordinary review remains available without enrollment, storage setup or a database.

Reuse existing `runtime/review_storage.py` ownership and D5 private evidence metadata.
At this implementation, `storage_status()` is observational;
`acquire_enrolled_accounting()` reads enrolled accounting, while `resolve_owner()`
and `resolve_project_owner()` may register compact temporary owner metadata.
There is no supported review-storage CLI or detailed-report producer in this base.
Do not call private artifact helpers, invoke setup, or treat inspection approval as
authority to create accounting records. Report unavailable interfaces explicitly.

For an enrolled member, preserve its canonical Project accounting. Temporary
unenrolled scope grants no enrollment, standing membership or future joining.
Repeated, subset and aliased reviews reuse reconciled original owners and ages.
Overlapping targets never silently reparent evidence or allocate a new allowance.
Unresolved identity or history stays protected and limits detailed retention.
Do not create another registry, database, quota, allowance or replacement owner.

Existing lifecycle owners retain these requirements: compact closed-task proof
for at most four months; detailed evidence for seven days after safe closure;
100 MiB of closed detailed evidence per Project, shared across umbrella members;
oldest eligible detail evicted first. Active, uncertain, merge-required and
recovery-required evidence remains exempt from expiry and the cap. Preserve
authoritative compact numerical proof before deleting detail. Safe closure needs
reconciled workers and no pending execution, merge or recovery dependencies.
Repeated requests and exports never reset ages or bypass allowances.

Until the existing owners provide and qualify the complete D10 publication,
cleanup and export path, keep detailed findings in bounded chat output. Explain
that no plugin report file was saved; do not promise control over native history,
user copies or deletion of externally retained exports.
