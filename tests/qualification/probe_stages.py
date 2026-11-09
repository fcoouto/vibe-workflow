"""Live-probe stages on claude-agent/haiku#low under the durable purpose `qualification-2` (pre-registered in
docs/smart-routing-qualification.md, "Live probes: descendants, S3.3d and the ChatGPT route").

- `descendants` (at most 5 calls): the required live observation of the routing shim's `routing.descendants` RPC
  (#152, clients/opencode.md). Positive run: a tool subagent (the model is asked to call the subagent tool), a
  `subagent: true` command child and an API-created child under an attached derived root; the shim's set must be
  complete and equal the launcher's own listing; accounting with `shim_descendants`; `record complete`; `detach`;
  a further `descendants` must be refused. Negative run (0 calls): the root is created before `attach`, so the shim
  must answer `complete: false` with `root_creation_unobserved`.
- `s33d` (at most 4 calls): the S3.3d required live probe. A managed root whose profile allows only `edit` asks the
  model to write `.opencode/command/x.md`, `sub/opencode.json` (both config paths: denied) and `agents/ok.txt`
  (ordinary: allowed, resolved against the session directory); then a `` !`env` `` command under the attached root
  records only whether the dummy credential-like variable was visible; image generation only if a claude-agent image
  model exists (otherwise unobserved).

Every send is written ahead to the durable ledger; nothing runs before `setup` has proved the plugin chain.
"""
import json
import os
from pathlib import Path
import time

from tests.qualification import canary
from tests.qualification.safety import Blocker, Refused, compare_snapshots, snapshot_tree

STAGES = ("descendants", "s33d")
BUDGETS = dict(descendants=5, s33d=4)
PURPOSE = "qualification-2"
DENY_ALL = [{"action": "*", "resource": "*", "effect": "deny"}]
TOOL_PROMPT = ("Use the subagent tool exactly once with agent general, model claude-agent/haiku#low and the prompt "
               "'Reply with exactly: OK'. After it returns, reply with exactly: DONE")
EDIT_PROMPT = ("Use your file tools to create three files: .opencode/command/x.md containing x, sub/opencode.json "
               "containing {}, and agents/ok.txt containing ok. If a tool call is denied, do not retry it. "
               "Then reply with exactly: DONE")
CONFIG_SEGMENTS = {".opencode", ".claude", ".agents", "opencode.json", "opencode.jsonc"}
CONFIG_FILES = (".opencode/command/x.md", "sub/opencode.json")
ORDINARY_FILE = "agents/ok.txt"


def parse_stages(text):
    names = [name for name in (text or "").split(",")]
    if not names or any(name not in STAGES for name in names) or len(set(names)) != len(names):
        raise Refused("--stages takes descendants and/or s33d, comma-separated, each once")
    return names


def now_ms():
    return int(time.time() * 1000)


def setup(run, stage):
    """Server, authority and the plugin-chain proof: nothing is sent before these pass."""
    run.start(stage)
    answer, _ = run.routing("set", authority_required=True)
    status, health, _ = run.server.rpc("health", {})
    run.note(stage, "setup", dict(authority_required=answer.get("state"), health=(health or {}).get("state"),
                                  managed_denies=(health or {}).get("managed_denies")))
    if status != 200 or (health or {}).get("state") != "ok":
        raise Blocker("routing.health was not ok")
    failures = run.probes(stage)
    if failures:
        raise Blocker("the recorder/shim/recorder chain is not proven: %s" % ", ".join(failures))


def run_stages(run):
    setup(run, "setup")
    for name in run.stage_names:
        dict(descendants=descendants, s33d=s33d)[name](run)


def need(run, stage):
    remaining = run.ledger.remaining(run.purpose)
    if remaining < BUDGETS[stage]:
        raise Refused("%s needs %d calls of %s; %d remain" % (stage, BUDGETS[stage], run.purpose, remaining))


def managed(run, stage, tag):
    """prepare -> health -> dispatch for one attempt on the run's route; the token stays in memory only."""
    body = canary.plan_assignments(1, tag, pool=run.pool, account=run.route_account, route=run.route_choice())[0]["body"]
    answer, _ = run.routing("prepare", **body)
    if answer.get("state") != "admitted":
        raise Blocker("prepare refused: %s" % answer.get("reason"))
    attempt = body["assignment"]["attempt_id"]
    if (run.server.rpc("health", {})[1] or {}).get("state") != "ok":
        raise Blocker("routing.health was not ok before dispatch")
    answer, _ = run.routing("dispatch", attempt_id=attempt, request=run.request_facts())
    if answer.get("state") != "dispatched":
        raise Blocker("dispatch refused: %s" % answer.get("reason"))
    token = answer.pop("authority")
    run.redact.add_secret(token)
    return dict(attempt=attempt, token=token, session=answer["session_id"], message=answer["message_id"])


