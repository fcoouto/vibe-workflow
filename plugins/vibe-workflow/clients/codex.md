# Codex

## Startup behavior

Apply the [shared policies](../policies/00-index.md) within the recorded checkout scope. Startup enrollment alone supplies orientation only. Runtime controls require separate opt-in. Only for explicit task controls, a `doctor` report, or lifecycle troubleshooting, read the [Codex task runtime contract](runtime.md). Bounded CLI qualification and bundled core lifecycle checks are recorded; a controlled bundled Interrupt passed while Stop output was held, with general read-first correlation still limited. See [validation details](../docs/d2-validation.md).

Actual Desktop startup orientation and D5 check/review/completion behavior are qualified for the recorded version and disposable scope; see [D5 validation](../docs/d5-validation.md). This does not extend the guard or continuation guarantees above.

The plugin registers one `SessionStart` command through `hooks` in `.codex-plugin/plugin.json`. Read the policy index and applicable contracts when the hook supplies their local paths. Normal activation adds model context without an activation announcement. A warning means orientation did not load; report that limitation and resolve it before claiming this startup integration works. It does not by itself block native editing, tests or review.

The [adapter](../runtime/adapters/codex.py) validates session identity, absolute `cwd`, event name and source (`startup`, `resume`, `clear`, `compact`, `fork`). Supplied model and permission mode remain observations; absent values, client version and UI surface remain unknown. The [shared startup code](../runtime/startup.py) resolves the nearest Git checkout and common Git directory. Parent and neighboring enrollment never extends scope.

No enrollment produces no output. Each lifecycle event can reload context after resume, fork or compaction; it does not run on every tool call. D1 has no persistent event deduplication because the native event supplies no unique invocation ID.

Ordinary Codex editing, tests, review and handoffs use the native workflow; they
need no runtime database, mandatory task-state transition or default patch
blocker. Load task-runtime and qualification procedures only when managed task
controls are explicitly selected. Their failure must not disable status,
configuration recovery or file undo. Preserve selected controls and existing
repository protections.

When a request calls for work after a completed objective in an adopted Thread
Naming chat, load [reopen a completed objective](workstreams-titles.md#reopen-a-completed-objective)
before substantive work. Use that procedure when the relationship to the old
objective is unclear. Ordinary questions about a completed result and
acknowledgments need no title update.

## Instruction output budgets

Select only applicable instruction files. Supply Vibe paths relative to the package or as absolute paths; supply external skills and their triggered Markdown references as absolute paths. A selection may mix both: the reader determines each source's authority and retains its package or external checks. Never run `--prepare` during routine tasks.

With `functions.exec`, use this complete launcher in one invocation for each selection. Set only `root` and `paths`; keep the launcher body intact. It loads one fixed packaged runner per selection, which automatically drains continuations through separate `notify` outputs, including bounded small-page recovery. Do not print/collect batch bodies into the direct result, run continuation calls manually, or evaluate instruction contents. If the cell yields as running, wait for its terminal result before proceeding. Only `complete: true` establishes delivery of the selected text; it does not establish applicability, semantic compliance or retention after compaction.

<!-- native-instruction-loader:start -->
```javascript
// @exec: {"yield_time_ms":60000,"max_output_tokens":1000}
const root = "/absolute/Vibe/package";
const paths = ["policies/02-task-planning.md"];
// Loader
const quote = s => "'" + s.replace(/'/g, "'\\''") + "'";
const r = await tools.exec_command({cmd:"python3 -I -B " + quote(root + "/scripts/read_instructions.py") + " --helper",
  max_output_tokens:10000, yield_time_ms:10000});
if (r.session_id || r.exit_code !== 0) throw new Error("Instruction delivery incomplete: helper did not finish successfully");
let helper; try { helper = JSON.parse(r.output); } catch {}
if (!helper || helper.version !== 1 || helper.kind !== "helper" || typeof helper.text !== "string" ||
    !Number.isSafeInteger(helper.characters) || helper.characters < 1 || [...helper.text].length !== helper.characters)
  throw new Error("Instruction delivery incomplete: helper output was omitted");
const drain = (0,eval)("(" + helper.text + ")");
text(await drain({root, paths, exec:tools.exec_command, emit:notify}));
```
<!-- native-instruction-loader:end -->

If this native surface is unavailable, use `python3 -I -B <package>/scripts/read_instructions.py --auto --batch <paths>`, return each batch alone and complete every continuation with the same mode, selection and revision. On reader or unrecovered transport failure, report incomplete delivery and recover only missing source ranges with bounded native reads before relying on the contract. Give each recovery read its own notification or outer call; never aggregate recovery bodies. For a direct `functions.exec` result, use one child with `max_output_tokens:10000` and an outer `max_output_tokens:12000` for wrapper headroom. Preserve already received text and freshness; a changed source requires a new consistent selection.

Size each child instruction read for its complete selected files or sections. For outputs accumulated into the direct `functions.exec` result, use supported child `max_output_tokens` limits and an outer budget covering their sum plus response wrappers. The loader above delivers separate notifications; its direct budget covers only the receipt. A larger outer budget cannot restore child-truncated text. Use available size information without adding a sizing command to every read; split large or uncertain reads into smaller, contiguous sections. Keep large diagnostics, diffs and third-party guides separate when they would exhaust either budget.

Required procedures must arrive completely. Requested limits may be capped; inspect both child and outer results for truncation or omitted text. Success exit codes, summaries and previews do not establish completeness. Retain complete material already received and recover only missing files or sections before proceeding, adjusting the limit or selection at the layer that omitted them. Required review, freshness checks and context recovery still apply.

In enrolled checkouts, the existing `PostToolUse` hook surfaces the observed child-truncation marker as a short recovery notice. The notice is advisory: stdout can imitate the marker. It does not replace completed output, run recovery commands or observe later outer clipping. Normal results remain silent.

## Startup implementation

Read [startup implementation and qualification](codex-startup-implementation.md) when implementing or diagnosing startup, preparing enrollment, qualifying a client adapter, or changing hook registration, trust or packaging.
