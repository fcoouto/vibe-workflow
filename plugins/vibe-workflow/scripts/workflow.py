#!/usr/bin/env python3
"""Plugin hooks and local controls, including approved onboarding and recovery."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def title_report(scope, client, session, report):
    if client == "codex" and session:
        from runtime.title_sync import repair
        result = repair(scope, client, session)
        if result.get("managed"):
            report = dict(report, title_sync=result)
    return report


def workstreams_control(scope, action, request):
    from runtime import delegation, state, workstreams
    feature = request.get('feature')
    state._text(feature, 'Workstreams feature', 64)
    delegation._require(feature in workstreams.FEATURES, 'Expected an explicit Workstreams feature')
    if feature != 'capacity_recovery' or action == 'status':
        return workstreams.control(scope, action, request)
    from runtime import parent_recovery
    state._fields(request, ('client', 'feature', 'expected_revision'))
    directory = parent_recovery.default_directory()
    preflight = parent_recovery.preflight_feature(scope, directory)
    if not preflight['ready']:
        return workstreams.control(scope, 'status', dict(client=request['client'], feature=feature)) | dict(
            ok=False, preference_changed=False, error=preflight['reason'],
            recovery=parent_recovery.compact_result(preflight, 'preflight'))
    previous = workstreams._feature(scope, feature)
    report = workstreams.control(scope, action, request)
    try:
        lifecycle = parent_recovery.reconcile_feature(scope, directory)
        if lifecycle['pending']:
            next_action = ('Inspect or perform the original native schedule operation for this token, then use '
                'scripts/workstreams_recovery.py record-schedule with its observed result. '
                'Inspect an uncertain operation before retrying it.')
        elif lifecycle['schedule_id'] is not None or lifecycle['wanted'] == 'present':
            next_action = ('Observe the owned native schedule, then use scripts/workstreams_recovery.py schedule '
                'with that observation and record-schedule after any native operation. '
                'Manual suspension requires explicit human reactivation.')
        else:
            next_action = 'No owned recovery schedule is recorded; no native schedule operation is pending.'
        return report | dict(recovery=parent_recovery.compact_result(lifecycle, 'reconcile-feature') |
            dict(next_action=next_action))
    except (ValueError, OSError, KeyError, TypeError) as error:
        return report | dict(ok=False, preference_changed=any(report[key] != previous[key] for key in ('enabled', 'revision')), error=str(error),
            recovery=dict(next_action='Reconcile the existing recovery registry and schedule through its original controls.'))


def control_main():
    from runtime.startup import MAX_INPUT, StartupError, json_object, acquire_project_entry, _entry_scope

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise StartupError("Invalid task, evidence, delivery, delegation, doctor or onboard command arguments")

    try:
        parser = Parser(add_help=False)
        commands = parser.add_subparsers(dest="command", required=True)
        task = commands.add_parser("task", add_help=False)
        actions = task.add_subparsers(dest="action", required=True)
        for name in ("start", "update", "status", "retire"):
            action = actions.add_parser(name, add_help=False)
            action.add_argument("--cwd", required=True)
            if name == "status":
                action.add_argument("--client", required=True, choices=("codex",))
                action.add_argument("--session", required=True)
                action.add_argument("--prepare-observability", action="store_true")
        title = actions.add_parser("title", add_help=False)
        title.add_argument("title_action", choices=("configure", "resume", "status", "sync"))
        title.add_argument("--cwd", required=True)
        evidence = commands.add_parser("evidence", add_help=False)
        evidence_actions = evidence.add_subparsers(dest="action", required=True)
        for name in ("plan", "status", "check", "reconcile-check", "verify", "cleanup"):
            action = evidence_actions.add_parser(name, add_help=False)
            action.add_argument("--cwd", required=True)
            if name == "status":
                action.add_argument("--client", required=True, choices=("codex",))
                action.add_argument("--session", required=True)
        review = evidence_actions.add_parser("review", add_help=False)
        review_actions = review.add_subparsers(dest="review_action", required=True)
        for name in ("prepare", "collect", "assess"):
            action = review_actions.add_parser(name, add_help=False)
            action.add_argument("--cwd", required=True)
        delivery = commands.add_parser("delivery", add_help=False)
        delivery_actions = delivery.add_subparsers(dest="action", required=True)
        for name in ("status", "pending"):
            action = delivery_actions.add_parser(name, add_help=False)
            action.add_argument("--cwd", required=True)
            action.add_argument("--client", required=True, choices=("codex",))
            if name == "status":
                action.add_argument("--session", required=True)
            else:
                action.add_argument("--cursor")
                action.add_argument("--limit", type=int, default=32)
        delegation = commands.add_parser("delegation", add_help=False)
        delegation_actions = delegation.add_subparsers(dest="delegation_action", required=True)
        for name in ("plan", "prepare", "observe", "reconcile", "status"):
            action = delegation_actions.add_parser(name, add_help=False)
            action.add_argument("--cwd", required=True)
            if name == "status":
                action.add_argument("--client", required=True, choices=("codex",))
                action.add_argument("--session", required=True)
                action.add_argument("--compact", action="store_true")
        workstreams = delegation_actions.add_parser("workstreams", add_help=False)
        workstream_actions = workstreams.add_subparsers(dest="workstream_action", required=True)
        for name in ("enable", "disable", "status", "consume-dispatch", "prepare-continuation",
                     "consume-continuation", "record-continuation", "resource-run", "resource-status",
                     "resource-release", "resource-cancel"):
            workstream_actions.add_parser(name, add_help=False).add_argument("--cwd", required=True)
        guidance = delegation_actions.add_parser("guidance", add_help=False)
        guidance_actions = guidance.add_subparsers(dest="guidance_action", required=True)
        for name in ("status", "claim", "record"):
            guidance_actions.add_parser(name, add_help=False).add_argument("--cwd", required=True)
        coordinator = delegation_actions.add_parser("coordinator", add_help=False)
        coordinator_actions = coordinator.add_subparsers(dest="coordinator_action", required=True)
        coordinator_actions.add_parser("reserve", add_help=False).add_argument("--cwd", required=True)
        doctor = commands.add_parser("doctor", add_help=False)
        doctor.add_argument("--cwd", required=True)
        doctor.add_argument("--client", required=True, choices=("codex",))
        doctor.add_argument("--executable", required=True)
        onboard = commands.add_parser("onboard", add_help=False)
        onboarding = onboard.add_subparsers(dest="action", required=True)
        for name in ("inspect", "plan", "apply", "resume", "rollback"):
            action = onboarding.add_parser(name, add_help=False)
            action.add_argument("--plan-dir", required=True)
            if name == "inspect":
                action.add_argument("--cwd", required=True)
        upgrade = commands.add_parser("upgrade", add_help=False)
        upgrade.add_argument("action", choices=("status", "record"))
        args = parser.parse_args()
        if args.command == "upgrade":
            from runtime import package_versions
            report = (package_versions.status if args.action == "status" else package_versions.record)(PACKAGE)
            print(json.dumps(report))
            return 0 if report["ok"] else 1
        if args.command == "onboard":
            from runtime.onboarding import MAX_REQUEST, inspect_project, read_request, record_plan
            request = read_request(sys.stdin.buffer.read(MAX_REQUEST + 1))
            if args.action == "inspect":
                report = inspect_project(Path(args.cwd), Path(args.plan_dir), request)
            elif args.action == "plan":
                report = record_plan(Path(args.plan_dir), request)
            else:
                from runtime.changes import apply_plan, resume_plan, rollback_plan
                operation = {"apply": apply_plan, "resume": resume_plan, "rollback": rollback_plan}
                report = operation[args.action](Path(args.plan_dir), request)
            print(json.dumps(report))
            return report["exit_code"]
        if args.command == "delegation":
            if args.delegation_action == "status":
                client = args.client
            else:
                request = json_object(sys.stdin.buffer.read(MAX_INPUT + 1))
                if args.delegation_action == "guidance":
                    if request.get("client_surface") not in {"codex-cli", "codex-desktop"}:
                        raise StartupError("Expected a supported native guidance surface")
                    client = "codex"
                else:
                    client = request.get("client")
                    if client != "codex":
                        raise StartupError("Expected a supported native task client")
        elif (args.command == "task" and args.action in ("start", "update", "retire", "title") or
                args.command == "evidence" and args.action != "status"):
            request = json_object(sys.stdin.buffer.read(MAX_INPUT + 1))
            client = request.get("client")
            if client != "codex":
                raise StartupError("Expected a supported native task client")
        else:
            client = args.client
        if args.command == "doctor":
            from runtime.doctor import inspect_runtime
            unavailable = False
            try:
                acquisition = acquire_project_entry(SimpleNamespace(client=client, cwd=Path(args.cwd)), PACKAGE)
            except (StartupError, ImportError, ValueError):
                acquisition = None
                unavailable = True
            report = inspect_runtime(None, args.executable, acquisition=acquisition, enrollment_unavailable=unavailable)
            print(json.dumps(report))
            return report["exit_code"]
        acquisition = acquire_project_entry(SimpleNamespace(client=client, cwd=Path(args.cwd)), PACKAGE)
        scope = _entry_scope(acquisition)
        if scope is None:
            raise StartupError("The requested checkout is not enrolled for this client")
        if scope.runtime is None:
            raise StartupError("Runtime state is not configured for this checkout")
        if args.command == "delivery":
            from runtime import delivery
            report = (delivery.pending(scope, cursor=args.cursor, limit=args.limit) if args.action == "pending" else
                      delivery.operate(scope, "status", dict(client=client, session_id=args.session)))
            print(json.dumps(report))
            return 0
        if args.command == "task" and args.action == "title":
            from runtime.title_sync import operate
            report = operate(scope, args.title_action, request, force=True)
            print(json.dumps(report))
            return 0
        if args.command == "delegation":
            from runtime import delegation
            if args.delegation_action == "workstreams":
                from runtime import workstreams
                action = args.workstream_action
                if action.startswith("resource-"):
                    from runtime import resource_execution
                    report = resource_execution.operate(scope, resource_execution.host_directory(),
                        action.removeprefix("resource-"), request)
                elif action in {"enable", "disable", "status"}:
                    report = workstreams_control(scope, action, request)
                else:
                    operation = {"consume-dispatch": workstreams.consume_dispatch,
                        "prepare-continuation": delegation.prepare_continuation,
                        "consume-continuation": delegation.consume_continuation,
                        "record-continuation": delegation.record_continuation}
                    report = operation[action](scope, request)
                    if action == "prepare-continuation":
                        from runtime.worker_visibility import prepare_managed
                        report = prepare_managed(scope.repository_root, request.get("session_id"), report,
                                                 prompt=request.get("prompt"))
            elif args.delegation_action == "guidance":
                report = {"status": delegation.guidance_status, "claim": delegation.claim_guidance,
                          "record": delegation.record_guidance}[args.guidance_action](scope, request)
            elif args.delegation_action == "status":
                report = delegation.delegation_status(scope, client, args.session, compact=args.compact)
            else:
                if args.delegation_action in {"prepare", "coordinator"} and {"workstream_revision", "workstream_limits"} & request.keys():
                    raise StartupError("Combined Workstreams fields cannot create assignments; use ordinary delegation and continuation_limits for native workers")
                operation = {"plan": delegation.plan_delegation, "prepare": delegation.prepare_assignment,
                             "observe": delegation.observe_assignment, "reconcile": delegation.reconcile_assignment,
                             "coordinator": delegation.reserve_coordinator}
                report = operation[args.delegation_action](scope, request)
                if args.delegation_action == "prepare":
                    from runtime.worker_visibility import prepare_managed
                    report = prepare_managed(scope.repository_root, request.get("session_id"), report)
            if not (args.delegation_action == "status" and args.compact):
                report = title_report(scope, client, request.get("session_id") if args.delegation_action != "status" else args.session, report)
            print(json.dumps(report))
            return 0 if report.get("ok", True) else 1
        if args.command == "evidence":
            from runtime import evidence
            operations = {"plan": evidence.plan_evidence, "check": evidence.run_check, "reconcile-check": evidence.reconcile_check, "verify": evidence.verify_completion, "cleanup": evidence.cleanup_evidence,
                          "prepare": evidence.prepare_review, "collect": evidence.collect_review, "assess": evidence.assess_review}
            report = (evidence.evidence_status(scope, client, args.session) if args.action == "status" else
                      operations[args.review_action if args.action == "review" else args.action](scope, request))
            if args.action == "review" and args.review_action == "prepare":
                from runtime.worker_visibility import prepare_managed
                report = prepare_managed(scope.repository_root, request.get("session_id"), report)
            report = title_report(scope, client, request.get("session_id") if args.action != "status" else args.session, report)
            print(json.dumps(report))
            return 0
        from runtime.state import start_task, update_task, task_status, retire_task
        if args.action == "status":
            report = task_status(scope, client, args.session)
            if args.prepare_observability:
                try:
                    from runtime.observability import prepare_task_status
                    preparation = {"status": "prepared", "data": prepare_task_status(report, enabled=True)}
                except Exception:
                    # Optional preparation must preserve the owner's ordinary status result.
                    preparation = {"status": "unavailable"}
                report = dict(report, observability_preparation=preparation)
        else:
            report = {"start": start_task, "update": update_task, "retire": retire_task}[args.action](scope, request)
        report = title_report(scope, client, request.get("session_id") if args.action != "status" else args.session, report)
        print(json.dumps(report))
        return 0
    except StartupError as error:
        reason = str(error)
    except (ImportError, ValueError, TypeError, KeyError, OSError, RecursionError, subprocess.TimeoutExpired):
        reason = "Invalid task input, enrollment or local runtime"
    print(json.dumps({"ok": False, "error": reason}))
    return 1


def main():
    control = len(sys.argv) > 1 and sys.argv[1] in {"task", "doctor", "onboard", "evidence", "delivery", "delegation", "upgrade"}
    if sys.version_info < (3, 11):
        print(json.dumps({"ok": False, "error": "Vibe Coding Workflow requires Python 3.11+"} if control else
                         {"systemMessage": "Vibe Coding Workflow requires Python 3.11+ on the hook PATH."}))
        return 1 if control else 0
    if control:
        return control_main()
    requested_event = sys.argv[2] if len(sys.argv) == 3 else None
    from runtime.adapters import claude_code, codex, session_start
    from runtime.engine import HookResult
    from runtime.startup import MAX_INPUT, RuntimeScopeError, StartupError, json_object, orient, acquire_project_entry, _entry_scope, qualification_only
    runtime_kind = requested_event if requested_event in codex.RUNTIME_EVENTS else None

    def failure(reason):
        if runtime_kind is not None:
            return codex.runtime_diagnostic(runtime_kind, reason)
        return session_start.diagnostic(reason)

    deadline = time.monotonic() + 4
    try:
        adapters = {"codex": codex, "claude-code": claude_code}
        if len(sys.argv) != 3 or sys.argv[1] not in adapters:
            raise StartupError("Expected a supported client and event")
        if requested_event != "SessionStart" and (sys.argv[1] != "codex" or runtime_kind is None):
            raise StartupError("Unsupported client runtime event")
        adapter = adapters[sys.argv[1]]
        payload = json_object(sys.stdin.buffer.read(MAX_INPUT + 1))
        if payload.get("hook_event_name") != requested_event:
            raise StartupError("CLI event does not match the payload event")
        if requested_event == "SessionStart":
            start = adapter.normalize(payload)
            # SessionStart uses managed scope only for restricted qualification context.
            acquisition = acquire_project_entry(start, PACKAGE, managed=None, deadline=deadline)
            output = adapter.render(orient(start, PACKAGE, acquisition=acquisition))
            if start.client == "codex":
                try:
                    scope = _entry_scope(acquisition, managed=False)
                    if scope is not None and scope.runtime is not None and not qualification_only(scope):
                        from runtime.title_sync import repair
                        presentation = repair(scope, start.client, payload.get("session_id"), force=True)
                        if presentation.get("notification"):
                            output = dict(output or {})
                            output["systemMessage"] = (output.get("systemMessage", "") + " " + presentation["notification"]).strip()
                except Exception:
                    # Optional title repair must preserve the startup result.
                    pass
            if output is not None and time.monotonic() < deadline:
                try:
                    from runtime import package_versions
                    warning = package_versions.drift_warning(PACKAGE, start.client)
                    if warning:
                        output = package_versions.append_warning(output, warning)
                except Exception:
                    # The optional drift warning must preserve the startup result.
                    pass
        else:
            event = codex.normalize_runtime(payload)
            acquisition = acquire_project_entry(event, PACKAGE, deadline=deadline)
            scope = _entry_scope(acquisition, managed=False)
            result = HookResult(event.kind)
            if scope is not None and scope.runtime is not None:
                from runtime.state import process_event
                result = process_event(scope, event, deadline=deadline)
            else:
                from runtime.state import retained_patch_guard
                result = retained_patch_guard(event, deadline=deadline) or result
            output = codex.render_runtime(result)
            if scope is not None and scope.runtime is not None and event.kind in {"PostToolUse", "Stop"}:
                from runtime.title_sync import repair
                presentation = repair(scope, event.client, event.session_id)
                if presentation.get("notification"):
                    output = dict(output or {})
                    output["systemMessage"] = (output.get("systemMessage", "") + " " + presentation["notification"]).strip()
            if scope is not None and event.kind == "PostToolUse":
                output = codex.with_output_notice(payload, output)
    except RuntimeScopeError as error:
        from runtime.state import retained_patch_guard
        retained = retained_patch_guard(event, deadline=deadline) if runtime_kind == "PreToolUse" else None
        output = codex.render_runtime(retained) if retained is not None else failure(str(error))
    except StartupError as error:
        output = failure(str(error))
    except (ValueError, OSError, RecursionError, subprocess.TimeoutExpired):
        # Input and OS exception text may contain private paths or record contents.
        output = failure("invalid input, enrollment or local runtime; inspect the installed client instructions")
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
