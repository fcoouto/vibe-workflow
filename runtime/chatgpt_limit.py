"""The ChatGPT limit mark: OpenCode reported a usage limit, so ChatGPT routes pause until the Codex reset (read
once from the Codex login, assumed to be the same ChatGPT account) or, without it, one hour."""
from runtime import codex_reset, storage

FILE = 'chatgpt-limit.json'
FALLBACK = 3600


def mark(state_dir, now, *, reader=codex_reset.read_reset):
    reset = reader(now=now)
    found = type(reset) is int and reset > now
    record = dict(version=1, marked_at=now, until=reset if found else now + FALLBACK,
                  source='codex' if found else 'fallback')
    with storage._locked(state_dir / FILE, lock='.chatgpt-limit.lock') as path:
        storage._write(path, record)
    return record


def limited(state_dir, now):
    """Whether a valid mark has `marked_at <= now < until`; a missing, unsafe or malformed file is false."""
    try:
        value = storage._read(state_dir / FILE)
    except (OSError, ValueError):
        return False
    return type(value.get('marked_at')) is int and type(value.get('until')) is int \
        and value['marked_at'] <= now < value['until']


def remaining(state_dir, now):
    """Seconds the mark still lasts, or None."""
    return storage._read(state_dir / FILE)['until'] - now if limited(state_dir, now) else None
