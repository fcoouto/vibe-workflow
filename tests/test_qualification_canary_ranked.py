"""The ranked canary (plan Task F7.2; validation Edit 7 point 9, approved call 36) on synthetic stores: the real
routing command (one-shot and a served `--serve` child), `routing_observations.LocalSource`, temporary settings,
ledger, qualifications, table and window store, the live-shaped OpenCode double and the existing Codex reader double.
No live call, provider, account, credential or real home. No production subscription-billing producer exists, so a
labelled code-level double stands in (in this process and in the served child); the unpatched production source is
pinned to refuse."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
import unittest
from unittest import mock

from runtime import (codex_rate_limits, opencode_launcher, routing_observations as collector, routing_paths,
                     routing_settings, subscription_billing, subscription_reconcile)
from runtime.usage_budget import Ledger
from scripts import routing as command
from tests.qualification import canary, canary_ranked, canary_ranked_state, safety
from tests.qualification import opencode_live as live
from tests.qualification.doubles import FakeOpenCode, Temp, qual_run

ROOT = Path(__file__).resolve().parents[1]
CLAUDE, CHATGPT = "claude-agent-sdk via opencode", "opencode provider openai"
CLAUDE_PATH, CLAUDE_POOL = "opencode-claude-subscription", "claude-subscription"
CHATGPT_PATH, CHATGPT_POOL = "opencode-chatgpt-subscription", "chatgpt-subscription"
ROWS = ("implementation", "review", "investigation")
APPROVAL = dict(by="the user", at="2026-11-12", scope="ranked canary")
LABEL = "canary-ranked-synthetic"
PRODUCTION_BILLING = collector.LocalSource.billing  # the unpatched production source: no billing producer
CHILD = ("import sys; sys.path.insert(0, %r); from unittest import mock; "
         "from runtime import routing_observations as c, subscription_billing as b; "
         "mock.patch.object(c.LocalSource, 'billing', lambda s, r, n: b.double(%r)(r, n)).start(); "
         "from scripts import routing as m; raise SystemExit(m.main(sys.argv[1:]))") % (str(ROOT), LABEL)


def strategy(sid, model, provider="claude-agent", harness=CLAUDE):
    return dict(strategy_id=sid, model=model, effort="low", harness=harness, provider=provider, evidence_refs=["e"])


def now():
    return int(time.time())


def window(name, reset, used=100, observed=None):
    return dict(id=name, limit=1000, used=used, length=18000 if name == "5h" else 604800, reset_at=reset,
                rolling=False, observed_at=now() if observed is None else observed, unit=collector.WINDOW_UNIT)


def rate_limit(resets, five=0.2, seven=None):
    """Claude Agent `native.rateLimit` telemetry as OpenCode stores it on an assistant message."""
    windows = {}
    if five is not None:
        windows["five_hour"] = dict(utilization=five, resetsAt=resets["5h"], status="allowed")
    if seven is not None:
        windows["seven_day"] = dict(utilization=seven, resetsAt=resets["7d"], status="allowed")
    return dict(rateLimit=dict(status="allowed", rateLimitType="five_hour", resetsAt=resets["5h"],
                               isUsingOverage=False, unifiedWindows=windows))


def observation(resets, five, at=None):
    """A settled launch's `rate_limit_observation` (opencode_accounting) reporting only the 5h window."""
    return dict(kind="rate_limit_observation", observed_ms=(now() if at is None else at) * 1000,
                windows=dict(five_hour=dict(utilization_fraction=five, resets_at_s=resets["5h"])))


