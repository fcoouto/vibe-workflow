"""OpenCode adapter normalization against the recorded, sanitized OpenCode 2.2.6 probe payloads."""
import ast
import builtins
import copy
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from runtime.adapters import opencode
from runtime.adapters.opencode import AdapterError

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "smart-routing" / "probes"
MODULE = ROOT / "runtime" / "adapters" / "opencode.py"
SECRET = "SECRETVALUE-do-not-leak"
FAMILIES = {"claude-agent/haiku": "claude-haiku"}  # p0r2-results.json resolved_model.catalog
PLACEHOLDER = re.compile(r"<(ses|msg|toolu|evt)([_#])(\d+)>")


def desanitize(value):
    """Map sanitizer placeholders (`<ses_5>`, `<ses#1>`) to valid-looking IDs; real IDs were removed."""
    if isinstance(value, dict):
        return {key: desanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [desanitize(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER.sub(
            lambda m: f"{m[1]}_{'fixture' if m[2] == '_' else 'hook'}{m[3]}", value)
    return value


def load(name):
    return desanitize(json.loads((PROBES / name).read_text(encoding="utf-8")))


EXCERPT = load("p0-hook-event-excerpt.json")
LINEAGE = load("p0-8-lineage-aux.json")
SESSION_A = load("p0-5-model-variant-messages.json")["messages"]
SESSION_F = load("p0r2-mcp-codemode-messages.json")["messages"]
QUAL = load("qual-results.json")  # the 9 November 2026 live re-qualification (docs/smart-routing-qualification.md)


def hook_record(hook, **match):
    return copy.deepcopy(next(h for h in EXCERPT["hooks"]
                              if h["hook"] == hook and all(h.get(k) == v for k, v in match.items())))


def event_record(event_type, **data):
    return copy.deepcopy(next(e for e in EXCERPT["events"]
                              if e["type"] == event_type and all(e["data"].get(k) == v for k, v in data.items())))


def model_request(kind):
    """Rebuild a payload with the recorded key set; baseURL and header values were never recorded."""
    rec = hook_record("model.request", kind=kind)
    payload = {"sessionID": rec["session"], "agent": rec["agent"], "kind": rec["kind"], "model": rec["model"],
               "baseURL": f"https://user:{SECRET}@api.example.test/v1?key={SECRET}",
               "headers": {"Authorization": f"Bearer {SECRET}", "x-api-key": SECRET}}
    assert sorted(payload) == rec["keys"]
    return payload


def tool_call(rec, tool_input, call_id="toolu_synthetic1"):
    payload = {"sessionID": rec["session"], "agent": rec.get("agent", "general"), "tool": rec["tool"],
               "id": call_id, "input": tool_input}
    if "message" in rec:
        payload["messageID"] = rec["message"]
    return payload


def subagent_call():
    rec = hook_record("tool.execute.before", tool="subagent", input_model="claude-agent/haiku#low")
    tool_input = {"agent": rec["input_agent"], "description": "Reply OK", "prompt": f"PROMPT {SECRET}",
                  "model": rec["input_model"]}
    assert sorted(tool_input) == rec["input_keys"]
    payload = tool_call(rec, tool_input)
    assert sorted(payload) == rec["keys"]
    return payload


def shell_create():
    rec = hook_record("shell.create.before")
    env = {name: "1" for name in rec["env_keys_recorded"]}
    env.update(PROBE_SECRET=SECRET, ANTHROPIC_API_KEY=SECRET, PATH="/usr/bin")
    payload = {"command": rec["command"], "cwd": "/tmp/p0hooks/proj", "env": env, "shell": "/bin/zsh",
               "timeout": 120000}
    assert sorted(payload) == rec["keys"]
    return payload


def every_fixture_call():
    calls = [("hook", ("model.request", model_request(kind))) for kind in ("primary", "title", "compaction")]
    calls += [("hook", ("tool.execute.before", subagent_call())), ("hook", ("shell.create.before", shell_create()))]
    calls += [("event", (event,)) for event in EXCERPT["events"]]
    messages = SESSION_A + SESSION_F + LINEAGE["aux_session_C"]["messages"] + LINEAGE["nested_session_D"]["messages"]
    calls += [("message", (message,)) for message in messages]
    return calls


def call(kind, args):
    if kind == "hook":
        return opencode.normalize_hook(*args, families=FAMILIES)
    if kind == "event":
        return opencode.normalize_event(*args, families=FAMILIES)
    return opencode.normalize_message(*args, families=FAMILIES)


def assert_error_hides(test, function, payload, *args):
    marker = "HOSTILE-MARKER"
    with test.assertRaises(AdapterError) as caught:
        function(*args, payload)
    text = str(caught.exception)
    test.assertNotIn(marker, text)
    test.assertLessEqual(len(text), 80)
    test.assertIsNone(caught.exception.__cause__)
    return caught.exception


class ModelRequestHook(unittest.TestCase):
    def test_title_request_is_classified_with_route_identity(self):
        result = opencode.normalize_hook("model.request", model_request("title"), families=FAMILIES)
        self.assertEqual(result["kind"], "hook")
        self.assertEqual(result["hook"], "model.request")
        self.assertEqual(result["session_id"], "ses_hook3")
        self.assertEqual(result["request_kind"], "title")
        self.assertEqual(result["agent"], "title")
        self.assertEqual(result["requested"], {"provider_id": "claude-agent", "model_alias": "haiku", "variant": "low"})
        self.assertEqual(result["route_identity"], {
            "harness": "claude-agent-sdk via opencode", "provider_id": "claude-agent", "model_alias": "haiku",
            "variant": "low", "catalog_family": "claude-haiku", "resolved_model": "unknown"})

    def test_primary_and_compaction_kinds(self):
        for kind in ("primary", "compaction"):
            result = opencode.normalize_hook("model.request", model_request(kind), families=FAMILIES)
            self.assertEqual(result["request_kind"], kind)
            self.assertEqual(result["requested"]["variant"], "default")

    def test_unrecorded_kind_is_unknown_not_guessed(self):
        payload = model_request("primary")
        payload["kind"] = "summarize-later"
        self.assertEqual(opencode.normalize_hook("model.request", payload)["request_kind"], "unknown")
        del payload["kind"]
        self.assertEqual(opencode.normalize_hook("model.request", payload)["request_kind"], "unknown")

    def test_headers_reduced_to_names_and_base_url_to_origin(self):
        result = opencode.normalize_hook("model.request", model_request("primary"))
        self.assertEqual(result["input"]["header_names"], ["authorization", "x-api-key"])
        self.assertEqual(result["input"]["base_url_origin"], "https://api.example.test")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_unknown_family_and_missing_variant_stay_unknown(self):
        payload = model_request("primary")
        del payload["model"]["variant"]
        result = opencode.normalize_hook("model.request", payload)
        self.assertIsNone(result["requested"]["variant"])
        self.assertIsNone(result["route_identity"]["catalog_family"])
        self.assertEqual(result["route_identity"]["resolved_model"], "unknown")

    def test_dispatch_fields_are_not_in_the_payload(self):
        result = opencode.normalize_hook("model.request", model_request("primary"))
        for field in ("task_id", "stage", "attempt_id", "parent_session_id", "message_id"):
            self.assertIsNone(result[field])

    def test_other_providers_get_a_provider_specific_harness(self):
        payload = model_request("primary")
        payload["model"] = {"id": "gpt-x", "providerID": "openai", "variant": "high"}
        self.assertEqual(opencode.normalize_hook("model.request", payload)["route_identity"]["harness"],
                         "opencode provider openai")


class ToolAndPolicyHooks(unittest.TestCase):
    def test_subagent_spawn_requested_model(self):
        result = opencode.normalize_hook("tool.execute.before", subagent_call(), families=FAMILIES)
        self.assertEqual(result["session_id"], "ses_hook1")
        self.assertEqual(result["message_id"], "msg_hook2")
        self.assertEqual(result["tool"], "subagent")
        self.assertEqual(result["tool_class"], "subagent")
        self.assertEqual(result["tool_call_id"], "toolu_synthetic1")
        self.assertEqual(result["spawn"], {
            "agent": "general", "follow_up_session_id": None,
            "requested_model": {"provider_id": "claude-agent", "model_alias": "haiku", "variant": "low"}})
        self.assertEqual(result["input"]["keys"], ["agent", "description", "model", "prompt"])
        self.assertNotIn("PROMPT", json.dumps(result))

    def test_spawn_without_model_keeps_it_unknown(self):
        rec = hook_record("tool.execute.before", tool="subagent", action_taken="throw-nested")
        result = opencode.normalize_hook("tool.execute.before", tool_call(rec, {
            "agent": rec["input_agent"], "description": "d", "prompt": "p"}))
        self.assertEqual(result["session_id"], "ses_hook2")
        self.assertEqual(result["spawn"]["agent"], "general")
        self.assertIsNone(result["spawn"]["requested_model"])
        self.assertIsNone(result["parent_session_id"])

    def test_mcp_codemode_and_shell_tools(self):
        mcp = hook_record("tool.execute.before", tool="mkd_touch_marker")
        result = opencode.normalize_hook("tool.execute.before", tool_call(mcp, {"name": mcp["input_name"]}))
        self.assertEqual((result["tool"], result["tool_class"], result["spawn"]), ("mkd_touch_marker", "other", None))
        code = hook_record("tool.execute.before", tool="execute")
        result = opencode.normalize_hook("tool.execute.before", tool_call(code, {"code": "x" * code["code_len"]}))
        self.assertEqual(result["tool_class"], "codemode")
        self.assertEqual(result["input"], {"keys": ["code"]})
        shell = hook_record("tool.execute.before", tool="shell")
        result = opencode.normalize_hook("tool.execute.before", tool_call(shell, {
            "command": f"curl -H 'x-api-key: {SECRET}' https://api.anthropic.com && opencode run"}))
        self.assertEqual(result["tool_class"], "shell")
        self.assertEqual(result["command_markers"], ["anthropic.com", "curl", "opencode"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_permission_evaluate_webfetch_reduced_to_origin(self):
        rec = hook_record("permission.evaluate", action="webfetch")
        payload = {"sessionID": rec["session"], "agent": rec["agent"], "action": rec["action"],
                   "effect": rec["effect"], "metadata": {"note": SECRET},
                   "resources": [rec["resources"][0] + f"?token={SECRET}"], "source": {"type": "tool"}}
        result = opencode.normalize_hook("permission.evaluate", payload)
        self.assertEqual((result["action"], result["effect"], result["resource_count"]), ("webfetch", "allow", 1))
        self.assertEqual(result["resources"], ["http://127.0.0.1:47392"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_permission_evaluate_subagent_and_shell_resources(self):
        rec = hook_record("permission.evaluate", action="subagent")
        payload = {key: rec[key] for key in ("action", "agent", "effect", "resources")}
        payload.update(sessionID=rec["session"], metadata={}, source={})
        self.assertEqual(sorted(payload), rec["keys"])
        self.assertEqual(opencode.normalize_hook("permission.evaluate", payload)["resources"], ["general"])
        payload.update(action="shell", resources=[f"echo {SECRET}"])
        result = opencode.normalize_hook("permission.evaluate", payload)
        self.assertEqual((result["resources"], result["resource_count"]), ([], 1))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_shell_create_before_has_no_session_and_names_only(self):
        result = opencode.normalize_hook("shell.create.before", shell_create())
        self.assertIsNone(result["session_id"])
        self.assertIn("OPENCODE_TERMINAL", result["env_names"])
        self.assertEqual(result["credential_env_names"], ["ANTHROPIC_API_KEY", "PROBE_SECRET"])
        self.assertEqual(result["input"]["keys"], ["command", "cwd", "env", "shell", "timeout"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("touch", json.dumps(result))

    def test_connection_string_names_are_credentials(self):
        env = dict.fromkeys(["DATABASE_URL", "SENTRY_DSN", "PG_CONNECTION_STRING", "DATABASE_HOST", "AWS_PROFILE"], "x")
        result = opencode.normalize_hook("shell.create.before", {"env": env})
        self.assertEqual(result["credential_env_names"], ["DATABASE_URL", "PG_CONNECTION_STRING", "SENTRY_DSN"])

    def test_password_token_and_url_names_are_credentials_and_common_names_are_not(self):
        credentials = ["PASS", "PASSPHRASE", "SECRETS", "JWT", "PGPASSWORD", "MYSQL_PWD", "GITHUB_PAT", "REDIS_URL",
                       "MONGODB_URI", "githubToken", "dbPassword", "GITHUB_OAUTH", "AWS_CREDS", "DB_PWD",
                       "AWS_ACCESSKEY", "MY_SECRETKEY"]
        ordinary = ["PATH", "HOME", "LANG", "EDITOR", "TERM", "PWD", "npm_config_cache", "GIT_AUTHOR_NAME",
                    "TOKENIZERS_PARALLELISM", "AUTHOR", "OLDPWD", "HTTP_PROXY", "PIP_INDEX_URL"]
        result = opencode.normalize_hook("shell.create.before", {"env": dict.fromkeys(credentials + ordinary, "x")})
        self.assertEqual(result["credential_env_names"], sorted(credentials))

    def test_unknown_hook_is_typed(self):
        result = opencode.normalize_hook("session.prompt", {"sessionID": "ses_hook1", "prompt": SECRET})
        self.assertEqual(result, {"schema": 1, "client": "opencode", "kind": "unknown", "source": "hook",
                                  "name": "session.prompt"})
        bad_name = opencode.normalize_hook("bad name‮", {})
        self.assertEqual((bad_name["kind"], bad_name["name"]), ("unknown", None))


class LiveShimPayloads(unittest.TestCase):
    """Full sanitized hook payloads recorded with the S3.3 shim loaded (s3-3-shim-live.json)."""
    LIVE = load("s3-3-shim-live.json")

    def payload(self, hook, **match):
        record = next(h for h in self.LIVE["hooks"]
                      if h["hook"] == hook and all(h["payload"].get(k) == v for k, v in match.items()))
        payload = copy.deepcopy(record["payload"])
        for field in ("headers", "env"):  # only the name count was recorded
            if field in payload:
                payload[field] = {}
        return payload

    def test_recorded_top_level_keys_match_the_adapter_shapes(self):
        self.assertEqual(sorted(self.payload("model.request", kind="primary")),
                         hook_record("model.request", kind="primary")["keys"])
        self.assertEqual(sorted(self.payload("tool.execute.before", tool="subagent")),
                         ["agent", "id", "input", "messageID", "sessionID", "tool"])
        self.assertEqual(sorted(self.payload("permission.evaluate")),
                         hook_record("permission.evaluate", action="subagent")["keys"])
        self.assertEqual(sorted(self.payload("shell.create.before")), ["command", "cwd", "env", "shell", "timeout"])

    def test_live_payloads_normalize(self):
        for kind in ("title", "primary"):
            result = opencode.normalize_hook("model.request", self.payload("model.request", kind=kind), families=FAMILIES)
            self.assertEqual((result["session_id"], result["request_kind"]), ("ses_hook1", kind))
            self.assertEqual(result["route_identity"]["catalog_family"], "claude-haiku")
        spawn = opencode.normalize_hook("tool.execute.before", self.payload("tool.execute.before", tool="subagent"))
        self.assertEqual(spawn["spawn"]["requested_model"],
                         {"provider_id": "claude-agent", "model_alias": "haiku", "variant": "low"})
        self.assertEqual(spawn["tool_call_id"], "toolu_hook1")
        fetch = opencode.normalize_hook("tool.execute.before", self.payload("tool.execute.before", tool="webfetch"))
        self.assertEqual(fetch["tool_class"], "builtin")
        permission = opencode.normalize_hook("permission.evaluate", self.payload("permission.evaluate"))
        self.assertEqual((permission["action"], permission["effect"], permission["resource_count"]), ("webfetch", "allow", 1))
        shell = opencode.normalize_hook("shell.create.before", self.payload("shell.create.before"))
        self.assertIsNone(shell["session_id"])

    def test_child_session_read_carries_parent_id(self):
        self.assertIn("parentID", self.LIVE["session_get_child_keys"])

    def test_model_call_ledger_is_bounded(self):
        calls = self.LIVE["model_calls"]
        self.assertLessEqual(calls["sent"], 4)
        self.assertEqual(calls["blocked_by_shim_before_sending"], 7)


class Events(unittest.TestCase):
    def test_root_and_child_session_created(self):
        root = opencode.normalize_event(event_record("session.created", sessionID="ses_fixture5"))
        self.assertEqual((root["type"], root["session_id"], root["parent_session_id"]),
                         ("session.created", "ses_fixture5", None))
        self.assertEqual(root["durable_seq"], 0)
        self.assertEqual(root["requested"]["variant"], None)
        child = opencode.normalize_event(event_record("session.created", sessionID="ses_fixture1"), families=FAMILIES)
        self.assertEqual(child["parent_session_id"], "ses_fixture5")
        self.assertEqual(child["agent"], "general")
        self.assertEqual(child["route_identity"]["variant"], "low")
        self.assertEqual(child["durable_aggregate_id"], "ses_fixture1")
        self.assertNotIn("Reply OK", json.dumps(child))  # titles are not carried

    def test_step_started_and_ended(self):
        started = opencode.normalize_event(event_record("session.step.started"))
        self.assertEqual((started["assistant_message_id"], started["agent"], started["durable_seq"]),
                         ("msg_fixture25", "build", 5))
        ended = opencode.normalize_event(event_record("session.step.ended"))
        self.assertEqual(ended["tokens"], {"input": 2, "output": 4, "reasoning": 0, "cache_read": 5539,
                                           "cache_write": 20406})
        self.assertEqual((ended["cost"], ended["finish"], ended["raw_finish"]), (0, "stop", "end_turn"))
        self.assertEqual(ended["rate_limit"]["windows"]["five_hour"],
                         {"utilization_fraction": 0.14, "resets_at_s": 1794178200, "status": "allowed"})
        self.assertEqual(ended["rate_limit"]["resets_at_s"], 1794178200)
        self.assertEqual(ended["created_ms"], 1794169976719)
        self.assertEqual(ended["durable_seq"], 9)

    def test_volatile_usage_has_no_durable_seq(self):
        usage = opencode.normalize_event(event_record("session.usage.updated"))
        self.assertIsNone(usage["durable_seq"])
        self.assertEqual(usage["tokens"]["cache_write"], 20406)

    def test_model_switch_and_failure(self):
        switched = opencode.normalize_event(event_record("session.model.selected"))
        self.assertEqual((switched["requested"]["variant"], switched["previous"]["variant"]), ("low", "default"))
        failed = opencode.normalize_event(event_record("session.execution.failed"))
        self.assertEqual(failed["error_type"], "unknown")
        self.assertNotIn("blocked by probe", json.dumps(failed))

    def test_unknown_event_keeps_durable_seq_but_no_body(self):
        result = opencode.normalize_event(event_record("session.inbox.enqueued"))
        self.assertEqual((result["kind"], result["type"], result["durable_seq"]),
                         ("unknown", "session.inbox.enqueued", 1))
        self.assertNotIn("Reply with exactly", json.dumps(result))
        tool = opencode.normalize_event(event_record("session.tool.called"))
        self.assertEqual(tool["kind"], "unknown")
        self.assertNotIn("probe_marker", json.dumps(tool))

    def test_durable_sequence_supports_gap_detection(self):
        seqs = [opencode.normalize_event(e)["durable_seq"] for e in EXCERPT["events"]
                if e["data"].get("sessionID") == "ses_fixture5" and "durable" in e]
        self.assertEqual(seqs, sorted(seqs))


class Messages(unittest.TestCase):
    def test_assistant_message_with_rate_limit(self):
        msg = next(m for m in LINEAGE["aux_session_C"]["messages"] if m["type"] == "assistant")
        result = opencode.normalize_message(msg, families=FAMILIES)
        self.assertEqual((result["kind"], result["message_id"], result["agent"]), ("message", "msg_fixture30", "build"))
        self.assertEqual(result["route_identity"]["catalog_family"], "claude-haiku")
        self.assertEqual(result["tokens"]["cache_read"], 25945)
        windows = result["rate_limit"]["windows"]
        self.assertEqual((windows["five_hour"]["utilization_fraction"], windows["seven_day"]["utilization_fraction"]),
                         (0.17, 0.04))
        self.assertEqual((result["created_ms"], result["completed_ms"]), (1794170364822, 1794170366331))
        self.assertNotIn("created", result)
        self.assertEqual(result["rate_limit"]["overage_status"], "rejected")
        self.assertNotIn('"OK"', json.dumps(result))

    def test_percent_looking_utilization_is_unknown_not_a_fraction(self):
        msg = copy.deepcopy(next(m for m in LINEAGE["aux_session_C"]["messages"] if m["type"] == "assistant"))
        windows = msg["native"]["rateLimit"]["unifiedWindows"]
        windows["five_hour"]["utilization"] = 45
        windows["seven_day"]["utilization"] = 1.0001
        result = opencode.normalize_message(msg)["rate_limit"]["windows"]
        self.assertEqual((result["five_hour"]["utilization_fraction"], result["seven_day"]["utilization_fraction"]),
                         (None, None))
        self.assertEqual(result["five_hour"]["resets_at_s"], 1794178200)
        windows["five_hour"]["utilization"] = 1
        self.assertEqual(opencode.normalize_message(msg)["rate_limit"]["windows"]["five_hour"]["utilization_fraction"], 1)
        windows["five_hour"]["utilization"] = True
        assert_error_hides(self, opencode.normalize_message, msg)

    def test_rate_limit_absent_stays_unknown(self):
        msg = next(m for m in SESSION_A if m["id"] == "msg_fixture2")
        self.assertIsNone(opencode.normalize_message(msg)["rate_limit"])
        self.assertEqual(opencode.normalize_message(msg)["requested"]["variant"], "low")

    def test_subagent_tool_part_names_child_session(self):
        msg = next(m for m in SESSION_A if m["id"] == "msg_fixture3")
        call_part = opencode.normalize_message(msg)["tool_calls"][0]
        self.assertEqual(call_part, {
            "tool": "subagent", "tool_class": "subagent", "tool_call_id": "toolu_fixture1", "executed": False,
            "status": "completed", "child_session_id": "ses_fixture1",
            "spawn": {"agent": "general", "follow_up_session_id": None,
                      "requested_model": {"provider_id": "claude-agent", "model_alias": "haiku", "variant": "low"}}})
        self.assertNotIn("Reply with exactly", json.dumps(opencode.normalize_message(msg)))

    def test_user_idle_compaction_and_switch_messages(self):
        user = opencode.normalize_message(next(m for m in SESSION_A if m["type"] == "user"))
        self.assertEqual(user["type"], "user")
        self.assertNotIn("subagent tool", json.dumps(user))
        failed = opencode.normalize_message(next(m for m in SESSION_A if m.get("outcome") == "failed"))
        self.assertEqual((failed["outcome"], failed["error_type"]), ("failed", "unknown"))
        compaction = opencode.normalize_message(
            next(m for m in LINEAGE["aux_session_C"]["messages"] if m["type"] == "compaction"))
        self.assertEqual(compaction["tokens"]["output"], 227)
        self.assertNotIn("Objective", json.dumps(compaction))
        switched = opencode.normalize_message(next(m for m in SESSION_A if m["type"] == "model-switched"))
        self.assertEqual(switched["previous"]["variant"], "default")

    def test_title_tokens_are_session_total_minus_messages(self):
        aux = LINEAGE["aux_session_C"]
        totals = [opencode.normalize_message(m)["tokens"] for m in aux["messages"]]
        output = sum(t["output"] for t in totals if t)
        self.assertEqual(output, aux["sum_of_message_tokens"]["output"])
        self.assertGreater(aux["session_tokens"]["output"], output)

    def test_unknown_message_type(self):
        result = opencode.normalize_message({"id": "msg_x1", "type": "hologram", "text": SECRET})
        self.assertEqual((result["kind"], result["source"], result["type"]), ("unknown", "message", "hologram"))
        self.assertNotIn(SECRET, json.dumps(result))
        for near in ("Synthetic", "synthetic ", "synthetic-result"):
            self.assertEqual(opencode.normalize_message({"id": "msg_x1", "type": near})["kind"], "unknown")

    def test_synthetic_message_is_a_non_model_record(self):
        # qual-results.json: a `subagent: true` command child's result reached the parent as a message whose
        # `type` is `synthetic` (only the type was recorded); it carries no model request.
        self.assertIn("synthetic", QUAL["inspection"]["managed_root_message_types"])
        raw = {"id": "msg_x2", "type": "synthetic", "time": {"created": 1794170447818},
               "text": SECRET, "content": [{"type": "text", "text": SECRET}]}
        result = opencode.normalize_message(raw)
        self.assertEqual(result, {"schema": 1, "client": "opencode", "kind": "synthetic", "type": "synthetic",
                                  "message_id": "msg_x2", "created_ms": 1794170447818, "completed_ms": None,
                                  "tokens": None})
        self.assertNotIn(SECRET, json.dumps(result))
        # Tokens are kept, never dropped, so accounting can flag a synthetic record that carries some.
        counted = opencode.normalize_message(dict(raw, tokens={"input": 1, "output": 2}))
        self.assertEqual(counted["tokens"], {"input": 1, "output": 2, "reasoning": None, "cache_read": None,
                                             "cache_write": None})
        for bad in ({"id": SECRET}, {"time": SECRET}, {"tokens": {"input": True}}):
            assert_error_hides(self, opencode.normalize_message, dict(raw, **bad))

    def test_synthetic_with_a_tool_part_model_or_cost_stays_unknown(self):
        # Its tool parts (a child link) and model are not read, so such a record is not classified as synthetic.
        raw = {"id": "msg_x3", "type": "synthetic", "time": {"created": 1794170447818}}
        spawn = {"type": "tool", "id": "toolu_1", "name": "subagent", "state": {"status": "completed",
                                                                               "metadata": {"sessionID": "ses_x9"}}}
        for extra in ({"content": [spawn]}, {"content": [{"type": "text", "text": SECRET}, spawn]},
                      {"model": {"id": "haiku", "providerID": "claude-agent"}}, {"cost": 0.01},
                      {"content": SECRET}, {"content": [SECRET]}):
            result = opencode.normalize_message(dict(raw, **extra))
            self.assertEqual((result["kind"], result["type"], result["message_id"]), ("unknown", "synthetic", "msg_x3"))
            self.assertNotIn(SECRET, json.dumps(result))
        for plain in ({}, {"content": []}, {"content": [{"type": "text", "text": SECRET}]}, {"cost": 0},
                      {"model": None}):
            self.assertEqual(opencode.normalize_message(dict(raw, **plain))["kind"], "synthetic")


class HostileInput(unittest.TestCase):
    def test_wrong_top_level_types(self):
        for payload in (None, [], "x", 1, True):
            assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
            assert_error_hides(self, opencode.normalize_event, payload)
            assert_error_hides(self, opencode.normalize_message, payload)

    def test_ids_validated_without_echo(self):
        for bad in ("ses HOSTILE-MARKER", "msg_HOSTILE-MARKER", "", "ses_" + "a" * 200, "ses_éHOSTILE-MARKER", 5):
            payload = model_request("primary")
            payload["sessionID"] = bad
            assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        payload = subagent_call()
        payload["messageID"] = "ses_HOSTILE-MARKER"
        assert_error_hides(self, opencode.normalize_hook, payload, "tool.execute.before")

    def test_bool_as_int_and_negative_tokens(self):
        for tokens in ({"input": True}, {"input": -1}, {"input": 1.5}, {"cache": {"read": "HOSTILE-MARKER"}}, []):
            msg = copy.deepcopy(next(m for m in SESSION_A if m["type"] == "assistant"))
            msg["tokens"] = tokens
            assert_error_hides(self, opencode.normalize_message, msg)
        event = copy.deepcopy(event_record("session.step.started"))
        event["durable"]["seq"] = True
        assert_error_hides(self, opencode.normalize_event, event)
        event["durable"]["seq"] = float("nan")
        assert_error_hides(self, opencode.normalize_event, event)

    def test_oversized_and_deeply_nested(self):
        payload = model_request("primary")
        payload["headers"] = {"x-big": "HOSTILE-MARKER" * 400_000}
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        deep = {}
        node = deep
        for _ in range(5000):
            node["a"] = {}
            node = node["a"]
        payload = model_request("primary")
        payload["headers"] = deep
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        wide = model_request("primary")
        wide["headers"] = {f"h{i}": "v" for i in range(100_000)}
        assert_error_hides(self, opencode.normalize_hook, wide, "model.request")

    def test_long_and_unicode_strings_are_rejected_or_bounded(self):
        payload = model_request("primary")
        payload["agent"] = "HOSTILE-MARKER" * 100
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        payload = model_request("primary")
        payload["model"]["id"] = "ha\u0000iku"
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        payload = model_request("primary")
        payload["headers"] = {"‮HOSTILE-MARKER evil": "x", "ok-name": "y"}
        result = opencode.normalize_hook("model.request", payload)
        self.assertEqual((result["input"]["header_names"], result["input"]["header_names_dropped"]), (["ok-name"], 1))

    def test_dropped_counts_invalid_names_not_case_collisions(self):
        payload = model_request("primary")
        payload["headers"] = {"Authorization": "x", "authorization": "y", "bad name": "z"}
        result = opencode.normalize_hook("model.request", payload)["input"]
        self.assertEqual((result["header_names"], result["header_names_dropped"]), (["authorization"], 1))

    def test_non_json_values_and_keys(self):
        payload = model_request("primary")
        payload["headers"] = {1: "HOSTILE-MARKER"}
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")
        payload = model_request("primary")
        payload["extra"] = object()
        assert_error_hides(self, opencode.normalize_hook, payload, "model.request")

    def test_malformed_subagent_model(self):
        payload = subagent_call()
        payload["input"]["model"] = "no-slash HOSTILE-MARKER"
        assert_error_hides(self, opencode.normalize_hook, payload, "tool.execute.before")

    def test_inputs_are_never_mutated(self):
        for kind, args in every_fixture_call():
            before = copy.deepcopy(args)
            call(kind, args)
            self.assertEqual(args, before)

    def test_outputs_are_bounded_and_resolved_model_unknown(self):
        for kind, args in every_fixture_call():
            result = call(kind, args)
            text = json.dumps(result)
            self.assertLess(len(text), 8192)
            self.assertNotIn(SECRET, text)
            for key, value in result.items():
                if key in ("route_identity", "previous") and value and "resolved_model" in value:
                    self.assertEqual(value["resolved_model"], "unknown")
            if result.get("route_identity"):
                self.assertEqual(result["route_identity"]["resolved_model"], "unknown")


CONFIG_PATHS = json.loads((ROOT / "tests" / "opencode_plugin" / "config_paths.json").read_text(encoding="utf-8"))


def permission(action, resources, effect="allow"):
    """The live-recorded `permission.evaluate` key set (s3-3-shim-live.json) with another action and resources."""
    return {"action": action, "agent": "build", "effect": effect, "metadata": {}, "resources": resources,
            "sessionID": "ses_hook1", "source": {"id": "toolu_hook1", "messageID": "msg_hook1", "type": "tool"}}


class ManagedDenies(unittest.TestCase):
    """S3.3d: image generation and model edits of OpenCode configuration (clients/opencode.md)."""

    def test_config_path_table(self):
        for resource in CONFIG_PATHS["deny"]:
            self.assertTrue(opencode.protected_path(resource), resource)
        for resource in CONFIG_PATHS["allow"]:
            self.assertFalse(opencode.protected_path(resource), resource)
        for resource in CONFIG_PATHS["unparseable"] + [None, 5, ["a"], {"path": "a"}, "x" * 5000]:
            self.assertTrue(opencode.protected_path(resource), repr(resource)[:40])

    def test_extra_paths_add_and_never_loosen(self):
        extra = opencode.extra_paths([".cursor", "Tools\\Agents", "/rules/"])
        self.assertEqual(extra, [(".cursor",), ("tools", "agents"), ("rules",)])
        for resource in ("a/.cursor/x", ".CURSOR", "x/tools/agents/y.md", "rules/z", "/abs/rules/z"):
            self.assertTrue(opencode.protected_path(resource, extra), resource)
        for resource in ("tools/x.md", "agents.md", "x/tools/y/agents/z"):
            self.assertFalse(opencode.protected_path(resource, extra), resource)
        for resource in CONFIG_PATHS["deny"]:
            self.assertTrue(opencode.protected_path(resource, extra), resource)
        self.assertEqual(opencode.extra_paths(None), [])
        self.assertEqual(opencode.extra_paths([]), [])
        for bad in ("a", [""], [".."], ["a/../b"], [5], ["x" * 300], ["a"] * 65, ["a\u0000"], {"a": 1}, ["."]):
            self.assertIsNone(opencode.extra_paths(bad), repr(bad)[:40])

    def test_managed_denial_by_action(self):
        self.assertEqual(opencode.managed_denial("experimental_image_generate", ["openai/gpt-image-1"]), "image_generation")
        self.assertEqual(opencode.managed_denial("experimental_image_generate", []), "image_generation")
        for action in ("edit", "write", "patch"):
            self.assertEqual(opencode.managed_denial(action, [".opencode/command/x.md"]), "config_edit")
            self.assertEqual(opencode.managed_denial(action, ["src/x.ts", "opencode.json"]), "config_edit")
            self.assertIsNone(opencode.managed_denial(action, ["src/x.ts", "docs/a.md"]))
            for odd in (None, [], "src/x.ts", [None], ["src/x.ts", 5], ["a"] * 257):
                self.assertEqual(opencode.managed_denial(action, odd), "config_edit", repr(odd)[:40])
        self.assertEqual(opencode.managed_denial("edit", ["tools/agents/a.md"], [("tools", "agents")]), "config_edit")
        self.assertEqual(opencode.managed_denial("edit", ["src/x.ts"], None), "config_edit")  # invalid extra option
        for action in ("read", "webfetch", "shell", "subagent", "external_directory"):
            self.assertIsNone(opencode.managed_denial(action, [".opencode/command/x.md"]))

    def test_permission_evaluate_reports_the_managed_denial_without_paths(self):
        image = opencode.normalize_hook("permission.evaluate", permission("experimental_image_generate", ["openai/gpt-image-1"]))
        self.assertEqual((image["action"], image["managed_deny"], image["resources"], image["resource_count"]),
                         ("experimental_image_generate", "image_generation", [], 1))
        edit = opencode.normalize_hook("permission.evaluate", permission("edit", [f".opencode/command/{SECRET}.md"]))
        self.assertEqual((edit["managed_deny"], edit["resources"], edit["resource_count"]), ("config_edit", [], 1))
        self.assertNotIn(SECRET, json.dumps(edit))
        plain = opencode.normalize_hook("permission.evaluate", permission("edit", ["src/x.ts"]))
        self.assertIsNone(plain["managed_deny"])
        fetch = opencode.normalize_hook("permission.evaluate", permission("webfetch", ["https://example.test/"]))
        self.assertIsNone(fetch["managed_deny"])


class Probe(unittest.TestCase):
    HOOKS = ["model.request", "tool.execute.before", "permission.evaluate", "shell.create.before"]
    ENFORCED_TOOLS = {"shell": "deny", "mcp": "deny", "execute": "deny", "webfetch": "deny",
                      "image_generate": "deny", "config_edit": "deny"}

    def test_enforced_profile_on_the_qualified_version(self):
        result = opencode.probe({"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": "ok",
                                 "worker_tools": self.ENFORCED_TOOLS})
        self.assertEqual(result["supported_hooks"], sorted(self.HOOKS))
        self.assertEqual((result["managed_launch"], result["enforcement_profile"]), ("allow", "enforced"))
        self.assertEqual(result["interactive_sessions"], "unguarded")
        for reason in ("plugin_load_fail_open", "shell_command_contents", "resolved_model", "provider_retries"):
            self.assertIn(reason, result["unsupported"])

    def test_plugin_missing_refuses_managed_launches(self):
        for health in ("unavailable", None):
            result = opencode.probe({"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": health,
                                     "worker_tools": self.ENFORCED_TOOLS})
            self.assertEqual(result["managed_launch"], "refuse")
            self.assertEqual(result["enforcement_profile"], "advisory")
            self.assertIn("plugin_health_failed", result["unsupported"])

    def test_shell_keeping_worker_is_advisory_only(self):
        for tools in ({**self.ENFORCED_TOOLS, "shell": "allow"}, {}):
            result = opencode.probe({"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": "ok",
                                     "worker_tools": tools})
            self.assertEqual((result["managed_launch"], result["enforcement_profile"]), ("allow", "advisory"))
            self.assertIn("worker_unmediated_tools", result["unsupported"])

    def test_narrow_is_enforceable_except_for_shell(self):
        narrow = {"shell": "deny", "mcp": "narrow", "execute": "narrow", "webfetch": "narrow",
                  "image_generate": "deny", "config_edit": "narrow"}
        result = opencode.probe({"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": "ok",
                                 "worker_tools": narrow})
        self.assertEqual(result["enforcement_profile"], "enforced")
        result = opencode.probe({"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": "ok",
                                 "worker_tools": {**narrow, "shell": "narrow"}})
        self.assertEqual(result["enforcement_profile"], "advisory")
        self.assertIn("worker_unmediated_tools", result["unsupported"])

    def test_enforced_requires_image_generation_denied_and_config_edits_mediated(self):
        base = {"version": "2.2.6", "hooks": self.HOOKS, "plugin_health": "ok"}
        cases = [({"image_generate": "deny", "config_edit": "deny"}, []),
                 ({"image_generate": "deny", "config_edit": "narrow"}, []),
                 ({"image_generate": "narrow", "config_edit": "deny"}, ["image_generation_unmediated"]),
                 ({"image_generate": "allow", "config_edit": "deny"}, ["image_generation_unmediated"]),
                 ({"config_edit": "deny"}, ["image_generation_unmediated"]),
                 ({"image_generate": "deny", "config_edit": "allow"}, ["config_edit_unmediated"]),
                 ({"image_generate": "deny"}, ["config_edit_unmediated"]),
                 ({}, ["image_generation_unmediated", "config_edit_unmediated"])]
        four = {tool: self.ENFORCED_TOOLS[tool] for tool in ("shell", "mcp", "execute", "webfetch")}
        for denies, reasons in cases:
            result = opencode.probe({**base, "worker_tools": {**four, **denies}})
            extra = [reason for reason in result["unsupported"] if reason not in opencode.ALWAYS_UNSUPPORTED]
            self.assertEqual(extra, reasons, denies)
            self.assertEqual(result["managed_launch"], "allow")
            self.assertEqual(result["enforcement_profile"], "advisory" if reasons else "enforced", denies)
        for bad in ({"image_generate": "maybe"}, {"config_edit": None}, {"image": "deny"}):
            assert_error_hides(self, opencode.probe, {**base, "worker_tools": {**four, **bad}})

    def test_missing_hook_and_unqualified_version(self):
        result = opencode.probe({"version": "2.3.0", "hooks": self.HOOKS[:2], "plugin_health": "ok",
                                 "worker_tools": self.ENFORCED_TOOLS})
        self.assertEqual(result["missing_hooks"], ["permission.evaluate", "shell.create.before"])
        self.assertEqual((result["managed_launch"], result["enforcement_profile"]), ("refuse", "advisory"))
        self.assertIn("version_unqualified", result["unsupported"])
        self.assertEqual(result["version"], "2.3.0")

    def test_probe_rejects_malformed_capabilities(self):
        for caps in (None, {"version": 5}, {"version": "2.2.6", "hooks": "model.request"},
                     {"version": "2.2.6", "hooks": [], "worker_tools": {"shell": "HOSTILE-MARKER"}}):
            assert_error_hides(self, opencode.probe, caps)


class Purity(unittest.TestCase):
    def test_imports_nothing_network_process_or_filesystem_related(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "print", "exec", "eval", "__import__", "input", "compile"})
        self.assertLessEqual(names, {"math", "re", "unicodedata"})

    def test_normalization_performs_no_io(self):
        def refuse(*args, **kwargs):
            raise AssertionError("I/O attempted")
        with patch.object(builtins, "open", refuse), patch("socket.socket", refuse), \
                patch("subprocess.Popen", refuse), patch("os.system", refuse):
            for kind, args in every_fixture_call():
                call(kind, args)
            opencode.probe({"version": "2.2.6", "hooks": [], "plugin_health": None, "worker_tools": {}})


if __name__ == "__main__":
    unittest.main()
