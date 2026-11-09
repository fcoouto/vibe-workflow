"""Claude Code SessionStart input and output; no client settings are changed."""

from runtime.adapters import session_start
from runtime.adapters.session_start import diagnostic, render


SOURCES = {"startup", "resume", "clear", "compact", "fork"}


def normalize(payload):
    return session_start.normalize(payload, "claude-code", SOURCES)
