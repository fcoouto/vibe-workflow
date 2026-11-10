"""`CanaryEnv`: the live environment the pilot driver (canary.PilotDriver) runs against, over a `Run` in canary mode.

It maps the driver's contract (see canary.py) onto the isolated OpenCode server, the routing shim's RPC and the
launcher's persistent routing child. Only the first admission's session is created untitled (it observes the one
real title request); later sessions are titled at creation.
"""
import json
import signal
import time

from tests.qualification import canary
from tests.qualification.safety import Blocker, ROUTE, latency_pairs, on_route, repo_changes, repo_snapshot, shape_of


class CanaryEnv:
    """The live environment the pilot driver runs against (see canary.py for the contract)."""

    def __init__(self, run):
        self.run = run

    def export_if_due(self):
        """The canary admissions' 12-hour replay export, read-only on the canary ledger (replay_export)."""
        from tests.qualification import replay_export
        return replay_export.export_if_due(self.run.canary_state["ledger"], self.run.replay_dir)

    def status(self):
        return self.run.routing.served("status")[0]

    def health(self):
        return self.run.server.rpc("health", {})[1] or {}

    def prepare(self, body):
        body = json.loads(json.dumps(body))
        if "observations" in body:  # the unmanaged pilot's own snapshot; a managed canary sends none (server.Routing)
            body["observations"]["now"] = int(time.time())
        return self.run.routing.served("prepare", **body)

    def dispatch(self, attempt):
        answer, ms = self.run.routing.served("dispatch", attempt_id=attempt, request=canary.request())
        self.run.redact.add_secret(answer.get("authority"))
        return answer, ms

    def attach(self, session, attempt, token):
        status, body, _ = self.run.server.rpc("attach", dict(session_id=session, attempt_id=attempt, authority=token,
                                                             funding_pool=canary.POOL, model=ROUTE))
        return status == 200 and (body or {}).get("state") == "attached"

    def detach(self, session, attempt, token):
        status, body, _ = self.run.server.rpc("detach", dict(session_id=session, attempt_id=attempt, authority=token))
        return status == 200 and (body or {}).get("state") == "detached"

    def launch(self, session, message, allow_title):
        run = self.run
        # Only the first admission observes a real title; later sessions are titled at creation, so OpenCode sends no
        # title request (the guard still blocks any unexpected one at 0 tokens).
        _, created, _ = run.create_session(session, title=None if allow_title else "pilot admission")
        if (run.data(created) or {}).get("id") != session:
            return dict(idle=False)
        run.server.write_control(title_session=session if allow_title else None)
        mark, began = len(run.server.hooks()), int(time.time() * 1000)
        run.gate(2 if allow_title else 1)  # the driver already wrote the reservation ahead
        run.prompt(session, message)
        try:
            messages = run.wait_idle(session, began)
            if allow_title:
                time.sleep(3)
        except Blocker:
            return dict(idle=False)
        finally:
            run.gate(0)
        records = run.server.hooks()[mark:]
        sent = [r for r in records if r.get("decision") == "transmit"]
        transmitted = dict(primary=sum(1 for r in sent if r["session"] == session and r["kind"] == "primary"),
                           title=sum(1 for r in sent if r["session"] == session and r["kind"] == "title"))
        transmitted["other"] = len(sent) - transmitted["primary"] - transmitted["title"]
        pairs, _ = latency_pairs([r for r in records if r.get("session") == session and r.get("kind") == "primary"])
        return dict(idle=True, transmitted=transmitted, outcome=run.outcome(messages, began)["outcome"],
                    off_route=sum(1 for r in records if r.get("hook") == "model.request" and r.get("phase") == "pre" and not on_route(r)),
                    blocked_titles=sum(1 for r in records if r.get("decision") == "guard_title"),
                    check_ms=pairs[0]["ms"] if pairs else None)

    def account(self, session, titles_sent=0):
        read = self.run.account_settled(session, titles_sent)
        return dict(read["result"], rereads=read["rereads"], deviation=read["deviation"])

    def progress(self):
        return dict(self.run.account_progress or {})

    def diagnostics(self, session):
        """Structure only (no text, no ids) of what accounting read, kept for an aborted admission."""
        run = self.run
        _, record, _ = run.server.request("GET", "/api/session/%s" % session)
        messages = run.messages(session)
        _, listing, _ = run.server.request("GET", "/api/session")
        listed = run.data(listing)
        return dict(record=shape_of(run.data(record)), messages=[shape_of(m) for m in messages[:12]], message_count=len(messages),
                    listing=dict(type=type(listed).__name__, count=len(listed) if isinstance(listed, list) else None))

    def record(self, event):
        return self.run.routing.served("record", event=event)

    def repo_changes(self):
        """Working-tree changes since the pilot started (D5): new dirty paths abort, already-dirty ones are reported."""
        return repo_changes(self.run.repo_start, repo_snapshot(self.run.canary_project))

    def quiesce(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)

    def kill_switch(self):
        status = self.status()
        answer, _ = self.run.routing.served("set", expected_revision=status.get("settings_revision"),
                                            change={"smart_routing": False})
        return dict(state=answer.get("state"), reason=answer.get("reason"))
