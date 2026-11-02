"""Read-only startup orientation shared by client adapters."""

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from types import MappingProxyType


MAX_INPUT = 128 * 1024
PROJECT_FILE = Path(".vibe-coding-workflow/project.json")


class StartupError(ValueError):
    """A safe, actionable startup diagnostic with no input contents."""


class RuntimeScopeError(StartupError):
    """Accepted runtime scope whose installed policy/client source is incompatible."""


@dataclass(frozen=True)
class StartupEvent:
    client: str
    session_id: str
    cwd: Path
    source: str
    model: str | None = None
    permission_mode: str | None = None


@dataclass(frozen=True)
class RuntimeSettings:
    schema_version: int
    state_dir: Path


@dataclass(frozen=True)
class ProjectScope:
    project_id: str
    repository_root: Path
    git_common_dir: Path
    policy_digest: str
    runtime: RuntimeSettings | None = None


def json_object(raw, *, limit=MAX_INPUT):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StartupError("Duplicate JSON field")
            result[key] = value
        return result

    def invalid_number(_):
        raise StartupError("Non-JSON number")

    if type(limit) is not int or limit < 1:
        raise StartupError("Invalid JSON byte limit")
    size = len(raw.encode('utf-8')) if isinstance(raw, str) else len(raw)
    if size > limit:
        raise StartupError("JSON exceeds 128 KiB" if limit == MAX_INPUT else "Internal JSON exceeds its byte limit")
    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    if not isinstance(result, dict):
        raise StartupError("Expected a JSON object")
    return result


def repository_root(cwd, *, budget=None):
    """Locate the nearest repository boundary without requiring working Git metadata."""
    cwd = cwd.resolve(strict=True)
    if not cwd.is_dir():
        raise StartupError("Startup cwd must be a directory")
    for candidate in (cwd, *cwd.parents):
        if budget is not None:
            _entry_budget_check(budget)
            budget["paths"] += 1
            _entry_budget_check(budget)
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def _check_observation_deadline(budget):
    if budget.get("deadline") is not None and time.monotonic() >= budget["deadline"]:
        raise StartupError("Canonical acquisition deadline exceeded")


def _entry_budget_check(budget, *, allow_full_bytes=False):
    _check_observation_deadline(budget)
    byte_limit = 8 * 1024 * 1024
    if not allow_full_bytes:
        byte_limit -= 1
    if (type(budget.get("paths")) is not int or type(budget.get("bytes")) is not int
            or not 0 <= budget["paths"] <= 4096
            or not 0 <= budget["bytes"] <= byte_limit):
        raise StartupError("Entry observation budget exhausted")


def _budgeted_source_read(path, budget):
    if budget is None:
        return path.read_bytes()
    try:
        _entry_budget_check(budget)
        budget["paths"] += 1
        _entry_budget_check(budget)
        limit = min(1024 * 1024, 8 * 1024 * 1024 - budget["bytes"])
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        budget["bytes"] += len(raw)
        if len(raw) > limit:
            raise StartupError("Installed source exceeds observation limit")
        _entry_budget_check(budget)
        return raw
    except (OSError, StartupError):
        budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
        raise


def read_git(root, *args, limit=1024 * 1024, discovery=False, timeout=5, with_stderr=False, deadline=None):
    """Bounded local Git observation shared with onboarding; inherit no selectors."""
    owner_deadline = deadline
    if owner_deadline is not None and time.monotonic() >= owner_deadline:
        raise subprocess.TimeoutExpired("read-only Git inventory", timeout)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    if discovery:
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
    if with_stderr:
        env.update(LC_ALL="C", LANG="C", LANGUAGE="C")
    process = subprocess.Popen(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root),
                                *args], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE if with_stderr else subprocess.DEVNULL)
    output, diagnostic = bytearray(), bytearray()
    deadline = min(owner_deadline, time.monotonic() + timeout) if owner_deadline is not None else time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            if with_stderr:
                selector.register(process.stderr, selectors.EVENT_READ, diagnostic)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                events = selector.select(max(0, remaining))
                if remaining <= 0 or not events:
                    raise subprocess.TimeoutExpired("read-only Git inventory", timeout)
                for key, _ in events:
                    raw = os.read(key.fd, min(65536, limit + 1 - len(output) - len(diagnostic)))
                    if not raw:
                        selector.unregister(key.fileobj)
                    else:
                        key.data.extend(raw)
                        if len(output) + len(diagnostic) > limit:
                            raise StartupError("Git inventory limit exceeded; narrow the checkout before planning")
        remaining = deadline - time.monotonic()
        if owner_deadline is not None and remaining <= 0:
            raise subprocess.TimeoutExpired("read-only Git inventory", timeout)
        code = process.wait(timeout=remaining if owner_deadline is not None else max(0.01, remaining))
        return (code, bytes(output), bytes(diagnostic)) if with_stderr else (code, bytes(output))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        if with_stderr:
            process.stderr.close()


def checkout(cwd, *, limit=1024 * 1024, deadline=None, budget=None):
    """Resolve checkout identity; never inherit a Git selector from the caller."""
    return _checkout_identity(cwd, limit=limit, deadline=deadline, budget=budget)


