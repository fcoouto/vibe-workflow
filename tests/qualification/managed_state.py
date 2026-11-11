"""A temporary managed project on temporary routing stores, for the live-session modes that launch through
`runtime/opencode_launcher.py`. Never the user's settings or ledger: every path lies under the run's isolated dir.

- The project joins `managed_projects` of its own temporary settings through `routing_settings.update` directly. The
  production one-shot `set managed_projects` needs the activation gate and an enrolled project, which a temporary
  project is not; the harness never changes any real settings.
- The shipped manifest has no qualified route (user decision 5), so the collector would admit nothing. As the
  launcher's own self-check does, code replaces `routing_observations.default_source` with a static source for the
  run's one route while the launch runs (`ManagedState.source_installed`), and restores it afterwards.
- `authority_required` is set on the temporary ledger (one-way; that ledger is deleted with the run).
"""
import contextlib
import json
from pathlib import Path
import time

from tests.qualification.safety import Refused

WINDOW_UNIT = "utilization_thousandths"


class ManagedState:
    def __init__(self, root, route, account, pool="sub", billing_class="included_subscription", real_home=None,
                 extra_routes=()):
        """`route`: (provider, model, variant or None); `extra_routes` share the state, the server and the pool (one
        server start for several launches). `root` must be a fresh directory under the isolated dir."""
        self.root = Path(root)
        resolved = self.root.parent.resolve() / self.root.name
        home = Path(real_home or Path.home()).resolve()
        from scripts import routing as command
        production = [(home / ".local/state/vibe-workflow").resolve(), command.default_settings().parent.resolve(),
                      command.default_ledger().parent.resolve()]
        if resolved == home or any(resolved == p or p in resolved.parents or resolved in p.parents for p in production):
            raise Refused("a managed live-session state must not be the production routing state")
        self.project = self.root / "project"
        self.settings = self.root / "routing" / "settings.json"
        self.ledger = self.root / "usage" / "organizations.sqlite3"
        self.provider, self.model, self.variant = route
        self.routes = [tuple(route)] + [tuple(item) for item in extra_routes]
        self.account, self.pool, self.billing_class = account, pool, billing_class

    def choice(self, funding="subscription", route=None):
        from runtime import opencode_launcher
        return opencode_launcher.choice(*(route or self.routes[0]), funding)

    def create(self):
        """The project, the stores, `managed_projects` and `authority_required`. Returns the service's status."""
        from runtime import routing_settings
        from scripts import routing as command
        if self.root.exists():
            raise Refused("a managed live-session state is created fresh, never reused")
        for folder in (self.root, self.project, self.settings.parent, self.ledger.parent):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project.resolve())]), 0)
        service = command.Service(self.settings, self.ledger, project=self.project)
        try:
            answer = service.handle(json.dumps(dict(v=1, op="set", authority_required=True)).encode())
            if answer.get("state") != "updated":
                raise Refused("could not set authority_required on the temporary ledger: %s" % answer.get("reason"))
            return service.handle(json.dumps(dict(v=1, op="status")).encode())
        finally:
            service.close()

    def service(self):
        from scripts import routing as command
        return command.Service(self.settings, self.ledger, project=self.project)

    def spec(self, route=None):
        from runtime import routing
        picked = self.choice("subscription" if self.billing_class == "included_subscription" else "api", route)
        index = self.routes.index(tuple(route or self.routes[0]))
        return dict(route="live-%s-%d" % (self.pool, index), status="qualified", billing_class=self.billing_class, pool=self.pool,
                    account=self.account, demand={"5h": 1}, binding_enforceable=True, window_unit=WINDOW_UNIT,
                    **{key: picked[key] for key in routing.ROUTE_IDENTITY})

    def static_source(self):
        from runtime import routing_observations
        return routing_observations.StaticSource([self.spec(route) for route in self.routes], lambda pool, at: [dict(
            id="5h", limit=1000, used=0, length=18000, reset_at=at + 3600, rolling=False, observed_at=at,
            unit=WINDOW_UNIT)])

    @contextlib.contextmanager
    def source_installed(self):
        """The run's static route source in place of the shipped manifest, for this process, restored afterwards."""
        from runtime import routing_observations
        saved = routing_observations.default_source
        source = self.static_source()
        routing_observations.default_source = lambda *args, **kwargs: source
        try:
            yield source
        finally:
            routing_observations.default_source = saved

    def assignment(self, attempt, prompt, route=None):
        from runtime import routing_launch
        scope = routing_launch.assignment_scope(self.project)
        assignment = dict(attempt_id=attempt, task="live-" + attempt, scope=scope, choice=self.choice(route=route))
        return assignment, routing_launch.unplanned_job("software-engineer", scope, prompt)


def attempt_id(prefix):
    return "%s-%d" % (prefix, int(time.time() * 1000))