class RankedOpenCode(FakeOpenCode):
    """The live-shaped double with the attempt's identity: `qual-post`'s guard lets through only its control route
    (the admitted identity the harness wrote for this attempt); the shim's check carries the session's model and the
    attached pool; the model list is the isolated server's catalog; assistant messages carry `native.rateLimit`."""

    CATALOG = (("claude-agent", "invented-a"), ("claude-agent", "invented-b"))

    def __init__(self, routing_cmd, log):
        super().__init__(routing_cmd, log)
        self.native, self.pools, self.models = None, {}, {}

    def facts(self, session, kind="primary"):
        """The session's model once created (the launch's), else the attach request's model."""
        if session in self.sessions:
            model = self.sessions[session]["record"]["model"]
            provider, name, variant = model["providerID"], model["id"], model["variant"]
        else:
            provider, rest = self.models[session].split("/", 1)
            name, variant = rest.split("#", 1)
        return dict(provider=provider, model=name, variant=variant, harness=CLAUDE, kind=kind,
                    funding_pool=self.pools.get(session))

    def rpc(self, method, payload):
        if method == "attach":
            self.pools[payload["session_id"]] = payload["funding_pool"]
            self.models[payload["session_id"]] = payload["model"]
            answer = self.routing.served("check", attempt_id=payload["attempt_id"], session_id=payload["session_id"],
                                         authority=payload["authority"], request=self.facts(payload["session_id"]))[0]
            self.attach_models = getattr(self, "attach_models", []) + [payload["model"]]
            if answer.get("state") == "allow" and answer.get("authority") == "verified":
                self.attached[payload["session_id"]] = (payload["attempt_id"], payload["authority"])
                return 200, dict(state="attached", session_id=payload["session_id"]), 1.0
            return 500, dict(type="rpc.internal", message="RPC call failed"), 1.0
        return super().rpc(method, payload)

    def _request_hook(self, session, kind):
        facts = self.facts(session, kind)
        base = dict(hook="model.request", kind=kind, provider=facts["provider"], model=facts["model"],
                    variant=facts["variant"], session=session)
        self._log(phase="pre", **base)
        if session in self.attached:
            attempt, token = self.attached[session]
            answer = self.routing.served("check", attempt_id=attempt, session_id=session, authority=token,
                                         request=facts)[0]
            if answer.get("state") != "allow" or answer.get("authority") != "verified":
                return False
        if [facts["provider"], facts["model"], facts["variant"]] != list(self.control.get("route") or []):
            self._log(phase="post", decision="guard_route", **base)
            return False
        sent = self.transmitted()
        if kind == "title" and not (self.control["title_session"] == session and not any(r["kind"] == "title" for r in sent)):
            self._log(phase="post", decision="guard_title", **base)
            return False
        if len(sent) >= self.control["budget"]:
            self._log(phase="post", decision="guard_budget", **base)
            return False
        self._log(phase="post", decision="transmit", **base)
        return True

    def request(self, method, path, body=None, query=None, timeout=60):
        if method == "GET" and path == "/api/model":
            return 200, dict(data=[dict(providerID=p, id=m, variants=["low"]) for p, m in self.CATALOG]), 1.0
        answer = super().request(method, path, body, query, timeout)
        if method == "POST" and path.endswith("/prompt"):
            session = self.sessions[path.strip("/").split("/")[2]]
            model = session["record"]["model"]
            for message in session["messages"]:
                if message.get("type") == "assistant" and "native" not in message:
                    message.update(model=dict(id=model["id"], providerID=model["providerID"], variant=model["variant"]),
                                   native=self.native)
        return answer