def _checkout_identity(cwd, *, include_git_dir=False, limit=1024 * 1024, total_limit=None, deadline=None, budget=None):
    """Read one identity window; the public default retains its two-field query."""
    boundary = repository_root(cwd, budget=budget)
    if boundary is None:
        return None
    if budget is not None:
        _entry_budget_check(budget)
        deadline = min(deadline, budget["deadline"]) if deadline is not None and budget.get("deadline") is not None else budget.get("deadline", deadline)
        limit = min(limit, 8 * 1024 * 1024 - budget["bytes"])
    args = ("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    read_limit = limit
    if include_git_dir:
        args += ("--absolute-git-dir",)
        read_limit += 1024 * 1024
        if budget is not None:
            read_limit = min(read_limit, 8 * 1024 * 1024 - budget["bytes"])
    if total_limit is not None:
        if type(total_limit) is not int or total_limit < 0:
            raise StartupError("Invalid owner identity byte limit")
        read_limit = min(read_limit, total_limit)
    try:
        # Consolidate the old 2s+5s caps only here; owner deadline and transport stay unchanged.
        code, raw = read_git(cwd, *args, limit=read_limit,
                             timeout=7 if include_git_dir else 2, deadline=deadline)
    except (StartupError, subprocess.SubprocessError, OSError):
        if budget is not None:
            budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
        raise
    if budget is not None:
        budget["bytes"] += len(raw)
        _entry_budget_check(budget, allow_full_bytes=include_git_dir)
    identity_raw, git_raw = raw, None
    if include_git_dir:
        parts = raw.split(b"\n", 2)
        if code or len(parts) != 3:
            raise StartupError("Cannot resolve checkout identity")
        identity_raw = b"\n".join(parts[:2]) + b"\n"
        git_raw = parts[2]
        if len(identity_raw) > limit or len(git_raw) > 1024 * 1024 or len(git_raw.splitlines()) != 1:
            if budget is not None:
                budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
            raise StartupError("Checkout identity component is over limit or malformed")
    lines = os.fsdecode(identity_raw).splitlines()
    if code or len(lines) != 2:
        raise StartupError("Cannot resolve checkout identity")
    root, common = (Path(line).resolve(strict=True) for line in lines)
    if root != boundary or not common.is_dir():
        raise StartupError("Checkout identity does not match the nearest repository")
    return (root, common, git_raw) if include_git_dir else (root, common)


def _machine_only_record_directory(root, *, budget=None):
    """Recognize existing machine family files, never project enrollment authority."""
    if os.name != "posix" or root != Path.home().resolve() or os.path.lexists(root / PROJECT_FILE):
        return False
    family = "workstreams-families.sqlite3"
    allowed = {family, family + "-journal", family + "-wal", family + "-shm"}
    descriptor = os.open(root / PROJECT_FILE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        names = set()
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if budget is not None:
                    _entry_budget_check(budget)
                    budget["paths"] += 1
                    _entry_budget_check(budget)
                if entry.name not in allowed or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                    return False
                names.add(entry.name)
        return family in names and not os.path.lexists(root / PROJECT_FILE)
    finally:
        os.close(descriptor)


def _read_record_snapshot(root, *, budget=None, allow_machine_only=False):
    """Reject links and special files before reading the bounded enrollment record."""
    if budget is not None:
        _entry_budget_check(budget)
        budget["paths"] += 1
        _entry_budget_check(budget)
    directory = root / PROJECT_FILE.parent
    if not os.path.lexists(directory):
        return None, None, None
    if directory.is_symlink() or not directory.is_dir():
        raise StartupError("Enrollment directory must be a local directory")
    if os.name != "posix":
        raise StartupError("Enrollment reads require a qualified POSIX client")
    path = root / PROJECT_FILE
    if not os.path.lexists(path):
        if allow_machine_only and _machine_only_record_directory(root, budget=budget):
            return None, None, None
        raise StartupError("Enrollment record is missing")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise StartupError("Enrollment record must be a regular file")
        limit = min(MAX_INPUT, 8 * 1024 * 1024 - budget["bytes"]) if budget is not None else MAX_INPUT
        raw = stream.read(limit + 1)
        if budget is not None:
            budget["bytes"] += len(raw)
            if len(raw) > limit:
                budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
                raise StartupError("Enrollment exceeds observation limit")
            _entry_budget_check(budget)
        info = os.fstat(stream.fileno())
        return json_object(raw), hashlib.sha256(raw).hexdigest(), (info.st_dev, info.st_ino)


def read_record(root, *, budget=None, allow_machine_only=False):
    return _read_record_snapshot(root, budget=budget, allow_machine_only=allow_machine_only)[0]


def policy_digest(package, *, budget=None):
    """Bind enrollment to the installed policy filenames and exact contents."""
    paths = sorted((package / "policies").glob("*.md"))
    if not paths or not (package / "policies/00-index.md").is_file():
        raise StartupError("Installed policy index is missing")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0" + _budgeted_source_read(path, budget) + b"\0")
    return digest.hexdigest()


def runtime_settings(record, package, common, *, budget=None):
    """Validate opt-in location without creating or opening runtime state."""
    if "runtime" not in record:
        return None
    settings = record["runtime"]
    if not isinstance(settings, dict) or type(settings.get("schema_version")) is not int or settings["schema_version"] != 2:
        raise StartupError("Unsupported runtime configuration schema")
    value = settings.get("state_dir")
    if not isinstance(value, str) or not value or len(value) > 4096 or any(not char.isprintable() for char in value):
        raise StartupError("Invalid runtime state directory")
    path = Path(value)
    if not path.is_absolute() or str(path.resolve()) != value:
        raise StartupError("Runtime state directory must be absolute and canonical")
    if path.is_relative_to(package.resolve()) or path.is_relative_to(common):
        raise StartupError("Runtime state directory must be outside repositories and plugin caches")
    for ancestor in (path, *path.parents):
        if budget is not None:
            _entry_budget_check(budget)
            budget["paths"] += 1
            _entry_budget_check(budget)
        if os.path.lexists(ancestor) and not ancestor.is_dir():
            raise StartupError("Runtime state directory ancestors must be directories")
        if os.path.lexists(ancestor / ".git") or (
                ancestor.name == "plugins" and ancestor.parent.name in {".codex", ".claude"}) or (
                ancestor.name == "cache" and ancestor.parent.name == "plugins"):
            raise StartupError("Runtime state directory must be outside repositories and plugin caches")
    existing = next(ancestor for ancestor in (path, *path.parents) if ancestor.is_dir())
    # Bare repositories have no .git marker; discover from the nearest existing directory.
    if budget is None:
        discovery = subprocess.run(
            ["git", "-c", "safe.bareRepository=all", "-C", str(existing),
             "rev-parse", "--absolute-git-dir"],
            env={**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
                 "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"},
            capture_output=True, text=True, timeout=2, check=False,
        )
        code = discovery.returncode
    else:
        _entry_budget_check(budget)
        try:
            code, raw, diagnostic = read_git(existing, "-c", "safe.bareRepository=all", "rev-parse",
                "--absolute-git-dir", discovery=True, with_stderr=True, timeout=2,
                deadline=budget.get("deadline"), limit=min(1024 * 1024, 8 * 1024 * 1024 - budget["bytes"]))
            budget["bytes"] += len(raw) + len(diagnostic)
            _entry_budget_check(budget)
        except (StartupError, subprocess.SubprocessError, OSError):
            budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
            raise
    if code == 0:
        raise StartupError("Runtime state directory must be outside repositories and plugin caches")
    return RuntimeSettings(2, path)


def resolve_project(event, package, *, budget=None) -> ProjectScope | None:
    """Resolve accepted checkout scope, preserving silence outside enrollment."""
    root = repository_root(event.cwd, budget=budget)
    if root is None:
        return None
    record = read_record(root, budget=budget)
    if record is None:
        return None
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise StartupError("Unsupported enrollment schema")
    if not isinstance(record.get("project_id"), str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,128}", record["project_id"]):
        raise StartupError("Invalid project identity")
    clients = record.get("clients")
    if not isinstance(clients, list) or not clients or any(
            not isinstance(client, str) or not client for client in clients):
        raise StartupError("Invalid enrolled clients")
    if event.client not in clients:
        return None
    root, common = checkout(root, budget=budget)
    if record.get("repository_root") != str(root) or record.get("git_common_dir") != str(common):
        raise StartupError("Enrollment does not cover this checkout")
    runtime = runtime_settings(record, package, common, budget=budget) if event.client == "codex" else None
    scope_error = RuntimeScopeError if runtime is not None else StartupError
    try:
        installed_policy = policy_digest(package, budget=budget)
    except (StartupError, OSError):
        raise scope_error("Installed policy sources are unavailable") from None
    if record.get("policy_digest") != installed_policy:
        raise scope_error("Installed policies differ from the accepted enrollment")
    client_file = package / "clients" / f"{event.client}.md"
    if not client_file.is_file():
        raise scope_error("Installed client instructions are missing")
    return ProjectScope(record["project_id"], root, common, record["policy_digest"],
                        runtime)


def orient(event, package):
    acquisition = acquire_project_entry(event, package)
    scope = _entry_scope(acquisition)
    if scope is None:
        return None
    client_file = package / "clients" / f"{event.client}.md"
    authority_path = scope.authority_path if isinstance(scope, AcceptedScope) else scope.repository_root / PROJECT_FILE
    context = (
        "Vibe Coding Workflow applies to this enrolled checkout. Read the client instructions at "
        f"{json.dumps(str(client_file))}, the policy index at "
        f"{json.dumps(str(package / 'policies/00-index.md'))}, and accepted project configuration at "
        f"{json.dumps(str(authority_path))}. Load only triggered policies and applicable "
        "project/directory instructions. Activate silently; explain useful task steps, blockers or "
        "decisions. Startup supplies orientation only; it does not establish enforcement or readiness."
    )
    if scope.runtime is not None and event.client == "codex":
        context += (f" Runtime state: {json.dumps(str(scope.runtime.state_dir / 'runtime.sqlite3'))}; "
                    f"native Codex session: {json.dumps(event.session_id)}. Read existing task status with task status; startup never changes bindings or pins.")
        if len(context.split()) > 150:
            raise StartupError("Runtime startup orientation exceeds the context limit")
    return context


# Pure schema-2 APIs are deliberately separate from production startup resolution.
CANONICAL_MAX_ROWS = 4096


@dataclass(frozen=True)
class CanonicalRecord:
    data: object


@dataclass(frozen=True)
class AcceptedScope:
    project_id: str
    repository_id: str
    checkout_id: str
    project_root: Path
    authority_path: Path
    authority_target: object
    authority_revision: int
    repository_root: Path
    primary_root: Path
    git_dir: Path
    git_common_dir: Path
    bindings: object
    client: str
    policy_digest: str
    runtime_digest: str
    runtime: RuntimeSettings
    accepted_identity: object
    accepted_scope_digest: str
    qualification_provenance: object


@dataclass(frozen=True)
class ScopeResolution:
    status: str
    scope: AcceptedScope | None
    reason: str


@dataclass(frozen=True)
class ScopePreview:
    eligible: bool
    reason: str


def _canonical_require(condition):
    if not condition:
        raise StartupError("Invalid canonical scope data")


def _canonical_fields(value, keys):
    _canonical_require(isinstance(value, dict) and set(value) == set(keys.split()))


def _canonical_text(value, limit=4096):
    _canonical_require(isinstance(value, str) and 0 < len(value) <= limit
                       and bool(value.strip()) and value.isprintable())
    return value


def _canonical_path(value, *, relative=False):
    _canonical_text(value)
    path = Path(value)
    _canonical_require(path.is_absolute() != relative and str(path) == value
                       and os.path.normpath(value) == value and ".." not in path.parts
                       and not value.startswith("//"))
    return path


def _canonical_hash(value):
    _canonical_require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value))


