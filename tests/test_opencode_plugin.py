"""Run the OpenCode routing plugin's `node --test` suite and compare its field extraction with the Python adapter."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from runtime import routing
from runtime.adapters import opencode
from runtime.adapters.opencode import AdapterError

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests/opencode_plugin/plugin.test.ts"
PROBES = ROOT / "tests/fixtures/smart-routing/probes"
CONFIG_PATHS = ROOT / "tests/opencode_plugin/config_paths.json"


def _requested(hook, payload):
    try:
        return opencode.normalize_hook(hook, payload)
    except AdapterError:
        return None


def parity_cases():
    """Expected values computed by the Python adapter for the plugin's TS extraction."""
    env_names = ["PATH", "HOME", "GITHUB_TOKEN", "OPENAI_API_KEY", "APIKEY", "SSH_AUTH_SOCK", "DB_PASSWORD",
                 "KEYBOARD", "TOKENIZER_PATH", "MONKEY", "AWS_SECRET_ACCESS_KEY", "GH_AUTH", "COOKIE_JAR",
                 "PRIVATE_KEY_PATH", "SESSION_KEY", "my_passwd", "Credentials", "OPENCODE_TERMINAL", "LANG",
                 "DATABASE_URL", "SENTRY_DSN", "PG_CONNECTION_STRING", "DATABASE_HOST", "AWS_PROFILE", "KUBECONFIG",
                 "PASS", "DB_PASS", "PASSPHRASE", "SECRETS", "JWT", "PGPASSWORD", "MYSQL_PWD", "GITHUB_PAT", "PAT",
                 "REDIS_URL", "MONGODB_URI", "githubToken", "dbPassword", "apiKey", "EDITOR", "TERM", "PWD", "OLDPWD",
                 "npm_config_cache", "GIT_AUTHOR_NAME", "TOKENIZERS_PARALLELISM", "AUTHOR", "PASSENGER", "NODE_PATH",
                 "GITHUB_OAUTH", "AWS_CREDS", "DB_PWD", "AWS_ACCESSKEY", "MY_SECRETKEY", "accessKey",
                 "OAUTHLIB_INSECURE_TRANSPORT", "HTTP_PROXY", "PIP_INDEX_URL"]
    shell = opencode.normalize_hook("shell.create.before", {"env": dict.fromkeys(env_names, "x")})
    env = [{"name": name, "credential": name in shell["credential_env_names"]} for name in env_names]

    excerpt = json.loads((PROBES / "p0-hook-event-excerpt.json").read_text())
    live = [hook["payload"] for hook in json.loads((PROBES / "s3-3-shim-live.json").read_text())["hooks"]]
    models = [hook["model"] for hook in excerpt["hooks"] if hook.get("hook") == "model.request"]
    models += [payload["model"] for payload in live if "model" in payload]
    models += [{"providerID": "openrouter", "id": "glm-5"}, {"providerID": "openrouter", "id": "x/y"},
               {"providerID": "claude-agent", "id": "haiku", "variant": None}, {"providerID": "", "id": "a"},
               {"providerID": "p", "id": "m" * 200}, {"id": "haiku"}]
    model_cases = []
    for model in models:
        result = _requested("model.request", {"sessionID": "ses_1", "kind": "primary", "model": model})
        route = result and result["route_identity"]
        facts = route and {"provider": route["provider_id"], "model": route["model_alias"], "variant": route["variant"],
                           "harness": route["harness"]}
        model_cases.append({"model": model, "facts": facts})

    spawns = [hook["input_model"] for hook in excerpt["hooks"] if hook.get("input_model")]
    spawns += [payload["input"]["model"] for payload in live if payload.get("tool") == "subagent"]
    spawns += ["claude-agent/haiku", "openrouter/z-ai/glm-5", "haiku", "a/b#c#d", "claude-agent/haiku#", "x" * 300 + "/m"]
    spawn_cases = []
    for model in spawns:
        result = _requested("tool.execute.before", {"sessionID": "ses_1", "tool": "subagent",
                                                    "input": {"agent": "general", "model": model}})
        requested = result and result["spawn"]["requested_model"]
        spawn_cases.append({"model": model, "requested": requested and {
            "provider": requested["provider_id"], "model": requested["model_alias"], "variant": requested["variant"]}})
    # The shim probes only ids shaped like `routing.derive_ids` output; OpenCode's own ids must not match.
    derived = [routing.derive_ids(attempt, identity, nonce)["session_id"]
               for attempt, identity, nonce in [("a1", "ledger-1", None), ("a1", "ledger-1", "0" * 32), ("x" * 256, "l", "f" * 32)]]
    sessions = [{"id": session, "derived": True} for session in derived]
    sessions += [{"id": session, "derived": False} for session in [
        "ses_live1", "ses_01JABCDEFGHJKMNPQRSTVWXYZ", "ses_" + "0" * 31, "ses_" + "0" * 33, "ses_" + "A" * 32,
        "msg_" + "0" * 32, derived[0].upper()]]
    table = json.loads(CONFIG_PATHS.read_text(encoding="utf-8"))
    resources = table["deny"] + table["allow"] + table["unparseable"] + [None, 5, "x" * 5000, "K/x"]
    extra_options = [None, [], [".cursor", "Tools\\Agents"], [".."], "a", [""], ["a"] * 65]
    extras = [{"option": option, "paths": opencode.extra_paths(option)} for option in extra_options]
    configured = opencode.extra_paths([".cursor", "Tools\\Agents"])
    paths = [{"resource": resource, "protected": opencode.protected_path(resource),
              "configured": opencode.protected_path(resource, configured)}
             for resource in resources + ["a/.cursor/b", "x/tools/agents/y"]]
    denials = [{"action": action, "resources": value, "deny": opencode.managed_denial(action, value)}
               for action in ("experimental_image_generate", "edit", "write", "patch", "read", "webfetch")
               for value in ([".opencode/command/x.md"], ["src/x.ts"], [], None, ["src/x.ts", 5], "src/x.ts")]
    return {"env": env, "models": model_cases, "spawns": spawn_cases, "sessions": sessions,
            "paths": paths, "extras": extras, "denials": denials}


