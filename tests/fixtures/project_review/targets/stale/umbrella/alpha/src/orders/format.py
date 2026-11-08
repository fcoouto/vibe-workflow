"""Synthetic presentation helper; no I/O."""
def format_total(cents):
    if not isinstance(cents, int) or cents < 0:
        raise ValueError("invalid total")
    return f"Total: {cents // 100}.{cents % 100:02d}"