def _canonical_binding(value):
    _canonical_fields(value, "device inode")
    _canonical_require(all(type(v) is int and v >= 0 for v in value.values()))


def _canonical_ref(value):
    _canonical_fields(value, "reference sha256")
    _canonical_text(value["reference"])
    _canonical_hash(value["sha256"])


def _canonical_rows(value):
    _canonical_require(isinstance(value, list) and len(value) <= CANONICAL_MAX_ROWS)
    return value


def _canonical_unique(values):
    try:
        _canonical_require(len(values) == len(set(values)))
    except TypeError:
        raise StartupError("Invalid canonical scope data") from None


def _canonical_hook(value):
    _canonical_fields(value, "root binding source sha256")
    root, source = (_canonical_path(value[k]) for k in ("root", "source"))
    _canonical_require(source.is_relative_to(root))
    _canonical_binding(value["binding"])
    _canonical_hash(value["sha256"])


def _canonical_qualification(value):
    _canonical_fields(value, "client surface version configuration_digest hook_owner accepted_scope_digest receipt")
    for key in ("client", "surface", "version"):
        _canonical_text(value[key], 128)
    for key in ("configuration_digest", "accepted_scope_digest"):
        _canonical_hash(value[key])
    _canonical_hook(value["hook_owner"])
    _canonical_ref(value["receipt"])


def _canonical_freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _canonical_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_canonical_freeze(item) for item in value)
    return value


def _canonical_plain(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _canonical_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_plain(item) for item in value]
    return value


