#!/usr/bin/env python3
"""Read-only check of the partner tools OpenCode relies on. Never installs or writes."""
import re

STATES = ("ready", "missing", "outdated", "unknown", "not-supported")
PRECEDENCE = ("missing", "outdated", "unknown", "not-supported", "ready")


def parse_version(text):
    match = re.search(r"\d+(?:\.\d+)*", text)
    if not match:
        raise ValueError("no_version")
    return tuple(int(part) for part in match.group().split("."))


def compare(installed, latest):
    a, b = parse_version(installed), parse_version(latest)
    size = max(len(a), len(b))
    a, b = a + (0,) * (size - len(a)), b + (0,) * (size - len(b))
    return (a > b) - (a < b)


def worst(states):
    return min(states, key=PRECEDENCE.index)


def exit_code(states):
    if any(state in ("missing", "outdated") for state in states):
        return 1
    return 2 if "unknown" in states else 0
