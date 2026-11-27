#!/usr/bin/env python3
"""Dev-only eval of the vibe-project-layout skill in small generated projects. Never exported.

Each trial builds a fixture repo with this checkout's package as a project-local plugin, runs one prompt as a private
`opencode run --standalone` with an empty config directory (so no installed plugin loads) on a pinned model, scores
the result from git, and removes the fixture. Results are appended to a JSONL file; `--summary` prints the counts.
"""
import os
import sys

# Run as a program, Python puts scripts/ first on the path, where scripts/select.py shadows the standard
# `select` module that subprocess needs. Drop exactly that entry before importing anything else.
if sys.path and sys.path[0] and os.path.realpath(sys.path[0]) == os.path.dirname(os.path.realpath(__file__)):
    del sys.path[0]

import argparse  # noqa: E402
import concurrent.futures  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live_check", ROOT / "scripts" / "live_check.py")
live_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live_check)

GIT = ["git", "-c", "user.name=layout-eval", "-c", "user.email=layout-eval@example.invalid", "-c", "core.hooksPath=/dev/null"]
SKILL = "vibe-project-layout"
DESCRIPTIONS = {
    "current": None,
    "C": ("Use when adding, moving or renaming a file or folder, before picking its path, when a new file may need "
          "listing in a package manifest, file list or export list, when unsure where code, tests, scripts or docs "
          "go, or when asked to organize or restructure a project."),
}
TASK = ('Add a small helper that parses a duration string such as "1h30m" or "45s" into a number of seconds, and add '
        'a test for it. Create the files and stop; do not run any tests, install anything, or use the network.')
MODULE_JS = "export function {name}(value) {{\n  return value\n}}\n"


def _node_test(module, fn, path="../src"):
    return (f'import assert from "node:assert/strict"\nimport {{ test }} from "node:test"\n'
            f'import {{ {fn} }} from "{path}/{module}.js"\n\ntest("{fn}", () => assert.ok({fn}))\n')


FIXTURES = {
    "node-files": {
        "prompt": TASK,
        "placed": r"^src/[^/]+\.js$",
        "lists": ["package.json"],
        "files": {
            "package.json": json.dumps({"name": "orders", "type": "module", "main": "src/index.js",
                                        "files": ["src/index.js", "src/orders.js", "src/billing.js"]}, indent=2) + "\n",
            "src/index.js": 'export { total } from "./orders.js"\nexport { invoice } from "./billing.js"\n',
            "src/orders.js": MODULE_JS.format(name="total"),
            "src/billing.js": MODULE_JS.format(name="invoice"),
            "test/orders.test.js": _node_test("orders", "total"),
            "test/billing.test.js": _node_test("billing", "invoice"),
            "README.md": "# orders\n\nOrder totals and invoices.\n",
        },
        "layout": ("- Code lives in `src/`, one module per file.\n- Tests live in `test/` as `<module>.test.js`.\n"
                   "- A new module must be added to `package.json` `files`, which lists every shipped file.\n"),
    },
    "python-modules": {
        "prompt": TASK,
        "placed": r"^src/[^/]+\.py$",
        "lists": ["pyproject.toml"],
        "files": {
            "pyproject.toml": ('[project]\nname = "inventory"\nversion = "0.1.0"\n\n[tool.setuptools]\n'
                               'package-dir = {"" = "src"}\npy-modules = ["stock", "pricing"]\n'),
            "src/stock.py": "def on_hand(item):\n    return 0\n",
            "src/pricing.py": "def price(item):\n    return 0\n",
            "tests/test_stock.py": "from stock import on_hand\n\n\ndef test_on_hand():\n    assert on_hand('x') == 0\n",
            "tests/test_pricing.py": "from pricing import price\n\n\ndef test_price():\n    assert price('x') == 0\n",
            "README.md": "# inventory\n\nStock and pricing helpers.\n",
        },
        "layout": ("- Modules live in `src/`, one per file.\n- Tests live in `tests/` as `test_<module>.py`.\n"
                   "- A new module must be added to `py-modules` in `pyproject.toml`.\n"),
    },
    "monorepo": {
        "prompt": TASK.replace("Add a small helper", "In the api package, add a small helper"),
        "placed": r"^packages/api/src/[^/]+\.js$",
        "lists": ["packages/api/package.json"],
        "files": {
            "package.json": json.dumps({"name": "shop", "private": True, "workspaces": ["packages/*"]}, indent=2) + "\n",
            "packages/api/package.json": json.dumps({"name": "@shop/api", "type": "module", "main": "src/server.js",
                                                     "files": ["src/server.js", "src/routes.js"]}, indent=2) + "\n",
            "packages/api/src/server.js": 'export { routes } from "./routes.js"\n',
            "packages/api/src/routes.js": MODULE_JS.format(name="routes"),
            "packages/api/test/routes.test.js": _node_test("routes", "routes"),
            "packages/web/package.json": json.dumps({"name": "@shop/web", "type": "module", "main": "src/app.js",
                                                     "files": ["src/app.js"]}, indent=2) + "\n",
            "packages/web/src/app.js": MODULE_JS.format(name="app"),
            "README.md": "# shop\n\nWorkspaces: `packages/api`, `packages/web`.\n",
        },
        "layout": ("- Each package lives in `packages/<name>/` with its own `src/`, `test/` and `package.json`.\n"
                   "- A new module must be added to its package's `package.json` `files`.\n"),
    },
    "flat-barrel": {
        "prompt": TASK,
        "placed": r"^[^/]+\.js$",
        "lists": ["index.js"],
        "files": {
            "package.json": json.dumps({"name": "tiny-utils", "type": "module", "main": "index.js"}, indent=2) + "\n",
            "index.js": ('export { slugify } from "./slugify.js"\nexport { formatMoney } from "./format-money.js"\n'
                         'export { parseDate } from "./parse-date.js"\n'),
            "slugify.js": MODULE_JS.format(name="slugify"),
            "format-money.js": MODULE_JS.format(name="formatMoney"),
            "parse-date.js": MODULE_JS.format(name="parseDate"),
            "slugify.test.js": _node_test("slugify", "slugify", "."),
            "format-money.test.js": _node_test("format-money", "formatMoney", "."),
            "parse-date.test.js": _node_test("parse-date", "parseDate", "."),
            "README.md": "# tiny-utils\n\nSmall string and number helpers.\n",
        },
        "layout": ("- Each helper is one root `<name>.js` with its test beside it as `<name>.test.js`.\n"
                   "- A new helper must be re-exported from `index.js`, which re-exports every helper.\n"),
    },
}
OFFER = re.compile(r"(record|write|save|add|document)\w*\b[^.?\n]{0,80}\blayout|## Project layout", re.I)