def _canonical_copy(value):
    try:
        raw = value if isinstance(value, (bytes, str)) else json.dumps(_canonical_plain(value), ensure_ascii=False)
        return json_object(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise StartupError("Invalid canonical scope JSON") from None


def _canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def decode_project_record(record) -> CanonicalRecord:
    """Validate and snapshot proposed canonical enrollment, without observing IO."""
    value = _canonical_copy(record)
    _canonical_fields(value, "schema_version revision project_id project_root kind project_binding membership shared_policy repositories")
    _canonical_require(type(value["schema_version"]) is int and value["schema_version"] == 2
                       and type(value["revision"]) is int and value["revision"] > 0)
    _canonical_require(isinstance(value["project_id"], str)
                       and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value["project_id"]))
    project = _canonical_path(value["project_root"])
    _canonical_binding(value["project_binding"])
    _canonical_require(value["kind"] in ("standalone", "umbrella"))
    membership = value["membership"]
    _canonical_fields(membership, "include exclude future_join authorization")
    _canonical_require(type(membership["future_join"]) is bool)
    _canonical_ref(membership["authorization"])
    for key in ("include", "exclude"):
        paths = [_canonical_path(p, relative=True) for p in _canonical_rows(membership[key])]
        _canonical_unique(paths)
        _canonical_require(not any(a != b and a.is_relative_to(b) for a in paths for b in paths))
    _canonical_require(bool(membership["include"]))
    shared = _canonical_rows(value["shared_policy"])
    for item in shared:
        _canonical_fields(item, "path sha256")
        _canonical_path(item["path"])
        _canonical_hash(item["sha256"])
    _canonical_unique([item["path"] for item in shared])
    shared_paths = {item["path"] for item in shared}
    ids, roots, common_roots, rows = [], [], [], len(shared)
    repositories = _canonical_rows(value["repositories"])
    _canonical_require(bool(repositories))
    for repo in repositories:
        _canonical_fields(repo, "repository_id primary_root git_common_dir binding membership checkouts")
        ids.append(_canonical_text(repo["repository_id"], 128))
        primary = _canonical_path(repo["primary_root"])
        common = _canonical_path(repo["git_common_dir"])
        roots.append(str(primary)); common_roots.append(str(common))
        _canonical_require(repo["membership"] in ("joined", "removed"))
        _canonical_fields(repo["binding"], "primary_root git_common_dir")
        for binding in repo["binding"].values():
            _canonical_binding(binding)
        checkouts = _canonical_rows(repo["checkouts"])
        _canonical_require(bool(checkouts))
        rows += 1 + len(checkouts)
        for selected in checkouts:
            _canonical_fields(selected, "checkout_id root git_dir binding clients")
            ids.append(_canonical_text(selected["checkout_id"], 128))
            root = _canonical_path(selected["root"])
            _canonical_path(selected["git_dir"])
            _canonical_fields(selected["binding"], "root git_dir")
            for binding in selected["binding"].values():
                _canonical_binding(binding)
            if value["kind"] == "umbrella":
                _canonical_require(not project.is_relative_to(root) and not project.is_relative_to(primary) and not project.is_relative_to(common))
            clients = _canonical_rows(selected["clients"])
            rows += len(clients)
            _canonical_unique([c.get("client") for c in clients if isinstance(c, dict)])
            for client in clients:
                _canonical_fields(client, "client configuration activation")
                _canonical_require(re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", _canonical_text(client["client"], 128)))
                config, activation = client["configuration"], client["activation"]
                _canonical_fields(activation, "status qualification accepted_scope_digest transaction_id evidence qualification_binding")
                _canonical_require(activation["status"] in ("pending", "configured", "active", "suspended")
                                   and activation["qualification"] in ("pending", "qualified"))
                for field in ("accepted_scope_digest",):
                    if activation[field] is not None:
                        _canonical_hash(activation[field])
                if activation["transaction_id"] is not None:
                    _canonical_text(activation["transaction_id"], 128)
                for proof in _canonical_rows(activation["evidence"]):
                    _canonical_ref(proof)
                if activation["qualification_binding"] is not None:
                    _canonical_qualification(activation["qualification_binding"])
                if config is None:
                    _canonical_require(activation == dict(status="pending", qualification="pending",
                        accepted_scope_digest=None, transaction_id=None, evidence=[], qualification_binding=None))
                    continue
                _canonical_fields(config, "policy_digest runtime_digest runtime shared_dependencies evidence")
                for field in ("policy_digest", "runtime_digest"):
                    _canonical_hash(config[field])
                runtime = config["runtime"]
                if runtime is not None:
                    _canonical_fields(runtime, "schema_version state_dir")
                    _canonical_require(type(runtime["schema_version"]) is int and runtime["schema_version"] == 3)
                    _canonical_path(runtime["state_dir"])
                dependencies = _canonical_rows(config["shared_dependencies"])
                _canonical_unique(dependencies)
                _canonical_require(all(p in shared_paths for p in dependencies))
                rows += len(dependencies)
                for proof in _canonical_rows(config["evidence"]):
                    _canonical_ref(proof)
                if activation["status"] == "active":
                    _canonical_require(activation["qualification"] == "qualified"
                        and activation["accepted_scope_digest"] is not None
                        and activation["transaction_id"] is not None and bool(activation["evidence"])
                        and activation["qualification_binding"] is not None)
    _canonical_require(rows <= CANONICAL_MAX_ROWS)
    _canonical_unique(ids); _canonical_unique(roots); _canonical_unique(common_roots)
    checkout_roots = [c["root"] for r in repositories for c in r["checkouts"]]
    _canonical_unique(checkout_roots)
    locations = {value["project_root"]: value["project_binding"]}
    for repo in repositories:
        entries = [(repo[k], b) for k, b in repo["binding"].items()]
        entries += [(selected[k], b) for selected in repo["checkouts"] for k, b in selected["binding"].items()]
        for path, binding in entries:
            _canonical_require(path not in locations or locations[path] == binding)
            locations[path] = binding
    if value["kind"] == "standalone":
        _canonical_require(len(repositories) == 1 and repositories[0]["primary_root"] == str(project))
    return CanonicalRecord(_canonical_freeze(value))


def canonical_scope_projection(record, *, repository_id, checkout_id, client):
    """Project proposed/historical configuration; never establish current eligibility."""
    _canonical_require(isinstance(record, CanonicalRecord))
    data = _canonical_plain(decode_project_record(_canonical_plain(record.data)).data)
    repo = next((r for r in data["repositories"] if r["repository_id"] == repository_id), None)
    _canonical_require(repo is not None)
    selected = next((c for c in repo["checkouts"] if c["checkout_id"] == checkout_id), None)
    _canonical_require(selected is not None)
    entry = next((c for c in selected["clients"] if c["client"] == client), None)
    _canonical_require(entry is not None)
    config = entry["configuration"]
    if config is None:
        return None
    membership = data["membership"]
    primary, project = Path(repo["primary_root"]), Path(data["project_root"])
    included = (repo["membership"] == "joined" and
        any(primary.is_relative_to(project / p) for p in membership["include"]) and
        not any(primary.is_relative_to(project / p) for p in membership["exclude"]))
    identity = dict(version=1, project_id=data["project_id"], repository_id=repo["repository_id"],
        checkout_id=selected["checkout_id"], project_root=data["project_root"], project_binding=data["project_binding"],
        primary_root=repo["primary_root"], repository_root=selected["root"], git_dir=selected["git_dir"],
        git_common_dir=repo["git_common_dir"], bindings=repo["binding"] | selected["binding"], client=client,
        membership=dict(included=included, authorization=membership["authorization"]),
        configuration={k: config[k] for k in ("policy_digest", "runtime_digest", "runtime")},
        shared_policy=sorted((s for s in data["shared_policy"] if s["path"] in config["shared_dependencies"]), key=lambda s: s["path"]))
    return _canonical_freeze(identity)


def _canonical_target(value):
    _canonical_fields(value, "path binding parents fingerprint")
    path = _canonical_path(value["path"])
    if value["binding"] is not None:
        _canonical_binding(value["binding"])
    _canonical_require(isinstance(value["parents"], dict) and 0 < len(value["parents"]) <= CANONICAL_MAX_ROWS)
    for name, binding in value["parents"].items():
        parent = _canonical_path(name)
        _canonical_require(parent != path and path.is_relative_to(parent))
        _canonical_binding(binding)
    fingerprint = value["fingerprint"]
    _canonical_require(isinstance(fingerprint, dict))
    if fingerprint.get("status") == "read":
        _canonical_fields(fingerprint, "status sha256 bytes")
        _canonical_hash(fingerprint["sha256"])
        _canonical_require(type(fingerprint["bytes"]) is int and fingerprint["bytes"] >= 0
                           and value["binding"] is not None and str(path.parent) in value["parents"])
    else:
        _canonical_fields(fingerprint, "status")
        _canonical_require(fingerprint["status"] == "absent" and value["binding"] is None)


def _canonical_provenance(value, kind):
    _canonical_fields(value, "kind reference sha256")
    _canonical_require(value["kind"] == kind)
    _canonical_ref({key: value[key] for key in ("reference", "sha256")})


def _canonical_runtime_location(value):
    if value is None:
        return
    _canonical_fields(value, "path existing_ancestors git_probe exclusions provenance")
    _canonical_path(value["path"])
    for ancestor in _canonical_rows(value["existing_ancestors"]):
        _canonical_fields(ancestor, "path binding kind")
        _canonical_path(ancestor["path"])
        _canonical_require(ancestor["kind"] in ("directory", "absent", "link", "other", "unreadable"))
        if ancestor["kind"] in ("absent", "unreadable"):
            _canonical_require(ancestor["binding"] is None)
        else:
            _canonical_binding(ancestor["binding"])
    _canonical_fields(value["git_probe"], "cwd result")
    _canonical_path(value["git_probe"]["cwd"])
    _canonical_require(value["git_probe"]["result"] in ("outside", "repository", "bare", "unknown"))
    _canonical_fields(value["exclusions"], "package_root software_roots git_roots plugin_cache_roots")
    _canonical_path(value["exclusions"]["package_root"])
    for key in ("software_roots", "git_roots", "plugin_cache_roots"):
        for path in _canonical_rows(value["exclusions"][key]):
            _canonical_path(path)
    if value["provenance"] is not None:
        _canonical_provenance(value["provenance"], "runtime-location-observer")


def _compare_canonical_scope(record, *, authority, observation, client, installed, preview=False):
    """Compare internal observer facts; external JSON never authenticates scope.

    Authority targets are supplied freshness provenance. The future IO wrapper
    must parse and fingerprint one bounded byte snapshot before supplying it.
    This pure comparison cannot authenticate receipt or observer provenance.
    """
    _canonical_require(isinstance(record, CanonicalRecord))
    # Revalidate even manually constructed wrappers; never retain caller-owned data.
    data = _canonical_plain(decode_project_record(_canonical_plain(record.data)).data)
    facts = _canonical_copy(dict(authority=authority, observation=observation, client=client, installed=installed))
    authority, observation, client, installed = (facts[k] for k in ("authority", "observation", "client", "installed"))
    _canonical_text(client, 128)
    _canonical_fields(authority, "path target")
    _canonical_path(authority["path"]); _canonical_target(authority["target"])
    _canonical_fields(installed, "policy_digest runtime_digest clients package_root")
    for field in ("policy_digest", "runtime_digest"):
        _canonical_hash(installed[field])
    _canonical_path(installed["package_root"])
    for name in _canonical_rows(installed["clients"]):
        _canonical_text(name, 128)
    _canonical_fields(observation, "project_root project_binding root primary_root git_dir git_common_dir binding membership conflicting_authorities dependencies qualification runtime_location")
    for field in ("project_root", "root", "primary_root", "git_dir", "git_common_dir"):
        _canonical_path(observation[field])
    _canonical_binding(observation["project_binding"])
    _canonical_fields(observation["binding"], "root primary_root git_dir git_common_dir")
    for binding in observation["binding"].values():
        _canonical_binding(binding)
    _canonical_require(observation["membership"] in ("included", "excluded", "outside", "unknown"))
    for path in _canonical_rows(observation["conflicting_authorities"]):
        _canonical_path(path)
    dependencies = _canonical_rows(observation["dependencies"])
    for dep in dependencies:
        _canonical_fields(dep, "path status target")
        _canonical_path(dep["path"])
        _canonical_require(dep["status"] in ("read", "absent", "unreadable", "unknown"))
        if dep["target"] is None:
            _canonical_require(dep["status"] in ("unreadable", "unknown"))
        else:
            _canonical_target(dep["target"])
    qualification = observation["qualification"]
    if qualification is not None:
        _canonical_fields(qualification, "current receipt binding provenance")
        _canonical_fields(qualification["current"], "client surface version configuration_digest hook_owner")
        for key in ("client", "surface", "version"):
            _canonical_text(qualification["current"][key], 128)
        _canonical_hash(qualification["current"]["configuration_digest"])
        _canonical_hook(qualification["current"]["hook_owner"])
        _canonical_ref(qualification["receipt"])
        if qualification["binding"] is not None:
            _canonical_qualification(qualification["binding"])
        if qualification["provenance"] is not None:
            _canonical_provenance(qualification["provenance"], "trusted-coordinator")
    location = observation["runtime_location"]
    _canonical_runtime_location(location)

    def result(status, reason):
        return ScopePreview(False, reason) if preview else ScopeResolution(status, None, reason)

    directories = {data["project_root"]: data["project_binding"]}
    for repo in data["repositories"]:
        directories.update({repo[k]: b for k, b in repo["binding"].items()})
        for selected in repo["checkouts"]:
            directories.update({selected[k]: b for k, b in selected["binding"].items()})
    observed_directories = {observation["project_root"]: observation["project_binding"]}
    observed_directories.update({observation[k]: b for k, b in observation["binding"].items()})
    for path, binding in observed_directories.items():
        if path in directories and directories[path] != binding:
            return result("conflict", "identity_changed")
        directories[path] = binding
    for path, binding in authority["target"]["parents"].items():
        if path in directories and directories[path] != binding:
            return result("conflict", "authority_conflict")
        directories[path] = binding

    expected_authority = str(Path(data["project_root"]) / PROJECT_FILE)
    if (observation["conflicting_authorities"] or authority["path"] != expected_authority
            or authority["target"]["path"] != expected_authority
            or authority["target"]["fingerprint"]["status"] not in (("read", "absent") if preview else ("read",))
            or authority["target"]["parents"].get(data["project_root"]) != data["project_binding"]):
        return result("conflict", "authority_conflict")
    if any(observation[k] != data[k] for k in ("project_root", "project_binding")):
        return result("conflict", "identity_changed")
    location_state = None
    if location is not None:
        chain = location["existing_ancestors"]
        exclusions = location["exclusions"]
        configured_paths = [Path(location["path"]), *Path(location["path"]).parents]
        known_roots = {data["project_root"], observation["primary_root"], observation["root"]}
        known_git = {observation["git_common_dir"], observation["git_dir"]}
        roots = [exclusions["package_root"], *exclusions["software_roots"],
                 *exclusions["git_roots"], *exclusions["plugin_cache_roots"],
                 *[r["primary_root"] for r in data["repositories"]],
                 *[r["git_common_dir"] for r in data["repositories"]],
                 *[c[k] for r in data["repositories"] for c in r["checkouts"] for k in ("root", "git_dir")]]
        unsafe = (exclusions["package_root"] != installed["package_root"]
            or any(Path(location["path"]).is_relative_to(Path(root)) for root in roots)
            or any(a["kind"] in ("link", "other") for a in chain)
            or location["git_probe"]["result"] in ("repository", "bare")
            or any((p.name == "plugins" and p.parent.name in (".codex", ".claude"))
                   or (p.name == "cache" and p.parent.name == "plugins") for p in configured_paths))
        if unsafe:
            return result("conflict", "runtime_location_unsafe")
        existing = next((a for a in chain if a["kind"] == "directory"), None)
        incomplete = ([a["path"] for a in chain] != [str(p) for p in configured_paths]
            or any(a["kind"] == "unreadable" for a in chain)
            or any(a["kind"] == "absent" for a in chain[next((i for i, a in enumerate(chain) if a["kind"] == "directory"), len(chain)):])
            or existing is None or location["git_probe"]["cwd"] != existing["path"]
            or location["git_probe"]["result"] == "unknown" or location["provenance"] is None
            or not known_roots <= set(exclusions["software_roots"])
            or not known_git <= set(exclusions["git_roots"]))
        if incomplete:
            location_state = "unobserved"
    else:
        location_state = "unobserved"

    repos = [r for r in data["repositories"] if r["primary_root"] == observation["primary_root"]]
    if not repos:
        for known in data["repositories"]:
            if (observation["git_common_dir"] == known["git_common_dir"]
                    or observation["binding"]["primary_root"] == known["binding"]["primary_root"]
                    or observation["binding"]["git_common_dir"] == known["binding"]["git_common_dir"]
                    or any(observation["binding"]["root"] == c["binding"]["root"] for c in known["checkouts"])):
                return result("conflict", "identity_changed")
        primary = Path(observation["primary_root"])
        project = Path(data["project_root"])
        membership = data["membership"]
        if (not any(primary.is_relative_to(project / p) for p in membership["include"])
                or any(primary.is_relative_to(project / p) for p in membership["exclude"])
                or observation["membership"] in ("excluded", "outside")):
            return result("outside", "excluded")
        if observation["membership"] == "unknown":
            return result("conflict", "identity_changed")
        return result("pending", "member_pending")
    repo = repos[0]
    if (repo["git_common_dir"] != observation["git_common_dir"]
            or any(repo["binding"][k] != observation["binding"][k] for k in repo["binding"])):
        return result("conflict", "identity_changed")
    selected = next((c for c in repo["checkouts"] if c["root"] == observation["root"]), None)
    if selected is None:
        if any(c["binding"]["root"] == observation["binding"]["root"]
                or c["git_dir"] == observation["git_dir"]
                or c["binding"]["git_dir"] == observation["binding"]["git_dir"]
                for c in repo["checkouts"]):
            return result("conflict", "identity_changed")
        primary = Path(repo["primary_root"])
        project = Path(data["project_root"])
        membership = data["membership"]
        if (repo["membership"] == "removed"
                or not any(primary.is_relative_to(project / p) for p in membership["include"])
                or any(primary.is_relative_to(project / p) for p in membership["exclude"])
                or observation["membership"] in ("excluded", "outside")):
            return result("outside", "excluded")
        if observation["membership"] == "unknown":
            return result("conflict", "identity_changed")
        if not membership["future_join"]:
            return result("outside", "not_enrolled")
        return result("pending", "member_pending")
    bindings = repo["binding"] | selected["binding"]
    if (repo["git_common_dir"] != observation["git_common_dir"] or selected["git_dir"] != observation["git_dir"]
            or bindings != observation["binding"]):
        return result("conflict", "identity_changed")

    config_entry = next((c for c in selected["clients"] if c["client"] == client), None)
    config = config_entry["configuration"] if config_entry is not None else None
    if (location is not None and config is not None and config["runtime"] is not None
            and location["path"] != config["runtime"]["state_dir"]):
        return result("conflict", "runtime_location_unsafe")

    primary = Path(repo["primary_root"])
    project = Path(data["project_root"])
    membership = data["membership"]
    included = any(primary.is_relative_to(project / p) for p in membership["include"])
    excluded = any(primary.is_relative_to(project / p) for p in membership["exclude"])
    if repo["membership"] == "removed" or excluded or not included or observation["membership"] in ("excluded", "outside"):
        return result("outside", "excluded")
    if observation["membership"] == "unknown":
        return result("conflict", "identity_changed")
    if config_entry is None:
        return result("pending", "member_pending")
    config, activation = config_entry["configuration"], config_entry["activation"]
    if config is None:
        return result("pending", "activation_pending")
    if (client not in installed["clients"] or any(config[k] != installed[k] for k in ("policy_digest", "runtime_digest"))):
        return result("suspended", "configuration_changed")
    shared = sorted((s for s in data["shared_policy"] if s["path"] in config["shared_dependencies"]), key=lambda s: s["path"])
    if (len(dependencies) != len(shared) or [d["path"] for d in dependencies] != [s["path"] for s in shared]
            or any(d["status"] != "read" for d in dependencies)):
        return result("suspended", "dependency_unobserved")
    for dependency in dependencies:
        for path, binding in dependency["target"]["parents"].items():
            if path in directories and directories[path] != binding:
                return result("suspended", "dependency_changed")
            directories[path] = binding
    if any(d["target"]["path"] != d["path"] or d["target"]["fingerprint"].get("sha256") != s["sha256"]
           for d, s in zip(dependencies, shared)):
        return result("suspended", "dependency_changed")
    identity = _canonical_plain(canonical_scope_projection(CanonicalRecord(_canonical_freeze(data)),
        repository_id=repo["repository_id"], checkout_id=selected["checkout_id"], client=client))
    digest = _canonical_digest(identity)
    if activation["accepted_scope_digest"] is not None and activation["accepted_scope_digest"] != digest:
        return result("suspended", "configuration_changed")
    if qualification is not None and qualification["binding"] is not None:
        binding = qualification["binding"]
        if (binding != activation["qualification_binding"] or qualification["receipt"] != binding["receipt"]
            or any(qualification["current"][k] != binding[k] for k in qualification["current"] if k != "version")
            or binding["accepted_scope_digest"] != digest or binding["client"] != client):
            return result("suspended", "qualification_changed")
    if activation["status"] == "suspended":
        return result("suspended", "activation_suspended")
    if (qualification is None or qualification["binding"] is None or qualification["provenance"] is None
            or activation["qualification"] != "qualified" or activation["qualification_binding"] is None):
        return result("configured", "qualification_unobserved")
    if config["runtime"] is None or location_state == "unobserved":
        return result("configured", "runtime_location_unobserved")
    if location["path"] != config["runtime"]["state_dir"]:
        return result("conflict", "runtime_location_unsafe")
    if activation["status"] == "configured":
        return result("configured", "activation_configured")
    if activation["status"] == "pending":
        return result("pending", "activation_pending")
    if preview:
        return ScopePreview(True, "eligible")
    scope = AcceptedScope(data["project_id"], repo["repository_id"], selected["checkout_id"], project,
        Path(authority["path"]), _canonical_freeze(authority["target"]), data["revision"], Path(selected["root"]),
        primary, Path(selected["git_dir"]), Path(repo["git_common_dir"]), _canonical_freeze(bindings), client,
        config["policy_digest"], config["runtime_digest"], RuntimeSettings(3, Path(config["runtime"]["state_dir"])),
        _canonical_freeze(identity), digest, _canonical_freeze(qualification["provenance"]))
    return ScopeResolution("active", scope, "active")


def resolve_canonical_scope(record, *, authority, observation, client, installed) -> ScopeResolution:
    """Compare a parsed current authority snapshot with internal observed facts."""
    return _compare_canonical_scope(record, authority=authority, observation=observation, client=client, installed=installed)


def preview_canonical_scope(proposed_record, *, authority, observation, client, installed) -> ScopePreview:
    """Compare approved proposed bytes against facts and an actual transaction preimage.

    Eligibility is neither a current-authority claim nor a task scope. The writer
    must recheck ownership, publish, reread the same bytes, then use the resolver.
    Only this comparison may use a completely observed absent authority preimage.
    """
    return _compare_canonical_scope(proposed_record, authority=authority, observation=observation,
                                    client=client, installed=installed, preview=True)


@dataclass(frozen=True)
class CanonicalAcquisition:
    resolution: ScopeResolution
    authority_path: Path | None


def _acquired_runtime_location(path, package, data, observed, budget):
    from runtime import onboarding as d3
    chain = []
    for ancestor in (path, *path.parents):
        _check_observation_deadline(budget)
        if budget["paths"] >= d3.MAX_PATHS:
            raise StartupError("Canonical observation limit exceeded")
        budget["paths"] += 1
        binding = None
        try:
            info = ancestor.lstat()
            binding = dict(device=info.st_dev, inode=info.st_ino)
            kind = "link" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "other"
        except FileNotFoundError:
            kind = "absent"
        except OSError:
            kind = "unreadable"
        chain.append(dict(path=str(ancestor), binding=binding, kind=kind))
    existing = next((a["path"] for a in chain if a["kind"] == "directory"), "/")
    result = "unknown"
    if not any(a["kind"] in ("link", "other", "unreadable") for a in chain):
        try:
            code, raw, diagnostic = read_git(existing, "-c", "safe.bareRepository=all", "rev-parse",
                                 "--absolute-git-dir", "--is-bare-repository", discovery=True, with_stderr=True,
                                 deadline=budget.get("deadline"), limit=min(d3.MAX_FILE, max(0, d3.MAX_TOTAL - budget["bytes"])))
            budget["bytes"] += len(raw) + len(diagnostic)
            lines = raw.splitlines()
            if code == 0 and len(lines) == 2 and lines[1] in (b"true", b"false"):
                result = "bare" if lines[1] == b"true" else "repository"
            elif code == 128 and not raw and diagnostic == b"fatal: not a git repository (or any of the parent directories): .git\n":
                result = "outside"
        except (OSError, subprocess.SubprocessError):
            pass
    _check_observation_deadline(budget)
    # A .git marker with failed discovery is unsafe, not proof that this is outside Git.
    if any(os.path.lexists(Path(a["path"]) / ".git") for a in chain):
        result = "repository"
    exclusions = dict(package_root=str(package),
        software_roots=sorted({data["project_root"], observed["root"], observed["primary_root"]}),
        git_roots=sorted({observed["git_dir"], observed["git_common_dir"]}),
        plugin_cache_roots=[str(p) for p in (path, *path.parents)
            if (p.name == "plugins" and p.parent.name in (".codex", ".claude"))
            or (p.name == "cache" and p.parent.name == "plugins")])
    facts = dict(path=str(path), existing_ancestors=chain, git_probe=dict(cwd=existing, result=result), exclusions=exclusions)
    return facts | dict(provenance=dict(kind="runtime-location-observer", reference="local-runtime-location",
                                        sha256=_canonical_digest(facts)))


def _acquired_dependency(location, budget):
    from runtime import onboarding as d3
    root = Path(location.anchor)
    fp = d3._inspection_fingerprint(root, str(location.relative_to(root)), budget)
    target = d3._physical_target(root, location, fp, budget) if fp["status"] in ("read", "absent") else None
    if fp["status"] == "read":
        raw = d3._read_file(location, min(d3.MAX_FILE, max(0, d3.MAX_TOTAL - budget["bytes"])),
                            expected_binding=target["binding"])
        budget["bytes"] += len(raw)
        fresh = d3._inspection_fingerprint(root, str(location.relative_to(root)), budget)
        if (hashlib.sha256(raw).hexdigest() != fp["sha256"] or fresh != fp
                or d3._physical_target(root, location, fresh, budget) != target):
            raise StartupError("Dependency changed while reading")
    return dict(path=str(location), status=fp["status"] if target else "unreadable", target=target)


@dataclass(frozen=True)
class CanonicalMembershipAcquisition:
    """Physical enrollment facts without client activation or evidence write authority."""
    status: str
    reason: str
    record: CanonicalRecord | None
    authority_path: Path | None
    authority_target: object
    observation: object


def _acquire_canonical_membership(cwd: Path, *, authority_path: Path | None, budget: dict) -> CanonicalMembershipAcquisition:
    """Observe one bounded authority candidate set and the actual selected checkout."""
    from runtime import onboarding as d3

    pointer = record = target = observation = None
    missing_checkout = False
    def result(status, reason):
        if missing_checkout and status == "conflict":
            reason = "checkout_unavailable"
        return CanonicalMembershipAcquisition(status, reason, record, pointer, target,
            _canonical_freeze(observation) if observation is not None else None)
    try:
        d3._no_links(cwd)
        root = repository_root(cwd, budget=budget)
        _check_observation_deadline(budget)
        if root is None:
            missing_checkout = True
            selected = cwd.resolve(strict=True)
            candidates = {selected, *selected.parents}
            observed = None
        else:
            _check_observation_deadline(budget)
            observed, error = d3._checkout_observation(str(root), budget)
            if error:
                return result("conflict", "identity_changed")
            selected = cwd.resolve(strict=True)
            candidates = {selected, *selected.parents, root, *root.parents,
                          Path(observed["primary_root"]), *Path(observed["primary_root"]).parents}
        if authority_path is not None:
            explicit = _canonical_path(str(authority_path))
            if explicit.name != PROJECT_FILE.name or explicit.parent.name != PROJECT_FILE.parent.name:
                raise StartupError("Invalid explicit authority route")
            d3._no_links(explicit)
            candidates.add(explicit.parent.parent)
        if budget["paths"] + len(candidates) > d3.MAX_PATHS:
            raise StartupError("Canonical candidate limit exceeded")
        budget["paths"] += len(candidates)
        required_roots = {selected, root}
        if observed is not None:
            required_roots.add(Path(observed["primary_root"]))
        if authority_path is not None:
            required_roots.add(explicit.parent.parent)
        present = []
        for candidate in sorted(candidates):
            _check_observation_deadline(budget)
            item = d3._authority_observation(candidate, budget, canonical_only=True)
            if item["error"] or (item["fingerprint"].get("status") == "absent"
                    and os.path.lexists(candidate / PROJECT_FILE.parent)
                    and not (candidate not in required_roots
                        and _machine_only_record_directory(candidate, budget=budget))):
                return result("conflict", "authority_conflict")
            if item["record"] is not None:
                present.append(item)
        if not present:
            return result("outside", "not_enrolled")
        if len(present) != 1:
            return result("conflict", "authority_conflict")
        item = present[0]
        if item["record"].get("schema_version") != 2:
            return result("conflict", "unsupported_enrollment_schema")
        record = decode_project_record(item["record"])
        data = _canonical_plain(record.data)
        _check_observation_deadline(budget)
        project = Path(data["project_root"])
        pointer = Path(item["path"])
        target = _canonical_freeze(item["target"])
        if pointer != project / PROJECT_FILE:
            return result("conflict", "authority_conflict")
        if observed is None:
            return result("conflict", "checkout_unavailable")
        primary = Path(observed["primary_root"])
        membership = data["membership"]
        included = primary.is_relative_to(project) and any(primary.is_relative_to(project / p) for p in membership["include"])
        excluded = any(primary.is_relative_to(project / p) for p in membership["exclude"])
        observation = {k: observed[k] for k in ("root", "primary_root", "git_dir", "git_common_dir", "binding")}
        observation.update(project_root=str(project), project_binding=d3._filesystem_binding(project),
            membership="excluded" if excluded else "included" if included else "outside",
            conflicting_authorities=[], dependencies=[], qualification=None, runtime_location=None)
        _check_observation_deadline(budget)
        return result("observed", "authority_observed")
    except (StartupError, OSError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        return result("conflict", "observation_unavailable")


def acquire_canonical_project(event, package, *, authority_path=None, qualifier=None, deadline=None, budget=None):
    """Read named canonical authority and current facts; leave public startup gated.

    qualifier is an internal trusted-owner, local read-only callback. Its verified
    durable qualification/current facts are never inferred from enrollment JSON.
    The trusted callback receives the shared monotonic deadline and must obey it.
    """
    from runtime.state import runtime_fingerprint

    pointer = None
    def result(status, reason):
        if status == "conflict" and budget is not None:
            budget["bytes"] = max(budget["bytes"], 8 * 1024 * 1024)
        return CanonicalAcquisition(ScopeResolution(status, None, reason), pointer)
    try:
        now = time.monotonic()
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise StartupError("Invalid acquisition deadline")
        # Reserve one second of the native hook's five-second budget for adaptation.
        cap = min(deadline, now + 4) if deadline is not None else now + 4
        if budget is None:
            budget = {"paths": 0, "bytes": 0, "deadline": cap}
        else:
            budget["deadline"] = min(budget.get("deadline", cap), cap)
        _entry_budget_check(budget)
        if qualifier is not None and not callable(qualifier):
            raise StartupError("Invalid internal qualification owner")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", event.client):
            raise StartupError("Invalid client identity")
        acquired = _acquire_canonical_membership(event.cwd, authority_path=authority_path, budget=budget)
        pointer = acquired.authority_path
        if acquired.reason == "checkout_unavailable":
            pointer = None
            return result("outside", "not_enrolled")
        if acquired.status != "observed":
            return result(acquired.status, acquired.reason)
        record = acquired.record
        data = _canonical_plain(record.data)
        observation = _canonical_plain(acquired.observation)
        observed = observation
        root, project = Path(observation["root"]), Path(data["project_root"])
        if data["kind"] == "standalone" and observed["primary_root"] != str(project):
            pointer = None
            return result("outside", "not_enrolled")
        selected = next((c for r in data["repositories"] if r["primary_root"] == observed["primary_root"]
                         for c in r["checkouts"] if c["root"] == str(root)), None)
        entry = next((e for e in selected["clients"] if e["client"] == event.client), None) if selected else None
        config = entry["configuration"] if entry else None
        if config:
            for name in sorted(config["shared_dependencies"]):
                observation["dependencies"].append(_acquired_dependency(Path(name), budget))
            if config["runtime"]:
                observation["runtime_location"] = _acquired_runtime_location(Path(config["runtime"]["state_dir"]),
                    Path(package).resolve(strict=True), data, observation, budget)
        package = Path(package).resolve(strict=True)
        if package != Path(__file__).resolve().parents[1]:
            return result("conflict", "installed_unavailable")
        _check_observation_deadline(budget)
        installed = dict(policy_digest=policy_digest(package, budget=budget), runtime_digest=runtime_fingerprint(budget=budget),
            clients=[event.client] if (package / "clients" / (event.client + ".md")).is_file() else [], package_root=str(package))
        facts = dict(authority=dict(path=str(pointer), target=_canonical_plain(acquired.authority_target)), observation=observation,
                     client=event.client, installed=installed)
        _check_observation_deadline(budget)
        resolution = resolve_canonical_scope(record, **facts)
        if qualifier is not None and resolution.status == "configured" and resolution.reason == "qualification_unobserved":
            observation["qualification"] = qualifier(record, _canonical_freeze(facts | {"deadline": budget["deadline"]}))
            _check_observation_deadline(budget)
            # The trusted callback may inspect facts, but it cannot normalize changed authority bytes.
            if _acquire_canonical_membership(event.cwd, authority_path=authority_path, budget=budget) != acquired:
                return result("conflict", "authority_conflict")
            # Reobserve selected inputs without adopting later facts into the qualified decision.
            for dependency in observation["dependencies"]:
                fresh = _acquired_dependency(Path(dependency["path"]), budget)
                if fresh != dependency:
                    return result("suspended", "dependency_changed" if fresh["status"] == "read" else "dependency_unobserved")
            if observation["runtime_location"] is not None:
                fresh = _acquired_runtime_location(Path(config["runtime"]["state_dir"]), package, data, observation, budget)
                if fresh != observation["runtime_location"]:
                    return result("conflict", "observation_unavailable")
            if (policy_digest(package, budget=budget) != installed["policy_digest"]
                    or runtime_fingerprint(budget=budget) != installed["runtime_digest"]
                    or ([event.client] if (package / "clients" / (event.client + ".md")).is_file() else []) != installed["clients"]
                    or str(package.resolve(strict=True)) != installed["package_root"]):
                return result("suspended", "configuration_changed")
            resolution = resolve_canonical_scope(record, **facts)
        _check_observation_deadline(budget)
        return CanonicalAcquisition(resolution, pointer)
    except (StartupError, OSError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        return result("conflict", "observation_unavailable")


def acquire_project_entry(event, package, *, qualifier=None, deadline=None, budget=None):
    """Internal actual-IO selector; no public wiring or native qualification."""
    cap = time.monotonic() + 4
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise StartupError('Invalid entry deadline')
        cap = min(cap, deadline)
    if budget is None:
        budget = dict(paths=0, bytes=0, deadline=cap)
    else:
        budget['deadline'] = min(budget.get('deadline', cap), cap)
    _entry_budget_check(budget)
    # Preserve legacy diagnostics and silence before canonical observation needs Git or state.
    root = repository_root(event.cwd, budget=budget)
    locations = {root, *root.parents} if root is not None else set()
    records = {path: read_record(path, budget=budget, allow_machine_only=path != root) for path in locations}
    canonical = any(record is not None and record.get('schema_version') == 2 for record in records.values())
    if not canonical:
        scope = resolve_project(event, package, budget=budget)
        primary = scope.git_common_dir.parent if scope is not None else None
        peer_locations = ({primary, *primary.parents} - locations) if primary is not None else set()
        if primary is not None:
            peer_locations.add(primary)
        peer_records = {path: read_record(path, budget=budget, allow_machine_only=path != primary)
                        for path in peer_locations}
        peers = peer_records.values()
        # A linked checkout without a local record still needs physical primary discovery.
        if ((root is None or scope is not None or records[root] is not None)
                and not any(record is not None and record.get('schema_version') == 2 for record in peers)):
            home = Path.home().resolve()
            home_was_absent = ((home in records and records[home] is None)
                               or (home in peer_records and peer_records[home] is None))
            if (home != root and home_was_absent
                    and read_record(home, budget=budget, allow_machine_only=home != primary) is not None):
                raise StartupError("Project authority changed during entry acquisition")
            if scope is not None and scope.runtime is not None:
                return CanonicalAcquisition(ScopeResolution('suspended', None,
                    'legacy_runtime_requires_original_pinned_package_and_approved_D4_enrollment'), root / PROJECT_FILE)
            return CanonicalAcquisition(ScopeResolution('active' if scope else 'outside', scope,
                'legacy_enrolled' if scope else 'not_enrolled'), root / PROJECT_FILE if scope else None)
    from runtime import onboarding as d3

    pointer = None
    try:
        cap = time.monotonic() + 4
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise StartupError("Invalid entry deadline")
            cap = min(cap, deadline)
        if budget is None:
            budget = dict(paths=0, bytes=0, deadline=cap)
        else:
            budget["deadline"] = min(budget.get("deadline", cap), cap)
        _entry_budget_check(budget)
        root = repository_root(event.cwd, budget=budget)
        _entry_budget_check(budget)
        if root is None:
            return CanonicalAcquisition(ScopeResolution("outside", None, "not_enrolled"), None)

        initial_root = _read_record_snapshot(root, budget=budget)
        root_record = initial_root[0]

        def record_candidates(locations, initial=False, primary=None, selected=None):
            if len(locations) + budget["paths"] > d3.MAX_PATHS:
                raise StartupError("Entry candidate limit exceeded")
            snapshots = []
            for candidate in sorted(locations):
                record, digest, binding = initial_root if initial and candidate == root else _read_record_snapshot(
                    candidate, budget=budget, allow_machine_only=candidate not in {root, primary, selected})
                if record is not None:
                    snapshots.append((candidate, record, digest, binding))
            return snapshots

        if (root_record is not None and type(root_record.get("schema_version")) is int
                and root_record["schema_version"] == 1 and isinstance(root_record.get("clients"), list)
                and event.client not in root_record["clients"]):
            # Recheck without Git/runtime observation for a stable unselected client.
            selected = event.cwd.resolve(strict=True)
            locations = {selected, *selected.parents, root, *root.parents}
            snapshots = record_candidates(locations, initial=True, selected=selected)
            parent = root / PROJECT_FILE.parent
            parent_binding = d3._filesystem_binding(parent)
            unselected = resolve_project(event, package, budget=budget)
            _entry_budget_check(budget)
            if (unselected is not None or record_candidates(locations, selected=selected) != snapshots
                    or d3._filesystem_binding(parent) != parent_binding
                    or repository_root(event.cwd, budget=budget) != root):
                raise StartupError("Unselected entry authority changed")
            _entry_budget_check(budget)
            return CanonicalAcquisition(ScopeResolution("outside", None, "not_enrolled"), None)

        def candidates(initial=False):
            observed, error = d3._checkout_observation(str(root), budget)
            if error:
                raise StartupError("Checkout observation unavailable")
            selected = event.cwd.resolve(strict=True)
            primary = Path(observed["primary_root"])
            locations = {selected, *selected.parents, root, *root.parents, primary, *primary.parents}
            return observed, record_candidates(locations, initial=initial, primary=primary, selected=selected)

        observed, snapshots = candidates(initial=True)
        canonical = [item for item in snapshots if type(item[1].get("schema_version")) is int and item[1]["schema_version"] == 2]
        unknown = [item for item in snapshots if type(item[1].get("schema_version")) is not int or item[1]["schema_version"] not in (1, 2)]
        legacy = next((item for item in snapshots if item[0] == root and item[1].get("schema_version") == 1), None)
        if unknown or len(canonical) > 1 or canonical and legacy:
            raise StartupError("Conflicting entry authority")
        if canonical:
            pointer = canonical[0][0] / PROJECT_FILE
            physical = d3._authority_observation(pointer.parent.parent, budget, canonical_only=True)
            if physical["error"]:
                raise StartupError("Canonical authority unavailable")
            result = acquire_canonical_project(event, package, authority_path=pointer, qualifier=qualifier, budget=budget)
        elif legacy:
            pointer = root / PROJECT_FILE
            scope = resolve_project(event, package, budget=budget)
            if scope is not None and scope.runtime is not None:
                return CanonicalAcquisition(ScopeResolution('suspended', None,
                    'legacy_runtime_requires_original_pinned_package_and_approved_D4_enrollment'), pointer)
            result = CanonicalAcquisition(ScopeResolution("active" if scope else "outside", scope,
                "legacy_enrolled" if scope else "not_enrolled"), pointer)
        else:
            result = CanonicalAcquisition(ScopeResolution("outside", None, "not_enrolled"), None)
        if candidates() != (observed, snapshots):
            raise StartupError("Entry authority changed")
        if canonical:
            fresh = d3._authority_observation(pointer.parent.parent, budget, canonical_only=True)
            if (fresh["error"] or fresh != physical or
                    isinstance(result.resolution.scope, AcceptedScope) and
                    fresh["target"] != _canonical_plain(result.resolution.scope.authority_target)):
                raise StartupError("Canonical physical authority changed")
        _entry_budget_check(budget)
        return result
    except (StartupError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        if budget is not None:
            budget["bytes"] = max(budget.get("bytes", 0), 8 * 1024 * 1024)
        return CanonicalAcquisition(ScopeResolution("conflict", None, "observation_unavailable"), pointer)


def _entry_scope(acquisition):
    """Unwrap one actual entry decision; unavailable current authority fails closed."""
    if not isinstance(acquisition, CanonicalAcquisition):
        raise RuntimeScopeError('Project authority acquisition is unavailable')
    resolution = acquisition.resolution
    if resolution.status not in {'active', 'outside'}:
        raise RuntimeScopeError('Project authority is unavailable or requires qualification: ' + resolution.reason)
    return resolution.scope
