"""Smart Routing's line in the startup orientation (T12): pointers only, one local settings read.

The line names the client's managed delegation path when `routing_settings.managed_project` holds for the checkout,
the same answer `scripts/routing.py status --project` gives (unreadable settings count as managed there and here).
Any error adds nothing: routing state never fails or delays startup. The line is appended after `startup.orient`'s
150-word check (which guards the Codex runtime orientation) and adds 23 words for Codex and 15 for every other client;
`tests/test_startup_routing.py` pins each client's managed total within those 150 words.
"""

LINE = '\nSmart Routing: managed project. Delegate every worker or reviewer only through '
HOST_PATHS = {
    'claude-code': 'the native bridge (clients/claude-code.md#native-routing-bridge).',
    'codex': '`workflow.py delegation prepare`, or the native bridge for a startup-only checkout (clients/codex.md).',
}
OTHER_HOSTS = 'the managed launcher (clients/opencode.md#managed-launcher).'


def settings_path():
    """The routing command's settings (`scripts/routing.py` `default_settings`), without importing that command."""
    from runtime import routing_settings
    return routing_settings.default_path()


def line(client, checkout):
    """The line to append to `client`'s startup context for `checkout`, or '' when it is not managed."""
    try:
        from runtime import routing_settings
        if routing_settings.managed_project(checkout, settings_path=settings_path()) is not True:
            return ''
    except Exception:  # noqa: BLE001 - startup orientation never fails on routing state
        return ''
    return LINE + HOST_PATHS.get(client, OTHER_HOSTS)
