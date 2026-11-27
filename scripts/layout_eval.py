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
import tomllib  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live_check", ROOT / "scripts" / "live_check.py")
live_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live_check)

GIT = ["git", "-c", "user.name=layout-eval", "-c", "user.email=layout-eval@example.invalid", "-c", "core.hooksPath=/dev/null"]
SKILL = "vibe-project-layout"
ALLOWED_MODELS = ("opencode-go/hungerg-free", "openai/gpt-6.1-terra")  # free, and an included subscription
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
        "lists": [{"file": "package.json", "kind": "files", "base": ""}],
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
        "lists": [{"file": "pyproject.toml", "kind": "py-modules", "base": ""}],
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
        "lists": [{"file": "packages/api/package.json", "kind": "files", "base": "packages/api"}],
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
        "lists": [{"file": "index.js", "kind": "reexports", "base": ""}],
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
REEXPORT = re.compile(r"""^\s*export\s+(?:\*|\{[^}]*\})\s+from\s+["']\./([\w.-]+?)(?:\.js)?["']""", re.M)


def _git(path, *args, check=False):
    return subprocess.run([*GIT, "-C", str(path), *args], capture_output=True, text=True, check=check).stdout


def patch_description(text, variant):
    """Replace the description inside the YAML frontmatter only; anything else is an error, never a silent no-op."""
    match = re.match(r"---\n(.*?\n)---\n", text, re.S)
    if not match:
        raise ValueError("no frontmatter")
    head, count = re.subn(r"(?m)^description: [^\n]*$", "description: " + DESCRIPTIONS[variant], match.group(1))
    if count != 1:
        raise ValueError(f"expected one description line in the frontmatter, found {count}")
    return "---\n" + head + "---\n" + text[match.end():]


def build_fixture(project, name, package, description, layout, marker):
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
    skill = plugin / "skills" / SKILL / "SKILL.md"
    text = patch_description(skill.read_text(), description) if DESCRIPTIONS[description] else skill.read_text()
    # The marker proves which copy the skill tool returned; both arms carry it, after the body.
    skill.write_text(text.rstrip("\n") + f"\n\n<!-- eval-marker {marker} -->\n")
    return project


def entries(spec, text):
    if spec["kind"] == "files":
        value = json.loads(text).get("files")
        return set(value) if isinstance(value, list) else set()
    if spec["kind"] == "py-modules":
        value = tomllib.loads(text).get("tool", {}).get("setuptools", {}).get("py-modules")
        return set(value) if isinstance(value, list) else set()
    if spec["kind"] == "reexports":
        return set(REEXPORT.findall(text))
    raise ValueError(spec["kind"])


def expected_entry(spec, code_file):
    relative = code_file[len(spec["base"]) + 1:] if spec["base"] else code_file
    return relative if spec["kind"] == "files" else Path(code_file).stem


