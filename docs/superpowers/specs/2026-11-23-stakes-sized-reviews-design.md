# Reviews sized to the stakes

Date: 23 November 2026. Status: texts sharpened by a level-3 reviewer and settled by the council; awaiting the user's approval. Depends on `2026-11-23-router-levels-design.md` (level 3 means the
top three labs' flagships).

## Intent

Do more with less: spend a strong model only where the stakes call for it. A
real-money project gets a strong reviewer on its design, its plan and what cheaper
models implemented; a reversible docs change gets a light check or none. The agent
judges the stakes per task; this change helps it judge and tells it where to apply
the judgment.

## Goals

- Designs and plans get an independent level-3 review when their stakes call for
  one. Today the
  design check (brainstorming's builder check) inherits the session's model and only
  asks what is unanswered; plans get only the author's self-review.
- Design and plan reviews run at level 3, as the user asked; cheap models implement
  exact plans; critical diffs get level-3 reviews.
- Agents rate `risk`, `spec` and `files` consistently, from short definitions read
  where they ask Smart Router for a model.
- Implementation reviews keep superpowers' existing rule (scale the reviewer to the
  diff's size, complexity and risk), now with sharper facts.

## Non-goals and anti-goals

- No per-project stakes setting; the agent judges (the user's decision).
- No new skill now. Write one only if the live check shows agents misjudge.
- Anti-goal: a review on every small change. Anti-goal: under-rating real-money,
  credential or production work.
- Smart Router never launches the reviewer; the agent dispatches it.

## Approaches

- **A. Definitions in the tool description plus skill-load notes, then a live check
  (chosen by the user).**
- B. A separate judging skill now. Deferred until evidence shows A is not enough.
- A per-project stakes setting at onboarding. Rejected: the user wants the agent to
  judge.

## Decided

1. `index.js` tool description for `smart_router_select` gains the definitions below.
   Current length 243 characters; the Claude Code bridge cuts descriptions at 2048.
2. A new note in `src/skill-advice.js` for `brainstorming` (gate: onboarded): the
   builder check becomes the design review, critiquing as well as asking.
3. The existing `writing-plans` note gains one plan-review step after the self-review.
4. Design and plan reviews run at level 3: the agent asks Smart Router with
   `work_kind: "planning"`. Stakes decide only whether a review runs, not its level.
   Without Smart Router, the top tier.
5. Diff reviews (superpowers' per-task and final reviews) take the reviewed work's
   own facts with `work_kind: "review"`, so a critical task's diff gets a level-3
   reviewer and other diffs level 2. No code change: the `max` rule already does it.
6. Implementing or testing an exact plan stays at level 1 even when its risk is
   critical; only `cross-system` raises it. This amends the task-level rule in
   `2026-11-23-router-levels-design.md`. Quality comes from the level-3 design and
   plan reviews and the level-3 review of each critical diff.
7. After the change, `scripts/live_check.py --injection` runs (pre-authorized), plus
   three `--prompt` cases with the user's OK (see Live check).

Council: `docs/vibe-council/sessions/00e94efeee57_cost-quality-balance/result.md`,
weighted quality 60, cost 40.

## Texts

Model names never appear in these texts; they speak in levels and facts (the
user's rule).

Tool description, appended after the rubric:

> Rate the task, not the project, by what a mistake in it would cost, not by its size. risk: critical = a mistake could lose real money, expose credentials, break production or users' data, or cannot be undone by a revert, including config, flags, helpers or fixtures that feed such a path; shared = it changes code, data or interfaces that other work depends on; reversible = local and undone by a revert. Unsure whether it is critical: say critical. spec: exact = a written plan names the files, the expected behavior and the checks that tell success from failure; prose = described in words; open = approach undecided. files: files changed; cross-system = changes in more than one repository or deployed service that must stay compatible with each other. Implementing or testing an exact plan stays at level 1 unless cross-system; its reviews carry the risk. To review a design or plan, use work_kind planning. To review a diff, give the risk and files of the work reviewed, spec exact if it was built from an exact plan, otherwise prose.

`brainstorming` note:

> vibe-workflow: your human partner asks that the design check match the stakes. Run the builder check for a project, and also for a small change where a mistake could lose real money, expose credentials, break production or users' data, or cannot be undone. Other small changes skip it, as the skill says. Add one request to its prompt: "Then list up to five flaws in the approach that would cost most: a wrong choice, a gap, an unhandled risk. No rewrites." Give the reviewer the document path only, for one pass, and sort its flaws into the same three piles. Set its model as planning work (level 3); without Smart Router, the top tier. An explicit request from your human partner in this session overrides this.

`writing-plans` note, step added before the override sentence:

> After your self-review and before you ask for theirs, unless the plan's risk is reversible, dispatch one fresh reviewer, once, with the spec and plan paths only. Ask it for, ranked: spec requirements with no task, misordered tasks, failure modes no test covers, and steps a cheap implementer could misread; "nothing important" is a valid answer. Set its model as planning work (level 3); without Smart Router, the top tier. Fix what it finds yourself, without a second review; bring your human partner only what they must decide.

The builder confirms the final character counts; the whole tool description must
stay under 2048.

## Known risks

1. Smart Router cannot enforce that the level-3 reviews happen; the live check must
   show they are dispatched.
2. Most plans touch shared code, so most get a level-3 plan review. Narrow the skip
   only if live use shows empty reviews.
3. Indirect paths may still be rated `reversible`; the definition now names them,
   and the third live-check case tests it. Misjudgment there is the trigger for the
   separate judging skill.
4. The author rates its own plan and may skip the review; the critical tie-break
   covers only that boundary.
5. The evidence for level-1 implementation is two task families in one repository;
   no review catch rate is measured.

## Live check

With the user's OK, three `--prompt` runs, inspecting the actual Smart Router
arguments and dispatches, not the agent's promises:

1. A change to order sizing in a trading project: `risk: critical`; plan review at
   level 3; the implementer of the exact plan at level 1; a level-3 review of its diff.
2. A docs typo: `risk: reversible`; no plan review.
3. A config or helper change feeding a money path: `risk: critical`.

## Left to the builder

- Exact placement of the definitions (tool description or the `risk`/`spec`/`files`
  property descriptions in the input schema, which the bridge passes whole).
- Test updates for the notes and the description.
