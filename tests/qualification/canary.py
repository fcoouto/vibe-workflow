"""Bounded pilot canary (docs/smart-routing-validation.md#pilot-canary-bounded-not-the-pre-registered-canary).

Pure decisions over an injectable environment, so every refusal and abort path is unit-tested without a server:
`check_project` (the one-project allowlist), `preconditions` (refuse to start), `plan_assignments` (up to 20 explicit
subscription admissions across three roles and two complexity bands) and `PilotDriver` (sequential admissions with
the durable `pilot` call cap, a 24-hour limit, and an abort that runs the Smart Routing OFF kill switch).

The environment (`opencode_live.CanaryEnv` in a live run, a fake in tests) provides: `status()`, `health()`,
`prepare(body)`, `dispatch(attempt)`, `attach(session, attempt, token)`, `detach(...)`, `launch(session, message,
allow_title)`, `account(session)`, `record(event)` and `kill_switch()`.
"""
import os
from pathlib import Path
import time

from tests.qualification.safety import (BudgetExceeded, HARNESS_NAME, MODEL, PROVIDER, Refused, VARIANT,
                                        summarize_ms)

ALLOWLIST = "/Users/fcouto/dev/git/vibe-workflow"
PILOT_MAX = 20
MAX_DURATION_S = 24 * 3600
DEFAULT_CAPS = dict(total=4, per_account=2)
POOL = "sub"
CHOICE = dict(provider=PROVIDER, model=MODEL, effort=VARIANT, harness=HARNESS_NAME, funding="subscription")
# Profile role -> explicit assignment role; `researcher` is explicit on a worker job (routing_settings.EXPLICIT_ROLES).
ROLES = (("implementer", "worker", None), ("reviewer", "review", "reviewer"), ("researcher", "worker", "researcher"))
BANDS = ("low", "medium")


def check_project(project):
    """The pilot runs only in the allowlisted checkout: exactly that path, resolved, and a git checkout root."""
    if not project:
        raise Refused("--project is required in canary mode")
    resolved = os.path.realpath(project)
    if resolved != os.path.realpath(ALLOWLIST) or not (Path(resolved) / ".git").exists():
        raise Refused("canary mode runs only in the allowlisted project")
    return resolved


def anomalies(status):
    """Reasons a routing `status` is not clean for a subscription-only pilot (settings and ledger)."""
    if (status or {}).get("state") != "ok" or status.get("settings_state") != "ok":
        return ["status_unavailable"]
    reasons = []
    settings = status.get("settings") or {}
    if settings.get("smart_routing") is not True:
        reasons.append("smart_routing_off")
    if settings.get("api_usage_enabled") is not False:
        reasons.append("api_usage_enabled")
    if status.get("test_inputs"):
        reasons.append("test_inputs_on")
    if status.get("project_pinned") is not True:
        reasons.append("project_not_pinned")
    ledger = status.get("ledger") or {}
    if ledger.get("state") != "ready":
        return reasons + ["ledger_unavailable"]
    if ledger.get("authority_required") is not True:
        reasons.append("authority_required_off")
    if any(org.get("enabled") for org in ledger.get("organizations") or []):
        reasons.append("organization_enabled")
    if ledger.get("unstamped_open_holds") != 0:
        reasons.append("unstamped_open_holds")
    if (ledger.get("cost_unknown") or {}).get("ids") or (ledger.get("holds") or {}).get("cost_unknown"):
        reasons.append("cost_unknown")
    if (ledger.get("holds") or {}).get("uncertain"):
        reasons.append("uncertain_holds")
    if (ledger.get("uncertain_dollar") or {}).get("ids"):
        reasons.append("uncertain_dollar")
    if ledger.get("blocked_organizations"):
        reasons.append("blocked_organizations")
    if ledger.get("overrun_backlog"):
        reasons.append("overrun_backlog")
    if ledger.get("reconcile_hint") is not None:
        reasons.append("reconcile_pending")
    caps = status.get("effective_caps") or {}
    if any(caps.get(key, 10 ** 9) > limit for key, limit in DEFAULT_CAPS.items()):
        reasons.append("concurrency_caps_raised")
    return reasons


