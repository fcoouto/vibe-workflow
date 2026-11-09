"""Pure local preparation of already-produced task-status reports; no authority."""

import re

from runtime.task_statuses import STATES


# Do not import core validators: importing state also loads mutation-capable owners.
_GAPS = ('source_authenticity_unverified', 'source_freshness_unverified',
         'export_eligibility_unverified', 'current_readiness_unverified')


def _require(condition):
    if not condition:
        raise ValueError('Invalid task-status preparation input')


def _object(value, required, optional=(), *, limit=None):
    _require(type(value) is dict and len(value) <= (limit or len(required) + len(optional)))
    _require(all(type(key) is str for key in value))
    _require(set(required) <= value.keys())
    if limit is None:
        _require(value.keys() <= set(required) | set(optional))
    return value


def _text(value, limit=1024):
    _require(type(value) is str and 0 < len(value) <= limit and value.strip() and value.isprintable())
    return value


def _integer(value, minimum=0):
    _require(type(value) is int and minimum <= value <= (1 << 63) - 1)
    return value


def _pins(value):
    _object(value, ('schema_version', 'policy_digest', 'runtime_digest'), ('accepted_scope_digest',))
    version = _integer(value['schema_version'], 1)
    _require(version in (1, 2, 3))
    _require(('accepted_scope_digest' in value) == (version == 3))
    result = {'schema_version': version}
    for key in ('policy_digest', 'runtime_digest') + (('accepted_scope_digest',) if version == 3 else ()):
        item = _text(value[key])
        if version == 3:
            _require(re.fullmatch('[0-9a-f]{64}', item) is not None)
        result[key] = item
    return result


def _task(value, gaps):
    if value is None:
        gaps.append('task_unavailable')
        return None
    _object(value, ('client', 'session_id', 'task_id', 'state', 'revision', 'pins'), limit=32)
    client, status = _text(value['client']), _text(value['state'])
    _require(client in ('codex', 'claude-code') and status in STATES)
    result = dict(client=client, session_id=_text(value['session_id']), task_id=_text(value['task_id'], 128),
                  state=status, revision=_integer(value['revision'], 1), pins=_pins(value['pins']),
                  accepted_identity=None)
    if result['pins']['schema_version'] == 3:
        identity = _object(value.get('accepted_identity'),
            ('version', 'client', 'project_id', 'repository_id', 'checkout_id'), limit=16)
        _require(type(identity['version']) is int and identity['version'] == 1
                 and _text(identity['client'], 128) == client)
        result['accepted_identity'] = {key: _text(identity[key], 128)
                                       for key in ('project_id', 'repository_id', 'checkout_id')}
        ids = result['accepted_identity']
        _require(re.fullmatch('[a-zA-Z0-9_-]{1,128}', ids['project_id']) is not None
                 and ids['repository_id'] != ids['checkout_id'])
    else:
        _require('accepted_identity' not in value)
        gaps.append('canonical_identity_unavailable')
    return result


def _historical(value, task, gaps):
    fields = ('historical', 'outcome', 'current', 'implementation_verified',
              'delivery_complete', 'merge_authority', 'status', 'compact')
    _object(value, fields)
    _require(task is not None and task['pins']['schema_version'] == 3)
    _require(value['historical'] is True and _text(value['outcome']) == task['state'])
    _require(all(value[key] is False for key in ('current', 'implementation_verified',
                                                'delivery_complete', 'merge_authority')))
    _require(type(value['status']) is str and value['status'] in ('retained', 'unavailable'))
    compact = value['compact']
    if compact is not None:
        _object(compact, ('name', 'digest'))
        _require(re.fullmatch('[0-9a-f]{32}\\.json', _text(compact['name'])) is not None
                 and re.fullmatch('[0-9a-f]{64}', _text(compact['digest'])) is not None)
        compact = dict(name=compact['name'], digest=compact['digest'])
    _require(value['status'] != 'retained' or compact is not None)
    if value['status'] == 'unavailable':
        gaps.append('historical_support_unavailable')
    return {key: compact if key == 'compact' else value[key] for key in fields}


def prepare_task_status(report, *, enabled=False):
    """Project supplied source facts only; disabled preparation never inspects report.

    The caller owns real opt-in, source acquisition and future integration. Neither
    this function nor its output establishes freshness, readiness or export rights.
    """
    _require(type(enabled) is bool)
    if not enabled:
        return None
    _object(report, ('ok', 'task', 'generation', 'blockers', 'limitations'), ('historical',))
    _require(report['ok'] is True)
    for key in ('blockers', 'limitations'):
        _require(type(report[key]) is list and len(report[key]) <= 64)
    generation = _integer(report['generation'])
    gaps = list(_GAPS)
    task = _task(report['task'], gaps)
    historical = _historical(report['historical'], task, gaps) if 'historical' in report else None
    return dict(preparation_only=True, source='runtime.state.task_status', task=task, generation=generation,
                historical=historical, usage=None, charges=None, native_model=None, native_settings=None,
                worker_coverage=None, gaps=gaps)
