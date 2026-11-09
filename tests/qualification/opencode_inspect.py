"""Read-only re-read of a stopped qualification run (tests/qualification/opencode_live.py --keep) on its own dir.

Restarts the server on the same directories with nothing permitted (`qual-post` refuses every model request), re-reads
the P0.7 and managed sessions, probes one refused RPC and runs the accounting on the root with and without the
command-spawned child. Prints shapes, counts and fixed reasons only, through the harness's `Redactor`.

It refuses a directory that is not a kept run (owner-only, holding `hooks.jsonl` and `server.log`, outside OpenCode's
user directories and any git worktree) or whose server is still running, and a run that has fewer than the three
sessions it reads. Remove the directory afterwards: it holds the server password.

  python3 -B tests/qualification/opencode_inspect.py <kept isolated dir>
"""
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime import opencode_accounting as accounting  # noqa: E402
from runtime.adapters import opencode as adapter  # noqa: E402
from tests.qualification import opencode_live as live  # noqa: E402
from tests.qualification.safety import (PROTECTED, Redactor, Refused, ROUTE, inside_git_worktree,  # noqa: E402
                                        leftover_processes)
from tests.qualification.server import Server, processes  # noqa: E402


def check_kept_run(path):
    base = Path(path).resolve()
    info = base.stat()
    home = Path.home().resolve()
    if any(base == home / p or (home / p) in base.parents for p in PROTECTED) or inside_git_worktree(base):
        raise Refused("not an isolated run directory")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused("the run directory must be owner-only")
    if not (base / "hooks.jsonl").is_file() or not (base / "server.log").is_file():
        raise Refused("not a kept qualification run")
    if leftover_processes(processes(), None, [str(base)]):
        raise Refused("a process of this run is still running; stop it first")
    return base


class Reader:
    messages = live.Run.messages
    data = staticmethod(live.Run.data)

    def __init__(self, server):
        self.server = server


def main(path):
    base = check_kept_run(path)
    hooks = [json.loads(line) for line in (base / "hooks.jsonl").read_text().splitlines() if line.strip()]
    sessions = []
    for record in hooks:
        if record.get("hook") == "model.request" and record.get("session") not in sessions:
            sessions.append(record["session"])
    if len(sessions) < 3:
        raise Refused("the run has %d sessions with model requests; the inspection reads three" % len(sessions))
    p07, managed, child = sessions[0], sessions[1], sessions[2]
    server = Server(base)
    server.start()
    server.write_control(budget=0, hold=sessions)
    try:
        run = Reader(server)
        redact = Redactor(secrets=[server.password], paths=[str(base)])
        out = {}
        msgs = run.messages(p07)
        out["p07_types"] = [(m.get("type"), m.get("delivery"), m.get("outcome")) for m in msgs]
        status, body, _ = server.rpc("attach", dict(session_id="ses_" + "0" * 32, attempt_id="qual-a1", authority="A" * 43,
                                                    funding_pool="sub", model=ROUTE))
        out["rpc_refusal"] = dict(http=status, body=redact(body))
        root = run.messages(managed)
        out["managed_types"] = [(m.get("type"), m.get("outcome")) for m in root]
        _, record, _ = server.request("GET", "/api/session/%s" % managed)
        total = accounting.normalize_session(run.data(record))
        normalized = [adapter.normalize_message(m) for m in root]
        out["managed_normalized_kinds"] = [(n.get("kind"), n.get("type")) for n in normalized]
        out["session_total"] = total["tokens"]
        _, child_record, _ = server.request("GET", "/api/session/%s" % child)
        child_total = accounting.normalize_session(run.data(child_record))
        out["child"] = dict(parent_is_managed=run.data(child_record).get("parentID") == managed, tokens=child_total["tokens"],
                            types=[m.get("type") for m in run.messages(child)])
        result = accounting.usage_from_messages(normalized, total["tokens"], session_id=managed, funding="subscription",
                                                complete=True, session_cost=total["cost"])
        out["root_only"] = dict(requests=[dict(kind=r["kind"], tokens=r["tokens"], cost=r.get("cost_usd_micros"))
                                          for r in result["usage"]["requests"]], unknown=result["unknown"], title=result["title"])
        print(json.dumps(redact(out), indent=1, default=str))
        print("transmitted during the inspection: %d" % (len(server.transmitted()) - sum(1 for r in hooks if r.get("decision") == "transmit")),
              file=sys.stderr)
    finally:
        server.stop()


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (IndexError, Refused, FileNotFoundError) as error:
        print("refused: %s" % error, file=sys.stderr)
        sys.exit(2)
