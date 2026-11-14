"""Initial supported software floors; release metadata never supplies capabilities."""
import re


MINIMUM_VERSIONS = {
    "codex": "0.163.0",
    "claude-code": "2.1.304",
    "opencode": "2.2.6",
    "python": "3.11",
    "graphify": "0.9.104",
    "keyring": "25.7.0",
    "habblaud": "1.0.3",
}
_STABLE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", re.ASCII)
_RELEASE = re.compile(
    r"v?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?"
    r"(?P<suffix>-[0-9A-Za-z.-]+|(?:a|b|rc|dev)[0-9]+|\.(?:dev|post)[0-9]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?", re.ASCII)


def at_least(version, minimum):
    """True/False for known releases; None for unknown metadata. Floors must be stable."""
    floor = _STABLE.fullmatch(minimum) if isinstance(minimum, str) and len(minimum) <= 128 else None
    if floor is None:
        raise ValueError("Invalid software minimum")
    observed = _RELEASE.fullmatch(version) if isinstance(version, str) and len(version) <= 128 else None
    if observed is None:
        return None
    actual = tuple(int(part or 0) for part in observed.group(1, 2, 3))
    required = tuple(int(part or 0) for part in floor.group(1, 2, 3))
    suffix = observed.group("suffix")
    prerelease = suffix is not None and not suffix.startswith(".post")
    return actual > required or actual == required and not prerelease
