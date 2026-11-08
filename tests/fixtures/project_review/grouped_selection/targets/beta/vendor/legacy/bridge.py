"""Synthetic vendor legacy fixture; no external effects."""

def normalized_rows(rows):
    """Return stable identifiers without changing the input."""
    return sorted({str(row["id"]).strip() for row in rows if row.get("id") is not None})
