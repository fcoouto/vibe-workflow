"""Native subagent routes by client, so the shared D5/D6 ledger asks here instead of naming a client.

A route fixes which surfaces may execute, which dispatch controls exist and which adapter observes the child.
A control a route does not list is unavailable to it, never assumed.
"""

ROUTES = {
    "codex-native-subagent": dict(
        client="codex", surfaces=("codex-cli", "codex-desktop"), mechanisms=("codex-native-agent-path", "codex-native-collab-call"),
        controls=("spawn", "explicit_model", "explicit_effort", "fork_turns_none"),
        explicit=("explicit_model", "explicit_effort", "fork_turns_none"), originators=("codex_exec", "Codex Desktop", "codex_work_desktop")),
    "claude-code-subagent-hook": dict(
        client="claude-code", surfaces=("claude-code-cli",), mechanisms=("claude-code-subagent-hook",),
        controls=("spawn", "explicit_model"), explicit=("explicit_model",), originators=("claude-code",)),
}
REVIEW_MECHANISMS = ("codex-persisted-history", *(m for route in ROUTES.values() for m in route["mechanisms"]))
# The reviewer receipt fields that must equal the observed worker's, by receipt mechanism family.
REVIEW_CORRESPONDENCE = dict(reviewer_id="worker_id", review_turn_id="worker_turn_id", report_item_id="result_item_id",
                             report_digest="result_digest")
REVIEW_REQUESTED = {"codex": dict(requested_model="spawn_observed_model", requested_effort="spawn_observed_effort"),
                    "claude-code": dict(requested_model="requested_model", requested_effort="requested_effort")}


def by_surface(surface):
    return next((route for route in ROUTES.values() if surface in route["surfaces"]), None)


def supported(route, scope):
    """The route spec when this plan route is the one existing native route of the scope's client."""
    spec = ROUTES.get(route["mechanism"])
    return spec if spec and route["client_surface"] in spec["surfaces"] and spec["client"] == getattr(scope, "client", "codex") else None


def discover(scope, controls):
    surface = controls.get("client_surface") if isinstance(controls, dict) else None
    if surface == "claude-code-cli":
        from runtime.adapters.claude_delegation import discover_native_workers
    else:
        from runtime.adapters.codex import discover_native_workers
    return discover_native_workers(scope, controls)


def observer(role, surface):
    if surface == "claude-code-cli":
        from runtime.adapters.claude_delegation import observe_native_review_assignment
        return observe_native_review_assignment
    from runtime.adapters.codex import observe_native_worker, observe_native_review_assignment
    return observe_native_worker if role == "worker" else observe_native_review_assignment


def collector(surface):
    if surface == "claude-code-cli":
        from runtime.adapters.claude_delegation import collect_native_review
    else:
        from runtime.adapters.codex import collect_native_review
    return collect_native_review


def origin_ok(receipt, surface):
    """A worker receipt's mechanism and originator belong to the route its binding named."""
    route = by_surface(surface)
    return (route is not None and receipt["mechanism"] in route["mechanisms"] and receipt["originator"] in (
        {"codex_exec"} if surface == "codex-cli" else {"Codex Desktop", "codex_work_desktop"} if surface == "codex-desktop"
        else set(route["originators"])))
