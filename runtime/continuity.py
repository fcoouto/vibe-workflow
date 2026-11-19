"""Generic account-generation fences, extracted without a native client producer."""
import re
import secrets

from runtime import storage


def _owned(path):
    try:
        return storage._read(path) or dict(version=1)
    except ValueError as error:
        if str(error) == 'observation_store_unsafe':
            raise
        return dict(version=1)


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


def _proved(state, key=None):
    identity = state.get('identity') if type(state) is dict else None
    found = state.get('account') if type(state) is dict else None
    if type(identity) is not dict or set(identity) != {'key', 'generation'} or not _hex(identity['key']) \
            or not _hex(identity['generation']) or key not in (None, identity['key']) or type(found) is not dict:
        return None
    if found == dict(pending=True, generation=identity['generation']) or 'pending' not in found and \
            found.get('key') == identity['key'] and found.get('generation') == identity['generation']:
        return identity['generation']
    return None


def _fence(state, held):
    generation = _proved(state)
    held.update(generation=generation, key=state['identity']['key'] if generation else None,
                fence=secrets.token_hex(16))
    return dict(identity=None, account=dict(pending=True, generation=held['fence']))


def _ours(state, held):
    return state.get('identity') is None and state.get('account') == dict(pending=True, generation=held.get('fence'))


def _settle(state, held):
    if not held.get('generation') or not _ours(state, held):
        return {}
    return dict(identity=dict(key=held['key'], generation=held['generation']),
                account=dict(pending=True, generation=held['generation']))


def _observe(key, held):
    def step(state):
        same = held.get('generation') and key == held.get('key') and _ours(state, held)
        held['observed'] = held['generation'] if same else secrets.token_hex(16)
        return dict(identity=dict(key=key, generation=held['observed']),
                    account=dict(pending=True, generation=held['observed']))
    return step
