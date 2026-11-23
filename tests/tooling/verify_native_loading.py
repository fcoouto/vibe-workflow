#!/usr/bin/env python3
"""Opt-in credential-free OpenCode loading proof with an observer readiness RPC."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

OBSERVER = """
export default {
  id: "vibe.verification-observer",
  async setup(ctx) {
    await ctx.rpc.register({
      id: "vibe-verification",
      methods: {
        inspect: {
          input: { type: "object", additionalProperties: false },
          output: { type: "object", additionalProperties: true },
        },
      },
      events: {},
    }, {
      inspect: async () => {
        const skills = await ctx.skill.list()
        return {
          tools: (await ctx.tool.list()).map((tool) => tool.id),
          plugins: await ctx.plugin.list(),
          skill: (Array.isArray(skills) ? skills : skills.data).find((entry) => entry.name === "vibe-onboard-project"),
        }
      },
    })
  },
}
"""


def verify(export, folder):
    cli = shutil.which("opencode")
    if cli is None:
        return dict(state="unavailable", reason="opencode_not_found")
    observer = folder / "observer"
    observer.mkdir(mode=0o700)
    (observer / "index.js").write_text(OBSERVER)
    (observer / "package.json").write_text(json.dumps(
        dict(name="vibe-verification-observer", type="module", exports={".": "./index.js"})))
    env = dict(PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               HOME=str(folder), LANG="en_US.UTF-8")
    for key, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                      ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                      ("OPENCODE_CONFIG_DIR", "config/opencode")):
        path = folder / name
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_MODELS_FETCH="1",
               OPENCODE_DISABLE_PROJECT_CONFIG="1", OPENCODE_CONFIG_CONTENT=json.dumps(
                   dict(plugins=[str(export), str(observer)], mcp={}, formatter=False, lsp=False)))
    version = subprocess.run([cli, "--version"], cwd=folder, env=env, capture_output=True,
                             text=True, timeout=10, check=True).stdout.strip()
    request = subprocess.run(
        [cli, "api", "--standalone", "POST", "/api/rpc/vibe-verification/inspect",
         "--data", '{"input":{}}'], cwd=folder, env=env, capture_output=True, text=True, timeout=25)
    (folder / "response.json").write_text(json.dumps(
        dict(code=request.returncode, stdout=request.stdout, stderr=request.stderr), indent=2))
    if request.returncode != 0:
        return dict(state="failed", reason="readiness_rpc_failed", version=version)
    result = inspect_result(json.loads(request.stdout), version, export)
    if result["state"] != "passed":
        return result
    database = folder / "data/opencode/opencode.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        counts = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("session_v2", "session_inbox", "session_message", "session_part")}
    if any(counts.values()):
        return dict(state="failed", reason="unexpected_session_activity", version=version, session_rows=counts)
    return dict(result, session_rows=counts, inference_requests=0)


def inspect_result(response, version, export):
    value = response.get("output", response.get("data", response))
    tools = value.get("tools", [])
    plugins = value.get("plugins", [])
    if isinstance(plugins, dict):
        plugins = plugins.get("data", [])
    target = [plugin for plugin in plugins if plugin.get("id") == "vibe-workflow"]
    if not {"smart_router_select", "vibe_workflow_onboard"} <= set(tools) or len(target) != 1 \
            or target[0].get("state", {}).get("status") != "active":
        return dict(state="failed", reason="tools_not_registered", version=version,
                    tool_ids=tools, plugins=plugins)
    skill = value.get("skill") or {}
    path = export / "skills/vibe-onboard-project/SKILL.md"
    source = target[0].get("source", {})
    if source.get("type") != "local" or source.get("path") != str(export) \
            or skill.get("name") != "vibe-onboard-project" or skill.get("path") != str(path) \
            or "references/procedure.md" not in skill.get("content", ""):
        return dict(state="failed", reason="bundled_skill_not_registered", version=version,
                    skill=skill, plugin=target[0])
    return dict(state="passed", version=version, tools=["smart_router_select", "vibe_workflow_onboard"],
                skill=skill["name"], plugin=target[0], readiness="observer RPC after native plugin setup",
                credentials="disposable empty HOME and all XDG roots")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = verify(args.export.resolve(strict=True), args.evidence.resolve())
    (args.evidence / "loading-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    sys.exit(0 if result["state"] == "passed" else 2)
