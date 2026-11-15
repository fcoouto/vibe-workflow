"""Synthetic offline bundles from openai/codex ac4332c05b11e00ae775a24cb762edc05c5b5932."""

import json
from pathlib import Path

from tests.fixture_native_behavior import PATCH


TRACE_ID = '11111111-2222-4333-8444-555555555555'
REASON = 'Patching requires a WORKING editable bound task without applicable blockers'
DENIAL = f'Command blocked by PreToolUse hook: {REASON}. Command: {PATCH}'


def write_bundle(home, root, rollout, *, thread='native-thread', turn='native-turn',
                 outer='native-call', inner='exec-inner'):
    home, root, rollout = map(Path, (home, root, rollout))
    home.chmod(0o700)
    bundle = home / f'trace-{TRACE_ID}-{thread}'
    bundle.mkdir(mode=0o700)
    (bundle/'payloads').mkdir(mode=0o700)
    script = 'text(await tools.apply_patch(' + json.dumps(PATCH) + '));\n'
    manifest = dict(schema_version=1, trace_id=TRACE_ID, rollout_id=thread, root_thread_id=thread,
        started_at_unix_ms=1000, raw_event_log='trace.jsonl', payloads_dir='payloads')
    payloads = [
        dict(thread_id=thread, agent_path='/root', task_name=None, nickname=None, agent_role=None,
            session_source='exec', cwd=str(root), rollout_path=str(rollout), model='fixture',
            provider_name='fixture', approval_policy='never', sandbox_policy='read-only'),
        dict(tool_name='apply_patch', tool_namespace=None, payload=dict(type='custom', input=PATCH)),
        dict(type='error', error=DENIAL),
        dict(response={'Result':dict(cell_id='cell-1', content_items=[], stored_values={}, error_text=DENIAL)}),
        dict(response={'Result':dict(cell_id='cell-1', content_items=[], stored_values={}, error_text=DENIAL)}),
    ]
    def ref(ordinal, kind):
        return dict(raw_payload_id=f'raw_payload:{ordinal}', kind=dict(type=kind), path=f'payloads/{ordinal}.json')
    events = [
        dict(type='rollout_started', trace_id=TRACE_ID, root_thread_id=thread),
        dict(type='thread_started', thread_id=thread, agent_path='/root', metadata_payload=ref(1, 'session_metadata')),
        dict(type='codex_turn_started', codex_turn_id=turn, thread_id=thread),
        dict(type='code_cell_started', runtime_cell_id='cell-1', model_visible_call_id=outer, source_js=script),
        dict(type='tool_call_started', tool_call_id=inner, model_visible_call_id=None,
            code_mode_runtime_tool_id='1', requester=dict(type='code_cell', runtime_cell_id='cell-1'),
            kind=dict(type='apply_patch'), summary=dict(type='generic', label='apply_patch',
                input_preview=PATCH[:160], output_preview=None), invocation_payload=ref(2, 'tool_invocation')),
        dict(type='tool_call_ended', tool_call_id=inner, status='failed', result_payload=ref(3, 'tool_result')),
        dict(type='code_cell_initial_response', runtime_cell_id='cell-1', status='failed', response_payload=ref(4, 'tool_result')),
        dict(type='code_cell_ended', runtime_cell_id='cell-1', status='failed', response_payload=ref(5, 'tool_result')),
        dict(type='codex_turn_ended', codex_turn_id=turn, status='completed'),
    ]
    rows = []
    for seq, payload in enumerate(events, 1):
        rows.append(dict(schema_version=1, seq=seq, wall_time_unix_ms=1000+seq, rollout_id=thread,
            thread_id=None if seq == 1 else thread, codex_turn_id=turn if seq >= 3 else None, payload=payload))
    write_json(bundle/'manifest.json', manifest)
    write_events(bundle, rows)
    for ordinal, value in enumerate(payloads, 1):
        write_json(bundle/f'payloads/{ordinal}.json', value)
    return bundle, rows, script


def write_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def write_events(bundle, rows):
    path = bundle/'trace.jsonl'
    path.write_bytes(b''.join(json.dumps(row).encode()+b'\n' for row in rows))
    path.chmod(0o600)
