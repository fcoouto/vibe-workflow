"""Compare a physical binding persisted earlier with the one observed now.

A binding is the `{device, inode}` pair of a file or directory. Device numbers are not stable across sessions
(macOS can renumber a volume after a reboot or remount while every inode stays the same), so a binding read
from a record matches when everything but the device matches. The caller still checks the canonical path that
the record names: a copy has new inodes and a move changes the path, so both still fail. Records keep storing
the device. Comparisons within one operation (open, then confirm the same file) keep comparing both numbers."""

from collections.abc import Mapping


def _number(value):
    return type(value) is int and value >= 0


def matches(stored, current):
    """Whether a persisted binding names the file or directory observed now; the device is not compared."""
    return (isinstance(stored, Mapping) and isinstance(current, Mapping) and set(stored) == set(current)
            and all(_number(value.get(key)) for value in (stored, current) for key in ("device", "inode"))
            and all(stored[key] == current[key] for key in stored if key != "device"))


def all_match(stored, current):
    """Whether two `{name: binding}` maps have the same names and every persisted binding matches."""
    return (isinstance(stored, Mapping) and isinstance(current, Mapping) and set(stored) == set(current)
            and all(matches(stored[name], current[name]) for name in stored))


def record_matches(stored, current):
    """Whether a persisted value equals the current one with exact JSON types, where every `{device, inode}`
    binding inside it ignores the device."""
    if isinstance(stored, Mapping) and isinstance(current, Mapping):
        if set(stored) == set(current) == {"device", "inode"}:
            return matches(stored, current)
        return set(stored) == set(current) and all(record_matches(stored[key], current[key]) for key in stored)
    if isinstance(stored, (list, tuple)) or isinstance(current, (list, tuple)):
        return (type(stored) is type(current) and len(stored) == len(current)
                and all(record_matches(left, right) for left, right in zip(stored, current)))
    return type(stored) is type(current) and stored == current