def preconditions(status, health, probes):
    """Every reason to refuse the pilot; empty means it may start. `probes['chain']` is the plugin-chain verdict."""
    reasons = anomalies(status)
    if reasons == ["status_unavailable"]:
        return reasons
    if ((status.get("ledger") or {}).get("open_attempts") or 0) != 0:
        reasons.append("open_attempts")
    if (health or {}).get("state") != "ok":
        reasons.append("routing_unhealthy")
    if (probes or {}).get("chain") != []:
        reasons.append("guard_probe_failed")
    return reasons


def plan_assignments(n, tag, now=None):
    """`n` (1-20) explicit subscription admissions cycling three roles and two complexity bands."""
    if type(n) is not int or not 1 <= n <= PILOT_MAX:
        raise Refused("the pilot runs 1 to %d admissions" % PILOT_MAX)
    now = int(time.time()) if now is None else now
    plan = []
    for index in range(n):
        role, profile_role, explicit = ROLES[index % len(ROLES)]
        band = BANDS[(index // len(ROLES)) % len(BANDS)]
        attempt = "%s-%02d" % (tag, index + 1)
        assignment = dict(attempt_id=attempt, task="%s-task-%02d" % (tag, index + 1), scope="pilot-scope", choice=dict(CHOICE))
        if explicit:
            assignment["role"] = explicit
        job = dict(id="job-" + attempt, role=profile_role, scope_ref="pilot-scope", objective="o", context="c",
                   profile=dict(task_family="code", complexity_band=band, risk_band="medium", review_policy="independent",
                                context_tokens=100, tools=["read"], uncertainty="low"))
        observations = dict(now=now, routes=[dict(route="sub", strategy_id="s-sub", provider=PROVIDER, model=MODEL, effort=VARIANT,
                                                  harness=HARNESS_NAME, account="anthropic:pilot",
                                                  billing_class="included_subscription", pool=POOL, demand={"5h": 10},
                                                  capable=True, permitted=True, binding_enforceable=True)],
                            pools={POOL: dict(windows=[dict(id="5h", limit=1000, used=100, length=18000, reset_at=None,
                                                            rolling=True, fresh=True)])})
        plan.append(dict(label=dict(role=role, band=band, index=index + 1),
                         body=dict(assignment=assignment, job=job, observations=observations)))
    return plan


class Abort(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class PilotDriver:
    """Runs the pilot admissions one at a time (never more than one open), each with a write-ahead reservation in
    the durable `pilot` purpose. Any abort condition stops launching and runs the kill switch once."""

    def __init__(self, env, ledger, n=PILOT_MAX, run="pilot", clock=time.monotonic, max_duration=MAX_DURATION_S,
                 tag=None, now=None):
        self.env, self.ledger, self.run_id, self.clock, self.max_duration = env, ledger, run, clock, max_duration
        self.plan = plan_assignments(n, tag or run, now)
        self.results = dict(planned=n, completed=0, admissions=[], settlements={}, abort=None,
                            latency=dict(prepare=[], dispatch=[], check=[], record=[]), blocked_titles=0)

    def abort(self, reason, index, detail=None):
        switched = self.env.kill_switch()
        self.results["abort"] = dict(reason=reason, admission=index, detail=detail, kill_switch=switched,
                                     kill_switch_ok=(switched or {}).get("state") == "updated")

    def run(self):
        started = self.clock()
        for item in self.plan:
            index = item["label"]["index"]
            try:
                if self.clock() - started > self.max_duration:
                    raise Abort("max_duration")
                self.admit(item, first=index == 1)
            except Abort as stop:
                self.abort(stop.reason, index, stop.detail)
                break
        latency = self.results["latency"]
        self.results["latency_summary"] = {key: summarize_ms(values) for key, values in latency.items()}
        return self.results

    def admit(self, item, first):
        env, label = self.env, item["label"]
        status = env.status()
        if ((status.get("ledger") or {}).get("open_attempts") or 0) != 0:
            raise Abort("concurrency")
        problems = anomalies(status)
        if "concurrency_caps_raised" in problems:
            raise Abort("concurrency")
        if problems:
            raise Abort("status_anomaly", problems)
        allowed = 2 if first else 1  # the first admission may carry the one observed title request
        try:
            ref = self.ledger.reserve("pilot", "admission %d (%s, %s)" % (label["index"], label["role"], label["band"]),
                                      allowed, run=self.run_id)
        except BudgetExceeded:
            raise Abort("cap_breach") from None
        attempt = item["body"]["assignment"]["attempt_id"]
        answer, prepare_ms = env.prepare(item["body"])
        if answer.get("state") != "admitted" or (answer.get("admission") or {}).get("billing_class") != "included_subscription":
            self.ledger.settle(ref, 0, "not admitted")
            raise Abort("admission_refused", answer.get("reason"))
        if (env.health() or {}).get("state") != "ok":
            self.ledger.settle(ref, 0, "routing unhealthy")
            raise Abort("routing_unhealthy")
        answer, dispatch_ms = env.dispatch(attempt)
        if answer.get("state") != "dispatched":
            self.ledger.settle(ref, 0, "not dispatched")
            raise Abort("dispatch_refused", answer.get("reason"))
        token, session, message = answer.get("authority"), answer.get("session_id"), answer.get("message_id")
        if not env.attach(session, attempt, token):
            self.ledger.settle(ref, 0, "attach refused before any session existed")
            env.record(dict(event_id=attempt + "-uncertain", attempt_id=attempt, kind="uncertain"))
            raise Abort("recovery_error", "attach")
        launched = env.launch(session, message, allow_title=first)
        if not launched.get("idle"):
            env.record(dict(event_id=attempt + "-uncertain", attempt_id=attempt, kind="uncertain"))
            raise Abort("unresolved_launch")  # the reservation stays open: its exposure counts in full
        sent = launched["transmitted"]
        try:
            self.ledger.settle(ref, sum(sent.values()))
        except BudgetExceeded:
            raise Abort("cap_breach") from None
        if launched.get("off_route"):
            raise Abort("non_haiku_request")
        if sent.get("primary") != 1 or sent.get("title", 0) > (1 if first else 0) or sent.get("other", 0):
            raise Abort("cap_breach" if sent.get("primary", 0) > 1 or sent.get("title", 0) or sent.get("other", 0)
                        else "managed_request_blocked", sent)
        self.results["blocked_titles"] += launched.get("blocked_titles", 0)
        try:
            accounting = env.account(session)
        except Exception as error:  # noqa: BLE001 - any accounting refusal is a recovery error
            raise Abort("recovery_error", "accounting: %s" % type(error).__name__) from None
        answer, record_ms = env.record(dict(event_id=attempt + "-complete", attempt_id=attempt, kind="complete",
                                            stage="worker", usage=accounting.get("usage")))
        settlement = answer.get("settlement") or {}
        if answer.get("state") != "recorded":
            raise Abort("recovery_error", "record complete: %s" % answer.get("reason"))
        if settlement.get("kind") != "window" or settlement.get("charged"):
            raise Abort("funding_violation", settlement.get("kind"))
        if not env.detach(session, attempt, token):
            raise Abort("recovery_error", "detach")
        after = anomalies(env.status())
        if after:
            raise Abort("status_anomaly", after)
        self.results["completed"] += 1
        self.results["settlements"][settlement["kind"]] = self.results["settlements"].get(settlement["kind"], 0) + 1
        latency = self.results["latency"]
        latency["prepare"].append(prepare_ms)
        latency["dispatch"].append(dispatch_ms)
        latency["record"].append(record_ms)
        if launched.get("check_ms") is not None:
            latency["check"].append(launched["check_ms"])
        self.results["admissions"].append(dict(label, sent=sent, unknown=sorted(accounting.get("unknown") or []),
                                               settlement=settlement.get("kind"), outcome=launched.get("outcome")))