class OpenCodePluginTests(unittest.TestCase):
    def test_parity_cases_cover_valid_and_invalid_shapes(self):
        cases = parity_cases()
        self.assertTrue(any(item["facts"] for item in cases["models"]))
        self.assertTrue(any(item["facts"] is None for item in cases["models"]))
        self.assertTrue(any(item["requested"] for item in cases["spawns"]))
        self.assertTrue(any(item["requested"] is None for item in cases["spawns"]))
        self.assertTrue(any(item["credential"] for item in cases["env"]))
        self.assertTrue(any(item["derived"] for item in cases["sessions"]))
        self.assertTrue(all(opencode.SESSION.fullmatch(item["id"]) for item in cases["sessions"] if item["derived"]))
        self.assertEqual({item["protected"] for item in cases["paths"]}, {True, False})
        self.assertEqual({item["paths"] is None for item in cases["extras"]}, {True, False})
        self.assertEqual({item["deny"] for item in cases["denials"]}, {"image_generation", "config_edit", None})

    def test_node_suite(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the OpenCode plugin's node --test suite was not run")
        if shutil.which("python3") is None:
            self.skipTest("python3 is not on PATH; the fake routing child cannot start")
        with tempfile.TemporaryDirectory() as directory:
            parity = Path(directory) / "parity.json"
            parity.write_text(json.dumps(parity_cases()))
            result = subprocess.run([node, "--test", "--test-reporter=tap", "--test-timeout=60000", str(SUITE)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=170,
                                    env={**os.environ, "OPENCODE_PLUGIN_PARITY": str(parity)})
        self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-2000:])
        self.assertIn("# fail 0", result.stdout)
        self.assertIn("# skipped 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