def is_test(path):
    parts, name = Path(path).parts, Path(path).name
    return (bool(set(parts[:-1]) & {"test", "tests", "__tests__"}) or name.endswith((".test.js", ".spec.js", "_test.py"))
            or (name.startswith("test_") and name.endswith(".py")))


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def _worktrees(project):
    return [os.path.realpath(line[9:]) for line in _git(project, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


def _location(path, base, fixture):
    new = set(_git(path, "diff", "--name-only", "--diff-filter=AR", base).splitlines())
    new |= set(_git(path, "ls-files", "--others", "--exclude-standard").splitlines())
    code = sorted(p for p in new if p.endswith((".js", ".py")) and not is_test(p) and not p.startswith(".opencode/"))
    lists = {}
    for spec in fixture["lists"]:
        before = _git(path, "show", f"{base}:{spec['file']}")
        after = (Path(path) / spec["file"]).read_text() if (Path(path) / spec["file"]).is_file() else ""
        try:
            lists[spec["file"]] = (entries(spec, before), entries(spec, after), before != after)
        except (ValueError, tomllib.TOMLDecodeError):
            lists[spec["file"]] = (entries(spec, before), set(), True)  # an unparsable list registers nothing
    registered = bool(code) and all(
        expected_entry(spec, f) in lists[spec["file"]][1] and expected_entry(spec, f) not in lists[spec["file"]][0]
        for spec in fixture["lists"] for f in code)
    return {"code_files": code, "new_files": sorted(new), "registered": registered,
            "placed": bool(code) and all(re.match(fixture["placed"], f) for f in code),
            "lists_changed": any(changed for _, _, changed in lists.values())}


def result(project, base, fixture):
    """The delivered result is the checkout holding the new code; list edits made in any other checkout mark it split."""
    here, locations = os.path.realpath(project), []
    for path in _worktrees(project):
        scored = _location(path, base, fixture)
        scored["where"] = "fixture" if path == here else "linked-worktree"
        locations.append(scored)
    with_code = [loc for loc in locations if loc["code_files"]]
    picked = max(with_code, key=lambda loc: len(loc["code_files"])) if with_code else locations[0]
    chosen = {**picked, "code_locations": len(with_code),
              "split": any(loc["lists_changed"] for loc in locations if loc is not picked)}
    return chosen


def facts(tools, reply, project, marker):
    loaded, unverified, outside = False, False, []
    root = os.path.normpath(str(project))
    for call in tools:
        given = call.get("input") or {}
        if call.get("tool") == "skill" and SKILL in (given.get("name"), given.get("id")):
            loaded = True
            unverified = unverified or f"eval-marker {marker}" not in (call.get("output") or "")
        path = given.get("filePath") or given.get("path") or ""
        if f"skills/{SKILL}" in path and not os.path.normpath(os.path.join(root, path)).startswith(root + os.sep):
            outside.append(path)
        command = given.get("command") or ""
        if f"skills/{SKILL}" in command:
            outside.append(command[:200])  # a shell read of the skill cannot be placed safely: fail closed
    return {"skill_loaded": loaded, "unverified_skill_load": unverified, "outside_skill_reads": outside,
            "offered_to_record": bool(OFFER.search(reply or ""))}


def invalid_reason(row):
    if row.get("error"):
        return "error"
    if not row.get("code_files"):
        return "no_code_file"
    if row.get("outside_skill_reads"):
        return "outside_skill_read"
    if row.get("unverified_skill_load"):
        return "unverified_skill_load"
    if row.get("split"):
        return "split_result"
    return None


def is_valid(row):
    return invalid_reason(row) is None


def summarize(rows):
    cells = {}
    for row in rows:
        for fixture in (row["fixture"], "*"):
            key = (row["experiment"], row["model"], row["description"], row["layout"], fixture)
            cell = cells.setdefault(key, {"trials": 0, "valid": 0, "registered": 0, "skill_loaded": 0,
                                          "offered_to_record": 0, "invalid": {}})
            cell["trials"] += 1
            if row["valid"]:
                cell["valid"] += 1
                for name in ("registered", "skill_loaded", "offered_to_record"):
                    cell[name] += bool(row.get(name))
            else:
                reason = row.get("invalid_reason") or "unknown"
                cell["invalid"][reason] = cell["invalid"].get(reason, 0) + 1
    return cells


def sweep_sessions(project, env):
    """Delete every session stored for this fixture directory; report unknown when the list cannot be read."""
    try:
        listed = json.loads(live_check._run(["opencode", "session", "list", "--standalone", "--format", "json"],
                                            cwd=project, timeout=60, data="", env=env) or "[]")
    except (subprocess.SubprocessError, OSError, ValueError):
        return [], True
    listed = listed.get("data", listed) if isinstance(listed, dict) else listed
    deleted, unknown = [], False
    for item in listed if isinstance(listed, list) else []:
        session = item.get("id") if isinstance(item, dict) else None
        if not (isinstance(session, str) and session.startswith("ses")):
            continue
        try:
            live_check._run(["opencode", "session", "delete", session, "--standalone"], cwd=project, timeout=60, data="", env=env)
            deleted.append(session)
        except (subprocess.SubprocessError, OSError):
            unknown = True
    return deleted, unknown


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
    work = Path(tempfile.mkdtemp(prefix="layout-eval-", dir=work_root)).resolve()
    project, env, marker = work / "project", None, uuid.uuid4().hex
    try:
        build_fixture(project, spec["fixture"], package, spec["description"], spec["layout"], marker)
        base = _git(project, "rev-parse", "HEAD").strip()
        env = clean_env(work, project)
        try:
            answer = live_check.run_model(project, spec["model"], env, prompt=FIXTURES[spec["fixture"]]["prompt"], timeout=1200)
        except (subprocess.SubprocessError, OSError) as error:
            row["error"] = repr(error)[:300]
            answer = {"reply": "", "tools": []}
        row.update(result(project, base, FIXTURES[spec["fixture"]]))
        row.update(facts(answer["tools"], answer["reply"], project, marker))
        row["tools_brief"] = [[call.get("tool"), str((call.get("input") or {}).get("name") or (call.get("input") or {}).get("id")
                               or (call.get("input") or {}).get("filePath") or (call.get("input") or {}).get("path")
                               or (call.get("input") or {}).get("command") or "")[:120]] for call in answer["tools"]]
        row.update(tool_calls=len(answer["tools"]), reply_tail=(answer["reply"] or "")[-800:])
    except Exception as error:  # report a broken trial; never hide it
        row["error"] = repr(error)[:300]
    finally:
        if env is not None:
            row["sessions_deleted"], row["sessions_unknown"] = sweep_sessions(project, env)
        row["left_behind"] = []
        if (project / ".git").exists():
            for path in _worktrees(project):
                if path == os.path.realpath(project):
                    continue
                if inside(path, work):
                    _git(project, "worktree", "remove", "--force", path)
                else:  # never delete outside the trial; report it for a person to handle
                    row["left_behind"].append(path)
        shutil.rmtree(work, ignore_errors=True)
    row["invalid_reason"] = invalid_reason(row)
    row["valid"] = row["invalid_reason"] is None
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
            print(json.dumps({"experiment": key[0], "model": key[1], "description": key[2], "layout": key[3], "fixture": key[4], **cell}))
        return 0
    if not args.model or not args.description:
        parser.error("--model and --description are required unless --summary")
    for model in args.model:
        if model not in ALLOWED_MODELS:
            parser.error(f"{model} is not an authorized model; allowed: {', '.join(ALLOWED_MODELS)}")
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
                                                       "valid", "invalid_reason", "registered", "skill_loaded", "left_behind") if k in row}), flush=True)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