class RankedStores(Temp):
    """A production-shaped canary state (a temporary one marked `production`, never the real one): settings with
    Smart Routing on, a managed git checkout, qualified OpenCode paths, a pinned table and seeded windows."""

    def setUp(self):
        super().setUp()
        billing = mock.patch.object(collector.LocalSource, "billing", lambda source, route, at:
                                    subscription_billing.double(LABEL)(route, at))
        billing.start()
        self.addCleanup(billing.stop)
        self.project = self.private(self.tmp / "project")
        subprocess.run(["git", "-C", str(self.project), "init", "-q"], check=True)
        directory = self.private(self.private(self.tmp / "q") / "canary")
        self.state = dict(dir=directory, settings=directory / "settings.json", ledger=directory / "ledger.sqlite3",
                          production=True, approval=dict(APPROVAL))
        self.settings = self.state["settings"]
        self.routing = self.served_routing()
        canary.initialize_state(self.routing)
        revision = routing_settings._load(self.settings)[0]
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project.resolve())]), revision)
        routing_paths.qualify(self.settings, CLAUDE_PATH, demand={"5h": 10, "7d": 10}, evidence="synthetic", now=now())
        self.table(strategy("s-a", "invented-a"), strategy("s-b", "invented-b"))
        self.resets = {"5h": now() + 3 * 3600, "7d": now() + 5 * 86400}
        for name in ("5h", "7d"):
            collector.store_window(self.settings, CLAUDE_POOL, window(name, self.resets[name]))

    def served_routing(self, harnesses=(CLAUDE,)):
        routing = live.Routing(self.state["settings"], self.state["ledger"], project=str(self.project),
                               harnesses=list(harnesses))
        routing.child_argv = [sys.executable, "-I", "-B", "-c", CHILD, "--serve", *routing.argv]
        self.addCleanup(routing.stop)
        return routing

    def table(self, *strategies, version="tv-1"):
        rows = {"%s|%s|medium" % (kind, band): list(strategies) for kind in ROWS for band in canary.BANDS}
        target = self.settings.parent / routing_paths.TABLE_FILE
        target.write_text(json.dumps(dict(schema=2, version=version, rows=rows)))
        target.chmod(0o600)

    def make_run(self):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "calls") / "calls.jsonl")
        # A disposable ledger only: the ranked purpose opened by a recorded test approval, never the pilot's calls.
        ledger.set_cap(canary.RANKED_PURPOSE, 83, "test", approval=dict(by="test", at="test", scope="test ledger"))
        base = self.private(self.tmp / "iso")
        run = qual_run(base, ledger, canary_project=str(self.project), canary_state=self.state)
        run.routing.stop()
        run.routing = self.routing
        run.replay_dir = self.tmp / "replay"
        run.server = RankedOpenCode(self.routing, base / "hooks.jsonl")
        run.repo_start = safety.repo_snapshot(self.project)
        run.reread_wait = 0
        run.server.native = rate_limit(self.resets, five=0.12)
        return run, ledger

    def digest(self, run, acknowledge=8):
        record = dict(canary_ranked.preflight(self.settings, run.routing.harnesses, now(), acknowledge),
                      project=str(run.canary_project), ledger=str(self.state["ledger"]))
        return canary_ranked_state.digest(record)

    def controller(self, run=None, clock=None, state_dir=None, **options):
        """An explicitly approved start: the operator approves the preflight's digest, then the run starts."""
        run = run or self.make_run()[0]
        pins = canary_ranked.approval_inputs(self.settings, run.routing.harnesses)
        options.setdefault("acknowledge", canary_ranked.coverage(pins)["minimum"])
        state_dir = state_dir or self.private(self.tmp / "ranked-state")
        if getattr(run, "ranked", None) is not None:
            canary_ranked.close(run.ranked)
        run.ranked = canary_ranked.setup(run, clock=clock, approved=self.digest(run, options["acknowledge"]),
                                         state_dir=state_dir, **options)
        self.addCleanup(lambda controller=run.ranked: controller.lock.__exit__(None, None, None))
        return run.ranked

    def drive(self, run, ledger, n=2, tag=None):
        return canary.PilotDriver(live.CanaryEnv(run), ledger, n=n, run=run.run_id, tag=tag or run.run_id, route=None,
                                  ranked=run.ranked).run()

    def body(self, index=0):
        return canary.plan_assignments(6, "b", ranked=True)[index]["body"]


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedPreflightTests(RankedStores):
    def test_approval_pins_paths_and_table_without_six_preview_lists(self):
        pins = canary_ranked.approval_inputs(self.settings, [CLAUDE])
        self.assertEqual(pins["paths"], [CLAUDE_PATH])
        self.assertEqual(pins["host_constraints"], [CLAUDE])
        self.assertEqual(sorted(pins["routes"]), [CLAUDE_PATH + ":s-a", CLAUDE_PATH + ":s-b"])
        self.assertEqual(pins["settings_revision"], routing_settings._load(self.settings)[0])
        self.assertEqual(len(pins["table_digest"]), 64)
        self.assertEqual(pins["routes_version"], collector.LocalSource(self.settings).routes_version(
            routing_paths._table(self.settings)))
        record = canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)
        self.assertNotIn("previews", json.dumps(record))
        self.assertEqual(sorted(record["coverage"]["profiles"]), sorted(
            "%s/%s" % (kind, band) for kind in canary_ranked.SPECIALISTS for band in canary.BANDS))
        self.assertTrue(all(record["coverage"]["profiles"].values()))

    def test_only_signed_in_providers_paths_are_approved(self):
        routing_paths.qualify(self.settings, CHATGPT_PATH, demand={"7d": 5}, evidence="synthetic", now=now())
        self.assertEqual(canary_ranked.approval_inputs(self.settings, [CLAUDE])["paths"], [CLAUDE_PATH])

    def test_canary_refuses_to_start_without_generated_routes(self):
        self.table(strategy("s-x", "invented-x", provider="openai", harness=CHATGPT))
        with self.assertRaisesRegex(safety.Refused, "no_generated_route"):
            canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)
        (self.settings.parent / routing_paths.TABLE_FILE).unlink()
        with self.assertRaisesRegex(safety.Refused, "table_empty"):
            canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)
        routing_paths.unqualify(self.settings, CLAUDE_PATH)
        with self.assertRaisesRegex(safety.Refused, "no_qualified_path"):
            canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)

    def test_cap_preflight_reports_reservation_coverage_not_a_usage_forecast(self):
        record = canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)
        coverage = record["coverage"]
        self.assertEqual(coverage["windows"][CLAUDE_POOL + "/5h"], dict(demand=10, reservation=12, full_reservations=8))
        self.assertEqual((coverage["minimum"], coverage["sample_minimum"]), (8, 60))
        self.assertEqual(coverage["funding_classes"], ["subscription"])
        self.assertNotIn("forecast", json.dumps(record))
        with mock.patch.object(Ledger, "_reserve", wraps=Ledger._reserve) as reserve:
            changed = canary_ranked.coverage(dict(canary_ranked.approval_inputs(self.settings, [CLAUDE]), margin_bp=5000))
        self.assertEqual(changed["windows"][CLAUDE_POOL + "/5h"]["reservation"], 15)
        reserve.assert_any_call(10, None, 5000)

    def test_low_coverage_requires_explicit_start_approval(self):
        for wrong in (None, 7, 9, -8, "8", True, 8.0):
            with self.assertRaisesRegex(safety.Refused, "acknowledge-low-coverage 8"):
                canary_ranked.preflight(self.settings, [CLAUDE], now(), wrong)
        self.assertEqual(canary_ranked.preflight(self.settings, [CLAUDE], now(), 8)["acknowledged"], 8)
        self.assertFalse(hasattr(canary_ranked, "acknowledged"))  # no prose convention: SCOPE grants nothing

    def test_preflight_reports_earliest_demanded_reset(self):
        early = now() + 1800
        collector.store_window(self.settings, CLAUDE_POOL, window("5h", early, observed=now() + 1))
        record = canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)
        self.assertEqual(record["earliest_reset"], early)
        self.assertLessEqual(record["remaining_s"], 1800)
        with self.assertRaisesRegex(safety.Refused, "reset"):
            canary_ranked.preflight(self.settings, [CLAUDE], early, acknowledge=8)

    def test_missing_reset_metadata_blocks_start(self):
        os.unlink(collector.window_path(self.settings))
        with self.assertRaisesRegex(safety.Refused, "reset"):
            canary_ranked.preflight(self.settings, [CLAUDE], now(), acknowledge=8)


