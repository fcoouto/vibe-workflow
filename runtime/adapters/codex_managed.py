"""Native guard observation; transport and parsing stay with the Codex adapter."""

import hashlib
import subprocess
import time

from runtime import delegation, onboarding as d3, setup_observation, startup, task_profile
from runtime.adapters import codex


def native_managed_tool(records, *, submitted_patch, turn_id, call_id, case_id, child_patch=False, raw=None):
    """Resolve hook identity only from direct calls or the supported native completion interval."""
    try:
        data = startup._canonical_plain(records)
        if (case_id not in {'pre-tool-enforcement', 'post-tool-handling'}
                or not isinstance(submitted_patch, str) or len(submitted_patch.encode()) > startup.MAX_INPUT
                or not submitted_patch.startswith('*** Begin Patch\n')
                or not submitted_patch.rstrip('\n').endswith('\n*** End Patch')
                or submitted_patch.count('*** Update File: ') != 1
                or not any(line.startswith(('+', '-')) for line in submitted_patch.splitlines())
                or turn_id not in data['turn_ids']):
            raise ValueError
        codex._review_id(call_id)
        rows = [row for row in data['records'] if row['payload']['type'] in {
            'custom_tool_call', 'custom_tool_call_output', 'function_call', 'function_call_output', 'local_shell_call'}]
        calls = [row for row in rows if row['payload'].get('call_id') == call_id]
        native_call_id = call_id
        if (case_id == 'post-tool-handling' and raw is not None
                and (child_patch or not calls or calls[0]['payload'].get('name') == 'exec')):
            pairs = codex._exec_wrapped_pairs(raw, data, turn_id, tools=('apply_patch',))
            matches = [(outer, pair) for outer, pair in pairs.items()
                if call_id in (outer, pair[0]['payload']['call_id'])]
            if matches:
                if len(matches) != 1:
                    raise ValueError
                native_call_id, pair = matches[0]
                inner = pair[0]['payload']['call_id']
                if (any(row['payload'].get('call_id') == inner for row in rows)
                        or sum(row['payload'].get('call_id') == native_call_id for row in rows) != 2):
                    raise ValueError
                calls, call_id = pair, inner
        if len(calls) != 2:
            raise ValueError
        call, output = calls
        item, result = call['payload'], output['payload']
        wrapped = case_id == 'pre-tool-enforcement' and not child_patch and item.get('name') == 'exec'
        if (call['turn_id'] != turn_id or output['turn_id'] != turn_id
                or item['type'] != 'custom_tool_call' or item.get('name') != ('exec' if wrapped else 'apply_patch')
                or item.get('namespace') not in (None, 'functions') or not wrapped and item.get('input') != submitted_patch
                or item.get('call_id') != call_id or result['type'] != 'custom_tool_call_output'
                or result.get('call_id') != call_id or result.get('name') not in (None, item['name'])
                or wrapped and result.get('namespace') not in (None, 'functions')):
            raise ValueError
        text = result.get('output')
        if case_id == 'pre-tool-enforcement':
            reason = 'Patching requires a WORKING editable bound task without applicable blockers'
            if not codex._native_patch_denied(item, text, submitted_patch,
                    rejection=f'Command blocked by PreToolUse hook: {reason}. Command: {submitted_patch}'):
                raise ValueError
            if wrapped:
                # A rejection envelope names only exec, not the nested hook invocation.
                raise codex.ObservationError('native_hook_correlation_unavailable')
        elif not (isinstance(text, str) or isinstance(text, list) and all(isinstance(part, dict)
                and part.get('type') == 'input_text' and isinstance(part.get('text'), str) for part in text)):
            raise ValueError
        fact = dict(thread_id=data['thread_id'], turn_id=turn_id, call_id=call_id,
            submitted_patch_sha256=hashlib.sha256(submitted_patch.encode()).hexdigest(), sources=[call['source'], output['source']])
        if native_call_id != call_id:
            fact['native_call_id'] = native_call_id
        if child_patch:
            if isinstance(text, list) and len(text) == 1:
                text = text[0]['text']
            if case_id != 'post-tool-handling' or not isinstance(text, str):
                raise ValueError
            fact['post_hook_fingerprint'] = codex._tool_fingerprint(dict(tool_name='apply_patch',
                tool_input=dict(command=submitted_patch), tool_response=text))
        return startup._canonical_freeze(fact)
    except (startup.StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise codex.ObservationError('native_managed_tool_unobserved') from None


def observe_managed_case(scope, task_ref, attempt_ref, case_id):
    projection = startup._canonical_plain(setup_observation.managed_observation_projection(
        scope, task_ref, attempt_ref, case_id=case_id))
    try:
        if case_id not in {'pre-tool-enforcement', 'post-tool-handling'}:
            raise codex.ObservationError('managed_case_pending')
        binding, selectors, probe = projection['binding'], projection['selectors'], projection['probe']
        parent_only = binding.get('kind') == 'parent'
        if (probe['patch'] is None or probe['before']['probe'] is None
                or not parent_only and not task_profile.native(binding['role'])):
            raise codex.ObservationError('managed_source_unqualified')
        now = time.monotonic()
        remaining = projection['attempt']['limits']['deadline']-time.time()
        deadline = now+min(codex.REVIEW_TIMEOUT, max(0, remaining))
        with codex._native_reader(codex.REVIEW_METHODS, deadline=deadline) as (observed_version, request):
            if parent_only:
                parent = codex._native_metadata(scope, request, binding['parent_session_id'], observed_version)
                codex._native_surface(parent, binding['client_surface'])
            else:
                assignment = projection['attempt']['assignment_id']
                prefix = 'review_' if task_profile.reviews(binding['role']) else 'worker_'
                parent, _, child, _, _ = codex._native_chain(scope, binding, assignment, prefix,
                    selectors['parent_turn_id'], selectors['spawn_item_id'], request, observed_version)
            if parent['id'] != task_ref['session_id'] or parent['id'] != binding['parent_session_id']:
                raise codex.ObservationError('managed_parent_mismatch')
            stream = parent
            if selectors['stream'] == 'child':
                if child['id'] != binding['worker_id'] or selectors['turn_id'] != binding['worker_turn_id']:
                    raise codex.ObservationError('managed_child_mismatch')
                stream = child
            path = startup._canonical_path(stream.get('path'))
            d3._no_links(path)
            info = path.stat()
            physical = dict(device=info.st_dev, inode=info.st_ino)
            if time.monotonic() >= deadline:
                raise codex.ObservationError('timeout')
            raw = d3._read_file(path, codex.NATIVE_MAX_FILE, expected_binding=physical)
            from runtime.doctor import native_behavior_records
            records = native_behavior_records(raw, thread_id=stream['id'], source_reference=str(path))
            fact = startup._canonical_plain(codex._native_managed_tool(records, submitted_patch=probe['patch'],
                turn_id=selectors['turn_id'], call_id=selectors['call_id'], case_id=case_id,
                child_patch=selectors['stream'] == 'child', raw=raw))
            hook_ref = task_ref | dict(call_id=fact['call_id'])
            after = startup._canonical_plain(setup_observation.managed_observation_readback(
                scope, hook_ref, attempt_ref, case_id=case_id))
            before = probe['before']; receipt = after['receipt']
            kind = 'PreToolUse' if case_id == 'pre-tool-enforcement' else 'PostToolUse'
            digest_field = 'before_digest' if parent_only else 'assignment_digest'
            if (after['identity'] != projection['identity'] or after['attempt_ref'] != startup._canonical_plain(attempt_ref)
                    or after[digest_field] != binding[digest_field]
                    or after['task_revision'] != task_ref['expected_revision'] or not isinstance(receipt, dict)
                    or receipt['kind'] != kind or receipt['conflicted'] is not False
                    or type(receipt['rowid']) is not int or receipt['rowid'] <= before['receipt_watermark']):
                raise codex.ObservationError('managed_effects_unobserved')
            startup._canonical_hash(receipt['fingerprint'])
            if parent_only and receipt['fingerprint'] != codex.patch_input_fingerprint(probe['patch']):
                raise codex.ObservationError('managed_parent_receipt_mismatch')
            if selectors['stream'] == 'child':
                pre = delegation._child_patch_pre_fingerprint(binding['assignment_digest'], codex.patch_input_fingerprint(probe['patch']))
                if receipt['fingerprint'] != delegation._child_patch_fingerprint(pre, fact['post_hook_fingerprint']):
                    raise codex.ObservationError('managed_child_receipt_mismatch')
            if case_id == 'pre-tool-enforcement':
                original = before['probe']['before']
                if after['canary'] != original:
                    raise codex.ObservationError('managed_canary_changed')
            else:
                count = after['post_receipts_count']
                if type(count) is not int or count <= 0 or after['generation']-before['generation'] != count:
                    raise codex.ObservationError('managed_generation_unattributed')
            if d3._read_file(path, codex.NATIVE_MAX_FILE, expected_binding=physical) != raw:
                raise codex.ObservationError('managed_native_source_changed')
            if (time.monotonic() >= deadline or projection != startup._canonical_plain(
                    setup_observation.managed_observation_projection(scope, task_ref, attempt_ref, case_id=case_id))
                    or after != startup._canonical_plain(setup_observation.managed_observation_readback(
                        scope, hook_ref, attempt_ref, case_id=case_id))):
                raise codex.ObservationError('managed_owner_changed')
        return codex._setup_witness(dict(binding=dict(accepted_scope_digest=startup._canonical_digest(projection['identity']),
            attempt_ref=startup._canonical_plain(attempt_ref), transaction_id=projection['transaction_id']),
            setup_reference=projection['setup_reference'],
            observations={case_id:[*fact['sources'], probe['reference'], after['reference']]}))
    except startup.StartupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, subprocess.TimeoutExpired):
        raise codex.ObservationError('managed_setup_observation_unavailable') from None
