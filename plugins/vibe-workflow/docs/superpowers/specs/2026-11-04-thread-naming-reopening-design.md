# Thread Naming when work resumes

**Status:** Approved on 4 November 2026, including the workflow-owned topic rule.

The five-cell meter represents progress on the current objective. In the
read-only W15 example, the title still showed `▰▰▰▰▰` during an active turn.
The observation establishes stale display. The exact cause in W15 is unknown.

## Approaches

- **Coordinator checkpoint (chosen).** Decide what the user asked for in the
  normal turn, then update the title before substantive work starts. The
  coordinator can distinguish a continuation, a new objective, and a question
  about completed work. Reliability depends on following the checkpoint.
- **Temporary indicator on prompt submission.** React as soon as a prompt
  arrives, then reconcile after its intent is known. This would briefly hide
  completion for ordinary questions and acknowledgments and require a new hook.

## Behavior

Apply this checkpoint to chats with an adopted Thread Naming title and a
completed objective. Classify the request before the first substantive step,
including research, review, edits, or delegation.

| User request | Result |
| --- | --- |
| Continue or correct the same objective | Reopen it in the actual phase, normally `WORKING`. Keep accepted milestones that still apply, add the agreed remaining work, and recalculate progress. |
| Clearly start a different objective in this chat | Switch automatically. Keep the family and member code. Reset progress to zero accepted milestones. Use a short topic for the new objective when the existing topic is workflow-owned. |
| Ask about the completed result or acknowledge it | Keep the completed objective and full meter. |
| Unclear whether requested work continues the old objective or starts a new one | Ask one focused question before switching or starting the work. |

The [existing meter rules](../../../clients/workstreams-naming.md#status-and-accepted-progress)
apply after reopening or switching. Measured progress rounds down to fifths
and stops at four cells until verified completion. A fresh objective with a
measurable plan and zero accepted milestones shows `▱▱▱▱▱`. An unmeasured
`WORKING` objective shows the existing `▰▱▱▱▱` activity indicator. Only verified
completion of the current objective restores all five cells.

## State and delivery

Use the existing [title synchronization paths](../../../clients/workstreams-titles.md).
For ordinary native work, record the new phase and progress in the existing plan
and update the adopted title directly. For an already runtime-bound task, use
the revision-checked transition for a continuation or the existing replacement
and title rebind for a different objective. The clear request supplies the scope
decision. Runtime permissions, ownership checks, and replacement prerequisites
still apply. Keep the earlier objective and its evidence in existing history.

Respect explicit title choices, topic edits, and paused synchronization. Use
the existing readback and pending-write recovery on delivery failure. A title
failure alone cannot block otherwise authorized work. Scope is limited to the
current adopted chat. Keep semantic decisions in the normal turn and title
delivery in the existing controls. This design requires neither a new hook nor
managed runtime activation for ordinary chats.

## Acceptance checks

- Exercise the four request types in disposable chats. Check the meter before
  the first substantive action, including a research-only continuation, and
  verify family and member stability, milestone retention or reset, and the
  completion gate for the new objective.
- Exercise both native and runtime-bound paths. Verify that a paused title stays
  paused and that failed delivery can be reconciled through the existing path.
- Run `python3 -B scripts/validate.py`. Report package validation separately
  from observed Desktop title updates and their timing. Use W15 only as the
  read-only example.
