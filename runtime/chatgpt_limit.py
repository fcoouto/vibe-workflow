"""The ChatGPT limit mark: OpenCode reported a usage limit, so ChatGPT routes pause until the Codex reset (read
once from the Codex login, assumed to be the same ChatGPT account) or, without it, one hour. A new mark never
shortens a still-valid pause."""
from runtime import codex_reset, storage

FILE = 'chatgpt-limit.json'
FALLBACK = 3600
MAX_PAUSE = 8 * 86400  # the longest Codex window plus a margin; a longer reset is a corrupt answer


def mark(state_dir, now, *, reader=codex_reset.read_reset):
    reset = reader(now=now)
    found = type(reset) is int and now < reset <= now + MAX_PAUSE
    record = dict(version=1, marked_at=now, until=reset if found else now + FALLBACK,
                  source='codex' if found else 'fallback')
    with storage._locked(state_dir / FILE, lock='.chatgpt-limit.lock') as path:
        old = _valid(state_dir, now)
        if old is not None and old['until'] > record['until']:
            return old  # never shorten a still-valid pause
        storage._write(path, record)
    return record


def _valid(state_dir, now):
    """The stored record while `marked_at <= now < until` and its span is within MAX_PAUSE, else None (a missing,
    unsafe or malformed file included)."""
    try:
        value = storage._read(state_dir / FILE)
    except (OSError, ValueError):
        return None
    start, end = value.get('marked_at'), value.get('until')
    return value if type(start) is int and type(end) is int and start <= now < end and end - start <= MAX_PAUSE \
        else None


def limited(state_dir, now):
    return _valid(state_dir, now) is not None


def remaining(state_dir, now):
    """Seconds the mark still lasts, or None."""
    value = _valid(state_dir, now)
    return value['until'] - now if value else None
