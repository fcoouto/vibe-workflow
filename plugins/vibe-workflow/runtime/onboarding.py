"""Bounded, private D3 review plans. No enrollment, retrieval, or project execution."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import uuid

from runtime.startup import PROJECT_FILE, StartupError, checkout, _checkout_identity, decode_project_record, json_object, policy_digest, runtime_settings, _check_observation_deadline
from runtime.state import runtime_fingerprint


PACKAGE = Path(__file__).resolve().parents[1]
MAX_REQUEST = 2 * 1024 * 1024
MAX_PLAN = 4 * 1024 * 1024
MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_PATHS = 4096
HEX = re.compile(r"[0-9a-f]{64}\Z")
CHECKOUT_INPUTS = ("repository_root", "git_common_dir", "head", "index_digest", "dirty_inventory", "paths")
LIMITS = ["Helpers do not authenticate source ownership, trust, catalog semantics, or agent evidence.",
          "Structural checks do not qualify client execution, enrollment, approval, or merge readiness.",
          "No project commands run; missing baseline and client observations remain agent-recorded gaps.",
          "Metadata-only inventory records do not verify file contents; dependent evidence and edits still require bounded content."]

def _require(condition, message):
    if not condition:
        raise StartupError(message)

def _object(value, required, optional=()):
    _require(isinstance(value, dict) and set(required) <= value.keys()
             and value.keys() <= set(required) | set(optional), "Invalid onboarding object fields")

def _text(value, empty=False):
    _require(isinstance(value, str) and (empty or bool(value.strip()))
             and len(value) <= 65536 and "\x00" not in value, "Invalid onboarding text")
    return value

def _strings(value, empty=True):
    _require(isinstance(value, list) and len(value) <= MAX_PATHS
             and (empty or bool(value)), "Invalid onboarding list")
    for item in value:
        _text(item)
    _require(len(value) == len(set(value)), "Duplicate onboarding reference")
    return value

def _relative(value):
    _text(value)
    path = PurePosixPath(value)
    _require(not path.is_absolute() and str(path) == value and ".." not in path.parts
             and value != "." and not any(ord(c) < 32 for c in value), "Invalid relative target/source path")
    return value

def _hash(value):
    _require(isinstance(value, str) and HEX.fullmatch(value), "Invalid content hash")
    return value

def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()

def _now():
    return datetime.now(timezone.utc).isoformat()

def read_request(raw, limit=MAX_REQUEST):
    """Use startup's strict parser for small inputs; retain its rules for larger artifacts."""
    _require(isinstance(raw, bytes) and len(raw) <= limit, "Onboarding JSON size limit exceeded")
    if len(raw) <= 128 * 1024:
        return json_object(raw)

    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, "Duplicate JSON field")
            value[key] = item
        return value

    def invalid_number(_):
        raise StartupError("Non-JSON number")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    _require(isinstance(result, dict), "Expected a JSON object")
    return result

def _bounded(value):
    """Bound direct Python callers as well as CLI input; reject private raw record categories."""
    raw = json.dumps(value, allow_nan=False).encode()
    _require(len(raw) <= MAX_REQUEST, "Onboarding request size limit exceeded")
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        _require(depth <= 30, "Onboarding nesting limit exceeded")
        if isinstance(item, dict):
            _require(all(isinstance(key, str) for key in item), "Expected string object keys")
            _require(not {key.lower() for key in item} & {
                "credentials", "password", "access_token", "api_key", "environment", "transcript",
                "raw_environment", "raw_output", "secret"}, "Use sanitized summaries, never private raw data")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            _require(len(item) <= MAX_PATHS, "Onboarding list limit exceeded")
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            _text(item, empty=True)
        else:
            _require(item is None or type(item) in (bool, int, float), "Invalid onboarding value")

def _no_links(path):
    for item in (path, *path.parents):
        if os.path.lexists(item):
            _require(not item.is_symlink(), "Onboarding paths must not contain links")

def _private(st, directory=False):
    _require((stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode))
             and st.st_uid == os.getuid() and st.st_mode & 0o077 == 0
             and (directory or st.st_nlink == 1), "Onboarding files must be private, owned, regular and unlinked")

def _git(root, *args, limit=None, deadline=None):
    from runtime.startup import read_git
    return read_git(root, *args, limit=MAX_FILE if limit is None else min(MAX_FILE, limit), deadline=deadline)


def _plan_path(value, *, create=True):
    _require(os.name == "posix", "Onboarding requires a qualified POSIX filesystem")
    path = Path(value)
    _require(path.is_absolute() and str(path.resolve()) == str(path),
             "Plan directory must be absolute and canonical")
    _no_links(path)
    _require(not path.is_relative_to(PACKAGE), "Plan directory must be outside the installed package")
    for ancestor in (path, *path.parents):
        _require(not os.path.lexists(ancestor / ".git") and ancestor.name not in {
            ".codex", ".claude", ".agents", ".cursor", "skills", "instructions", "plugins"},
            "Plan directory must be outside repositories, plugin caches and instruction discovery paths")
    existing = next((p for p in (path, *path.parents) if p.exists()), None)
    _require(existing is not None and existing.is_dir(), "Plan directory parent must exist")
    code, _ = _git(existing, "-c", "safe.bareRepository=all", "rev-parse", "--absolute-git-dir")
    _require(code != 0, "Plan directory must be outside repositories")
    if not path.exists():
        _require(create, "An existing private reviewed plan is required")
        _require(path.parent.is_dir(), "Plan directory parent must exist")
        path.mkdir(mode=0o700)
    _private(path.stat(), directory=True)
    return path


@contextmanager
def _locked(value):
    import fcntl
    path = _plan_path(value)
    directory = _open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    lock = None
    try:
        _private(os.fstat(directory), directory=True)
        lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=directory)
        _private(os.fstat(lock))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError("Onboarding plan busy; retry the same expected revision") from None
        yield path, directory
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)

def _open_path(path, flags):
    """Resolve every component with no-follow descriptors, closing symlink-swap races."""
    path = Path(path)
    _require(path.is_absolute(), "Expected an absolute onboarding path")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)

