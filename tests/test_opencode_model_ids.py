"""OpenRouter `vendor/model` ids through the OpenCode adapter and accounting (lane F4 item 1).

Every OpenRouter model id is `vendor/model`. The adapter accepts exactly one `/` between two parts in its safe name
charset, under one length cap, and refuses every other shape; the TypeScript shim mirrors it (parity cases in
tests/test_opencode_plugin.py, run by its node suite).
"""
import json
import unittest

from runtime import opencode_accounting as accounting, routing
from runtime.adapters import opencode
from runtime.adapters.opencode import AdapterError

ROUTER_IDS = ["anthropic/claude-sonnet-4.5", "z-ai/glm-5", "openai/gpt-5.1:online", "meta-llama/llama-3.3-70b-instruct",
              "x/y", "a" * 64 + "/" + "b" * 63]
HOSTILE_IDS = ["../x", "a/../b", "./x", "a/.", "a/..", "a/b/c", "a//b", "/b", "a/", "/", "", ".", "..",
               "a/b\n", "a\n/b", "a\x00/b", "a/b\x00", "\x00", "a%2Fb", "a/b%2Fc", "..%2Fx",
               "\u0430/b", "a/\u212a", "a/b\u200b", "a\\b", "a /b", "a/b#c", "a/b?c",
               "a" * 64 + "/" + "b" * 64, "a" * 129, "a" * 128 + "/b", "a/" + "b" * 128]


def model_request(model_id, provider="openrouter"):
    return {"sessionID": "ses_1", "kind": "primary", "model": {"providerID": provider, "id": model_id, "variant": "low"}}


def assistant(model_id, *, message="msg_a1", cost=0.0125):
    return {"id": message, "type": "assistant", "finish": "stop", "cost": cost,
            "tokens": {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "model": {"providerID": "openrouter", "id": model_id, "variant": "low"},
            "time": {"created": 1, "completed": 2}}


class ModelIdShape(unittest.TestCase):
    def test_router_ids_are_accepted_as_one_route(self):
        for model_id in ROUTER_IDS:
            with self.subTest(model_id=model_id):
                result = opencode.normalize_hook("model.request", model_request(model_id))
                self.assertEqual(result["route_identity"]["model_alias"], model_id)
                self.assertEqual(result["route_identity"]["provider_id"], "openrouter")
                self.assertEqual(result["route_identity"]["harness"], "opencode provider openrouter")

    def test_plain_names_still_pass(self):
        for model_id in ("haiku", "glm-5", "a" * 128):
            self.assertEqual(opencode.normalize_hook("model.request", model_request(model_id))["requested"]["model_alias"],
                             model_id)

    def test_hostile_ids_are_refused_without_echo(self):
        for model_id in HOSTILE_IDS:
            with self.subTest(model_id=model_id):
                with self.assertRaises(AdapterError) as caught:
                    opencode.normalize_hook("model.request", model_request(model_id))
                self.assertEqual(str(caught.exception), "invalid model")
                with self.assertRaises(AdapterError):
                    opencode.normalize_message(assistant(model_id))

    def test_provider_and_variant_stay_single_names(self):
        for field, value in (("providerID", "open/router"), ("variant", "lo/w")):
            payload = model_request("z-ai/glm-5")
            payload["model"][field] = value
            with self.assertRaises(AdapterError):
                opencode.normalize_hook("model.request", payload)

    def test_spawn_and_attach_model_string_carries_a_router_id(self):
        call = {"sessionID": "ses_1", "tool": "subagent",
                "input": {"agent": "general", "model": "openrouter/anthropic/claude-sonnet-4.5#high"}}
        spawn = opencode.normalize_hook("tool.execute.before", call)["spawn"]["requested_model"]
        self.assertEqual(spawn, {"provider_id": "openrouter", "model_alias": "anthropic/claude-sonnet-4.5",
                                 "variant": "high"})
        for model in ["openrouter/" + item for item in HOSTILE_IDS if "#" not in item and item]:
            with self.subTest(model=model):
                call["input"]["model"] = model
                with self.assertRaises(AdapterError):
                    opencode.normalize_hook("tool.execute.before", call)


class RouterAccounting(unittest.TestCase):
    def test_an_api_funded_router_message_settles_with_a_cost(self):
        """Before F4 the normalizer raised `invalid model` here, so every real worker send ended uncertain."""
        message = opencode.normalize_message(assistant("anthropic/claude-sonnet-4.5"))
        idle = opencode.normalize_message({"id": "msg_a2", "type": "idle", "time": {"created": 3}})
        total = message["tokens"]
        result = accounting.usage_from_messages([message, idle], total, session_id="ses_1", funding="api",
                                                complete=True, session_cost=0.0125, children=[], lineage=[],
                                                shim_descendants={"state": "ok", "root": "ses_1", "descendants": [],
                                                                  "complete": True, "truncated": False})
        requests = result["usage"]["requests"]
        self.assertEqual([(item["model"], item["cost_usd_micros"]) for item in requests],
                         [("anthropic/claude-sonnet-4.5", 12500)])
        self.assertNotIn("anthropic/claude-sonnet-4.5", json.dumps(result["unknown"]))
        self.assertIsNone(routing._usage_error(result["usage"]))  # `record complete` takes it as it is


if __name__ == "__main__":
    unittest.main()