def _git(path, *args, check=False):
    return subprocess.run([*GIT, "-C", str(path), *args], capture_output=True, text=True, check=check).stdout


def build_fixture(project, name, package, description, layout):
    fixture = FIXTURES[name]
    project.mkdir(parents=True)
    for rel, text in fixture["files"].items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text)
    if layout:
        (project / "AGENTS.md").write_text("# Agent instructions\n\n## Project layout\n\n" + fixture["layout"])
    _git(project, "init", "-q", "-b", "main", check=True)
    _git(project, "add", "-A", check=True)
    _git(project, "commit", "-qm", "initial", check=True)
    with open(project / ".git" / "info" / "exclude", "a") as exclude:
        exclude.write("\n.opencode/\n")
    plugin = project / ".opencode" / "plugins" / "live-check"
    shutil.copytree(package, plugin)
    index = plugin / "index.js"
    index.write_text(live_check.rename_plugin_id(index.read_text()))
    if DESCRIPTIONS[description]:
        skill = plugin / "skills" / SKILL / "SKILL.md"
        text, count = re.subn(r"(?m)^description: [^\n]*$", "description: " + DESCRIPTIONS[description], skill.read_text(), count=1)
        assert count == 1, "no description line"
        skill.write_text(text)
    return project


def _worktrees(project):
    return [os.path.realpath(line[9:]) for line in _git(project, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


def score(path, base, fixture):
    new = set(_git(path, "diff", "--name-only", "--diff-filter=A", base).splitlines())
    new |= set(_git(path, "ls-files", "--others", "--exclude-standard").splitlines())
    code = sorted(p for p in new if p.endswith((".js", ".py")) and "test" not in Path(p).name.lower())
    code_file = code[0] if code else None
    registered = False
    if code_file:
        stem = Path(code_file).stem
        added = {name: "\n".join(line for line in _git(path, "diff", "-U0", base, "--", name).splitlines()
                                 if line.startswith("+") and not line.startswith("+++")) for name in fixture["lists"]}
        registered = all(re.search(r"\b" + re.escape(stem) + r"\b", text) for text in added.values())
    return {"code_file": code_file, "code_files": code, "new_files": sorted(new),
            "placed": bool(code_file and re.match(fixture["placed"], code_file)), "registered": registered}


def best_score(project, base, fixture):
    here, scored = os.path.realpath(project), []
    for path in _worktrees(project):
        result = score(path, base, fixture)
        result["where"] = "fixture" if path == here else "linked-worktree"
        scored.append(result)
    hits = [s for s in scored if s["code_file"]]
    return max(hits, key=lambda s: (s["registered"], s["placed"])) if hits else scored[0]


def facts(tools, reply, project):
    loaded, outside = False, []
    for call in tools:
        given = call.get("input") or {}
        if call.get("tool") == "skill" and SKILL in (given.get("name"), given.get("id")):
            loaded = True
        path = given.get("filePath") or given.get("path") or ""
        full = os.path.normpath(os.path.join(str(project), path))
        if f"skills/{SKILL}" in path and not full.startswith(os.path.normpath(str(project)) + os.sep):
            outside.append(path)
        command = given.get("command") or ""
        if f"skills/{SKILL}" in command and str(project) not in command:
            outside.append(command[:200])
    return {"skill_loaded": loaded, "outside_skill_reads": outside, "offered_to_record": bool(OFFER.search(reply or ""))}


def is_valid(row):
    return not row.get("error") and bool(row.get("code_file")) and not row.get("outside_skill_reads")


def summarize(rows):
    cells = {}
    for row in rows:
        key = (row["experiment"], row["model"], row["description"], row["layout"])
        cell = cells.setdefault(key, {"trials": 0, "valid": 0, "registered": 0, "skill_loaded": 0, "offered_to_record": 0})
        cell["trials"] += 1
        if row["valid"]:
            cell["valid"] += 1
            for name in ("registered", "skill_loaded", "offered_to_record"):
                cell[name] += bool(row.get(name))
    return cells


def clean_env(work, project):
    """Private config and home: no installed plugin and no personal skill folder (~/.agents, ~/.claude) is visible.

    The real data and state folders stay, so provider logins keep working and sessions can be deleted.
    """
    config, home = work / "config", work / "home"
    config.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    real = Path.home()
    return {**live_check.isolated_env(config), "PWD": str(project), "HOME": str(home),
            "XDG_DATA_HOME": str(real / ".local" / "share"), "XDG_STATE_HOME": str(real / ".local" / "state"),
            "XDG_CACHE_HOME": str(home / ".cache")}


def trial(package, work_root, spec):
    started, row = time.time(), {**spec, "error": None}
    work = Path(tempfile.mkdtemp(prefix="layout-eval-", dir=work_root))
    try:
        project = build_fixture(work / "project", spec["fixture"], package, spec["description"], spec["layout"])
        base = _git(project, "rev-parse", "HEAD").strip()
        env = clean_env(work, project)
        try:
            answer = live_check.run_model(project, spec["model"], env, prompt=FIXTURES[spec["fixture"]]["prompt"], timeout=1200)
        except (subprocess.SubprocessError, OSError) as error:
            row["error"] = repr(error)[:300]
            answer = {"reply": "", "tools": [], "session": None, "deleted": False}
        row.update(best_score(project, base, FIXTURES[spec["fixture"]]))
        row.update(facts(answer["tools"], answer["reply"], project))
        row["tools_brief"] = [[call.get("tool"), str((call.get("input") or {}).get("name") or (call.get("input") or {}).get("id")
                               or (call.get("input") or {}).get("filePath") or (call.get("input") or {}).get("path") or (call.get("input") or {}).get("command") or "")[:120]]
                              for call in answer["tools"]]
        row.update(tool_calls=len(answer["tools"]), session_left=None if answer.get("deleted") else answer.get("session"),
                   reply_tail=(answer["reply"] or "")[-600:])
    except Exception as error:  # report a broken trial; never hide it
        row["error"] = repr(error)[:300]
    finally:
        for path in _worktrees(work / "project") if (work / "project" / ".git").exists() else []:
            if path != os.path.realpath(work / "project"):
                _git(work / "project", "worktree", "remove", "--force", path)
                shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    row["valid"] = is_valid(row)
    row["seconds"] = round(time.time() - started)
    return row


def plan(experiment, models, descriptions, layout, reps):
    return [{"experiment": experiment, "model": model, "fixture": fixture, "description": description, "layout": layout,
             "rep": rep} for rep in range(1, reps + 1) for model in models for description in descriptions
            for fixture in FIXTURES]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", action="store_true", help="print counts from --out and run nothing")
    parser.add_argument("--experiment", default="E1")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--description", action="append", choices=sorted(DESCRIPTIONS), default=[])
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    if args.summary:
        rows = [json.loads(line) for line in args.out.read_text().splitlines() if line.strip()]
        for key, cell in sorted(summarize(rows).items(), key=str):
            print(json.dumps({"experiment": key[0], "model": key[1], "description": key[2], "layout": key[3], **cell}))
        return 0
    if not args.model or not args.description:
        parser.error("--model and --description are required unless --summary")
    work_root = Path(tempfile.mkdtemp(prefix="layout-eval-run-")).resolve()
    try:
        package = work_root / "package"
        live_check._run([sys.executable, "-B", str(ROOT / "scripts" / "package.py"), "--output", str(package)], cwd=ROOT)
        specs = plan(args.experiment, args.model, args.description, args.layout, args.reps)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for row in pool.map(lambda spec: trial(package, work_root, spec), specs):
                with args.out.open("a") as out:
                    out.write(json.dumps(row) + "\n")
                print(json.dumps({k: row[k] for k in ("experiment", "model", "fixture", "description", "layout", "rep",
                                                       "valid", "registered", "skill_loaded", "error") if k in row}), flush=True)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
