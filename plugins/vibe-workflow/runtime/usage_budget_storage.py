"""Shared-ledger storage accounting (F5.S): lossless compact UTF-8 JSON, encoded-byte guards and the growth every
existing owner may still add after admission.

A growing write commits only while `encoded_size(body) + remaining_growth(body) <= SOFT_RECORD`; any other write only
while `encoded_size(body) <= MAX_RECORD`. `remaining_growth` prepays what non-growing writers can still add without
another admission: settlement, uncertainty, coverage, scalar observation, native release, and a funded hold's
conversion into a new run, native task and finished-native key. It reads table sizes and one bounded scan of holds; no
budget is stored. Event and root rows are separate and keep their character limits.
"""
import json

# Per-owner growth reserves, in UTF-8 bytes (approved F5.S formula). tests/test_routing_storage.py measures each
# owner's source-bound maximum against them.
RUN_RESERVE = 4096  # two calls: cost, state, incomplete, over-bound and coverage fields
HOLD_RESERVE = 1024  # dispatch fields and settlement
CONVERSION_RESERVE = 65536  # a convertible hold's new run, its growth, a native task and its finished key
ORGANIZATION_RESERVE = 24576  # settled cycles, live funding, observation and coverage fields
NATIVE_RESERVE = 8192  # the finished-native key of a native task
ROUTING_RESERVE = 4096  # a routing configuration's qualification replacement
FIXED_RESERVE = 262144  # window debits, settled tasks and the caps record


def _budget():
    from runtime import usage_budget  # imported at call time: usage_budget imports this module
    return usage_budget


def serialize(value):
    """Compact, lossless UTF-8 JSON: the text the shared ledger stores."""
    return json.dumps(value, allow_nan=False, separators=(',', ':'), ensure_ascii=False)


def encoded_size(value):
    """The stored size of `value` in UTF-8 bytes."""
    return len(serialize(value).encode('utf-8'))


def text_size(text):
    """The UTF-8 size of stored text (an escaped body from older code is ASCII: bytes equal characters)."""
    return len(text.encode('utf-8'))


def convertible(hold):
    """A hold `convert_hold` can still turn into a run: open, funded and not yet converted."""
    return not hold.get('closed') and (hold.get('amount') or 0) > 0 and hold.get('state') != 'converted'


def remaining_growth(data):
    """R: the bytes existing owners may still add through writers that do not pass a growing admission."""
    holds = data.get('holds') or {}
    return (RUN_RESERVE * len(data.get('runs') or {}) + HOLD_RESERVE * len(holds)
            + CONVERSION_RESERVE * sum(1 for hold in holds.values() if convertible(hold))
            + ORGANIZATION_RESERVE * len(data.get('organizations') or {}) + NATIVE_RESERVE * len(data.get('native') or {})
            + ROUTING_RESERVE * len(data.get('routing') or {}) + FIXED_RESERVE)


def require_growth(data, raw=None):
    """The growing-write admission for `data` (its serialized text `raw`, when already made)."""
    budget = _budget()
    size = text_size(raw if raw is not None else serialize(data))
    budget.require(size + remaining_growth(data) <= budget.SOFT_RECORD, 'ledger_capacity_reached')


def body_for_write(data, *, grows):
    """The text to store for `data`, after its storage admission; refusal raises and the transaction writes nothing."""
    budget = _budget()
    raw = serialize(data)
    if grows:
        require_growth(data, raw)
    budget.require(text_size(raw) <= budget.MAX_RECORD, 'ledger_capacity_reached')
    return raw