class FakeEnv:
    """A driver environment for the gate tests: a ranked admission, then the existing driver contract."""

    def __init__(self, admission):
        self.admission, self.calls = admission, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            if name == "status":
                return dict(state="ok", settings_state="ok", settings=dict(smart_routing=True, api_usage_enabled=False),
                            project_pinned=True, ledger=dict(state="ready", authority_required=True, open_attempts=0,
                                                             unstamped_open_holds=0, reconcile_hint=None))
            if name == "prepare":
                return dict(state="admitted", admission=self.admission), 1.0
            if name == "record":
                return dict(state="recorded"), 1.0
            if name == "kill_switch":
                return dict(state="updated")
            return None
        return call


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedGateTests(RankedStores):
    def admitted(self, run, index=0):
        answer, _ = live.CanaryEnv(run).prepare(self.body(index))
        self.assertEqual(answer.get("state"), "admitted", answer)
        return answer["admission"]

    def test_canary_plan_sends_no_choice_or_observation(self):
        plan = canary.plan_assignments(6, "r", ranked=True)
        self.assertEqual({(p["label"]["role"], p["label"]["band"]) for p in plan},
                         {(role, band) for role, _, _ in canary.ROLES for band in canary.BANDS})
        for item in plan:
            self.assertNotIn("choice", item["body"]["assignment"])
            self.assertNotIn("observations", item["body"])
        with self.assertRaises(safety.Refused):
            canary.plan_assignments(1, "r", ranked=True, route=safety.choice(safety.parse_route("claude-agent/haiku#low")))

    def test_canary_aborts_a_non_ranked_admission(self):
        ranked = self.controller()
        with self.assertRaises(canary.Abort) as caught:
            ranked.before_dispatch(dict(mode="worker_choice", billing_class="included_subscription"))
        self.assertEqual(caught.exception.reason, "non_ranked_admission")
        env = FakeEnv(dict(mode="choice", billing_class="included_subscription"))
        ranked.before_prepare = lambda body: None
        calls = safety.CallLedger.initialize(self.private(self.tmp / "c2") / "l.jsonl")
        calls.set_cap(canary.RANKED_PURPOSE, 83, "test", approval=dict(by="test", at="test", scope="test ledger"))
        result = canary.PilotDriver(env, calls, n=1, ranked=ranked).run()
        self.assertEqual((result["abort"]["reason"], result["abort"]["recovery"]), ("non_ranked_admission", "not_dispatched"))
        self.assertNotIn("dispatch", env.calls)

    def test_guard_requires_the_admitted_identity_not_just_an_approved_one(self):
        run, _ = self.make_run()
        ranked = self.controller(run)
        ranked.before_prepare(self.body())
        admission = self.admitted(run)
        other = CLAUDE_PATH + (":s-b" if admission["route"].endswith(":s-a") else ":s-a")
        for wrong in (dict(admission, route=other), dict(admission, pool=CHATGPT_POOL),
                      dict(admission, route=CLAUDE_PATH + ":s-unknown")):
            with self.assertRaises(canary.Abort) as caught:
                ranked.before_dispatch(wrong)
            self.assertEqual(caught.exception.reason, "admission_not_approved")
        ranked.before_dispatch(admission)  # the exact admission passes

    def test_canary_dispatches_the_admitted_route(self):
        run, _ = self.make_run()
        self.controller(run)
        admission = self.admitted(run)
        env, sent = live.CanaryEnv(run), []
        real = run.routing.served
        run.routing.served = lambda op, **body: (sent.append((op, body)), real(op, **body))[1]
        answer, _ = env.dispatch(admission["attempt_id"])
        self.assertEqual(answer.get("state"), "dispatched", answer)
        identity = run.ranked.identity(admission)
        self.assertEqual(sent[-1][1]["request"], dict(provider="claude-agent", model=identity[1], variant="low",
                                                      harness=CLAUDE, kind="primary", funding_pool=CLAUDE_POOL))
        self.assertNotEqual(sent[-1][1]["request"]["funding_pool"], canary.POOL)

    def test_canary_attaches_the_admitted_pool(self):
        run, _ = self.make_run()
        self.controller(run)
        admission = self.admitted(run)
        env = live.CanaryEnv(run)
        answer, _ = env.dispatch(admission["attempt_id"])
        self.assertTrue(env.attach(answer["session_id"], admission["attempt_id"], answer["authority"]))
        self.assertEqual(run.server.pools[answer["session_id"]], CLAUDE_POOL)
        self.assertEqual(run.server.attach_models, ["claude-agent/%s#low" % run.ranked.identity(admission)[1]])

    def test_unpatched_production_source_refuses_dispatch(self):
        run, _ = self.make_run()
        self.controller(run)
        admission = self.admitted(run)
        with mock.patch.object(collector.LocalSource, "billing", PRODUCTION_BILLING):
            answer, _ = live.CanaryEnv(run).dispatch(admission["attempt_id"])
        self.assertEqual((answer["state"], answer["reason"]), ("refused", subscription_billing.UNVERIFIED))

    def test_canary_uses_one_shared_request_preview(self):
        run, _ = self.make_run()
        ranked = self.controller(run)
        with mock.patch.object(command.Service, "preview", autospec=True, side_effect=command.Service.preview) as preview:
            ranked.before_prepare(self.body())
        self.assertEqual(preview.call_count, 1)
        sent = preview.call_args[0][1]
        self.assertEqual(sent["constraints"]["allowed_harnesses"], [CLAUDE])  # the exact request the child gets
        self.assertNotIn("observations", sent)
        self.assertIn("preview_ms", ranked.timings[-1])

    def test_changed_approval_inputs_require_new_approval(self):
        """Table content, rules, host constraints, qualification or settings revision: each change after the preview
        releases the unsent admission (the driver's `not_dispatched`) and needs a renewed preflight and approval."""
        run, _ = self.make_run()
        rules = mock.patch.object(routing_paths, "load_rules", return_value=dict(routing_paths.load_rules(), version=99))
        changes = ((lambda: self.table(strategy("s-a", "invented-a"), version="tv-2"),
                    lambda: self.table(strategy("s-a", "invented-a"), strategy("s-b", "invented-b"))),
                   (rules.start, rules.stop),
                   (lambda: setattr(run.ranked, "harnesses", [CLAUDE, CHATGPT]), lambda: None),
                   (lambda: routing_paths.qualify(self.settings, CLAUDE_PATH, demand={"5h": 11, "7d": 10},
                                                  evidence="again", now=now()), lambda: None),
                   (lambda: routing_settings.update(self.settings, dict(margin_bp=2500),
                                                    routing_settings._load(self.settings)[0]), lambda: None))
        for index, (change, revert) in enumerate(changes):
            ranked = self.controller(run, state_dir=self.private(self.tmp / ("state-%d" % index)))
            ranked.before_prepare(self.body(index))
            admission = self.admitted(run, index)
            change()
            with self.assertRaises(canary.Incomplete) as caught:
                ranked.before_dispatch(admission)
            self.assertEqual(caught.exception.reason, "approval_inputs_changed")
            revert()
            live.CanaryEnv(run).record(dict(event_id="nd-%d" % index, attempt_id=admission["attempt_id"],
                                            kind="not_dispatched"))


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedWindowTests(RankedStores):
    def test_canary_stores_the_settled_window(self):
        ranked = self.controller()
        observed = now() + 1  # newer than the seed; the observation's own time, not the settlement's
        accounting = dict(rate_limit=observation(self.resets, 0.13, at=observed))
        ranked.after_settlement(dict(pool=CLAUDE_POOL, reserved={"win:%s/5h" % CLAUDE_POOL: 12}), accounting)
        stored = {w["id"]: w for w in collector.LocalSource(self.settings).windows(CLAUDE_POOL, now())}
        self.assertEqual((stored["5h"]["used"], stored["5h"]["observed_at"]), (130, observed))  # not settlement time
        self.assertEqual(ranked.allowance.cumulative[CLAUDE_POOL + "/5h"], 30)

    def test_partial_window_telemetry_does_not_refresh_the_missing_window(self):
        ranked = self.controller()
        values = json.loads(collector.window_path(self.settings).read_text())
        values["pools"][CLAUDE_POOL]["7d"]["observed_at"] = now() - collector.FRESH_SECONDS - 5  # 7d is now stale
        collector.window_path(self.settings).write_text(json.dumps(values))
        ranked.after_settlement(dict(pool=CLAUDE_POOL, reserved={}), dict(rate_limit=observation(
            self.resets, 0.1, at=now() + 1)))  # telemetry reports 5h only
        ranked.clock = lambda: now() + 2
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.before_prepare(self.body())
        self.assertEqual((caught.exception.reason, caught.exception.detail), ("seed_required", [CLAUDE_POOL + "/7d"]))

    def test_recent_observation_after_saved_reset_is_not_seed_ready(self):
        ranked = self.controller()
        values = json.loads(collector.window_path(self.settings).read_text())
        values["pools"][CLAUDE_POOL]["5h"]["reset_at"] = now() - 1  # fresh observation, reset already passed
        collector.window_path(self.settings).write_text(json.dumps(values))
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.before_prepare(self.body())
        self.assertEqual(caught.exception.reason, "seed_required")

    def test_a_seed_or_reader_reading_counts_toward_the_allowance(self):
        """Every newer stored reading of an approved window counts, a seed included: a `7d` the telemetry never
        reports still accumulates, and a seed below the last reading is an unknown delta, never a reset."""
        ranked = self.controller()
        collector.store_window(self.settings, CLAUDE_POOL, window("7d", self.resets["7d"], used=130, observed=now() + 1))
        ranked.clock = lambda: now() + 2
        ranked.before_prepare(self.body())
        self.assertEqual(ranked.allowance.cumulative[CLAUDE_POOL + "/7d"], 30)
        values = json.loads(collector.window_path(self.settings).read_text())
        values["pools"][CLAUDE_POOL]["7d"].update(used=110, observed_at=now() + 2, source="operator")
        collector.window_path(self.settings).write_text(json.dumps(values))
        ranked.clock = lambda: now() + 3
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.before_prepare(self.body(1))
        self.assertEqual(caught.exception.reason, "window_usage_unknown")

    def test_canary_window_cap_survives_a_reset(self):
        ranked = self.controller()
        key = CLAUDE_POOL + "/5h"
        ranked.allowance.observe(CLAUDE_POOL, "5h", 140, ranked.allowance.baseline[key]["reset_at"], now() + 1)
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.allowance.observe(CLAUDE_POOL, "5h", 10, now() + 5 * 3600, now() + 2)  # a new window: never replenished
        self.assertEqual(caught.exception.reason, "window_usage_unknown")
        self.assertEqual(ranked.allowance.cumulative[key], 40)
        with self.assertRaises(canary.Incomplete):
            ranked.before_prepare(self.body())  # stopped for good; a reseed cannot restart it

    def test_reset_gap_ends_incomplete_without_replenishment(self):
        ranked = self.controller()
        reset = ranked.allowance.baseline[CLAUDE_POOL + "/5h"]["reset_at"]
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.allowance.observe(CLAUDE_POOL, "5h", 50, reset, now() + 1)  # an ambiguous decrease
        self.assertEqual(caught.exception.reason, "window_usage_unknown")

    def test_canary_stops_after_a_window_overrun(self):
        ranked = self.controller()
        reset = ranked.allowance.baseline[CLAUDE_POOL + "/5h"]["reset_at"]
        with self.assertRaises(canary.Abort) as caught:
            ranked.allowance.observe(CLAUDE_POOL, "5h", 201, reset, now() + 1)
        self.assertEqual(caught.exception.reason, "window_overrun")

    def test_canary_refuses_demand_beyond_remaining_allowance(self):
        ranked = self.controller()
        reset = ranked.allowance.baseline[CLAUDE_POOL + "/5h"]["reset_at"]
        ranked.allowance.observe(CLAUDE_POOL, "5h", 190, reset, now() + 1)
        with self.assertRaises(canary.Incomplete) as caught:
            ranked.allowance.check({"win:%s/5h" % CLAUDE_POOL: 12})
        self.assertEqual(caught.exception.reason, "allowance_exhausted")
        ranked.allowance.check({"win:%s/5h" % CLAUDE_POOL: 10})

    def test_unsettled_reservations_reduce_remaining_allowance(self):
        ranked = self.controller()
        ranked.allowance.hold("other", {"win:%s/5h" % CLAUDE_POOL: 90})
        with self.assertRaises(canary.Incomplete):
            ranked.allowance.check({"win:%s/5h" % CLAUDE_POOL: 12})
        ranked.allowance.release("other")
        ranked.allowance.check({"win:%s/5h" % CLAUDE_POOL: 12})

    def test_allowance_exhausted_releases_before_stopping_incomplete(self):
        run, ledger = self.make_run()
        ranked = self.controller(run)
        reset = ranked.allowance.baseline[CLAUDE_POOL + "/5h"]["reset_at"]
        ranked.allowance.observe(CLAUDE_POOL, "5h", 195, reset, now() + 1)
        result = self.drive(run, ledger, n=2)
        self.assertIsNone(result["abort"])
        self.assertEqual((result["incomplete"]["reason"], result["incomplete"]["recovery"]),
                         ("allowance_exhausted", "not_dispatched"))
        self.assertEqual(run.routing("status")[0]["ledger"]["open_attempts"], 0)
        self.assertEqual(run.server.transmitted(), [])

    def test_reset_deadline_ends_incomplete_even_without_a_crossing_launch(self):
        run, ledger = self.make_run()
        ranked = self.controller(run)
        real = ranked.before_prepare

        def then_the_reset(body):  # the deadline passes between preview and dispatch; nothing crossed it
            real(body)
            ranked.clock = lambda: ranked.earliest_reset
        ranked.before_prepare = then_the_reset
        result = self.drive(run, ledger, n=2)
        self.assertEqual(result["incomplete"]["reason"], "window_usage_unknown")
        self.assertEqual(result["incomplete"]["recovery"], "not_dispatched")  # the undispatched hold is released
        self.assertEqual(run.server.transmitted(), [])
        self.assertEqual(run.routing("status")[0]["ledger"]["open_attempts"], 0)

    def test_canary_never_clears_abandoned_debits(self):
        run, ledger = self.make_run()
        self.controller(run)
        with mock.patch.object(subscription_reconcile, "clear_debits", side_effect=AssertionError("cleared")):
            self.assertNotIn("clear_debits", Path(canary_ranked.__file__).read_text())
            self.assertNotIn("clear_debits", Path(canary.__file__).read_text())
            self.assertEqual(self.drive(run, ledger, n=1)["completed"], 1)
        self.assertIn("reconcile", canary_ranked.DEBIT_ADVICE)

    def test_preview_never_refreshes_a_nonshortlisted_pool(self):
        routing_paths.qualify(self.settings, CHATGPT_PATH, demand={"7d": 5}, evidence="synthetic", now=now())
        self.table(strategy("s-a", "invented-a"), strategy("s-g", "invented-g", provider="openai", harness=CHATGPT))
        run, _ = self.make_run()
        ranked = self.controller(run)  # only the Claude harness is signed in: the ChatGPT route is never kept
        with mock.patch.object(codex_rate_limits, "refresh", side_effect=AssertionError("refreshed")) as refresh:
            ranked.before_prepare(self.body())
        refresh.assert_not_called()
        self.assertEqual(ranked.timings[-1]["kept"], [CLAUDE_PATH + ":s-a"])


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedProductionShapedTests(RankedStores):
    def test_canary_admissions_are_ranked(self):
        run, ledger = self.make_run()
        self.controller(run)
        result = self.drive(run, ledger, n=2)
        self.assertIsNone(result["abort"], result["abort"])
        self.assertEqual(result["completed"], 2)
        self.assertEqual({a["mode"] for a in result["admissions"]}, {"ranked"})
        self.assertEqual(result["incomplete"]["reason"], "sample_below_minimum")  # 2 < 60: never a pass

    def test_canary_prepare_stays_served(self):
        run, ledger = self.make_run()
        self.controller(run)
        spawned = []
        real = subprocess.Popen
        with mock.patch.object(live.Routing, "__call__", side_effect=AssertionError("one-shot prepare")), \
                mock.patch.object(subprocess, "Popen", lambda argv, **kw: (spawned.append(argv), real(argv, **kw))[1]):
            env = live.CanaryEnv(run)
            answer, _ = env.prepare(self.body())
        self.assertEqual(answer.get("state"), "admitted", answer)
        self.assertTrue(all("--serve" in argv for argv in spawned))  # no reader is started by served preparation
        self.assertFalse([argv for argv in spawned if "app-server" in " ".join(map(str, argv))])

    def test_canary_runs_on_the_local_source_across_a_stale_gap(self):
        """P6: seed, rank, dispatch/attach the admitted facts, settle/store, pass 900 s, the Claude path needs a seed,
        then the operator seed lets the same run's allowance resume (never replenished)."""
        run, ledger = self.make_run()
        ranked = self.controller(run)  # the real clock, as the served child's
        seeded = now() - collector.FRESH_SECONDS + 5  # both seeded readings turn stale within 5 s
        values = json.loads(collector.window_path(self.settings).read_text())
        for name in ("5h", "7d"):
            values["pools"][CLAUDE_POOL][name]["observed_at"] = seeded
        collector.window_path(self.settings).write_text(json.dumps(values))
        first = self.drive(run, ledger, n=1, tag="p6a")
        self.assertIsNone(first["abort"], first["abort"])
        admission = first["admissions"][0]
        self.assertEqual((admission["mode"], admission["pool"]), ("ranked", CLAUDE_POOL))
        self.assertTrue(admission["route"].startswith(CLAUDE_PATH + ":"))
        self.assertEqual(ranked.allowance.cumulative[CLAUDE_POOL + "/5h"], 20)  # 100 -> 120 observed
        self.assertEqual(run.server.control["route"], list(ranked.identity(admission)))
        stored = {w["id"]: w for w in collector.LocalSource(self.settings).windows(CLAUDE_POOL, now())}
        self.assertEqual(stored["5h"]["used"], 120)
        time.sleep(max(0.0, seeded + collector.FRESH_SECONDS + 1 - time.time()))  # the gap: the 7d seed is stale
        stopped = self.drive(run, ledger, n=1, tag="p6b")
        self.assertEqual(stopped["incomplete"]["reason"], "seed_required")
        self.assertTrue(stopped["incomplete"]["resumable"])
        self.assertEqual(stopped["incomplete"]["detail"], [CLAUDE_POOL + "/7d"])  # the reported 5h stays fresh
        self.assertEqual(stopped["completed"], 0)
        answer, _ = run.routing("seed_window", pool=CLAUDE_POOL, window="7d", used=100, limit=1000,
                                reset_at=self.resets["7d"], length=604800, event_id="seed-7d")
        self.assertEqual(answer.get("state"), "recorded", answer)  # the operator seed of the needed window
        resumed = self.drive(run, ledger, n=1, tag="p6c")
        self.assertIsNone(resumed["abort"], resumed["abort"])
        self.assertEqual(resumed["completed"], 1)
        self.assertEqual(ranked.allowance.cumulative[CLAUDE_POOL + "/5h"], 20)  # never replenished or reset


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedChatGPTRefreshTests(RankedStores):
    """The stale-and-needed ChatGPT refresh through the existing bounded reader, with the Codex app-server double as
    the reader executable; a private fake HOME and an empty PATH (no real `codex`, no credential)."""

    def test_canary_refreshes_only_a_needed_stale_chatgpt_pool(self):
        from tests import test_routing_codex_rate_limits as codex
        routing_paths.qualify(self.settings, CHATGPT_PATH, demand={"7d": 5}, evidence="synthetic", now=now())
        self.table(strategy("s-g", "invented-g", provider="openai", harness=CHATGPT), strategy("s-a", "invented-a"))
        home = self.private(self.tmp / "codex-home")
        self.private(home / ".codex")
        (home / ".codex" / "auth.json").write_text('{"fake": true}')
        empty = self.private(self.tmp / "empty-bin")
        log = self.tmp / "reader.log"
        command = [sys.executable, "-I", "-B", str(codex.DOUBLE), str(log), "ok",
                   json.dumps(codex.result(primary=codex.window(15, resets_in=0, now=self.resets["7d"])))]
        original = codex_rate_limits.refresh
        calls = []

        def refresh(settings, **options):
            calls.append(options)
            return original(settings, command=command, environ=dict(PATH=str(empty), HOME=str(home), LANG="C"),
                            **options)
        stale = now() - collector.FRESH_SECONDS - 60  # known reset, stale reading: needed only if kept
        collector.store_window(self.settings, CHATGPT_POOL, window("7d", self.resets["7d"], observed=stale))
        self.routing = self.served_routing(harnesses=(CLAUDE, CHATGPT))
        run, _ = self.make_run()
        ranked = self.controller(run)
        spawns = lambda: sum(1 for line in log.read_text().splitlines() if json.loads(line).get("spawned")) \
            if log.exists() else 0  # noqa: E731
        with mock.patch.object(codex_rate_limits, "refresh", side_effect=refresh):
            ranked.before_prepare(self.body())
            self.assertEqual([call["need"] for call in calls], [("7d",)])  # the kept ChatGPT pool, its window
            self.assertEqual(spawns(), 1)
            stored = {w["id"]: w for w in collector.LocalSource(self.settings).windows(CHATGPT_POOL, now())}
            self.assertEqual((stored["7d"]["used"], stored["7d"]["reset_at"]), (150, self.resets["7d"]))
            self.assertEqual(ranked.allowance.cumulative[CHATGPT_POOL + "/7d"], 50)  # the reader's reading counts
            ranked.before_prepare(self.body(1))  # fresh now: the bounded reader answers without a process
            self.assertEqual(spawns(), 1)
            answer, _ = live.CanaryEnv(run).prepare(self.body())  # served preparation never starts the reader
        self.assertIn(answer.get("state"), ("admitted", "pending"), answer)
        self.assertEqual((len(calls), spawns()), (2, 1))


@unittest.skipUnless(shutil.which("git"), "git is required")
class RankedEntryPointTests(RankedStores):
    def test_canary_run_builds_the_ranked_controller_before_the_server_starts(self):
        run, _ = self.make_run()
        run.ranked_options = dict(approved=self.digest(run), acknowledge=8,
                                  state_dir=self.private(self.tmp / "ranked-state"))
        order = []
        run.snapshot_user_dirs = run.report_only_home = lambda: {}
        run.compare_user_dirs = lambda before, home: None
        run.start = lambda stage: order.append(("start", getattr(run, "ranked", None) is not None))
        run.probes = lambda stage: order.append(("probes", None)) or []
        run.server.rpc = lambda method, payload: (200, dict(state="ok"), 1.0)
        built = []
        original = canary.PilotDriver
        canary.PilotDriver = lambda *args, **kwargs: built.append(kwargs) or original(*args, **dict(kwargs, n=1))
        try:
            run.run()
        finally:
            canary.PilotDriver = original
        self.assertEqual(order[0], ("start", True))
        self.assertIsNotNone(built[0]["ranked"])
        self.assertIsNone(built[0]["route"])
        self.assertIn("ranked", run.results["production_ledger"]["approval"])
