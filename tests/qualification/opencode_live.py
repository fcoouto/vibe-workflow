#!/usr/bin/env python3
"""Live qualification of the Smart Routing stack on a real, isolated OpenCode 2.2.6 server, and the bounded pilot.

An operator tool, not a unit test (its name keeps unittest discovery from collecting it). It acts as the managed
launcher of the OpenCode contract (clients/opencode.md#handshake).

Qualification stages (`--through N`, temporary routing stores pinned in the isolated directory):
  0  server, plugins and a real `routing.py --serve` child; health before and after `prepare`; `authority_required`;
     `status`; then the plugin-chain probes, which must pass before anything may be sent
  1  P0.7: derived ids, one request per derived message id, duplicate send, read-back, id shapes, ordering
  2  the shim handshake on a real session and its blocked paths
  3  P0.8: re-read accounting with `children` and `lineage`, title by subtraction, events, `record complete`
  4  lost acknowledgment (no model call)

Canary mode (`--canary --project <allowlisted checkout>`): the bounded pilot of docs/smart-routing-validation.md,
on a dedicated canary routing state (`--canary-state`, never the production settings or ledger) with `--project`
pinned; see tests/qualification/canary.py. A dead run's reservation is settled with `--settle-dead SEQ`.

Safety rules it enforces itself (tests/test_qualification_harness.py covers the pure parts):
- `--isolated-dir` must be an empty, owner-only directory outside OpenCode's user directories, ~/Library/Application
  Support and any git worktree; `opencode --version` must be 2.2.6 before anything starts.
- Only `claude-agent/haiku#low`. `qual-pre` (before the shim) and `qual-post` (after it) record every
  `model.request`; `qual-post` blocks any other model or variant, any title not allowed, held sessions and anything
  past the permitted count. A recorder log write that fails blocks the hook. Nothing is sent before stage 0 has
  proved the chain: all three plugins present, a guard probe refused by `qual-post` (`guard_variant`) after
  `qual-pre`, and a pending probe refused by the shim between them.
- Every send is written ahead to the durable `--call-ledger` (owner-only, outside the run directory, flock-protected,
  caps per purpose: qualification 14, pilot 24 in code, raised to 28 by a recorded cap line under pre-registration
  Edit 4). The count is OpenCode `model.request` hooks, not upstream API calls.
- It never prints or stores the dispatch token, the server password or credentials; output goes through `Redactor`
  and is refused if anything unsanitized remains.
- On exit, error or SIGTERM/SIGINT it stops the server and every child, removes the isolated directory (it holds
  the server password and the login-method reference) unless `--keep`, removes the canary project config, checks
  that no process is left and the directories are gone, and compares the user's OpenCode directories.

  python3 -B tests/qualification/opencode_live.py --init-call-ledger ~/.local/state/vibe-workflow-qualification/calls.jsonl
  Q=$(mktemp -d) && chmod 700 "$Q"
  python3 -B tests/qualification/opencode_live.py --isolated-dir "$Q" --call-ledger <path> --through 0 --out results.json
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.qualification import canary, chatgpt, probe_stages, safety, sanitize  # noqa: E402
from tests.qualification.safety import (Blocker, COUNTING, CallLedger, DERIVED_MESSAGE,  # noqa: E402
                                        DERIVED_SESSION, MODEL, PROMPT_TEXT, PROVIDER, QUALIFIED_OPENCODE, ROUTE,
                                        Redactor, Refused, USER_OPENCODE_DIRS, VARIANT, check_isolated_dir, check_real_home,
                                        check_opencode_version, compare_snapshots, id_shape,
                                        latency_pairs, on_route, repo_snapshot, snapshot_tree,
                                        summarize_ms, verify_plugin_chain)
from tests.qualification.pilot_env import CanaryEnv  # noqa: E402,F401 - re-exported: live.CanaryEnv
from tests.qualification.server import (EventStream, Routing, Server, kill_leftovers, listener,  # noqa: E402
                                        processes, server_environment, status_view)

POOL = "sub"
REQUEST = dict(provider=PROVIDER, model=MODEL, variant=VARIANT, harness="claude-agent-sdk via opencode", kind="primary",
               funding_pool=POOL)
PROBE_REQUEST = dict(provider="opencode", model="session-probe", variant=None, harness="opencode session probe",
                     kind="primary", funding_pool="none")
DENY_ALL = [{"action": "*", "resource": "*", "effect": "deny"}]
KEEP_WARNING = ("kept: the isolated dir still holds the server password, the login-method reference and any login OpenCode "
                "stored there (a ChatGPT sign-in is an OAuth record with access and refresh tokens); the server was "
                "stopped. Remove the dir when done.")
REPORT_ONLY_HOME = (".claude", ".opencode")  # written by the claude CLI / OpenCode under HOME: reported, not compared


class Interrupted(Exception):
    pass


def _interrupt(signum, _frame):
    raise Interrupted("signal %d" % signum)


class Run:
    def __init__(self, base, ledger, through=4, canary_project=None, admissions=canary.PILOT_MAX, keep=False,
                 canary_state=None, purpose=None, stages=None, chatgpt_model=None):
        self.base, self.ledger, self.through, self.keep = base, ledger, through, keep
        self.canary_project = canary_project
        self.purpose = purpose or ("pilot" if canary_project else "qualification")
        if self.purpose not in ("pilot", "qualification", "qualification-2", "chatgpt"):
            raise Refused("unknown run purpose")
        self.stage_names, self.chatgpt_model = list(stages or []), chatgpt_model
        # The one route this run may send to; the chatgpt run sets it only after the login's model list is checked.
        self.route = None if self.purpose == "chatgpt" else (PROVIDER, MODEL, VARIANT)
        self.guard_variant = "high"
        self.chatgpt_timeout = 900
        self.run_id = "%s-%s" % (self.purpose, uuid.uuid4().hex[:8])
        self.admissions = admissions
        if canary_project and not canary_state:
            raise Refused("canary mode needs the dedicated canary state")
        self.canary_state = canary_state
        if canary_project:
            self.server = Server(base, canary_project, canary_state["settings"], canary_state["ledger"])
            self.routing = Routing(canary_state["settings"], canary_state["ledger"], project=canary_project)
        else:
            self.server = Server(base, route=self.route)
            self.routing = Routing(self.server.settings, self.server.ledger)
        # Not `self.account`: that name is the accounting method (shadowing it caused pilot attempt 1's TypeError).
        self.pool, self.route_account = dict(pilot=(canary.POOL, canary.ACCOUNT), qualification=("sub", "anthropic:qual"),
                                             **{"qualification-2": ("sub", "anthropic:qual2"),
                                                "chatgpt": ("chatgpt-sub", "openai:chatgpt-qual")})[self.purpose]
        self.repo_start = None
        self.reread_wait = canary.REREAD_WAIT_S
        self.account_progress = None
        self.results = dict(mode=self.purpose, stages={}, ledger=[], blockers=[], deviations=[])
        self.redact = Redactor(paths=[str(base), os.path.realpath(base), str(base).replace("/private/", "/", 1), str(ROOT),
                                      str(Path.home())], names=[os.environ.get("USER", "")])
        self.events = None
        self.token = None
        self.children = []

    # ------------------------------------------------------------ the run's route
    def route_ref(self):
        return "%s/%s#%s" % tuple(self.route)

    def route_choice(self):
        """The explicit worker choice for `prepare`: the run's route as `{provider, model, effort, harness}`."""
        provider, model, variant = self.route
        return dict(provider=provider, model=model, effort=variant, harness=safety.harness_for(provider))

    def request_facts(self, route=None):
        """The `check`/`dispatch` request facts for the route (the run's own by default)."""
        provider, model, variant = route or self.route
        return dict(provider=provider, model=model, variant=variant, harness=safety.harness_for(provider), kind="primary",
                    funding_pool=self.pool)

    # ------------------------------------------------------------ budget gate (write-ahead)
    def gate(self, permitted):
        """Let `qual-post` transmit `permitted` more requests from now (0 closes it)."""
        self.server.write_control(budget=len(self.server.transmitted()) + permitted)

    def expect(self, stage, label, permitted, exposure=None):
        """Reserve the calls in the durable ledger before anything is sent, then open the gate. Must-block actions
        reserve an exposure of 1 with nothing permitted, so even a guard failure is counted."""
        calls = max(permitted, exposure or 0)
        ref = self.ledger.reserve(self.purpose, label, calls, run=self.run_id)
        token = dict(ref=ref, stage=stage, label=label, before=len(self.server.transmitted()))
        self.gate(permitted)
        return token

    def done(self, token):
        """Close the gate and settle the reservation with what `qual-post` actually transmitted."""
        sent = len(self.server.transmitted()) - token["before"]
        self.gate(0)
        self.results["ledger"].append(dict(stage=token["stage"], label=token["label"], calls=sent))
        self.ledger.settle(token["ref"], sent)  # more than reserved raises BudgetExceeded
        return sent

    # ------------------------------------------------------------ helpers
    def note(self, stage, key, value):
        self.results["stages"].setdefault(stage, {})[key] = value

    def records(self, since, session=None, hook=None, phase=None):
        return [r for r in self.server.hooks()[since:]
                if (session is None or r.get("session") == session) and (hook is None or r.get("hook") == hook)
                and (phase is None or r.get("phase") == phase)]

    @staticmethod
    def reason(body):
        """The fixed reason code in an error body (`routing: blocked (x)` or a guard code), never the body itself."""
        text = json.dumps(body)
        match = re.search(r"routing: blocked \(([a-z0-9_]+)\)", text) or re.search(r"qualification guard \(([a-z_]+)\)", text)
        return match.group(1) if match else None

    @staticmethod
    def data(body):
        return body.get("data", body) if isinstance(body, dict) else body

    def prompt(self, session, message_id=None, text=PROMPT_TEXT):
        body = {"text": text}
        if message_id:
            body["id"] = message_id
        return self.server.request("POST", "/api/session/%s/prompt" % session, body)

    def outcome(self, messages, after_ms):
        idle = [m for m in messages if m.get("type") == "idle" and ((m.get("time") or {}).get("created") or 0) >= after_ms]
        last = idle[-1] if idle else {}
        return dict(outcome=last.get("outcome"), reason=self.reason(last))

    def wait_idle(self, session, after_ms, timeout=120):
        """Until the session is not active and has an idle record created after `after_ms`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, active, _ = self.server.request("GET", "/api/session/active")
            listed = json.dumps(active) if status == 200 else ""
            messages = self.messages(session)
            idle = [m for m in messages if m.get("type") == "idle" and ((m.get("time") or {}).get("created") or 0) >= after_ms]
            if idle and session not in listed:
                return messages
            time.sleep(0.5)
        raise Blocker("session did not become idle")

    def messages(self, session, limit=None):
        """Every page of a session's messages (ascending)."""
        found, cursor, pages = [], None, 0
        while True:
            query = {} if cursor else {"order": "asc"}  # the cursor carries the order; sending both is HTTP 400
            if limit:
                query["limit"] = str(limit)
            if cursor:
                query["cursor"] = cursor
            status, body, _ = self.server.request("GET", "/api/session/%s/message" % session, query=query)
            if status != 200:
                raise Blocker("message list failed with HTTP %d" % status)
            pages += 1
            data = (body.get("data") if isinstance(body, dict) else body) or []
            found.extend(data)
            # `cursor` is `{previous, next}`; `next` stays set on the last page, so paging ends on an empty page.
            cursor = ((body.get("cursor") or {}).get("next")) if isinstance(body, dict) else None
            if not data or not cursor or pages > 200:
                self.last_pages = pages
                return found

    def create_session(self, session_id=None, title=None, permissions=None, variant=None, parent=None):
        provider, model, default = self.route or (PROVIDER, MODEL, VARIANT)
        body = {"model": {"id": model, "providerID": provider, "variant": variant or default},
                "location": {"directory": self.server.directory}, "permissions": permissions or DENY_ALL}
        if session_id:
            body["id"] = session_id
        if parent:
            body["parentID"] = parent
        if title:
            body["title"] = title
        return self.server.request("POST", "/api/session", body)

    def blocked_prompt(self, stage, label, session, expect):
        """Send a prompt that must be blocked; record the reason and that nothing was sent (exposure 1 reserved)."""
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        token = self.expect(stage, label, 0, exposure=1)
        status, _, _ = self.prompt(session)
        messages = self.wait_idle(session, began)
        sent = self.done(token)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        assistant = [m for m in messages if m.get("type") == "assistant" and ((m.get("time") or {}).get("created") or 0) >= began]
        result = dict(http=status, sent=sent, pre_invocations=len(invoked), kinds=sorted({r["kind"] for r in invoked}),
                      assistant_messages=len(assistant), **self.outcome(messages, began))
        result["pass"] = sent == 0 and not assistant and result["reason"] == expect
        if sent:
            raise Blocker("%s: a request was transmitted when it must be blocked" % label)
        if not result["pass"]:
            self.results["deviations"].append("%s: expected %s, observed %s" % (label, expect, result["reason"]))
        return result

    def descendants(self, root):
        """Every listed session under `root` by `parentID` (command children included) and the lineage list."""
        _, listing, _ = self.server.request("GET", "/api/session")
        listed = self.data(listing)
        if self.account_progress is not None:
            self.account_progress["listing"] = dict(type=type(listed).__name__,
                                                    count=len(listed) if isinstance(listed, list) else None)
        sessions = [s for s in (listed or []) if isinstance(s, dict) and s.get("id")]
        parents = {s["id"]: s.get("parentID") for s in sessions}
        found, frontier = [], [root]
        while frontier:
            current = frontier.pop()
            for child, parent in parents.items():
                if parent == current and child not in found and child != root:
                    found.append(child)
                    frontier.append(child)
        lineage = [dict(session_id=s, parent_session_id=p) for s, p in parents.items()]
        return found, lineage[:1024]

    def account(self, session, previous=None, shim_descendants=None, cross_check=False):
        """S3.6b-1 accounting per #146: the root, every descendant as `children`, every listed session as `lineage`."""
        from runtime import opencode_accounting as accounting
        from runtime.adapters import opencode as adapter
        progress = self.account_progress = dict(session_read=None, message_pages=None, listing=None, children=None,
                                                lineage=None)
        status, record, _ = self.server.request("GET", "/api/session/%s" % session)
        progress["session_read"] = status
        raw = self.messages(session)
        pages = progress["message_pages"] = self.last_pages
        normalized = [adapter.normalize_message(m) for m in raw]
        total = accounting.normalize_session(self.data(record))
        found, lineage = self.descendants(session)
        progress.update(children=len(found), lineage=len(lineage))
        children = []
        for child in found:
            _, child_record, _ = self.server.request("GET", "/api/session/%s" % child)
            children.append(dict(accounting.normalize_session(self.data(child_record)),
                                 messages=[adapter.normalize_message(m) for m in self.messages(child)], complete=True))
        state = dict(session_id=session, messages=normalized, tokens=total["tokens"], cost=total["cost"], complete=True,
                     children=children, lineage=lineage)
        extra = dict(shim_descendants=shim_descendants) if cross_check else {}  # #152: the shim's own descendant record
        result = accounting.usage_from_messages(normalized, total["tokens"], session_id=session, funding="subscription",
                                                complete=True, session_cost=total["cost"], children=children, lineage=lineage,
                                                **extra)
        return dict(result=result, state=state, raw=raw, pages=pages, total=total, normalized=normalized,
                    children=len(children), lineage=len(lineage))

    def account_settled(self, session, titles_sent=0, shim_descendants=None, cross_check=False):
        """`account`, re-read while the result may still be catching up (`canary.unsettled`): at most
        `canary.MAX_REREADS` full re-reads, `reread_wait` seconds apart. Accepted once settled; a token class that
        decreases or a message set that changes raises `AccountingRegressed`. Accounting and adapter errors are
        deterministic and propagate unretried. Still unsettled after the limit: `deviation: title_unaccounted`."""
        read, rereads = self.account(session, shim_descendants=shim_descendants, cross_check=cross_check), 0
        while canary.unsettled(read["result"], titles_sent) and rereads < canary.MAX_REREADS:
            time.sleep(self.reread_wait)
            again, rereads = self.account(session, shim_descendants=shim_descendants, cross_check=cross_check), rereads + 1
            before, after = read["total"]["tokens"] or {}, again["total"]["tokens"] or {}
            if {m.get("id") for m in again["raw"]} != {m.get("id") for m in read["raw"]} or any(
                    after.get(k) is None and before.get(k) is not None or
                    (after.get(k) is not None and before.get(k) is not None and after[k] < before[k]) for k in before):
                raise canary.AccountingRegressed("accounting_regressed")
            read = again
        read["rereads"] = rereads
        read["deviation"] = "title_unaccounted" if canary.unsettled(read["result"], titles_sent) else None
        return read

    # ------------------------------------------------------------ stage 0
    def start(self, stage):
        self.server.start()
        self.redact.add_secret(self.server.password)
        self.note(stage, "server", dict(version=self.server.request("GET", "/api/info")[1].get("version"), port="<random>",
                                        location="canary project subdirectory" if self.canary_project else "isolated project"))
        if self.purpose == "chatgpt":  # no Claude login in this run: the only provider is the ChatGPT one
            self.events = EventStream(self.server)
            self.events.start()
            return
        for _ in range(20):  # the provider's integration registers shortly after start-up
            if self.server.request("GET", "/api/integration/claude-agent")[0] == 200:
                break
            time.sleep(0.5)
        status, _, _ = self.server.request("POST", "/api/integration/claude-agent/connect/external", {"methodID": "claude-login"})
        self.note(stage, "claude_agent_connect_http", status)
        if status not in (200, 204):
            raise Blocker("could not connect claude-agent through its external Claude Code login method")
        self.server.request("POST", "/api/location/reload")
        listed = []
        for _ in range(30):
            time.sleep(0.5)
            _, models, _ = self.server.request("GET", "/api/model")
            listed = [m for m in (models or {}).get("data", []) if m.get("providerID") == PROVIDER and m.get("id") == MODEL]
            if listed:
                break
        variants = [v if isinstance(v, str) else (v or {}).get("id") for v in (listed[0].get("variants") or [])] if listed else []
        self.note(stage, "haiku_listed", dict(listed=bool(listed), low_variant=VARIANT in variants))
        if not listed or VARIANT not in variants:
            raise Blocker("claude-agent/haiku#low is not available on the isolated server")
        self.events = EventStream(self.server)
        self.events.start()

    def probes(self, stage):
        """The plugin-chain proof required before any real request (B1). Both probes must be blocked at 0 tokens."""
        status, plugins, _ = self.server.request("GET", "/api/plugin")
        ids = [p.get("id") for p in (plugins or {}).get("data", []) if isinstance(p, dict)]
        chain_ids = [i for i in ids if i in ("qual-pre", "vibe-workflow.routing", "qual-post")]
        # Guard probe: an interactive session on haiku#high; `qual-post` must refuse it (guard_variant).
        _, created, _ = self.create_session(title="qual guard probe", variant=self.guard_variant)
        guard_session = (self.data(created) or {}).get("id")
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        token = self.expect(stage, "guard probe (must be refused by qual-post)", 0, exposure=1)
        self.prompt(guard_session)
        guard_messages = self.wait_idle(guard_session, began)
        guard_sent = self.done(token)
        guard = self.records(mark, session=guard_session, hook="model.request")
        # Pending probe: the derived session of an admitted, undispatched attempt; the shim must refuse it.
        probe = canary.plan_assignments(1, self.run_id + "-probe", pool=self.pool, account=self.route_account,
                                        route=self.route_choice())[0]["body"]
        answer, _ = self.routing("prepare", **probe)
        attempt = probe["assignment"]["attempt_id"]
        ids_ = self.routing.admission_ids(attempt) if answer.get("state") == "admitted" else None
        pending, pending_outcome, pending_sent = [], dict(outcome=None, reason=None), 0
        if ids_:
            self.create_session(ids_["session_id"], title="qual pending probe")
            mark, began = len(self.server.hooks()), int(time.time() * 1000)
            token = self.expect(stage, "pending probe (must be refused by the shim)", 0, exposure=1)
            self.prompt(ids_["session_id"])
            pending_messages = self.wait_idle(ids_["session_id"], began)
            pending_sent = self.done(token)
            pending = self.records(mark, session=ids_["session_id"], hook="model.request")
            pending_outcome = self.outcome(pending_messages, began)
        released, _ = self.routing("record", event=dict(event_id=attempt + "-not-dispatched", attempt_id=attempt,
                                                        kind="not_dispatched"))
        guard_outcome = self.outcome(guard_messages, 0)
        chain = verify_plugin_chain(ids, guard, pending, guard_outcome["outcome"], guard_outcome["reason"], pending_outcome["reason"])
        if guard_sent or pending_sent:
            chain.append("probe_transmitted")
        self.note(stage, "plugin_chain", dict(
            plugins_http=status, present=chain_ids, api_order_documented=False,
            guard_probe=[dict(phase=r["phase"], decision=r.get("decision"), variant=r.get("variant")) for r in guard],
            guard_probe_reason=guard_outcome["reason"],
            pending_probe=dict(prepare=answer.get("state"), records=[r["phase"] for r in pending], **pending_outcome,
                               released=released.get("state")),
            sent=guard_sent + pending_sent, failures=chain))
        if released.get("state") != "recorded":
            chain.append("probe_not_released")
        return chain

    def stage0(self):
        s = "0"
        self.start(s)
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_before_ledger", dict(http=status, body=health, ms=ms))
        if status != 200 or (health or {}).get("state") != "not_ready":
            self.results["deviations"].append("stage 0: health before any ledger was not not_ready")
        answer, ms = self.routing("set", authority_required=True)
        self.note(s, "set_authority_required", dict(answer=answer, ms=ms))
        answer, ms = self.routing("prepare", **canary.plan_assignments(1, "qual-a1", pool=self.pool, account=self.route_account)[0]["body"])
        self.attempt = "qual-a1-01"
        self.note(s, "prepare", dict(state=answer.get("state"), reason=answer.get("reason"), ms=ms,
                                     billing_class=(answer.get("admission") or {}).get("billing_class")))
        if answer.get("state") != "admitted":
            raise Blocker("prepare did not admit the qualification attempt")
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_after_prepare", dict(http=status, body=health, ms=ms))
        if status != 200 or (health or {}).get("state") != "ok":
            raise Blocker("routing.health was not ok after prepare")
        answer, ms = self.routing("status")
        self.note(s, "status", dict(view=status_view(answer), ms=ms))
        ledger = answer.get("ledger") or {}
        if ledger.get("authority_required") is not True or ledger.get("unstamped_open_holds") != 0:
            raise Blocker("status does not show authority_required with zero unstamped open holds")
        failures = self.probes(s)
        if failures:
            raise Blocker("the recorder/shim/recorder chain is not proven: %s" % ", ".join(failures))

    # ------------------------------------------------------------ stage 1 (P0.7)
    def stage1(self):
        s = "1"
        import secrets as random_source
        from runtime import routing as core
        identity = "qual-identity-" + random_source.token_hex(8)
        ids = core.derive_ids("qual-p07", identity, random_source.token_hex(16))
        session, message = ids["session_id"], ids["message_id"]
        status, created, ms = self.create_session(session, title="qual p07")
        accepted = status == 200 and (self.data(created) or {}).get("id") == session
        self.note(s, "a_create_derived_session", dict(http=status, accepted_same_id=accepted, ms=ms))
        if not accepted:
            raise Blocker("the server did not accept the derived session id")
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        token = self.expect(s, "P0.7 (a) prompt with derived ids", 1)
        status, body, ms = self.prompt(session, message)
        returned = (self.data(body) or {}).get("id") if isinstance(self.data(body), dict) else None
        messages = self.wait_idle(session, began)
        sent = self.done(token)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        users = [m for m in messages if m.get("type") == "user"]
        self.note(s, "a_prompt", dict(http=status, ms=ms, response_id_is_derived=returned == message,
                                      user_message_ids_derived=[m.get("id") == message for m in users],
                                      model_request_invocations=len(invoked), kinds=sorted(r["kind"] for r in invoked),
                                      transmitted=sent, assistant_messages=len([m for m in messages if m.get("type") == "assistant"]),
                                      message_types=[m.get("type") for m in messages], **self.outcome(messages, began)))
        if not any(m.get("id") == message for m in users):
            raise Blocker("the server did not store the prompt under the derived message id")
        if sent != 1 or len(invoked) != 1:
            raise Blocker("one prompt with a derived message id caused %d transmitted requests (%d hook invocations)" % (sent, len(invoked)))
        mark = len(self.server.hooks())
        self.server.write_control(hold=[session])
        token = self.expect(s, "P0.7 (b) duplicate prompt", 0, exposure=1)
        status, body, ms = self.prompt(session, message)
        time.sleep(4)
        again = self.messages(session)
        self.server.write_control(hold=[])
        sent = self.done(token)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        returned = (self.data(body) or {}).get("id") if isinstance(self.data(body), dict) else None
        self.note(s, "b_duplicate", dict(http=status, ms=ms, returned_original_id=returned == message,
                                         model_request_invocations=len(invoked), transmitted=sent,
                                         added_types=[m.get("type") for m in again[len(messages):]]))
        if invoked or sent:
            raise Blocker("the duplicate prompt caused a second model request")
        status, body, ms = self.server.request("GET", "/api/session/%s/message/%s" % (session, message))
        self.note(s, "c_read_back", dict(http=status, ms=ms, same_id=(self.data(body) or {}).get("id") == message,
                                         type=(self.data(body) or {}).get("type")))
        status, created, _ = self.create_session(title="qual own id")
        own = (self.data(created) or {}).get("id")
        own_messages = [m.get("id") for m in messages if m.get("id") != message]
        self.note(s, "d_own_id_shapes", dict(session=id_shape(own), messages=[json.loads(x) for x in sorted(
            {json.dumps(id_shape(i), sort_keys=True) for i in own_messages})]))
        if id_shape(own)["derived_shape"] or any(id_shape(i)["derived_shape"] for i in own_messages):
            raise Blocker("an OpenCode-generated id has the derived shape")
        status, created, _ = self.create_session(title="qual order")
        order = (self.data(created) or {}).get("id")
        self.server.write_control(hold=[order])
        mark = len(self.server.hooks())
        low = high = None
        for index in range(400):
            candidate = core.derive_ids("qual-order-%d" % index, identity, None)["message_id"]
            low = low or (candidate if candidate[4] == "0" else None)
            high = high or (candidate if candidate[4] == "f" else None)
            if low and high:
                break
        token = self.expect(s, "P0.7 (e) ordering", 0, exposure=1)
        for chosen in (None, high, None, low, None):
            self.server.request("POST", "/api/session/%s/shell" % order, dict({"command": "true"}, **({"id": chosen} if chosen else {})))
            time.sleep(0.3)
        time.sleep(1)
        listed = [m.get("id") for m in self.messages(order) if m.get("type") == "shell"]
        self.server.write_control(hold=[])
        sent = self.done(token)
        positions = [listed.index(i) if i in listed else None for i in (high, low)]
        self.note(s, "e_ordering", dict(listed=["derived" if i in (low, high) else "server" for i in listed],
                                        derived_positions_high_then_low=positions, lexicographic_order=listed == sorted(listed),
                                        model_request_invocations=len(self.records(mark, session=order, hook="model.request")),
                                        transmitted=sent))
        if positions != [1, 3]:
            self.results["deviations"].append("stage 1 (e): derived message ids are not listed where they were inserted")

    # ------------------------------------------------------------ stage 2 (shim live)
    def stage2(self):
        s = "2"
        answer, ms = self.routing("dispatch", attempt_id=self.attempt, request=REQUEST)
        if answer.get("state") != "dispatched":
            raise Blocker("dispatch refused: %s" % answer.get("reason"))
        self.token = answer.pop("authority")
        self.redact.add_secret(self.token)
        session, message = answer["session_id"], answer["message_id"]
        self.session = session
        self.note(s, "dispatch", dict(state="dispatched", ms=ms, token_length=len(self.token),
                                      session_derived=bool(DERIVED_SESSION.fullmatch(session)),
                                      message_derived=bool(DERIVED_MESSAGE.fullmatch(message))))
        again, _ = self.routing("dispatch", attempt_id=self.attempt, request=REQUEST)
        self.note(s, "dispatch_repeated", dict(state=again.get("state"), has_authority="authority" in again))
        status, health, ms = self.server.rpc("health", {})
        if (health or {}).get("state") != "ok":
            raise Blocker("routing.health not ok before the managed launch")
        attach = dict(session_id=session, attempt_id=self.attempt, authority=self.token, funding_pool=POOL, model=ROUTE)
        status, body, ms = self.server.rpc("attach", {**attach, "authority": "A" * 43})
        self.note(s, "attach_wrong_token", dict(http=status, ms=ms))
        status, body, ms = self.server.rpc("attach", attach)
        self.note(s, "attach", dict(http=status, state=(body or {}).get("state"), ms=ms))
        if (body or {}).get("state") != "attached":
            raise Blocker("routing.attach with the right token failed")
        permissions = DENY_ALL + [{"action": "webfetch", "resource": "*", "effect": "ask"},
                                  {"action": "subagent", "resource": "*", "effect": "allow"}]
        _, created, _ = self.create_session(session, permissions=permissions)
        if (self.data(created) or {}).get("id") != session:
            raise Blocker("the derived session was not created")
        self.server.write_control(title_session=session)
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        token = self.expect(s, "managed primary request and its one title", 2)
        status, body, ms = self.prompt(session, message)
        messages = self.wait_idle(session, began)
        time.sleep(3)  # the title request may finish after the primary one
        transmitted = self.server.transmitted()[token["before"]:]
        sent = self.done(token)
        primaries = [r for r in transmitted if r["kind"] == "primary"]
        assistants = [m for m in messages if m.get("type") == "assistant"]
        self.note(s, "managed_request", dict(http=status, primary_transmitted=len(primaries), sent=sent,
                                             title_transmitted=len([r for r in transmitted if r["kind"] == "title"]),
                                             assistant_messages=len(assistants), **self.outcome(messages, began)))
        if len(primaries) != 1 or not assistants:
            raise Blocker("the managed request was not allowed and completed exactly once")
        server, counter = listener()
        url = "http://127.0.0.1:%d/qual" % server.server_address[1]
        mark = len(self.server.hooks())
        status, body, ms = self.server.request("POST", "/api/session/%s/permission" % session,
                                               {"action": "webfetch", "resources": [url], "metadata": {"url": url}})
        evaluated = self.records(mark, hook="permission.evaluate")
        server.shutdown()
        self.note(s, "webfetch_permission", dict(http=status, hook=[dict(phase=r["phase"], effect=r["effect"]) for r in evaluated],
                                                 listener_hits=counter.hits))
        post = [r for r in evaluated if r["phase"] == "post"]
        if post and post[-1]["effect"] != "deny":
            raise Blocker("webfetch was not denied for the attached session")
        _, created, _ = self.create_session(title="qual shell")
        mark = len(self.server.hooks())
        self.server.request("POST", "/api/session/%s/shell" % (self.data(created) or {}).get("id"), {"command": "true"})
        time.sleep(2)
        shells = self.records(mark, hook="shell.create.before")
        self.note(s, "shell_env_attached", [dict(phase=r["phase"], probe_present=r["probe_present"]) for r in shells])
        if any(r["phase"] == "post" and r["probe_present"] for r in shells):
            raise Blocker("the shell kept a credential-like variable while a session was attached")
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        self.server.write_control(hold=[session])  # the parent's follow-up turn after the child's failure is held
        token = self.expect(s, "subagent command on another model", 0, exposure=1)
        self.server.request("POST", "/api/session/%s/command" % session, {"name": "qualspawn", "text": ""})
        time.sleep(8)
        self.children, _ = self.descendants(session)
        for child in self.children:
            try:
                self.wait_idle(child, began, timeout=30)
            except Blocker:
                pass
        self.wait_idle(session, 0, timeout=30)
        self.server.write_control(hold=[])
        sent = self.done(token)
        self.note(s, "subagent_other_model", dict(children=len(self.children), transmitted=sent,
                                                  child_outcomes=[self.outcome(self.messages(c), began) for c in self.children],
                                                  tool_hook=len(self.records(mark, hook="tool.execute.before"))))
        if sent or any(r.get("decision") == "guard_model" for r in self.records(mark)):
            raise Blocker("a request on another model passed the shim")
        status, body, ms = self.server.rpc("detach", dict(session_id=session, attempt_id=self.attempt, authority="A" * 43))
        self.note(s, "detach_wrong_token", dict(http=status))
        status, body, ms = self.server.rpc("detach", dict(session_id=session, attempt_id=self.attempt, authority=self.token))
        self.note(s, "detach", dict(http=status, state=(body or {}).get("state")))
        self.note(s, "prompt_without_attachment", self.blocked_prompt(s, "prompt, session not attached", session, "attach_required"))
        self.server.rpc("attach", {**attach, "authority": "B" * 43})
        self.note(s, "prompt_after_wrong_token", self.blocked_prompt(s, "prompt after a wrong-token attach", session, "attach_required"))

    # ------------------------------------------------------------ stage 3 (P0.8)
    def stage3(self):
        s = "3"
        from runtime import opencode_accounting as accounting
        from runtime.adapters import opencode as adapter
        titles = sum(1 for r in self.server.transmitted() if r.get("kind") == "title" and r.get("session") == self.session)
        try:
            read = self.account_settled(self.session, titles)
        except (accounting.AccountingError, adapter.AdapterError, canary.AccountingRegressed) as error:
            raise Blocker("the live session could not be accounted: %s" % canary.classify(error))
        self.note(s, "rereads", dict(count=read["rereads"], deviation=read["deviation"]))
        if read["deviation"]:
            self.results["deviations"].append("stage 3: %s" % read["deviation"])
        result, state = read["result"], read["state"]
        title = accounting.title_tokens(read["total"]["tokens"], read["normalized"])
        events, failures = [], 0
        for event in self.events.events if self.events else []:
            if isinstance(event, dict) and str(event.get("type", "")).startswith("session."):
                try:
                    events.append(adapter.normalize_event(event))
                except adapter.AdapterError:
                    failures += 1
        tracked = accounting.track_events(events)
        resync = accounting.reconcile_with_state(result, state, funding="subscription", tracker=tracked["state"])
        requests = (result["usage"] or {}).get("requests") or []
        self.note(s, "reread", dict(message_pages=read["pages"], children=read["children"], lineage_entries=read["lineage"],
                                    message_types=[m.get("type") for m in read["raw"]], session_tokens=read["total"]["tokens"]))
        self.note(s, "usage_from_messages", dict(source=(result["usage"] or {}).get("source"),
                                                 requests=[dict(kind=r["kind"], tokens=r["tokens"], cost_usd_micros=r.get("cost_usd_micros"))
                                                           for r in requests], title=result["title"], unknown=result["unknown"]))
        self.note(s, "title_tokens", title)
        self.note(s, "consistency_report", accounting.consistency_report(state))
        self.note(s, "events", dict(session_events=len(events), normalize_failures=failures, counts=tracked["counts"],
                                    resync_needed=[r["reason"] for r in tracked["resync_needed"]]))
        self.note(s, "reconcile_with_state", resync["changes"])
        if any(resync["changes"]["tokens_delta"].values()) or resync["changes"]["messages_added"] or resync["changes"]["messages_missing"]:
            raise Blocker("reconcile_with_state on the re-read state is not zero")
        answer, ms = self.routing("record", event=dict(event_id=self.attempt + "-complete", attempt_id=self.attempt,
                                                       kind="complete", stage="worker", usage=result["usage"]))
        settlement = answer.get("settlement") or {}
        self.note(s, "record_complete", dict(state=answer.get("state"), reason=answer.get("reason"), outcome=answer.get("outcome"),
                                             ms=ms, settlement=settlement))
        if answer.get("state") != "recorded":
            raise Blocker("record complete refused: %s" % answer.get("reason"))
        status, _ = self.routing("status")
        view = status_view(status)
        self.note(s, "status_after_complete", view)
        ledger = view["ledger"]
        if (ledger.get("cost_unknown") or {}).get("ids") or (ledger.get("uncertain_dollar") or {}).get("ids") or \
                ledger.get("blocked_organizations") or ledger.get("unstamped_open_holds"):
            raise Blocker("status is not clean after completion")
        if settlement.get("kind") != "window":
            raise Blocker("a subscription hold did not settle as a window observation")

    def stage2_closed(self):
        s = "2"
        self.note(s, "prompt_closed_attempt", self.blocked_prompt(s, "prompt after record complete", self.session, "attempt_closed"))
        listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
        killed = 0
        for line in listing.splitlines():
            if "routing.py" in line and "--serve" in line and str(self.server.ledger) in line:
                os.kill(int(line.split()[0]), signal.SIGKILL)
                killed += 1
        self.note(s, "routing_child_killed", killed)
        _, created, _ = self.create_session(title="qual interactive")
        interactive = (self.data(created) or {}).get("id")
        mark, began = len(self.server.hooks()), int(time.time() * 1000)
        token = self.expect(s, "interactive session, routing child killed", 1)
        self.prompt(interactive)
        messages = self.wait_idle(interactive, began)
        sent = self.done(token)
        pairs, _ = latency_pairs(self.records(mark, session=interactive))
        self.note(s, "interactive_child_killed", dict(transmitted=sent, shim_ms=[p["ms"] for p in pairs], **self.outcome(messages, began)))
        if sent != 1:
            raise Blocker("the interactive session was not left untouched with the routing child down")
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_after_kill", dict(http=status, state=(health or {}).get("state"), child=(health or {}).get("child")))

    # ------------------------------------------------------------ stage 4 (lost acknowledgment)
    def stage4(self):
        s = "4"
        body = canary.plan_assignments(1, "qual-a2", pool=self.pool, account=self.route_account)[0]["body"]
        attempt = body["assignment"]["attempt_id"]
        answer, _ = self.routing("prepare", **body)
        if answer.get("state") != "admitted":
            raise Blocker("prepare for the lost-acknowledgment drill failed")
        code = ("import json, os, signal, subprocess, sys\n"
                "done = subprocess.run(sys.argv[1:], input=sys.stdin.read(), capture_output=True, text=True)\n"
                "answer = json.loads(done.stdout)\n"
                "print(json.dumps({k: answer.get(k) for k in ('state', 'session_id', 'message_id')}), flush=True)\n"
                "os.kill(os.getpid(), signal.SIGKILL)\n")
        launcher = subprocess.run([sys.executable, "-I", "-B", "-c", code, sys.executable, "-I", "-B", str(ROOT / "scripts" / "routing.py"),
                                   "dispatch", *self.routing.argv],
                                  input=json.dumps(dict(v=1, op="dispatch", attempt_id=attempt, request=REQUEST)),
                                  capture_output=True, text=True, timeout=60)
        lost = json.loads(launcher.stdout.splitlines()[0])
        self.note(s, "lost_launcher", dict(dispatch_state=lost.get("state"), launcher_exit=launcher.returncode))
        if lost.get("state") != "dispatched":
            raise Blocker("the drill's dispatch did not happen")
        session, message = lost["session_id"], lost["message_id"]
        self.note(s, "status_after_lost_launcher", status_view(self.routing("status")[0]))
        self.note(s, "host_read", dict(message_http=self.server.request("GET", "/api/session/%s/message/%s" % (session, message))[0],
                                       session_http=self.server.request("GET", "/api/session/%s" % session)[0]))
        answer, _ = self.routing("check", session_id=session, request=PROBE_REQUEST)
        self.note(s, "check_session_dispatched", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("record", event=dict(event_id=attempt + "-uncertain", attempt_id=attempt, kind="uncertain"))
        self.note(s, "record_uncertain", dict(state=answer.get("state"), reason=answer.get("reason")))
        self.note(s, "status_after_uncertain", status_view(self.routing("status")[0]))
        answer, _ = self.routing("check", attempt_id=attempt, session_id=session, request=REQUEST)
        self.note(s, "check_after_uncertain", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("record", event=dict(event_id=attempt + "-complete", attempt_id=attempt, kind="complete"))
        self.note(s, "complete_on_uncertain", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("reconcile_uncertain", attempt_id=attempt, evidence=dict(kind="charge_reservation"),
                                 event_id=attempt + "-reconcile")
        self.note(s, "reconcile_uncertain_subscription", dict(state=answer.get("state"), reason=answer.get("reason")))
        self.note(s, "status_final", status_view(self.routing("status")[0]))

    # ------------------------------------------------------------ canary
    def canary_run(self):
        """The pilot on the dedicated canary state. Order matters (D3): every refusal decidable without a server is
        checked before anything is written to the project or a server starts; health and the probes follow the
        start, and no admission begins unless they pass."""
        s = "pilot"
        self.note(s, "canary_state", canary.initialize_state(self.routing))
        before = self.routing("status")[0]
        self.note(s, "status_at_start", status_view(before))
        refusals = canary.preflight(before)
        if refusals:
            raise Refused("canary refused before any write: %s. %s" % (", ".join(refusals), canary.RESET_ADVICE))
        canary.check_ancestor_configs(self.canary_project)
        if self.ledger.remaining("pilot") < 4:  # two probe exposures plus the first admission and its title
            raise Refused("the pilot call budget is exhausted")
        self.repo_start = repo_snapshot(self.canary_project)
        self.note(s, "repo_at_start", dict(dirty_paths=len(self.repo_start["dirty"]), head_known=self.repo_start["head"] is not None))
        self.start(s)  # creates the git-excluded project config and starts the server
        health = self.server.rpc("health", {})[1] or {}
        chain = self.probes(s)
        status = self.routing.served("status")[0]
        refusals = canary.preconditions(status, health, dict(chain=chain))
        self.note(s, "preconditions", dict(refusals=refusals, health=health.get("state")))
        if refusals:
            raise Refused("canary refused: %s" % ", ".join(refusals))
        driver = canary.PilotDriver(CanaryEnv(self), self.ledger, n=self.admissions, run=self.run_id, tag=self.run_id)
        result = driver.run()
        self.note(s, "pilot", result)
        self.note(s, "status_at_end", status_view(self.routing.served("status")[0]))
        if result["abort"]:
            self.results["blockers"].append("pilot aborted: %s" % result["abort"]["reason"])

    # ------------------------------------------------------------ run, cleanup, output
    def snapshot_user_dirs(self):
        home = Path.home()
        return dict({name: snapshot_tree(home / name) for name in USER_OPENCODE_DIRS}, watched=safety.watched_home(home))

    def report_only_home(self):
        found = {}
        for name in REPORT_ONLY_HOME:
            try:
                found[name] = snapshot_tree(Path.home() / name, max_depth=2)
            except Refused:
                found[name] = None
        claude_json = Path.home() / ".claude.json"
        found[".claude.json"] = {"": claude_json.stat().st_mtime_ns} if claude_json.exists() else {}
        return found

    def run(self):
        user_before, home_before = self.snapshot_user_dirs(), self.report_only_home()
        previous = {sig: signal.signal(sig, _interrupt) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            if self.canary_project:
                self.canary_run()
            elif self.purpose == "qualification-2":
                probe_stages.run_stages(self)
            elif self.purpose == "chatgpt":
                chatgpt.run(self)
            else:
                for number, stage in ((0, self.stage0), (1, self.stage1), (2, self.stage2), (3, self.stage3),
                                      (3, self.stage2_closed), (4, self.stage4)):
                    if number > self.through:
                        break
                    stage()
        except Exception as error:  # noqa: BLE001 - every failure is recorded; cleanup always runs
            self.results["blockers"].append("%s: %s" % (type(error).__name__, error))
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)  # a second signal must not interrupt cleanup
            try:
                self.cleanup()
            except Exception as error:  # noqa: BLE001 - reported; the run then fails
                self.results["blockers"].append("cleanup failed: %s" % type(error).__name__)
                self.results["cleanup"] = dict(verified_by_harness=False)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        self.compare_user_dirs(user_before, home_before)
        return self.results

    def read_hooks(self):
        """The recorder log, tolerant of a damaged last line (a write cut short): one retry, then recorded as unreadable."""
        for attempt in range(2):
            try:
                return (self.server.hooks() if self.server.log.exists() else []), False
            except Blocker:
                if attempt == 0:
                    time.sleep(0.2)
        return [], True

    def cleanup(self):
        """Stop and restore first, each step on its own so one failure never skips the next; read results after."""
        errors = []

        def step(name, action):
            try:
                return action()
            except Exception as error:  # noqa: BLE001 - recorded; the remaining steps still run
                errors.append("%s: %s" % (name, type(error).__name__))
                return None
        if self.events:
            self.events.stopped = True
        pgid = self.server.process.pid if self.server.process else None
        step("stop_server", self.server.stop)
        config_removed = step("remove_project_config", self.server.project_config.remove) if self.server.project_config else None
        step("stop_routing", self.routing.stop)
        left = step("kill_leftovers", lambda: kill_leftovers(pgid, [str(self.base), os.path.realpath(self.base)]))
        hooks, unreadable = self.read_hooks()
        self.results["hooks_unreadable"] = unreadable
        self.results["hook_records"] = len(hooks)
        pairs, blocked = latency_pairs(hooks)
        by_hook = {}
        for pair in pairs:
            by_hook.setdefault("%s/%s" % (pair["hook"], pair.get("kind") or "-"), []).append(pair["ms"])
        self.results["latency"] = dict(shim_ms_by_hook={k: summarize_ms(v) for k, v in sorted(by_hook.items())},
                                       unmatched_pre_records=len(blocked))
        self.results["off_route_requests"] = sum(1 for r in hooks if r.get("hook") == "model.request" and r.get("phase") == "pre"
                                                 and not on_route(r))
        fields = ("phase", "hook", "kind", "model", "variant", "tool", "effect", "probe_present", "decision", "session")
        self.results["hooks"] = [{k: r.get(k) for k in fields if r.get(k) is not None} for r in hooks]
        if not self.keep:
            step("remove_isolated_dir", lambda: shutil.rmtree(self.base, ignore_errors=True))
        dir_removed = not self.base.exists()
        self.results["cleanup"] = dict(processes_left=None if left is None else len(left), isolated_dir_removed=dir_removed,
                                       kept=self.keep, project_config_removed=config_removed, errors=errors,
                                       verified_by_harness=not errors and left == [] and (dir_removed or self.keep)
                                       and config_removed is not False)
        if not self.results["cleanup"]["verified_by_harness"]:
            self.results["blockers"].append("cleanup could not be verified")
        self.results["call_ledger"] = step("call_ledger", self.ledger.summary)

    def compare_user_dirs(self, before, home_before):
        after = self.snapshot_user_dirs()
        changes = {}
        for name in USER_OPENCODE_DIRS:
            unexpected, churn, session = safety.classify_snapshots(before[name], after[name], name == safety.SESSION_OUTPUT_DIR,
                                                                   Path.home() / name)
            changes[name] = dict(unexpected=unexpected[:50], unexpected_count=len(unexpected), desktop_churn_count=len(churn),
                                 session_output_count=len(session))
            if unexpected:
                self.results["blockers"].append("the user's %s changed during the run" % name)
        codex, unexpected = safety.watched_changes(before["watched"], safety.watched_home(Path.home()))
        if unexpected:
            self.results["blockers"].append("the user's ~/.codex credentials or config changed during the run")
        home_after = self.report_only_home()
        report = {}
        for name, snapshot in home_before.items():
            if snapshot is None or home_after.get(name) is None:
                report[name] = "not snapshotted (too many entries)"
                continue
            diff = compare_snapshots(snapshot, home_after[name])
            report[name] = {key: len(value) for key, value in diff.items()}
        self.results["user_dirs"] = dict(opencode=changes, codex=codex, home_report_only=report)

    def output(self, results):
        meta = dict(opencode=QUALIFIED_OPENCODE, date=time.strftime("%Y-%m-%d"), mode=self.purpose,
                    provider=self.route_ref() if self.route else None,
                    counting=COUNTING, sanitized=True, generated_by="tests/qualification/opencode_live.py (no post-processing)")
        return self.redact(dict(_meta=meta, **results))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--isolated-dir")
    parser.add_argument("--call-ledger", help="the durable call ledger (owner-only, outside every run directory)")
    parser.add_argument("--init-call-ledger", metavar="PATH", help="create the call ledger once, seeded with the 5 calls spent")
    parser.add_argument("--export-call-ledger", metavar="OUT", help="write the sanitized ledger view (qual-call-ledger.json)")
    parser.add_argument("--through", type=int, default=4, help="last qualification stage to run")
    parser.add_argument("--canary", action="store_true", help="run the bounded pilot canary instead")
    parser.add_argument("--stages", help="live probes on claude-agent/haiku#low under purpose qualification-2: "
                                         "descendants,s33d (docs/smart-routing-qualification.md)")
    parser.add_argument("--chatgpt", action="store_true", help="the ChatGPT route probe under purpose chatgpt (its own run)")
    parser.add_argument("--chatgpt-methods", action="store_true", help="0 calls: list the openai integration's methods")
    parser.add_argument("--chatgpt-model", help="chatgpt: openai/MODEL[#VARIANT] from the login's list, or auto (cheapest)")
    parser.add_argument("--chatgpt-timeout", type=int, default=900, help="chatgpt: seconds to wait for the sign-in")
    parser.add_argument("--project", help="canary: the allowlisted checkout")
    parser.add_argument("--canary-state", default=str(canary.DEFAULT_STATE),
                        help="canary: the dedicated routing state directory (never the production one)")
    parser.add_argument("--set-cap", nargs=2, metavar=("PURPOSE", "CAP"),
                        help="append a recorded cap change (up to the code's recorded maximum; needs --reason)")
    parser.add_argument("--reason", help="the recorded reason for --set-cap")
    parser.add_argument("--approval", nargs=3, metavar=("BY", "AT", "SCOPE"), help="--set-cap: who approved, when, for what")
    parser.add_argument("--reset-canary-state", action="store_true",
                        help="delete the dedicated canary state (refused while a harness process is alive)")
    parser.add_argument("--settle-dead", type=int, metavar="SEQ",
                        help="settle a dead run's reservation at its reserved count (needs --call-ledger)")
    parser.add_argument("--admissions", type=int, default=canary.PILOT_MAX, help="canary: admissions (1-20)")
    parser.add_argument("--out", help="write the sanitized results JSON here (outside the isolated dir)")
    parser.add_argument("--keep", action="store_true", help="leave the isolated dir in place (it holds the server password)")
    args = parser.parse_args(argv)
    try:
        if args.init_call_ledger:
            CallLedger.initialize(Path(args.init_call_ledger).expanduser())
            print(json.dumps(CallLedger(Path(args.init_call_ledger).expanduser()).summary(), indent=2))
            return 0
        if args.set_cap:
            purpose, cap = args.set_cap
            if not cap.isdigit():
                raise Refused("the cap must be an integer")
            approval = dict(zip(("by", "at", "scope"), args.approval)) if args.approval else None
            print(json.dumps(CallLedger(args.call_ledger or "").set_cap(purpose, int(cap), args.reason or "", approval=approval),
                             indent=2))
            return 0
        if args.reset_canary_state:
            print(json.dumps(canary.reset_state(Path(args.canary_state), processes()), indent=2))
            return 0
        if args.settle_dead is not None:
            totals = CallLedger(args.call_ledger or "").settle_dead(args.settle_dead, processes())
            print(json.dumps(totals, indent=2))
            return 0
        if args.export_call_ledger:
            exported = CallLedger(args.call_ledger or "").export()
            Path(args.export_call_ledger).write_text(json.dumps(exported, indent=1) + "\n")
            return 0
        if not args.isolated_dir:
            parser.error("--isolated-dir is required")
        base = check_isolated_dir(args.isolated_dir)
        ledger = CallLedger(args.call_ledger or "")
        if args.out and (Path(os.path.realpath(args.out)) == base or base in Path(os.path.realpath(args.out)).parents):
            raise Refused("--out must be outside the isolated dir, which is removed")
        if sum(map(bool, (args.canary, args.stages, args.chatgpt, args.chatgpt_methods))) > 1:
            raise Refused("--canary, --stages, --chatgpt and --chatgpt-methods are separate runs")
        if args.keep and (args.chatgpt or args.chatgpt_methods):
            raise Refused("--keep is refused with --chatgpt and --chatgpt-methods: the dir would keep OpenCode's stored login")
        if args.chatgpt_model and not args.chatgpt:
            raise Refused("--chatgpt-model belongs to --chatgpt")
        stages = probe_stages.parse_stages(args.stages) if args.stages else None
        if args.chatgpt:
            ref = args.chatgpt_model or ""  # without #VARIANT the lowest listed variant; `auto`: chatgpt.AUTO_RULE
            route = ("openai",) if ref == chatgpt.AUTO else safety.parse_route(ref if "#" in ref or not ref else ref + "#lowest")
            if route[0] != "openai":
                raise Refused("the chatgpt run allows only an openai model")
            if not 60 <= args.chatgpt_timeout <= 1800:
                raise Refused("--chatgpt-timeout is 60 to 1800 seconds")
        purpose = "qualification-2" if stages else "chatgpt" if args.chatgpt or args.chatgpt_methods else None
        project = canary.check_project(args.project) if args.canary else None
        if args.canary:
            canary.check_ancestor_configs(project)
        state = canary.check_canary_state(args.canary_state, isolated=base) if args.canary else None
        if args.canary:
            canary.plan_assignments(args.admissions, "check")
        check_real_home()  # the last check: nothing has started yet
        check_opencode_version(env=server_environment(base))
    except Refused as error:
        print("refused: %s" % error, file=sys.stderr)
        return 2
    run = Run(base, ledger, args.through, canary_project=project, admissions=args.admissions, keep=args.keep,
              canary_state=state, purpose=purpose, stages=stages, chatgpt_model=args.chatgpt_model)
    run.chatgpt_timeout, run.chatgpt_methods = args.chatgpt_timeout, args.chatgpt_methods
    results = run.run()
    clean = run.output(results)
    text = json.dumps(clean, indent=2, sort_keys=True)
    leaks = run.redact.leaks(text)
    if leaks:
        print("refused to write output: %d unsanitized fragments; wrote a skeleton" % len(leaks), file=sys.stderr)
        if args.out:  # blockers (masked), cleanup status, ledger totals and the leak kinds only
            Path(args.out).write_text(json.dumps(sanitize.skeleton(clean, leaks), indent=2, sort_keys=True) + "\n")
        return 3
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(json.dumps(dict(blockers=clean["blockers"], cleanup=clean.get("cleanup"), call_ledger=clean.get("call_ledger")), indent=2))
    if args.keep:
        print(KEEP_WARNING, file=sys.stderr)
    return 1 if results["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
