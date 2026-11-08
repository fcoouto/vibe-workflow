"""Raw native protocol fixtures shared by the native behavior record tests."""

import hashlib
import json


REJECTION = 'patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings'
PATCH = '*** Begin Patch\n*** Update File: public-canary.txt\n@@\n-before\n+after\n*** End Patch'
CONTEXT = 'Restricted qualification context: observe this disposable target only.'


def row(kind, payload):
    return dict(timestamp='2026-11-02T10:00:00Z', type=kind, payload=payload)


def encoded(rows):
    return b''.join(json.dumps(value, separators=(',', ':')).encode()+b'\n' for value in rows)


def session():
    return row('session_meta', dict(id='native-thread', cwd='/public/disposable'))


def started():
    return row('event_msg', dict(type='task_started', turn_id='native-turn'))


def call(**changes):
    return row('response_item', dict(type='custom_tool_call', name='apply_patch', call_id='native-call', input=PATCH, **changes))


def output(text=REJECTION):
    return row('response_item', dict(type='custom_tool_call_output', call_id='native-call', output=text))


def bootstrap_reads():
    # Sanitized Mac rollout shape. The loader emitted both a result and a named notification.
    inputs = [
        'const r = await tools.exec_command({cmd:"python3 -I -B /package/scripts/read_instructions.py --batch bootstrap"}); text(r.output);\n',
        'const r = await tools.exec_command({cmd:"cat /public/disposable/.vibe-workflow/project.json"}); text(r.output);\n',
        'text(await drain({root, paths, exec:tools.exec_command, emit:notify}));\n',
    ]
    rows = []
    for index, script in enumerate(inputs):
        call_id = f'bootstrap-read-{index}'
        rows.extend([
            row('response_item', dict(type='custom_tool_call', name='exec', call_id=call_id, input=script)),
            row('response_item', dict(type='custom_tool_call_output', call_id=call_id, output=[
                dict(type='input_text', text='Script completed\nWall time 0.5 seconds\nOutput:\n'),
                dict(type='input_text', text='startup result'),
            ])),
        ])
    rows.append(row('response_item', dict(type='custom_tool_call_output', call_id=call_id, name='exec',
        output='Selected instructions: startup context')))
    return rows, [hashlib.sha256(script.encode()).hexdigest() for script in inputs]


def wrapped_patch_denial():
    patch = call()
    patch['payload'].update(name='exec', input='text(await tools.apply_patch(' + json.dumps(PATCH) + '));\n')
    denial = output([dict(type='input_text', text='Script failed\nWall time 0.5 seconds\nOutput:\n'),
        dict(type='input_text', text='Script error:\n' + REJECTION)])
    return patch, denial


def hook(*, text=CONTEXT, provenance=None):
    return row('response_item', dict(type='message', role='user', content=[dict(type='input_text',
        text='<hook_prompt hook_run_id="native-hook">'+text+'</hook_prompt>')],
        internal_chat_message_metadata_passthrough=dict(content_item_metadata=[dict(
            provenance=provenance or dict(type='harness', hook=True))])))