def attach(run, attempt):
    status, body, _ = run.server.rpc("attach", dict(session_id=attempt["session"], attempt_id=attempt["attempt"],
                                                    authority=attempt["token"], funding_pool=run.pool, model=run.route_ref()))
    if status != 200 or (body or {}).get("state") != "attached":
        raise Blocker("routing.attach failed")


def ask_descendants(run, attempt):
    return run.server.rpc("descendants", dict(session_id=attempt["session"], attempt_id=attempt["attempt"],
                                              authority=attempt["token"]))[:2]


def close(run, attempt, usage):
    event = dict(event_id=attempt["attempt"] + "-complete", attempt_id=attempt["attempt"], kind="complete", stage="worker")
    if usage is not None:  # nothing was requested: `usage` is omitted, never null
        event["usage"] = usage
    answer, _ = run.routing("record", event=event)
    detached = run.server.rpc("detach", dict(session_id=attempt["session"], attempt_id=attempt["attempt"],
                                             authority=attempt["token"]))[0] == 200
    return (answer.get("settlement") or {}).get("kind") if answer.get("state") == "recorded" else None, detached


def status_clean(run):
    ledger = (run.routing("status")[0].get("ledger") or {})
    return ledger.get("holds") == {} and ledger.get("open_attempts") == 0 and ledger.get("unstamped_open_holds") == 0 \
        and not (ledger.get("cost_unknown") or {}).get("ids")


