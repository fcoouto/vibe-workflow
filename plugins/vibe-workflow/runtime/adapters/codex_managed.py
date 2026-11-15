"""Native guard observation; transport and parsing stay with the Codex adapter."""

import subprocess
import time

from runtime import delegation, onboarding as d3, setup_observation, startup, task_profile
from runtime.adapters import codex


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
            after = startup._canonical_plain(setup_observation.managed_observation_readback(
                scope, task_ref, attempt_ref, case_id=case_id))
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
                        scope, task_ref, attempt_ref, case_id=case_id))):
                raise codex.ObservationError('managed_owner_changed')
        return codex._setup_witness(dict(binding=dict(accepted_scope_digest=startup._canonical_digest(projection['identity']),
            attempt_ref=startup._canonical_plain(attempt_ref), transaction_id=projection['transaction_id']),
            setup_reference=projection['setup_reference'],
            observations={case_id:[*fact['sources'], probe['reference'], after['reference']]}))
    except startup.StartupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, subprocess.TimeoutExpired):
        raise codex.ObservationError('managed_setup_observation_unavailable') from None
