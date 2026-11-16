"""Early provider resets (Wave F1 addendum; the user, 16 November 2026): an on-request re-read, never a poll.

A provider may reset an account before its scheduled reset, so an exhausted reading refuses only for
`routing_observations.EXHAUSTED_RECHECK_SECONDS` and a provider quota-limit error refuses its pool only for
`PROVIDER_LIMIT_SECONDS`. `recheck` runs inside a one-shot routing request's billing acquisition (never under `--serve`,
never on a timer, never a model call): it re-reads, with one bounded zero-token producer read, a producer whose stored
reading still shows exhaustion but is older than that window, and the native Codex reading while an OpenCode ChatGPT
provider-limit mark is active (clear-only: a newer Codex reading with capacity clears the mark; it never creates a
refusal and never links the ChatGPT and Codex identities). Each producer is re-read at most once per window, recorded
in one small private file; nothing is read while nothing is exhausted. Cost: at most one bounded read per window per
exhausted producer (Claude `get_usage`, the Codex app-server account read, or OpenCode Go's usage-limits read).
"""
from runtime import routing
from runtime import routing_observations as observations


def allowed(settings_path, name, now):
    """Whether producer `name` may be re-read now; records the read time (one write per allowed read)."""
    with observations._locked(observations._resolved(observations.window_path(settings_path)).parent
                              / observations.RECHECK_FILE) as path:
        value = observations._read(path) or dict(version=1)
        last = value.get(name)
        if routing._amount(last) and 0 <= now - last < observations.EXHAUSTED_RECHECK_SECONDS:
            return False
        value[name] = now
        observations._write(path, value)
        return True


def due(settings_path, routes, now):
    """The producers whose re-read is due for these statically relevant routes."""
    from runtime import claude_usage, codex_account, opencode_account
    found = set()
    for route in routes:
        if codex_account.exhausted_stale(settings_path, route, now) or (
                opencode_account.family(route) == 'chatgpt'
                and opencode_account.provider_limit_active(settings_path, route.get('pool'), now)):
            found.add('codex')
        if claude_usage.exhausted_stale(settings_path, route, now) or (
                opencode_account.family(route) == 'claude'
                and claude_usage.exhausted_stale(settings_path, route, now, native=True)):
            found.add('claude')
        if opencode_account.exhausted_stale(settings_path, route, now):
            found.add('go')
    return found


def recheck(settings_path, routes, now):
    from runtime import claude_usage, codex_account, opencode_account
    readers = dict(codex=lambda: codex_account.refresh(settings_path, force=True),
                   claude=lambda: claude_usage.refresh(settings_path, force=True),
                   go=lambda: opencode_account.refresh(settings_path, families=('go',), force=True))
    for name in sorted(due(settings_path, routes, now)):
        if allowed(settings_path, name, now):
            readers[name]()
