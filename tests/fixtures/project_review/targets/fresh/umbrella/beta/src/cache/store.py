"""Synthetic in-memory cache with explicit time input."""
def store(values, key, value, expires_at):
    values[key] = (value, expires_at)


def lookup(values, key, now):
    item = values.get(key)
    if item is None:
        return None
    value, expires_at = item
    return value if now < expires_at else None
