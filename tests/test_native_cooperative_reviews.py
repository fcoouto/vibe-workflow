"""Cooperative review observation and review assignments are bound to exact native lineage."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import types
import unittest
from unittest.mock import patch

from runtime import doctor, evidence
from runtime.adapters import codex
from runtime.startup import StartupError
from tests.fixture_native_reviews import NativeReviewFixture


class NativeCooperativeReviewTests(NativeReviewFixture, unittest.TestCase):
    def test_guarded_child_lineage_uses_only_the_current_child_rollout(self):
        parent = "01a1a8b7-cf58-7421-8784-50419f70173e"
        child = "01a1a8bb-e785-79d0-b2a1-be3d32d3ce57"
        parent_turn = "01a1a8ba-7c00-7131-b423-b5827ded1768"
        child_turn = "01a1a8bb-e7a7-7720-a018-8fbeb4439e39"
        task_name = "worker_" + "a" * 32
        agent_path = "/root/" + task_name
        home = self.root / "native-home"
        sessions = home / "sessions"
        sessions.mkdir(parents=True, mode=0o700)
        child_path = sessions / f"rollout-{child}.jsonl"
        event = types.SimpleNamespace(client="codex", kind="PreToolUse", tool_name="apply_patch",
            session_id=child, turn_id=child_turn, cwd=self.root, transcript_path=child_path)

        def row(kind, **payload):
            return dict(timestamp="2026-11-04T22:44:09.000Z", type=kind, payload=payload)

        meta = dict(cwd=str(self.root), originator="codex_exec", cli_version="0.165.0")
        child_meta = row("session_meta", id=child, **meta, agent_path=agent_path,
            source=dict(subagent=dict(thread_spawn=dict(parent_thread_id=parent, depth=1, agent_path=agent_path))))
        child_start = row("event_msg", type="task_started", turn_id=child_turn, turn_attribution=dict(
            turn_id=child_turn, turn_trigger="exec", parent_turn_id=parent_turn,
            root_turn_id=parent_turn, initiating_agent_path="/root"))
        context = row("turn_context", turn_id=child_turn, cwd=str(self.root),
                      multi_agent_version="v2", model="gpt-6-luna", effort="high")

        def write(path, rows):
            path.write_text("".join(json.dumps(item) + "\n" for item in rows))
            path.chmod(0o600)

        child_rows = [child_meta, child_start, context]
        write(child_path, child_rows)
        with patch.dict(os.environ, CODEX_HOME=str(home)), patch.object(codex, "_native_reader",
                side_effect=AssertionError("The child hook must not launch another Codex process")):
            observe = lambda: codex.guarded_child_lineage(event, deadline=time.monotonic() + 2)
            self.assertEqual(observe(), dict(parent_session_id=parent, task_name=task_name,
                native_version="0.165.0", model="gpt-6-luna", effort="high"))
            wrong_turn = copy.deepcopy(context)
            wrong_turn["payload"]["turn_id"] = parent_turn
            for name, child_records in (
                ("aborted", child_rows + [row("event_msg", type="turn_aborted")]),
                ("wrong turn", [child_meta, child_start, wrong_turn]),
                ("duplicate metadata", child_rows + [child_meta]),
            ):
                with self.subTest(name=name):
                    write(child_path, child_records)
                    with self.assertRaises(StartupError):
                        observe()
            write(child_path, child_rows)
            alias = sessions / "linked" / child_path.name
            alias.parent.symlink_to(sessions, target_is_directory=True)
            event.transcript_path = alias
            with self.assertRaises(StartupError):
                observe()

    def test_cooperative_observation_accounts_for_the_exact_patch_and_read_only_reviewer(self):
        responses, binding = self.worker_fixture()
        command = '*** Begin Patch\n*** Update File: canary.py\n@@\n-return 0\n+return 42\n*** End Patch'
        raw = self.cooperative_rollout(responses, [dict(type='custom_tool_call', call_id='patch', name='apply_patch', input=command),
            dict(type='custom_tool_call_output', call_id='patch', output='Success')])
        observed = self.observe_worker(responses, binding, cooperative=True)
        self.assertEqual(observed['activity'], dict(source_digest=hashlib.sha256(raw).hexdigest(),
            calls=[dict(id='patch', tool='apply_patch', input_digest=codex.patch_input_fingerprint(command))]))
        self.assertFalse(observed['receipt']['descendants_stopped'])
        self.assertNotIn('activity', self.observe_worker(responses, binding))
        responses, binding = self.review_assignment_fixture()
        self.cooperative_rollout(responses, [])
        self.assertEqual(self.observe_review_assignment(responses, binding, cooperative=True)['activity']['calls'], [])

    def test_cooperative_observation_binds_wrapped_wait_and_patch_to_nested_ids(self):
        responses, binding = self.worker_fixture()
        wait = dict(marker=str(self.root / 'ready'), python=str(Path(sys.executable).resolve()), seconds=5)
        binding['foreground_wait'] = wait
        args = codex.foreground_wait_args(wait, self.scope.repository_root)
        command = '*** Begin Patch\n*** Update File: canary.py\n@@\n-return 0\n+return 42\n*** End Patch'

        def wrapped(outer, tool, value, item, result):
            return [dict(type='custom_tool_call', name='exec', call_id=outer,
                input=f'text(await tools.{tool}({json.dumps(value)}));\n'),
                dict(type='item_completed', thread_id='child', turn_id='child-turn', item=item),
                dict(type='custom_tool_call_output', call_id=outer, output=[
                    dict(type='input_text', text='Script completed\nWall time 0.3 seconds\nOutput:\n'),
                    dict(type='input_text', text=json.dumps(result)),
                ])]

        wait_result = dict(chunk_id='1234ab', wall_time_seconds=0.05, exit_code=0, original_token_count=3,
            output='D6_READY\n')
        wait_item = dict(type='CommandExecution', id='inner-wait', status='completed', exit_code=0,
            source='unified_exec_startup', aggregated_output='D6_READY\n', cwd=Path(args['workdir']).as_uri(),
            command=[args['shell'], '-c', args['cmd']])
        patch_item = dict(type='FileChange', id='inner-patch', status='completed', changes={'canary.py': {'type': 'update'}},
            stdout='Success. Updated the following files:\nM canary.py\n', stderr='')
        wait_rows = wrapped('outer-wait', 'exec_command', args, wait_item, wait_result)
        patch_rows = wrapped('outer-patch', 'apply_patch', command, patch_item, {})
        patch_rows[0]['input'] = patch_rows[0]['input'].replace('));\n', '))\n')
        rows = [dict(type='additional_tools', role='developer', tools=[]),
                dict(type='thread_settings_applied', thread_id='child', thread_settings={}), *wait_rows, *patch_rows]
        projection = dict(type='commandExecution', id='inner-wait', status='completed', exitCode=0,
                          source='unifiedExecStartup', aggregatedOutput='D6_READY\n', cwd=args['workdir'])
        patch_projection = dict(type='fileChange', id='inner-patch', status='completed')
        responses['history'] = [dict(turnId='child-turn', item=item) for item in (projection, patch_projection)] + responses['items']['data']
        raw = self.cooperative_rollout(responses, rows)
        observed = self.observe_worker(responses, binding, cooperative=True)
        self.assertEqual(observed['activity'], dict(source_digest=hashlib.sha256(raw).hexdigest(), calls=[
            dict(id='inner-wait', tool='exec_command', input_digest=evidence._digest(dict(tool_name='exec_command', tool_input=args))),
            dict(id='inner-patch', tool='apply_patch', input_digest=codex.patch_input_fingerprint(command))]))

        for name, activity in (
            ('extra JS', [*wait_rows, dict(patch_rows[0], input=patch_rows[0]['input'] + 'text(1);'), *patch_rows[1:]]),
            ('wrong wait args', [*wrapped('outer-wait', 'exec_command', dict(args, tty=True), wait_item, wait_result), *patch_rows]),
            ('resumable', [*wrapped('outer-wait', 'exec_command', args, wait_item, dict(wait_result, session_id=42)), *patch_rows]),
            ('extra nested item', [*wait_rows[:2], dict(wait_rows[1], item=dict(wait_item, id='another')), wait_rows[2], *patch_rows]),
            ('early patch', [*wait_rows[:2], *patch_rows, wait_rows[2]]),
            ('missing nested patch', [*wait_rows, patch_rows[0], patch_rows[2]]),
        ):
            self.cooperative_rollout(responses, activity)
            with self.subTest(name=name), self.assertRaises(StartupError):
                self.observe_worker(responses, binding, cooperative=True)
        self.cooperative_rollout(responses, rows)
        projection['id'] = 'outer-wait'
        with self.assertRaises(StartupError):
            self.observe_worker(responses, binding, cooperative=True)

    def test_cooperative_activity_allows_only_the_initial_v2_trigger_metadata(self):
        def row(kind, **payload):
            return dict(timestamp='fixture', type=kind, payload=payload)

        start = [row('session_meta', id='child'), row('event_msg', type='task_started', turn_id='child-turn'),
                 row('turn_context', turn_id='child-turn', multi_agent_version='v2'),
                 row('event_msg', type='thread_settings_applied', thread_id='child', thread_settings={})]
        trigger = row('inter_agent_communication_metadata', trigger_turn=True)
        call = row('response_item', type='custom_tool_call', call_id='patch', name='apply_patch', input='patch')
        output = row('response_item', type='custom_tool_call_output', call_id='patch', output='Success')
        completed = row('event_msg', type='task_complete', turn_id='child-turn', last_agent_message='report')

        def observe(rows, *, review=False):
            raw = b''.join(json.dumps(item).encode() + b'\n' for item in rows)
            return codex._cooperative_activity(raw, path=self.root / 'rollout.jsonl', child='child',
                turn='child-turn', result='report', review=review)['calls']

        self.assertEqual(observe([*start, trigger, call, output, completed]), [
            dict(id='patch', tool='apply_patch', input_digest=codex.patch_input_fingerprint('patch'))])
        self.assertEqual(observe([*start, trigger, completed], review=True), [])
        for rows in (
            [*start[:-1], trigger, start[-1], call, output, completed],
            [*start, trigger, trigger, call, output, completed],
            [*start, row('inter_agent_communication_metadata', trigger_turn=1), call, output, completed],
            [*start, row('inter_agent_communication_metadata', trigger_turn=True, extra=True), call, output, completed],
            [*start, row('inter_agent_communication', trigger_turn=True), call, output, completed],
            [*start, call, trigger, output, completed],
            [*start, call, output, completed, trigger],
        ):
            with self.subTest(rows=rows), self.assertRaises(doctor.ObservationError):
                observe(rows)

    def test_cooperative_observation_rejects_unknown_or_unfinished_activity(self):
        call = dict(type='custom_tool_call', call_id='patch', name='apply_patch', input='patch')
        output = dict(type='custom_tool_call_output', call_id='patch', output='Success')
        cases = [([call], True), ([output, call], True), ([call, output, output], True),
                 ([call, output], False), ([dict(call, name='exec'), output], True),
                 ([dict(type='function_call', call_id='shell', name='exec_command', arguments='{}')], True)]
        for activity, terminal in cases:
            responses, binding = self.worker_fixture()
            self.cooperative_rollout(responses, activity, terminal=terminal)
            with self.subTest(activity=activity, terminal=terminal), self.assertRaises(StartupError):
                self.observe_worker(responses, binding, cooperative=True)

    def test_cooperative_observation_accounts_for_the_pinned_wait_before_patching(self):
        responses, binding = self.worker_fixture()
        wait = dict(marker=str(self.root / 'ready'), python=str(Path(sys.executable).resolve()), seconds=5)
        binding['foreground_wait'] = wait
        args = codex.foreground_wait_args(wait, self.scope.repository_root)
        self.assertEqual(args['yield_time_ms'], 10000)
        self.assertIs(args['login'], False)
        self.assertIs(args['tty'], False)
        call = dict(type='function_call', call_id='wait', name='exec_command', arguments=json.dumps(args))
        result = dict(type='function_call_output', call_id='wait', output=(
            'Chunk ID: 1234ab\nWall time: 0.0500 seconds\nProcess exited with code 0\n'
            'Original token count: 3\nOutput:\nD6_READY\n'))
        terminal = dict(type='CommandExecution', id='wait', status='completed', exit_code=0,
                        source='unified_exec_startup', aggregated_output='D6_READY\n',
                        command=['/bin/sh', '-c', args['cmd']], cwd=Path(args['workdir']).as_uri())
        projection = dict(type='commandExecution', id='wait', status='completed', exitCode=0,
                          source='unifiedExecStartup', aggregatedOutput='D6_READY\n', cwd=args['workdir'])
        responses['history'] = [dict(turnId='child-turn', item=projection), *responses['items']['data']]
        command = '*** Begin Patch\n*** Update File: canary.py\n@@\n-return 0\n+return 42\n*** End Patch'
        patch_call = dict(type='custom_tool_call', call_id='patch', name='apply_patch', input=command)
        patch_result = dict(type='custom_tool_call_output', call_id='patch', output='Success')
        completed = dict(type='item_completed', thread_id='child', turn_id='child-turn', item=terminal)
        for wait_records in ([call, completed, result], [call, result, completed]):
            self.cooperative_rollout(responses, [*wait_records, patch_call, patch_result])
            observed = self.observe_worker(responses, binding, cooperative=True)
            self.assertEqual([(item['id'], item['tool']) for item in observed['activity']['calls']],
                             [('wait', 'exec_command'), ('patch', 'apply_patch')])
            self.assertEqual(observed['activity']['calls'][0]['input_digest'],
                             evidence._digest(dict(tool_name='exec_command', tool_input=args)))
        cases = {
            'changed arguments': ([dict(call, arguments=json.dumps(dict(args, login=True))), completed, result], projection),
            'resumable result': ([call, completed, dict(result, output=result['output'].replace(
                'Original token count:', 'Process running with session ID 42\nOriginal token count:'))], projection),
            'missing terminal event': ([call, result], projection),
            'terminal before call': ([completed, call, result], projection),
            'terminal after patch': ([call, result, patch_call, patch_result, completed], projection),
            'early patch': ([call, completed, patch_call, patch_result, result], projection),
            'inconsistent raw command': ([call, dict(completed, item=dict(terminal, command=['/bin/sh', '-c', 'true'])), result],
                                         projection),
            'prefixed shell command': ([call, dict(completed, item=dict(terminal,
                command=['/bin/sh', '-c', 'true', '-c', args['cmd']])), result], projection),
            'changed shell': ([call, dict(completed, item=dict(terminal,
                command=['/bin/zsh', '-c', args['cmd']])), result], projection),
            'changed raw cwd': ([call, dict(completed, item=dict(terminal, cwd='file:///other')), result], projection),
            'duplicate wait': ([call, completed, result, dict(call, call_id='other')], projection),
            'missing projection': ([call, completed, result], None),
            'failed projection': ([call, completed, result], dict(projection, status='failed', exitCode=1)),
        }
        for name, (activity, projected) in cases.items():
            with self.subTest(name=name), self.assertRaises(StartupError):
                responses['history'] = list(responses['items']['data'])
                if projected is not None:
                    responses['history'].insert(0, dict(turnId='child-turn', item=projected))
                self.cooperative_rollout(responses, activity)
                self.observe_worker(responses, binding, cooperative=True)

    def test_foreground_wait_command_requires_a_regular_marker(self):
        marker = self.root / "private ' marker"
        wait = dict(marker=str(marker), python=str(Path(sys.executable).resolve()), seconds=1)
        args = codex.foreground_wait_args(wait, self.scope.repository_root)

        def run_wait():
            return subprocess.run([args['shell'], '-c', args['cmd']], cwd=args['workdir'],
                                  capture_output=True, text=True, timeout=3)

        absent = run_wait()
        self.assertEqual((absent.returncode, absent.stdout), (1, ''))
        marker.touch()
        ready = run_wait()
        self.assertEqual((ready.returncode, ready.stdout), (0, 'D6_READY\n'))
        marker.rename(self.root / 'old')
        marker.symlink_to(self.root / 'old')
        linked = run_wait()
        self.assertEqual((linked.returncode, linked.stdout), (1, ''))

    def test_cooperative_observation_rejects_retry_and_replaced_turn(self):
        for retry, changed in ((True, False), (False, True), ('unknown', False)):
            responses, binding = self.worker_fixture()
            self.cooperative_rollout(responses, [])
            responses['turns']['data'][0]['willRetry'] = retry
            if changed:
                responses['turns_second'] = dict(data=[dict(id='later', status='completed', error=None)])
            with self.subTest(retry=retry, changed=changed), self.assertRaises(StartupError):
                self.observe_worker(responses, binding, cooperative=True)

    def test_cooperative_observation_rejects_projected_activity_missing_from_rollout(self):
        for item in (dict(type='commandExecution', status='completed', exitCode=0),
                     dict(type='fileChange', status='completed')):
            responses, binding = self.worker_fixture()
            self.cooperative_rollout(responses, [])
            responses['history'] = [dict(turnId='child-turn', item=dict(id='unaccounted', **item)),
                                    *responses['items']['data']]
            with self.subTest(item=item), self.assertRaises(StartupError):
                self.observe_worker(responses, binding, cooperative=True)

    def test_cooperative_observation_rechecks_the_same_raw_source(self):
        responses, binding = self.worker_fixture()
        self.cooperative_rollout(responses, [])
        with self.assertRaises(StartupError):
            self.observe_worker(responses, binding, mode='changed-rollout', cooperative=True)

    def test_review_assignment_observation_preserves_unknown_and_incomplete_lifecycle(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        for status in (None, "inProgress", "interrupted", "failed"):
            responses, binding = self.review_assignment_fixture()
            if status is None:
                responses["turns"]["data"] = []
            else:
                responses["turns"]["data"][0]["status"] = status
            observed = self.observe_review_assignment(responses, binding)
            receipt = observed["receipt"]
            self.assertEqual(receipt["version"], 2)
            self.assertEqual(receipt["task_name"], "review_attempt")
            self.assertEqual(receipt["assignment_digest"], "d" * 64)
            self.assertEqual(receipt["turn_status"], status or "unknown")
            self.assertEqual(receipt["worker_turn_id"], "child-turn" if status else None)
            self.assertIsNone(receipt["result_item_id"])
            self.assertIsNone(receipt["result_digest"])
            self.assertIsNone(observed["result"])
            self.assertFalse(receipt["descendants_stopped"])
            self.assertEqual(receipt["prompt_delivery"], "unverified")
        methods = {json.loads(line)["method"] for line in self.log.read_text().splitlines()}
        self.assertLessEqual(methods, {"initialize", "initialized", "thread/read", "thread/items/read", "thread/turns/list"})

    def test_completed_review_assignment_returns_raw_result_and_d6_binding_without_d5_acceptance(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        responses, binding = self.review_assignment_fixture()
        responses["parent"]["thread"]["modelProvider"] = "parent-provider"
        responses["child"]["thread"]["modelProvider"] = "review-provider"
        for text in (self.text, "plain review output without a D5 report envelope"):
            responses["items"]["data"][0]["item"]["text"] = text
            observed = self.observe_review_assignment(responses, binding)
            receipt = observed["receipt"]
            self.assertEqual(observed["result"], text)
            self.assertEqual(receipt["result_digest"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(receipt["assignment_digest"], "d" * 64)
            self.assertEqual(receipt["candidate_digest"], "b" * 64)
            self.assertEqual(receipt["worker_id"], "child")
            self.assertEqual(receipt["worker_turn_id"], "child-turn")
            self.assertEqual(receipt["result_item_id"], "final")
            self.assertEqual(receipt["agent_path"], "/root/review_attempt")
            self.assertEqual(receipt["parent_model_provider"], "parent-provider")
            self.assertEqual(receipt["worker_model_provider"], "review-provider")
            self.assertEqual(receipt["requested_model"], "requested-model")
            self.assertEqual(receipt["configured_model"], "configured-model")
            self.assertEqual(receipt["spawn_observed_model"], "gpt-6-luna")
            self.assertIsNone(receipt["execution_model"])
            self.assertEqual(self.observe_review_assignment(responses, binding), observed)

    def test_review_assignment_rejects_wrong_marker_binding_and_child(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        for label in ("worker marker", "wrong native marker", "wrong binding", "changed binding", "wrong child", "changed turn", "oversized result"):
            responses, binding = self.review_assignment_fixture()
            changed = None
            if label == "worker marker": binding["task_name"] = "worker_attempt"
            elif label == "wrong native marker": responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/worker_attempt"
            elif label == "wrong binding": binding["attempt_id"] = "other"
            elif label == "changed binding": changed = dict(binding, candidate_digest="c" * 64)
            elif label == "wrong child": responses["child"]["thread"]["parentThreadId"] = "other"
            elif label == "changed turn": responses["turns_second"] = dict(data=[dict(id="other", status="completed", error=None)])
            else: responses["items"]["data"][0]["item"]["text"] = "x" * (64 * 1024 + 1)
            with self.subTest(label=label), self.assertRaises(StartupError):
                self.observe_review_assignment(responses, binding, changed)
        responses, binding = self.review_assignment_fixture()
        with self.assertRaises(StartupError):
            self.observe_worker(responses, binding)


if __name__ == "__main__":
    unittest.main()