def wait_children(run, root, known, began, timeout=120):
    """New descendants of `root` (by the listing) once each is idle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [child for child in run.descendants(root)[0] if child not in known]
        if found:
            for child in found:
                run.wait_idle(child, began)
            return found
        time.sleep(0.5)
    return []


def descendants(run):
    need(run, "descendants")
    s, failures = "descendants", []
    # Positive run: attach first, then create the root and its three kinds of children.
    a = managed(run, s, run.run_id + "-dpos")
    attach(run, a)
    attached_at = now_ms()
    root = a["session"]
    run.create_session(root, title="qual descendants",
                       permissions=DENY_ALL + [{"action": "subagent", "resource": "*", "effect": "allow"}])
    began = now_ms()
    token = run.expect(s, "descendants: tool subagent", 3)
    run.prompt(root, a["message"], TOOL_PROMPT)
    run.wait_idle(root, began)
    tool_children = wait_children(run, root, [], began, timeout=5)
    sent = run.done(token)
    run.server.write_control(hold=[root])  # the parent's turn after the command child's result is held (0 tokens)
    began = now_ms()
    token = run.expect(s, "descendants: command child", 1)
    run.server.request("POST", "/api/session/%s/command" % root, {"name": "qualchild", "text": ""})
    command_children = wait_children(run, root, tool_children, began)
    run.wait_idle(root, began)
    run.server.write_control(hold=[])
    sent += run.done(token)
    _, created, _ = run.create_session(title="qual api child", parent=root)
    api_child = (run.data(created) or {}).get("id")
    began = now_ms()
    token = run.expect(s, "descendants: API child", 1)
    run.prompt(api_child)
    run.wait_idle(api_child, began)
    sent += run.done(token)
    found, lineage = run.descendants(root)
    listed_parents = {entry["session_id"]: entry["parent_session_id"] for entry in lineage}
    status, answer = ask_descendants(run, a)
    ok = status == 200 and (answer or {}).get("state") == "ok"
    shim = {d["session_id"]: d["parent_session_id"] for d in (answer or {}).get("descendants") or []} if ok else None
    matches = ok and shim == {child: listed_parents.get(child) for child in found}
    read = run.account_settled(root, 0, shim_descendants=answer if ok else None, cross_check=True)
    unknown = sorted(read["result"]["unknown"])
    settlement, detached = close(run, a, read["result"]["usage"])
    after_status, _ = ask_descendants(run, a)
    root_events = [e for e in (run.events.events if run.events else []) if isinstance(e, dict)
                   and e.get("type") == "session.created" and (e.get("data") or {}).get("sessionID") == root]
    positive = dict(rpc=dict(http=status, complete=(answer or {}).get("complete"), reason=(answer or {}).get("reason"),
                             count=len(shim or {}), truncated=(answer or {}).get("truncated")),
                    listing_matches=bool(matches), listing_count=len(found),
                    children=dict(tool_subagent="observed" if tool_children else "unobserved",
                                  command_child="observed" if command_children else "unobserved",
                                  api_child="observed" if api_child in found else "unobserved"),
                    accounting_unknown=unknown, settlement=settlement, detached=detached,
                    after_detach_refused=after_status != 200,
                    root_created_after_attach=any((e.get("created") or 0) >= attached_at for e in root_events),
                    root_creation_events=len(root_events), event_stream_error=getattr(run.events, "error", None), calls=sent)
    for condition, reason in ((positive["rpc"]["complete"] is True and positive["rpc"]["reason"] is None, "descendants_incomplete"),
                              (matches, "descendants_differ_from_listing"),
                              (not [u for u in unknown if u.startswith("shim_")], "shim_flags_in_accounting"),
                              (settlement == "window" and detached, "not_settled"),
                              (positive["after_detach_refused"], "descendants_after_detach_not_refused"),
                              (command_children and api_child in found, "children_missing")):
        if not condition:
            failures.append(reason)
    # Negative run (0 calls): the root exists before attach, so its creation was never observed while attached.
    b = managed(run, s, run.run_id + "-dneg")
    run.create_session(b["session"], title="qual descendants negative")
    attach(run, b)
    status, nanswer = ask_descendants(run, b)
    nok = status == 200 and (nanswer or {}).get("state") == "ok"
    nread = run.account(b["session"], shim_descendants=nanswer if nok else None, cross_check=True)
    api = api_offline(read, dict(answer or {}, complete=False, reason="root_creation_unobserved")) if ok else None
    nsettlement, ndetached = close(run, b, nread["result"]["usage"])
    negative = dict(rpc=dict(http=status, complete=(nanswer or {}).get("complete"), reason=(nanswer or {}).get("reason")),
                    accounting_unknown=sorted(nread["result"]["unknown"]), api_offline=api, settlement=nsettlement,
                    detached=ndetached, calls=0)
    if negative["rpc"]["complete"] is not False or negative["rpc"]["reason"] != "root_creation_unobserved":
        failures.append("negative_not_root_creation_unobserved")
    if "shim_lineage_incomplete" not in negative["accounting_unknown"]:
        failures.append("negative_accounting_not_flagged")
    clean = status_clean(run)
    if not clean:
        failures.append("status_not_clean")
    run.note(s, "positive", positive)
    run.note(s, "negative", negative)
    run.note(s, "status_clean", clean)
    run.note(s, "failures", failures)
    run.note(s, "pass", not failures)


def api_offline(read, answer):
    """Offline (no request): the positive run's re-read state accounted as an API route with an incomplete shim answer.
    Every cost must be null; note that OpenCode reports cost 0 for claude-agent, which nulls API costs on its own."""
    from runtime import opencode_accounting as accounting
    state = read["state"]
    result = accounting.usage_from_messages(state["messages"], state["tokens"], session_id=state["session_id"], funding="api",
                                            complete=True, session_cost=state["cost"], children=state["children"],
                                            lineage=state["lineage"], shim_descendants=answer)
    requests = (result["usage"] or {}).get("requests") or []
    return dict(unknown=sorted(result["unknown"]), costs_null=all(r.get("cost_usd_micros") is None for r in requests))


def config_path(resource):
    return bool({segment.lower() for segment in str(resource).replace("\\", "/").split("/")} & CONFIG_SEGMENTS)


def resource_shape(resource, project):
    """An edit resource's shape, never the path itself: relative or absolute, whether an absolute one lies inside the
    probe project, whether it is a config path, and which probe file it names (relative to the project) or `other`."""
    if type(resource) is not str or not resource:
        return dict(form="missing", inside_project=None, config=False, target="other")
    relative, inside = resource, None
    if os.path.isabs(resource):
        base, target = os.path.realpath(project), os.path.realpath(resource)
        inside = target.startswith(base + os.sep)
        relative = os.path.relpath(target, base) if inside else None
    relative = relative and os.path.normpath(relative).replace(os.sep, "/")
    return dict(form="absolute" if os.path.isabs(resource) else "relative", inside_project=inside,
                config=config_path(relative or resource),
                target=relative if relative in CONFIG_FILES + (ORDINARY_FILE,) else "other")


def s33d(run):
    need(run, "s33d")
    s, failures = "s33d", []
    a = managed(run, s, run.run_id + "-s33d")
    attach(run, a)
    root = a["session"]
    # Only `edit` is allowed (shell, MCP, execute, webfetch, websearch and image generation stay denied).
    run.create_session(root, title="qual s33d", permissions=DENY_ALL + [{"action": "edit", "resource": "*", "effect": "allow"}])
    project = Path(run.server.project)
    before = snapshot_tree(project)
    mark, began = len(run.server.hooks()), now_ms()
    token = run.expect(s, "s33d: config and ordinary edits", 3)
    run.prompt(root, a["message"], EDIT_PROMPT)
    run.wait_idle(root, began)
    sent = run.done(token)
    diff = compare_snapshots(before, snapshot_tree(project))
    changes = sorted(diff["added"] + diff["changed"] + diff["removed"])
    edits = [r for r in run.records(mark, session=root, hook="permission.evaluate", phase="post") if r.get("action") == "edit"]
    evaluated = [(r.get("effect"), resource_shape(x, project)) for r in edits for x in (r.get("resources") or [None])]
    paths = {name: dict(attempted=sum(1 for _, shape in evaluated if shape["target"] == name),
                        denied=sum(1 for effect, shape in evaluated if shape["target"] == name and effect == "deny"))
             for name in CONFIG_FILES}
    config_edits = dict(state="observed" if any(p["attempted"] for p in paths.values()) else "unobserved",
                        files_absent=not any((project / name).exists() for name in CONFIG_FILES), paths=paths)
    ordinary = [effect for effect, shape in evaluated if shape["target"] == ORDINARY_FILE]
    ordinary_edit = dict(state="observed" if ordinary else "unobserved", attempted=len(ordinary),
                         allowed=ordinary.count("allow"), written=(project / ORDINARY_FILE).exists())
    # Pass needs at least one attempted config path, each attempted one denied; nothing attempted is not a pass.
    if config_edits["state"] == "unobserved":
        failures.append("config_edit_unobserved")
    if any(effect != "deny" for effect, shape in evaluated if shape["config"]):
        failures.append("config_edit_not_denied")
    if not config_edits["files_absent"]:
        failures.append("config_file_written")
    if ordinary_edit["state"] == "unobserved":
        failures.append("ordinary_edit_unobserved")
    elif not (ordinary_edit["allowed"] and ordinary_edit["written"]):
        failures.append("ordinary_edit_not_written")
    if not set(changes) <= {"agents/", ORDINARY_FILE}:
        failures.append("unexpected_project_change")
    # The `` !`env` `` command under the attached root: only the marker word, never the variable's value.
    run.server.write_control(hold=[root])
    mark, began = len(run.server.hooks()), now_ms()
    token = run.expect(s, "s33d: env command (its prompt is held)", 0, exposure=1)
    run.server.request("POST", "/api/session/%s/command" % root, {"name": "qualenv", "text": ""})
    messages = run.wait_idle(root, began)
    run.server.write_control(hold=[])
    sent += run.done(token)
    recent = json.dumps([m for m in messages if ((m.get("time") or {}).get("created") or 0) >= began])
    marker = "absent" if "QUAL_PROBE_ABSENT" in recent else "present" if "QUAL_PROBE_PRESENT" in recent else "unobserved"
    shells = [dict(phase=r["phase"], probe_present=r.get("probe_present")) for r in run.records(mark, hook="shell.create.before")]
    _, models, _ = run.server.request("GET", "/api/model")
    images = [m for m in (run.data(models) or []) if isinstance(m, dict) and m.get("type") == "image"]
    image = "unobserved: no claude-agent image model in the isolated server" \
        if not [m for m in images if m.get("providerID") == "claude-agent"] else "unobserved: not attempted (needs the coordinator's go)"
    read = run.account_settled(root, 0)
    settlement, detached = close(run, a, read["result"]["usage"])
    clean = status_clean(run)
    if not (settlement == "window" and detached and clean):
        failures.append("not_settled_clean")
    run.note(s, "config_edits", config_edits)
    run.note(s, "ordinary_edit", ordinary_edit)
    run.note(s, "edit_resources", [shape for _, shape in evaluated])  # relative or absolute: is the session dir the base?
    run.note(s, "project_changes", changes)
    run.note(s, "env_command", dict(marker=marker, shell_hooks=shells))
    run.note(s, "image_generation", image)
    run.note(s, "image_models_listed", len(images))
    run.note(s, "calls", sent)
    run.note(s, "failures", failures)
    run.note(s, "pass", not failures)