def _read_file(path, limit, private=False, *, expected_binding=None, resources=None, held=None):
    descriptor = _open_path(path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if private:
            _private(metadata)
        if expected_binding is not None:
            _require({"device": metadata.st_dev, "inode": metadata.st_ino} == expected_binding
                     and metadata.st_nlink == 1, "Observed input physical identity changed")
        _require(stat.S_ISREG(metadata.st_mode), "Expected a regular onboarding input file")
        _require(metadata.st_size <= limit, "Onboarding file size limit exceeded")
        if resources is not None:
            admission = resources.reserve()
            try: owner = resources.current_acquisition()
            finally: admission.discard()
            owner.reserve(requested=limit + 1, live=limit + 1).__enter__()
        raw = None
        try:
            raw = stream.read(limit + 1)
            _require(len(raw) <= limit, "Onboarding file size limit exceeded")
            after = os.fstat(stream.fileno())
            _require(all(getattr(metadata, field) == getattr(after, field) for field in
                         ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink", "st_mtime_ns", "st_ctime_ns")),
                     "Onboarding input changed while reading")
            if resources is not None: resources.check()
            return raw
        finally: raw = None

def _load(path):
    if not os.path.lexists(path / "plan.json"):
        return None
    try:
        plan = read_request(_read_file(path / "plan.json", MAX_PLAN, private=True), MAX_PLAN)
    except OSError:
        raise StartupError("Plan record must be a private regular file without links") from None
    if plan.get("schema_version") == 2:
        _project_plan_shape(plan)
        for item in plan["sources"]["declared"]["toolkit"]["files"]:
            _source_location(item["local_path"], path)
        return plan
    _object(plan, ("schema_version", "plan_id", "revision", "created_at", "identity", "package_pins",
        "sources", "requirements", "inventory", "decisions", "coverage", "actions", "evidence",
        "semantic_assessment", "invalidations", "decision_history", "recovery_requirement", "status",
        "blockers", "verified_at", "review_digest", "current_input_digest", "evidence_limits"), ("screening",))
    _require(plan.get("schema_version") == 1 and type(plan.get("revision")) is int
             and plan["revision"] >= 1, "Unsupported onboarding plan")
    if "screening" in plan:
        _screening_projection(plan["screening"])
    _inventory_enabled(plan["identity"])
    return plan

def _save(plan, path, directory, *, budget=None):
    _check_observation_deadline(budget or {})
    raw = json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=True).encode() + b"\n"
    _check_observation_deadline(budget or {})
    _require(len(raw) <= MAX_PLAN, "Onboarding artifact size limit exceeded; plan remains unchanged")
    if os.path.lexists(path / "plan.json"):
        _private((path / "plan.json").lstat())
    name = ".plan-" + uuid.uuid4().hex
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _check_observation_deadline(budget or {})
        os.replace(name, "plan.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        _check_observation_deadline(budget or {})
    finally:
        if os.path.lexists(path / name):
            os.unlink(name, dir_fd=directory)


def _source_location(value, directory):
    local = Path(_text(value))
    _require(local.is_absolute() and local.is_relative_to(directory) and local != directory
             and ".." not in local.parts and local.name not in {"plan.json", ".lock"}
             and not local.name.startswith(".plan-"), "Retain source material inside the private plan directory")
    return local


def _source_record(record, path):
    _bounded(record)
    _object(record, ("schema_version", "clients", "toolkit", "required_sources"))
    _require(type(record["schema_version"]) is int and record["schema_version"] == 1,
             "Unsupported onboarding source schema")
    _strings(record["clients"], empty=False)
    for client in record["clients"]:
        _require(client in ("codex", "claude-code")
                 and (PACKAGE / "clients" / (client + ".md")).is_file(), "Unknown selected native client")
    toolkit = record["toolkit"]
    _object(toolkit, ("commit", "origin", "acquisition", "provenance", "trust", "files", "catalog"))
    _require(isinstance(toolkit["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", toolkit["commit"]),
             "Expected a pinned Toolkit commit")
    for field in ("origin", "provenance"):
        _text(toolkit[field])
    _require(toolkit["acquisition"] in ("online", "offline") and toolkit["trust"] in ("recorded", "pending"),
             "Invalid source acquisition/provenance status")
    _require(isinstance(toolkit["files"], list) and isinstance(toolkit["catalog"], list)
             and len(toolkit["files"]) <= MAX_PATHS and len(toolkit["catalog"]) <= MAX_PATHS,
             "Invalid source manifest/catalog")
    paths = set()
    for item in toolkit["files"]:
        _object(item, ("path", "local_path", "sha256", "required"))
        _relative(item["path"])
        _require(item["path"] not in paths, "Duplicate source path")
        paths.add(item["path"])
        _hash(item["sha256"])
        _require(type(item["required"]) is bool, "Expected source requirement boolean")
        local = _source_location(item["local_path"], path)
        _require(str(local.resolve()) == str(local), "Retain source material inside the private plan directory")
    for name in _strings(record["required_sources"], empty=False):
        _relative(name)
    ids = set()
    for row in toolkit["catalog"]:
        _object(row, ("id", "source", "section", "stage", "mandatory", "dependencies"))
        for field in ("id", "source", "section", "stage"):
            _text(row[field])
        _require(not row["id"].startswith("policy:") and row["id"] not in ids,
                 "Duplicate or reserved catalog ID")
        ids.add(row["id"])
        _require(row["stage"] in ("installation", "project_onboarding", "runtime"),
                 "Invalid catalog lifecycle stage")
        _require(row["source"] in paths or row["source"] in record["required_sources"],
                 "Catalog row must reference a declared source")
        _require(type(row["mandatory"]) is bool, "Expected catalog mandatory boolean")
        _strings(row["dependencies"])
    _dependencies(toolkit["catalog"], ids)

def _dependencies(rows, ids):
    for row in rows:
        _strings(row["dependencies"])
    remaining = {r["id"]: set(r["dependencies"]) for r in rows}
    _require(all(deps <= ids and key not in deps for key, deps in remaining.items()),
             "Unknown or self-referencing dependency")
    while remaining:
        ready = {key for key, deps in remaining.items() if not deps}
        _require(bool(ready), "Cyclic onboarding dependencies")
        remaining = {key: deps - ready for key, deps in remaining.items() if key not in ready}

def _sources(record):
    observed = {}
    total = 0
    for item in record["toolkit"]["files"]:
        if total >= MAX_TOTAL:
            observed[item["path"]] = {"status": "source_total_limit"}
            continue
        try:
            raw = _read_file(Path(item["local_path"]), min(MAX_FILE, MAX_TOTAL - total), private=True)
            total += len(raw)
            _require(total <= MAX_TOTAL, "Retained source total size limit exceeded")
            observed[item["path"]] = {"sha256": hashlib.sha256(raw).hexdigest(), "status": "read",
                                      "bytes": len(raw)}
        except (OSError, StartupError):
            observed[item["path"]] = {"status": "unavailable"}
    return observed

def _secret_file(path):
    return path.name == ".env" or path.name.startswith(".env.") or path.suffix in {".pem", ".key", ".p12", ".pfx"}

def _fingerprint(root, relative, limit=MAX_FILE, *, resources=None):
    path = root / relative
    held = ExitStack() if resources is not None else None; raw = None
    try:
        _no_links(path)
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            return {"status": "special"}
        if _secret_file(path):
            return {"status": "excluded", "reason": "potential secret file"}
        if st.st_size > limit:
            return {"status": "oversized"}
        raw = _read_file(path, limit, resources=resources, held=held)
        if resources is not None:
            resources.current_acquisition().reserve(requested=len(raw) + 64, live=1024)
        return {"status": "read", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    except FileNotFoundError:
        return {"status": "absent"}
    except (OSError, StartupError) as error:
        if resources is not None and getattr(error, "_vcw_resource_origin", None) is resources: raise
        return {"status": "unreadable_or_link"}
    finally:
        raw = None
        if held is not None: held.close()

def _file_targets(actions):
    _require(isinstance(actions, list) and len(actions) <= MAX_PATHS, "Invalid proposed actions")
    targets = []
    for action in actions:
        _require(isinstance(action, dict), "Invalid proposed action")
        if action.get("kind") in ("write", "delete"):
            targets.append(_relative(action.get("target")))
    return targets

def _inventory_enabled(snapshot):
    version = snapshot.get("inventory_version")
    _require("inventory_version" not in snapshot or type(version) is int and version == 1, "Unsupported file inventory version")
    for name, value in snapshot["fingerprints"].items():
        if value["status"] != "metadata":
            continue
        _require(version == 1 and not name.startswith("instruction:"), "Metadata cannot replace instruction/content evidence")
        _object(value, ("status", "reason", "metadata"))
        _require(value["reason"] in ("oversized", "excluded", "special", "unreadable_or_link"),
                 "Invalid metadata-only inventory reason")
        metadata = value["metadata"]
        _object(metadata, ("device", "inode", "mode", "links", "size", "mtime_ns", "ctime_ns"))
        _require(all(type(v) is int for v in metadata.values())
                 and all(metadata[k] >= 0 for k in ("device", "inode", "mode", "links", "size")),
                 "Invalid file inventory metadata")
    return version == 1

def _inventory_fingerprint(root, relative, limit):
    # Observe the leaf without opening it or following links, including secret/special files.
    path = root / relative
    try:
        descriptor = _open_path(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        finally:
            os.close(descriptor)
        if _secret_file(path):
            reason = "excluded"
        elif not stat.S_ISREG(info.st_mode):
            reason = "unreadable_or_link" if stat.S_ISLNK(info.st_mode) else "special"
        elif info.st_size > MAX_FILE:
            reason = "oversized"
        else:
            # Shared-budget exhaustion must not change evidence strength between inspect and apply.
            return _fingerprint(root, relative, limit)
        return {"status": "metadata", "reason": reason, "metadata": {
            "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode, "links": info.st_nlink,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}}
    except FileNotFoundError:
        return {"status": "absent"}
    except (OSError, StartupError):
        return {"status": "unreadable_or_link"}

def _snapshot_targets(plan):
    names = {name for name in plan["identity"]["fingerprints"] if not name.startswith("instruction:")}
    return sorted(names | set(_file_targets(plan["actions"])))

def _snapshot(root, targets=(), *, budget=None, inventory=False, relevant=False):
    def check(*, reading=False):
        if budget is not None:
            _require(type(budget["paths"]) is int and 0 <= budget["paths"] <= MAX_PATHS,
                     "Project aggregate path limit exceeded; inventory remains incomplete")
            _require(type(budget["bytes"]) is int and 0 <= budget["bytes"] <= MAX_TOTAL,
                     "Project aggregate byte limit exceeded; inventory remains incomplete")
            if reading:
                _require(budget["bytes"] < MAX_TOTAL,
                         "Project aggregate byte limit exceeded; inventory remains incomplete")
            _check_observation_deadline(budget)

    def git(*args):
        if budget is None:
            return _git(root, *args)
        check(reading=True)
        try:
            code, raw = _git(root, *args, limit=MAX_TOTAL - budget["bytes"], deadline=budget.get("deadline"))
        except (StartupError, OSError, subprocess.SubprocessError):
            # Unknown partial output must not leave a reusable allowance.
            budget["bytes"] = max(budget["bytes"], MAX_TOTAL + 1)
            raise
        budget["bytes"] += len(raw)
        check()
        return code, raw

    if budget is None:
        identity = checkout(root)
    else:
        check(reading=True)
        _require(budget["paths"] < MAX_PATHS, "Project aggregate path limit exceeded; inventory remains incomplete")
        try:
            identity = checkout(root, limit=min(MAX_FILE, MAX_TOTAL - budget["bytes"]),
                                deadline=budget.get("deadline"), budget=budget)
        except (StartupError, OSError, subprocess.SubprocessError):
            budget["bytes"] = max(budget["bytes"], MAX_TOTAL + 1)
            raise
        check()
    _require(identity is not None and identity[0] == root, "Selected checkout identity changed")
    if relevant:
        names = sorted({_relative(name) for name in targets})
    else:
        code, raw = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        _require(code == 0, "Cannot enumerate checkout files")
        names = sorted(set(os.fsdecode(name) for name in raw.split(b"\x00") if name))
    observed_names = sorted(set(names) | {_relative(name) for name in targets})
    _require(len(observed_names) <= MAX_PATHS, "Checkout path limit exceeded; inventory remains incomplete")
    fingerprints = {}
    total = 0
    observer = _inventory_fingerprint if inventory else _fingerprint
    for name in observed_names:
        _relative(name)
        fingerprints[name] = (_inspection_fingerprint(root, name, budget, inventory=inventory)
                              if budget is not None else observer(root, name, MAX_FILE))
        total += fingerprints[name].get("bytes", 0)
        _require(total <= MAX_TOTAL, "Checkout byte limit exceeded; inventory remains incomplete")
    instructions = []
    directories = {root, *root.parents}
    for name in observed_names:
        directory = (root / name).parent
        while directory.is_relative_to(root):
            directories.add(directory)
            if directory == root:
                break
            directory = directory.parent
    _require(len(directories) <= MAX_PATHS, "Instruction scope limit exceeded")
    for directory in sorted(directories):
        for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", ".github/copilot-instructions.md"):
            location = directory / name
            key = "instruction:" + str(location)
            fingerprints[key] = (_inspection_fingerprint(directory, name, budget)
                                 if budget is not None else _fingerprint(directory, name))
            if fingerprints[key]["status"] != "absent":
                instructions.append({"path": str(location), "scope": str(directory),
                                     "editable": location.is_relative_to(root)})
    if relevant:
        check()
        return {"repository_root": str(root), "git_common_dir": str(identity[1]),
                "paths": names, "fingerprints": fingerprints, "instructions": instructions}
    code, index = git("ls-files", "--stage", "-z")
    _require(code == 0, "Cannot read staged checkout identity")
    code, status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    _require(code == 0, "Cannot read checkout changes")
    code, head = git("rev-parse", "--verify", "HEAD")
    result = {"repository_root": str(root), "git_common_dir": str(identity[1]),
            "head": head.decode("ascii").strip() if code == 0 else None,
            "index_digest": hashlib.sha256(index).hexdigest(),
            "dirty_inventory": [os.fsdecode(v) for v in status.split(b"\x00") if v],
            "paths": names, "fingerprints": fingerprints, "instructions": instructions}
    check()
    if inventory:
        result["inventory_version"] = 1
    return result

def _package_pins():
    return {"policy_digest": policy_digest(PACKAGE), "runtime_digest": runtime_fingerprint()}

def _requirements(record):
    rows = [dict(row) for row in record["toolkit"]["catalog"]]
    for path in sorted((PACKAGE / "policies").glob("*.md")):
        if path.name == "00-index.md":
            continue
        rows.append({"id": "policy:" + path.stem, "source": "policies/" + path.name,
                     "section": "complete contract", "stage": "project_onboarding", "mandatory": True,
                     "dependencies": []})
    return rows

def _block(scope, reason, resumption):
    return {"scope": scope, "reason": reason, "resumption": resumption}

def _source_blockers(plan, observed):
    record = plan["sources"]["declared"]
    blockers = []
    if record["toolkit"]["trust"] != "recorded":
        blockers.append(_block("sources", "Source trust/provenance remains pending",
                               "Record explicitly trusted pinned retrieval/copy evidence"))
    required = set(record["required_sources"]) | {
        f["path"] for f in record["toolkit"]["files"] if f["required"]}
    declared = {f["path"]: f for f in record["toolkit"]["files"]}
    for name in sorted(required | {r["source"] for r in record["toolkit"]["catalog"]}):
        if name not in declared or observed.get(name, {}).get("sha256") != declared[name]["sha256"]:
            blockers.append(_block("source:" + name, "Pinned source missing, changed, unsafe or over limit",
                                   "Retain the same pinned source with matching declared hash"))
    if not record["toolkit"]["catalog"]:
        blockers.append(_block("catalog", "Complete upstream catalog is missing", "Discover complete pinned catalog"))
    return blockers

def _current(plan):
    root = Path(plan["identity"]["repository_root"])
    snapshot = _snapshot(root, _snapshot_targets(plan), inventory=_inventory_enabled(plan["identity"]))
    sources = _sources(plan["sources"]["declared"])
    package = _package_pins()
    changes = []
    previous = plan["identity"]
    for key in CHECKOUT_INPUTS:
        if snapshot[key] != previous[key]:
            changes.append("checkout:" + key)
    old, new = previous["fingerprints"], snapshot["fingerprints"]
    changes.extend(key for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key))
    for name in sorted(plan["sources"]["observed"].keys() | sources.keys()):
        if plan["sources"]["observed"].get(name) != sources.get(name):
            changes.append("source:" + name)
    if package != plan["package_pins"]:
        changes.append("package")
    for action in plan["actions"]:
        if action["kind"] in ("write", "delete"):
            actual = _fingerprint(root, action["target"])
            expected = "absent" if actual["status"] == "absent" else actual.get("sha256")
            if expected != action["before"]:
                changes.append(action["target"])
    current = {"identity": snapshot | {"clients": previous["clients"]},
               "sources": sources, "package_pins": package}
    return sorted(set(changes)), sources, current

def _remember_decision(plan, decision):
    entry = {"revision": plan["revision"], "decision": deepcopy(decision)}
    if entry not in plan["decision_history"]:
        plan["decision_history"].append(entry)

def _invalidate(plan, changes):
    if not changes:
        return
    affected = []
    for decision in plan["decisions"]:
        if set(decision["invalidate_on"]) & set(changes) or "package" in changes:
            if decision["disposition"] != "pending":
                _remember_decision(plan, decision)
                decision["disposition"] = "pending"
            affected.append(decision["id"])
    entry = {"at": _now(), "revision": plan["revision"], "changed_inputs": changes,
             "decisions": affected, "actions": [a["id"] for a in plan["actions"]
                 if a["target"] in changes or set(a["decision_ids"]) & set(affected) or "package" in changes]}
    if not plan["invalidations"] or plan["invalidations"][-1].get("changed_inputs") != changes:
        plan["invalidations"].append(entry)

def _review_digest(plan):
    if "screening" in plan:
        ordinary = {key: value for key, value in plan.items() if key != "screening"}
        return _digest(dict(plan=_review_digest(ordinary), screening=plan["screening"]))
    if plan["schema_version"] == 2:
        return _digest({key: plan[key] for key in ("identity", "project_scope", "assessments",
            "package_pins", "sources", "requirements", "current_observations")})
    return _digest({key: plan[key] for key in (
        "identity", "package_pins", "sources", "inventory", "decisions", "coverage", "actions", "evidence",
        "semantic_assessment")})

def _finish(plan, sources, changes, blockers, draft=False, current=None, screening_authentication=None):
    blockers = _source_blockers(plan, sources) + blockers
    decisions = ([d | {"id": key} for key, d in _project_decisions(plan).items()]
                 if plan["schema_version"] == 2 else plan["decisions"])
    pending = [d for d in decisions if d["disposition"] in ("pending", "proposed")]
    if changes:
        blockers = [_block(name, "Pinned input changed; dependent assessment is stale",
                           "Reinspect affected inputs and reconcile this plan with current_input_digest; restore changed sources to their pinned hashes")
                    for name in changes] + blockers
        status = "stale"
    elif pending:
        status = "awaiting_decision"
    elif blockers:
        status = "blocked"
    elif draft:
        status = "draft"
    else:
        status = "ready_for_review"
    if plan["schema_version"] == 2 and len(blockers) > MAX_PATHS:
        blockers = blockers[:MAX_PATHS - 1] + [_block("limits", "Aggregate blocker limit reached; assessment remains incomplete",
            "Resolve listed gaps, then reinspect for remaining bounded observations")]
    current = current or {"identity": plan["identity"], "sources": sources, "package_pins": plan["package_pins"]}
    if "screening" in plan and plan["screening"]["freshness"]["status"] != "unavailable":
        freshness = _screening_freshness(plan["screening"]["payload"], _digest(current))
        if freshness["status"] == "stale" or plan["screening"]["freshness"]["status"] != "stale":
            plan["screening"]["freshness"] = freshness
    plan.update(status=status, blockers=blockers, verified_at=_now(), review_digest=_review_digest(plan),
                current_input_digest=_digest(current))
    limits = list(LIMITS)
    if plan["sources"]["declared"]["toolkit"]["acquisition"] == "offline":
        limits.append("Offline source freshness is unverified; declared commit/hash is not current upstream proof.")
    plan["evidence_limits"] = limits
    report = {"plan_id": plan["plan_id"], "revision": plan["revision"], "status": status,
            "blockers": blockers, "next_decision": pending[0] if pending else None,
            "evidence_limits": limits, "review_digest": plan["review_digest"], "current_input_digest": plan["current_input_digest"],
            "exit_code": 0 if status == "ready_for_review" else 2}
    if "screening" in plan:
        report["screening"] = _screening_report(plan, screening_authentication)["screening"]
    return report

def inspect_project(cwd, plan_dir, source_record):
    """Pin one explicitly selected checkout and declared retained source bundle."""
    if isinstance(source_record, dict) and "project_scope" in source_record:
        return _inspect_project_scope(cwd, plan_dir, source_record)
    _require(Path(cwd).is_absolute(), "Select an absolute target checkout")
    identity = checkout(Path(cwd))
    _require(identity is not None, "Onboarding prerequisite: select an existing Git checkout; do not initialize one")
    root, _ = identity
    with _locked(plan_dir) as (path, directory):
        _source_record(source_record, path)
        plan = _load(path)
        if plan is None:
            snapshot = _snapshot(root, inventory=True)
            sources = _sources(source_record)
            plan = {"schema_version": 1, "plan_id": uuid.uuid4().hex, "revision": 1,
                    "created_at": _now(), "identity": snapshot | {"clients": source_record["clients"]},
                    "package_pins": _package_pins(),
                    "sources": {"declared": source_record, "observed": sources},
                    "requirements": _requirements(source_record), "inventory": {}, "decisions": [],
                    "coverage": [], "actions": [], "evidence": [], "semantic_assessment": {},
                    "invalidations": [], "decision_history": [], "recovery_requirement": "D4 must back up privately and journal before apply"}
            report = _finish(plan, sources, [], [], draft=True)
        else:
            _require(plan["schema_version"] == 1, "Project scope is frozen; resume with the same explicit scope")
            _require(plan["identity"]["repository_root"] == str(root), "Plan belongs to another selected checkout")
            _require(plan["sources"]["declared"] == source_record,
                     "Pinned sources/clients cannot change on resume; start a new plan directory")
            changes, sources, current = _current(plan)
            _invalidate(plan, changes)
            plan["revision"] += 1
            blockers = _validate_proposal(plan) if plan["semantic_assessment"] else []
            report = _finish(plan, sources, changes, blockers, draft=not bool(plan["semantic_assessment"]), current=current)
        _save(plan, path, directory)
    return _screen_onboarding_plan(plan_dir, plan["revision"], inspection=plan)

def _inspection_fingerprint(root, relative, budget, *, resources=None, inventory=False):
    _require(not inventory or resources is None, "Capture resource owners require strict content observations")
    _check_observation_deadline(budget)
    _require(budget["paths"] < MAX_PATHS, "Project aggregate path limit exceeded; inventory remains incomplete")
    budget["paths"] += 1
    remaining = MAX_TOTAL - budget["bytes"]
    _require(remaining >= 0, "Project aggregate byte limit exceeded; inventory remains incomplete")
    result = (_inventory_fingerprint(root, relative, min(MAX_FILE, remaining)) if inventory else
              _fingerprint(root, relative, min(MAX_FILE, remaining), resources=resources))
    _require(not (result["status"] == "oversized" and remaining < MAX_FILE),
             "Project aggregate byte limit exceeded; inventory remains incomplete")
    budget["bytes"] += result.get("bytes", 0)
    _check_observation_deadline(budget)
    return result

def _filesystem_binding(path):
    _no_links(path)
    info = path.stat()
    _require(stat.S_ISDIR(info.st_mode), "Project identity needs an existing directory")
    return {"device": info.st_dev, "inode": info.st_ino}

def _relevant_inspection(scope):
    _require("inspection_scope" not in scope or scope["inspection_scope"] == "relevant"
             and scope["kind"] == "standalone", "Relevant inspection requires a standalone Project")
    return "inspection_scope" in scope


def _project_scope(value):
    _object(value, ("kind", "project_root", "member_checkouts", "membership", "authority", "project_files"), ("inspection_scope",))
    _require(value["kind"] in ("standalone", "umbrella"), "Invalid Project kind")
    root = Path(_text(value["project_root"]))
    _require(root.is_absolute(), "Select an absolute Project root")
    _require(str(root.resolve(strict=True)) == str(root), "Project root must be canonical")
    _filesystem_binding(root)
    members = _strings(value["member_checkouts"], empty=False)
    _require(all(Path(member).is_absolute() for member in members), "Select absolute member checkout roots")
    _require(value["kind"] != "standalone" or len(members) == 1,
             "Standalone inspection selects exactly one checkout")
    membership = value["membership"]
    _object(membership, ("include", "exclude", "future_join", "authorization"))
    for name in ("include", "exclude"):
        for boundary in _strings(membership[name], empty=name == "exclude"):
            if boundary != ".":
                _relative(boundary)
    _require(type(membership["future_join"]) is bool, "Expected future-join boolean")
    _text(membership["authorization"])
    authority = value["authority"]
    _object(authority, ("path", "sha256", "reference"))
    _require(authority["path"] == str(root / PROJECT_FILE), "Project authority must use its canonical enrollment path")
    if authority["sha256"] != "absent":
        _hash(authority["sha256"])
    _text(authority["reference"])
    for name in _strings(value["project_files"]):
        _relative(name)
    _require(value["kind"] != "standalone" or _relevant_inspection(value) or not value["project_files"],
             "Standalone named files belong to its selected Repository assessment")
    _relevant_inspection(value)
    _require(len(members) + len(value["project_files"]) + len(membership["include"])
             + len(membership["exclude"]) <= MAX_PATHS, "Project scope aggregate path limit exceeded")
    return deepcopy(value)

def _checkout_observation(value, budget):
    budget.setdefault("bytes", 0)
    def git(root, *args):
        code, raw = _git(root, *args, limit=max(0, MAX_TOTAL - budget["bytes"]), deadline=budget.get("deadline"))
        budget["bytes"] += len(raw)
        _require(budget["bytes"] <= MAX_TOTAL, "Project aggregate byte limit exceeded")
        return code, raw
    root = Path(value)
    observed = {"root": str(root), "primary_root": None, "git_dir": None, "git_common_dir": None,
                "binding": {}, "membership": "unknown", "snapshot": None}
    try:
        _check_observation_deadline(budget)
        _require(str(root.resolve(strict=True)) == str(root), "Member boundary contains a link or noncanonical root")
        observed["binding"]["root"] = _filesystem_binding(root)
        identity = _checkout_identity(root, include_git_dir=True, limit=min(MAX_FILE, max(0, MAX_TOTAL - budget["bytes"])), total_limit=max(0, MAX_TOTAL - budget["bytes"]), deadline=budget.get("deadline"), budget=budget)
        _require(identity is not None and identity[0] == root, "Member must select an existing checkout root")
        common = identity[1]
        observed["git_common_dir"] = str(common)
        observed["binding"]["git_common_dir"] = _filesystem_binding(common)
        raw = identity[2]
        _require(len(raw.splitlines()) == 1, "Cannot resolve selected worktree Git directory")
        git_dir = Path(os.fsdecode(raw.rstrip(b"\n")))
        _require(git_dir.is_absolute() and git_dir.resolve(strict=True) == git_dir,
                 "Noncanonical worktree Git directory")
        observed["git_dir"] = str(git_dir)
        observed["binding"]["git_dir"] = _filesystem_binding(git_dir)
        code, raw = git(root, "worktree", "list", "--porcelain", "-z")
        _require(code == 0 and raw.endswith(b"\x00\x00"), "Unsupported worktree metadata")
        entries = []
        for block in raw[:-2].split(b"\x00\x00"):
            fields = block.split(b"\x00")
            _require(fields[0].startswith(b"worktree ")
                     and sum(field.startswith(b"worktree ") for field in fields) == 1,
                     "Ambiguous worktree metadata")
            location = Path(os.fsdecode(fields[0][9:]))
            _require(location.is_absolute(), "Unsupported worktree path")
            entries.append((location, fields[1:]))
            _require(len(entries) + budget["paths"] <= MAX_PATHS, "Project aggregate worktree limit exceeded")
        budget["paths"] += len(entries)
        locations = [entry[0] for entry in entries]
        _require(len(locations) == len(set(locations)) and root in locations, "Ambiguous selected worktree metadata")
        primary = locations[0]
        for location, fields in entries:
            if location in (primary, root):
                _require(not any(f == b"bare" or f.startswith(b"prunable") for f in fields)
                         and sum(f.startswith(b"HEAD ") for f in fields) == 1,
                         "Unsupported primary or selected worktree metadata")
        primary_identity = _checkout_identity(primary, include_git_dir=True, limit=min(MAX_FILE, max(0, MAX_TOTAL - budget["bytes"])), total_limit=max(0, MAX_TOTAL - budget["bytes"]), deadline=budget.get("deadline"), budget=budget)
        _require(budget["bytes"] <= MAX_TOTAL, "Project aggregate byte limit exceeded")
        _require(primary_identity is not None and primary_identity[:2] == (primary, common)
                 and os.fsdecode(primary_identity[2].rstrip(b"\n")) == str(common), "Primary worktree identity is ambiguous")
        observed["binding"]["primary_root"] = _filesystem_binding(primary)
        observed["primary_root"] = str(primary)
        _check_observation_deadline(budget)
        return observed, None
    except (StartupError, OSError, ValueError, subprocess.SubprocessError) as error:
        if "Git inventory limit exceeded" in str(error) and MAX_TOTAL - budget["bytes"] < MAX_FILE:
            error = StartupError("Project aggregate byte limit exceeded; inventory remains incomplete")
        return observed, "Checkout/worktree observation incomplete: " + str(error)

def _authority_observation(root, budget, *, canonical_only=False, resources=None):
    held = ExitStack() if resources is not None else None; raw = None
    if resources is not None:
        from runtime.resource_accounting import Exhausted
    location = root / PROJECT_FILE
    observed = {"path": str(location), "fingerprint": {}, "binding": None, "record": None, "error": None, "target": None}
    try:
        observed["fingerprint"] = _inspection_fingerprint(root, str(PROJECT_FILE), budget, resources=resources)
        observed["target"] = _physical_target(root, location, observed["fingerprint"], budget)
        if observed["fingerprint"]["status"] == "absent":
            return observed
        _require(observed["fingerprint"]["status"] == "read", "Unreadable authority preimage")
        info = location.stat()
        _require(info.st_nlink == 1, "Authority record has physical aliases")
        observed["binding"] = {"device": info.st_dev, "inode": info.st_ino}
        raw = _read_file(location, min(MAX_FILE, 128 * 1024, max(0, MAX_TOTAL - budget["bytes"])), expected_binding=observed["binding"], resources=resources, held=held)
        budget["bytes"] += len(raw)
        _require(budget["bytes"] <= MAX_TOTAL, "Project aggregate byte limit exceeded")
        if resources is not None:
            owner = resources.current_acquisition()
            owner.reserve(requested=len(raw) + 64, live=512)
        _require(hashlib.sha256(raw).hexdigest() == observed["fingerprint"]["sha256"], "Authority changed while reading")
        if resources is None:
            record = read_request(raw)
            _bounded(record)
        else:
            decoded = owner._decode_owned(raw, read_request, _bounded)
            owner._require_owned(decoded)
            record = decoded.value
        _require(_physical_target(root, location, observed["fingerprint"], budget) == observed["target"],
                 "Authority physical identity changed while reading")
        if record.get("schema_version") == 2:
            observed["record"] = record
            if resources is not None:
                owner._require_owned(decoded)
                tree, serialized = decoded.tree, decoded.serialized
                path = 768 + 196 * (len(decoded.raw) + 1)
                owner.reserve(requested=2 * tree + serialized + len(decoded.raw), live=2048)
                pair_work = 0
                membership = record.get("membership")
                if type(membership) is dict:
                    for boundary in ("include", "exclude"):
                        rows = membership.get(boundary)
                        if type(rows) is list:
                            count = len(rows); chars = components = longest = 0; strings = True
                            for text in rows:
                                if type(text) is str:
                                    parts = text.count("/") + 1
                                    chars += len(text); components += parts; longest = max(longest, parts)
                                else: strings = False
                            if strings:
                                pair_work += 64 * count * count + 32 * count * (longest + 1) * (2 * chars + 2 * components + count)
                # Fresh pairwise ancestor/string work; sequential live scratch stays linear.
                owner.reserve(requested=8 * tree + 16 * serialized + 4 * path + pair_work,
                    live=8 * tree + 6 * serialized + 4 * path)
            decode_project_record(record)
            _check_observation_deadline(budget)
            return observed
        _require(not canonical_only, "Unsupported canonical enrollment schema")
        _object(record, ("schema_version", "project_id", "repository_root", "git_common_dir", "clients", "policy_digest"), ("runtime",))
        _require(type(record["schema_version"]) is int and record["schema_version"] == 1,
                 "Unsupported existing authority schema")
        _require(isinstance(record["project_id"], str)
                 and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", record["project_id"]), "Invalid authority identity")
        _strings(record["clients"], empty=False)
        _hash(record["policy_digest"])
        for key in ("repository_root", "git_common_dir"):
            _text(record[key])
        if "runtime" in record:
            _object(record["runtime"], ("schema_version", "state_dir"))
        runtime_settings(record, PACKAGE, Path(record["git_common_dir"]))
        observed["record"] = record
    except (StartupError, OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        if resources is not None and (isinstance(error, Exhausted) or getattr(error, "_vcw_resource_origin", None) is resources): raise
        observed["error"] = "Existing authority is malformed, unsupported, changed, aliased or unreadable"
    finally:
        raw = None
        if held is not None: held.close()
    return observed

def _project_observations(scope, sources, assessments=None):
    root = Path(scope["project_root"])
    budget = {"paths": len(sources), "bytes": sum(value.get("bytes", 0) for value in sources.values())}
    blockers = []
    def gap(owner, reason):
        blockers.append(_block(owner, reason, "Resolve the explicit scope/identity gap before proposing adoption"))
    by_root = {a["owner"]["root"]: a for a in _assessments(assessments)}
    repositories = []
    for member in scope["member_checkouts"]:
        observation, error = _checkout_observation(member, budget)
        repositories.append(observation)
        if error:
            gap(member, error)
    boundaries_valid = True
    for boundary in scope["membership"]["include"] + scope["membership"]["exclude"]:
        location = root / boundary
        try:
            _no_links(location)
            _require(location.resolve().is_relative_to(root), "Membership boundary escapes Project root")
        except (StartupError, OSError):
            boundaries_valid = False
            gap(boundary, "Membership boundary contains a link or canonical escape")
    if scope["kind"] == "umbrella":
        code, _ = _git(root, "-c", "safe.bareRepository=all", "rev-parse", "--absolute-git-dir")
        if code == 0:
            gap("project", "Umbrella inspection requires an explicitly selected non-Git root")
    common_dirs = [r["git_common_dir"] for r in repositories if r["git_common_dir"]]
    duplicate_repositories = {common for common in common_dirs if common_dirs.count(common) > 1}
    for repository in repositories:
        primary = Path(repository["primary_root"]) if repository["primary_root"] else None
        if repository["git_common_dir"] in duplicate_repositories:
            gap(repository["root"], "Multiple selected checkouts name the same Repository; select one action checkout")
            continue
        if primary is None:
            continue
        if scope["kind"] == "standalone" and primary != root:
            gap(repository["root"], "Standalone Project root must match its observed primary checkout")
            continue
        excluded = any(primary.is_relative_to(root / p) for p in scope["membership"]["exclude"])
        included = primary.is_relative_to(root) and any(
            primary.is_relative_to(root / p) for p in scope["membership"]["include"])
        repository["membership"] = "excluded" if excluded else "included" if included else "outside"
        if not included or excluded:
            gap(repository["root"], "Selected Repository is excluded or outside the literal membership boundary")
            continue
        if not boundaries_valid:
            repository["membership"] = "unknown"
            continue
        try:
            assessment = by_root.get(repository["root"], {})
            relevant = _relevant_inspection(scope)
            targets = _relevant_inputs(scope, assessment) if relevant else _observed_targets(assessment)
            prior = assessment.get("identity", {}).get("snapshot")
            repository["snapshot"] = _snapshot(Path(repository["root"]), targets, budget=budget,
                inventory=False if relevant else _inventory_enabled(prior) if prior is not None else True,
                relevant=relevant)
            if assessment.get("actions") or assessment.get("identity", {}).get("targets"):
                repository["targets"] = _target_observations(assessment, repository["snapshot"]["fingerprints"], budget=budget)
            if any(value["status"] not in ("read", "absent", "metadata") for value in repository["snapshot"]["fingerprints"].values()):
                gap(repository["root"], "Repository inventory contains unreadable, excluded or over-limit files")
        except (StartupError, OSError, ValueError, subprocess.SubprocessError) as error:
            gap(repository["root"], "Repository inventory incomplete: " + str(error))
    candidates = {root, *root.parents}
    for repository in repositories:
        for key in ("root", "primary_root"):
            if repository[key]:
                location = Path(repository[key])
                candidates.update((location, *location.parents))
    _require(len(candidates) <= MAX_PATHS, "Project authority path limit exceeded")
    authority = [_authority_observation(candidate, budget) for candidate in sorted(candidates)]
    present = [a for a in authority if a["fingerprint"].get("status") != "absent"]
    selected = next(a for a in authority if a["path"] == scope["authority"]["path"])
    actual = selected["fingerprint"].get("sha256", selected["fingerprint"].get("status"))
    if actual != scope["authority"]["sha256"]:
        gap("authority", "Selected authority preimage does not match the explicit scope")
    for item in present:
        if item["error"]:
            gap(item["path"], item["error"])
        if item is not selected or len(present) > 1:
            gap(item["path"], "Overlapping or member authority requires explicit reconciliation; membership does not transfer identity")
    project = None
    if scope["kind"] == "umbrella":
        assessment = by_root.get(str(root), {})
        names = list(dict.fromkeys([*scope["project_files"], *_observed_targets(assessment)]))
        fingerprints = _project_files(root, names, repositories, budget, gap)
        project = {"project_root": str(root), "paths": names, "fingerprints": fingerprints}
        if assessment.get("actions") or assessment.get("identity", {}).get("targets"):
            project["targets"] = _target_observations(assessment, fingerprints, repositories, budget=budget)

    return {"project_binding": _filesystem_binding(root), "authority": authority,
            "project": project, "repositories": repositories}, blockers


def _assessments(value):
    if value is None:
        return []
    return ([value["project"]] if value["project"] is not None else []) + value["repositories"]

def _observed_targets(assessment):
    prior = assessment.get("identity", {}).get("targets", {})
    return list(dict.fromkeys([*(name for name in prior if not name.startswith("cwd:/")),
                              *_file_targets(assessment.get("actions", []))]))


def _relevant_inputs(scope, assessment):
    snapshot = assessment.get("identity", {}).get("snapshot") or {}
    names = [*scope["project_files"], *_observed_targets(assessment),
             *(name for name in snapshot.get("fingerprints", {}) if not name.startswith("instruction:"))]
    inventory = assessment.get("inventory", {})
    _require(isinstance(inventory, dict), "Invalid scoped inventory")
    documents = inventory.get("documents", {})
    _require(isinstance(documents, dict) and all(isinstance(document, dict) for document in documents.values()),
             "Invalid scoped documents")
    names.extend(document["path"] for document in documents.values() if document.get("path") is not None)
    for rows, field in ((assessment.get("evidence", []), "inputs"),
                        (assessment.get("decisions", []), "invalidate_on")):
        _require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), "Invalid scoped input references")
        names.extend(name for row in rows for name in _strings(row.get(field, []))
                     if not name.startswith(("instruction:", "checkout:", "source:")) and name != "package")
    return sorted({_relative(name) for name in names})

def _project_file_boundary(root, name, repositories, *, budget=None):
    def check():
        if budget is not None:
            _require(type(budget["paths"]) is int and 0 <= budget["paths"] <= MAX_PATHS,
                     "Project aggregate path limit exceeded; inventory remains incomplete")
            _require(type(budget["bytes"]) is int and 0 <= budget["bytes"] <= MAX_TOTAL,
                     "Project aggregate byte limit exceeded; inventory remains incomplete")
            _check_observation_deadline(budget)
    check()
    target = root / name
    _no_links(target)
    protected = [Path(r[k]) for r in repositories for k in ("root", "primary_root", "git_dir", "git_common_dir") if r[k]]
    _require(not any(target.is_relative_to(p) or p.is_relative_to(target) for p in protected)
             and ".git" not in Path(name).parts, "Project file enters a member or Git tree")
    for parent in (target.parent, *target.parent.parents):
        if not parent.is_relative_to(root):
            break
        _require(not os.path.lexists(parent / ".git"), "Project file enters an unbound software tree")
    existing = next(parent for parent in (target.parent, *target.parent.parents) if parent.is_dir())
    if budget is None:
        code, _ = _git(existing, "-c", "safe.bareRepository=all", "rev-parse", "--absolute-git-dir")
    else:
        check()
        _require(budget["bytes"] < MAX_TOTAL, "Project aggregate byte limit exceeded; inventory remains incomplete")
        try:
            code, raw = _git(existing, "-c", "safe.bareRepository=all", "rev-parse", "--absolute-git-dir",
                             limit=MAX_TOTAL - budget["bytes"], deadline=budget.get("deadline"))
        except (StartupError, OSError, subprocess.SubprocessError):
            budget["bytes"] = max(budget["bytes"], MAX_TOTAL + 1)
            raise
        budget["bytes"] += len(raw)
        check()
    _require(code != 0, "Project file enters an unbound Git tree or metadata")
    check()

def _project_files(root, names, repositories, budget, gap):
    fingerprints = {}
    for name in names:
        try:
            _project_file_boundary(root, name, repositories, budget=budget)
            fingerprints[name] = _inspection_fingerprint(root, name, budget)
            _require(fingerprints[name]["status"] in ("read", "absent"), "Project file cannot be inspected safely")
        except (StartupError, OSError, subprocess.SubprocessError) as error:
            fingerprints[name] = {"status": "blocked"}
            gap(name, "Project file inspection blocked: " + str(error))
    return fingerprints

def _physical_target(root, location, fingerprint, budget, *, directory=False):
    _check_observation_deadline(budget)
    _no_links(location)
    parents = {}
    for parent in (location.parent, *location.parent.parents):
        _check_observation_deadline(budget)
        if not parent.is_relative_to(root):
            break
        if parent.exists():
            charged = budget.setdefault("target_directories", set())
            if str(parent) not in charged:
                _require(budget["paths"] < MAX_PATHS, "Project aggregate target-parent limit exceeded")
                budget["paths"] += 1
                charged.add(str(parent))
            parents[str(parent)] = _filesystem_binding(parent)
    physical = None
    if os.path.lexists(location):
        info = location.stat()
        _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
                 "Action target must be a regular file or command directory")
        _require(directory or info.st_nlink == 1, "Action target has physical aliases")
        physical = {"device": info.st_dev, "inode": info.st_ino}
    _require(not directory or physical is not None, "Command cwd must be an observed existing directory")
    if directory:
        _require(budget["paths"] < MAX_PATHS, "Project aggregate command-cwd limit exceeded")
        budget["paths"] += 1
    _check_observation_deadline(budget)
    return {"path": str(location), "binding": physical, "parents": parents, "fingerprint": fingerprint}

def _target_observations(assessment, fingerprints, repositories=(), *, budget):
    def check():
        _require(type(budget["paths"]) is int and 0 <= budget["paths"] <= MAX_PATHS,
                 "Project aggregate path limit exceeded; inventory remains incomplete")
        _require(type(budget["bytes"]) is int and 0 <= budget["bytes"] <= MAX_TOTAL,
                 "Project aggregate byte limit exceeded; inventory remains incomplete")
        _check_observation_deadline(budget)
    check()
    owner = assessment["owner"]
    root = Path(owner["root"])
    targets = {}
    names = _observed_targets(assessment)
    cwds = [action.get("cwd") for action in assessment["actions"] if action.get("kind") == "command"]
    cwds += [name[4:] for name in assessment["identity"].get("targets", {}) if name.startswith("cwd:/")]
    for name in [*names, *("cwd:" + _text(cwd) for cwd in dict.fromkeys(cwds))]:
        check()
        directory = name.startswith("cwd:/")
        location = Path(name[4:]) if directory else root / name
        _require(location.is_absolute() and location.is_relative_to(root)
                 and location.resolve() == location and ".git" not in location.relative_to(root).parts,
                 "Action target/cwd escapes its selected owner or enters Git metadata")
        _no_links(location)
        if owner["kind"] == "project":
            if location != root:
                _project_file_boundary(root, str(location.relative_to(root)), repositories, budget=budget)
        else:
            parent = location if directory else location.parent
            existing = next(p for p in (parent, *parent.parents) if p.is_dir())
            check()
            _require(budget["paths"] < MAX_PATHS, "Project aggregate path limit exceeded; inventory remains incomplete")
            _require(budget["bytes"] < MAX_TOTAL, "Project aggregate byte limit exceeded; inventory remains incomplete")
            try:
                identity = checkout(existing, limit=min(MAX_FILE, MAX_TOTAL - budget["bytes"]),
                                    deadline=budget.get("deadline"), budget=budget)
            except (StartupError, OSError, subprocess.SubprocessError):
                budget["bytes"] = max(budget["bytes"], MAX_TOTAL + 1)
                raise
            check()
            _require(identity == (root, Path(owner["git_common_dir"])),
                     "Action target/cwd enters another checkout or Git tree")
        targets[name] = _physical_target(root, location, None if directory else
            fingerprints.get(name, {"status": "blocked"}), budget, directory=directory)
    check()
    return targets

def _new_assessment(owner, identity):
    return {"owner": owner, "identity": identity, "inventory": {}, "decisions": [], "coverage": [],
            "actions": [], "evidence": [], "semantic_assessment": {}}

def _project_plan_shape(plan):
    def rows(value):
        _require(isinstance(value, list) and len(value) <= MAX_PATHS
                 and all(isinstance(row, dict) for row in value), "Invalid saved Project rows")
        return value

    def binding(value):
        _object(value, ("device", "inode"))
        _require(all(type(v) is int and v >= 0 for v in value.values()), "Invalid saved filesystem binding")

    def fingerprint(value, incomplete=False):
        if incomplete and value == {}:
            return
        _object(value, ("status",), ("sha256", "bytes", "reason"))
        _require(value["status"] in ("read", "absent", "special", "excluded", "oversized",
            "unreadable_or_link", "blocked", "unavailable", "source_total_limit"), "Invalid saved file observation")
        if value["status"] == "read":
            _require("sha256" in value and "bytes" in value, "Incomplete saved file fingerprint")
        if "sha256" in value:
            _hash(value["sha256"])
        if "bytes" in value:
            _require(type(value["bytes"]) is int and 0 <= value["bytes"] <= MAX_FILE, "Invalid saved file byte count")
        if "reason" in value:
            _text(value["reason"])

    def fingerprints(value, inventory=False):
        _require(isinstance(value, dict) and len(value) <= MAX_PATHS, "Invalid saved file observations")
        for name, item in value.items():
            _text(name)
            if item["status"] != "metadata" or not inventory:
                fingerprint(item)

    _object(plan, ("schema_version", "plan_id", "revision", "created_at", "project_scope", "identity",
        "assessments", "current_observations", "package_pins", "sources", "requirements", "invalidations",
        "decision_history", "recovery_requirement", "status", "blockers", "verified_at", "review_digest",
        "current_input_digest", "evidence_limits"), ("screening",))
    if "screening" in plan:
        _screening_projection(plan["screening"])
    _require(type(plan["schema_version"]) is int and plan["schema_version"] == 2
             and type(plan["revision"]) is int and plan["revision"] >= 1, "Unsupported Project inspection")
    for key in ("plan_id", "created_at", "verified_at", "recovery_requirement"):
        _text(plan[key])
    for key in ("review_digest", "current_input_digest"):
        _hash(plan[key])
    _require(plan["status"] in ("blocked", "stale", "draft", "awaiting_decision", "ready_for_review"), "Invalid Project inspection status")
    _object(plan["package_pins"], ("policy_digest", "runtime_digest"))
    for value in plan["package_pins"].values():
        _hash(value)
    _strings(plan["evidence_limits"])
    for item in rows(plan["blockers"]):
        _object(item, ("scope", "reason", "resumption"))
        for value in item.values():
            _text(value)
    for item in rows(plan["invalidations"]):
        _object(item, ("revision", "input_digest", "reason"))
        _require(type(item["revision"]) is int and 1 <= item["revision"] <= plan["revision"], "Invalid saved invalidation revision")
        _hash(item["input_digest"])
        _text(item["reason"])
    for item in rows(plan["decision_history"]):
        _object(item, ("revision", "decision"))
        _require(type(item["revision"]) is int and 1 <= item["revision"] <= plan["revision"]
                 and isinstance(item["decision"], dict), "Invalid saved decision history")
        _decision_fields(item["decision"])
    _object(plan["sources"], ("declared", "observed"))
    declared = plan["sources"]["declared"]
    _object(declared, ("schema_version", "clients", "toolkit", "required_sources"))
    _require(type(declared["schema_version"]) is int and declared["schema_version"] == 1, "Invalid saved source schema")
    _strings(declared["clients"], empty=False)
    for name in _strings(declared["required_sources"], empty=False):
        _relative(name)
    toolkit = declared["toolkit"]
    _object(toolkit, ("commit", "origin", "acquisition", "provenance", "trust", "files", "catalog"))
    _require(isinstance(toolkit["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", toolkit["commit"]), "Invalid saved Toolkit pin")
    for key in ("origin", "provenance"):
        _text(toolkit[key])
    _require(toolkit["acquisition"] in ("online", "offline") and toolkit["trust"] in ("recorded", "pending"),
             "Invalid saved source provenance")
    source_paths = []
    for item in rows(toolkit["files"]):
        _object(item, ("path", "local_path", "sha256", "required"))
        source_paths.append(_relative(item["path"]))
        _require(Path(_text(item["local_path"])).is_absolute(), "Invalid saved source location")
        _hash(item["sha256"])
        _require(type(item["required"]) is bool, "Invalid saved source requirement")
    _require(len(source_paths) == len(set(source_paths)), "Duplicate saved source path")
    fingerprints(plan["sources"]["observed"])
    _require(set(plan["sources"]["observed"]) == set(source_paths), "Saved source observations do not match their declaration")
    for catalog in (toolkit["catalog"], plan["requirements"]):
        for item in rows(catalog):
            _object(item, ("id", "source", "section", "stage", "mandatory", "dependencies"))
            for key in ("id", "source", "section"):
                _text(item[key])
            _require(item["stage"] in ("installation", "project_onboarding", "runtime")
                     and type(item["mandatory"]) is bool, "Invalid saved requirement")
            _strings(item["dependencies"])
        ids = [item["id"] for item in catalog]
        _require(len(ids) == len(set(ids)), "Duplicate saved requirement")
        _dependencies(catalog, set(ids))
    _require([r for r in plan["requirements"] if not r["id"].startswith("policy:")] == toolkit["catalog"],
             "Saved requirements must retain the complete pinned catalog metadata")
    _object(plan["identity"], ("project_id", "project_root", "kind", "binding", "authority", "id_origin"))
    scope = plan["project_scope"]
    _object(scope, ("kind", "project_root", "member_checkouts", "membership", "authority", "project_files"), ("inspection_scope",))
    relevant = _relevant_inspection(scope)
    _object(scope["membership"], ("include", "exclude", "future_join", "authorization"))
    _object(scope["authority"], ("path", "sha256", "reference"))
    _strings(scope["member_checkouts"], empty=False)
    for root in [scope["project_root"], *scope["member_checkouts"]]:
        _require(Path(_text(root)).is_absolute(), "Invalid saved Project/checkout root")
    for name in _strings(scope["project_files"]):
        _relative(name)
    for key in ("include", "exclude"):
        for name in _strings(scope["membership"][key], empty=key == "exclude"):
            if name != ".":
                _relative(name)
    _require(type(scope["membership"]["future_join"]) is bool, "Invalid saved future-join choice")
    _text(scope["membership"]["authorization"])
    _text(scope["authority"]["reference"])
    _require(scope["authority"]["path"] == str(Path(scope["project_root"]) / PROJECT_FILE), "Invalid saved authority path")
    if scope["authority"]["sha256"] != "absent":
        _hash(scope["authority"]["sha256"])
    identity = plan["identity"]
    _require(identity["kind"] == scope["kind"] and identity["project_root"] == scope["project_root"]
             and identity["kind"] in ("standalone", "umbrella")
             and identity["id_origin"] in ("proposed", "legacy_binding", "canonical_binding"), "Invalid Project identity binding")
    _text(identity["project_id"])
    binding(identity["binding"])
    _require(isinstance(identity["authority"], list) and len(identity["authority"]) <= MAX_PATHS,
             "Invalid Project authority observations")
    for authority in identity["authority"]:
        _object(authority, ("path", "fingerprint", "binding", "record", "error"), ("target",))
        _require(Path(_text(authority["path"])).is_absolute(), "Invalid saved authority location")
        fingerprint(authority["fingerprint"], incomplete=authority["error"] is not None)
        if authority["binding"] is not None:
            binding(authority["binding"])
        _require(authority["record"] is None or isinstance(authority["record"], dict), "Invalid saved authority record")
        if authority["error"] is not None:
            _text(authority["error"])
        if authority.get("target") is not None:
            target = authority["target"]
            _object(target, ("path", "binding", "parents", "fingerprint"))
            _require(target["path"] == authority["path"] and target["binding"] == authority["binding"]
                     and target["fingerprint"] == authority["fingerprint"], "Saved authority target differs")
            _require(isinstance(target["parents"], dict) and len(target["parents"]) <= MAX_PATHS,
                     "Invalid saved authority target parents")
            for parent, value in target["parents"].items():
                _require(Path(parent).is_relative_to(Path(authority["path"]).parent.parent)
                         and Path(authority["path"]).is_relative_to(Path(parent))
                         and Path(parent) != Path(authority["path"]), "Invalid saved authority target parent")
                binding(value)
    current = plan["current_observations"]
    _object(current, ("project_binding", "authority", "project", "repositories"))
    _require(current["project_binding"] == identity["binding"] and current["authority"] == identity["authority"],
             "Current Project authority binding differs from the frozen identity")
    _object(plan["assessments"], ("project", "repositories"))
    repositories = plan["assessments"]["repositories"]
    _require(isinstance(repositories, list) and 0 < len(repositories) <= MAX_PATHS
             and len(repositories) == len(scope["member_checkouts"])
             and isinstance(current["repositories"], list) and len(current["repositories"]) == len(repositories),
             "Invalid Repository assessments")
    _require((plan["assessments"]["project"] is None) == (identity["kind"] == "standalone")
             and (current["project"] is None) == (identity["kind"] == "standalone"), "Invalid Project assessment")
    ids = set()
    for kind, assessment, observed in zip(["project", *["repository"] * len(repositories)],
            [plan["assessments"]["project"], *repositories], [current["project"], *current["repositories"]]):
        if assessment is None:
            _require(kind == "project", "Missing saved Repository assessment")
            continue
        _object(assessment, ("owner", "identity", "inventory", "decisions", "coverage", "actions", "evidence", "semantic_assessment"))
        _require(isinstance(assessment["inventory"], dict) and isinstance(assessment["semantic_assessment"], dict),
                 "Invalid saved assessment inventory/semantics")
        for key in ("decisions", "coverage", "actions", "evidence"):
            rows(assessment[key])
        owner = assessment["owner"]
        _require(isinstance(owner, dict) and owner.get("project_id") == identity["project_id"]
                 and owner.get("kind") == kind, "Invalid assessment slot owner")
        _object(observed, ("owner", "identity"))
        _require(observed["owner"] == owner, "Current observation owner differs from its frozen assessment")
        if kind == "project":
            _object(owner, ("kind", "project_id", "root"))
            _require(owner["root"] == identity["project_root"], "Invalid Project assessment root")
            for value in (assessment["identity"], observed["identity"]):
                _object(value, ("project_root", "paths", "fingerprints"), ("targets",))
                _strings(value["paths"])
                _require(value["project_root"] == owner["root"] and set(scope["project_files"]) <= set(value["paths"])
                         and set(value["paths"]) <= set(scope["project_files"]) | set(_observed_targets(assessment)),
                         "Invalid Project named-file observations")
                fingerprints(value["fingerprints"])
                _require(set(value["fingerprints"]) == set(value["paths"]), "Incomplete Project named-file observation map")
        else:
            _object(owner, ("kind", "project_id", "repository_id", "checkout_id", "root", "primary_root", "git_dir", "git_common_dir"))
            _require(owner["kind"] == "repository", "Invalid assessment owner")
            for key in ("repository_id", "checkout_id"):
                _text(owner[key])
                _require(owner[key] not in ids, "Duplicate Repository/checkout identity")
                ids.add(owner[key])
            for key in ("primary_root", "git_dir", "git_common_dir"):
                _require(owner[key] is None or Path(_text(owner[key])).is_absolute(), "Invalid saved Repository location")
            for value in (assessment["identity"], observed["identity"]):
                _object(value, ("root", "primary_root", "git_dir", "git_common_dir", "binding", "membership", "snapshot"), ("targets",))
                _object(value["binding"], (), ("root", "primary_root", "git_dir", "git_common_dir"))
                for entry in value["binding"].values():
                    binding(entry)
                _require(all(value[key] == owner[key] for key in ("root", "primary_root", "git_dir", "git_common_dir"))
                         and value["binding"] == assessment["identity"]["binding"]
                         and value["membership"] in ("included", "excluded", "outside", "unknown"),
                         "Invalid Repository observation binding")
                if value["snapshot"] is not None:
                    snapshot = value["snapshot"]
                    checkout_fields = ("repository_root", "git_common_dir", "paths") if relevant else CHECKOUT_INPUTS
                    _object(snapshot, (*checkout_fields, "fingerprints", "instructions", "clients"),
                            () if relevant else ("inventory_version",))
                    _require(snapshot["repository_root"] == owner["root"]
                             and snapshot["git_common_dir"] == owner["git_common_dir"]
                             and snapshot["clients"] == declared["clients"], "Invalid selected checkout snapshot")
                    if not relevant:
                        if snapshot["head"] is not None:
                            _text(snapshot["head"])
                        _hash(snapshot["index_digest"])
                    for name in _strings(snapshot["paths"]):
                        _relative(name)
                    if not relevant:
                        _require(isinstance(snapshot["dirty_inventory"], list)
                                 and len(snapshot["dirty_inventory"]) <= 2 * MAX_PATHS, "Invalid saved checkout changes")
                        for item in snapshot["dirty_inventory"]:
                            _text(item)
                    fingerprints(snapshot["fingerprints"], inventory=_inventory_enabled(snapshot))
                    for instruction in rows(snapshot["instructions"]):
                        _object(instruction, ("path", "scope", "editable"))
                        _require(Path(_text(instruction["path"])).is_absolute()
                                 and Path(_text(instruction["scope"])).is_absolute()
                                 and type(instruction["editable"]) is bool, "Invalid saved instruction observation")
    _require([a["owner"]["root"] for a in repositories] == scope["member_checkouts"],
             "Repository assessments do not match the explicit member selection")
    for assessment, observed in zip(_assessments(plan["assessments"]), _assessments(current)):
        root = Path(assessment["owner"]["root"])
        for identity in (assessment["identity"], observed["identity"]):
            targets = identity.get("targets", {})
            _require(isinstance(targets, dict) and len(targets) <= MAX_PATHS, "Invalid saved target observations")
            for name, target in targets.items():
                directory = name.startswith("cwd:/")
                location = Path(name[4:]) if directory else root / _relative(name)
                _object(target, ("path", "binding", "parents", "fingerprint"))
                _require(target["path"] == str(location) and location.is_relative_to(root), "Invalid saved target owner binding")
                if target["binding"] is not None:
                    binding(target["binding"])
                _require(isinstance(target["parents"], dict) and len(target["parents"]) <= MAX_PATHS,
                         "Invalid saved target parents")
                for parent, value in target["parents"].items():
                    _require(Path(parent).is_relative_to(root) and location.is_relative_to(Path(parent)),
                             "Invalid saved target parent binding")
                    binding(value)
                if directory:
                    _require(target["fingerprint"] is None and target["binding"] is not None, "Invalid saved command cwd")
                else:
                    fingerprint(target["fingerprint"])
    if plan["identity"]["id_origin"] == "canonical_binding":
        selected = next((a for a in plan["identity"]["authority"] if a["path"] == scope["authority"]["path"]), None)
        _require(selected is not None and not selected["error"] and selected["record"] is not None,
                 "Canonical identity requires its frozen authority")
        record = selected["record"]
        decode_project_record(record)
        _require(record["project_id"] == plan["identity"]["project_id"]
                 and record["project_root"] == scope["project_root"] and record["kind"] == scope["kind"]
                 and record["project_binding"] == plan["identity"]["binding"], "Canonical Project identity differs")
        retained_ids = {r["repository_id"] for r in record["repositories"]} | {
            c["checkout_id"] for r in record["repositories"] for c in r["checkouts"]}
        for assessment in repositories:
            expected = _canonical_owner_ids(record, assessment["identity"])
            for key, accepted in zip(("repository_id", "checkout_id"), expected):
                proposed = assessment["owner"][key]
                _require(proposed == accepted if accepted is not None else proposed not in retained_ids,
                         "Canonical owner ID differs from its frozen authority")
    _validate_project_proposal(plan)


def _match_authorities(frozen, current):
    comparable = [{k: v for k, v in item.items() if k != "target" or "target" in prior}
                  for prior, item in zip(frozen, current)]
    return len(frozen) == len(current) and frozen == comparable


def _canonical_owner_ids(record, repository):
    matches = [r for r in record["repositories"] if r["primary_root"] == repository["primary_root"]]
    if not matches:
        for known in record["repositories"]:
            _require(known["git_common_dir"] != repository["git_common_dir"] and
                     known["binding"]["primary_root"] != repository["binding"].get("primary_root") and
                     known["binding"]["git_common_dir"] != repository["binding"].get("git_common_dir") and
                     all(c["root"] != repository["root"] and c["binding"]["root"] != repository["binding"].get("root")
                         for c in known["checkouts"]), "Canonical Repository identity conflicts; reconcile ownership")
        return None, None
    known = matches[0]
    _require(known["membership"] == "joined" and repository["membership"] == "included"
             and known["git_common_dir"] == repository["git_common_dir"]
             and all(known["binding"][k] == repository["binding"].get(k) for k in known["binding"]),
             "Canonical Repository binding or removed membership requires reconciliation")
    selected = next((c for c in known["checkouts"] if c["root"] == repository["root"]), None)
    if selected is None:
        _require(all(c["binding"]["root"] != repository["binding"].get("root") and
                     c["git_dir"] != repository["git_dir"] and
                     c["binding"]["git_dir"] != repository["binding"].get("git_dir") for c in known["checkouts"]),
                 "Canonical checkout identity moved; reconcile ownership")
        return known["repository_id"], None
    _require(selected["git_dir"] == repository["git_dir"] and
             all(selected["binding"][k] == repository["binding"].get(k) for k in selected["binding"]),
             "Canonical checkout binding requires reconciliation")
    return known["repository_id"], selected["checkout_id"]


def _inspect_project_scope(cwd, plan_dir, request):
    _bounded(request)
    _require(Path(cwd).is_absolute(), "Select an absolute Project inspection cwd")
    scope = _project_scope(request["project_scope"])
    _require(str(Path(cwd).resolve(strict=True)) in {scope["project_root"], *scope["member_checkouts"]},
             "Inspection cwd must select the Project or an explicit member checkout")
    private_path = Path(plan_dir).resolve()
    _require(not any(private_path.is_relative_to(Path(root).resolve())
                     for root in [scope["project_root"], *scope["member_checkouts"]]),
             "Project inspection plan directory must stay outside the Project and every selected checkout")
    declared = {key: deepcopy(value) for key, value in request.items() if key != "project_scope"}
    with _locked(plan_dir) as (path, directory):
        _source_record(declared, path)
        plan = _load(path)
        if plan is not None:
            _require(plan["schema_version"] == 2 and plan["project_scope"] == scope,
                     "Project scope is frozen; start a new reviewed plan for changed scope")
            _require(plan["sources"]["declared"] == declared, "Pinned sources/clients cannot change on resume")
        sources = _sources(declared)
        observed, blockers = _project_observations(scope, sources, plan["assessments"] if plan else None)
        package = _package_pins()
        reusable_id, canonical = None, None
        present = [a for a in observed["authority"] if a["fingerprint"].get("status") != "absent"]
        selected = next(a for a in observed["authority"] if a["path"] == scope["authority"]["path"])
        if selected["record"] and selected["record"].get("schema_version") == 2:
            canonical = selected["record"]
            _require(not selected["error"] and len(present) == 1
                     and selected["fingerprint"].get("sha256") == scope["authority"]["sha256"]
                     and canonical["kind"] == scope["kind"] and canonical["project_root"] == scope["project_root"]
                     and canonical["project_binding"] == observed["project_binding"],
                     "Canonical authority identity/bindings conflict; start a reconciled plan")
            for repository in observed["repositories"]:
                _canonical_owner_ids(canonical, repository)
            reusable_id = canonical["project_id"]
        elif scope["kind"] == "standalone" and len(present) == 1:
            selected = present[0]
            record = selected["record"]
            repository = observed["repositories"][0]
            if (record and selected["path"] == scope["authority"]["path"]
                    and selected["fingerprint"].get("sha256") == scope["authority"]["sha256"]
                    and record["repository_root"] == scope["project_root"] == repository["primary_root"]
                    and record["git_common_dir"] == repository["git_common_dir"]
                    and repository["membership"] == "included"
                    and record["policy_digest"] == package["policy_digest"]
                    and set(declared["clients"]) <= set(record["clients"])):
                reusable_id = record["project_id"]
            else:
                blockers.append(_block("authority", "Existing authority identity/bindings do not cover this Project",
                                       "Reconcile copied, moved or incompatible authority before adoption"))
        elif scope["kind"] == "umbrella" and any(a["record"] for a in present):
            blockers.append(_block("authority", "Existing standalone authority cannot become umbrella authority by discovery",
                                   "Propose an explicit transfer after authority reconciliation"))
        if plan is None:
            project_id = reusable_id or uuid.uuid4().hex
            origin = "canonical_binding" if canonical else "legacy_binding" if reusable_id else "proposed"
            identity = {"project_id": project_id, "project_root": scope["project_root"], "kind": scope["kind"],
                        "binding": observed["project_binding"], "authority": observed["authority"], "id_origin": origin}
            assessments = {"project": None, "repositories": []}
            if observed["project"] is not None:
                assessments["project"] = _new_assessment({"kind": "project", "project_id": project_id,
                    "root": scope["project_root"]}, observed["project"])
            for repository in observed["repositories"]:
                repository_id, checkout_id = _canonical_owner_ids(canonical, repository) if canonical else (None, None)
                owner = {"kind": "repository", "project_id": project_id, "repository_id": repository_id or uuid.uuid4().hex,
                         "checkout_id": checkout_id or uuid.uuid4().hex} | {key: repository[key] for key in (
                             "root", "primary_root", "git_dir", "git_common_dir")}
                if repository["snapshot"] is not None:
                    repository["snapshot"]["clients"] = declared["clients"]
                assessments["repositories"].append(_new_assessment(owner, deepcopy(repository)))
            plan = {"schema_version": 2, "plan_id": uuid.uuid4().hex, "revision": 1, "created_at": _now(),
                    "project_scope": scope, "identity": identity, "assessments": assessments,
                    "package_pins": package, "sources": {"declared": declared, "observed": sources},
                    "requirements": _requirements(declared), "invalidations": [], "decision_history": [],
                    "recovery_requirement": "Schema 2 requires scoped D3 proposals and D4 adoption support before apply"}
        else:
            _require(plan["identity"]["binding"] == observed["project_binding"]
                     and _match_authorities(plan["identity"]["authority"], observed["authority"]),
                     "Project authority/filesystem binding changed; start a new reviewed plan")
            observed["authority"] = deepcopy(plan["identity"]["authority"])
            for prior, current in zip(plan["assessments"]["repositories"], observed["repositories"]):
                _require(all(prior["identity"][key] == current[key] for key in (
                    "root", "primary_root", "git_dir", "git_common_dir", "binding", "membership")),
                    "Repository/checkout identity binding changed; start a new reviewed plan")
                if current["snapshot"] is not None:
                    current["snapshot"]["clients"] = declared["clients"]
            plan["revision"] += 1
        observed["repositories"] = [{"owner": deepcopy(assessment["owner"]), "identity": current}
            for assessment, current in zip(plan["assessments"]["repositories"], observed["repositories"])]
        if observed["project"] is not None:
            observed["project"] = {"owner": deepcopy(plan["assessments"]["project"]["owner"]), "identity": observed["project"]}
        current = {"observations": observed, "sources": sources, "package_pins": package}
        report = _finish_project(plan, current, blockers)
        _project_plan_shape(plan)
        _save(plan, path, directory)
    if _relevant_inspection(plan["project_scope"]):
        return report
    return _screen_onboarding_plan(plan_dir, plan["revision"], inspection=plan)


def _rows(value, fields):
    _require(isinstance(value, list) and len(value) <= MAX_PATHS, "Invalid onboarding rows")
    ids = set()
    for row in value:
        _object(row, fields)
        _text(row["id"])
        _require(row["id"] not in ids, "Duplicate onboarding row ID")
        ids.add(row["id"])
    return ids


def _decision_fields(decision):
    _object(decision, ("id", "rule", "source", "scope", "options", "recommendation", "selected",
        "disposition", "provenance", "evidence", "affected_actions", "invalidate_on", "conflict", "mode"))
    _text(decision["id"])
    for field in ("rule", "source", "scope", "recommendation", "provenance"):
        _text(decision[field])
    options = _strings(decision["options"], empty=False)
    _require(decision["recommendation"] in options and decision["disposition"] in (
        "confirmed", "inherited", "proposed", "pending"), "Invalid decision disposition/recommendation")
    _require(decision["selected"] is None or decision["selected"] in options, "Unknown decision selection")
    _require(decision["disposition"] not in ("confirmed", "inherited") or decision["selected"] is not None,
             "Confirmed decisions need a selected value")
    _require(type(decision["conflict"]) is bool and decision["mode"] in (None, "decide", "preserve", "replace"),
             "Invalid scoped conflict mode")
    _require(decision["conflict"] or decision["mode"] is None, "A mode requires an actual scoped conflict")
    for key in ("evidence", "affected_actions", "invalidate_on"):
        _strings(decision[key])


def _validate_proposal(plan, *, shared=False, references=None):
    """Validate references and concrete review targets; semantic findings remain agent assertions."""
    inventory = plan["inventory"]
    names = ("product", "critical_flows", "stack", "documents", "quality_commands", "services",
             "client_capabilities", "merge_feasibility", "instructions", "integrations")
    _object(inventory, () if shared else names, (*names, "uninspected_files") if shared else ("uninspected_files",))
    uninspected = inventory.get("uninspected_files", {})
    _require(isinstance(uninspected, dict) and len(uninspected) <= MAX_PATHS, "Invalid uninspected file assessment")
    for name, reason in uninspected.items():
        _relative(name)
        _text(reason)
    documents = inventory.get("documents", {})
    _require(isinstance(documents, dict) and (shared or len(documents) == 9),
             "Map all nine canonical document destinations from the pinned manifest")
    for document in documents.values():
        _object(document, ("path", "reason"))
        _text(document["reason"])
        if document["path"] is not None:
            _relative(document["path"])
    fact_records = []
    for key in inventory.keys() - {"documents", "uninspected_files"}:
        _object(inventory[key], ("facts", "assumptions"))
        _require(isinstance(inventory[key]["facts"], list), "Inventory facts must be a list")
        _strings(inventory[key]["assumptions"])
        for fact in inventory[key]["facts"]:
            _object(fact, ("summary", "evidence"))
            _text(fact["summary"])
            _strings(fact["evidence"])
            fact_records.append((key, fact))
    evidence_ids = _rows(plan["evidence"], ("id", "kind", "summary", "scope", "inputs", "reference")
                         + (("input_digest",) if references is not None else ()))
    fingerprints = plan["identity"]["fingerprints"]
    _require(set(uninspected) <= {name for name, value in fingerprints.items() if value["status"] == "metadata"},
             "Uninspected file assessments must name current metadata-only observations")
    input_keys = set(fingerprints) | (set() if shared else {"checkout:" + key for key in CHECKOUT_INPUTS if key in plan["identity"]}) | {"package"} | {
        "source:" + name for name in plan["sources"]["observed"]}
    if references is not None:
        input_keys.update(_file_targets(plan["actions"]))
    evidence = {row["id"]: row for row in plan["evidence"]}
    for row in evidence.values():
        _require(row["kind"] in ("observation", "behavior", "configuration", "baseline", "authorization"),
                 "Invalid evidence kind")
        for field in ("summary", "scope", "reference"):
            _text(row[field])
        _require(set(_strings(row["inputs"])) <= input_keys, "Unknown evidence input fingerprint")
    action_ids = _rows(plan["actions"], ("id", "dependencies", "kind", "target", "section", "before", "text",
        "after", "scope", "authorization", "verification", "preservation_exceptions", "decision_ids", "backup_required")
        + (("owner", "cwd") if references is not None else ()))
    decision_ids = _rows(plan["decisions"], ("id", "rule", "source", "scope", "options", "recommendation",
        "selected", "disposition", "provenance", "evidence", "affected_actions", "invalidate_on", "conflict", "mode"))
    coverage_ids = _rows(plan["coverage"], ("id", "source", "section", "stage", "mandatory", "dependencies",
        "applicability", "reason", "current_adoption", "qualification_evidence", "outcome", "actions", "scope",
        "risk", "verification", "blocker", "resumption", "decision_ids"))
    if references is not None:
        evidence = references["evidence"]
        evidence_ids = set(evidence)
        action_ids = references["actions"]
        decision_ids = set(references["decisions"])
    blockers = []
    if references is not None:
        for row in plan["evidence"]:
            _hash(row["input_digest"])
            context = references["context"] if row["kind"] in ("behavior", "baseline") else None
            expected = _evidence_digest(references["owner"], plan["identity"], plan["package_pins"],
                                        plan["sources"]["observed"], row["inputs"], context)
            if row["input_digest"] != expected:
                blockers.append(_block(row["id"], "Evidence does not match its owner/current input observations",
                                       "Reassess these exact inputs and provide newly bound evidence"))
            if row["kind"] in ("behavior", "baseline") and not any(
                    name in fingerprints or name.startswith("checkout:") for name in row["inputs"]):
                blockers.append(_block(row["id"], "Behavior evidence needs actual Repository/checkout inputs",
                                       "Observe behavior against this selected checkout"))
    for key, fact in fact_records:
        _require(set(fact["evidence"]) <= evidence_ids, "Unknown inventory evidence reference")
        if not fact["evidence"]:
            blockers.append(_block("inventory:" + key, "Observed facts need evidence references",
                                   "Supply sanitized observation evidence on pinned inputs"))
    for decision in plan["decisions"]:
        _decision_fields(decision)
        for field, allowed in (("evidence", evidence_ids), ("affected_actions", action_ids), ("invalidate_on", input_keys)):
            _require(set(_strings(decision[field])) <= allowed, "Unknown decision reference")
        if decision["disposition"] == "inherited" and not decision["evidence"]:
            blockers.append(_block(decision["id"], "Inherited choice lacks evidence", "Record its scoped provenance evidence"))
        if decision["conflict"] and decision["mode"] is None:
            blockers.append(_block(decision["id"], "Conflict needs a scoped mode/resolution", "Obtain the missing material decision"))
    decisions = references["decisions"] if references is not None else {d["id"]: d for d in plan["decisions"]}
    if references is None:
        _dependencies(plan["actions"], action_ids)
    seen = set()
    completed = action_ids - {a["id"] for a in plan["actions"]} if references is not None else set()
    for action in plan["actions"]:
        _strings(action["dependencies"])
        _require(set(action["dependencies"]) <= completed, "Actions must be in dependency order")
        completed.add(action["id"])
        _require(action["kind"] in ("write", "delete", "command", "external"), "Unknown proposed action kind")
        _require(action["scope"] in ("checkout", "shared", "global") and action["backup_required"] is True,
                 "Actions require explicit scope and D4 recovery")
        for field in ("target", "authorization", "verification"):
            _text(action[field])
        _strings(action["preservation_exceptions"])
        _require(set(_strings(action["decision_ids"])) <= decision_ids, "Unknown action decision")
        _require(action["section"] is None or isinstance(action["section"], str), "Invalid owned section")
        location = (action["scope"], action["target"])
        if location in seen:
            blockers.append(_block(action["id"], "Proposed actions overlap or duplicate an integration",
                                   "Merge section edits into one full-postimage action per owned file/resource"))
        seen.add(location)
        if action["kind"] in ("write", "delete"):
            _require(action["scope"] == ("shared" if shared else "checkout"),
                     "File changes must use their assessed owner scope")
            _relative(action["target"])
            _require(action["before"] == "absent" or (isinstance(action["before"], str) and HEX.fullmatch(action["before"])),
                     "File actions need exact before hash/absence")
            if action["kind"] == "write":
                # Text is the full postimage; section describes authorized scope for D4 review.
                _text(action["text"], empty=True)
                _hash(action["after"])
                _require(hashlib.sha256(action["text"].encode()).hexdigest() == action["after"],
                         "Proposed text does not match after hash")
            else:
                _require(action["text"] is None and action["after"] == "absent"
                         and action["section"] is None, "Delete must own one entire existing path")
        else:
            _require(action["section"] is None and action["after"] is None,
                     "External/command actions cannot assert a deterministic file result")
            _require(action["before"] is None, "External preconditions belong in scoped evidence")
            if action["kind"] == "command":
                _require(isinstance(action["text"], list) and 0 < len(action["text"]) <= MAX_PATHS,
                         "Expected a bounded exact argv vector")
                for argument in action["text"]:
                    _text(argument)
            else:
                _text(action["text"])
            if not any(e["kind"] == "authorization" and e["scope"] == action["scope"]
                       for e in evidence.values()):
                blockers.append(_block(action["id"], "External action lacks scoped authorization evidence",
                                       "Record authorization for that resource; no execution occurs in D3"))
    _require(not shared or not plan["coverage"], "Project shared-file assessment must keep coverage empty")
    requirements = {} if shared else {r["id"]: r for r in plan["requirements"]}
    _require(coverage_ids <= requirements.keys(), "Unknown coverage component/policy ID")
    for missing in sorted(requirements.keys() - coverage_ids):
        blockers.append(_block(missing, "Coverage assessment is missing", "Assess this pinned catalog/policy row"))
    for row in plan["coverage"]:
        requirement = requirements[row["id"]]
        _require(all(row[key] == requirement[key] for key in requirement),
                 "Coverage must retain pinned requirement metadata")
        _require(row["applicability"] in ("applicable", "inapplicable") and row["current_adoption"] in (
            "absent", "configured", "qualified") and row["outcome"] in ("planned", "reuse", "omit", "blocked"),
                 "Unknown coverage assessment value")
        for field in ("reason", "blocker", "resumption"):
            _text(row[field], empty=True)
        for field in ("scope", "risk", "verification"):
            _text(row[field])
        coverage_actions = references["coverage_actions"] if references is not None else action_ids
        for field, allowed in (("actions", coverage_actions), ("qualification_evidence", evidence_ids), ("decision_ids", decision_ids)):
            _require(set(_strings(row[field])) <= allowed, "Unknown coverage reference")
        problem = None
        if not row["reason"]:
            problem = "Applicability/optional omission needs an explicit reason"
        elif row["mandatory"] and row["outcome"] == "omit":
            problem = "Mandatory adoption cannot be omitted by runtime inapplicability"
        elif row["outcome"] == "planned" and not row["actions"]:
            problem = "Planned adoption lacks exact proposed actions"
        elif row["outcome"] == "blocked":
            problem = row["blocker"] or "Required prerequisite is missing"
        elif row["outcome"] == "reuse" and (row["current_adoption"] != "qualified" or not any(
                evidence[ref]["kind"] == "behavior" and evidence[ref]["inputs"] for ref in row["qualification_evidence"])):
            problem = "Equivalent integration lacks observed behavioral evidence on pinned inputs"
        elif row["mandatory"] and any(decisions[ref]["conflict"] and decisions[ref]["mode"] == "preserve"
                                       for ref in row["decision_ids"]):
            problem = "Preserved conflicting behavior blocks mandatory adoption"
        if problem:
            blockers.append(_block(row["id"], problem, row["resumption"] or "Resolve assessment/decision and supply evidence"))
    assessment = plan["semantic_assessment"]
    _object(assessment, ("complete", "conflicts_checked", "instructions_checked", "source_provenance_checked")
            + (() if shared else ("catalog_complete", "native_evaluation")))
    for field in set(assessment) - {"native_evaluation"}:
        _require(type(assessment[field]) is bool, "Semantic assessment fields must be booleans")
        if not assessment[field]:
            blockers.append(_block("semantic:" + field, "Agent semantic assessment remains incomplete",
                                   "Complete the scoped assessment; structural checks cannot replace it"))
    if not shared:
        native = assessment["native_evaluation"]
        _object(native, ("method", "smoke_cases"))
        _text(native["method"], empty=True)
        _strings(native["smoke_cases"])
        if not native["method"] or not native["smoke_cases"]:
            blockers.append(_block("native_evaluation", "Native harness method/initial smoke cases missing",
                                   "Plan required native evaluation; keep optional paid labs separate"))
    required = set(_file_targets(plan["actions"]))
    required.update(name for row in plan["evidence"] for name in row["inputs"])
    required.update(name for row in plan["decisions"] for name in row["invalidate_on"])
    required.update(document["path"] for document in documents.values() if document["path"] is not None)
    inventory_enabled = _inventory_enabled(plan["identity"])
    for key, value in fingerprints.items():
        if value["status"] == "metadata" and inventory_enabled and key not in required:
            if key not in uninspected:
                blockers.append(_block(key, "Uninspected content needs an applicability assessment",
                    "Record why this untouched file is unnecessary for adoption, or leave dependent coverage pending"))
            continue
        if value["status"] not in ("read", "absent"):
            blockers.append(_block(key, "File inventory has an explicit assessment gap: " + value["status"],
                                   "Resolve safely or record a scoped independent assessment prerequisite"))
    return blockers

def _record_plan(plan_dir, request, *, screening_check=None):
    """Replace one agent-authored proposal using an expected revision; never execute it."""
    _bounded(request)
    with _locked(plan_dir) as (path, directory):
        plan = _load(path)
        _require(plan is not None, "Inspect the selected checkout before recording a plan")
        screening_authentication = None
        if screening_check is not None:
            from runtime import screening
            _require((plan['plan_id'], plan['revision'], screening._selection(plan)) == screening_check[:3],
                     'Onboarding plan changed during screening authentication')
            screening_authentication = screening_check[3]
        payload = _authenticated_screening_payload(plan, screening_authentication)
        if payload is not None:
            plan['screening'] = dict(version=1, payload=payload,
                freshness=_screening_freshness(payload, plan['current_input_digest']))
        else:
            screening_authentication = None
            if 'screening' in plan:
                plan['screening']['freshness'] = dict(status='unavailable', reason='Source pointer authentication failed; allowance is not reset')
        if plan["schema_version"] == 2:
            return _record_project_plan(plan, request, path, directory, screening_authentication=screening_authentication)
        _object(request, ("expected_revision", "inventory", "decisions", "coverage", "actions", "evidence", "semantic_assessment"), ("reconcile",))
        _require(type(request["expected_revision"]) is int, "Expected an integer plan revision")
        _require(request["expected_revision"] == plan["revision"], "Onboarding revision conflict; inspect the current plan")
        previous = {d["id"]: deepcopy(d) for d in plan["decisions"]}
        _require(isinstance(request["decisions"], list), "Invalid decisions")
        _require(all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in request["decisions"]),
                 "Every decision needs a string ID")
        incoming = {d["id"]: d for d in request["decisions"]}
        _require(previous.keys() <= incoming.keys(), "Existing decision dispositions/history must be retained")
        changes, sources, current = _current(plan)
        _invalidate(plan, changes)
        if "reconcile" in request:
            reconciliation = request["reconcile"]
            _object(reconciliation, ("input_digest", "reason"))
            _hash(reconciliation["input_digest"])
            _text(reconciliation["reason"])
            _require(reconciliation["input_digest"] == _digest(current),
                     "Onboarding reconciliation inputs changed; inspect again")
            _require(current["identity"]["repository_root"] == plan["identity"]["repository_root"]
                     and current["identity"]["git_common_dir"] == plan["identity"]["git_common_dir"],
                     "Checkout authority changed; select a new plan")
            for item in plan["sources"]["declared"]["toolkit"]["files"]:
                observed = sources[item["path"]]
                _require(observed["status"] != "read" or observed["sha256"] == item["sha256"],
                         "Changed source bytes cannot be reconciled to a pinned source")
            plan["identity"] = current["identity"]
            plan["package_pins"] = current["package_pins"]
            plan["sources"]["observed"] = sources
            plan["requirements"] = _requirements(plan["sources"]["declared"])
            plan["invalidations"].append({"at": _now(), "revision": plan["revision"],
                "reconciled_inputs": changes, "input_digest": reconciliation["input_digest"],
                "reason": reconciliation["reason"]})
            changes = []
        for key in request.keys() - {"expected_revision", "reconcile"}:
            plan[key] = deepcopy(request[key])
        for key, old in previous.items():
            if incoming[key] != old:
                _remember_decision(plan, old)
        # New file destinations add raw fingerprints and instruction scopes even when Git ignores them.
        targets = _file_targets(plan["actions"])
        expanded = _snapshot(Path(plan["identity"]["repository_root"]), targets,
                             inventory=_inventory_enabled(plan["identity"]))
        fingerprints = plan["identity"]["fingerprints"]
        fingerprints.update({key: value for key, value in expanded["fingerprints"].items()
                             if (key.startswith("instruction:") or key in targets) and key not in fingerprints})
        known = {row["path"] for row in plan["identity"]["instructions"]}
        plan["identity"]["instructions"].extend(row for row in expanded["instructions"] if row["path"] not in known)
        blockers = _validate_proposal(plan)
        # New action absence/content must be checked too, including ignored destinations.
        new_changes, sources, current = _current(plan)
        changes = sorted(set(changes) | set(new_changes))
        _invalidate(plan, changes)
        plan["revision"] += 1
        report = _finish(plan, sources, changes, blockers, current=current,
                         screening_authentication=screening_authentication)
        _save(plan, path, directory)
        return report


def _evidence_digest(owner, identity, package, sources, inputs, context=None):
    values = dict(identity["fingerprints"])
    values.update({"checkout:" + key: identity[key] for key in CHECKOUT_INPUTS if key in identity})
    values.update({"source:" + key: value for key, value in sources.items()})
    values["package"] = package
    if "repository_root" in identity and "head" not in identity:
        _require(set(inputs) <= values.keys(), "Unknown evidence input fingerprint")
    value = {"owner": owner, "inputs": {name: values.get(name) for name in inputs}}
    if context is not None:
        value["context"] = context
    return _digest(value)


def _qualified(owner, local):
    parts = (["project", owner["project_id"]] if owner["kind"] == "project" else
             ["repository", owner["repository_id"], owner["checkout_id"]])
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in [*parts, local])

def _frozen_project(plan):
    return {"project_binding": plan["identity"]["binding"], "authority": plan["identity"]["authority"],
            "project": None if plan["assessments"]["project"] is None else {
                key: plan["assessments"]["project"][key] for key in ("owner", "identity")},
            "repositories": [{key: a[key] for key in ("owner", "identity")}
                             for a in plan["assessments"]["repositories"]]}

def _project_current(plan):
    sources = _sources(plan["sources"]["declared"])
    observed, blockers = _project_observations(plan["project_scope"], sources, plan["assessments"])
    _require(observed["project_binding"] == plan["identity"]["binding"]
             and _match_authorities(plan["identity"]["authority"], observed["authority"]),
             "Project authority/filesystem binding changed; start a new reviewed plan")
    observed["authority"] = deepcopy(plan["identity"]["authority"])
    for prior, current in zip(plan["assessments"]["repositories"], observed["repositories"]):
        _require(all(prior["identity"][key] == current[key] for key in (
            "root", "primary_root", "git_dir", "git_common_dir", "binding", "membership")),
            "Repository/checkout identity binding changed; start a new reviewed plan")
        if current["snapshot"] is not None:
            current["snapshot"]["clients"] = plan["sources"]["declared"]["clients"]
    observed["repositories"] = [{"owner": deepcopy(a["owner"]), "identity": current}
        for a, current in zip(plan["assessments"]["repositories"], observed["repositories"])]
    if observed["project"] is not None:
        observed["project"] = {"owner": deepcopy(plan["assessments"]["project"]["owner"]), "identity": observed["project"]}
    return {"observations": observed, "sources": sources, "package_pins": _package_pins()}, blockers

def _project_changes(plan, current):
    changes = ["authority_parent_observations"] if any("target" not in a for a in plan["identity"]["authority"]) else []
    if current["observations"] != _frozen_project(plan):
        changes.append("observations")
    for name in ("package_pins", "sources"):
        frozen = plan["sources"]["observed"] if name == "sources" else plan[name]
        if current[name] != frozen:
            changes.append(name)
    for assessment, observed in zip(_assessments(plan["assessments"]), _assessments(current["observations"])):
        for action in assessment["actions"]:
            if action["kind"] in ("write", "delete"):
                fingerprint = observed["identity"].get("targets", {}).get(action["target"], {}).get("fingerprint", {})
                actual = fingerprint.get("sha256", fingerprint.get("status"))
                if actual != action["before"]:
                    changes.append(_qualified(assessment["owner"], action["id"]))
    return sorted(set(changes))

def _project_decisions(plan):
    return {_qualified(a["owner"], d["id"]): d for a in _assessments(plan["assessments"]) for d in a["decisions"]}

def _invalidate_project(plan, current, changes):
    if not changes:
        return
    for identity, decision in _project_decisions(plan).items():
        if decision["disposition"] != "pending":
            _remember_decision(plan, decision | {"id": identity})
            decision["disposition"] = "pending"
    entry = {"revision": plan["revision"], "input_digest": _digest(current),
             "reason": "Frozen Project proposal inputs changed: " + ", ".join(changes)}
    if not plan["invalidations"] or plan["invalidations"][-1] != entry:
        plan["invalidations"].append(entry)

def _validate_project_proposal(plan):
    assessments = _assessments(plan["assessments"])
    count = 0
    for assessment in assessments:
        _require(isinstance(assessment["inventory"], dict) and isinstance(assessment["semantic_assessment"], dict),
                 "Invalid scoped inventory/semantic assessment")
        for key in ("actions", "evidence", "decisions", "coverage"):
            _require(isinstance(assessment[key], list), "Invalid scoped proposal rows")
            count += len(assessment[key])
        for key, section in assessment["inventory"].items():
            _require(isinstance(section, dict), "Invalid scoped inventory section")
            if key in ("documents", "uninspected_files"):
                count += len(section)
            else:
                _object(section, ("facts", "assumptions"))
                _require(isinstance(section["facts"], list) and isinstance(section["assumptions"], list),
                         "Invalid scoped inventory rows")
                count += len(section["facts"]) + len(section["assumptions"])
    _require(count <= MAX_PATHS, "Project aggregate proposal row limit exceeded")
    views, evidence, decisions, actions = [], {}, {}, {}
    for assessment in assessments:
        owner = assessment["owner"]
        view = {key: deepcopy(assessment[key]) for key in (
            "inventory", "decisions", "coverage", "actions", "evidence", "semantic_assessment")}
        view.update(requirements=plan["requirements"], sources=plan["sources"], package_pins=plan["package_pins"],
                    identity=assessment["identity"] if owner["kind"] == "project" else
                        assessment["identity"]["snapshot"] or {"fingerprints": {}})
        for key, lookup in (("actions", actions), ("evidence", evidence), ("decisions", decisions)):
            seen = set()
            for row in view[key]:
                _require(isinstance(row, dict) and isinstance(row.get("id"), str), "Every scoped row needs a string ID")
                _text(row["id"])
                _require(row["id"] not in seen, "Duplicate onboarding row ID")
                seen.add(row["id"])
                row["id"] = _qualified(owner, row["id"])
                lookup[row["id"]] = row
        views.append((assessment, view))
    blockers, graph = [], []
    project = plan["assessments"]["project"]
    shared_evidence = {_qualified(project["owner"], row["id"]) for row in project["evidence"]} if project else set()
    shared_actions = {_qualified(project["owner"], row["id"]) for row in project["actions"]} if project else set()
    for assessment, view in views:
        owner = assessment["owner"]
        local_evidence = {row["id"] for row in view["evidence"]}
        allowed = {key: row for key, row in evidence.items()
                   if key in local_evidence or key in shared_evidence and row.get("kind") in ("configuration", "authorization")}
        for action in view["actions"]:
            _require(action.get("owner") == owner, "Action owner must match its frozen Repository/checkout or Project")
            if action.get("kind") == "command":
                _require(isinstance(action.get("cwd"), str) and Path(action["cwd"]).is_absolute()
                         and Path(action["cwd"]).is_relative_to(Path(owner["root"])),
                         "Command action requires its explicit actual cwd within its owner")
            else:
                _require("cwd" in action and action["cwd"] is None, "Only command actions declare cwd")
        if owner["kind"] == "project":
            _require(all(row.get("kind") not in ("behavior", "baseline") for row in view["evidence"]),
                     "Project shared-file assessment cannot assert Repository behavior proof")
        if not view["semantic_assessment"]:
            _require(not any(view[k] for k in ("inventory", "decisions", "coverage", "actions", "evidence")),
                     "Incomplete scoped proposal requires its semantic assessment")
            blockers.append(_block(_qualified(owner, "proposal"), "Owner assessment is missing",
                                   "Complete this owner's scoped proposal before review"))
            continue
        project = plan["assessments"]["project"]
        context = {"checkout": view["identity"], "package_pins": plan["package_pins"],
                   "sources": plan["sources"]["observed"],
                   "project_files": project["identity"]["fingerprints"] if project else {}}
        found = _validate_proposal(view, shared=owner["kind"] == "project",
            references={"evidence": allowed, "decisions": decisions, "actions": set(actions),
                        "owner": owner, "context": context,
                        "coverage_actions": shared_actions | {a["id"] for a in view["actions"]}})
        blockers.extend(b | {"scope": b["scope"] if b["scope"] in evidence.keys() | decisions.keys() | actions.keys()
                            else _qualified(owner, b["scope"])} for b in found)
        previous = None
        for action in view["actions"]:
            dependencies = list(action["dependencies"])
            if previous is not None and previous not in dependencies:
                dependencies.append(previous)
            graph.append({"id": action["id"], "dependencies": dependencies})
            previous = action["id"]
    _dependencies(graph, set(actions))
    project = plan["assessments"]["project"]
    if project is not None:
        for action in project["actions"]:
            ref = _qualified(project["owner"], action["id"])
            for repository in plan["assessments"]["repositories"]:
                if not any(ref in row["actions"] for row in repository["coverage"]):
                    blockers.append(_block(ref, "Shared change lacks affected Repository adoption/evaluation linkage",
                                           "Reference this action in each selected Repository's assessed coverage"))
    return blockers

def _project_target_conflicts(plan, current):
    seen = []
    for assessment, observed in zip(_assessments(plan["assessments"]), _assessments(current["observations"])):
        for action in assessment["actions"]:
            if action["kind"] not in ("write", "delete"):
                continue
            target = observed["identity"].get("targets", {}).get(action["target"])
            _require(target is not None and target["fingerprint"]["status"] in ("read", "absent"),
                     "Action target observation is incomplete")
            path = Path(target["path"])
            for prior in seen:
                other = Path(prior["path"])
                _require(not (path.is_relative_to(other) or other.is_relative_to(path)
                    or target["binding"] is not None and target["binding"] == prior["binding"]),
                    "Proposed file targets overlap, alias or have an ancestor conflict")
            seen.append(target)

def _expand_project_inputs(plan, current):
    for assessment, observed in zip(_assessments(plan["assessments"]), _assessments(current["observations"])):
        old, new = assessment["identity"], observed["identity"]
        if "targets" in new:
            old.setdefault("targets", {}).update({key: value for key, value in new["targets"].items()
                                                 if key not in old.get("targets", {})})
        if assessment["owner"]["kind"] == "repository":
            old, new = old["snapshot"], new["snapshot"]
            if old is None or new is None:
                continue
            known = {row["path"] for row in old["instructions"]}
            old["instructions"].extend(row for row in new["instructions"] if row["path"] not in known)
            if _relevant_inspection(plan["project_scope"]):
                old["paths"] = sorted(set(old["paths"]) | set(new["paths"]))
        else:
            old["paths"] = list(dict.fromkeys([*old["paths"], *new["paths"]]))
        old["fingerprints"].update({key: value for key, value in new["fingerprints"].items()
                                    if key not in old["fingerprints"]})

def _finish_project(plan, current, blockers, screening_authentication=None):
    if current["package_pins"] == plan["package_pins"]:
        _require(plan["requirements"] == _requirements(plan["sources"]["declared"]),
                 "Current package requirements must retain the complete catalog and policy metadata")
    changes = _project_changes(plan, current)
    _invalidate_project(plan, current, changes)
    blockers += _validate_project_proposal(plan)
    plan["current_observations"] = current["observations"]
    report = _finish(plan, current["sources"], changes, blockers, current=current,
                     draft=any(not a["semantic_assessment"] for a in _assessments(plan["assessments"])),
                     screening_authentication=screening_authentication)
    plan["evidence_limits"].append("Private IDs and authority references do not authenticate adoption; schema 2 cannot apply.")
    return report

def _record_project_plan(plan, request, path, directory, screening_authentication=None):
    _object(request, ("expected_revision", "assessments"), ("reconcile",))
    _require(type(request["expected_revision"]) is int and request["expected_revision"] == plan["revision"],
             "Onboarding revision conflict; inspect the current plan")
    _object(request["assessments"], ("project", "repositories"))
    incoming = deepcopy(request["assessments"])
    _require(isinstance(incoming["repositories"], list)
             and len(incoming["repositories"]) == len(plan["assessments"]["repositories"])
             and (incoming["project"] is None) == (plan["assessments"]["project"] is None),
             "Every frozen owner assessment must be retained")
    previous = deepcopy(_project_decisions(plan))
    budget = {"paths": 0, "bytes": 0}
    for old, new in zip(_assessments(plan["assessments"]), _assessments(incoming)):
        _require(isinstance(new, dict), "Invalid owner assessment")
        new.setdefault('identity', deepcopy(old['identity']))
        _object(new, ("owner", "identity", "inventory", "decisions", "coverage", "actions", "evidence", "semantic_assessment"))
        _require(new["owner"] == old["owner"] and new["identity"] == old["identity"],
                 "Frozen owner/checkout identity cannot be rebound by a proposal")
        snapshot = old['identity'].get('snapshot', old['identity'])
        _require(isinstance(new['actions'], list) and isinstance(new['evidence'], list), 'Invalid proposal rows')
        for action in new['actions']:
            _require(isinstance(action, dict), 'Invalid action')
            action.setdefault('owner', deepcopy(old['owner']))
            if action.get('kind') in {'write', 'delete'}:
                _relative(action.get('target'))
                if 'before' not in action:
                    observed = _inspection_fingerprint(Path(old['owner']['root']), action['target'], budget)
                    _require(observed['status'] in {'read', 'absent'}, 'Action preimage is unavailable')
                    action['before'] = observed.get('sha256', 'absent')
                if 'after' not in action:
                    if action['kind'] == 'write':
                        _text(action.get('text'), empty=True)
                        action['after'] = hashlib.sha256(action['text'].encode()).hexdigest()
                    else:
                        action['after'] = 'absent'
        if _relevant_inspection(plan["project_scope"]):
            expanded = _snapshot(Path(old['owner']['root']), _relevant_inputs(plan['project_scope'], new),
                                 budget=budget, relevant=True)
            snapshot['paths'] = sorted(set(snapshot['paths']) | set(expanded['paths']))
            snapshot['fingerprints'].update({name: value for name, value in expanded['fingerprints'].items()
                                            if name not in snapshot['fingerprints']})
            known = {row['path'] for row in snapshot['instructions']}
            snapshot['instructions'].extend(row for row in expanded['instructions'] if row['path'] not in known)
            new['identity'] = deepcopy(old['identity'])
        for item in new['evidence']:
            _require(isinstance(item, dict) and isinstance(item.get('inputs'), list), 'Invalid evidence inputs')
            if 'input_digest' not in item:
                item['input_digest'] = _evidence_digest(old['owner'], snapshot, plan['package_pins'],
                    plan['sources']['observed'], item['inputs'])
        _require(isinstance(new["decisions"], list) and all(isinstance(d, dict) and isinstance(d.get("id"), str)
                 for d in new["decisions"]), "Every decision needs a string ID")
        _require({d["id"] for d in old["decisions"]} <= {d["id"] for d in new["decisions"]},
                 "Existing decision dispositions/history must be retained")
    _validate_project_proposal(plan | {"assessments": incoming})
    current, blockers = _project_current(plan)
    changes = _project_changes(plan, current)
    _invalidate_project(plan, current, changes)
    if "reconcile" in request:
        reconciliation = request["reconcile"]
        _object(reconciliation, ("input_digest", "reason"))
        _hash(reconciliation["input_digest"])
        _text(reconciliation["reason"])
        _require(reconciliation["input_digest"] == _digest(current), "Onboarding reconciliation inputs changed; inspect again")
        for item in plan["sources"]["declared"]["toolkit"]["files"]:
            observed = current["sources"][item["path"]]
            _require(observed["status"] != "read" or observed["sha256"] == item["sha256"],
                     "Changed source bytes cannot be reconciled to a pinned source")
        for assessment, observed in zip(_assessments(plan["assessments"]), _assessments(current["observations"])):
            assessment["identity"] = deepcopy(observed["identity"])
        plan["package_pins"] = current["package_pins"]
        plan["sources"]["observed"] = current["sources"]
        plan["requirements"] = _requirements(plan["sources"]["declared"])
        plan["invalidations"].append({"revision": plan["revision"], "input_digest": reconciliation["input_digest"],
                                      "reason": reconciliation["reason"]})
    for assessment, new in zip(_assessments(plan["assessments"]), _assessments(incoming)):
        for key in ("inventory", "decisions", "coverage", "actions", "evidence", "semantic_assessment"):
            assessment[key] = deepcopy(new[key])
    for key, decision in _project_decisions(plan).items():
        if key in previous and decision != previous[key]:
            _remember_decision(plan, previous[key] | {"id": key})
    _validate_project_proposal(plan)
    expanded, observation_blockers = _project_current(plan)
    _project_target_conflicts(plan, expanded)
    _expand_project_inputs(plan, expanded)
    # Reinspect the same named targets after structural and cross-owner checks.
    final, final_blockers = _project_current(plan)
    _require(expanded == final, "Project inputs changed during proposal validation; inspect again")
    plan["revision"] += 1
    report = _finish_project(plan, final, blockers + observation_blockers + final_blockers,
                             screening_authentication=screening_authentication)
    _project_plan_shape(plan)
    _save(plan, path, directory)
    return report


def _screening_directory_binding(path):
    from runtime import review_storage as storage
    return dict(directory=_filesystem_binding(path), parents=storage._parents(path))


@contextmanager
def _existing_screening_plan(value, *, budget):
    """Existing-only no-follow opener; a lost source never creates directories or locks."""
    import fcntl
    _check_observation_deadline(budget)
    path = Path(value)
    _require(path.is_absolute() and path.resolve(strict=True) == path, 'Screening plan path changed')
    _no_links(path)
    directory = _open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    lock = None
    try:
        _private(os.fstat(directory), directory=True)
        lock = os.open('.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        _private(os.fstat(lock))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError('Onboarding plan busy; screening remains unavailable') from None
        _check_observation_deadline(budget)
        yield path, directory
        _check_observation_deadline(budget)
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def _screening_projection(value):
    from runtime import state
    _object(value, ('version', 'payload', 'freshness'))
    _require(type(value['version']) is int and value['version'] == 1, 'Unsupported screening projection')
    payload = value['payload']
    _object(payload, ('version', 'accounting', 'ticket', 'repositories', 'inputs', 'coverage', 'timing'))
    _require(type(payload['version']) is int and payload['version'] == 1, 'Unsupported screening payload')
    accounting = payload['accounting']
    _object(accounting, ('authority', 'owner', 'reference', 'created_at'))
    _object(accounting['owner'], ('kind', 'id', 'created_at'))
    _require(accounting['owner']['kind'] == 'temporary'
        and re.fullmatch(r'[0-9a-f]{32}', accounting['owner']['id'])
        and accounting['owner']['created_at'] == accounting['created_at'], 'Invalid screening owner')
    state._integer(accounting['created_at'], 'original screening owner creation time')
    state._artifact_reference(accounting['reference'])
    authority = accounting['authority']
    _object(authority, ('directory', 'database', 'directory_binding', 'database_binding'))
    for key in ('directory', 'database'):
        state._canonical_path(authority[key])
        state._canonical_binding(authority[key + '_binding'])
    state._screening_ticket(payload['ticket'])
    _require(isinstance(payload['repositories'], list) and 0 < len(payload['repositories']) <= MAX_PATHS,
             'Invalid screening Repository scope')
    from runtime import review_storage as storage
    roots = []
    for target in payload['repositories']:
        storage._target_shape(target)
        _require(target['git'] is not None, 'Screening Repository needs its physical Git identity')
        roots.append(target['path'])
    _require(roots == sorted(set(roots)), 'Screening Repository scope must be unique and sorted')
    _object(payload['inputs'], ('digest', 'files', 'checkouts'))
    _hash(payload['inputs']['digest'])
    _require(isinstance(payload['inputs']['files'], list) and len(payload['inputs']['files']) <= MAX_PATHS,
             'Invalid screening input fingerprints')
    names = set()
    for item in payload['inputs']['files']:
        _object(item, ('repository_root', 'path', 'sha256', 'bytes', 'binding'))
        _require(item['repository_root'] in roots, 'Screening input escapes its Repository')
        _relative(item['path'])
        key = (item['repository_root'], item['path'])
        _require(key not in names, 'Duplicate screening input fingerprint')
        names.add(key)
        _hash(item['sha256'])
        state._integer(item['bytes'], 'screening input bytes')
        _require(item['bytes'] <= MAX_FILE, 'Screening input exceeds its read bound')
        state._canonical_binding(item['binding'])
    _require(isinstance(payload['inputs']['checkouts'], list)
        and len(payload['inputs']['checkouts']) <= len(roots), 'Invalid screening checkout inputs')
    seen = set()
    for item in payload['inputs']['checkouts']:
        _object(item, ('repository_root', 'head', 'index_digest', 'dirty_digest', 'paths_digest'))
        _require(item['repository_root'] in roots and item['repository_root'] not in seen,
                 'Invalid screening checkout scope')
        seen.add(item['repository_root'])
        if item['head'] is not None:
            _require(isinstance(item['head'], str) and re.fullmatch(r'[0-9a-f]{40,64}', item['head']),
                     'Invalid screening checkout HEAD')
        for key in ('index_digest', 'dirty_digest', 'paths_digest'):
            _hash(item[key])
    coverage = payload['coverage']
    _object(coverage, ('groups', 'gaps', 'partial'))
    _strings(coverage['gaps'])
    _require(type(coverage['partial']) is bool and isinstance(coverage['groups'], list)
        and len(coverage['groups']) <= MAX_PATHS, 'Invalid screening coverage')
    for group in coverage['groups']:
        _object(group, ('repository_root', 'ancestor', 'counted_files', 'oversized_count', 'inspected_files',
            'exclusions', 'unknown_coverage', 'representatives', 'impact', 'effort', 'risk', 'confidence', 'next_step'))
        _require(group['repository_root'] in roots, 'Screening group escapes its Repository')
        if group['ancestor'] != '.':
            _relative(group['ancestor'])
        for key in ('counted_files', 'oversized_count', 'inspected_files'):
            state._integer(group[key], 'screening ' + key)
        _require(group['oversized_count'] <= group['counted_files']
            and group['inspected_files'] <= group['oversized_count'], 'Inconsistent screening counts')
        for key in ('exclusions', 'unknown_coverage'):
            _require(isinstance(group[key], dict) and len(group[key]) <= 16, 'Invalid screening gaps')
            for reason, count in group[key].items():
                _text(reason)
                state._integer(count, 'screening gap count', 1)
        _require(isinstance(group['representatives'], list) and len(group['representatives']) <= 3,
                 'Invalid screening representatives')
        for item in group['representatives']:
            _object(item, ('path', 'lines', 'bytes', 'reference_limit', 'basis', 'sample_definitions'))
            _relative(item['path'])
            for key in ('lines', 'bytes', 'reference_limit', 'sample_definitions'):
                state._integer(item[key], 'screening representative ' + key)
            _text(item['basis'])
        for key in ('impact', 'effort', 'risk', 'confidence', 'next_step'):
            _text(group[key])
    timing = payload['timing']
    _object(timing, ('limit_ms', 'elapsed_ms', 'qualification'))
    _require(type(timing['limit_ms']) is int and timing['limit_ms'] == 300000, 'Invalid screening deadline')
    state._integer(timing['elapsed_ms'], 'observed screening elapsed time')
    _text(timing['qualification'])
    _object(value['freshness'], ('status', 'reason'))
    _require(value['freshness']['status'] in ('current', 'stale', 'unavailable'), 'Invalid screening freshness')
    _text(value['freshness']['reason'])
    _bounded(value)
    return value


def _read_onboarding_screening_source(pointer, *, locked=None, budget=None):
    from runtime import state
    state._screening_result(pointer)
    budget = budget or {}
    path = Path(pointer['plan_directory'])

    def read():
        _check_observation_deadline(budget)
        _require(_screening_directory_binding(path) == pointer['directory_binding'], 'Screening source physical binding changed')
        plan = _load(path)
        _require(plan is not None and plan['plan_id'] == pointer['plan_id'] and 'screening' in plan,
                 'Screening source plan is unavailable')
        payload = plan['screening']['payload']
        _require(_digest(payload) == pointer['projection_digest'] and payload['ticket'] == pointer['ticket'],
                 'Screening source payload changed')
        _require(_screening_directory_binding(path) == pointer['directory_binding'], 'Screening source binding changed while reading')
        _check_observation_deadline(budget)
        return deepcopy(payload)

    if locked is not None:
        _require(str(path) in locked, 'Screening source lacks its existing plan lock')
        return read()
    with _existing_screening_plan(path, budget=budget):
        return read()


def _screening_freshness(payload, current_digest):
    current = payload['inputs']['digest'] == current_digest
    return dict(status='current' if current else 'stale', reason=(
        'Matches the observed input digest' if current else 'Inputs changed since the bounded attempt; coverage has not expanded'))


def _collect_onboarding_screening(run):
    """Local physical-line inventory with a bounded shallow sample, never a project runner."""
    from runtime import screening
    from runtime.startup import _canonical_plain
    import time
    execution = screening._execution(run)
    _require('produced' not in execution and 'ticket' in execution, 'Screening collector is one-shot')
    _require(not _relevant_inspection(execution['last_plan'].get('project_scope', {})),
             'Repository screening requires a full inspection')
    repositories = sorted((target for target in _canonical_plain(execution['targets']) if target['git'] is not None),
                          key=lambda item: item['path'])
    budget = dict(execution['observation_budget'], deadline=run.deadline_monotonic - 0.5)
    groups, gaps, fingerprints, checkouts = {}, [], [], []
    source_suffixes = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}
    excluded = {'.git', 'node_modules', 'vendor', 'vendors', 'dist', 'build', 'coverage', '__pycache__',
                '.venv', 'venv', 'generated', '.next', '.tox'}
    try:
        for repository in repositories:
            _check_observation_deadline(budget)
            root = Path(repository['path'])
            _require(_filesystem_binding(root) == repository['binding'], 'Screening Repository binding changed')
            checkout_inputs, names = _screening_checkout_inputs(root, budget)
            plan = execution['last_plan']
            snapshots = ([plan['identity']] if plan['schema_version'] == 1 else
                [item['identity']['snapshot'] for item in plan['current_observations']['repositories']])
            prior = next((item for item in snapshots if item is not None and item['repository_root'] == str(root)), None)
            if prior is not None:
                _require(checkout_inputs == dict(repository_root=str(root), head=prior['head'],
                    index_digest=prior['index_digest'], dirty_digest=_digest(prior['dirty_inventory']),
                    paths_digest=_digest(prior['paths'])), 'Screening inputs changed after required Inspect')
            checkouts.append(checkout_inputs)
            for name in names:
                _check_observation_deadline(budget)
                _relative(name)
                budget['paths'] += 1
                _require(budget['paths'] <= MAX_PATHS, 'Screening aggregate path limit reached')
                relative = PurePosixPath(name)
                ancestor = str(PurePosixPath(*relative.parts[:min(2, len(relative.parts) - 1)]))
                group = groups.setdefault((str(root), ancestor), dict(repository_root=str(root), ancestor=ancestor,
                    counted_files=0, oversized_count=0, inspected_files=0, exclusions={}, unknown_coverage={},
                    representatives=[], impact='unknown', effort='unknown', risk='unknown', confidence='unknown',
                    next_step='Request a separate detailed assessment before choosing refactoring boundaries'))
                def gap(category, reason):
                    group[category][reason] = group[category].get(reason, 0) + 1
                if any(part in excluded for part in relative.parts) or '.min.' in name or relative.name.endswith(('.generated.py', '.generated.ts')):
                    gap('exclusions', 'generated/vendor/dependency paths')
                    continue
                if relative.name == '.env' or relative.name.startswith('.env.') or relative.suffix.lower() in {
                        '.pem', '.key', '.p12', '.pfx'}:
                    gap('exclusions', 'potential secret file')
                    continue
                if relative.suffix.lower() not in source_suffixes:
                    gap('exclusions', 'outside supported source-file types')
                    continue
                try:
                    _require(budget['bytes'] < MAX_TOTAL, 'Screening aggregate byte limit reached')
                    info = (root / name).lstat()
                    binding = dict(device=info.st_dev, inode=info.st_ino)
                    # Reserve two rereads for publication checks within the same byte/path allowance.
                    _require(3 * (budget['bytes'] + info.st_size) <= MAX_TOTAL,
                             'Screening aggregate read allowance reached')
                    data = _read_file(root / name, min(MAX_FILE, MAX_TOTAL - budget['bytes']), expected_binding=binding)
                    after = (root / name).lstat()
                    _require((after.st_dev, after.st_ino) == (info.st_dev, info.st_ino), 'Screening input path changed')
                    budget['bytes'] += len(data)
                    _check_observation_deadline(budget)
                    if b'\0' in data:
                        gap('exclusions', 'binary source file')
                        continue
                    text = data.decode('utf-8')
                    lines = len(text.splitlines())
                except UnicodeError:
                    gap('exclusions', 'non-UTF-8 source file')
                    continue
                except (StartupError, OSError):
                    gap('unknown_coverage', 'unsafe/unreadable/over-limit input')
                    continue
                fingerprints.append(dict(repository_root=str(root), path=name, sha256=hashlib.sha256(data).hexdigest(),
                                         bytes=len(data), binding=binding))
                group['counted_files'] += 1
                limit = 1000 if relative.suffix.lower() == '.py' else 350
                if lines <= limit:
                    continue
                group['oversized_count'] += 1
                # Count every safe candidate; sample only a bounded prefix and retain no source text.
                sample = text[:8192].splitlines()[:80]
                definitions = sum(bool(re.match(r'\s*(?:async\s+)?(?:def|class|function)\s|\s*(?:export\s+)?(?:const|let)\s', line))
                                  for line in sample)
                group['inspected_files'] += 1
                if definitions > 1:
                    group.update(impact='Multiple definitions in a bounded prefix; responsibility mix remains unverified', confidence='low')
                if len(group['representatives']) < 3:
                    group['representatives'].append(dict(path=name, lines=lines, bytes=len(data), reference_limit=limit,
                        basis='Policy reference only; local physical lines include blanks and comments, not an enforced project gate',
                        sample_definitions=definitions))
    except (StartupError, OSError):
        gaps.append('Collection stopped at its shared deadline or an aggregate safety limit; remaining coverage is unknown')
    partial = bool(gaps) or any(group['unknown_coverage'] for group in groups.values())
    payload = dict(version=1, accounting=_canonical_plain(execution['accounting']), ticket=_canonical_plain(execution['ticket']),
        repositories=repositories, inputs=dict(_canonical_plain(execution['inputs']), files=fingerprints, checkouts=checkouts),
        coverage=dict(groups=list(groups.values()), gaps=gaps, partial=partial),
        timing=dict(limit_ms=300000, elapsed_ms=max(0, int((time.monotonic() - run.entered_monotonic) * 1000)),
                    qualification='Helper elapsed through collection; persistence/reporting and native reasoning are not hard timing qualified'))
    _check_observation_deadline(dict(deadline=run.deadline_monotonic))
    _screening_projection(dict(version=1, payload=payload, freshness=dict(status='current', reason='Bounded producer result')))
    _check_observation_deadline(dict(deadline=run.deadline_monotonic))
    execution['observation_budget'].update(paths=budget['paths'], bytes=budget['bytes'])
    execution.update(produced=payload, produced_digest=_digest(payload), verification_budget=execution['observation_budget'])
    return payload


def _screening_checkout_inputs(root, budget):
    """Only fixed read-only Git observations; no authored command is executed."""
    _check_observation_deadline(budget)
    values = []
    for args in (('ls-files', '--cached', '--others', '--exclude-standard', '-z'),
                 ('ls-files', '--stage', '-z'), ('status', '--porcelain=v1', '-z', '--untracked-files=all')):
        code, raw = _git(root, *args, limit=max(0, MAX_TOTAL - budget['bytes']), deadline=budget['deadline'])
        budget['bytes'] += len(raw)
        _require(budget['bytes'] <= MAX_TOTAL and code == 0, 'Screening checkout inputs unavailable or over limit')
        values.append(raw)
    code, head = _git(root, 'rev-parse', '--verify', 'HEAD', limit=max(0, MAX_TOTAL - budget['bytes']), deadline=budget['deadline'])
    budget['bytes'] += len(head)
    _require(budget['bytes'] <= MAX_TOTAL, 'Screening aggregate Git read limit reached')
    _check_observation_deadline(budget)
    names = sorted(set(os.fsdecode(name) for name in values[0].split(b'\0') if name))
    _require(len(names) <= MAX_PATHS, 'Screening checkout inventory limit reached')
    return dict(repository_root=str(root), head=head.decode('ascii').strip() if code == 0 else None,
        index_digest=hashlib.sha256(values[1]).hexdigest(),
        dirty_digest=_digest([os.fsdecode(value) for value in values[2].split(b'\0') if value]),
        paths_digest=_digest(names)), names


def _screening_inputs_match(payload, budget):
    """Recheck only captured inputs; this never extends coverage or resumes collection."""
    try:
        for item in payload['inputs']['checkouts']:
            current, _ = _screening_checkout_inputs(Path(item['repository_root']), budget)
            _require(current == item, 'Screening checkout inventory changed')
        for item in payload['inputs']['files']:
            _check_observation_deadline(budget)
            budget['paths'] += 1
            _require(budget['paths'] <= MAX_PATHS and budget['bytes'] + item['bytes'] <= MAX_TOTAL,
                     'Screening verification allowance reached')
            path = Path(item['repository_root']) / item['path']
            data = _read_file(path, min(MAX_FILE, item['bytes']), expected_binding=item['binding'])
            budget['bytes'] += len(data)
            info = path.lstat()
            _require(dict(device=info.st_dev, inode=info.st_ino) == item['binding']
                and len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'],
                'Screening input fingerprint changed')
        _check_observation_deadline(budget)
        return True
    except (OSError, StartupError):
        return False


def _finalize_onboarding_screening(run, payload):
    from runtime import screening
    return screening._publish_onboarding_screening(run, payload)


def _compact_screening_coverage(coverage):
    """Roll up presentation only; every observed candidate remains in the immutable payload."""
    if len(coverage['groups']) <= 12:
        return deepcopy(coverage), None
    repositories = {}
    totals = dict(counted_files=0, oversized_count=0, inspected_files=0)
    exclusions, unknowns = {}, {}
    for group in coverage['groups']:
        root = group['repository_root']
        if root not in repositories:
            repositories[root] = dict(group, ancestor='.', counted_files=0, oversized_count=0, inspected_files=0,
                exclusions={}, unknown_coverage={}, representatives=[], impact='unknown', confidence='unknown')
        combined = repositories[root]
        for key in totals:
            combined[key] += group[key]
            totals[key] += group[key]
        for key in ('exclusions', 'unknown_coverage'):
            for reason, count in group[key].items():
                combined[key][reason] = combined[key].get(reason, 0) + count
                aggregate = exclusions if key == 'exclusions' else unknowns
                aggregate[reason] = aggregate.get(reason, 0) + count
        combined['representatives'].extend(deepcopy(group['representatives'][:3 - len(combined['representatives'])]))
    groups = list(repositories.values())
    return dict(groups=groups[:12], gaps=deepcopy(coverage['gaps']), partial=coverage['partial']), dict(
        folder_groups=len(coverage['groups']), repository_groups=len(groups), omitted_repository_groups=max(0, len(groups) - 12),
        totals=totals, exclusions=exclusions, unknown_coverage=unknowns,
        detail='All folder groups and coverage gaps remain in the private plan')


def _authenticated_screening_payload(plan, authentication):
    """Saved shape or generic accounting success never authenticates report coverage."""
    from runtime import state
    from runtime.startup import _canonical_plain
    try:
        if (authentication is None or not state._frozen_snapshot(authentication)
                or authentication.get('status') not in ('published', 'reuse')):
            return None
        verified = _canonical_plain(authentication)
        payload = verified['payload']
        _require(plan['plan_id'] == verified['plan_id']
            and _screening_directory_binding(Path(verified['plan_directory'])) == verified['directory_binding']
            and _digest(payload) == verified['projection_digest']
            and _digest(plan['screening']['payload']) == verified['projection_digest'],
            'Saved screening differs from authenticated report evidence')
        return payload
    except (OSError, StartupError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def _screening_report(plan, authentication=None):
    report = {key: deepcopy(plan[key]) for key in ('plan_id', 'revision', 'status', 'blockers', 'evidence_limits',
                                                  'review_digest', 'current_input_digest')}
    decisions = list(_project_decisions(plan).values()) if plan['schema_version'] == 2 else plan['decisions']
    report.update(next_decision=next((item for item in decisions if item['disposition'] in ('pending', 'proposed')), None),
                  exit_code=0 if plan['status'] == 'ready_for_review' else 2)
    payload = _authenticated_screening_payload(plan, authentication)
    if payload is not None:
        freshness = _screening_freshness(payload, plan['current_input_digest'])
        coverage, reporting = _compact_screening_coverage(payload['coverage'])
        report['screening'] = dict(status=('stale' if freshness['status'] == 'stale' else
            'partial' if payload['coverage']['partial'] else 'complete'),
            coverage=coverage, timing=deepcopy(payload['timing']), freshness=freshness)
        if reporting is not None:
            report['screening']['reporting'] = reporting
    else:
        report['screening'] = dict(status='unavailable', reason='Authenticated screening coverage is unavailable; allowance is not reset')
    return report


def _screen_onboarding_plan(plan_dir, expected_revision, *, inspection):
    from runtime import screening, review_storage as storage
    # Entry clock precedes every screening-specific path, Git, history or plan observation.
    with screening._onboarding_screening_run() as run:
        run.execution['last_plan'] = inspection
        result = dict(ok=False, status='unavailable')
        try:
            result = screening._begin_onboarding_screening(run, plan_dir, expected_revision)
            if result.get('status') == 'admitted':
                payload = _collect_onboarding_screening(run)
                result = _finalize_onboarding_screening(run, payload)
        except (*storage._ERRORS, RuntimeError):
            result = dict(ok=False, status='unavailable')
        # Reporting shares the original deadline; expiry uses the last already-observed plan.
        plan = run.execution['last_plan']
        try:
            _check_observation_deadline(dict(deadline=run.deadline_monotonic))
            latest = _load(Path(plan_dir))
            _require(latest is not None, 'Onboarding plan is unavailable')
            plan = latest
            _check_observation_deadline(dict(deadline=run.deadline_monotonic))
            authentication = (run.execution.get('report_authentication')
                if result.get('ok') and result.get('status') in ('published', 'reuse') else None)
            if authentication is not None and _digest(plan) != authentication['plan_digest']:
                authentication = None
            report = _screening_report(plan, authentication)
            _check_observation_deadline(dict(deadline=run.deadline_monotonic))
            return report
        except (*storage._ERRORS, RuntimeError):
            return _screening_report(plan)


def record_plan(plan_dir, request):
    from runtime import screening, review_storage as storage
    _bounded(request)
    _require('screening' not in request, 'Screening evidence is producer-owned')
    # Validate before taking the proposal lock; never acquire machine guard under a plan lock.
    plan = _load(Path(plan_dir))
    check = None
    if plan is not None:
        authentication = None
        if 'screening' in plan:
            with screening._onboarding_screening_run() as run:
                try:
                    result = screening._begin_onboarding_screening(run, plan_dir, plan['revision'], admit=False)
                    if result.get('ok') and result.get('status') == 'reuse':
                        authentication = run.execution.get('report_authentication')
                except (*storage._ERRORS, RuntimeError):
                    pass
        check = (plan['plan_id'], plan['revision'], screening._selection(plan), authentication)
    return _record_plan(plan_dir, request, screening_check=check)
