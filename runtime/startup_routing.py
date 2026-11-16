"""Smart Routing's line in the startup orientation (T12): pointers only, one local settings read.

The line names the client's managed delegation path and this installed package's routing command (agents ran a
checkout's own `scripts/routing.py`: R1, R2) when `routing_settings.managed_project` holds for the checkout, the same
answer `scripts/routing.py status --project` gives (unreadable settings count as managed there and here). `with_line`
adds it to the SessionStart output whatever the enrolment outcome (an orientation, nothing, or a diagnostic such as a
linked worktree's missing enrolment record: R1 B2), never twice and never for an unmanaged checkout. Any error adds
nothing: routing state never fails or delays startup. The line is appended after `startup.orient`'s 150-word check
(which guards the Codex runtime orientation) and adds 22 words for Codex and 17 for every other client;
`tests/test_startup_routing.py` pins each client's managed total within those 150 words.
"""
from pathlib import Path

LINE = '\nSmart Routing: managed project; delegate only via '
HOST_PATHS = {
    'claude-code': 'the native bridge (clients/claude-code.md#native-routing-bridge)',
    'codex': '`workflow.py delegation prepare` (runtime state) or native bridge (clients/codex.md)',
}
OTHER_HOSTS = 'the managed launcher (clients/opencode.md#managed-launcher)'
COMMAND = '; routing commands: python3 -I -B %s' % (Path(__file__).resolve().parents[1] / 'scripts' / 'routing.py')


def settings_path():
    """The routing command's settings (`scripts/routing.py` `default_settings`), without importing that command."""
    from runtime import routing_settings
    return routing_settings.default_path()


def text(client):
    """The line for `client`, managed or not."""
    return LINE + HOST_PATHS.get(client, OTHER_HOSTS) + COMMAND


def line(client, checkout):
    """The line to append to `client`'s startup context for `checkout`, or '' when it is not managed."""
    try:
        from runtime import routing_settings
        if routing_settings.managed_project(checkout, settings_path=settings_path()) is not True:
            return ''
    except Exception:  # noqa: BLE001 - startup orientation never fails on routing state
        return ''
    return text(client)


def with_line(output, client, payload):
    """The SessionStart hook output with the line for the payload's `cwd` added once when it is managed."""
    try:
        cwd = payload.get('cwd') if isinstance(payload, dict) else None
        added = line(client, cwd) if isinstance(cwd, str) and cwd else ''
        hook = (output or {}).get('hookSpecificOutput') if isinstance(output, (dict, type(None))) else None
        context = (hook or {}).get('additionalContext') or '' if isinstance(hook, (dict, type(None))) else None
        if not added or context is None or added.strip() in context:
            return output
        hook = dict(hook or {}, hookEventName='SessionStart', additionalContext=context + added if context else added.lstrip())
        return dict(output or {}, hookSpecificOutput=hook)
    except Exception:  # noqa: BLE001 - never fail startup
        return output
