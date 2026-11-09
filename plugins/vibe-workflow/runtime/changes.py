"""Bounded D4 transactions; trusted coordinators verify approval and effect claims.

A manifest declares an approved effect contract. It is not a command sandbox or
an authentication mechanism. Receipts prove local file observations only.
"""

from contextlib import ExitStack, closing, contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import subprocess
import threading
import time
import unicodedata
import uuid

from runtime import onboarding as d3
from runtime import state as task_state
from runtime import recovery_uses
from runtime.startup import (PROJECT_FILE, ProjectScope, StartupError, checkout,
                             policy_digest, runtime_settings, _canonical_freeze, _canonical_plain)

LOCK_ROOT = Path.home() / ".local/share/vcw-transactions"
MAX_JOURNAL = 8 * 1024 * 1024
LIMITATIONS = ["Approval, qualification and command effect claims require trusted coordinator verification; JSON does not authenticate intent.",
              "Declared command effects are not a sandbox; shared/global effects require manual resource serialization.",
              "Local file receipts and enrollment do not qualify client execution or future task readiness."]


class Pending(StartupError):
    """Safe intervention needed before the affected operation can continue."""


def _checkpoint(stage):
    """Crash-boundary injection point for disposable transaction tests."""


def _checkout(root):
    found = checkout(Path(root))
    d3._require(found is not None and found[0] == Path(root), "Selected checkout identity changed")
    code, raw = d3._git(found[0], "rev-parse", "--path-format=absolute", "--git-dir")
    d3._require(code == 0, "Cannot resolve worktree Git directory")
    git_dir = Path(os.fsdecode(raw).strip()).resolve(strict=True)
    d3._require(git_dir.is_dir(), "Worktree Git directory is unavailable")
    return found[0], git_dir, found[1]


def _evidence(value, many=False):
    if many:
        d3._require(isinstance(value, list) and len(value) <= d3.MAX_PATHS, "Invalid scoped evidence list")
        for item in value:
            d3._require(_evidence(item), "Evidence lists require non-null reference/hash objects")
        return bool(value)
    if value is None:
        return False
    d3._object(value, ("reference", "sha256"))
    d3._text(value["reference"])
    d3._hash(value["sha256"])
    return True


def _fp_schema(value):
    if value is not None:
        d3._object(value, ("sha256", "mode"))
        d3._hash(value["sha256"])
        d3._require(type(value["mode"]) is int and 0 <= value["mode"] <= 0o777,
                    "Invalid file permission mode")


def _target(value):
    d3._relative(value)
    d3._require(".git" not in Path(value).parts and value != str(PROJECT_FILE)
                and not Path(value).is_relative_to(PROJECT_FILE) and not PROJECT_FILE.is_relative_to(Path(value))
                and not any(part.startswith(".d4-") for part in Path(value).parts), "Reserved mutation target")
    return value


def _record(record, plan, current=True):
    d3._object(record, ("schema_version", "project_id", "clients", "repository_root", "git_common_dir", "policy_digest"), ("runtime",))
    d3._require(type(record["schema_version"]) is int and record["schema_version"] == 1,
                "Unsupported enrollment schema")
    d3._require(isinstance(record["project_id"], str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", record["project_id"]),
                "Invalid project identity")
    d3._require(record["repository_root"] == plan["identity"]["repository_root"]
                and record["git_common_dir"] == plan["identity"]["git_common_dir"], "Enrollment checkout changed")
    d3._require((not current or record["clients"] == plan["identity"]["clients"]) and bool(record["clients"])
                and all((d3.PACKAGE / "clients" / (client + ".md")).is_file() for client in record["clients"]),
                "Enrollment client selection changed")
    d3._hash(record["policy_digest"])
    d3._require(not current or record["policy_digest"] == policy_digest(d3.PACKAGE), "Enrollment policies are stale")
    legacy = (not current and isinstance(record.get("runtime"), dict) and
              type(record["runtime"].get("schema_version")) is int and record["runtime"]["schema_version"] == 1)
    inspected = dict(record, runtime=dict(record["runtime"], schema_version=2)) if legacy else record
    runtime = runtime_settings(inspected, d3.PACKAGE, Path(record["git_common_dir"]))
    if legacy:
        runtime = replace(runtime, schema_version=1)
    return ProjectScope(record["project_id"], Path(record["repository_root"]), Path(record["git_common_dir"]),
                        record["policy_digest"], runtime)


def _validate(request, plan):
    d3._bounded(request)
    d3._validate_proposal(plan)
    d3._object(request, ("schema_version", "manifest", "approval"), ("observations",))
    d3._require(type(request["schema_version"]) is int and request["schema_version"] == 1, "Unsupported D4 request schema")
    m, approval = request["manifest"], request["approval"]
    d3._object(m, ("schema_version", "transaction_id", "plan", "checkout", "pins", "authorization_reference", "approved_scope", "actions", "enrollment"))
    d3._require(type(m["schema_version"]) is int and m["schema_version"] == 1, "Unsupported D4 manifest schema")
    d3._require(isinstance(m["transaction_id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", m["transaction_id"]),
                "Invalid transaction identity")
    d3._object(m["plan"], ("plan_id", "revision", "review_digest", "current_input_digest"))
    d3._require(m["plan"] == {k: plan[k] for k in m["plan"]} and type(m["plan"]["revision"]) is int,
                "Frozen plan identity, revision or digest changed")
    d3._require(plan["review_digest"] == d3._review_digest(plan), "Frozen D3 plan digest is invalid")
    root, git_dir, common = _checkout(Path(plan["identity"]["repository_root"]))
    d3._object(m["checkout"], ("repository_root", "git_dir", "git_common_dir"))
    d3._require(m["checkout"] == dict(repository_root=str(root), git_dir=str(git_dir), git_common_dir=str(common)),
                "Approved checkout identity changed")
    d3._object(m["pins"], ("package_pins", "toolkit_commit", "sources_digest"))
    d3._require(m["pins"] == dict(package_pins=plan["package_pins"], toolkit_commit=plan["sources"]["declared"]["toolkit"]["commit"],
                    sources_digest=d3._digest(plan["sources"])) and plan["package_pins"] == d3._package_pins(),
                "Source or installed package pins changed")
    d3._text(m["authorization_reference"])
    d3._strings(m["approved_scope"])
    ids = [a["id"] for a in plan["actions"]]
    d3._require("enrollment" not in ids and set(m["approved_scope"]) <= set(ids) | {"enrollment"}, "Unknown approved scope")
    d3._object(approval, ("reference", "evidence", "manifest_digest"))
    d3._require(approval["reference"] == m["authorization_reference"] and approval["manifest_digest"] == d3._digest(m),
                "Approval does not bind this exact manifest")
    _evidence(approval["evidence"], many=True)
    d3._require(isinstance(m["actions"], list) and [a.get("id") for a in m["actions"] if isinstance(a, dict)] == ids,
                "Manifest must retain exact D3 action order and IDs")
    paths, groups, last = set(), set(), None
    for original, action in zip(plan["actions"], m["actions"]):
        d3._object(action, ("id", "group", "resources", "preconditions", "effects", "recovery", "execution"))
        d3._text(action["group"])
        if action["group"] != last:
            d3._require(action["group"] not in groups, "Dependency groups must be contiguous")
            groups.add(action["group"])
            last = action["group"]
        d3._strings(action["resources"])
        d3._require(action["recovery"] in ("file-preimages", "manual"), "Unknown recovery method")
        for key in ("preconditions", "effects"):
            d3._require(isinstance(action[key], list) and len(action[key]) <= d3.MAX_PATHS, "Invalid effect/precondition set")
        seen = set()
        for condition in action["preconditions"]:
            d3._object(condition, ("path", "fingerprint"))
            _target(condition["path"])
            _fp_schema(condition["fingerprint"])
            d3._require(condition["path"] not in seen, "Duplicate precondition")
            seen.add(condition["path"])
        for effect in action["effects"]:
            d3._object(effect, ("path", "before", "after"))
            if original["target"] == str(PROJECT_FILE) and original["kind"] == "write":
                d3._require(effect["path"] == str(PROJECT_FILE), "Enrollment proposal target changed")
            else:
                _target(effect["path"])
            _fp_schema(effect["before"])
            _fp_schema(effect["after"])
            d3._require(effect["path"] not in paths, "Overlapping approved effects")
            paths.add(effect["path"])
        if original["kind"] in ("write", "delete"):
            d3._require(len(action["effects"]) == 1 and action["execution"] is None, "File action needs exactly one effect")
            effect = action["effects"][0]
            d3._require(effect["path"] == original["target"]
                        and (effect["before"]["sha256"] if effect["before"] else "absent") == original["before"]
                        and (effect["after"]["sha256"] if effect["after"] else "absent") == original["after"],
                        "File effects differ from the frozen D3 proposal")
        elif original["kind"] == "command":
            execution = action["execution"]
            d3._object(execution, ("argv", "timeout_seconds", "qualification", "idempotency"))
            d3._require(execution["argv"] == original["text"] and type(execution["timeout_seconds"]) is int
                        and 1 <= execution["timeout_seconds"] <= 300, "Command differs from the approved bounded proposal")
            _evidence(execution["qualification"])
            _evidence(execution["idempotency"])
        else:
            d3._require(action["execution"] is None, "External action requires manual operation")
    e = m["enrollment"]
    d3._object(e, ("record", "before", "requires", "qualification"))
    _fp_schema(e["before"])
    _record(e["record"], plan)
    d3._strings(e["requires"])
    mandatory = {a for row in plan["coverage"] if row["mandatory"] and row["applicability"] == "applicable" for a in row["actions"]}
    by_id = {a["id"]: a for a in plan["actions"]}
    while True:
        expanded = mandatory | {dep for key in mandatory for dep in by_id[key]["dependencies"]}
        if expanded == mandatory:
            break
        mandatory = expanded
    d3._require(mandatory <= set(e["requires"]) <= set(ids), "Enrollment must follow mandatory adoption dependencies")
    enrollment_action = next((a for a in plan["actions"] if a["target"] == str(PROJECT_FILE)), None)
    if enrollment_action is not None:
        d3._require(enrollment_action["kind"] == "write" and enrollment_action["scope"] == "checkout"
                    and d3.read_request(enrollment_action["text"].encode()) == e["record"],
                    "Final enrollment differs from its exact D3 proposal")
    q = e["qualification"]
    d3._object(q, ("requirements", "security_required"))
    d3._require(type(q["security_required"]) is bool, "Invalid security review applicability")
    d3._strings(q["requirements"])
    required = {r["id"] for r in plan["coverage"] if r["mandatory"] and r["applicability"] == "applicable"}
    d3._require(required <= set(q["requirements"]) <= {r["id"] for r in plan["requirements"]}, "Qualification must cover applicable mandatory requirements")
    if "observations" in request:
        o = request["observations"]
        d3._object(o, ("manifest_digest", "input_digest", "qualification", "task_reconciliation", "prior_compatibility"), ("command_reconciliation",))
        d3._require(o["manifest_digest"] == d3._digest(m), "Observations bind a different manifest")
        d3._hash(o["input_digest"])
        _evidence(o["task_reconciliation"])
        _evidence(o["prior_compatibility"])
        command_ids = {a["id"] for a in plan["actions"] if a["kind"] == "command"}
        commands = o.get("command_reconciliation", [])
        d3._require(isinstance(commands, list) and len(commands) <= len(command_ids), "Invalid command lifecycle proof list")
        seen_commands = set()
        for command in commands:
            d3._object(command, ("id", "attempt_id", "stopped", "evidence"))
            d3._require(command["id"] in command_ids and command["id"] not in seen_commands and command["stopped"] is True,
                        "Command reconciliation needs exact stopped command scope")
            seen_commands.add(command["id"])
            d3._require(isinstance(command["attempt_id"], str) and re.fullmatch(r"[0-9a-f]{32}", command["attempt_id"]), "Invalid command attempt identity")
            d3._require(_evidence(command["evidence"], many=True), "Command lifecycle proof requires evidence")
        proof = o["qualification"]
        d3._object(proof, ("requirements", "general_review", "security_review"))
        _evidence(proof["general_review"], many=True)
        _evidence(proof["security_review"], many=True)
        d3._require(isinstance(proof["requirements"], list), "Invalid qualification evidence")
        seen = set()
        for row in proof["requirements"]:
            d3._object(row, ("id", "evidence"))
            d3._require(row["id"] in q["requirements"] and row["id"] not in seen, "Unknown/duplicate qualification requirement")
            seen.add(row["id"])
            _evidence(row["evidence"], many=True)
    return root, git_dir


@contextmanager
def _lock(path):
    """No waits: an owned descriptor serializes this resource until the operation ends."""
    descriptor = d3._open_path(path, os.O_RDWR | os.O_CREAT)
    try:
        d3._private(os.fstat(descriptor))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError("D4 transaction busy; retry the same bound request") from None
        yield descriptor
    finally:
        os.close(descriptor)


def _worktree_lock(git_dir):
    root = d3._plan_path(LOCK_ROOT)
    location = root / (hashlib.sha256(str(git_dir).encode()).hexdigest() + ".lock")
    if not location.exists():
        try:
            fd = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    return _lock(location)


_authority_context = threading.local()


def _held_authority_guards(directories):
    """Observe this thread's still-held locks, never a caller's asserted lock set."""
    held={}
    for observation in getattr(_authority_context,'held',()):
        if observation['active']:
            held.update(observation['bindings'])
    for directory in directories:
        directory=Path(directory)
        if directory not in held or not directory.exists():return False
        info=directory.lstat()
        if held[directory]!=(info.st_dev,info.st_ino):return False
    return True


def _authority_locations(directories):
    keys = set()
    for directory in directories:
        directory = Path(directory)
        d3._require(directory.is_absolute() and directory.resolve() == directory, "Unsafe state authority identity")
        keys.add("path:" + unicodedata.normalize("NFC", str(directory)).casefold())
        if directory.exists():
            task_state._private(directory, True)
            info = directory.stat()
            keys.add("inode:" + str(info.st_dev) + ":" + str(info.st_ino))
    if not keys:
        return []
    try:
        root = d3._plan_path(LOCK_ROOT)
    except FileExistsError:
        # A concurrent first user may create the root; re-run every ownership/path check.
        root = d3._plan_path(LOCK_ROOT)
    return [root / ("authority-" + hashlib.sha256(key.encode()).hexdigest()) for key in sorted(keys)]


def _authority_owner(location):
    try:
        owner = d3.read_request(d3._read_file(Path(str(location) + ".json"), d3.MAX_REQUEST, private=True))
    except FileNotFoundError:
        return None
    d3._object(owner, ("schema_version", "plan_dir", "transaction_id", "manifest_digest"))
    d3._require(type(owner["schema_version"]) is int and owner["schema_version"] == 1 and
                Path(owner["plan_dir"]).is_absolute() and str(Path(owner["plan_dir"]).resolve()) == owner["plan_dir"],
                "Unsafe authority operation owner")
    d3._text(owner["transaction_id"])
    d3._hash(owner["manifest_digest"])
    return owner


@contextmanager
def _authority_guards(directories, owner=None, *, observe=False):
    """Shared approved-authority exclusion; never locks or writes the old database."""
    directories=tuple(sorted(set(map(Path,directories))))
    locations = _authority_locations(directories)
    with ExitStack() as locks:
        for location in locations:
            lock = Path(str(location) + ".lock")
            try:
                descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            except FileExistsError:
                pass
            locks.enter_context(_lock(lock))
            held = _authority_owner(location)
            if held is not None and held != owner and not observe:
                raise Pending("Shared state authority has an active or uncertain D4 operation; resume its bound journal")
        observation=dict(active=True,bindings={directory:(directory.lstat().st_dev,directory.lstat().st_ino)
            for directory in directories if directory.exists()})
        previous=getattr(_authority_context,'held',())
        _authority_context.held=(*previous,observation)
        try:yield locations
        finally:
            observation['active']=False
            _authority_context.held=previous


@contextmanager
def _authority_operation(path, root, journal, observation):
    records = _writer_records(path, root, journal)
    scopes = [_record(record, journal["plan"], current=False) for record in records]
    directories = {scope.runtime.state_dir for scope in scopes if scope.runtime is not None}
    owner = dict(schema_version=1, plan_dir=str(path), transaction_id=journal["transaction_id"],
                 manifest_digest=journal["manifest_digest"])
    with _authority_guards(directories, owner) as locations:
        d3._require(_writer_records(path, root, journal) == records, "Enrollment authorities changed while acquiring shared guards")
        if journal["state"] != "rolled_back":
            for record, scope in zip(records, scopes):
                _writers(journal["plan"], record, observation, scope=scope)
        for location in locations:
            _atomic(location.parent, location.name + ".json", json.dumps(owner, sort_keys=True).encode())
    previous = getattr(_authority_context, "directories", None)
    _authority_context.directories = directories
    complete = False
    try:
        yield
        complete = True
    finally:
        _authority_context.directories = previous
        unsettled = any(receipt.get("command") and not receipt["command"]["stopped"]
                        for receipt in journal["receipts"].values())
        if complete and not unsettled:
            with _authority_guards(directories, owner):
                for location in locations:
                    d3._require(_authority_owner(location) == owner, "D4 authority owner changed; preserve recovery evidence")
                    os.unlink(Path(str(location) + ".json"))
                if locations:
                    descriptor = d3._open_path(locations[0].parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)


def _owner_path(git_dir):
    return d3._plan_path(LOCK_ROOT) / (hashlib.sha256(str(git_dir).encode()).hexdigest() + ".command.json")


def _command_owner(git_dir):
    try:
        owner = d3.read_request(d3._read_file(_owner_path(git_dir), d3.MAX_REQUEST, private=True))
    except FileNotFoundError:
        return None
    d3._object(owner, ("schema_version", "plan_dir", "transaction_id", "id", "attempt_id"))
    d3._require(owner["schema_version"] == 1 and isinstance(owner["plan_dir"], str)
                and Path(owner["plan_dir"]).is_absolute() and str(Path(owner["plan_dir"]).resolve()) == owner["plan_dir"], "Unsafe command ownership marker")
    for field in ("transaction_id", "id", "attempt_id"):
        d3._text(owner[field])
    return owner


def _publish_command_owner(path, journal, action, attempt_id):
    location = _owner_path(journal["manifest"]["checkout"]["git_dir"])
    d3._require(_command_owner(journal["manifest"]["checkout"]["git_dir"]) is None, "Another unresolved command owns this checkout")
    owner = dict(schema_version=1, plan_dir=str(path), transaction_id=journal["transaction_id"], id=action["id"], attempt_id=attempt_id)
    _atomic(location.parent, location.name, json.dumps(owner, sort_keys=True).encode())


def _reconcile_command_lifecycles(path, journal):
    git_dir = journal["manifest"]["checkout"]["git_dir"]
    owner = _command_owner(git_dir)
    observation = _current_observation(journal)
    proofs = observation.get("command_reconciliation", []) if observation else []
    known_owner = owner is None
    for action_id, receipt in journal["receipts"].items():
        command = receipt.get("command")
        if not command:
            continue
        matching_owner = owner and owner["id"] == action_id and owner["attempt_id"] == command["attempt_id"]
        known_owner |= bool(matching_owner)
        if not command["stopped"]:
            proof = next((p for p in proofs if p["id"] == action_id and p["attempt_id"] == command["attempt_id"]), None)
            if not proof:
                raise Pending("Command lifecycle remains uncertain; verify this exact attempt and all writable descendants stopped before recovery")
            d3._require(proof["stopped"] is True and _evidence(proof["evidence"], many=True), "Invalid stopped command evidence")
            command["stopped"] = True
            _save(path, journal)
        if matching_owner:
            location = _owner_path(git_dir)
            os.unlink(location)
            descriptor = d3._open_path(location.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    if not known_owner:
        raise Pending("Unresolved checkout command marker has no matching receipt; preserve it for original-plan/manual recovery")


def _atomic(directory, name, raw, mode=0o600, target=False, guard=None, temporary=None, prepared=None):
    """Replace one bounded regular file and durably publish its directory entry."""
    d3._require(len(raw) <= MAX_JOURNAL, "D4 artifact size limit exceeded")
    fd = d3._open_path(directory, os.O_RDONLY | os.O_DIRECTORY)
    temporary = temporary or ".d4-" + uuid.uuid4().hex
    created = False
    created_identity = None
    try:
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
        created = True
        created_info = os.fstat(output)
        created_identity = (created_info.st_dev,created_info.st_ino)
        with os.fdopen(output, "wb") as stream:
            stream.write(raw)
            if target:
                _checkpoint("temp_write")
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            if prepared is not None:
                os.fsync(fd)
                prepared(os.fstat(stream.fileno()))
            if target:
                _checkpoint("temp_fsync")
        if guard is not None:
            guard()
        current_parent = d3._open_path(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            d3._require(os.fstat(current_parent).st_ino == os.fstat(fd).st_ino and os.fstat(current_parent).st_dev == os.fstat(fd).st_dev,
                        "Target directory changed during replacement")
        finally:
            os.close(current_parent)
        if prepared is not None:
            observed = os.stat(temporary,dir_fd=fd,follow_symlinks=False)
            if (observed.st_dev,observed.st_ino) != created_identity or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise Pending("Prepared temporary physical identity changed; preserve it")
            descriptor=os.open(temporary,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            with os.fdopen(descriptor,"rb") as stream:
                current=os.fstat(stream.fileno())
                if ((current.st_dev,current.st_ino) != created_identity or stream.read(len(raw)+1) != raw or stat.S_IMODE(current.st_mode) != mode):
                    raise Pending("Prepared temporary bytes or mode changed; preserve it")
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        if target:
            _checkpoint("rename")
        os.fsync(fd)
        if target:
            _checkpoint("parent_fsync")
    finally:
        if created:
            try:
                safe = True
                if prepared is not None:
                    info=os.stat(temporary,dir_fd=fd,follow_symlinks=False)
                    safe = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and (info.st_dev,info.st_ino) == created_identity
                    if safe:
                        descriptor=os.open(temporary,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                        with os.fdopen(descriptor,"rb") as stream:
                            safe=raw.startswith(stream.read(len(raw)+1))
                if safe: os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.close(fd)


def _journal(path, *, file_only=False):
    try:
        raw = d3._read_file(path / "transaction.json", MAX_JOURNAL, private=True)
    except FileNotFoundError:
        return None
    result = d3.read_request(raw, limit=MAX_JOURNAL)
    d3._object(result, ("schema_version", "transaction_id", "manifest_digest", "manifest", "approval", "plan", "state", "receipts", "enrollment", "blocked", "directories", "observations"), ("birth", "setup_receipts", "qualification_scope", "native_observations") if result.get("schema_version") == 2 else ())
    if result.get("schema_version") == 2:
        return _scoped_journal_shape(result, file_only=file_only)
    d3._require(result["schema_version"] == 1 and result["state"] in
                ("prepared", "applying", "interrupted", "applied", "blocked", "rollback_partial", "rolled_back"), "Invalid D4 journal")
    return result


def _save(path, journal):
    with _lock(path / ".d4-journal.lock"):
        if (path / "transaction.json").exists():
            d3._private((path / "transaction.json").lstat())
        _atomic(path, "transaction.json", json.dumps(journal, sort_keys=True, ensure_ascii=True).encode() + b"\n")


def _load_journal(path, *, file_only=False):
    lock = path / ".d4-journal.lock"
    if not lock.exists():
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    with _lock(lock):
        return _journal(path, file_only=file_only)


def _fingerprint(root, relative):
    path = root / relative
    try:
        fd = d3._open_path(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > d3.MAX_FILE or info.st_mode & 0o7000:
            raise Pending("Target must be a bounded regular file without links or special permissions")
        raw = stream.read(d3.MAX_FILE + 1)
        d3._require(len(raw) <= d3.MAX_FILE, "Target file size limit exceeded")
        return {"sha256": hashlib.sha256(raw).hexdigest(), "mode": stat.S_IMODE(info.st_mode)}


def _backup(path, root, relative, expected, name):
    actual = _fingerprint(root, relative)
    if actual != expected:
        raise Pending("Current file differs from the approved preimage")
    if expected is None:
        return None
    raw = d3._read_file(root / relative, d3.MAX_FILE)
    d3._require(hashlib.sha256(raw).hexdigest() == expected["sha256"], "File changed while backing up")
    backup = path / name
    if os.path.lexists(backup):
        d3._require(hashlib.sha256(d3._read_file(backup, d3.MAX_FILE, private=True)).hexdigest() == expected["sha256"],
                    "Existing recovery backup differs from the approved original")
    else:
        _atomic(path, name, raw)
    _checkpoint("backup")
    return name


def _replace(root, relative, raw, fingerprint, expected, temporary=None):
    if _fingerprint(root, relative) != expected:
        raise Pending("Target changed outside this transaction; preserve it for manual reconciliation")
    path = root / relative
    if fingerprint is None:
        if expected is not None:
            directory = d3._open_path(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.unlink(path.name, dir_fd=directory)
                _checkpoint("rename")
                os.fsync(directory)
                _checkpoint("parent_fsync")
            finally:
                os.close(directory)
    else:
        def unchanged():
            if _fingerprint(root, relative) != expected:
                raise Pending("Target changed while preparing replacement; preserve the external edit")
        _atomic(path.parent, path.name, raw, fingerprint["mode"], target=True, guard=unchanged, temporary=temporary)
    if _fingerprint(root, relative) != fingerprint:
        raise Pending("Observed operation result differs from the intended postimage")


def _scoped_replace(path, root, relative, raw, before, after, receipt, journal):
    """Keep physical ownership durable across schema-2 replacement crash boundaries."""
    location = root / relative
    def binding():
        if not os.path.lexists(location):
            return None
        d3._no_links(location)
        info = location.stat()
        return dict(device=info.st_dev, inode=info.st_ino)
    actual = _fingerprint(root, relative)
    if actual is None and after is None and (before is None or receipt.get("delete_intent") is True):
        receipt["result_binding"] = None
        _save(path,journal)
        return
    if actual == after and after is not None:
        owned = receipt.get("result_binding") or receipt["prepared_binding"]
        if owned is None and before == after and receipt.get("before_binding") == binding():
            owned = receipt["before_binding"]
        if owned is None or binding() != owned:
            raise Pending("Scoped output physical ownership is ambiguous; preserve the current file")
        receipt["result_binding"] = owned
        _save(path, journal)
        return
    if actual != before:
        raise Pending("Scoped target changed outside this transaction")
    if "before_binding" in receipt and binding() != receipt["before_binding"]:
        raise Pending("Scoped preimage physical identity changed")
    expected_binding = binding()
    temporary = location.parent / receipt["temporary"]
    if os.path.lexists(temporary):
        info = temporary.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                dict(device=info.st_dev, inode=info.st_ino) != receipt["prepared_binding"]):
            raise Pending("Scoped temporary ownership is unverified; preserve it")
        directory = d3._open_path(temporary.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.unlink(temporary.name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
    def unchanged():
        if _fingerprint(root, relative) != before or binding() != expected_binding:
            raise Pending("Scoped target physical identity changed before replacement")
    def prepared(info):
        receipt["prepared_binding"] = dict(device=info.st_dev, inode=info.st_ino)
        _save(path, journal)
        _checkpoint("scoped_temp_recorded")
    if after is None:
        unchanged()
        receipt["delete_intent"] = True
        _save(path,journal)
        _replace(root, relative, raw, after, before, receipt["temporary"])
        receipt["result_binding"] = None
    else:
        _atomic(location.parent, location.name, raw, after["mode"], target=True,
                guard=unchanged, temporary=receipt["temporary"], prepared=prepared)
        if _fingerprint(root, relative) != after or binding() != receipt["prepared_binding"]:
            raise Pending("Scoped output differs from its prepared physical identity")
        receipt["result_binding"] = receipt["prepared_binding"]
    _save(path, journal)


def _restore(path, root, relative, receipt, before, after):
    actual = _fingerprint(root, relative)
    if actual == before:
        return
    if actual != after:
        raise Pending("Unrecognized current file; preserve user changes and reconcile manually")
    raw = b"" if before is None else d3._read_file(path / receipt["backup"], d3.MAX_FILE, private=True)
    if before is not None:
        d3._require(hashlib.sha256(raw).hexdigest() == before["sha256"], "Recovery backup is corrupt")
    _replace(root, relative, raw, before, after, receipt["temporary"])


def _owned(journal):
    return {effect["path"]: (effect, receipt) for action in journal["manifest"]["actions"]
            for effect, receipt in zip(action["effects"], journal["receipts"].get(action["id"], {}).get("files", []))}


def _recovery_files(path, root, journal, cleanup=True):
    manifest = journal["manifest"]
    d3._require(journal["transaction_id"] == manifest["transaction_id"] and isinstance(journal["receipts"], dict)
                and set(journal["receipts"]) <= {a["id"] for a in manifest["actions"]}, "Invalid transaction receipt identities")
    d3._require(isinstance(journal["directories"], dict) and len(journal["directories"]) <= d3.MAX_PATHS, "Invalid parent directory receipts")
    allowed_parents = {str(parent) for action in manifest["actions"] for effect in action["effects"]
                       for parent in Path(effect["path"]).parents if str(parent) != "."} | {str(PROJECT_FILE.parent)}
    for name, stored in journal["directories"].items():
        d3._require(name in allowed_parents, "Unknown parent directory recovery scope")
        d3._object(stored, ("status", "device", "inode"))
        d3._require(stored["status"] in ("prepared", "owned", "retained", "removed")
                    and all(value is None or type(value) is int for value in (stored["device"], stored["inode"])), "Invalid parent directory ownership")
    temporary_files = []
    for index, action in enumerate(manifest["actions"]):
        receipt = journal["receipts"].get(action["id"])
        if receipt is None:
            continue
        d3._object(receipt, ("status", "files", "returncode"), ("command",))
        d3._require(receipt["status"] in ("prepared", "started", "completed", "rolled_back")
                    and (receipt["returncode"] is None or type(receipt["returncode"]) is int), "Invalid action receipt")
        original = journal["plan"]["actions"][index]
        if "command" in receipt:
            command = receipt["command"]
            d3._object(command, ("attempt_id", "stopped"))
            d3._require(original["kind"] == "command" and isinstance(command["attempt_id"], str)
                        and re.fullmatch(r"[0-9a-f]{32}", command["attempt_id"]) and type(command["stopped"]) is bool,
                        "Invalid durable command lifecycle")
        d3._require(original["kind"] != "command" or receipt["status"] == "prepared" or "command" in receipt,
                    "Command receipt lacks recoverable attempt identity")
        enrollment_action = original["target"] == str(PROJECT_FILE)
        d3._require(isinstance(receipt["files"], list) and len(receipt["files"]) == (0 if enrollment_action else len(action["effects"])),
                    "Incomplete durable backup receipt")
        for number, (effect, stored) in enumerate(zip(action["effects"], receipt["files"])):
            d3._object(stored, ("backup", "temporary"))
            expected_backup = "backup-" + str(index) + "-" + str(number) if effect["before"] else None
            d3._require(stored["backup"] == expected_backup and stored["temporary"] ==
                        ".d4-" + journal["transaction_id"] + "-" + str(index) + "-" + str(number), "Invalid recovery material reference")
            candidates = []
            if expected_backup:
                candidates.append(d3._read_file(path / expected_backup, d3.MAX_FILE, private=True))
                d3._require(hashlib.sha256(candidates[-1]).hexdigest() == effect["before"]["sha256"], "Recovery backup is corrupt")
            if original["kind"] == "write":
                candidates.append(original["text"].encode())
            temporary_files.append((effect["path"], stored["temporary"], candidates))
    receipt = journal["enrollment"]
    if receipt:
        d3._object(receipt, ("before", "after", "backup", "status", "temporary"))
        d3._require(receipt["before"] == manifest["enrollment"]["before"] and receipt["status"] in
                    ("prepared", "inactive", "activating", "active", "restoring", "restored")
                    and receipt["backup"] == ("enrollment.backup" if receipt["before"] else None)
                    and receipt["temporary"] == ".d4-" + journal["transaction_id"] + "-enrollment", "Invalid enrollment receipt")
        raw, mode = _enrollment_postimage(journal)
        d3._require(receipt["after"] == {"sha256": hashlib.sha256(raw).hexdigest(), "mode": mode}, "Invalid enrollment postimage receipt")
        candidates = [raw]
        if receipt["backup"]:
            candidates.append(d3._read_file(path / receipt["backup"], d3.MAX_FILE, private=True))
            d3._require(hashlib.sha256(candidates[-1]).hexdigest() == receipt["before"]["sha256"], "Prior enrollment backup is corrupt")
        temporary_files.append((str(PROJECT_FILE), receipt["temporary"], candidates))
    if not cleanup:
        return
    for relative, name, candidates in temporary_files:
        temporary = root / Path(relative).parent / name
        if not os.path.lexists(temporary):
            continue
        _fingerprint(root, str(temporary.relative_to(root)))
        raw = d3._read_file(temporary, d3.MAX_FILE)
        if not any(candidate.startswith(raw) for candidate in candidates):
            raise Pending("Transaction temporary contains unrecognized edits; preserve it for manual reconciliation")
        directory = d3._open_path(temporary.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)


def _fresh(plan, journal=None, *, external_owned=()):
    """Normalize only receipt-owned paths; unrelated inputs retain D3 freshness checks."""
    root = Path(plan["identity"]["repository_root"])
    current = d3._snapshot(root, d3._snapshot_targets(plan), inventory=d3._inventory_enabled(plan["identity"]))
    previous = plan["identity"]
    owned = _owned(journal) if journal else {}
    owned.update({key:(None,None) for key in external_owned})
    if journal and journal["enrollment"]:
        owned[str(PROJECT_FILE)] = (None, None)
    for key in ("repository_root", "git_common_dir", "head", "index_digest"):
        d3._require(current[key] == previous[key], "Unrelated checkout identity/index changed")
    def external_inventory(rows):
        return [row for row in rows if row[3:] not in owned]
    d3._require(external_inventory(current["dirty_inventory"]) == external_inventory(previous["dirty_inventory"])
                and set(current["paths"]) - owned.keys() == set(previous["paths"]) - owned.keys(),
                "Unrelated current checkout inputs changed")
    old, new = previous["fingerprints"], current["fingerprints"]
    for key in old.keys() | new.keys():
        relative = key
        if key.startswith("instruction:"):
            location = Path(key[len("instruction:"):])
            relative = str(location.relative_to(root)) if location.is_relative_to(root) else key
        if relative not in owned:
            d3._require(old.get(key, {"status": "absent"}) == new.get(key, {"status": "absent"}), "Unrelated current input fingerprint changed")
    if journal is None:
        changes, _, _ = d3._current(plan)
        # Sources are checked per dependent action; all unrelated checkout inputs must match.
        d3._require(not [c for c in changes if not c.startswith("source:")], "Current D3 inputs are stale")


def _action_blockers(plan, action, manifest, completed, observed):
    original = next(a for a in plan["actions"] if a["id"] == action["id"])
    if action["id"] not in manifest["approved_scope"]:
        return "Action has no scoped approval"
    if not set(original["dependencies"]) <= completed:
        return "Required dependency has not completed"
    if original["scope"] != "checkout" or action["resources"] != ["checkout"]:
        return "Shared/global or unknown resources require manual serialization"
    if action["recovery"] != "file-preimages" or not action["effects"]:
        return "Complete bounded effects and recoverable file preimages are required"
    for decision in plan["decisions"]:
        if (decision["id"] in original["decision_ids"] or action["id"] in decision["affected_actions"]) and decision["disposition"] in ("pending", "proposed"):
            return "An applicable scoped decision remains unresolved"
    if original["kind"] == "external":
        return "External effects need the approved manual procedure"
    if original["kind"] == "command" and not action["execution"]["qualification"]:
        return "Coordinator qualification of complete command effects/recovery is missing"
    source_blockers = {row["scope"] for row in d3._source_blockers(plan, observed)}
    for row in plan["coverage"]:
        if action["id"] in row["actions"]:
            if row["blocker"] or row["outcome"] == "blocked":
                return "The applicable adoption requirement remains blocked"
            if "sources" in source_blockers or "catalog" in source_blockers or "source:" + row["source"] in source_blockers:
                return "The applicable pinned Toolkit source/provenance is unavailable"
    return None


def _legacy_writers(scope):
    """Inspect schema-1 retirement only; never initialize, adopt pins or open writable state."""
    from runtime.evidence import _read
    from runtime.startup import json_object
    # Current path/artifact safety checks apply without making the old DB runnable.
    inspected = replace(scope, runtime=replace(scope.runtime, schema_version=task_state.SCHEMA_VERSION))
    try:
        path = task_state._path(inspected)
        task_state._sidecars(path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
            db.execute("pragma trusted_schema=off")
            db.execute("pragma query_only=on")
            db.execute("begin")
            task_state._schema(db, version=1)
            checkouts = {}
            for scope_id, project_id, root, common, generation in db.execute("select * from checkouts"):
                d3._require(len(checkouts) < 1024, "Legacy checkout inspection exceeds its bound")
                for value in (project_id, root, common):
                    task_state._text(value, "legacy checkout identity", 4096)
                d3._require(Path(root).is_absolute() and str(Path(root).resolve()) == root and
                            Path(common).is_absolute() and str(Path(common).resolve()) == common,
                            "Legacy checkout identity is not canonical")
                task_state._integer(generation, "legacy checkout generation")
                identity = ProjectScope(project_id, Path(root), Path(common), scope.policy_digest)
                d3._require(scope_id == task_state._scope_id(identity), "Legacy checkout identity is corrupt")
                checkouts[scope_id] = (root, common)
            rows = db.execute("select scope_id,client,session_id,task_id,data from tasks limit 1025").fetchall()
            d3._require(len(rows) <= 1024 and sum(len(row[4]) for row in rows) <= 4 * 1024 * 1024,
                        "Legacy task inspection exceeds its bound")
            active = False
            identities = set()
            for scope_id, client, session, task_id, raw in rows:
                d3._require(scope_id in checkouts, "Legacy task has no checkout identity")
                json_object(raw)
                task = task_state._task(db, scope_id, client, session, task_id, historical=True)
                d3._require("delegation" not in task, "Legacy task contains unsupported delegation ownership state")
                d3._require(task["pins"]["schema_version"] == 1, "Legacy task pins have an incompatible schema")
                for field in ("policy_digest", "runtime_digest"):
                    d3._hash(task["pins"][field])
                identities.add((scope_id, client, session, task_id))
                unresolved = [a["id"] for a in task.get("evidence", {}).get("attempts", [])
                              if a["status"] in {"reserved", "running", "uncertain"}]
                shared_git = checkouts[scope_id][1] == str(scope.git_common_dir)
                if not unresolved and (task["mode"] != "editable" or not shared_git):
                    continue
                retirement = task.get("retirement")
                if task["state"] != "cancelled" or not retirement:
                    active = True
                    continue
                d3._hash(retirement["proof_digest"])
                proof = _read(inspected, retirement["proof"])
                task_state._fields(proof, ("version", "kind", "identity", "expected_revision", "expected_pins",
                    "observed_turn_id", "stopped", "descendants_stopped", "attempt_ids", "observation_ref", "coordinator_ref"))
                task_state._integer(proof["expected_revision"], "legacy stopped revision", 1)
                task_state._fields(proof["expected_pins"], ("schema_version", "policy_digest", "runtime_digest"))
                d3._require(type(proof["expected_pins"]["schema_version"]) is int,
                            "Legacy stopped-writer proof pins are corrupt")
                d3._require(type(proof["version"]) is int and proof["version"] == 1 and
                    proof["kind"] == "stopped-writer-reconciliation" and proof["stopped"] is True and
                    proof["descendants_stopped"] is True and proof["identity"] ==
                    {key: task[key] for key in ("client", "session_id", "task_id", "scope_ref")} and
                    proof["expected_pins"] == task["pins"] and proof["expected_revision"] == retirement["previous_revision"] and
                    retirement["previous_revision"] == task["revision"] - 1 and proof["observed_turn_id"] == task["turn_id"] and
                    proof["attempt_ids"] == unresolved and all(proof[key] == retirement[key]
                    for key in ("observation_ref", "coordinator_ref")), "Legacy stopped-writer proof binding failed")
            for identity in db.execute("select scope_id,client,session_id,task_id from bindings"):
                d3._require(identity in identities, "Legacy task binding is corrupt")
            for table in ("turns", "receipts"):
                d3._require(not db.execute("select 1 from " + table +
                    " where scope_id is null or scope_id not in (select scope_id from checkouts) limit 1").fetchone(),
                    "Legacy event checkout binding is corrupt")
            return active
    except (StartupError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        raise Pending("Legacy runtime state or retained stopped-writer proof is unavailable, unsafe or incompatible") from None


def _writers(plan, prior, observation, scope=None):
    scope = scope or _record(prior, plan, current=False)
    if scope.runtime is None:
        return
    if not observation or not observation["task_reconciliation"]:
        raise Pending("Confirm stopped writers and reconcile their task/evidence records")
    if scope.runtime.schema_version == 1:
        if _legacy_writers(scope):
            raise Pending("Affected legacy task writers remain active or unreconciled; use their old pinned runtime first")
        return
    active = False
    try:
        with task_state._database(scope, historical=scope.runtime.schema_version == 2) as db:
            if db is None:
                return
            rows = db.execute("select t.scope_id,t.client,t.session_id,t.task_id from tasks t join checkouts c on t.scope_id=c.scope_id where c.repository_root=? and c.git_common_dir=?",
                              (str(scope.repository_root), str(scope.git_common_dir))).fetchall()
            for scope_id, client, session, task_id in rows:
                task = task_state._task(db, scope_id, client, session, task_id, historical=True)
                active |= task["mode"] == "editable" and task["state"] not in {"CANCELLED", "cancelled"}
            from runtime.delegation import _ledger, _held_conflict, _linked_attempt
            affected = [dict(kind="path", key=str(scope.repository_root), mode="write"),
                        dict(kind="git", key=str(scope.git_common_dir), mode="write")]
            from runtime.delegation import _claims
            records = _ledger(scope, db)
            for record in records:
                if record["data"]["status"] != "settled" and _held_conflict(_claims(affected), record["data"]["claims"]):
                    active = True
            for scope_id, client, session, task_id in db.execute("select scope_id,client,session_id,task_id from tasks"):
                task = task_state._task(db, scope_id, client, session, task_id, historical=True)
                active |= not task.get("retirement") and any(a["status"] in {"reserved", "running", "uncertain"} and
                            _linked_attempt(scope, scope_id, task, a, records) is None
                            for a in task.get("evidence", {}).get("attempts", []))
    except task_state.StateError:
        raise Pending('Affected assignment history is incompatible or unavailable') from None
    if active:
        raise Pending("Affected task writers remain active or unreconciled; stop and reconcile them first")


def _writer_records(path, root, journal):
    records = [journal["manifest"]["enrollment"]["record"]]
    actual = _fingerprint(root, str(PROJECT_FILE))
    if actual is not None:
        stored = journal["enrollment"]
        allowed = [journal["manifest"]["enrollment"]["before"]] if stored is None else [stored["after"]]
        if stored and stored["status"] in ("prepared", "restoring", "restored"):
            allowed.append(stored["before"])
        if actual not in allowed:
            raise Pending("Enrollment changed externally; preserve it and supporting configuration")
        records.append(d3.read_request(d3._read_file(root / PROJECT_FILE, d3.MAX_FILE)))
    if journal["enrollment"] and journal["enrollment"]["backup"]:
        records.append(d3.read_request(d3._read_file(path / journal["enrollment"]["backup"], d3.MAX_FILE, private=True)))
    return records


def _guard_writers(path, root, journal, observation):
    records = _writer_records(path, root, journal)
    covered = getattr(_authority_context, "directories", None)
    if covered is not None:
        d3._require({Path(record["runtime"]["state_dir"]) for record in records if record.get("runtime")} <= covered,
                    "Enrollment authority changed outside the protected operation")
    seen = set()
    for record in records:
        key = d3._digest(record.get("runtime"))
        if key not in seen:
            _writers(journal["plan"], record, observation)
            seen.add(key)


def _ensure_parents(path, root, relative, journal):
    parts = Path(relative).parent.parts
    for number in range(1, len(parts) + 1):
        name = str(Path(*parts[:number]))
        location = root / name
        if journal["schema_version"] == 2:
            name = str(location)
        stored = journal["directories"].get(name)
        try:
            descriptor = d3._open_path(location, os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError:
            if stored and stored["status"] == "owned":
                raise Pending("A transaction-owned parent directory disappeared; reconcile it before writing")
            journal["directories"][name] = {"status": "prepared", "device": None, "inode": None}
            _save(path, journal)
            parent = d3._open_path(location.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                try:
                    os.mkdir(location.name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    raise Pending("A parent directory appeared during preparation; preserve it and retry reconciliation") from None
                os.fsync(parent)
                _checkpoint("directory_created")
                descriptor = os.open(location.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                info = os.fstat(descriptor)
                journal["directories"][name] = {"status": "owned", "device": info.st_dev, "inode": info.st_ino}
                os.close(descriptor)
                _save(path, journal)
                _checkpoint("directory_recorded")
            finally:
                os.close(parent)
        else:
            try:
                info = os.fstat(descriptor)
                if stored and stored["status"] == "owned" and (info.st_dev, info.st_ino) != (stored["device"], stored["inode"]):
                    raise Pending("Parent directory ownership changed; preserve it before further mutation")
                if stored and stored["status"] == "prepared":
                    journal["directories"][name] = {"status": "retained", "device": info.st_dev, "inode": info.st_ino}
                    _save(path, journal)
            finally:
                os.close(descriptor)


def _restore_directories(path, root, journal):
    for name in sorted(journal["directories"], key=lambda value: len(Path(value).parts), reverse=True):
        stored = journal["directories"][name]
        if stored["status"] not in ("owned", "prepared"):
            continue
        location = root / name
        try:
            descriptor = d3._open_path(location, os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError:
            stored["status"] = "removed"
            continue
        try:
            if stored["status"] == "prepared":
                stored["status"] = "retained"
                continue
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (stored["device"], stored["inode"]):
                stored["status"] = "retained"
                continue
        finally:
            os.close(descriptor)
        parent = d3._open_path(location.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.rmdir(location.name, dir_fd=parent)
            except OSError:
                stored["status"] = "retained"
            else:
                os.fsync(parent)
                stored["status"] = "removed"
        finally:
            os.close(parent)
    _save(path, journal)


def _deactivate(path, root, journal, observation):
    _guard_writers(path, root, journal, observation)
    e = journal["manifest"]["enrollment"]
    stored = journal["enrollment"]
    if stored is not None:
        actual = _fingerprint(root, str(PROJECT_FILE))
        accepted = [None, stored["before"], stored["after"]] if stored["status"] == "prepared" else [None, stored["after"]]
        if actual not in accepted:
            raise Pending("Enrollment changed externally; preserve it and reconcile ownership")
        if stored["status"] == "prepared" and actual == stored["before"]:
            _guard_writers(path, root, journal, observation)
            _replace(root, str(PROJECT_FILE), b"", None, stored["before"])
            stored["status"] = "inactive"
            _save(path, journal)
        return
    actual = _fingerprint(root, str(PROJECT_FILE))
    if actual != e["before"]:
        raise Pending("Existing enrollment does not match the approved record")
    if actual is not None:
        prior = d3.read_request(d3._read_file(root / PROJECT_FILE, d3.MAX_FILE))
        _writers(journal["plan"], prior, observation)
    backup = _backup(path, root, str(PROJECT_FILE), actual, "enrollment.backup")
    raw, mode = _enrollment_postimage(journal)
    after = {"sha256": hashlib.sha256(raw).hexdigest(), "mode": mode}
    journal["enrollment"] = {"before": actual, "after": after, "backup": backup, "status": "prepared",
                             "temporary": ".d4-" + journal["transaction_id"] + "-enrollment"}
    _save(path, journal)
    if actual is not None:
        _guard_writers(path, root, journal, observation)
        _replace(root, str(PROJECT_FILE), b"", None, actual)
    journal["enrollment"]["status"] = "inactive"
    _save(path, journal)


def observation_digest(manifest):
    """Bind coordinator observations to current checkout, effect files and enrollment."""
    root = Path(manifest["checkout"]["repository_root"])
    paths = sorted({str(PROJECT_FILE)} | {e["path"] for a in manifest["actions"] for e in a["effects"]})
    return d3._digest({"identity": d3._snapshot(root, paths, inventory=True),
        "effects": {name: _fingerprint(root, name) for name in paths},
        "enrollment": _fingerprint(root, str(PROJECT_FILE)), "package_pins": d3._package_pins()})


def _current_observation(journal):
    current = observation_digest(journal["manifest"])
    return next((o for o in reversed(journal["observations"]) if o["input_digest"] == current), None)


def _enrollment_postimage(journal):
    original = next((a for a in journal["plan"]["actions"] if a["target"] == str(PROJECT_FILE)), None)
    if original:
        action = next(a for a in journal["manifest"]["actions"] if a["id"] == original["id"])
        return original["text"].encode(), action["effects"][0]["after"]["mode"]
    return json.dumps(journal["manifest"]["enrollment"]["record"], sort_keys=True, ensure_ascii=True).encode() + b"\n", 0o600


def _qualification(journal):
    plan, manifest = journal["plan"], journal["manifest"]
    e = manifest["enrollment"]
    if "enrollment" not in manifest["approved_scope"]:
        return "Enrollment activation/recovery lacks scoped approval"
    observation = _current_observation(journal)
    if observation is None:
        return "Collect adoption qualification/reviews and bind observations to current inputs before final enrollment"
    rows = {row["id"]: row["evidence"] for row in observation["qualification"]["requirements"]}
    required_actions = set(e["requires"])
    required_decisions = {d for a in plan["actions"] if a["id"] in required_actions for d in a["decision_ids"]}
    for requirement in plan["requirements"]:
        coverage = next((r for r in plan["coverage"] if r["id"] == requirement["id"]), None)
        if coverage is None:
            return "Complete pinned Toolkit/policy coverage is missing"
        if requirement["mandatory"] and coverage["applicability"] == "applicable" and not rows.get(requirement["id"]):
            return "A mandatory adoption requirement lacks scoped qualification evidence"
        relevant = coverage["applicability"] == "applicable" and (requirement["mandatory"] or bool(set(coverage["actions"]) & required_actions))
        if relevant:
            required_decisions.update(coverage["decision_ids"])
            if coverage["blocker"] or coverage["outcome"] == "blocked":
                return "Required adoption coverage contains unresolved blockers"
    q = observation["qualification"]
    if not q["general_review"] or (e["qualification"]["security_required"] and not q["security_review"]):
        return "Required general/security review evidence is missing"
    if not plan["semantic_assessment"] or not all(plan["semantic_assessment"].get(k) for k in
            ("complete", "catalog_complete", "conflicts_checked", "instructions_checked", "source_provenance_checked")):
        return "Complete semantic assessment and catalog coverage are required before enrollment"
    if any(d["disposition"] in ("pending", "proposed") and (d["id"] in required_decisions or bool(set(d["affected_actions"]) & required_actions)) for d in plan["decisions"]):
        return "Unresolved adoption decisions prevent enrollment"
    if d3._source_blockers(plan, d3._sources(plan["sources"]["declared"])):
        return "Pinned Toolkit source coverage is incomplete"
    return None


def _apply_action(path, root, journal, original, action, observation, checkout_lock):
    receipt = journal["receipts"].get(action["id"])
    outputs = {e["path"]: e["after"] for e in action["effects"]} if receipt else {}
    for condition in action["preconditions"]:
        if condition["path"] in outputs and _fingerprint(root, condition["path"]) == outputs[condition["path"]]:
            continue
        if _fingerprint(root, condition["path"]) != condition["fingerprint"]:
            raise Pending("An approved action precondition changed")
    receipt = journal["receipts"].get(action["id"])
    if receipt is None or receipt["status"] != "completed":
        _guard_writers(path, root, journal, observation)
        enrollment = journal["enrollment"]
        if enrollment and _fingerprint(root, str(PROJECT_FILE)) == enrollment["after"]:
            _replace(root, str(PROJECT_FILE), b"", None, enrollment["after"])
            enrollment["status"] = "inactive"
            _save(path, journal)
    if receipt is None:
        files = []
        for index, effect in enumerate(action["effects"]):
            backup = _backup(path, root, effect["path"], effect["before"],
                             "backup-" + str(journal["manifest"]["actions"].index(action)) + "-" + str(index))
            files.append({"backup": backup, "temporary": ".d4-" + journal["transaction_id"] + "-"
                          + str(journal["manifest"]["actions"].index(action)) + "-" + str(index)})
        receipt = {"status": "prepared", "files": files, "returncode": None}
        journal["receipts"][action["id"]] = receipt
        _save(path, journal)
        _checkpoint("prepared")
    actual = [_fingerprint(root, effect["path"]) for effect in action["effects"]]
    before = [e["before"] for e in action["effects"]]
    after = [e["after"] for e in action["effects"]]
    if any(value not in (old, new) for value, old, new in zip(actual, before, after)):
        raise Pending("Action output changed externally; preserve the unrecognized state")
    command = original["kind"] == "command"
    if receipt["status"] == "completed":
        if actual != after:
            raise Pending("Completed action output no longer matches its receipt")
        return
    if command:
        execution = action["execution"]
        if receipt["status"] == "started":
            d3._require(receipt["command"]["stopped"], "Command lifecycle must be reconciled before effects")
            if actual == after and (before != after or receipt["returncode"] == 0):
                if receipt["returncode"] not in (None, 0):
                    raise Pending("Command reported failure; reconcile its effects manually before completion")
                receipt["status"] = "completed"
                _save(path, journal)
                return
            if not execution["idempotency"] or actual != before:
                raise Pending("Command outcome is uncertain; inspect approved effects before any replay")
        elif actual != before:
            raise Pending("Command preimages changed before execution")
        receipt["status"] = "started"
        receipt["returncode"] = None
        receipt["command"] = {"attempt_id": uuid.uuid4().hex, "stopped": False}
        _save(path, journal)
        _publish_command_owner(path, journal, action, receipt["command"]["attempt_id"])
        _checkpoint("command_started")
        _guard_writers(path, root, journal, observation)
        try:
            process = subprocess.Popen(execution["argv"], cwd=root,
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, pass_fds=(checkout_lock,))
            try:
                receipt["returncode"] = process.wait(timeout=execution["timeout_seconds"])
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                receipt["returncode"] = process.returncode
                raise
        except (OSError, subprocess.TimeoutExpired):
            _save(path, journal)
            raise Pending("Command failed or timed out; verify all writable descendants stopped before reconciling effects") from None
        _save(path, journal)
        _checkpoint("command_finished")
        raise Pending("Command direct process exited; current lifecycle proof for this attempt and all writable descendants is required")
    else:
        effect = action["effects"][0]
        if actual[0] != effect["after"]:
            raw = original["text"].encode() if original["kind"] == "write" else b""
            _ensure_parents(path, root, effect["path"], journal)
            _guard_writers(path, root, journal, observation)
            _replace(root, effect["path"], raw, effect["after"], effect["before"], receipt["files"][0]["temporary"])
    receipt["status"] = "completed"
    _save(path, journal)
    _checkpoint("outcome")


def _activate(path, root, journal, observation):
    e, receipt = journal["manifest"]["enrollment"], journal["enrollment"]
    actual = _fingerprint(root, str(PROJECT_FILE))
    if actual == receipt["after"] and receipt["status"] in ("activating", "active"):
        receipt["status"] = "active"
        _save(path, journal)
        return
    reason = _qualification(journal)
    if reason:
        raise Pending(reason)
    compatibility = task_state.inspect_state(_record(e["record"], journal["plan"]))
    if not compatibility["compatible"]:
        raise Pending("Enrollment runtime state/pins are incompatible; reconcile them before activation")
    if actual is not None:
        raise Pending("Enrollment changed externally; activation preserves it")
    _guard_writers(path, root, journal, observation)
    _ensure_parents(path, root, str(PROJECT_FILE), journal)
    receipt["status"] = "activating"
    _save(path, journal)
    raw, _ = _enrollment_postimage(journal)
    _replace(root, str(PROJECT_FILE), raw, receipt["after"], None, receipt["temporary"])
    receipt["status"] = "active"
    _save(path, journal)


def _rollback(path, root, journal, observation):
    _guard_writers(path, root, journal, observation)
    receipt = journal["enrollment"]
    if receipt is not None:
        actual = _fingerprint(root, str(PROJECT_FILE))
        if receipt["status"] in ("restoring", "restored") and actual == receipt["before"]:
            receipt["status"] = "restored"
            return
        if actual == receipt["after"] and receipt["status"] in ("activating", "active"):
            _guard_writers(path, root, journal, observation)
            _replace(root, str(PROJECT_FILE), b"", None, receipt["after"])
        elif actual is not None and not (receipt["status"] == "prepared" and actual == receipt["before"]):
            raise Pending("Enrollment changed externally; dependent configuration rollback is blocked")
        receipt["status"] = "inactive"
        _save(path, journal)
    failed = set()
    originals = {a["id"]: a for a in journal["plan"]["actions"]}
    for action in reversed(journal["manifest"]["actions"]):
        stored = journal["receipts"].get(action["id"])
        if stored is None:
            continue
        if stored["status"] == "rolled_back":
            if any(_fingerprint(root, effect["path"]) != effect["before"] for effect in action["effects"]):
                failed.add(action["id"])
                journal["blocked"].append({"id": action["id"], "reason": "A restored file changed externally; preserve it and keep prior enrollment inactive"})
            continue
        if any(action["id"] in originals[key]["dependencies"] for key in failed):
            journal["blocked"].append({"id": action["id"], "reason": "A dependent action has not been safely rolled back"})
            failed.add(action["id"])
            continue
        try:
            for effect, backup in reversed(list(zip(action["effects"], stored["files"]))):
                _guard_writers(path, root, journal, observation)
                _restore(path, root, effect["path"], backup, effect["before"], effect["after"])
            stored["status"] = "rolled_back"
            _save(path, journal)
        except Pending as error:
            failed.add(action["id"])
            journal["blocked"].append({"id": action["id"], "reason": str(error)})
    if failed:
        return
    _restore_directories(path, root, journal)
    if receipt is not None and receipt["before"] is not None:
        for action in journal["manifest"]["actions"]:
            for effect in action["effects"]:
                if effect["path"] != str(PROJECT_FILE) and _fingerprint(root, effect["path"]) != effect["before"]:
                    raise Pending("Prior enrollment stays inactive because supporting configuration changed")
        _fresh(journal["plan"], journal)
        observation = _current_observation(journal)
        if not observation or not observation["prior_compatibility"]:
            raise Pending("Prior enrollment stays inactive until client/runtime compatibility is qualified")
        prior_raw = d3._read_file(path / receipt["backup"], d3.MAX_FILE, private=True)
        try:
            prior_scope = _record(d3.read_request(prior_raw), journal["plan"])
            if not task_state.inspect_state(prior_scope)["compatible"]:
                raise StartupError("Prior runtime state is incompatible")
        except StartupError:
            raise Pending("Prior enrollment is stale or incompatible and remains inactive") from None
        receipt["status"] = "restoring"
        _save(path, journal)
        _restore(path, root, str(PROJECT_FILE), receipt, receipt["before"], None)
    if receipt is not None:
        receipt["status"] = "restored"


def _report(journal, exit_code, reason=None):
    blocked = deepcopy(journal.get("blocked", [])) if journal else []
    if reason:
        blocked.append({"id": "transaction", "reason": reason})
    try:
        inputs = observation_digest(journal["manifest"]) if journal else None
    except (StartupError, OSError, ValueError, KeyError, TypeError):
        inputs = None
    return {"transaction_id": journal["transaction_id"] if journal else None,
            "state": journal["state"] if journal else "blocked",
            "completed": [key for key, value in journal["receipts"].items() if value["status"] == "completed"] if journal else [],
            "blocked": blocked,
            "next_step": "No further action for this operation" if exit_code == 0 else
                "Resolve the reported scope safely, then retry the same bound request; obtain renewed approval for changed scope",
            "command_attempts": [dict(id=key, **value["command"]) for key, value in journal.get("receipts", {}).items() if "command" in value] if journal else [],
            "retained_directories": [name for name, value in journal.get("directories", {}).items() if value["status"] == "retained"] if journal else [],
            "observation_input_digest": inputs,
            "exit_code": exit_code, "limitations": LIMITATIONS}


def _run(plan_dir, request, operation):
    journal = None
    try:
        path = d3._plan_path(plan_dir)
        with d3._locked(path):
            plan = d3._load(path)
        d3._require(plan is not None, "Inspect and record a D3 plan before applying")
        d3._require(plan["schema_version"] == 1,
                    "D4 cannot apply, resume or roll back onboarding schema 2 Project inspections")
        root, git_dir = _validate(request, plan)
        with _worktree_lock(git_dir) as checkout_lock:
            owner = _command_owner(git_dir)
            if owner and (owner["plan_dir"] != str(path) or owner["transaction_id"] != request["manifest"]["transaction_id"]):
                missing = not (Path(owner["plan_dir"]) / "transaction.json").is_file()
                raise Pending("Unresolved command owner blocks this checkout; " +
                              ("the original plan is missing and needs trusted manual recovery" if missing else "resume the original plan with current command lifecycle proof"))
            journal = _load_journal(path)
            if journal is not None:
                d3._require(journal["manifest_digest"] == d3._digest(request["manifest"])
                            and journal["manifest"] == request["manifest"] and journal["approval"] == request["approval"]
                            and journal["plan"] == plan, "Transaction binding or frozen proposal changed")
                _recovery_files(path, root, journal, cleanup=False)
            elif operation != "apply":
                raise StartupError("No prepared transaction to resume or roll back")
            if not request["approval"]["evidence"]:
                return _report(journal, 2, "Trusted host/session approval evidence is missing; D3 free text grants no authority")
            if journal is None:
                _fresh(plan)
                for action in request["manifest"]["actions"]:
                    for effect in action["effects"]:
                        d3._require(_fingerprint(root, effect["path"]) == effect["before"], "Approved effect preimage changed")
                journal = {"schema_version": 1, "transaction_id": request["manifest"]["transaction_id"],
                    "manifest_digest": d3._digest(request["manifest"]), "manifest": deepcopy(request["manifest"]),
                    "approval": deepcopy(request["approval"]), "plan": deepcopy(plan), "state": "prepared",
                    "receipts": {}, "enrollment": None, "blocked": [], "directories": {}, "observations": []}
                _save(path, journal)
                _checkpoint("state")
            effective_observation = None
            if journal["state"] != "rolled_back":
                if "observations" in request:
                    effective_observation = request["observations"]
                    d3._require(effective_observation["input_digest"] == observation_digest(request["manifest"]),
                                "Observation input bindings are stale")
                else:
                    effective_observation = _current_observation(journal)
            with _authority_operation(path, root, journal, effective_observation):
                if journal["state"] == "rolled_back":
                    if owner is not None:
                        _reconcile_command_lifecycles(path, journal)
                    if operation == "rollback":
                        for action in journal["manifest"]["actions"]:
                            for effect in action["effects"]:
                                if effect["path"] != str(PROJECT_FILE) and _fingerprint(root, effect["path"]) != effect["before"]:
                                    return _report(journal, 2, "Previously restored content changed externally; preserve the current user edit")
                        if journal["enrollment"] and _fingerprint(root, str(PROJECT_FILE)) != journal["enrollment"]["before"]:
                            return _report(journal, 2, "Enrollment changed after rollback; preserve its external ownership")
                    return _report(journal, 0 if operation == "rollback" else 1,
                                   None if operation == "rollback" else "Rolled-back transactions cannot apply again; prepare a fresh approved transaction")
                journal["blocked"] = []
                if "observations" in request:
                    observation = request["observations"]
                    d3._require(observation["input_digest"] == observation_digest(request["manifest"]), "Observation input bindings are stale")
                    if observation not in journal["observations"]:
                        journal["observations"].append(deepcopy(observation))
                        _save(path, journal)
                _reconcile_command_lifecycles(path, journal)
                _recovery_files(path, root, journal)
                writer_observation = _current_observation(journal)
                try:
                    if operation == "rollback":
                        _rollback(path, root, journal, writer_observation)
                        journal["state"] = "rollback_partial" if journal["blocked"] else "rolled_back"
                    else:
                        if journal["state"] == "rollback_partial":
                            raise Pending("Partial rollback must finish before any application can continue")
                        _fresh(plan, journal)
                        if "enrollment" not in journal["manifest"]["approved_scope"]:
                            raise Pending("Enrollment activation and recovery need scoped approval before changing adoption configuration")
                        _deactivate(path, root, journal, writer_observation)
                        journal["state"] = "applying"
                        _save(path, journal)
                        completed = set()
                        observed = d3._sources(plan["sources"]["declared"])
                        originals = {a["id"]: a for a in plan["actions"]}
                        for action in journal["manifest"]["actions"]:
                            if any(r.get("command") and not r["command"]["stopped"] for r in journal["receipts"].values()):
                                journal["blocked"].append({"id": action["id"], "reason": "An earlier command attempt needs stopped-descendant lifecycle proof before further mutation"})
                                continue
                            if originals[action["id"]]["target"] == str(PROJECT_FILE):
                                continue
                            reason = _action_blockers(plan, action, journal["manifest"], completed, observed)
                            if not reason:
                                try:
                                    _apply_action(path, root, journal, originals[action["id"]], action, writer_observation, checkout_lock)
                                    completed.add(action["id"])
                                except Pending as error:
                                    reason = str(error)
                            if reason:
                                journal["blocked"].append({"id": action["id"], "reason": reason})
                        required = set(journal["manifest"]["enrollment"]["requires"])
                        enrollment_ids = {a["id"] for a in plan["actions"] if a["target"] == str(PROJECT_FILE)}
                        unsettled = any(r.get("command") and not r["command"]["stopped"] for r in journal["receipts"].values())
                        if unsettled or not required - enrollment_ids <= completed:
                            journal["state"] = "blocked"
                        else:
                            for action_id in enrollment_ids:
                                original = originals[action_id]
                                if action_id not in journal["manifest"]["approved_scope"] or not set(original["dependencies"]) <= completed:
                                    raise Pending("Final enrollment action/dependencies lack scoped approval or completion")
                            _activate(path, root, journal, writer_observation)
                            for action_id in enrollment_ids:
                                journal["receipts"][action_id] = {"status": "completed", "files": [], "returncode": None}
                            journal["state"] = "blocked" if journal["blocked"] else "applied"
                except Pending as error:
                    journal["blocked"].append({"id": "enrollment" if operation == "rollback" else "transaction", "reason": str(error)})
                    journal["state"] = "rollback_partial" if operation == "rollback" else "blocked"
                except StartupError:
                    journal["state"] = "interrupted"
                    _save(path, journal)
                    raise
                _save(path, journal)
                _checkpoint("state")
                return _report(journal, 0 if journal["state"] in ("applied", "rolled_back") else 2)
    except Pending as error:
        return _report(journal, 2, str(error))
    except StartupError as error:
        return _report(journal, 1, str(error))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return _report(journal, 1, "D4 input, filesystem or runtime state is unavailable or unsafe; preserve recovery files")


def apply_plan(plan_dir, request, *, host=None):
    """Apply approved effects, then publish configured or qualified active enrollment."""
    return _public_operation(plan_dir, request, "apply", host)


def resume_plan(plan_dir, request, *, host=None):
    """Reconcile durable receipts against the frozen manifest before continuing."""
    return _public_operation(plan_dir, request, "resume", host)


def rollback_plan(plan_dir, request, *, host=None):
    """Restore owned effects backwards; active enrollment also requires scoped reconciliation."""
    return _public_operation(plan_dir, request, "rollback", host)


def _public_host(host):
    if not (isinstance(host, _D4HostCapability) and host.issuer is _D4_HOST_ISSUER
            and host.state.get('active') and host.state.get('capability') is host
            and callable(getattr(host.owner, 'authorize_d4_operation', None))):
        raise Pending('Actual trusted coordinator operation authority is unavailable')


def _public_plan_directory(value):
    """Observe an existing review directory without creating it for admission."""
    from runtime import review_storage
    path = d3._plan_path(value, create=False)
    return path, dict(path=str(path), binding=d3._filesystem_binding(path), parents=review_storage._parents(path))


@contextmanager
def _public_plan_lock(path):
    """Use the existing D3 lock without directory or lock creation."""
    directory = d3._open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        d3._private(os.fstat(directory), directory=True)
        descriptor = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        d3._private(os.fstat(descriptor))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Pending('Onboarding plan busy; retry the same approved operation') from None
        yield path, directory
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _public_entry(host, path, plan, request, operation, *, authorities=None):
    _public_host(host)
    _, directory = _public_plan_directory(path)
    facts = dict(operation=operation, plan_directory=directory, plan=plan, request=request)
    if authorities is not None:
        facts['authorities'] = authorities
    host.owner.authorize_d4_operation(_canonical_freeze(deepcopy(facts)))
    _public_host(host)
    if _public_plan_directory(path)[1] != directory or d3._load(path) != plan:
        raise Pending('The approved plan or private directory changed during admission')


def _public_operation(plan_dir, request, operation, host):
    try:
        path, _ = _public_plan_directory(plan_dir)
        plan = d3._load(path)
        d3._require(plan is not None, 'Inspect and record a plan before applying')
        if plan['schema_version'] == 1:
            return _run(plan_dir, request, operation)
        _public_host(host)
        request = deepcopy(request)
        d3._require(request.get('schema_version') == 2, 'Request schema differs from canonical plan')
        def authorize(selected_path, selected_plan, selected_request, selected_operation, *, authorities=None):
            _public_entry(host, selected_path, selected_plan, selected_request, selected_operation, authorities=authorities)
        result = _run_scoped(path, request, operation, host=host, entry_authorizer=authorize)
        if (operation != 'rollback' and result['exit_code'] == 0 and not result.get('qualified')
                and (not _file_configuration(request['manifest'], plan) or getattr(host.owner, '_creation', None) is not None)):
            configured = all(entry['activation']['status'] == 'configured'
                for _, _, entry in _scoped_entries(request['manifest']['enrollment']['record']).values())
            result = dict(result, exit_code=2, next_step=(
                'Configuration is complete; use a separately authorized operation to activate managed runtime'
                if configured else 'Collect current mandatory qualification and resume the same approved transaction'))
            result['blocked'] = [*result['blocked'], dict(id='qualification', reason='Configuration is applied; mandatory qualification remains unobserved')]
        return result
    except (StartupError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        return dict(state='blocked', exit_code=2 if isinstance(error, Pending) else 1,
                    blocked=[dict(id='transaction', reason=str(error) if isinstance(error, StartupError) else 'Canonical input or authority is unavailable')],
                    activation_status='configured', qualified=False)


@contextmanager
def _public_source_guards(locations, owner, manifest):
    with ExitStack() as guards:
        try:
            acquired = guards.enter_context(_scoped_source_guards(locations, owner, manifest))
        except (StartupError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            raise Pending('Complete existing canonical runtime authorities are required before mutation') from None
        yield acquired


def _birth_intent_shape(intent):
    from runtime import review_storage
    d3._object(intent, ('identity', 'project_id', 'destination', 'members', 'peers', 'history'))
    d3._object(intent['identity'], ('plan_dir', 'transaction_id', 'manifest_digest'))
    task_state._canonical_path(intent['identity']['plan_dir']); d3._text(intent['identity']['transaction_id']); d3._hash(intent['identity']['manifest_digest'])
    d3._text(intent['project_id'])
    destination = intent['destination']
    d3._object(destination, ('directory', 'database', 'parents', 'missing_directories', 'directory_preimage', 'database_preimage'))
    task_state._canonical_path(destination['directory'])
    d3._require(destination['database'] == str(Path(destination['directory']) / 'runtime.sqlite3')
        and destination['database_preimage'] == 'absent', 'Invalid birth destination')
    d3._object(destination['directory_preimage'], ('status', 'binding'))
    d3._require(destination['directory_preimage']['status'] in ('absent', 'private-empty'), 'Invalid birth preimage')
    for binding in destination['parents'].values(): task_state._canonical_binding(binding)
    d3._require(isinstance(destination['missing_directories'], list) and len(destination['missing_directories']) <= 128, 'Invalid birth directory chain')
    for path in destination['missing_directories']: task_state._canonical_path(path)
    d3._require(isinstance(intent['members'], list) and 0 < len(intent['members']) <= 4096
        and isinstance(intent['peers'], list) and len(intent['peers']) <= 4096, 'Invalid birth members/peers')
    for member in intent['members']:
        d3._object(member, ('repository_id', 'checkout_id', 'clients', 'physical_identity'))
        d3._text(member['repository_id']); d3._text(member['checkout_id']); d3._strings(member['clients'], empty=False)
        d3._object(member['physical_identity'], ('version', 'project_id', 'repository_id', 'checkout_id', 'project_root',
            'project_binding', 'primary_root', 'repository_root', 'git_dir', 'git_common_dir', 'bindings'))
        d3._require(member['physical_identity']['project_id'] == intent['project_id'] and member['physical_identity']['repository_id'] == member['repository_id']
            and member['physical_identity']['checkout_id'] == member['checkout_id'], 'Birth member identity changed')
    for peer in intent['peers']:
        d3._object(peer, ('authority', 'baseline'))
        d3._object(peer['authority'], ('directory', 'database', 'directory_binding', 'database_binding'))
        d3._object(peer['baseline'], ('reference', 'content', 'dependencies'))
    review_storage._birth_history_shape(intent['history'])
    d3._require(len(json.dumps(intent,sort_keys=True).encode()) <= d3.MAX_REQUEST, 'Birth intent exceeds existing input bound')
    return intent


def _birth_intent(plan, manifest, history, *, plan_dir):
    from runtime import review_storage, evidence
    entries = _scoped_entries(manifest['enrollment']['record'])
    directories = {Path(entry['configuration']['runtime']['state_dir']) for _, _, entry in entries.values()
        if entry['configuration'] and entry['configuration']['runtime']}
    fresh = [path for path in directories if not os.path.lexists(path / 'runtime.sqlite3')]
    d3._require(len(fresh) == 1, 'Birth requires one approved absent shared authority')
    directory = review_storage._location(fresh[0])
    roots = {Path(manifest['enrollment']['record']['project_root'])}
    roots.update(Path(value) for repository, checkout, _ in entries.values() for value in
        (repository['primary_root'], repository['git_common_dir'], checkout['root'], checkout['git_dir']))
    d3._require(not any(directory.is_relative_to(root) or root.is_relative_to(directory) for root in roots), 'Birth storage overlaps Project/Git scope')
    if directory.exists():
        task_state._private(directory, True)
        d3._require(next(directory.iterdir(), None) is None, 'Unknown existing birth directory content')
    missing = []; parent = directory
    while not parent.exists(): missing.append(str(parent)); parent = parent.parent
    d3._require(history['version'] != 2 or not any(
        Path(history['machine']['path']).is_relative_to(Path(name)) for name in missing),
        'Birth storage would create missing machine-history parents; choose a separate state directory')
    destination = dict(directory=str(directory), database=str(directory / 'runtime.sqlite3'),
        parents=review_storage._parents(directory, existing=True), missing_directories=list(reversed(missing)),
        directory_preimage=dict(status='private-empty' if directory.exists() else 'absent',
            binding=d3._filesystem_binding(directory) if directory.exists() else None), database_preimage='absent')
    record = manifest['enrollment']['record']; members = {}
    for key, (_, _, entry) in entries.items():
        if not entry['configuration'] or entry['configuration']['runtime'] != dict(schema_version=3, state_dir=str(directory)): continue
        projection = _scoped_projection(record, key); physical = task_state._physical_identity(projection)
        member = members.setdefault(key[:2], dict(repository_id=key[0], checkout_id=key[1], clients=[], physical_identity=physical))
        d3._require(member['physical_identity'] == physical, 'Birth physical client identities differ')
        member['clients'].append(key[2])
    peers = {}; pending = set(directories) - {directory}
    _, prior = _scoped_prior(plan)
    pending.update(_scoped_runtime_locations([prior]) if prior else ())
    pending.discard(directory)
    while pending:
        location = pending.pop()
        if str(location) in peers: continue
        context = _recovery_context(task_state._authority_identity(location / 'runtime.sqlite3'))
        with evidence._context_database(context) as db: baseline = task_state._source_baseline(None, db, context=context)
        d3._require(baseline is not None and baseline['content']['source_contract'] == 2, 'Birth peer has no original baseline')
        if baseline['content'].get('birth_expectation') is not None:
            with evidence._context_database(context) as db: handoff=evidence._read_birth_handoff(context,db)
            review_storage._retained_birth_history(handoff['certificate'])
        peers[str(location)] = dict(authority=_canonical_plain(context.authority_set[0]), baseline=_canonical_plain(baseline))
        _, sources = _recovery_sources(context)
        pending.update(Path(authority['directory']) for _, body in sources for authority in body['authorities'] if Path(authority['directory']) != directory)
        d3._require(len(peers) <= 4096, 'Birth peer coverage exceeds bound')
    intent = dict(identity=dict(plan_dir=str(plan_dir), transaction_id=manifest['transaction_id'], manifest_digest=d3._digest(manifest)),
        project_id=record['project_id'], destination=destination, members=[members[key] for key in sorted(members)],
        peers=[peers[key] for key in sorted(peers)], history=deepcopy(history))
    return _birth_intent_shape(intent)


def _birth_certificate_shape(certificate):
    d3._object(certificate, ('version', 'intent', 'allocation', 'baseline', 'seed_digest'))
    d3._require(type(certificate['version']) is int and certificate['version'] == 1, 'Unsupported birth certificate')
    intent = _birth_intent_shape(certificate['intent'])
    d3._object(certificate['allocation'], ('authority', 'created_at'))
    authority = certificate['allocation']['authority']; d3._object(authority, ('directory', 'database', 'directory_binding', 'database_binding'))
    d3._require(authority['directory'] == intent['destination']['directory'] and authority['database'] == intent['destination']['database'], 'Birth allocation destination changed')
    for name in ('directory_binding', 'database_binding'): task_state._canonical_binding(authority[name])
    task_state._integer(certificate['allocation']['created_at'], 'original birth time')
    d3._object(certificate['baseline'], ('reference', 'content')); task_state._artifact_reference(certificate['baseline']['reference'])
    content = certificate['baseline']['content']
    d3._object(content, ('version','kind','source_contract','authority','project_id','created_at','runtime_digest','birth_expectation'))
    d3._require(type(content['version']) is int and content['version']==1 and type(content['source_contract']) is int
        and content['source_contract']==2 and content['kind']=='authority-source-baseline','Invalid original birth baseline content')
    d3._hash(content['runtime_digest'])
    d3._object(content['birth_expectation'], ('version','identity','intent_digest'))
    d3._require(type(content['birth_expectation']['version']) is int and content['birth_expectation']['version']==1,
        'Invalid original birth expectation version')
    d3._require(content['authority'] == authority and content['project_id'] == intent['project_id']
        and content['created_at'] == certificate['allocation']['created_at']
        and content.get('birth_expectation') == dict(version=1, identity=intent['identity'], intent_digest=d3._digest(intent)), 'Original birth expectation differs')
    d3._hash(certificate['seed_digest'])
    d3._require(certificate['seed_digest']==d3._digest(_birth_seed(None,intent)), 'Original birth seed certificate changed')
    return certificate


def _birth_seed(db, intent):
    rows = []
    for member in intent['members']:
        identity = member['physical_identity']; scope_id = d3._digest([3, intent['project_id'], member['repository_id'], member['checkout_id']])
        rows.append((scope_id, intent['project_id'], identity['repository_root'], identity['git_common_dir'], 0, json.dumps(identity, sort_keys=True)))
    return sorted(rows)


def _birth_store_inventory(context, db):
    from runtime import evidence
    rows=db.execute('select name,digest from evidence_metadata limit 4097').fetchall()
    d3._require(len(rows)<=4096,'Birth artifact inventory exceeds bound')
    folder=context.directory/'evidence';task_state._private(folder,True)
    d3._require({entry.name for entry in folder.iterdir()}=={row[0] for row in rows},'Unaccounted birth artifact stays protected')
    total=0
    for name,digest in rows:
        metadata=task_state._metadata(db,name);total+=metadata['bytes']
        d3._require(total<=d3.MAX_TOTAL,'Birth artifact inventory byte bound exceeded')
        evidence._managed_artifact(None,dict(name=name,digest=digest),context=context,db=db)


def _birth_current_history(record, expected):
    from runtime import review_storage
    with review_storage._capture_birth_history(record) as (history, reobserve):
        d3._require(history == expected, 'Original birth history changed')
        reobserve()


@contextmanager
def _birth_grant(host, stage, path, plan, request, journal, *, expect_absent=False):
    from runtime import review_storage, evidence
    birth = journal['birth']; certificate = birth['certificate']
    journal_file=path/'transaction.json'
    raw=d3._read_file(journal_file,MAX_JOURNAL,private=True) if journal_file.exists() else None
    d3._require(not expect_absent or raw is None,'Another private journal appeared before birth admission')
    facts = dict(operation='canonical-authority-birth', version=1, stage=stage, request=request, plan=plan,
        identity=birth['intent']['identity'], intent=birth['intent'], journal_digest=hashlib.sha256(raw).hexdigest() if raw is not None else None,
        directories=birth['directories'], allocation=birth['allocation'],
        baseline=birth['committed']['baseline'] if birth['committed'] else None,
        certificate_digest=d3._digest(certificate) if certificate else None)
    operation = _canonical_plain(host.owner._decision)['operation']
    with _acquire_d4_grant(host, facts) as grant:
        _public_entry(host,path,plan,request,operation)
        observed=d3._read_file(journal_file,MAX_JOURNAL,private=True) if journal_file.exists() else None
        d3._require(observed==raw,'Birth journal changed during host acquisition')
        intent=birth['intent'];destination=Path(intent['destination']['directory'])
        review_storage._location(destination);review_storage._check_parents(intent['destination']['parents'])
        for row in birth['directories']:
            if row['status']=='owned':d3._require(evidence._binding(Path(row['path']))==row['binding'],'Birth directory ownership changed')
        if destination.exists() and intent['destination']['directory_preimage']['status']=='absent':
            d3._require(any(row['path']==str(destination) and row['status']=='owned' and row['binding']==evidence._binding(destination)
                for row in birth['directories']),'Unowned birth destination appeared')
        elif intent['destination']['directory_preimage']['status']=='private-empty':
            d3._require(evidence._binding(destination)==intent['destination']['directory_preimage']['binding'],'Selected private directory changed')
        existing=[destination] if destination.exists() else []
        existing.extend(Path(peer['authority']['directory']) for peer in intent['peers'])
        d3._require(not existing or _held_authority_guards(existing),'Birth authority guards changed')
        for peer in intent['peers']:
            context=_recovery_context(peer['authority'])
            with evidence._context_database(context) as db:baseline=task_state._source_baseline(None,db,context=context)
            d3._require(baseline is not None and {key:_canonical_plain(baseline[key]) for key in ('reference','content')}==
                {key:peer['baseline'][key] for key in ('reference','content')},'Original birth peer baseline changed')
            if stage in ('directories','allocate','commit'):
                d3._require(_canonical_plain(baseline)==peer['baseline'],'Birth peer dependencies changed before creation')
                _,sources=_recovery_sources(context)
                allowed={d3._digest(item['authority']) for item in intent['peers']}
                d3._require(all(d3._digest(authority) in allowed for _,body in sources for authority in body['authorities']),
                    'Original birth peer closure changed')
        _birth_current_history(request['manifest']['enrollment']['record'],intent['history'])
        yield grant
        _public_entry(host,path,plan,request,operation)


def _prepare_birth(path, plan, request, host, owner, journal):
    from runtime import review_storage, evidence
    creation_decision = getattr(host.owner, '_creation', None)
    if creation_decision is None: return journal
    creation_decision = _canonical_plain(creation_decision); intent = creation_decision['intent']
    _birth_intent_shape(intent)
    d3._require(intent['identity'] == dict(plan_dir=str(path), transaction_id=request['manifest']['transaction_id'],
        manifest_digest=d3._digest(request['manifest'])), 'Birth transaction changed')
    record = request['manifest']['enrollment']['record']; directory = Path(intent['destination']['directory'])
    entries=_scoped_entries(record)
    if any(entries[(member['repository_id'],member['checkout_id'],client)][2]['activation']['status']=='active'
            for member in intent['members'] for client in member['clients']):
        raise Pending('Fresh authority supports configured enrollment only; native qualification remains unavailable')
    review_storage._location(directory); review_storage._check_parents(intent['destination']['parents'])
    _birth_current_history(record, intent['history'])
    if journal is None:
        actual = _birth_intent(plan, request['manifest'], intent['history'], plan_dir=path)
        d3._require(actual == intent and creation_decision['resume'] is None, 'Fresh birth decision changed')
        journal = dict(schema_version=2, transaction_id=request['manifest']['transaction_id'], manifest_digest=d3._digest(request['manifest']),
            manifest=deepcopy(request['manifest']), approval=deepcopy(request['approval']), plan=deepcopy(plan), state='prepared',
            receipts={}, enrollment=None, blocked=[], directories={}, observations=[],
            birth=dict(version=1, intent=intent, intent_digest=d3._digest(intent), phase='intent', directories=[], allocation=None, committed=None, certificate=None))
        # Actual host admission and complete guarded peer checks precede intent persistence.
        current, _ = d3._project_current(plan)
        d3._require(not d3._project_changes(plan,current), 'Birth plan inputs changed')
        for action in request['manifest']['actions']:
            for effect in action['effects']:
                d3._require(_fingerprint(Path(action['owner']['root']),effect['path'])==effect['before'], 'Birth effect preimage changed')
        locations=_scoped_runtime_locations([_scoped_prior(plan)[1],record])
        with _scoped_source_guards(locations,owner,request['manifest']) as (_,contexts):
            actual_peers=[]
            for context in contexts:
                with evidence._context_database(context) as db: baseline=task_state._source_baseline(None,db,context=context)
                actual_peers.append(dict(authority=_canonical_plain(context.authority_set[0]),baseline=_canonical_plain(baseline)))
            d3._require(sorted(actual_peers,key=lambda row:row['authority']['directory'])==intent['peers'], 'Birth peer closure changed')
            _birth_current_history(record,intent['history'])
            roots,git_roots=_scoped_writer_roots(request['manifest'])
            _scoped_writers(_scoped_writer_locations(locations,contexts),roots,git_roots,changed_paths=_scoped_changed_paths(request['manifest']))
            with _birth_grant(host, 'directories', path, plan, request, journal, expect_absent=True): _save(path, journal)
    birth = journal['birth']; d3._require(birth['intent'] == intent, 'Original birth intent changed')
    if birth['allocation'] is None and os.path.lexists(directory/'runtime.sqlite3'):
        raise Pending('Unreceipted birth database stays protected; manual reconciliation required')
    all_directories = [directory, *[Path(peer['authority']['directory']) for peer in intent['peers']]]
    if birth['certificate'] is not None:
        certificate = _birth_certificate_shape(birth['certificate']); context = _recovery_context(certificate['allocation']['authority'])
        with evidence._context_database(context) as db:
            baseline = task_state._source_baseline(None, db, context=context)
            _birth_store_inventory(context,db)
            d3._require(baseline is not None and {key: _canonical_plain(baseline[key]) for key in ('reference', 'content')} == certificate['baseline'], 'Original completed birth changed')
        with _authority_guards(all_directories,owner), _birth_grant(host, 'reconcile', path, plan, request, journal): pass
        return journal
    if birth['allocation'] is not None:
        context = _recovery_context(birth['allocation']['authority'])
        with evidence._context_database(context) as db:
            baseline = task_state._source_baseline(None, db, context=context)
            rows = sorted(db.execute('select * from checkouts').fetchall())
            _birth_store_inventory(context,db)
            d3._require(baseline is not None and rows == _birth_seed(db, intent), 'Incomplete birth stays protected; manual reconciliation required')
            for table in task_state.CANONICAL_TABLE_SQL:
                if table not in ('checkouts', 'evidence_metadata'): d3._require(db.execute('select 1 from '+table+' limit 1').fetchone() is None, 'Incomplete birth contains later state')
        original = {key: _canonical_plain(baseline[key]) for key in ('reference', 'content')}
        certificate = dict(version=1, intent=intent, allocation=birth['allocation'], baseline=original, seed_digest=d3._digest(rows))
        _birth_certificate_shape(certificate)
        birth.update(committed=dict(baseline=original, seed_digest=certificate['seed_digest']), certificate=certificate)
        with _authority_guards(all_directories,owner), _birth_grant(host, 'reconcile', path, plan, request, journal):
            birth['phase']='committed'; _save(path,journal)
        return journal
    with _authority_guards(all_directories, owner) as markers:
        for marker in markers: _atomic(marker.parent, marker.name+'.json', json.dumps(owner, sort_keys=True).encode())
        with _birth_grant(host, 'directories', path, plan, request, journal):
            for name in intent['destination']['missing_directories']:
                target = Path(name)
                receipt = next((row for row in birth['directories'] if row['path'] == name), None)
                if receipt is None:
                    d3._require(not os.path.lexists(target), 'Unowned birth directory stays protected')
                    receipt = dict(path=name, parents=review_storage._parents(target), preimage='absent', status='prepared', binding=None)
                    birth['directories'].append(receipt); _save(path,journal)
                    target.mkdir(mode=0o700)
                    review_storage._sync_directory(target.parent)
                    receipt.update(status='owned', binding=evidence._binding(target)); _save(path,journal)
                    _checkpoint('birth-directory')
                d3._require(receipt['status']=='owned' and evidence._binding(target)==receipt['binding'], 'Incomplete birth directory stays protected')
            birth['phase']='directories_owned'; _save(path,journal)
    with _authority_guards(all_directories, owner):
        d3._require(_held_authority_guards(all_directories), 'Birth physical authority guards are incomplete')
        d3._require(not os.path.lexists(directory/'runtime.sqlite3') and next(directory.iterdir(),None) is None, 'Unknown partial authority stays protected')
        directory_binding=evidence._binding(directory)
        with _birth_grant(host, 'allocate', path, plan, request, journal) as grant, review_storage._capture_birth_history(record) as (initial_history, reobserve_history):
            _check_d4_grant(grant)
            d3._require(initial_history==intent['history'],'Birth history changed before initialization')
            d3._require(evidence._binding(directory)==directory_binding,'Birth directory changed during allocation admission')
            def initialize(db, creation):
                _check_d4_grant(grant); task_state._check_creation(directory/'runtime.sqlite3',creation,db)
                birth.update(phase='allocated', allocation=dict(authority=_canonical_plain(creation.authority), created_at=creation.created_at))
                _save(path,journal); _checkpoint('birth-allocated')
                with _birth_grant(host, 'commit', path, plan, request, journal) as commit_grant:
                    _birth_current_history(record,intent['history'])
                    context=evidence._existing_authority_context(_canonical_plain(creation.authority),db=db)
                    evidence._register_source_baseline(None,db,creation,context=context,grant=commit_grant)
                    db.executemany('insert into checkouts values(?,?,?,?,?,?)',_birth_seed(db,intent))
                    reobserve_history()
                    _checkpoint('birth-before-commit')
            creation=task_state._initialize(directory/'runtime.sqlite3',version=3,expected_directory=directory_binding,initialize_metadata=initialize)
            try:
                task_state._check_creation(directory/'runtime.sqlite3',creation)
                reobserve_history()
            finally: os.close(creation.descriptor)
        _checkpoint('birth-committed')
        context=_recovery_context(birth['allocation']['authority'])
        with evidence._context_database(context) as db:
            baseline=task_state._source_baseline(None,db,context=context); rows=sorted(db.execute('select * from checkouts').fetchall())
            d3._require(rows==_birth_seed(db,intent),'Birth physical seed changed')
        original={key:_canonical_plain(baseline[key]) for key in ('reference','content')}
        certificate=dict(version=1,intent=intent,allocation=birth['allocation'],baseline=original,seed_digest=d3._digest(rows))
        _birth_certificate_shape(certificate)
        birth.update(phase='committed',committed=dict(baseline=original,seed_digest=certificate['seed_digest']),certificate=certificate)
        _save(path,journal)
    return journal


def _public_new_subject_witness(context, db, plan, manifest, key, *, journal=None):
    """Eligibility only; the existing independent bookkeeping grant still precedes publication."""
    from runtime import evidence
    d3._require(_held_authority_guards([context.directory]),'New subject admission requires its actual store guard')
    evidence._check_context(context,db)
    _,prior=_scoped_prior(plan);record=manifest['enrollment']['record']
    previous=_scoped_entries(prior);configuration=_scoped_entries(record)[key][2]['configuration']
    destination=dict(schema_version=3,state_dir=str(context.directory))
    d3._require(prior is not None and prior.get('schema_version')==2 and key not in previous
        and all(prior[name]==record[name] for name in ('project_id','project_root','project_binding')),
        'New subject admission requires the same actual canonical Project')
    d3._require(configuration is not None and configuration['runtime']==destination
        and any(entry['configuration'] is not None and entry['configuration']['runtime']==destination
            for _,_,entry in previous.values()),'No prior canonical member owns the new subject destination')
    baseline=task_state._source_baseline(None,db,context=context)
    runtime=task_state.runtime_fingerprint()
    d3._require(baseline is not None and baseline['content']['source_contract']==2
        and baseline['content']['project_id']==record['project_id'],
        'New subject destination lacks its original same-Project source baseline')
    d3._require(baseline['content']['runtime_digest']==runtime==configuration['runtime_digest']
        ==manifest['pins']['package_pins']['runtime_digest'],
        'New subject destination runtime generation differs from the installed approved generation')
    if journal is not None:
        d3._require(_scoped_owned_authority(journal),'New subject recovery authority is outside its owned transition')
    permission=_d4_taskless_permission(journal or dict(plan=plan,manifest=manifest),context,db,record['project_id'])
    for proof in permission['permitted']:
        witness=tuple(proof['subject'][name] for name in ('repository_id','checkout_id','client'))
        before=previous.get(witness)
        if before is None or before[2]['configuration'] is None or before[2]['configuration']['runtime']!=destination:continue
        if proof['identity']!=_scoped_projection(prior,witness):continue
        if proof['phase']=='current' or journal is not None and _d4_owned_transition(journal,witness):return
    raise Pending('New subject destination has no exact prior physical ownership witness')


def _public_existing_authorities(locations, contexts, plan, manifest, *, journal=None):
    """Require every selected store and every discovered original baseline."""
    from runtime import evidence
    _, prior = _scoped_prior(plan)
    birth = journal.get('birth') if journal else None
    if (not prior or prior.get('schema_version') != 2) and not (birth and birth['certificate']) or not locations:
        raise Pending('Canonical runtime preparation is required before mutation')
    observed = {context.directory: context for context in contexts}
    if not set(locations) <= set(observed) or any(version != 3 for _, version in locations.values()):
        raise Pending('Complete existing canonical runtime authorities are required before mutation')
    previous = _scoped_entries(prior)
    for member in manifest['enrollment']['affected']:
        key = tuple(member[name] for name in ('repository_id', 'checkout_id', 'client'))
        entry = previous.get(key)
        config = entry[2]['configuration'] if entry else None
        if entry is None and not (birth and birth['certificate']):
            configuration=_scoped_entries(manifest['enrollment']['record'])[key][2]['configuration']
            runtime=configuration['runtime'] if configuration else None
            directory=Path(runtime['state_dir']) if runtime and runtime['schema_version']==3 else None
            d3._require(directory in observed,'New subject requires an observed existing canonical destination')
            with evidence._context_database(observed[directory]) as db:
                _public_new_subject_witness(observed[directory],db,plan,manifest,key,journal=journal)
            continue
        if (not config or not config['runtime'] or config['runtime']['schema_version'] != 3) and not (birth and birth['certificate']):
            raise Pending('Canonical runtime preparation is required for an affected subject')
    authorities = []
    for context in contexts:
        with evidence._context_database(context) as db:
            baseline = task_state._source_baseline(None, db, context=context)
        if baseline is None or baseline['content']['source_contract'] != 2:
            raise Pending('An original source-contract-2 baseline is required before mutation')
        project_id = baseline['content']['project_id']
        if baseline['content'].get('birth_expectation') is not None and not (birth and birth['certificate']
                and birth['certificate']['allocation']['authority']==_canonical_plain(context.authority_set[0])):
            from runtime import review_storage
            with evidence._context_database(context) as db: handoff=evidence._read_birth_handoff(context,db)
            review_storage._retained_birth_history(handoff['certificate'])
        if context.directory in locations and project_id != manifest['enrollment']['record']['project_id']:
            raise Pending('Selected canonical authority belongs to another Project')
        authorities.append(dict(authority=_canonical_plain(context.authority_set[0]), project_id=project_id))
    return authorities


# Internal schema-2 path; public startup/transaction activation remains gated.
def _scoped_entries(record):
    if record is None or record.get("schema_version") != 2:
        return {}
    return {(r["repository_id"], c["checkout_id"], e["client"]): (r, c, e)
            for r in record["repositories"] for c in r["checkouts"] for e in c["clients"]}


def _scoped_projection(record, key):
    from runtime.startup import canonical_scope_projection, decode_project_record, _canonical_plain
    return _canonical_plain(canonical_scope_projection(decode_project_record(record),
        repository_id=key[0], checkout_id=key[1], client=key[2]))


def _canonical_record(record, plan, *, current):
    from runtime.startup import decode_project_record
    decode_project_record(record)
    d3._require(all(record[k] == plan["identity"][k] for k in ("project_id", "project_root", "kind"))
                and record["project_binding"] == plan["identity"]["binding"], "Canonical Project binding changed")
    entries = _scoped_entries(record)
    for assessment in plan["assessments"]["repositories"]:
        owner, identity = assessment["owner"], assessment["identity"]
        repo = next((r for r in record["repositories"] if r["repository_id"] == owner["repository_id"]), None)
        selected = next((c for c in repo["checkouts"] if c["checkout_id"] == owner["checkout_id"]), None) if repo else None
        d3._require(repo is not None and selected is not None and all(repo[k] == owner[k] for k in ("primary_root", "git_common_dir"))
                    and selected["root"] == owner["root"] and selected["git_dir"] == owner["git_dir"]
                    and repo["binding"] | selected["binding"] == identity["binding"], "Canonical selected owner changed")
        if current:
            for key, (_, _, entry) in entries.items():
                if key[:2] == (owner["repository_id"], owner["checkout_id"]) and entry["configuration"] is not None:
                    d3._require(all(entry["configuration"][k] == plan["package_pins"][k] for k in ("policy_digest", "runtime_digest")),
                                "Canonical selected configuration pins are stale")
    return record


def _scoped_originals(plan):
    originals, dependencies, order = {}, {}, []
    for assessment in d3._assessments(plan["assessments"]):
        previous = None
        for original in assessment["actions"]:
            identity = d3._qualified(assessment["owner"], original["id"])
            originals[identity] = original
            dependencies[identity] = set(original["dependencies"]) | ({previous} if previous else set())
            order.append(identity)
            previous = identity
    result, done = [], set()
    while len(done) != len(order):
        identity = next((i for i in order if i not in done and dependencies[i] <= done), None)
        d3._require(identity is not None, "Scoped action dependencies are incomplete or cyclic")
        result.append(identity)
        done.add(identity)
    return originals, result, dependencies


def _scoped_prior(plan):
    authority = next(a for a in plan["identity"]["authority"] if a["path"] == plan["project_scope"]["authority"]["path"])
    d3._require(authority.get("target") is not None and not authority["error"], "Complete authority parent observations are required")
    return authority, authority["record"]


def _file_configuration(manifest, plan):
    """File-only configured records do not create or operate a managed runtime."""
    _, prior = _scoped_prior(plan)
    records = [record for record in (prior, manifest['enrollment']['record']) if record]
    if any(record.get('schema_version') != 2 for record in records):
        return False
    current = manifest['enrollment']['record']
    old, new = _scoped_entries(prior), _scoped_entries(current)
    if prior and prior['shared_policy'] != current['shared_policy']:
        return False
    changed = _scoped_changed_paths(manifest) - {Path(current['project_root']) / PROJECT_FILE}
    if any(Path(item['path']) in changed for record in records for item in record['shared_policy']):
        return False
    affected = set(old) ^ set(new)
    affected.update(key for key in old.keys() & new.keys()
        if _scoped_projection(prior, key) != _scoped_projection(current, key)
        or old[key][2]['activation'] != new[key][2]['activation'])
    # Runtime-bearing membership changes retain existing-store admission.
    runtime_members_changed = any(entry['configuration'] and entry['configuration']['runtime']
        for entries in (old, new) for key, (_, _, entry) in entries.items() if key in affected)
    shared_roots = [Path(action['owner']['root']) for action in manifest['actions']
        if action['owner']['kind'] == 'project' and any(effect['before'] != effect['after'] for effect in action['effects'])]
    for entries in (old, new):
        for key, (_, checkout, entry) in entries.items():
            if key in affected and entry['configuration'] and entry['configuration']['shared_dependencies']:
                return False
            if (key in affected or runtime_members_changed or any(path.is_relative_to(Path(checkout['root'])) for path in changed)
                    or any(Path(checkout['root']).is_relative_to(root) for root in shared_roots)) \
                    and entry['activation']['status'] != 'configured':
                return False
    originals, _, _ = _scoped_originals(plan)
    return all(originals[action['id']]['kind'] in ('write', 'delete')
               and action['recovery'] == 'file-preimages' and action['execution'] is None
               for action in manifest['actions'])


def _validate_scoped(request, plan, *, operation=None):
    d3._bounded(request)
    d3._project_plan_shape(plan)
    d3._require(plan["schema_version"] == 2 and plan["review_digest"] == d3._review_digest(plan), "Frozen scoped proposal changed")
    d3._object(request, ("schema_version", "manifest", "approval"), ("observations",))
    m = request["manifest"]
    d3._object(m, ("schema_version", "transaction_id", "plan", "pins", "authorization_reference", "approved_scope", "owners", "actions", "enrollment"))
    if d3._relevant_inspection(plan['project_scope']):
        d3._require(_file_configuration(m, plan) and not any(entry['configuration'] and entry['configuration']['runtime']
            for _, _, entry in _scoped_entries(m['enrollment']['record']).values()),
            'Relevant inspection supports only file configuration; use a full inspection for managed execution')
    d3._require(type(request["schema_version"]) is int and request["schema_version"] == 2 and type(m["schema_version"]) is int and m["schema_version"] == 2,
                "Unsupported scoped transaction schema")
    d3._require(isinstance(m["transaction_id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", m["transaction_id"]), "Invalid transaction ID")
    d3._require(m["plan"] == {k: plan[k] for k in ("plan_id", "revision", "review_digest", "current_input_digest")}
        and m["pins"] == dict(package_pins=plan["package_pins"], toolkit_commit=plan["sources"]["declared"]["toolkit"]["commit"], sources_digest=d3._digest(plan["sources"])),
        "Scoped plan/source provenance changed")
    d3._require(operation in ('apply', 'resume', 'rollback') and _file_configuration(m, plan)
        or plan['package_pins'] == d3._package_pins(), 'Scoped executing package changed')
    d3._require(isinstance(m["actions"],list) and all(isinstance(a,dict) and isinstance(a.get("effects"),list) and isinstance(a.get("preconditions"),list) for a in m["actions"])
                and sum(len(a["effects"])+len(a["preconditions"]) for a in m["actions"]) <= d3.MAX_PATHS, "Scoped effect/precondition limit exceeded")
    expected_owners = [{k: a[k] for k in ("owner", "identity")} for a in d3._assessments(plan["assessments"])]
    d3._require(m["owners"] == expected_owners, "Frozen scoped owners changed")
    originals, order, dependencies = _scoped_originals(plan)
    d3._strings(m["approved_scope"])
    d3._require(set(m["approved_scope"]) <= set(order) | {"enrollment"} and "enrollment" in m["approved_scope"], "Unknown or missing scoped approval")
    approval = request["approval"]
    d3._object(approval, ("reference", "evidence", "manifest_digest"))
    d3._text(m["authorization_reference"])
    d3._require(approval["reference"] == m["authorization_reference"] and approval["manifest_digest"] == d3._digest(m), "Approval differs from exact scoped manifest")
    d3._require(_evidence(approval["evidence"], many=True), "Trusted session approval evidence is required")
    d3._require(isinstance(m["actions"], list) and [a.get("id") for a in m["actions"]] == order, "Scoped action order differs from stable dependency order")
    physical, groups, last = [], set(), None
    for action in m["actions"]:
        d3._object(action, ("id", "owner", "group", "resources", "preconditions", "effects", "recovery", "execution"))
        original, owner = originals[action["id"]], action["owner"]
        d3._require(owner == original["owner"] and action["recovery"] in ("file-preimages", "manual"), "Scoped action owner/recovery changed")
        d3._text(action["group"]); d3._strings(action["resources"])
        if last != action["group"]:
            d3._require(action["group"] not in groups, "Scoped action groups must be contiguous")
            groups.add(action["group"]); last = action["group"]
        for condition in action["preconditions"]:
            d3._object(condition, ("path", "fingerprint")); _target(condition["path"]); _fp_schema(condition["fingerprint"])
        for effect in action["effects"]:
            d3._object(effect, ("path", "before", "after", "target") if original["kind"] == "command" else ("path", "before", "after"))
            _fp_schema(effect["before"]); _fp_schema(effect["after"])
            if not (original["kind"] == "write" and original["target"] == str(PROJECT_FILE) and effect["path"] == str(PROJECT_FILE)):
                _target(effect["path"])
            if original["kind"] == "command":
                from runtime.startup import _canonical_target
                _canonical_target(effect["target"])
                d3._require(effect["target"]["path"] == str(Path(owner["root"])/effect["path"])
                    and effect["target"]["parents"].get(owner["root"]) == next(a["identity"]["binding"]["root"] for a in m["owners"] if a["owner"] == owner)
                    and all(Path(p).is_relative_to(Path(owner["root"])) for p in effect["target"]["parents"]), "Command effect target is outside its selected owner")
            location = Path(owner["root"]) / effect["path"]
            d3._require(all(not location.is_relative_to(p) and not p.is_relative_to(location) for p in physical), "Scoped effects overlap")
            physical.append(location)
        if original["kind"] in ("write", "delete"):
            d3._require(len(action["effects"]) == 1 and action["execution"] is None, "Scoped file action needs one effect")
            effect = action["effects"][0]
            d3._require(effect["path"] == original["target"] and (effect["before"]["sha256"] if effect["before"] else "absent") == original["before"]
                and (effect["after"]["sha256"] if effect["after"] else "absent") == original["after"], "Scoped file postimage differs from proposal")
        elif original["kind"] == "command":
            e = action["execution"]
            d3._object(e, ("argv", "cwd", "cwd_target", "timeout_seconds", "qualification", "idempotency"))
            assessment = next(a for a in d3._assessments(plan["assessments"]) if a["owner"] == owner)
            d3._require(e["argv"] == original["text"] and e["cwd"] == original["cwd"]
                and e["cwd_target"] == assessment["identity"]["targets"].get("cwd:" + original["cwd"])
                and e["cwd_target"] is not None and type(e["timeout_seconds"]) is int and 1 <= e["timeout_seconds"] <= 300, "Scoped command cwd or execution changed")
            _evidence(e["qualification"]); _evidence(e["idempotency"])
        else:
            d3._require(action["execution"] is None, "External actions require manual operation")
    enrollment = m["enrollment"]
    d3._object(enrollment, ("record", "before", "affected", "requires", "qualification"))
    record = _canonical_record(enrollment["record"], plan, current=True)
    d3._require(all(record["membership"][k] == plan["project_scope"]["membership"][k] for k in ("include","exclude","future_join")), "Canonical membership differs from frozen Project scope")
    d3._require(all(isinstance(enrollment[k],list) and len(enrollment[k]) <= d3.MAX_PATHS for k in ("affected","requires","qualification")), "Scoped enrollment row limit exceeded")
    for member in enrollment["affected"]: d3._object(member,("repository_id","checkout_id","client"))
    authority, prior = _scoped_prior(plan)
    expected_before = dict(revision=prior.get("revision") if prior else None,
        raw_sha256=authority["fingerprint"].get("sha256"), target=authority["target"])
    d3._require(enrollment["before"] == expected_before, "Scoped authority preimage changed")
    old, new = _scoped_entries(prior), _scoped_entries(record)
    d3._require(set(old) <= set(new), "Removed members must retain tombstone/history entries")
    affected = {key for key in new if key not in old or _scoped_projection(prior, key) != _scoped_projection(record, key)
                or old[key][2]["activation"] != new[key][2]["activation"]}
    declared = [tuple(a[k] for k in ("repository_id", "checkout_id", "client")) for a in enrollment["affected"]]
    d3._require(declared == sorted(affected), "Scoped affected set is incomplete or excessive")
    selected = {(a["owner"]["repository_id"], a["owner"]["checkout_id"]) for a in plan["assessments"]["repositories"]}
    d3._require(all(k[:2] in selected and k[2] in plan["sources"]["declared"]["clients"] for k in affected), "Shared dependency update lacks an affected member/client assessment")
    if prior and prior.get("schema_version") == 2:
        for previous in prior["repositories"]:
            retained = next((r for r in record["repositories"] if r["repository_id"] == previous["repository_id"]),None)
            d3._require(retained is not None and all(previous[k] == retained[k] for k in ("primary_root","git_common_dir","binding")), "Historical Repository identity/tombstone was discarded")
            d3._require(previous["membership"] == retained["membership"] or previous["repository_id"] in {o[0] for o in selected}, "Unselected membership tombstone changed")
            for checkout in previous["checkouts"]:
                kept = next((c for c in retained["checkouts"] if c["checkout_id"] == checkout["checkout_id"]),None)
                d3._require(kept is not None and all(checkout[k] == kept[k] for k in ("root","git_dir","binding")), "Historical checkout identity was discarded")
    for key in set(old) - affected:
        before_repo,before_checkout,before_entry = old[key]
        after_repo,after_checkout,after_entry = new[key]
        d3._require(before_entry == after_entry and {k:v for k,v in before_repo.items() if k != "checkouts"} == {k:v for k,v in after_repo.items() if k != "checkouts"}
            and {k:v for k,v in before_checkout.items() if k != "clients"} == {k:v for k,v in after_checkout.items() if k != "clients"}, "Unrelated member identity/configuration/receipts changed")
    suspend = bool(prior and (prior.get("schema_version") == 1 or any(old[k][2]["activation"]["status"] == "active" for k in affected if k in old)))
    d3._require(record["revision"] == (prior.get("revision", 0) if prior else 0) + (2 if suspend else 1), "Scoped final revision must match frozen write sequence")
    d3._strings(enrollment["requires"])
    mandatory = set()
    for assessment in plan["assessments"]["repositories"]:
        for row in assessment["coverage"]:
            if row["mandatory"] and row["applicability"] == "applicable": mandatory.update(row["actions"])
    while True:
        expanded = mandatory | {d for i in mandatory for d in dependencies[i]}
        if expanded == mandatory: break
        mandatory = expanded
    d3._require(mandatory <= set(enrollment["requires"]) <= set(order), "Scoped enrollment omits mandatory dependencies")
    for action in m["actions"]:
        if originals[action["id"]]["target"] == str(PROJECT_FILE):
            d3._require(Path(action["owner"]["root"]) == Path(record["project_root"]) and originals[action["id"]]["kind"] == "write"
                and d3.read_request(originals[action["id"]]["text"].encode()) == record, "Canonical enrollment differs from exact proposal")
    shared_before = {s["path"]:s["sha256"] for s in prior.get("shared_policy",[])} if prior else {}
    shared_after = {s["path"]:s["sha256"] for s in record["shared_policy"]}
    for action in m["actions"]:
        for effect in action["effects"]:
            location=str(Path(action["owner"]["root"])/effect["path"])
            if location in shared_before or location in shared_after:
                d3._require(shared_after.get(location) == (effect["after"]["sha256"] if effect["after"] else None), "Shared resource postimage is missing from selected dependency projections")
    qkeys = []
    for row in enrollment["qualification"]:
        d3._object(row, ("repository_id", "checkout_id", "client", "requirements", "security_required"))
        key = tuple(row[k] for k in ("repository_id", "checkout_id", "client")); qkeys.append(key)
        assessment = next(a for a in plan["assessments"]["repositories"] if (a["owner"]["repository_id"],a["owner"]["checkout_id"]) == key[:2])
        required = {r["id"] for r in assessment["coverage"] if r["mandatory"] and r["applicability"] == "applicable"}
        d3._strings(row["requirements"])
        d3._require(type(row["security_required"]) is bool and required <= set(row["requirements"]) <= {r["id"] for r in plan["requirements"]}, "Scoped qualification requirements incomplete")
    d3._require(sorted(qkeys) == sorted(affected), "Scoped member/client qualification scope changed")
    return originals, dependencies, affected, suspend


def _scoped_target(root, relative):
    return d3._physical_target(root, root / relative, d3._fingerprint(root, relative), {"paths": 0, "bytes": 0})


def _scoped_runtime_locations(records, affected=None):
    locations = {}
    for record in records:
        if not record: continue
        if record.get("schema_version") == 1:
            rows = [(record["project_id"], record["repository_root"], record["git_common_dir"], record["policy_digest"], record.get("runtime"))]
        else:
            rows = [(record["project_id"], c["root"], r["git_common_dir"], e["configuration"]["policy_digest"], e["configuration"]["runtime"])
                    for key, (r, c, e) in _scoped_entries(record).items() if e["configuration"] and (affected is None or key in affected)]
        for project, root, common, policy, runtime in rows:
            if runtime is None: continue
            # This is a historical storage locator, not a fabricated active canonical scope.
            settings = runtime_settings({"runtime": dict(schema_version=2, state_dir=runtime["state_dir"])}, d3.PACKAGE, Path(common))
            locations[settings.state_dir] = (ProjectScope(project, Path(root), Path(common), policy, settings), runtime["schema_version"])
    return locations


def _scoped_writer_roots(manifest):
    owners = [action['owner'] for action in manifest['actions']
        if any(effect['before'] != effect['after'] for effect in action['effects'])]
    affected = {(member['repository_id'], member['checkout_id']) for member in manifest['enrollment']['affected']}
    owners.extend(item['owner'] for item in manifest['owners'] if item['owner']['kind'] == 'repository'
        and (item['owner']['repository_id'], item['owner']['checkout_id']) in affected)
    git_roots = {Path(owner['git_common_dir']) for owner in owners if owner['kind'] == 'repository'}
    return {Path(owner['root']) for owner in owners} | git_roots, git_roots


def _scoped_changed_paths(manifest):
    # Fingerprints include mode and existence, not just the content digest.
    paths={Path(action['owner']['root'])/effect['path'] for action in manifest['actions']
        for effect in action['effects'] if effect['before']!=effect['after']}
    # The approved canonical revision always changes; publication/recovery can also create/delete it.
    paths.add(Path(manifest['enrollment']['record']['project_root'])/PROJECT_FILE)
    return paths


def _scoped_task_relevant(checkout_row,roots,task=None,changed_paths=()):
    physical=Path(checkout_row[0]) in roots or any(Path(checkout_row[1])==root or root.is_relative_to(Path(checkout_row[1])) for root in roots)
    historical=task.get('accepted_identity',{}).get('shared_policy',[]) if task is not None else []
    return physical or any(Path(dependency['path']) in changed_paths for dependency in historical)


def _scoped_writers(locations, affected_roots, git_roots=(), *, changed_paths=(), live_only=False):
    from runtime import delegation, evidence
    claims = [dict(kind="path", key=str(root), mode="write") for root in affected_roots] + [dict(kind="git", key=str(root), mode="write") for root in git_roots]
    # D4's validated finite Project scope has its own bound, separate from assignment claim budgets.
    claims = delegation._claims(claims, maximum=len(claims)) if claims else []
    for directory, (locator, version) in locations.items():
        if version == 1:
            if _legacy_writers(replace(locator, runtime=replace(locator.runtime, schema_version=1))):
                raise Pending("Affected legacy writers remain unreconciled")
            continue
        selected = locator if isinstance(locator,evidence.EvidenceAuthorityContext) else None
        path = selected.database if selected else task_state._path(locator)
        if not os.path.lexists(path):
            d3._require(selected is None,'Discovered writer physical authority unavailable')
            continue
        task_state._sidecars(path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
            db.execute("pragma trusted_schema=off"); db.execute("begin")
            task_state._schema(db, version=version)
            context = selected or (evidence._existing_authority_context(task_state._authority_identity(path), db=db) if version == 3 else None)
            if context: evidence._check_context(context,db)
            if live_only:
                rows = db.execute("select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments "
                    "where json_extract(data,'$.status') is not 'settled' limit 4097").fetchall()
                d3._require(len(rows) <= 4096, 'Live assignment inspection bound exceeded')
                records = [delegation._record(locator, row, db=db, context=context) for row in rows]
            else:
                records = delegation._ledger(locator, db, context=context)
            if any(r["data"]["status"] != "settled" and delegation._held_conflict(claims, r["data"]["claims"]) for r in records):
                raise Pending("Affected foreign or uncertain assignment remains unreconciled")
            condition = " where json_extract(data,'$.lifecycle.safe_closed_at') is null" if live_only else ""
            rows = db.execute("select scope_id,client,session_id,task_id from tasks" + condition + " limit 4097").fetchall()
            d3._require(len(rows) <= 4096, "Writer history exceeds safe inspection bound")
            for scope_id, client, session, task_id in rows:
                task = task_state._task(db, scope_id, client, session, task_id)
                row = db.execute("select repository_root,git_common_dir from checkouts where scope_id=?", (scope_id,)).fetchone()
                d3._require(row is not None, "Historical writer has no checkout")
                unresolved = [a for a in task.get("evidence", {}).get("attempts", []) if a["status"] in {"reserved", "running", "uncertain"}]
                retirement = task.get("retirement")
                relevant = _scoped_task_relevant(row,affected_roots,task,changed_paths)
                if retirement and (relevant or unresolved):
                    proof = evidence._read(None if context else locator, retirement["proof"], context=context, db=db)
                    d3._require(task["state"] == "CANCELLED" and proof.get("kind") == "stopped-writer-reconciliation"
                        and proof.get("stopped") is True and proof.get("descendants_stopped") is True
                        and proof.get("identity") == {k: task[k] for k in ("client", "session_id", "task_id", "scope_ref")}
                        and proof.get("expected_pins") == task["pins"] and proof.get("expected_revision") == retirement["previous_revision"] == task["revision"] - 1
                        and proof.get("observed_turn_id") == task["turn_id"] and proof.get("attempt_ids") == [a["id"] for a in unresolved],
                        "Historical stopped-writer proof does not match original pins/identity")
                elif task.get('writer_stop') and (relevant or unresolved):
                    task_state._ready_stop(context,db,task,observe=True)
                elif relevant and task["mode"] == "editable":
                    raise Pending("Affected editable task lacks exact stopped-writer retirement proof")
                elif any(delegation._linked_attempt(locator, scope_id, task, a, records, context=context, db=db) is None for a in unresolved):
                    raise Pending("Unlinked or foreign task attempt retains unknown writer ownership")


_RECOVERY_ISSUER = object()
_RECOVERY_RELEASE_ISSUER = object()
_D4_HOST_BOOTSTRAP_ISSUER = object()
_D4_HOST_ISSUER = object()


@dataclass(frozen=True)
class _D4HostCapability:
    owner: object
    issuer: object
    state: object


@dataclass(frozen=True)
class _D4HostGrant:
    capability: object
    acquisition: object
    facts: object
    issuer: object


@contextmanager
def _bootstrap_d4_host(owner, *, issuer):
    """Trusted integration only: the caller independently verifies user/session authority."""
    d3._require(issuer is _D4_HOST_BOOTSTRAP_ISSUER and callable(getattr(owner,'acquire_d4_bookkeeping',None)),
        'D4 host bootstrap requires its independently trusted integration owner')
    state=dict(active=True,capability=None)
    capability=_D4HostCapability(owner,_D4_HOST_ISSUER,state);state['capability']=capability
    try:yield capability
    finally:state['active']=False


def _check_d4_grant(grant):
    d3._require(isinstance(grant,_D4HostGrant),'Actual D4 host grant is required')
    capability=grant.capability;acquisition=grant.acquisition
    d3._require(isinstance(capability,_D4HostCapability) and capability.issuer is _D4_HOST_ISSUER
        and capability.state['active'] and capability.state['capability'] is capability
        and grant.issuer is _D4_HOST_ISSUER and acquisition['active'] and acquisition['grant'] is grant
        and acquisition['capability'] is capability and acquisition['facts'] is grant.facts,
        'D4 host grant is foreign, reconstructed or expired')


@contextmanager
def _acquire_d4_grant(host,facts):
    if not isinstance(host,_D4HostCapability) or host.issuer is not _D4_HOST_ISSUER or not host.state['active'] or host.state['capability'] is not host:
        raise Pending('Actual trusted D4 host authority is unavailable; bookkeeping remains pending')
    frozen=_canonical_freeze(facts)
    acquisition=dict(active=True,capability=host,facts=frozen,grant=None)
    def issue(approved):
        d3._require(acquisition['active'] and acquisition['grant'] is None and _canonical_plain(approved)==facts,
            'D4 host approval differs from exact acquired subjects/sources/transitions')
        grant=_D4HostGrant(host,acquisition,frozen,_D4_HOST_ISSUER);acquisition['grant']=grant
        return grant
    try:
        grant=host.owner.acquire_d4_bookkeeping(frozen,issue)
        _check_d4_grant(grant)
        d3._require(grant.acquisition is acquisition,'D4 host returned a copied or stale acquisition')
        yield grant
    finally:acquisition['active']=False


@dataclass(frozen=True)
class _RecoverySourceObservation:
    content: object
    contexts: tuple
    generations: object
    created_at: int
    issuer: object
    release: object = None
    ready_inputs: object = None
    provenance: object = None
    birth_certificate: object = None


def _stop_revision(task):
    return task['retirement']['previous_revision'] if task.get('state')=='CANCELLED' else task.get('writer_stop',{}).get('task_revision',task['revision'])


def _stop_envelope(task):
    return task['writer_stop'] if task.get('writer_stop') else task['retirement']


def _barrier_facts(context,db,task,record,rec):
    from runtime import evidence
    task_state._fields(rec,('version','kind','barrier_id','verification','revision'))
    task_state._integer(rec['revision'],'original synchronous revision',2)
    verification=evidence._read(None,rec['verification'],context=context,db=db)
    operation=evidence._read(None,record['body']['operation'],context=context,db=db)
    task_state._fields(verification,('version','candidate_digest','plan_digest','purpose','generation','blockers','reuse','security_impact','resource_closure','barrier_id'))
    task_state._integer(verification['generation'],'original verification generation')
    d3._hash(verification['candidate_digest']);d3._hash(verification['plan_digest'])
    d3._require(type(verification['version']) is int and verification['version']==1 and
        verification['purpose'] in {'completion','merge-evidence'} and isinstance(verification['blockers'],list) and
        verification['plan_digest']==record['data']['plan_digest'] and rec['revision']==record['data']['revision']+1,
        'Synchronous barrier does not retain its actual original verification transition')
    outcome='failed' if verification['blockers'] else 'completed'
    d3._require(type(rec['version']) is int and rec['version']==1 and rec['kind']=='synchronous-verification' and
        rec['barrier_id']==record['id']==verification['barrier_id'] and rec['revision']<=_stop_revision(task) and
        operation['kind']=='completion-verification' and verification['resource_closure']==record['data']['claims'] and
        record['data']['result']==dict(outcome=outcome),'Original synchronous barrier differs from independent stopped owner')
    return outcome


def _barrier_certificate(context,db,task,record,rec):
    task_state._ready_stop(context,db,task)
    return _barrier_certificate_fields(context,db,task,record,rec)


def _barrier_certificate_fields(context,db,task,record,rec):
    outcome=_barrier_facts(context,db,task,record,rec)
    data=record['data']
    return dict(version=2,reference=dict(authority=_canonical_plain(context.authority_set[0]),id=record['id'],owner=record['owner'],assignment=data['assignment']),
        created_at=data['lifecycle']['created_at'],request_digest=data['request_digest'],role=data['role'],claims=data['claims'],
        reconciliation=dict(version=2,kind='synchronous-verification-stop',reference=data['reconciliation'],body=rec,outcome=outcome,
            stop_digest=task['writer_stop']['proof']['digest'],bindings=dict(operation=record['body']['operation'],verification=rec['verification'],candidate=data['candidate'])))


def _assignment_certificate_shape(value, task):
    from runtime import delegation, evidence
    task_state._fields(value,('reference','created_at','request_digest','role','claims','reconciliation'),('version',))
    ref=value['reference']; task_state._fields(ref,('authority','id','owner','assignment'))
    owner=ref['owner']; task_state._retired_identity('assignment',owner); task_state._artifact_reference(ref['assignment'])
    d3._require(isinstance(ref['id'],str) and re.fullmatch(r'[0-9a-f]{32}',ref['id']) and
        ref['authority']==task['reference']['authority'] and all(owner[k]==task['reference'][k] for k in
        ('scope_id','client','session_id','task_id')),'Stopped assignment original owner differs')
    task_state._integer(value['created_at'],'original stopped assignment age'); d3._hash(value['request_digest'])
    delegation._stored_claims(value['claims'],maximum=delegation.MAX_VERIFICATION_CLAIMS)
    role=value['role']; d3._require(role in {'coordinator','check','worker','review'},'Unknown stopped assignment role')
    rec=value['reconciliation']
    if 'version' in value:
        d3._require(type(value['version']) is int and value['version']==2 and task.get('writer_stop') and task.get('state') in {'DONE','REVIEW','CANCELLED'} and role=='coordinator',
            'Barrier certificate needs its independently stopped complete owner')
        task_state._fields(rec,('version','kind','reference','body','outcome','stop_digest','bindings'))
        task_state._artifact_reference(rec['reference']);body=rec['body']
        task_state._fields(body,('version','kind','barrier_id','verification','revision'))
        task_state._integer(body['revision'],'synchronous barrier revision',2)
        task_state._fields(rec['bindings'],('operation','verification','candidate'))
        for ref in rec['bindings'].values():
            if ref is not None:task_state._artifact_reference(ref)
        d3._require(type(rec['version']) is int and rec['version']==2 and rec['kind']=='synchronous-verification-stop' and
            type(body['version']) is int and body['version']==1 and body['kind']=='synchronous-verification' and
            body['barrier_id']==value['reference']['id'] and body['revision']<=_stop_revision(task) and
            body['verification']==rec['bindings']['verification'] and rec['stop_digest']==task['writer_stop']['proof']['digest'] and
            rec['outcome'] in {'completed','failed'},'Barrier stop certificate changed original facts')
        return value
    task_state._fields(rec,('reference','source_digest','request_digest','revision','outcome','assessment','proof','bindings'))
    task_state._artifact_reference(rec['reference']); d3._hash(rec['source_digest']); d3._hash(rec['request_digest'])
    task_state._integer(rec['revision'],'assignment reconciliation revision',2)
    d3._require(rec['revision']<=_stop_revision(task) and rec['outcome'] in {'completed','failed','cancelled'},
        'Assignment reconciliation does not precede exact stopped transition')
    proof=rec['proof']; evidence._bounded(proof)
    d3._require(proof.get('version')==2 and type(proof['version']) is int and
        proof.get('identity')==dict(client=owner['client'],session_id=owner['session_id'],task_id=owner['task_id'],scope_ref=task['scope_ref']) and
        proof.get('scope_id')==owner['scope_id'] and proof.get('attempt_id')==ref['id'] and
        proof.get('dispatch_id')==owner['dispatch_id'] and proof.get('assignment_digest')==ref['assignment']['digest'] and
        proof.get('claims_digest')==evidence._digest(value['claims']) and proof.get('expected_pins')==task['pins'] and
        type(proof.get('expected_revision')) is int and proof['expected_revision']==rec['revision']-1,
        'Stopped assignment proof original identity differs')
    bindings=rec['bindings']
    fields={'coordinator':('operation','operation_evidence','resource_observations','d5_plan','d6_plan','candidate','current_candidate'),
        'check':('d5_plan','candidate','current_candidate','execution'),
        'worker':('plan','candidate','current_candidate','observation','native_receipt'),
        'review':('plan','candidate','current_candidate','observation','native_receipt')}[role]
    task_state._fields(bindings,fields)
    for key in fields:
        if key!='native_receipt' and bindings[key] is not None: task_state._artifact_reference(bindings[key])
    if role=='check':
        d3._require(rec['assessment'] is None,'Check certificate invented an assessment')
    else:
        assessment=rec['assessment']; task_state._fields(assessment,('outcome','changed_paths','evidence','coordinator_ref'))
        d3._require(assessment['outcome']==rec['outcome'],'Stopped assessment outcome differs')
        for key in ('changed_paths','evidence'): delegation._strings(assessment[key],key,maximum=64,limit=4096,nonempty=key=='evidence')
        for name in assessment['changed_paths']: evidence._relative(name)
        task_state._text(assessment['coordinator_ref'],'assessment coordinator',4096)
    if role=='coordinator':
        d3._require(proof.get('kind')=='coordinator-operation-reconciliation' and proof.get('operation_stopped') is True and
            proof.get('operation_descendants_stopped') is True and proof.get('operation_digest')==bindings['operation']['digest'] and
            all(proof.get(k)==bindings[k] for k in ('operation_evidence','resource_observations')),
            'Stopped coordinator operation bindings differ')
        for key in ('d5_plan','d6_plan'):
            if bindings[key] is not None: d3._require(proof.get(key+'_digest')==bindings[key]['digest'],'Stopped coordinator plan differs')
    else:
        d3._require(proof.get('kind')==('check-writer-reconciliation' if role=='check' else 'assignment-writer-reconciliation') and
            proof.get('stopped') is True and proof.get('descendants_stopped') is True,'Stopped assignment descendants differ')
        if role=='check':
            d3._require(proof.get('plan_digest')==bindings['d5_plan']['digest'] and
                proof.get('execution_digest')==(bindings['execution']['digest'] if bindings['execution'] else None),'Stopped check bindings differ')
        else:
            receipt=bindings['native_receipt']; task_state._fields(receipt,('native_identity','result_digest','launch_state'))
            d3._require(proof.get('d6_plan_digest')==bindings['plan']['digest'] and
                proof.get('observation_digest')==(bindings['observation']['digest'] if bindings['observation'] else None) and
                all(proof.get(k)==receipt[k] for k in receipt),'Stopped native receipt differs')
    if bindings['candidate'] is not None:
        d3._require(proof.get('candidate_digest')==bindings['candidate']['digest'] and bindings['current_candidate'] is not None and
            proof.get('current_candidate_digest')==bindings['current_candidate']['digest'],'Stopped candidate bindings differ')
    return value


def _assignment_certificate(context,db,task,record,*,read_context=None,read_roster=None):
    from runtime import evidence
    if read_roster is not None:
        linked=task.get('writer_stop',{}).get('linked')
        read_roster=evidence._matching_linked_read_roster(read_roster,
            evidence._read(None,linked,context=context,db=db) if linked else None)
        if read_roster is not None:
            evidence._check_linked_read_roster(read_roster,evidence._canonical_plain(read_roster.pointer),
                member=dict(authority=_canonical_plain(context.authority_set[0]),scope_id=evidence._task_scope_id(task),
                    **{key:task[key] for key in ('client','session_id','task_id')}),writer=(context,db))
    d3._require(record['data']['status']=='settled' and record['data'].get('lifecycle'),'Recovery assignment lacks dated reconciled stop proof')
    rec=evidence._read(None,record['data']['reconciliation'],context=context,db=db)
    if rec.get('kind')=='synchronous-verification':task_state._ready_stop(context,db,task,read_context=read_context,read_roster=read_roster)
    return evidence._finish_linked_read_context(_assignment_certificate_fields(context,db,task,record,rec),read_context,read_roster=read_roster)


def _assignment_certificate_fields(context,db,task,record,rec):
    """Shared local role checks; complete stop obligations belong to the caller."""
    from runtime import evidence,delegation
    data,body,owner=record['data'],record['body'],record['owner']
    d3._require(data['status']=='settled' and data.get('lifecycle'),'Recovery assignment lacks dated reconciled stop proof')
    read=lambda ref:evidence._read(None,ref,context=context,db=db)
    if rec.get('kind')=='synchronous-verification':return _barrier_certificate_fields(context,db,task,record,rec)
    d3._require(type(rec.get('version')) is int and rec['version']==2,'Recovery assignment requires version2 role proof')
    role=data['role']
    task_state._fields(rec,('version','proof','source_digest','request_digest','current_candidate',*(() if role=='check' else ('assessment','revision','reservation_snapshot_digest'))),
        ('prior_cooperative',) if role in delegation.NATIVE_ROLES else ())
    if 'prior_cooperative' in rec:
        prior=read(rec['prior_cooperative'])
        d3._require(prior.get('proof',{}).get('kind')==delegation.COOPERATIVE_PROOF and
            prior['proof'].get('attempt_id')==record['id'] and
            prior['proof'].get('assignment_digest')==data['assignment']['digest'],
            'Prior cooperative completion differs from the strict assignment')
    d3._hash(rec['source_digest']);d3._hash(rec['request_digest'])
    if role!='check': d3._hash(rec['reservation_snapshot_digest'])
    proof=rec['proof']; attempt=next((a for a in task.get('evidence',{}).get('attempts',[]) if a['id']==record['id']),None)
    current=rec['current_candidate']; revision=rec.get('revision')
    if role=='check':
        d3._require(attempt is not None and delegation._linked_check(None,owner['scope_id'],task,attempt,[record],context=context,db=db) is not None,
            'Recovery check lacks actual linked attempt')
        d3._require(attempt.get('reconciliation')==data['reconciliation'] and attempt['status'] not in {'reserved','running','uncertain'},
            'Recovery check remains unresolved')
        outcome='completed' if attempt['status']=='passed' else 'cancelled' if attempt['status']=='cancelled' else 'failed'
        # The check writer persists no revision/assessment; its exact transition increments once.
        revision=proof['expected_revision']+1; assessment=None
    else:
        if role=='review':
            d3._require(attempt is not None and attempt['status'] not in {'reserved','running','uncertain'} and
                attempt.get('ownership_reconciliation')==data['reconciliation'],'Recovery review remains unresolved')
        assessment=rec['assessment']; task_state._fields(assessment,('outcome','changed_paths','result_assessment','partial_change_assessment','evidence','coordinator_ref'))
        outcome=assessment['outcome']; assessment={k:assessment[k] for k in ('outcome','changed_paths','evidence','coordinator_ref')}
    task_state._integer(revision,'historical reconciliation revision',2)
    d3._require(revision<=_stop_revision(task) and data['result']==dict(outcome=outcome),
        'Recovery role outcome/revision differs from stopped owner')
    delegation._validate_role_proof(record,task,proof,scope_id=owner['scope_id'],revision=revision-1,read=read,
        linked_review=lambda a:delegation._linked_review(None,owner['scope_id'],task,a,[record],context=context,db=db),
        current_candidate_digest=current['digest'] if current else None)
    if current is not None: evidence._candidate_projection(read(current),task)
    if role=='coordinator':
        bindings={k:body[k] for k in ('operation','d5_plan','d6_plan')}
        bindings.update({k:proof[k] for k in ('operation_evidence','resource_observations')})
    elif role=='check':
        bindings=dict(d5_plan=body['d5_plan'],execution=data['observation'])
    else:
        history=delegation._worker_history(None,data['observation'],context=context,db=db)
        observation=history[0][1] if history else None;receipt=observation['receipt'] if observation else {}
        if observation is not None:
            d3._require(isinstance(receipt,dict) and all(receipt.get(k)==v for k,v in data['binding'].items()) and
                receipt.get('assignment_digest')==data['assignment']['digest'] and receipt.get('candidate_digest')==data['candidate']['digest'] and
                receipt.get('checkout')==task['accepted_identity']['repository_root'] and
                any(read(item['incoming']).get('receipt')==receipt for _,item in history),
                'Recovery native receipt lacks actual original observation/binding')
            result=observation['result']
            d3._require((result is None and receipt.get('result_digest') is None) or
                isinstance(result,str) and hashlib.sha256(result.encode()).hexdigest()==receipt.get('result_digest'),
                'Recovery native result identity differs')
        else:
            d3._require(data['observation'] is None and proof['launch_state']=='not-started' and
                proof['observation_digest'] is None and proof['result_digest'] is None,'Recovery no-launch proof is incomplete')
        native={k:data['binding'][k] for k in ('client_surface','native_version','parent_session_id','task_name')}
        native.update({k:receipt.get(k) for k in ('parent_turn_id','spawn_item_id','worker_id','worker_turn_id','result_item_id')})
        bindings=dict(plan=body['plan'],observation=data['observation'],native_receipt=dict(native_identity=native,
            result_digest=receipt.get('result_digest'),launch_state='observed' if observation else 'not-started'))
    bindings.update(candidate=data['candidate'],current_candidate=current)
    return dict(reference=dict(authority=_canonical_plain(context.authority_set[0]),id=record['id'],owner=owner,assignment=data['assignment']),
        created_at=data['lifecycle']['created_at'],request_digest=data['request_digest'],role=role,claims=data['claims'],
        reconciliation=dict(reference=data['reconciliation'],source_digest=rec['source_digest'],request_digest=rec['request_digest'],
            revision=revision,outcome=outcome,assessment=assessment,proof=proof,bindings=bindings))


def _stopped_certificate_shape(value):
    complete='writer_stop' in value
    cancelled=complete and value.get('state')=='CANCELLED'
    task_state._fields(value,('reference','created_at','revision','pins','accepted_identity','scope_ref','turn_id','proof',
        *(('version','state','writer_stop',*(['retirement','retirement_proof'] if cancelled else []),
            *(['linked'] if value.get('writer_stop',{}).get('linked') else [])) if complete else ('retirement',))),('assignments','preparation'))
    task_state._task_reference(value['reference']); task_state._integer(value['created_at'],'original stopped task age')
    task_state._integer(value['revision'],'stopped task revision',2); task_state._text(value['scope_ref'],'stopped scope')
    d3._require(task_state._validate_pins(value['pins'])==3,'Stopped certificate requires canonical pins')
    accepted=task_state._accepted_identity(value['accepted_identity'])
    ref=value['reference']
    d3._require(task_state._digest(accepted)==value['pins']['accepted_scope_digest'] and accepted['client']==ref['client']
        and task_state._digest([3,accepted['project_id'],accepted['repository_id'],accepted['checkout_id']])==ref['scope_id'],
        'Stopped certificate original accepted identity differs')
    if complete:
        from runtime import evidence
        d3._require(value['state'] in {'DONE','CANCELLED','REVIEW'} and (value['state']!='REVIEW' or 'linked' in value) and type(value['version']) is int and value['version']==2,'Unknown complete stop certificate')
        stop=value['writer_stop'];task_state._writer_stop_shape(stop);proof=value['proof']
        evidence._ready_stop_shape(proof)
        d3._require(proof['identity']=={k:(ref[k] if k!='scope_ref' else value[k]) for k in ('client','session_id','task_id','scope_ref')} and
            proof['expected_pins']==value['pins'] and proof['expected_revision']==stop['task_revision']==value['revision'] and
            proof['observed_turn_id']==value['turn_id'] and proof['candidate_digest']==stop['candidate']['digest'] and
            proof['verification_digest']==(stop['verification']['digest'] if stop['verification'] is not None else None) and proof['generation']==stop['generation'] and
            evidence._digest(proof)==stop['proof']['digest'] and all(proof[k]==stop[k] for k in ('observation_ref','coordinator_ref')),
            'Complete certificate changed original independent task stop')
        d3._require(('linked' in value)==('linked_digest' in proof),'Complete certificate lost its linked basis')
        if 'linked' in value:
            linked=value['linked'];evidence._linked_stop_shape(linked)
            if stop['verification'] is None:
                d3._require(linked['version']==2 and linked['aggregate'] is None and proof['verification_digest'] is None and
                    any(row['task']==ref and row['verification'] is None for row in linked['members']),
                    'Absent verifier needs its exact factual lifecycle member basis')
            d3._require(evidence._digest(linked)==proof['linked_digest']==stop['linked']['digest'] and
                any(row['task']==ref and row['state']==value['state'] and row['revision']==value['revision'] and
                    row['pins']==value['pins'] and row['created_at']==value['created_at'] for row in linked['members']),
                'Complete certificate changed its actual linked-member basis')
    if not complete or cancelled:
        retirement=value['retirement']; proof=value['retirement_proof'] if complete else value['proof']
        task_state._fields(retirement,('proof_digest','proof','observation_ref','coordinator_ref','previous_revision'))
        task_state._artifact_reference(retirement['proof']); d3._hash(retirement['proof_digest'])
        task_state._fields(proof,('version','kind','identity','expected_revision','expected_pins','observed_turn_id','stopped','descendants_stopped','attempt_ids','observation_ref','coordinator_ref'))
        d3._require(type(proof['version']) is int and proof['version']==1 and proof['kind']=='stopped-writer-reconciliation'
            and proof['stopped'] is True and proof['descendants_stopped'] is True
            and proof['identity']=={k:(ref[k] if k!='scope_ref' else value[k]) for k in ('client','session_id','task_id','scope_ref')}
            and proof['expected_pins']==value['pins'] and proof['expected_revision']==retirement['previous_revision']==value['revision']-1
            and proof['observed_turn_id']==value['turn_id'] and all(proof[k]==retirement[k] for k in ('observation_ref','coordinator_ref')),
            'Stopped certificate exact reconciliation differs')
        d3._require(isinstance(proof['attempt_ids'],list) and len(proof['attempt_ids'])<=4096 and len(set(proof['attempt_ids']))==len(proof['attempt_ids']),
            'Stopped certificate attempt set differs')
        for attempt in proof['attempt_ids']: task_state._text(attempt,'stopped attempt ID')
        for key in ('observation_ref','coordinator_ref'): task_state._text(proof[key],key)
        if complete:d3._require(evidence._digest(proof)==retirement['proof']['digest'],
            'Complete certificate changed original cancellation proof')
    if 'preparation' in value:
        d3._require('linked' in value,'Preparation needs an actual stopped linked member')
        prepared=value['preparation'];task_state._fields(prepared,('reference','component_digest','authorities'))
        task_state._artifact_reference(prepared['reference']);d3._hash(prepared['component_digest'])
        d3._require(isinstance(prepared['authorities'],list) and 0<len(prepared['authorities'])<=4096,'Prepared authority set is incomplete')
        for authority in prepared['authorities']:task_state._task_reference(dict(value['reference'],authority=authority))
    if 'assignments' in value:
        d3._require(isinstance(value['assignments'],list) and len(value['assignments'])<=4096,'Stopped assignment certificate bound exceeded')
        for assignment in value['assignments']: _assignment_certificate_shape(assignment,value)
        d3._require(len({a['reference']['id'] for a in value['assignments']})==len(value['assignments']),'Duplicate stopped assignments')
    return value


def _stopped_certificate(context,db,item,*,read_context=None,read_roster=None):
    from runtime import evidence
    ref=item['reference']; task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
    d3._require(task is not None and task.get('lifecycle') and task['pins']==item['pins'],'Recovery original stopped owner/age/pins changed')
    if read_roster is not None:
        linked=task.get('writer_stop',{}).get('linked')
        read_roster=evidence._matching_linked_read_roster(read_roster,
            evidence._read(None,linked,context=context,db=db) if linked else None)
    if task.get('writer_stop'):
        proof=task_state._ready_stop(context,db,task,observe=True,read_context=read_context,read_roster=read_roster)
        result=dict(reference=ref,created_at=task['lifecycle']['created_at'],version=2,state=task['state'],writer_stop=task['writer_stop'],
            **{k:task[k] for k in ('revision','pins','accepted_identity','scope_ref','turn_id')},proof=proof)
        if 'linked' in task['writer_stop']:result['linked']=evidence._read(None,task['writer_stop']['linked'],context=context,db=db)
        if task['state']=='CANCELLED':result.update(retirement=task['retirement'],
            retirement_proof=evidence._read(None,task['retirement']['proof'],context=context,db=db))
    else:
        d3._require(task['state']=='CANCELLED' and task.get('retirement'),'Recovery release requires exact cancelled or independently stopped ready owner')
        proof=evidence._read(None,task['retirement']['proof'],context=context,db=db)
        unresolved=[a['id'] for a in task.get('evidence',{}).get('attempts',[]) if a['status'] in {'reserved','running','uncertain'}]
        d3._require(proof.get('attempt_ids')==unresolved,'Recovery stopped attempt ownership differs')
        result=dict(reference=ref,created_at=task['lifecycle']['created_at'],**{k:task[k] for k in
            ('revision','pins','accepted_identity','scope_ref','turn_id','retirement')},proof=proof)
    from runtime import delegation
    records=delegation._ledger(None,db,context=context)
    result['assignments']=[_assignment_certificate(context,db,task,r,read_context=read_context,read_roster=read_roster) for r in records if all(r['owner'][k]==ref[k] for k in ('scope_id','client','session_id','task_id'))]
    result['assignments'].sort(key=d3._digest)
    return evidence._finish_linked_read_context(_stopped_certificate_shape(result),read_context,read_roster=read_roster)


def _stopped_certificate_inputs(context,db,item):
    """Actual local comparison inputs only; pending jobs never establish complete validation."""
    from runtime import evidence,delegation
    evidence._check_context(context,db)
    ref=item['reference'];task_state._task_reference(ref)
    d3._require(ref['authority']==_canonical_plain(context.authority_set[0]),'Stopped certificate authority differs')
    task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
    d3._require(task is not None and task.get('lifecycle') and task['pins']==item['pins'],'Recovery original stopped owner/age/pins changed')
    stop=task.get('writer_stop');local=None;linked=None;retirement=None
    if stop:
        task_state._writer_stop_shape(stop)
        linked=evidence._read(None,stop['linked'],context=context,db=db) if 'linked' in stop else None
        if linked is None or task['lifecycle']['safe_closed_at'] is None:
            local=task_state._ready_stop_local(context,db,task)
            proof=local['proof']
        else:
            # The existing complete linked validator owns retained-branch interpretation.
            proof=evidence._read(None,stop['proof'],context=context,db=db)
        if task['state']=='CANCELLED':retirement=evidence._read(None,task['retirement']['proof'],context=context,db=db)
    else:
        d3._require(task['state']=='CANCELLED' and task.get('retirement'),'Recovery release requires exact cancelled or independently stopped ready owner')
        proof=evidence._read(None,task['retirement']['proof'],context=context,db=db)
        unresolved=[a['id'] for a in task.get('evidence',{}).get('attempts',[]) if a['status'] in {'reserved','running','uncertain'}]
        d3._require(proof.get('attempt_ids')==unresolved,'Recovery stopped attempt ownership differs')
    records=delegation._ledger(None,db,context=context)
    assignments=[]
    for record in records:
        if not all(record['owner'][k]==ref[k] for k in ('scope_id','client','session_id','task_id')):continue
        d3._require(record['data']['status']=='settled' and record['data'].get('lifecycle'),'Recovery assignment lacks dated reconciled stop proof')
        rec=evidence._read(None,record['data']['reconciliation'],context=context,db=db)
        assignments.append(_assignment_certificate_fields(context,db,task,record,rec))
    assignments.sort(key=d3._digest)
    inputs=dict(item=item,task=task,proof=proof,linked=linked,retirement_proof=retirement,
        assignments=assignments,local_stop=local)
    _stopped_certificate_from_inputs(inputs)
    evidence._check_context(context,db)
    return _canonical_freeze(inputs)


def _stopped_certificate_from_inputs(inputs):
    task=inputs['task'];ref=inputs['item']['reference']
    result=dict(reference=ref,created_at=task['lifecycle']['created_at'],
        **{k:task[k] for k in ('revision','pins','accepted_identity','scope_ref','turn_id')},proof=inputs['proof'],assignments=inputs['assignments'])
    if task.get('writer_stop'):
        result.update(version=2,state=task['state'],writer_stop=task['writer_stop'])
        if inputs['linked'] is not None:result['linked']=inputs['linked']
        if task['state']=='CANCELLED':result.update(retirement=task['retirement'],retirement_proof=inputs['retirement_proof'])
    else:result['retirement']=task['retirement']
    return _stopped_certificate_shape(result)


def _stopped_certificate_assembly(inputs):
    """Pure matched obligations; comparison results confer no publication or stop authority."""
    inputs=_canonical_plain(inputs);result=_stopped_certificate_from_inputs(inputs)
    task=inputs['task'];ref=inputs['item']['reference']
    job=None
    if inputs['linked'] is not None:
        job=dict(kind='linked-certificate',reference=ref,item=inputs['item'],expected=result)
    elif task.get('writer_stop'):
        job=dict(kind='ready-candidate',reference=ref,task=task,candidate=inputs['local_stop']['candidate'])
    if job is not None:
        response=yield job
        d3._require(isinstance(response,dict) and set(response)=={'job','result'} and response['job']==job,
            'Stopped certificate validation job is unresolved or mismatched')
        d3._require(isinstance(response['result'],dict),'Stopped certificate job requires actual result facts')
        if job['kind']=='linked-certificate':
            d3._require(response['result']==result,'Complete linked certificate differs from original local inputs')
        else:
            task_state._writer_stop_shape(dict(task['writer_stop'],inputs=response['result']))
    return result


def _resolve_stopped_certificate_job(context,job,*,read_context=None,read_roster=None):
    """Normal full validators; the serial owner calls this only after closing its local reader."""
    from runtime import evidence
    d3._require(job['reference']['authority']==_canonical_plain(context.authority_set[0]),'Stopped job authority differs')
    if job['kind']=='ready-candidate':
        result=evidence._ready_stop_candidate(context,job['task'],job['candidate'],read_context=read_context,read_roster=read_roster)
    else:
        d3._require(job['kind']=='linked-certificate','Unknown stopped certificate validation job')
        with evidence._context_database(context) as db:
            result=_stopped_certificate(context,db,job['item'],read_context=read_context,read_roster=read_roster)
            _check_stopped_certificate(context,db,result,read_context=read_context,read_roster=read_roster)
    return evidence._finish_linked_read_context(dict(job=job,result=result),read_context,read_roster=read_roster)


def _linked_live_certificate_inputs(context,db,item,basis):
    """Read factual-v2 open-member comparisons; other lifecycle branches keep their full path."""
    from runtime import evidence
    evidence._check_context(context,db);ref=item['reference']
    d3._require(ref['authority']==_canonical_plain(context.authority_set[0]),'Stopped certificate authority differs')
    task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
    d3._require(task is not None and task.get('lifecycle'),'Recovery original stopped owner/age/pins changed')
    if task['lifecycle']['safe_closed_at'] is not None or not task.get('writer_stop',{}).get('linked'):return None
    linked=evidence._read(None,task['writer_stop']['linked'],context=context,db=db)
    if linked.get('version')!=2:return None
    evidence._linked_stop_shape(linked)
    d3._require(linked==basis,'Live linked member basis changed')
    members=[member for member in linked['members'] if member['task']==ref]
    d3._require(len(members)==1,'Live linked member is outside its actual basis')
    inputs=_canonical_plain(_stopped_certificate_inputs(context,db,item));value=_stopped_certificate_from_inputs(inputs)
    member=members[0]
    d3._require(member['state']==task['state'] and member['revision']==task['revision'] and member['pins']==task['pins'] and
        member['created_at']==task['lifecycle']['created_at'] and member['bindings']==inputs['local_stop']['bindings'],
        'Live linked member original bindings changed')
    identity=task_state._lifecycle_identity(db,'task',ref);_check_stopped_task_identity(identity,value)
    d3._require(identity['status']=='live','Live linked member identity is no longer live')
    d3._require([attempt['id'] for attempt in task.get('evidence',{}).get('attempts',[])]==value['proof']['attempt_ids'],
        'Recovery live stopped attempts changed')
    assignments=[]
    for assignment in value['assignments']:
        observed=task_state._lifecycle_identity(db,'assignment',assignment['reference'])
        _check_stopped_assignment_identity(observed,value,assignment)
        d3._require(observed['status']=='live','Live linked assignment identity is no longer live')
        assignments.append(observed)
    return _canonical_freeze(dict(certificate_inputs=inputs,identity=identity,assignment_identities=assignments))


def _linked_live_certificate_assembly(inputs):
    """Pure live certificate projection; an exact external basis result is still required."""
    from runtime import evidence
    inputs=_canonical_plain(inputs)['certificate_inputs'];basis=inputs['linked']
    job=dict(kind='linked-basis',reference=inputs['item']['reference'],aggregate=evidence._linked_basis_root(basis),
        authorities=basis['authorities'],expected=basis)
    response=yield job
    d3._require(isinstance(response,dict) and set(response)=={'job','result'} and response['job']==job,
        'Live linked basis validation is unresolved or mismatched')
    d3._require(isinstance(response['result'],dict) and response['result']==basis,'Live linked basis result changed')
    return _stopped_certificate_from_inputs(inputs)


def _resolve_linked_live_certificate_job(job,*,read_context=None,read_roster=None):
    """The acquisition owner closes its member reader before invoking the complete basis validator."""
    from runtime import evidence
    d3._require(job['kind']=='linked-basis','Unknown live linked certificate job')
    return evidence._finish_linked_read_context(dict(job=job,result=evidence._linked_stopped_basis(job['aggregate'],evidence._linked_read_authorities(read_context,job['expected'],member=job['reference'],read_roster=read_roster))),read_context,read_roster=read_roster)


def _check_stopped_task_identity(identity,value):
    ref=value['reference']
    d3._require(identity['pins_digest']==task_state._digest(value['pins']) and identity['created_at']==value['created_at']
        and identity['identity']=={k:ref[k] for k in ('scope_id','client','session_id','task_id')},'Recovery stopped identity support differs')


def _check_stopped_assignment_identity(observed,value,assignment):
    d3._require(observed['pins_digest']==task_state._digest(value['pins']) and observed['request_digest']==assignment['request_digest'] and
        observed['created_at']==assignment['created_at'] and observed['identity']==dict(id=assignment['reference']['id'],owner=assignment['reference']['owner']),
        'Recovery stopped assignment identity support differs')


def _stopped_assignment_certificate(value,reference):
    """Resolve an exact issued pointer; retirement markers cannot authenticate that pointer."""
    _stopped_certificate_shape(value)
    matches=[a for a in value.get('assignments',[]) if a['reference']==reference]
    d3._require(len(matches)==1,'Recovery requested assignment differs from retained issued pointer')
    return matches[0]


def _check_stopped_certificate(context,db,value,*,preparation_reader=None,writer=None,read_context=None,read_roster=None):
    from runtime import evidence
    _stopped_certificate_shape(value)
    ref=value['reference']
    read_context=evidence._matching_linked_read_context(read_context,value['linked']) if value.get('linked') else None
    linked_writer=writer if writer is not None else (context,db)
    component_context=read_context
    if type(read_roster) is _RecoveryReadRosters:
        read_roster=read_roster.select(value,writer=linked_writer)
        # The exact component scope owns full validation; retain the borrowed check at return.
        component_context=None
    read_roster=evidence._matching_linked_read_roster(read_roster,value.get('linked'))
    if read_roster is not None:
        evidence._check_linked_read_roster(read_roster,evidence._linked_basis_root(value['linked']),
            basis=value['linked'],member=ref,writer=linked_writer)
    if value.get('linked') and evidence._linked_component_closed(evidence._linked_basis_root(value['linked']),writer=linked_writer):
        if preparation_reader is not None and 'preparation' in value:
            evidence._check_linked_preparation_certificate(context,db,value,preparation_reader=preparation_reader,writer=writer,read_context=component_context,read_roster=read_roster)
        else:
            authorities=(evidence._linked_read_authorities(component_context,value['linked'],member=ref,writer=linked_writer,read_roster=read_roster)
                if component_context is not None or read_roster is not None else value.get('preparation',{}).get('authorities',value['linked']['authorities']))
            support=evidence._linked_retained_component(evidence._linked_basis_root(value['linked']),authorities,writer=linked_writer)
            evidence._check_retained_linked_certificate(support,value)
        identity=task_state._lifecycle_identity(db,'task',ref)
        d3._require(identity['status']!='expired','Expired linked identity cannot validate historical support')
        return evidence._finish_linked_read_context(identity,read_context,read_roster=read_roster)
    identity=task_state._lifecycle_identity(db,'task',ref)
    _check_stopped_task_identity(identity,value)
    if identity['status']=='live':
        task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
        complete='writer_stop' in value
        d3._require(task['state']==value.get('state','CANCELLED') and all(task[k]==value[k] for k in
            ('revision','pins','accepted_identity','scope_ref','turn_id',*(['writer_stop',*(['retirement'] if task['state']=='CANCELLED' else [])] if complete else ['retirement']))),'Recovery live stopped owner changed')
        if complete:task_state._ready_stop(context,db,task,read_context=component_context,read_roster=read_roster)
        attempts=[a['id'] for a in task.get('evidence',{}).get('attempts',[]) if complete or a['status'] in {'reserved','running','uncertain'}]
        d3._require(attempts==value['proof']['attempt_ids'],'Recovery live stopped attempts changed')
    for assignment in value.get('assignments',[]):
        observed=task_state._lifecycle_identity(db,'assignment',assignment['reference'])
        _check_stopped_assignment_identity(observed,value,assignment)
        if observed['status']=='live':
            from runtime import delegation
            row=db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',(assignment['reference']['id'],)).fetchone()
            record=delegation._record(None,row,context=context,db=db)
            d3._require(_assignment_certificate(context,db,task,record,read_context=component_context,read_roster=read_roster)==assignment,'Recovery live stopped assignment changed')
    if 'preparation' in value:evidence._check_linked_preparation_certificate(context,db,value,preparation_reader=preparation_reader,writer=writer,read_context=component_context,read_roster=read_roster)
    return evidence._finish_linked_read_context(identity,read_context,read_roster=read_roster)


def _cut_recovery_content(content,certificates,contexts,*,read_context=None):
    # Preserve independent artifact/assignment raw routes; only the issued proof route is cut.
    from runtime import evidence
    content=deepcopy(content);certificates,extra,component_routes=evidence._linked_prepared_replacements(certificates,contexts,read_context=read_context)
    content['artifacts']=list({evidence._raw(row):row for row in [*content['artifacts'],*extra]}.values())
    certificates=sorted(certificates,key=d3._digest)
    component_routes={(d3._digest(ref['authority']),d3._digest(ref['artifact'])):owners
        for raw,owners in component_routes.items() for ref in [json.loads(raw)]}
    metadata_by_key={}
    by_authority={d3._digest(_canonical_plain(c.authority_set[0])):c for c in contexts}
    for artifact in content['artifacts']:
        key=(d3._digest(artifact['authority']),d3._digest(artifact['artifact']))
        with evidence._context_database(by_authority[key[0]]) as db:
            metadata,_=evidence._managed_artifact(None,artifact['artifact'],context=by_authority[key[0]],db=db)
        metadata_by_key[key]=(artifact['authority'],metadata)
    content=_recovery_cut_projection(content,certificates,metadata_by_key,component_routes)
    if 'use_contract' in content:
        content['use_contract']=_project_recovery_uses(content,contexts,[content['use_contract']])
    return content


def _recovery_cut_projection(content,certificates,metadata_by_key,component_routes):
    """Project acquired cut inputs only; this grants no release or provenance authority."""
    from runtime import evidence
    content=dict(content)
    certified={d3._digest(a['reference']):a for c in certificates if 'linked' not in c or 'preparation' in c for a in c.get('assignments',[])}
    # Linked raw routes need every exact prepared replacement; no safe closure is implied.
    seeds=[((d3._digest(c['reference']['authority']),d3._digest(_stop_envelope(c)['proof'])),c['reference']) for c in certificates if 'linked' not in c or 'preparation' in c]
    for c in certificates:
        if 'linked' in c and 'preparation' not in c:continue
        if c.get('writer_stop'):
            if c.get('state')=='CANCELLED':seeds.append(((d3._digest(c['reference']['authority']),d3._digest(c['retirement']['proof'])),c['reference']))
            seeds.extend(((d3._digest(c['reference']['authority']),d3._digest(ref)),c['reference']) for ref in (c['writer_stop']['candidate'],c['writer_stop']['verification']) if ref is not None)
    seeds.extend((key,json.loads(next(iter(owners)))) for key,owners in component_routes.items())
    for assignment in certified.values():
        authority=assignment['reference']['authority']; rec=assignment['reconciliation']
        refs=[assignment['reference']['assignment'],rec['reference']]
        refs += [ref for key,ref in rec['bindings'].items() if key!='native_receipt' and ref is not None]
        owner=dict(authority=authority,**{k:assignment['reference']['owner'][k] for k in ('scope_id','client','session_id','task_id')})
        seeds.extend(((d3._digest(authority),d3._digest(ref)),owner) for ref in refs)
    raw_tasks=set()
    # A route retains its original owner; another certified task is still foreign.
    support=set();foreign=set();seen=set();pending=list(seeds)
    while pending:
        key,owner=pending.pop();route=(key,d3._digest(owner))
        if route in seen or key not in metadata_by_key: continue
        seen.add(route);authority,metadata=metadata_by_key[key]
        component=component_routes.get(key)
        owned=component is not None and evidence._raw(owner) in component and evidence._raw(metadata['owner'].get('task')) in component
        if not owned and (authority!=owner['authority'] or metadata['owner'].get('task')!=owner):
            foreign.add(key);continue
        support.add(key)
        for edge in metadata['dependencies']:
            if edge['kind']!='artifact': continue
            ref=edge['reference'];other=ref.get('authority',authority)
            pending.append(((d3._digest(other),d3._digest(ref.get('artifact',ref))),owner))
    # Discover independent routes before removing any certified raw ownership edge.
    raw_assignments=set();retained=set();pending=(set(metadata_by_key)-support)|foreign
    while pending:
        key=pending.pop()
        if key in retained: continue
        d3._require(key in metadata_by_key,'Recovery retained artifact route is incomplete')
        retained.add(key);authority,metadata=metadata_by_key[key]
        for edge in metadata['dependencies']:
            if edge['kind']=='task': raw_tasks.add(d3._digest(edge['reference']))
            elif edge['kind']=='assignment':
                assignment_key=d3._digest(edge['reference']);raw_assignments.add(assignment_key)
                if assignment_key in certified:
                    assignment=certified[assignment_key];rec=assignment['reconciliation']
                    refs=[assignment['reference']['assignment'],rec['reference']]
                    refs += [ref for key,ref in rec['bindings'].items() if key!='native_receipt' and ref is not None]
                    pending.update((d3._digest(assignment['reference']['authority']),d3._digest(ref)) for ref in refs
                        if (d3._digest(assignment['reference']['authority']),d3._digest(ref)) in metadata_by_key)
            elif edge['kind'] in ('artifact','recovery'):
                ref=edge['reference'];other=ref.get('authority',authority)
                required=(d3._digest(other),d3._digest(ref.get('artifact',ref)))
                if required not in retained: pending.add(required)
    content['assignments']=[a for a in content['assignments'] if d3._digest(a) not in certified or d3._digest(a) in raw_assignments]
    for assignment in content['assignments']:
        owner=assignment['owner'];raw_tasks.add(d3._digest(dict(authority=assignment['authority'],**{k:owner[k] for k in ('scope_id','client','session_id','task_id')})))
    cut={d3._digest(c['reference']) for c in certificates}-raw_tasks
    content['tasks']=[t for t in content['tasks'] if d3._digest(t['reference']) not in cut]
    content['artifacts']=[a for a in content['artifacts'] if (d3._digest(a['authority']),d3._digest(a['artifact'])) in retained]
    content.update(version=2,stopped=certificates,previous=None)
    return content


def _retention_source_records(context,db,head,identity,budget):
    """Authenticate genuine local predecessor receipts; previous-only history is insufficient."""
    from runtime import evidence
    d3._require(evidence._RETENTION_CAPTURE_BUDGET.get() is budget,'Predecessor capture lost its actual shared budget')
    authority=_canonical_plain(context.authority_set[0]);records={};paths={};pending=[(head,[],None)]
    baseline=task_state._source_baseline(None,db,context=context)
    d3._require(baseline is not None and baseline['content']['source_contract']==2,'Retention source baseline is unavailable')
    matches=[row['reference']['artifact'] for row in baseline['dependencies'] if row['reference']['identity']==identity]
    d3._require(matches==[head],'Retention source is not its actual current baseline head')
    age=None;head_receipts=None
    while pending:
        reference,path,receipt=pending.pop();name=evidence._raw(reference)
        d3._require(reference!=head or not path,'Recovery predecessor closure loops to its head')
        if name in records:
            if receipt is not None:d3._require(records[name]['content']['journal']['sha256']==receipt['journal_sha256'],
                'Conflicting predecessor journal receipt')
            continue
        budget.take(parents=1);budget.retain(path)
        metadata,raw=evidence._managed_artifact(None,reference,context=context,db=db);body=json.loads(raw)
        evidence._recovery_source_shape(body,identity);evidence._validate_birth_origin(context,db,body,source_metadata=metadata)
        d3._require(body['authorities']==[authority] and metadata['owner']==dict(kind='project',project_id=baseline['content']['project_id'],task=None)
            and metadata['class']=='compact' and metadata['closed_at'] is None and not metadata['targets'] and metadata['export'] is None,
            'Retention source is foreign or differs from its original owner')
        d3._require(metadata['dependencies']==evidence._recovery_source_dependencies(body,authority),'Retention source metadata differs')
        if age is None:age=metadata['created_at'];head_receipts=body.get('supersedes',[])
        d3._require(metadata['created_at']==age and (receipt is None or body['journal']['sha256']==receipt['journal_sha256']),
            'Retention predecessor age or journal differs')
        budget.retain(body);budget.retain(metadata)
        records[name]=dict(reference=reference,metadata=metadata,content=body);paths[name]=path
        receipts=body.get('supersedes',[])
        d3._require(len({evidence._raw(row['artifact']) for row in receipts})==len(receipts),'Duplicated predecessor receipt')
        for row in receipts:
            d3._require(row['authority']==authority and row['identity']==identity and row['artifact']!=reference,
                'Retention predecessor receipt authority or identity differs')
            d3._require(not any(prior['artifact']==row['artifact'] for prior in path),'Recovery predecessor receipts are cyclic')
            budget.take(edges=1);budget.retain(row);pending.append((row['artifact'],[*path,row],row))
        if body['previous'] is not None:
            d3._require(not any(prior['artifact']==body['previous'] for prior in path),'Recovery previous chain is cyclic')
            prior=[row for row in head_receipts if row['artifact']==body['previous']]
            d3._require(len(prior)==1,'Legacy previous lacks its genuine current-head predecessor receipt')
            budget.take(edges=1);budget.retain(prior[0]);pending.append((body['previous'],[*path,prior[0]],prior[0]))
    return baseline,records,paths


def _retention_journal(body,budget):
    identity=body['identity'];path=d3._plan_path(identity['plan_dir'])/'transaction.json';info=path.lstat()
    d3._require(info.st_size<=MAX_JOURNAL,'Retention journal exceeds its original bound')
    budget.take(bytes=info.st_size)
    raw=d3._read_file(path,info.st_size,private=True,expected_binding=dict(device=info.st_dev,inode=info.st_ino))
    journal=_scoped_journal_shape(d3.read_request(raw,limit=MAX_JOURNAL))
    d3._require(all(journal[key]==identity[key] for key in ('transaction_id','manifest_digest'))
        and hashlib.sha256(raw).hexdigest()==body['journal']['sha256'] and journal['state']==body['journal']['state'],
        'Retention current journal identity or snapshot changed')
    budget.retain(journal)
    return path,info,raw,journal


def _retention_certificate_facts(context,db,certificate,budget):
    from runtime import evidence
    with evidence._retention_capture(context,db,budget), evidence._retention_global_metadata(context,db):
        return _retention_certificate_facts_core(context,db,certificate,budget)[0]


def _retention_certificate_facts_core(context,db,certificate,budget):
    from runtime import evidence
    d3._require(evidence._RETENTION_CAPTURE_BUDGET.get() is budget,'Certificate capture lost its actual shared budget')
    authority=_canonical_plain(context.authority_set[0]);_check_stopped_certificate(context,db,certificate)
    owner=certificate['reference']
    originals,retained=evidence._retention_closure_candidates(context,db,owner,budget)
    support=None if not retained else evidence._retained_closure_support_core(context,db,retained[0])
    if support is None:
        d3._require(len(originals)==1,'Historical root lacks its unique original closure')
        support=evidence._retention_original_support_core(context,db,originals[0],budget=budget)
        original=support['original']['content'];receipts=support['releases']
        budget.take(bytes=128+64*(len(receipts)+len(support['native_sources'])))
        used=[*receipts,*(item['discovery'] for item in support['native_sources'])]
    else:
        original=support['content'];budget.take(bytes=256+64*(len(original['release'])+1))
        receipts=[original['original'],*original['release']]
        used=[dict(reference=support['reference'],metadata=support['metadata']),*receipts]
    d3._require(original['identity']==owner and original['stopped']==certificate,'Historical root certificate differs from actual closure')
    proof=dict(authority=authority,artifact=_stop_envelope(certificate)['proof'])
    d3._require(evidence._digest(certificate['proof'])==proof['artifact']['digest'],'Historical proof payload digest differs')
    matching=[row for row in receipts if row['reference']==proof['artifact']]
    d3._require(len(matching)==1,'Historical proof lacks its exact original receipt')
    actual=task_state._metadata(db,proof['artifact']['name'])
    if actual['status']=='live' or os.path.lexists(actual['managed']['path']):
        proof_raw=evidence._managed_content(context,proof['artifact'],actual)
        d3._require(json.loads(proof_raw)==certificate['proof'],'Historical proof managed payload differs')
    else:d3._require(evidence._retained_detail_core(context,db,proof['artifact']) is not None,'Absent historical proof lacks real successor')
    budget.retain(certificate);budget.retain(matching[0])
    evidence._retention_reobserve_receipts(context,db,receipts)
    _check_stopped_certificate(context,db,certificate)
    budget.take(bytes=256)
    return dict(certificate=certificate,proof=proof,receipt=matching[0]),used


def _historical_anchor_facts(context,db,*,source,journal,root,budget):
    from runtime import evidence
    with evidence._retention_capture(context,db,budget), evidence._retention_global_metadata(context,db):
        return _historical_anchor_facts_core(context,db,source=source,journal=journal,root=root,budget=budget)


def _historical_anchor_from_inputs(current,source,root,authority,records,paths,journal,certificate_facts,budget):
    """Compare one occurrence against its enclosing owner's freshly authenticated inputs."""
    from runtime import evidence
    from runtime.startup import decode_project_record,canonical_scope_projection
    budget.take(bytes=512);budget.retain(current);budget.retain(source);budget.retain(root);budget.retain(authority)
    task_state._fields(current,('authority','identity','source_artifact'))
    task_state._fields(source,('actual_current_head','selected_occurrence_source','authenticated_receipt_path'))
    head=source['actual_current_head'];selected=source['selected_occurrence_source']
    recovery_uses._pointer(head);recovery_uses._pointer(selected)
    d3._require(head['authority']==selected['authority']==current['authority']==authority
        and head['artifact']==current['source_artifact'],'Historical occurrence source authority differs')
    head_name=evidence._raw(head['artifact']);name=evidence._raw(selected['artifact'])
    d3._require(head_name in records and records[head_name]['reference']==head['artifact']
        and records[head_name]['content']['identity']==current['identity'],'Historical occurrence current head differs')
    body=records[head_name]['content']
    d3._require(name in records and paths[name]==source['authenticated_receipt_path'],'Historical occurrence lacks its actual receipt path')
    issued=records[name]['content']
    d3._require(root in issued['use_contract']['roots'] and root['purpose']=='historical-stop','Historical root differs from issued program')
    budget.take(bytes=128+64*(len(body.get('stopped',[]))+len(certificate_facts)))
    certificates=[row for row in body.get('stopped',[]) if row['reference']==root['task']]
    d3._require(len(certificates)==1 and 'linked' not in certificates[0],'Historical root lacks an ordinary current certificate')
    certificate=certificates[0];budget.retain(certificate)
    matching=[fact for fact in certificate_facts if fact['certificate']==certificate]
    d3._require(len(matching)==1,'Historical root lacks its authenticated complete certificate')
    support=matching[0];proof=support['proof']
    d3._require(root['reference']==dict(kind='task',reference=root['task']) and root['proof']==proof,'Historical root exact support differs')
    identity=certificate['accepted_identity'];record=journal['manifest']['enrollment']['record']
    # Four plain trees, two parsed trees, two frozen trees; two spaced dumps and two UTF-8 size copies cost <=2x each.
    for _ in range(16):budget.retain(record)
    budget.retain(identity);budget.retain(identity);budget.retain(journal['manifest']['actions'])
    proposed=canonical_scope_projection(decode_project_record(record),repository_id=identity['repository_id'],checkout_id=identity['checkout_id'],client=identity['client'])
    effects=_historical_effects(identity,_canonical_plain(proposed) if proposed is not None else None,journal['manifest']['actions'])
    d3._require(root['effect'] in effects,'Historical occurrence has no original issued changed effect')
    budget.take(bytes=384);budget.retain(proof);budget.retain(support['receipt'])
    return dict(anchor=dict(root=root,support=[root['reference'],dict(kind='artifact',reference=proof)]),
        certificate=certificate,source=source,receipt=support['receipt'])


def _historical_anchor_facts_core(context,db,*,source,journal,root,budget):
    """Authenticate an issued historical occurrence from its actual current-head receipt path."""
    from runtime import evidence
    with evidence._retention_capture(context,db,budget):
        task_state._fields(source,('actual_current_head','selected_occurrence_source','authenticated_receipt_path'))
        head=source['actual_current_head'];selected=source['selected_occurrence_source']
        recovery_uses._pointer(head);recovery_uses._pointer(selected)
        authority=_canonical_plain(context.authority_set[0])
        d3._require(head['authority']==selected['authority']==authority,'Historical occurrence source authority differs')
        metadata,raw=evidence._managed_artifact(None,head['artifact'],context=context,db=db);body=json.loads(raw)
        _,records,paths=_retention_source_records(context,db,head['artifact'],body['identity'],budget)
        _,_,journal_raw,actual_journal=_retention_journal(body,budget)
        d3._require(actual_journal==journal,'Historical anchor borrowed another original journal')
        budget.take(bytes=128);budget.retain(source)
        name=evidence._raw(selected['artifact'])
        d3._require(name in records and paths[name]==source['authenticated_receipt_path'],'Historical occurrence lacks its actual receipt path')
        issued=records[name]['content']
        d3._require(root in issued['use_contract']['roots'] and root['purpose']=='historical-stop','Historical root differs from issued program')
        budget.take(bytes=384+64*len(body.get('stopped',[])));budget.retain(body['identity']);budget.retain(authority)
        certificates=[row for row in body.get('stopped',[]) if row['reference']==root['task']]
        d3._require(len(certificates)==1 and 'linked' not in certificates[0],'Historical root lacks an ordinary current certificate')
        certificate=certificates[0];_check_stopped_certificate(context,db,certificate)
        support=_retention_certificate_facts_core(context,db,certificate,budget)[0]
        budget.take(bytes=128);certificate_facts=[support]
        current=dict(authority=authority,identity=body['identity'],source_artifact=head['artifact'])
        result=_historical_anchor_from_inputs(current,source,root,authority,records,paths,actual_journal,certificate_facts,budget)
        d3._require(_retention_journal(body,budget)[2]==journal_raw,'Historical anchor journal changed')
        return result


def _retention_legacy_inputs(context,db,current,budget):
    """Observe ordinary legacy roots as current content; never issue a historical waiver."""
    from runtime import evidence,recovery_uses
    authority=_canonical_plain(context.authority_set[0]);head=current['source_artifact'];identity=current['identity']
    d3._require(evidence._RETENTION_CAPTURE_BUDGET.get() is budget,'Legacy acquisition changed its capture lifetime')
    history=evidence._recovery_source_history(context,db,head,identity,budget=budget)
    budget.retain(history)
    d3._require(history and all(body['version']==1 and 'use_contract' not in body for _,body in history),
        'Mixed or compact legacy history needs its original responsible owner')
    metadata=task_state._metadata(db,head['name']);age=metadata['created_at'];body=history[0][1]
    journal_path,info,journal_raw,journal=_retention_journal(body,budget)
    responses=[];captured=None
    for _ in range(recovery_uses.MAX_ROWS):
        captured=evidence._recovery_graph_inputs(context,db,head,identity=identity,created_at=age,
            artifacts=body['artifacts'],tasks=body['tasks'],assignments=body['assignments'],budget=budget,responses=responses)
        for row in captured['history']:
            evidence._validate_birth_origin(context,db,row['content'],source_metadata=row['metadata'])
        if not captured['jobs']:break
        for job in captured['jobs']:
            d3._require(job['kind'] in ('managed-source','task','assignment'),
                'Legacy acquisition cannot discharge historical authority jobs')
            budget.retain(job);response=_resolve_recovery_local_job(context,db,job,budget)
            budget.retain(response);responses.append(response)
    else:raise Pending('Legacy raw owner acquisition did not converge')
    budget.retain(captured['history'])
    admission=evidence._recovery_admission_projection(dict(authority=authority,history=captured['history'],jobs=captured['jobs']),budget=budget)
    d3._require(not admission['jobs'] and not admission.get('restricted_routes'),'Legacy admission remains unresolved or restricted')
    budget.retain(admission)
    budget.take(bytes=128+64*len(captured['history']))
    current_history=[(row['reference'],row['content']) for row in captured['history']]
    d3._require(current_history==history,'Legacy history changed during complete acquisition')
    # Each authentic direct dependency creates one row; ordinary v1 sources have no stopped rows.
    for row in captured['history']:
        count=1+len(row['metadata']['dependencies'])
        budget.take(edges=count,bytes=256*count);budget.retain(row['metadata']['dependencies'])
        for _ in range(count):
            budget.retain(current);budget.retain(row['reference']);budget.retain(row['reference']);budget.retain(authority)
    requirements=_recovery_current_requirements(current,current_history)
    budget.take(bytes=512+128*(len(body['tasks'])+len(body['assignments'])));budget.retain(body)
    generations={d3._digest(authority):_recovery_generation(db,body['tasks'],body['assignments'])}
    budget.retain(generations)
    budget.take(bytes=512+64*len(body['tasks']));budget.retain(current);budget.retain(body)
    dependency=dict(authority=authority,**identity,source_artifact=head,journal=body['journal'],
        task_refs=[item['reference'] for item in body['tasks']],assignments=body['assignments'],artifacts=body['artifacts'],
        generation_digest=d3._digest(generations))
    occurrences=[]
    for row in captured['observations']:
        budget.take(edges=1,bytes=256);budget.retain([current,row['reference']])
        occurrences.append(dict(root=dict(current=current,use_root=None),source=row['reference'],parent=None,
            field_path=['reference'],incoming='legacy-content',purpose='content',
            dependency=dict(kind='artifact',reference=row['pointer']),witness=dict(admission='legacy-content')))
        for index,edge in enumerate(row['dependencies']):
            budget.take(edges=1,bytes=256);budget.retain([current,row['reference'],edge])
            occurrences.append(dict(root=dict(current=current,use_root=None),source=row['reference'],parent=row['pointer'],
                field_path=['metadata','dependencies',index],incoming='legacy-content',purpose='content',
                dependency=edge,witness=dict(admission='legacy-content')))
    # Reobserve actual raw bodies and every managed binding on the same reader before returning facts.
    for response in captured['job_results']:
        latest=_resolve_recovery_local_job(context,db,response['job'],budget)
        d3._require(latest==response,'Legacy raw owner response changed during acquisition')
    for row in captured['observations']:
        observed=task_state._metadata(db,row['reference']['name']);budget.take(bytes=observed['bytes']);budget.retain(observed)
        latest,raw=evidence._managed_artifact(None,row['reference'],context=context,db=db)
        d3._require(latest==row['metadata'] and evidence._recovery_capture_json(raw,budget)==row['content'],'Legacy managed content changed during acquisition')
    budget.take(bytes=info.st_size)
    d3._require(d3._read_file(journal_path,info.st_size,private=True,expected_binding=dict(device=info.st_dev,inode=info.st_ino))==journal_raw,
        'Legacy journal changed during acquisition')
    budget.take(bytes=512)
    for value in (captured,admission,dependency,occurrences):budget.retain(value)
    stable=dict(root=current,baseline=captured['baseline'],records=captured['observations'],admission=admission,
        job_results=captured['job_results'],journal=journal)
    return stable,requirements,dependency,occurrences


def _retention_recovery_facts(context,db,*,recovery,owner,original,releases,retained=None,budget):
    from runtime import evidence
    with evidence._retention_capture(context,db,budget), evidence._retention_global_metadata(context,db):
        return _retention_recovery_facts_core(context,db,recovery=recovery,owner=owner,original=original,releases=releases,retained=retained,budget=budget)


def _retention_recovery_facts_core(context,db,*,recovery,owner,original,releases,retained=None,budget):
    """Guarded read-only occurrence authentication; generic content entry remains independent."""
    from runtime import evidence,recovery_uses
    with evidence._retention_capture(context,db,budget):
        authority=_canonical_plain(context.authority_set[0]);budget.retain(recovery);budget.retain(releases)
        budget.take(bytes=128);used_support=[]
        recovery=_canonical_plain(recovery)
        d3._require(recovery['complete'] and not recovery['blockers'] and not recovery['discovered_authorities']
            and recovery['authorities']==[authority],'Retention recovery coverage is incomplete or foreign')
        if retained is None:
            d3._require(original is not None,'Prepublication retention requires actual original bytes')
            group=evidence._retention_original_support_core(context,db,original['reference'],budget=budget)
            d3._require(group['owner']==owner and group['original']==original and group['releases']==releases,'Retention original group changed')
            budget.take(bytes=128+64*(len(group['releases'])+len(group['native_sources'])))
            used_support.extend(group['releases'])
            used_support.extend(item['discovery'] for item in group['native_sources'])
            phase=dict(retained=None,receipts=releases)
        else:
            d3._require(original is None,'Published retention cannot substitute original content')
            support=evidence._retained_for_owner_core(context,db,owner)
            d3._require(support is not None and support['reference']==retained,'Retention successor is not the actual unique LIVE owner')
            d3._require(support['content']['identity']==owner and [support['content']['original'],*support['content']['release']]==releases,
                'Retention successor original receipts differ')
            budget.take(bytes=256+64*(len(support['content']['release'])+1))
            used_support.extend((support['content']['original'],*support['content']['release']))
            used_support.append(dict(reference=support['reference'],metadata=support['metadata']))
            phase=dict(retained=retained,receipts=[dict(reference=row['reference'],metadata=task_state._metadata(db,row['reference']['name'])) for row in releases])
        stable=[];occurrences=[];requirements=[];required_dependencies=[]
        baseline=task_state._source_baseline(None,db,context=context)
        d3._require(baseline is not None and baseline['content']['source_contract']==2,'Retention baseline is unavailable')
        heads=[row['reference'] for row in baseline['dependencies']]
        budget.retain(heads)
        heads=_canonical_plain(heads)
        actual_roots=[dict(authority=authority,identity=row['identity'],source_artifact=row['artifact']) for row in heads]
        d3._require(sorted(actual_roots,key=d3._digest)==sorted(recovery['roots'],key=d3._digest),'Retention recovery root differs from actual baseline head')
        for current in actual_roots:
            head=current['source_artifact'];identity=current['identity']
            head_metadata=task_state._metadata(db,head['name'])
            d3._require(head_metadata is not None,'Retention current head metadata is unavailable')
            budget.take(bytes=head_metadata['bytes']);budget.retain(head_metadata)
            _,head_raw=evidence._managed_artifact(None,head,context=context,db=db)
            head_body=evidence._recovery_capture_json(head_raw,budget)
            if head_body.get('version')==1 and 'use_contract' not in head_body:
                legacy,needed,dependency,content=_retention_legacy_inputs(context,db,current,budget)
                budget.retain([legacy,needed,dependency,content])
                stable.append(legacy);requirements.extend(needed);required_dependencies.append(dependency);occurrences.extend(content)
                continue
            baseline,records,paths=_retention_source_records(context,db,head,identity,budget)
            current_body=records[evidence._raw(head)]['content'];d3._require(current_body['version']==2,'Uncompacted source remains protected')
            journal_path,info,journal_raw,journal=_retention_journal(current_body,budget)
            requirements.extend(_recovery_current_requirements(current,[(head,current_body)]))
            for item in current_body['tasks']:
                ref=item['reference'];count=db.execute('select length(cast(data as blob)) from tasks where scope_id=? and client=? and session_id=? and task_id=?',tuple(ref[k] for k in ('scope_id','client','session_id','task_id'))).fetchone()
                d3._require(count is not None,'Current raw task is unavailable');budget.take(parents=1,bytes=2*count[0]+64)
            for item in current_body['assignments']:
                count=db.execute('select length(cast(data as blob)) from assignments where id=?',(item['id'],)).fetchone()
                d3._require(count is not None,'Current raw assignment is unavailable');budget.take(parents=1,bytes=2*count[0]+64)
            generations={d3._digest(authority):_recovery_generation(db,current_body['tasks'],current_body['assignments'])}
            required_dependencies.append(dict(authority=authority,**identity,source_artifact=head,journal=current_body['journal'],
                task_refs=[item['reference'] for item in current_body['tasks']],assignments=current_body['assignments'],artifacts=current_body['artifacts'],generation_digest=d3._digest(generations)))
            certificates=current_body['stopped'];certificate_facts=[]
            for certificate in certificates:
                d3._require('linked' not in certificate,'Linked historical support remains protected')
                fact,receipts=_retention_certificate_facts_core(context,db,certificate,budget)
                budget.take(bytes=128+64*len(receipts));certificate_facts.append(fact);used_support.extend(receipts)
            managed={};qualified={};pending=[];queued=set()
            def qualify(edge):
                budget.retain(edge);budget.retain(authority)
                return recovery_uses.qualify_dependency(edge,authority)
            def enqueue(pointer):
                recovery_uses._pointer(pointer);name=recovery_uses.key(pointer)
                d3._require(pointer['authority']==authority,'Foreign occurrence graph remains protected')
                if name not in queued:budget.take(parents=1);budget.retain(pointer);queued.add(name);pending.append(pointer)
            for row in records.values():
                for dependency in row['metadata']['dependencies']:
                    pointer=recovery_uses._history_pointer(qualify(dependency))
                    if pointer is not None:enqueue(pointer)
                for pointer in row['content']['artifacts']:enqueue(pointer)
            while pending:
                pointer=pending.pop();ref=pointer['artifact'];metadata=task_state._metadata(db,ref['name'])
                d3._require(metadata is not None,'Occurrence graph metadata is missing')
                if metadata['status']=='live':evidence._managed_artifact(None,ref,context=context,db=db)
                else:evidence._retained_detail_core(context,db,ref)
                budget.take(edges=len(metadata['dependencies']));budget.retain(metadata)
                managed[(d3._digest(authority),d3._digest(ref))]=(authority,metadata)
                dependencies=[qualify(edge) for edge in metadata['dependencies']]
                budget.retain(dependencies);qualified[recovery_uses.key(pointer)]=dependencies
                for dependency in dependencies:
                    child=recovery_uses._history_pointer(dependency)
                    if child is not None:enqueue(child)
            def graph(starts):
                result={};todo=list(starts)
                while todo:
                    pointer=todo.pop();name=recovery_uses.key(pointer)
                    if name in result:continue
                    d3._require(name in qualified,'Issued occurrence graph lacks actual parent metadata')
                    budget.take(parents=1);budget.retain(qualified[name]);result[name]=qualified[name]
                    todo.extend(child for edge in qualified[name] if (child:=recovery_uses._history_pointer(edge)) is not None)
                return result
            budget.retain(current_body);before=deepcopy(current_body)
            for field in ('tasks','assignments','artifacts'):
                budget.retain([row['content'][field] for row in records.values()])
                before[field]=sorted({d3._digest(value):value for row in records.values() for value in row['content'][field]}.values(),key=d3._digest)
            cut_metadata={(d3._digest(pointer['authority']),d3._digest(pointer['artifact'])):managed[(d3._digest(pointer['authority']),d3._digest(pointer['artifact']))] for pointer in before['artifacts']}
            budget.retain([metadata for _,metadata in cut_metadata.values()])
            budget.take(parents=len(cut_metadata)*max(1,len(certificates)),bytes=128*len(cut_metadata)*max(1,len(certificates)))
            projected=_recovery_cut_projection(before,certificates,cut_metadata,{})
            for field in ('tasks','assignments','artifacts'):
                d3._require(projected[field]==current_body[field],'Current head does not preserve the exact shared cut correspondence')
            comparisons=[]
            for selected,row in records.items():
                body=row['content'];d3._require('use_contract' in body,'Legacy predecessor has no issued use program; typed admission remains unproved')
                direct=[qualify(edge) for edge in row['metadata']['dependencies']]
                starts=([dict(authority=authority,artifact=body['previous'])] if body['previous'] else [])
                birth=body.get('birth_handoff',{}).get('origin_source')
                if birth is not None:starts.append(birth)
                start_edges=[dict(kind='recovery',reference=pointer) for pointer in starts]
                payload=[edge for edge in direct if edge not in start_edges]
                anchors=[]
                source=dict(actual_current_head=dict(authority=authority,artifact=head),selected_occurrence_source=dict(authority=authority,artifact=row['reference']),authenticated_receipt_path=paths[selected])
                for root in body['use_contract']['roots']:
                    if root['purpose']=='historical-stop':anchors.append(_historical_anchor_from_inputs(current,source,root,authority,records,paths,journal,certificate_facts,budget)['anchor'])
                inherited=[];seen=set();todo=[receipt['artifact'] for receipt in body.get('supersedes',[])]
                if body['previous'] is not None:todo.append(body['previous'])
                while todo:
                    name=evidence._raw(todo.pop())
                    if name in seen:continue
                    d3._require(name in records and name!=selected,'Issued predecessor admission closure differs')
                    budget.take(parents=1);seen.add(name);inherited.append(records[name]);prior=records[name]['content']
                    todo.extend(receipt['artifact'] for receipt in prior.get('supersedes',[]))
                    if prior['previous'] is not None:todo.append(prior['previous'])
                budget.retain(inherited)
                legacy=evidence._recovery_admission_projection(dict(authority=authority,history=inherited,jobs=[]))['legacy_routes']
                result=recovery_uses.compare_issued_program(body['use_contract'],direct=direct,payload_direct=payload,
                    payload_metadata=graph([pointer for edge in payload if (pointer:=recovery_uses._history_pointer(edge)) is not None]),
                    history_metadata=graph(starts),starts=starts,anchors=anchors,legacy_routes=legacy,pending_jobs=[],pending_parents=[],budget=budget)
                comparisons.append(dict(source=source,result=result))
                def occurrence(field_path,dependency,purpose,parent=None,use_root=None,witness=None):
                    budget.take(edges=1,bytes=128);budget.retain([source,field_path,dependency,parent,use_root,witness])
                    occurrences.append(dict(root=dict(current=current,use_root=use_root),source=row['reference'],parent=parent,
                        field_path=field_path,incoming='structured-d4',purpose=purpose,dependency=dependency,witness=witness))
                for anchor in anchors:
                    occurrence(['use_contract','roots',body['use_contract']['roots'].index(anchor['root']),'proof'],
                        dict(kind='artifact',reference=anchor['root']['proof']),'retention-only',use_root=anchor['root'],
                        witness=dict(source=source,anchor=anchor,cut_index=len(stable)))
                for index,edge in enumerate(result['obligations']):
                    use_root=body['use_contract']['roots'][edge['root']]
                    occurrence(['use_contract','edges',index,'dependency'],edge['dependency'],edge['purpose'],edge['parent'],use_root,
                        dict(source=source,row=edge,cut_index=len(stable)))
                    if edge['parent'] is not None:
                        structural=evidence._raw(edge['parent']['artifact']) in records or any(fact['proof']==edge['parent'] for fact in certificate_facts)
                        purpose='retention-only' if edge['purpose']=='retention-only' and structural else 'content'
                        occurrence(['use_contract','edges',index,'parent'],dict(kind='artifact',reference=edge['parent']),purpose,
                            edge['parent'],use_root,dict(source=source,row=edge,structural_parent=structural,cut_index=len(stable)))
                kept=[qualify(edge) for edge in evidence._recovery_source_dependencies(projected,authority)]
                for index,dependency in enumerate(row['metadata']['dependencies']):
                    dependency=qualify(dependency)
                    occurrence(['metadata','dependencies',index],dependency,'content' if dependency in kept else 'retention-only',
                        witness=dict(source=source,cut_index=len(stable),retained=dependency in kept))
                for field,kind in (('tasks','task'),('assignments','assignment'),('artifacts','artifact')):
                    for index,item in enumerate(body[field]):
                        dependency=dict(kind=kind,reference=item['reference'] if kind=='task' else item)
                        path=[field,index,'reference'] if kind=='task' else [field,index]
                        retained_item=item in projected[field]
                        occurrence(path,dependency,'content' if retained_item else 'retention-only',
                            witness=dict(source=source,cut_index=len(stable),field=field,before=item,retained=retained_item))
                for index,receipt in enumerate(body.get('supersedes',[])):
                    occurrence(['supersedes',index,'artifact'],dict(kind='recovery',reference=dict(authority=authority,artifact=receipt['artifact'])),
                        'retention-only',witness=dict(source=source,receipt=receipt,cut_index=len(stable)))
                for index,certificate in enumerate(body.get('stopped',[])):
                    d3._require(certificate in certificates,'Older source has an unexplained conflicting stopped certificate')
                    pointers=[]
                    for section in ('writer_stop','retirement'):
                        for field in (('proof','candidate','verification') if section=='writer_stop' else ('proof',)):
                            pointer=certificate.get(section,{}).get(field)
                            if pointer is not None:pointers.append((['stopped',index,section,field],dict(authority=authority,artifact=pointer)))
                    for assignment_index,assignment in enumerate(certificate.get('assignments',[])):
                        prefix=['stopped',index,'assignments',assignment_index]
                        pointers.extend(((prefix+['reference','assignment'],dict(authority=authority,artifact=assignment['reference']['assignment'])),
                            (prefix+['reconciliation','reference'],dict(authority=authority,artifact=assignment['reconciliation']['reference']))))
                        pointers.extend((prefix+['reconciliation','bindings',field],dict(authority=authority,artifact=pointer))
                            for field,pointer in assignment['reconciliation']['bindings'].items() if field!='native_receipt' and pointer is not None)
                    for field_path,pointer in pointers:
                        key=(d3._digest(authority),d3._digest(pointer['artifact']));metadata=managed.get(key)
                        certified_proof=any(fact['certificate']==certificate and fact['proof']==pointer for fact in certificate_facts)
                        owned=certified_proof or metadata is not None and metadata[1]['owner'].get('task')==certificate['reference']
                        retained_pointer=pointer in projected['artifacts']
                        occurrence(field_path,dict(kind='artifact',reference=pointer),'retention-only' if certified_proof or owned and pointer in before['artifacts'] and not retained_pointer else 'content',
                            witness=dict(source=source,certificate=certificate['reference'],cut_index=len(stable),certified_proof=certified_proof,owned=owned,retained=retained_pointer))
            budget.retain(baseline);budget.retain(comparisons)
            for record in records.values():budget.retain(record)
            original_metadata={evidence._raw(item['reference']):item['metadata'] for item in releases}
            for _,metadata in managed.values():
                before_metadata=original_metadata.get(evidence._raw(dict(name=metadata['managed']['path'].split('/')[-1],digest=metadata['managed']['sha256'])))
                if before_metadata is not None:d3._require(dict(metadata,status=before_metadata['status'],closed_at=before_metadata['closed_at'])==before_metadata,
                    'Retained metadata changed beyond its exact owned status/date transition')
            stable_metadata=[dict(pointer=dict(authority=authority,artifact=dict(name=metadata['managed']['path'].split('/')[-1],digest=metadata['managed']['sha256'])),
                metadata=original_metadata.get(evidence._raw(dict(name=metadata['managed']['path'].split('/')[-1],digest=metadata['managed']['sha256'])),metadata)) for _,metadata in managed.values()]
            budget.retain(stable_metadata)
            stable.append(dict(root=current,baseline=baseline,metadata=stable_metadata,records=list(records.values()),receipt_paths=list(paths.values()),
                journal=journal,certificates=certificate_facts,cut_before=before,cut_after=projected,comparisons=comparisons))
            for row in records.values():
                latest,raw=evidence._managed_artifact(None,row['reference'],context=context,db=db)
                d3._require(latest==row['metadata'] and json.loads(raw)==row['content'],'Retention source changed during acquisition')
            budget.take(bytes=info.st_size)
            d3._require(d3._read_file(journal_path,info.st_size,private=True,expected_binding=dict(device=info.st_dev,inode=info.st_ino))==journal_raw,'Retention journal changed during acquisition')
        d3._require(sorted(requirements,key=d3._digest)==sorted(recovery['requirements'],key=d3._digest)
            and sorted(required_dependencies,key=d3._digest)==sorted(recovery['dependencies'],key=d3._digest),'Retention recovery requirements or dependency meaning changed')
        # Fresh final checks cover support files omitted from source-record-only reobservation.
        evidence._retention_reobserve_receipts(context,db,used_support)
        for row in stable:
            for fact in row.get('certificates',[]):_check_stopped_certificate(context,db,fact['certificate'])
        for current in actual_roots:
            metadata,raw=evidence._managed_artifact(None,current['source_artifact'],context=context,db=db)
            body=evidence._recovery_capture_json(raw,budget)
            budget.retain(body);budget.take(bytes=512+64*len(recovery['dependencies']))
            for item in body['tasks']:
                ref=item['reference'];count=db.execute('select length(cast(data as blob)) from tasks where scope_id=? and client=? and session_id=? and task_id=?',tuple(ref[k] for k in ('scope_id','client','session_id','task_id'))).fetchone()
                d3._require(count is not None,'Current raw task is unavailable');budget.take(parents=1,bytes=2*count[0]+64)
            for item in body['assignments']:
                count=db.execute('select length(cast(data as blob)) from assignments where id=?',(item['id'],)).fetchone()
                d3._require(count is not None,'Current raw assignment is unavailable');budget.take(parents=1,bytes=2*count[0]+64)
            generation={d3._digest(authority):_recovery_generation(db,body['tasks'],body['assignments'])}
            expected=[row for row in recovery['dependencies'] if row['authority']==authority
                and row['source_artifact']==current['source_artifact']
                and all(row[key]==value for key,value in current['identity'].items())]
            d3._require(len(expected)==1 and expected[0]['generation_digest']==d3._digest(generation),
                'Retention raw owner generation changed during acquisition')
        budget.retain(stable);budget.retain(phase);budget.retain(occurrences)
        return dict(complete=True,blockers=[],stable=_canonical_freeze(stable),phase=_canonical_freeze(phase),occurrences=_canonical_freeze(occurrences))


def _recovery_ready_inputs(content,contexts,*,read_context=None):
    from runtime import evidence
    files={}
    for context in contexts:
        authority=_canonical_plain(context.authority_set[0])
        with evidence._context_database(context) as db:
            for certificate in content.get('stopped',[]):
                ref=certificate['reference']
                if not certificate.get('writer_stop') or ref['authority']!=authority:continue
                identity=task_state._lifecycle_identity(db,'task',ref)
                if identity['status']!='live':continue
                task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
                if certificate.get('linked') and task['lifecycle']['safe_closed_at'] is not None and evidence._linked_component_closed(evidence._linked_basis_root(certificate['linked']),writer=(context,db)):continue
                candidate=evidence._read(None,task['writer_stop']['candidate'],context=context,db=db)
                files[d3._digest(ref)]=evidence._ready_stop_candidate(context,task,candidate,read_context=evidence._matching_linked_read_context(read_context,certificate['linked']) if certificate.get('linked') else None)
    return _canonical_freeze(files)


def _recovery_generation(db,tasks,assignments):
    capture=task_state._CAPTURE_SQL_SCOPE.get();owner=None;raw_material=row_count=0
    if capture is not None:
        resources,escrow=capture
        if escrow is not None:raise task_state.StateError('Recovery generation cannot borrow exit-validation allowance')
        task_state._capture_registered(resources,db);owner=resources.current_acquisition()
        if not db.in_transaction:raise task_state.StateError('Recovery generation requires its existing transaction')
        chars,units,integer=task_state._lifecycle_reference_size(owner,tasks)
        c,u,i=task_state._lifecycle_reference_size(owner,assignments)
        chars+=c;units+=u;integer+=i
        tree=4*(chars+1)+384*(units+1)+4*integer
        owner.reserve(requested=4*tree+512,live=4*tree+512)
    def read(sql,parameters):
        nonlocal raw_material,row_count
        if owner is not None:
            owner.reserve(requested=512,live=2048)
            try:
                measured=db.execute('select count(*),coalesce(sum(coalesce(length(cast(data as blob)),0)),0) '
                    'from ('+sql+' limit 2)',parameters).fetchone()
                if (type(measured) is not tuple or len(measured)!=2
                        or any(type(value) is not int or value<0 for value in measured) or measured[0]>1):
                    raise task_state.StateError('Recovery generation point measurement overflow or invalid scalar')
                material=4*measured[1]+128*measured[0]
                owner.reserve(requested=material,live=4*material+512*measured[0])
                raw_material+=measured[1];row_count+=measured[0]
            except BaseException as error:owner.fail(error);raise
        return db.execute(sql,parameters).fetchone()
    rows=[]
    for item in tasks:
        ref=item['reference']
        row=read('select data from tasks where scope_id=? and client=? and session_id=? and task_id=?',
            tuple(ref[k] for k in ('scope_id','client','session_id','task_id')))
        d3._require(row is not None,'Recovery original task unavailable')
        rows.append(['task',ref,row[0]])
    for item in assignments:
        row=read('select data from assignments where id=?',(item['id'],))
        d3._require(row is not None,'Recovery original assignment unavailable')
        rows.append(['assignment',item['id'],row[0]])
    if owner is not None:
        chars+=raw_material+10*row_count;units+=4*row_count+1
        tree=4*(chars+1)+384*(units+1)+4*integer
        serialized=12*(chars+1)+4*(units+1)+4*integer+32
        # Fresh escaping/encoding/hash scratch; raw rows remain owned through caller settlement.
        owner.reserve(requested=6*serialized+2*tree+512,live=4*serialized+2*tree+512)
    return d3._digest(rows)


def _historical_effects(accepted_identity,original_proposed_projection,actions):
    """Compare original removed shared effects; confer no current permission."""
    if original_proposed_projection is None:return []
    expected=deepcopy(accepted_identity);expected['shared_policy']=original_proposed_projection['shared_policy']
    if original_proposed_projection!=expected:return []
    old={row['path']:row for row in accepted_identity['shared_policy']}
    if any(old.get(row['path'])!=row for row in original_proposed_projection['shared_policy']):return []
    removed=set(old)-{row['path'] for row in original_proposed_projection['shared_policy']}
    return [dict(action_id=action['id'],path=effect['path']) for action in actions for effect in action['effects']
        if effect['before']!=effect['after'] and str(Path(action['owner']['root'])/effect['path']) in removed]


def _historical_recovery_roots(manifest,context,db,task,reference):
    """Recognize only the same physical member's removed, actually changed shared dependency."""
    from runtime import evidence
    from runtime.startup import decode_project_record,canonical_scope_projection
    identity=task_state._accepted_identity(task['accepted_identity'])
    record=manifest['enrollment']['record']
    changed={str(Path(action['owner']['root'])/effect['path']) for action in manifest['actions'] for effect in action['effects']
        if effect['before']!=effect['after']}
    if not any(row['path'] in changed for row in identity['shared_policy']):return []
    if identity['project_id']!=record['project_id'] or identity['project_root']!=record['project_root']:
        return []
    observed=d3._authority_observation(Path(identity['project_root']),{'paths':0,'bytes':0},canonical_only=True)
    if observed['error'] or observed['record'] is None:return []
    current=observed['record'];key=tuple(identity[k] for k in ('repository_id','checkout_id','client'))
    if current.get('schema_version')!=2 or key not in _scoped_entries(current):return []
    projection=canonical_scope_projection(decode_project_record(current),repository_id=key[0],checkout_id=key[1],client=key[2])
    if projection is None:return []
    projection=_canonical_plain(projection)
    # Only removed dependency declarations and client suspension may differ.
    expected=deepcopy(identity);expected['shared_policy']=projection['shared_policy']
    if projection!=expected or _scoped_entries(current)[key][2]['activation']['status']!='suspended':return []
    configuration=projection['configuration']
    if configuration['runtime']!=dict(schema_version=3,state_dir=str(context.directory)):return []
    # The responsible stop validator decides whether original historical pins remain supported.
    baseline=task_state._source_baseline(None,db,context=context)
    if baseline is None or baseline['content']['project_id']!=record['project_id'] or baseline['content']['source_contract']!=2:return []
    proposed=canonical_scope_projection(decode_project_record(record),repository_id=key[0],checkout_id=key[1],client=key[2])
    if proposed is None or _canonical_plain(proposed)!=projection:return []
    if _scoped_entries(record)[key][2]['activation']['status']!='suspended':return []
    if evidence._binding(Path(identity['project_root']))!=identity['project_binding']:return []
    if any(evidence._binding(Path(identity[path]))!=identity['bindings'][binding] for path,binding in
            (('repository_root','root'),('primary_root','primary_root'),('git_dir','git_dir'),('git_common_dir','git_common_dir'))):return []
    effects=_historical_effects(identity,_canonical_plain(proposed),manifest['actions'])
    if not effects or not (task.get('retirement') or task.get('writer_stop')):return []
    certificate=_stopped_certificate(context,db,dict(reference=reference,pins=task['pins']))
    _check_stopped_certificate(context,db,certificate)
    proof=dict(authority=reference['authority'],artifact=_stop_envelope(task)['proof'])
    return [dict(purpose='historical-stop',reference=dict(kind='task',reference=reference),task=reference,proof=proof,effect=effect)
        for effect in effects]


def _recovery_owner_refs(manifest,context,task_refs,*,use_roots=None):
    """Select actual affected-writer proofs; never inspect user payloads for lookalike refs."""
    from runtime import evidence,delegation
    roots,_=_scoped_writer_roots(manifest)
    changed_paths=_scoped_changed_paths(manifest)
    tasks=[]; assignments={}; references=[];reasons={}
    authority=_canonical_plain(context.authority_set[0])
    with evidence._context_database(context) as db:
        records=delegation._ledger(None,db,context=context)
        rows=db.execute('select scope_id,client,session_id,task_id from tasks limit 4097').fetchall()
        d3._require(len(rows)<=4096,'Recovery task discovery exceeds bound')
        for scope_id,client,session,task_id in rows:
            task=task_state._task(db,scope_id,client,session,task_id)
            row=db.execute('select repository_root,git_common_dir from checkouts where scope_id=?',(scope_id,)).fetchone()
            d3._require(row is not None,'Recovery task checkout unavailable')
            relevant=_scoped_task_relevant(row,roots,task,changed_paths)
            unresolved=[a for a in task.get('evidence',{}).get('attempts',[]) if a['status'] in {'reserved','running','uncertain'}]
            ref=dict(authority=authority,scope_id=scope_id,client=client,session_id=session,task_id=task_id)
            named=any(_canonical_plain(r)==ref for r in task_refs)
            if not (named or unresolved or relevant and (task.get('retirement') or task.get('writer_stop'))): continue
            tasks.append(dict(reference=ref,revision=task['revision'],pins=task['pins'],turn_id=task['turn_id']))
            if use_roots is not None:
                selected=_historical_recovery_roots(manifest,context,db,task,ref)
                if not selected or named:selected.append(dict(purpose='content',reference=dict(kind='task',reference=ref)))
                reasons[d3._digest(ref)]=[dict(root=root,dependencies=[dict(kind='task',reference=ref)]) for root in selected]
                use_roots.extend(reasons[d3._digest(ref)])
            start=len(references)
            for record in records:
                if all(record['owner'][k]==ref[k] for k in ('scope_id','client','session_id','task_id')): assignments[record['id']]=record
            if task.get('retirement') or task.get('writer_stop'):
                envelope=_stop_envelope(task)
                references.extend(edge['reference'] for edge in evidence._artifact_dependencies(envelope))
            else:
                for attempt in unresolved:
                    record=delegation._linked_attempt(None,scope_id,task,attempt,records,context=context,db=db,artifact_refs=references)
                    d3._require(record is not None,'Recovery task attempt has unknown ownership')
                    assignments[record['id']]=record
            if use_roots is not None:
                for reason in reasons[d3._digest(ref)]:
                    reason['dependencies'].extend(dict(kind='artifact',reference=dict(authority=authority,artifact=artifact))
                        for artifact in references[start:])
        for record in assignments.values():
            row=db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',(record['id'],)).fetchone()
            start=len(references)
            delegation._record(None,row,db=db,context=context,artifact_refs=references)
            if use_roots is not None:
                owner=dict(authority=authority,**{k:record['owner'][k] for k in ('scope_id','client','session_id','task_id')})
                assignment=dict(authority=authority,id=record['id'],owner=record['owner'],assignment=record['data']['assignment'])
                for reason in reasons[d3._digest(owner)]:
                    reason['dependencies'].append(dict(kind='assignment',reference=assignment))
                    reason['dependencies'].extend(dict(kind='artifact',reference=dict(authority=authority,artifact=artifact))
                        for artifact in references[start:])
    return tasks,[dict(authority=authority,id=r['id'],owner=r['owner'],assignment=r['data']['assignment']) for r in assignments.values()],references


def _collect_recovery_references(manifest,contexts,task_refs=(),*,use_roots=None):
    from runtime import evidence,delegation
    by_authority={d3._digest(_canonical_plain(c.authority_set[0])):c for c in contexts}
    tasks={}; assignments={}; artifacts={}; foreign={}; pending=[]; seen=set()
    def enqueue(kind,authority,reference):
        key=(kind,d3._digest(authority),d3._digest(reference))
        if key not in seen:
            d3._require(len(seen)<4096,'Recovery typed reference closure exceeds bound')
            seen.add(key); pending.append((kind,authority,reference))
    for context in contexts:
        authority=_canonical_plain(context.authority_set[0])
        selected,selected_assignments,roots=_recovery_owner_refs(manifest,context,task_refs,use_roots=use_roots)
        for task in selected: tasks[d3._digest(task['reference'])]=task
        for assignment in selected_assignments:
            assignments[d3._digest(assignment)]=assignment
        for reference in roots: enqueue('artifact',authority,reference)
    def dependency(value,authority):
        task_state._fields(value,('kind','reference')); kind=value['kind']; ref=value['reference']
        if kind=='task':
            task_state._task_reference(ref); enqueue('task',ref['authority'],ref)
        elif kind=='artifact':
            if set(ref)=={'name','digest'}:
                task_state._artifact_reference(ref); enqueue('artifact',authority,ref)
            else:
                task_state._fields(ref,('authority','artifact')); task_state._artifact_reference(ref['artifact'])
                enqueue('artifact',ref['authority'],ref['artifact'])
        elif kind=='assignment':
            task_state._fields(ref,('authority','id','owner','assignment'))
            task_state._retired_identity('assignment',ref['owner']); task_state._artifact_reference(ref['assignment'])
            d3._require(isinstance(ref['id'],str) and re.fullmatch(r'[0-9a-f]{32}',ref['id']),'Invalid recovery assignment ID')
            enqueue('assignment',ref['authority'],ref)
        elif kind=='recovery':
            if set(ref)=={'identity','artifact'}:
                task_state._fields(ref['identity'],('plan_dir','transaction_id','manifest_digest'))
                task_state._canonical_path(ref['identity']['plan_dir']); d3._text(ref['identity']['transaction_id']); d3._hash(ref['identity']['manifest_digest'])
                task_state._artifact_reference(ref['artifact']); enqueue('artifact',authority,ref['artifact'])
            else:
                task_state._fields(ref,('authority','artifact')); task_state._artifact_reference(ref['artifact'])
                enqueue('artifact',ref['authority'],ref['artifact'])
        else:
            raise Pending('Unsupported typed recovery dependency; owner observation required')
    while pending:
        kind,authority,reference=pending.pop()
        task_state._fields(authority,('directory','database','directory_binding','database_binding'))
        task_state._canonical_path(authority['directory']); task_state._canonical_path(authority['database'])
        task_state._canonical_binding(authority['directory_binding']); task_state._canonical_binding(authority['database_binding'])
        context=by_authority.get(d3._digest(authority))
        if context is None:
            foreign[d3._digest(authority)]=authority
            continue
        with evidence._context_database(context) as db:
            if kind=='artifact':
                metadata,raw=evidence._managed_artifact(None,reference,context=context,db=db)
                if use_roots is not None:
                    body=json.loads(raw)
                    d3._require(not (isinstance(body,dict) and body.get('kind')=='d4-recovery-source' and 'use_contract' in body),
                        'Restricted recovery wrapper requires exact owner admission projection')
                artifacts[d3._digest([authority,reference])]=dict(authority=authority,artifact=reference)
                for edge in metadata['dependencies']: dependency(edge,authority)
            elif kind=='task':
                task=task_state._task(db,reference['scope_id'],reference['client'],reference['session_id'],reference['task_id'])
                d3._require(task is not None,'Typed recovery task unavailable')
                tasks[d3._digest(reference)]=dict(reference=reference,revision=task['revision'],pins=task['pins'],turn_id=task['turn_id'])
            else:
                row=db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',(reference['id'],)).fetchone()
                d3._require(row is not None,'Typed recovery assignment unavailable')
                roots=[]; record=delegation._record(None,row,db=db,context=context,artifact_refs=roots)
                d3._require(record['owner']==reference['owner'] and record['data']['assignment']==reference['assignment'],'Typed recovery assignment differs')
                assignments[d3._digest(reference)]=reference
                for root in roots: enqueue('artifact',authority,root)
    return list(tasks.values()),list(assignments.values()),list(artifacts.values()),list(foreign.values())


def _recovery_use_graph(content,contexts):
    """Read the full acquired graph; annotations never replace its typed dependencies."""
    from runtime import evidence
    by_authority={d3._digest(_canonical_plain(c.authority_set[0])):c for c in contexts}
    direct=[dict(kind='task',reference=t['reference']) for t in content['tasks']]
    direct.extend(dict(kind='assignment',reference=a) for a in content['assignments'])
    direct.extend(dict(kind='artifact',reference=a) for a in content['artifacts'])
    metadata={};owners={}
    for pointer in content['artifacts']:
        context=by_authority[d3._digest(pointer['authority'])]
        with evidence._context_database(context) as db:
            observed,raw=evidence._managed_artifact(None,pointer['artifact'],context=context,db=db)
        # An outer inventory cannot turn an inherited restricted body into fresh content.
        body=json.loads(raw)
        d3._require(not (isinstance(body,dict) and body.get('kind')=='d4-recovery-source' and 'use_contract' in body),
            'Restricted recovery wrapper requires exact owner admission projection')
        name=recovery_uses.key(pointer)
        metadata[name]=[recovery_uses.qualify_dependency(edge,pointer['authority']) for edge in observed['dependencies']]
        owners[name]=observed['owner']['task']
    return direct,metadata,owners


def _build_recovery_uses(content,contexts,reasons):
    """Freeze each actual selected reason before graph deduplication can hide a mixed role."""
    direct,metadata,owners=_recovery_use_graph(content,contexts)
    roots=[reason['root'] for reason in reasons];edges=[]
    for index,reason in enumerate(reasons):
        root=reason['root'];historical=root['purpose']=='historical-stop'
        pending=[(None,edge,'retention-only' if historical else 'content') for edge in reason['dependencies']]
        seen=set()
        while pending:
            parent,dependency,purpose=pending.pop()
            if historical and purpose=='retention-only':
                ref=dependency['reference'];kind=dependency['kind']
                if kind in ('artifact','recovery'):
                    owned=owners.get(recovery_uses.key(ref))==root['task']
                elif kind=='task':owned=ref==root['task']
                else:owned=dict(authority=ref['authority'],**{k:ref['owner'][k] for k in ('scope_id','client','session_id','task_id')})==root['task']
                if not owned:purpose='content'
            route=recovery_uses.key([parent,dependency,purpose])
            if route in seen:continue
            d3._require(len(edges)<recovery_uses.MAX_ROWS,'Recovery rooted use closure exceeds bound')
            seen.add(route);edges.append(dict(root=index,parent=parent,dependency=dependency,purpose=purpose))
            # Every flattened direct dependency keeps each actual rooted reason as well.
            if parent is not None:pending.append((None,dependency,purpose))
            if dependency['kind'] in ('artifact','recovery'):
                pointer=dependency['reference'];name=recovery_uses.key(pointer)
                d3._require(name in metadata,'Recovery use has an unacquired managed parent')
                pending.extend((pointer,child,purpose) for child in metadata[name])
    try:
        contract=recovery_uses.build(roots,edges)
        recovery_uses.check_coverage(contract,direct,metadata)
        return contract
    except recovery_uses.UseContractError as exc:raise Pending(str(exc)) from None


def _project_recovery_uses(content,contexts,contracts,legacy_routes=()):
    """Release may remove proved routes; unknown replacements cannot invent admission."""
    direct,metadata,_=_recovery_use_graph(content,contexts)
    try:
        contract=recovery_uses.merge([*contracts,recovery_uses.build([],[],legacy_routes)])
        return recovery_uses.project(contract,direct,metadata)
    except recovery_uses.UseContractError as exc:raise Pending(str(exc)) from None


def _existing_recovery_age(contexts,identity,*,read_context=None):
    """Observe one original source age across stores, with no cross-store transaction held."""
    from runtime import evidence
    ages=set()
    for context in contexts:
        with evidence._context_database(context) as db:
            baseline=task_state._source_baseline(None,db,context=context)
            if baseline is None: continue
            matches=[edge for edge in baseline['dependencies'] if _canonical_plain(edge['reference']['identity'])==identity]
            d3._require(len(matches)<=1,'Conflicting original recovery source age edges')
            if matches:
                reference=_canonical_plain(matches[0]['reference']['artifact'])
                evidence._recovery_source_history(context,db,reference,identity,read_context=read_context)
                ages.add(task_state._metadata(db,reference['name'])['created_at'])
    d3._require(len(ages)<=1,'Conflicting original recovery source ages')
    return next(iter(ages)) if ages else None


def _observe_recovery_source(path, journal, contexts, task_refs):
    """Observe exact existing source owners under caller-held complete authority guards."""
    raw=d3._read_file(path/'transaction.json',MAX_JOURNAL,private=True)
    d3._require(d3.read_request(raw, limit=MAX_JOURNAL)==journal,'Recovery journal changed during source observation')
    _scoped_journal_shape(journal); contexts=tuple(contexts)
    use_roots=[]
    tasks,assignments,artifacts,foreign=_collect_recovery_references(journal['manifest'],contexts,task_refs,use_roots=use_roots)
    d3._require(not foreign,'Recovery requires another physical authority')
    for ref in task_refs:
        task_state._task_reference(_canonical_plain(ref))
        d3._require(any(t['reference']==_canonical_plain(ref) for t in tasks),'Named recovery task unavailable')
    canonical=lambda values: sorted(values,key=d3._digest)
    content=dict(version=1,kind='d4-recovery-source',identity=dict(plan_dir=str(path),transaction_id=journal['transaction_id'],manifest_digest=journal['manifest_digest']),
        journal=dict(sha256=hashlib.sha256(raw).hexdigest(),observed_at=task_state._owner_time(),state=journal['state']),
        authorities=canonical([_canonical_plain(c.authority_set[0]) for c in contexts]),tasks=canonical(tasks),assignments=canonical(assignments),artifacts=canonical(artifacts),previous=None)
    from runtime import evidence
    if any(reason['root']['purpose']=='historical-stop' for reason in use_roots):
        content['use_contract']=_build_recovery_uses(content,contexts,use_roots)
    prior_cuts=[];supersedes=[];restricted='use_contract' in content
    for context in contexts:
        authority=_canonical_plain(context.authority_set[0])
        _,sources=_recovery_sources(context)
        for reference,prior in sources:
            if prior['identity']!=content['identity']:continue
            with evidence._context_database(context) as db:
                history=evidence._recovery_source_history(context,db,reference,content['identity'])
            for prior_reference,prior_body in history:
                if 'use_contract' in prior_body:restricted=True
            if prior['version']==2:
                prior_cuts.extend(prior['stopped']);supersedes.extend(prior['supersedes'])
    if restricted and 'use_contract' not in content:
        content['use_contract']=_build_recovery_uses(content,contexts,use_roots)
    if prior_cuts or supersedes:
        content=_cut_recovery_content(content,list({d3._digest(c):c for c in prior_cuts}.values()),contexts)
        content['supersedes']=sorted({d3._digest(c):c for c in supersedes}.values(),key=d3._digest)
    generations={}
    for context in contexts:
        authority=_canonical_plain(context.authority_set[0])
        with evidence._context_database(context) as db:
            selected=[t for t in content['tasks'] if t['reference']['authority']==authority]
            for item in selected:
                ref=item['reference']; current=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
                d3._require(current is not None and all(current[k]==item[k] for k in ('revision','pins','turn_id')),
                    'Selected recovery owner changed during observation')
            generations[d3._digest(authority)]=_recovery_generation(db,selected,[a for a in content['assignments'] if a['authority']==authority])
    created_at=_existing_recovery_age(contexts,content['identity'])
    if created_at is None: created_at=task_state._owner_time()
    return _RecoverySourceObservation(_canonical_freeze(content),contexts,_canonical_freeze(generations),created_at,_RECOVERY_ISSUER,
        ready_inputs=_recovery_ready_inputs(content,contexts),
        birth_certificate=_canonical_freeze(journal['birth']['certificate']) if journal.get('birth',{}).get('certificate') else None)


def _content_linked_read_context(content,authorities,*,writer=None):
    from runtime import evidence
    certificate=next((c for c in content.get('stopped',()) if c.get('linked')),None)
    if certificate is None:return None
    return evidence._bind_linked_read_context(evidence._linked_basis_root(_canonical_plain(certificate['linked'])),
        _canonical_plain(authorities),writer=writer,basis=_canonical_plain(certificate['linked']))


def _content_linked_read_roster(content,authorities,scopes,rosters,*,read_roster=None,writer=None):
    """Select one certificate's exact subset of the already-held inspection set."""
    from runtime import evidence
    certificates=[c for c in content.get('stopped',()) if c.get('linked')]
    d3._require(len(certificates)<=1,'Recovery read routing requires one exact certificate')
    if not certificates:
        evidence._matching_linked_read_roster(read_roster,None)
        return None
    certificate=certificates[0]
    basis=_canonical_plain(certificate['linked'])
    selected=evidence._matching_linked_read_roster(read_roster,basis)
    pointer=evidence._linked_basis_root(basis);key=d3._digest(pointer)
    allowed={evidence._raw(_canonical_plain(a)) for a in authorities}
    if selected is None and key in rosters:
        selected=evidence._matching_linked_read_roster(rosters[key],basis)
    if selected is None:
        pending=sorted(basis['authorities'],key=evidence._raw)
        for _ in range(4096):
            d3._require({evidence._raw(a) for a in pending}<=allowed,'Recovery component authority is outside inspection')
            d3._require(_held_authority_guards(Path(a['directory']) for a in pending),'Recovery component authority is unheld')
            for authority in pending:evidence._check_context(evidence._reference_context(authority))
            _,expanded,_=evidence._linked_stop_sources(pointer,pending)
            if expanded==pending:break
            pending=expanded
        else:raise task_state.StateError('Recovery component read closure did not converge')
        rosters[key]=scopes.enter_context(evidence._linked_preparation_read_roster(pointer,
            pending,basis=basis,writer=writer))
        selected=evidence._matching_linked_read_roster(rosters[key],basis)
    current=evidence._check_linked_read_roster(selected,pointer,basis=basis,
        member=certificate['reference'],writer=writer)
    d3._require({evidence._raw(a) for a in current}<=allowed,'Recovery component roster exceeds inspection')
    return selected


class _RecoveryReadRosters:
    """Explicit lexical routing owner; entries hold scope tokens, never semantic results."""
    def __init__(self,authorities,scopes,*,read_roster=None):
        self.authorities=_canonical_plain(authorities)
        self.scopes=scopes
        self.supplied=read_roster
        self.rosters={}
        self.active=True

    def select(self,certificate,*,writer=None):
        d3._require(self.active,'Recovery read routing owner expired')
        return _content_linked_read_roster(dict(stopped=[certificate]),self.authorities,self.scopes,
            self.rosters,read_roster=self.supplied,writer=writer)

    def finish(self):
        from runtime import evidence
        d3._require(self.active,'Recovery read routing owner expired')
        for roster in self.rosters.values():evidence._check_linked_read_roster(roster,evidence._canonical_plain(roster.pointer))
        if self.supplied is not None:evidence._check_linked_read_roster(self.supplied,evidence._canonical_plain(self.supplied.pointer))


def _source_linked_read_context(source,*,writer=None):
    from runtime import evidence
    lifetime=_d4_publication_lifetime(source)
    if lifetime['kind']!='release':return None
    certificate=next((c for c in source.content.get('stopped',()) if c.get('linked')),None)
    if certificate is None:return None
    return evidence._bind_linked_read_context(evidence._linked_basis_root(_canonical_plain(certificate['linked'])),
        _canonical_plain(source.content['authorities']),writer=writer,basis=_canonical_plain(certificate['linked']))


def _check_recovery_source(source,context,db,*,writer=None):
    from runtime import evidence
    d3._require(isinstance(source,_RecoverySourceObservation) and source.issuer is _RECOVERY_ISSUER,'D4 owner-observed recovery source is required')
    authority=_canonical_plain(context.authority_set[0]); content=_canonical_plain(source.content)
    evidence._recovery_source_shape(content,content['identity'])
    d3._require(authority in content['authorities'],'Recovery registration physical authority differs')
    for observed in source.contexts: evidence._check_context(observed)
    originals=_canonical_plain(source.release) if source.release is not None else content
    tasks=[t for t in originals['tasks'] if t['reference']['authority']==authority]
    assignments=[a for a in originals['assignments'] if a['authority']==authority]
    d3._require(_recovery_generation(db,tasks,assignments)==source.generations[d3._digest(authority)],
        'Recovery source selected owner generation changed')
    for item in content['artifacts']:
        if item['authority']==authority:
            evidence._managed_artifact(None,item['artifact'],context=context,db=db)
    for certificate in content.get('stopped',[]):
        if certificate['reference']['authority']!=authority:continue
        read_context=None
        lifetime=getattr(_authority_context,'publication',None)
        if certificate.get('linked') and lifetime is not None and lifetime['source'] is source:
            _d4_publication_lifetime(source)
            read_context=evidence._bind_linked_read_context(evidence._linked_basis_root(certificate['linked']),content['authorities'],writer=writer,basis=certificate['linked'])
        identity=_check_stopped_certificate(context,db,certificate,writer=writer,read_context=read_context)
        historical=certificate.get('linked') and evidence._linked_component_closed(evidence._linked_basis_root(certificate['linked']),
            writer=writer if writer is not None else (context,db))
        if certificate.get('writer_stop') and identity['status']=='live' and not historical:
            key=d3._digest(certificate['reference'])
            d3._require(source.ready_inputs is not None and key in source.ready_inputs,'Ready source needs its actual bounded input observation')
            evidence._check_ready_inputs(_canonical_plain(source.ready_inputs[key]))
    if source.release is not None:
        baseline=task_state._source_baseline(None,db,context=context)
        matches=[edge for edge in baseline['dependencies'] if _canonical_plain(edge['reference']['identity'])==content['identity']]
        d3._require(len(matches)==1,'Recovery release source head unavailable')
        head=_canonical_plain(matches[0]['reference']['artifact'])
        expected=next(row['artifact'] for row in originals['heads'] if row['authority']==authority)
        if head!=expected:
            current=evidence._read(None,head,context=context,db=db)
            projection=_d4_publication_lifetime(source)['registration_projections'][str(context.database)]
            comparable=deepcopy(projection['content']); comparable['journal']['observed_at']=current['journal']['observed_at']
            d3._require(current==comparable,'Recovery release source head changed')
    evidence._check_context(context,db)
    raw=d3._read_file(Path(content['identity']['plan_dir'])/'transaction.json',MAX_JOURNAL,private=True)
    d3._require(hashlib.sha256(raw).hexdigest()==content['journal']['sha256'],'Recovery journal changed before registration')


def _release_source_inputs(source,context,db,groups):
    """Collect actual local release facts; None selects a genuine unsupported family."""
    from runtime import evidence,delegation
    authority=_canonical_plain(context.authority_set[0]);content=_canonical_plain(source.content)
    evidence._check_context(context,db)
    originals=_canonical_plain(source.release)
    tasks=[t for t in originals['tasks'] if t['reference']['authority']==authority]
    assignments=[a for a in originals['assignments'] if a['authority']==authority]
    generation=_recovery_generation(db,tasks,assignments)
    d3._require(generation==source.generations[d3._digest(authority)],'Recovery source selected owner generation changed')
    records=delegation._ledger(None,db,context=context)
    references=[c['reference'] for group in groups for c in group['certificates'] if c['reference']['authority']==authority]
    for record in records:
        if record['data']['role']!='check' or record['data']['status']!='settled' or record['data']['result']!=dict(outcome='completed'):
            return None
        if not any(all(record['owner'][key]==ref[key] for key in ('scope_id','client','session_id','task_id')) for ref in references):return None
    members=[]
    for group in groups:
        for certificate in group['certificates']:
            ref=certificate['reference']
            if ref['authority']!=authority:continue
            task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
            if task is None or not task.get('lifecycle') or task['lifecycle']['safe_closed_at'] is not None:return None
            owned=[r for r in records if all(r['owner'][key]==ref[key] for key in ('scope_id','client','session_id','task_id'))]
            if not owned:return None
            for record in owned:
                attempt=next((a for a in task.get('evidence',{}).get('attempts',[]) if a['id']==record['id']),None)
                if attempt is None or attempt['status']!='passed':return None
                reconciliation=evidence._read(None,record['data']['reconciliation'],context=context,db=db)
                d3._require(record['data'].get('lifecycle') and reconciliation.get('version')==2 and
                    reconciliation.get('proof',{}).get('kind')=='check-writer-reconciliation' and
                    record['data']['observation'] is not None and reconciliation.get('current_candidate') is not None,
                    'Release ordinary check lacks actual dated reconciliation/execution support')
            inputs=_linked_live_certificate_inputs(context,db,dict(reference=ref,pins=certificate['pins']),certificate['linked'])
            if inputs is None:return None
            prepared=evidence._linked_owned_preparation(context,db,ref)
            evidence._prepared_certificate_inputs(certificate,inputs,prepared)
            key=d3._digest(ref)
            d3._require(source.ready_inputs is not None and key in source.ready_inputs,
                'Ready source needs its actual bounded input observation')
            evidence._check_ready_inputs(_canonical_plain(source.ready_inputs[key]))
            members.append(dict(reference=ref,inputs=_canonical_plain(inputs),prepared=prepared))
    artifacts=[]
    for item in content['artifacts']:
        if item['authority']!=authority:continue
        metadata,raw=evidence._managed_artifact(None,item['artifact'],context=context,db=db)
        artifacts.append(dict(reference=item['artifact'],metadata=metadata,body_digest=hashlib.sha256(raw).hexdigest(),
            stat=evidence._closure_file_stat(Path(metadata['managed']['path']))))
    baseline=task_state._source_baseline(None,db,context=context)
    d3._require(baseline is not None,'Recovery release source baseline unavailable')
    original_baseline=_d4_publication_lifetime(source)['baselines'][str(context.database)]
    for observed in (baseline,_canonical_plain(original_baseline)):
        if any(key in observed or key in observed['content'] for key in ('birth_handoff','birth_expectation','birth_certificate')):return None
    matches=[edge for edge in baseline['dependencies'] if _canonical_plain(edge['reference']['identity'])==content['identity']]
    d3._require(len(matches)==1,'Recovery release source head unavailable')
    head=_canonical_plain(matches[0]['reference']['artifact'])
    expected=next(row['artifact'] for row in originals['heads'] if row['authority']==authority)
    head_metadata,head_raw=evidence._managed_artifact(None,head,context=context,db=db)
    current=json.loads(head_raw)
    if any(key in current for key in ('birth_handoff','birth_expectation','birth_certificate')):return None
    if head!=expected:
        comparable=deepcopy(_d4_publication_lifetime(source)['registration_projections'][str(context.database)]['content'])
        comparable['journal']['observed_at']=current['journal']['observed_at']
        d3._require(current==comparable,'Recovery release source head changed')
    rows=evidence._evidence_inventory_rows(db)
    if any(row['metadata']['status']!='live' for row in rows) or db.execute('select 1 from retired_identities limit 1').fetchone():return None
    physical=evidence._historical_inventory(context,db,rows)
    d3._require(sorted(p.name for p in (context.directory/'evidence').iterdir())==
        sorted(row['reference']['name'] for row in physical if row['stat'] is not None),
        'Unaccounted release preparation content changed')
    journal=d3._read_file(Path(content['identity']['plan_dir'])/'transaction.json',MAX_JOURNAL,private=True)
    d3._require(hashlib.sha256(journal).hexdigest()==content['journal']['sha256'],'Recovery journal changed before registration')
    evidence._check_context(context,db)
    return _canonical_freeze(dict(generation=generation,members=members,artifacts=artifacts,baseline=_canonical_plain(baseline),
        head=head,head_metadata=head_metadata,head_digest=hashlib.sha256(head_raw).hexdigest(),physical=physical,
        journal_sha256=hashlib.sha256(journal).hexdigest()))


def _check_release_source_preflight(source):
    """Release-only collect/close/full-job/recollect; supplied writers keep the full path."""
    from runtime import evidence
    lifetime=_d4_publication_lifetime(source)
    d3._require(isinstance(source,_RecoverySourceObservation) and source.issuer is _RECOVERY_ISSUER and
        source.provenance is _RECOVERY_RELEASE_ISSUER and source.release is not None and
        lifetime['kind']=='release' and lifetime.get('writer') is None,'Actual read-only release owner is required')
    content=_canonical_plain(source.content)
    if getattr(source,'birth_certificate',None) is not None or any(
            key in content for key in ('birth_handoff','birth_expectation','birth_certificate')):return False
    evidence._recovery_source_shape(content,content['identity'])
    authorities=[_canonical_plain(c.authority_set[0]) for c in source.contexts]
    d3._require(sorted(authorities,key=d3._digest)==sorted(content['authorities'],key=d3._digest) and
        len({d3._digest(a) for a in authorities})==len(authorities),'Release physical authority set differs')
    groups={}
    if content['version']!=2 or not content.get('stopped'):return False
    for certificate in content['stopped']:
        _stopped_certificate_shape(certificate)
        if not certificate.get('preparation') or certificate.get('linked',{}).get('version')!=2:return False
        pointer=evidence._linked_basis_root(certificate['linked']);key=d3._digest(pointer)
        group=groups.setdefault(key,dict(kind='release-preparation',pointer=pointer,authorities=certificate['preparation']['authorities'],certificates=[]))
        d3._require(group['authorities']==certificate['preparation']['authorities'] and
            {d3._digest(a) for a in group['authorities']}<={d3._digest(a) for a in authorities},
            'Release preparation requires its complete held authorities')
        group['certificates'].append(certificate)
    groups=list(groups.values())
    for group in groups:
        basis=group['certificates'][0]['linked']
        d3._require(all(c['linked']==basis for c in group['certificates']) and
            {d3._digest(c['reference']) for c in group['certificates']}=={d3._digest(m['task']) for m in basis['members']},
            'Release preparation omits an original component member')
    _check_d4_current_facts(source,lifetime)
    captured=[]
    for context in source.contexts:
        with evidence._context_database(context) as db:inputs=_release_source_inputs(source,context,db,groups)
        if inputs is None:return False
        captured.append(inputs)
    for group in groups:
        group['members']=[m for inputs in captured for m in _canonical_plain(inputs)['members']
            if any(m['reference']==c['reference'] for c in group['certificates'])]
        response=evidence._resolve_prepared_certificate_job(group,read_context=evidence._bind_linked_read_context(group['pointer'],group['authorities'],basis=group['certificates'][0]['linked']),serial_members=True)
        d3._require(isinstance(response,dict) and set(response)=={'job','result'} and response['job']==group and
            isinstance(response['result'],dict) and set(response['result'])=={'facts','receipts'},
            'Release preparation job is unresolved or mismatched')
        result=response['result'];evidence._prepared_certificate_facts(group['certificates'],result['facts'],result['receipts'])
        evidence._check_preparation_inputs(result['facts'])
        group['result']=result
    d3._require(_d4_publication_lifetime(source) is lifetime,'Release owner lifetime changed during semantic validation')
    _check_d4_current_facts(source,lifetime)
    for context,original in zip(source.contexts,captured):
        with evidence._context_database(context) as db:current=_release_source_inputs(source,context,db,groups)
        d3._require(current==original,'Release source inputs changed during semantic validation')
    for group in groups:
        result=group['result'];facts=result['facts']
        receipts={evidence._raw(m['reference']).decode():evidence._preparation_receipt(facts,m['reference']) for m in facts['members']}
        d3._require(receipts==result['receipts'],'Release actual preparation receipts changed during validation')
        evidence._prepared_certificate_facts(group['certificates'],facts,receipts)
        evidence._check_preparation_inputs(facts)
    _check_d4_current_facts(source,lifetime)
    return True


def _check_recovery_source_global(source):
    """Recheck each existing store before opening the next local registration writer."""
    from runtime import evidence
    lifetime=getattr(_authority_context,'publication',None)
    release=(isinstance(source,_RecoverySourceObservation) and source.release is not None and lifetime is not None and
        lifetime['active'] and lifetime['source'] is source and lifetime['kind']=='release' and lifetime.get('writer') is None)
    if not (release and _check_release_source_preflight(source)):
        for context in source.contexts:
            with evidence._context_database(context) as db:
                _check_recovery_source(source,context,db)
    read_context=None
    if release and source.content.get('stopped'):
        certificate=next((c for c in source.content['stopped'] if c.get('linked')),None)
        if certificate is not None:read_context=evidence._bind_linked_read_context(evidence._linked_basis_root(_canonical_plain(certificate['linked'])),_canonical_plain(source.content['authorities']),basis=_canonical_plain(certificate['linked']))
    age=_existing_recovery_age(source.contexts,_canonical_plain(source.content['identity']),read_context=read_context)
    d3._require(age is None or age==source.created_at,'Original recovery source age changed')


def _recovery_context(authority):
    from runtime import evidence
    path=Path(authority['database'])
    task_state._sidecars(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0.1)) as db:
        db.execute('pragma trusted_schema=off'); db.execute('begin')
        return evidence._existing_authority_context(authority,db=db)


def _recovery_sources(context,*,validate_stops=True,preparation_reader=None,read_context=None,read_roster=None):
    from runtime import evidence
    with evidence._context_database(context) as db:
        baseline=task_state._source_baseline(None,db,context=context)
        if baseline is None: return None,[]
        sources=[]
        for edge in baseline['dependencies']:
            reference=_canonical_plain(edge['reference']['artifact'])
            content=evidence._read(None,reference,context=context,db=db)
            metadata=task_state._metadata(db,reference['name'])
            d3._require(metadata['class']=='compact' and metadata['owner']==dict(kind='project',project_id=baseline['content']['project_id'],task=None)
                and metadata['closed_at'] is None and not metadata['targets'] and metadata['export'] is None,'Recovery source metadata owner differs')
            evidence._recovery_source_history(context,db,reference,_canonical_plain(edge['reference']['identity']),validate_stops=validate_stops,preparation_reader=preparation_reader,read_context=read_context,read_roster=read_roster)
            evidence._recovery_source_shape(content,_canonical_plain(edge['reference']['identity']))
            sources.append((reference,content))
        return baseline,sources


def _recovery_foreign_authorities(context,manifest):
    return _collect_recovery_references(manifest,(context,))[3]


def _validate_recovery_references(content,contexts,*,preparation_reader=None,read_context=None,read_roster=None,writer=None):
    from runtime import evidence,delegation
    by_authority={d3._digest(_canonical_plain(c.authority_set[0])):c for c in contexts}
    generations={}
    for context in contexts:
        authority=_canonical_plain(context.authority_set[0])
        with evidence._preparation_database(context,writer) as db:
            generations[d3._digest(authority)]=_recovery_generation(db,[t for t in content['tasks'] if t['reference']['authority']==authority],
                [a for a in content['assignments'] if a['authority']==authority])
            for item in content['tasks']:
                ref=item['reference']; task_state._task_reference(ref)
                if ref['authority']!=authority: continue
                task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
                d3._require(task is not None and task['pins']==item['pins'],'Recovery original task unavailable or changed')
            selected=[a for a in content['assignments'] if a['authority']==authority]
            for assignment in selected:
                capture=task_state._CAPTURE_SQL_SCOPE.get();point_owner=None
                try:
                    if capture is None:
                        row=db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',(assignment['id'],)).fetchone()
                    else:
                        resources,escrow=capture
                        d3._require(escrow is None,'Recovery assignment cannot borrow exit-validation allowance')
                        task_state._capture_registered(resources,db);point_owner=resources.current_acquisition()
                        d3._require(point_owner.token is not None and db.in_transaction,'Recovery assignment requires its active acquisition and transaction')
                        budget=evidence._RETENTION_CAPTURE_BUDGET.get()
                        d3._require(budget is None or resources.comparison is budget,'Recovery assignment changed its capture budget')
                        point_owner.reserve(requested=512,live=2048)
                        identity=assignment['id']
                        d3._require(type(identity) is str and len(identity)==32 and re.fullmatch('[0-9a-f]{32}',identity) is not None,
                            'Invalid captured recovery assignment id')
                        chars,units,integer=task_state._lifecycle_reference_size(point_owner,identity)
                        tree=4*(chars+1)+384*(units+1)+4*integer
                        # Fresh identifier encoding/parameter tuple credit precedes both measured and actual SQL.
                        point_owner.reserve(requested=4*tree+1024,live=4*tree+1024)
                        row=task_state._lifecycle_assignment_row(db,identity)
                    d3._require(row is not None,'Recovery original assignment unavailable')
                    record=delegation._record(None,row,db=db,context=context)
                    owner=record['owner']
                    original=task_state._task(db,owner['scope_id'],owner['client'],owner['session_id'],owner['task_id'])
                    d3._require(record['owner']==assignment['owner'] and record['data']['assignment']==assignment['assignment'] and original is not None
                        and original['pins']==record['body']['pins'] and original['accepted_identity']==record['body']['accepted_identity'],
                        'Recovery original assignment unavailable or changed')
                except BaseException as error:
                    if point_owner is not None:point_owner.fail(error)
                    raise
            for item in content['artifacts']:
                if item['authority']==authority:
                    evidence._managed_artifact(None,item['artifact'],context=context,db=db)
            for certificate in content.get('stopped',[]):
                if certificate['reference']['authority']==authority: _check_stopped_certificate(context,db,certificate,preparation_reader=preparation_reader,read_context=read_context,read_roster=read_roster)
            if writer is not None and writer[0].database==context.database:evidence._check_context(context,db)
    for item in [*[t['reference'] for t in content['tasks']],*content['assignments'],*content['artifacts']]:
        d3._require(d3._digest(item['authority']) in by_authority,'Recovery reference physical authority unavailable')
    return generations


@contextmanager
def _scoped_source_guards(locations,owner,manifest):
    """Reacquire a complete finite source-authority set before registration/effects."""
    pending={Path(d):None for d in locations}
    for _ in range(4096):
        expanded={}; contexts=[]
        with _authority_guards(pending,owner) as guards:
            for directory,expected in pending.items():
                database=directory/'runtime.sqlite3'
                version=locations.get(directory,(None,3))[1]
                if expected is not None:
                    context=_recovery_context(expected)
                elif version==3 and os.path.lexists(database):
                    context=_recovery_context(task_state._authority_identity(database))
                else: continue
                contexts.append(context)
                _,sources=_recovery_sources(context)
                candidates=_recovery_foreign_authorities(context,manifest)
                candidates += [a for _,content in sources for a in content['authorities']]
                for authority in candidates:
                    other=Path(authority['directory'])
                    if other not in pending: expanded[other]=authority
                    elif pending[other] is not None:
                        d3._require(pending[other]==authority,'Conflicting prior physical authority bindings')
                    else:
                        d3._require(task_state._authority_identity(other/'runtime.sqlite3')==authority,'Prior physical authority changed')
            if not expanded:
                yield guards,tuple(contexts)
                return
            d3._require(len(pending)+len(expanded)<=4096,'Recovery authority closure exceeds bound')
        pending.update(expanded)
    raise Pending('Recovery authority closure did not converge')


def _scoped_writer_locations(locations,contexts):
    # Historical schema3 reads use the exact discovered binding, never a synthetic scope.
    return dict(locations) | {context.directory:(context,3) for context in contexts}


def _d4_owned_transition(journal,key):
    record=journal['manifest']['enrollment']['record'];_,prior=_scoped_prior(journal['plan'])
    affected={tuple(member[k] for k in ('repository_id','checkout_id','client')) for member in journal['manifest']['enrollment']['affected']}
    before=_scoped_entries(prior).get(key);after=_scoped_entries(record).get(key)
    return key in affected and (before is None or after is None or _scoped_projection(prior,key)!=_scoped_projection(record,key)
        or before[2]['activation']!=after[2]['activation'])


def _d4_taskless_permission(journal,context,db,project):
    """Stored physical identities locate an owner; only fresh authority/grant facts permit bookkeeping."""
    from runtime import evidence
    from runtime.startup import decode_project_record,canonical_scope_projection
    rows=db.execute('select scope_id,physical_identity from checkouts where project_id=? limit 4097',(project,)).fetchall()
    d3._require(0<len(rows)<=4096,'Taskless D4 source Project identity is unavailable or excessive')
    identities={};roots={}
    for scope_id,raw in rows:
        identity=task_state.json_object(raw)
        task_state._fields(identity,('version','project_id','repository_id','checkout_id','project_root','project_binding',
            'primary_root','repository_root','git_dir','git_common_dir','bindings'))
        d3._require(type(identity['version']) is int and identity['version']==1 and identity['project_id']==project and
            scope_id==d3._digest([3,project,identity['repository_id'],identity['checkout_id']]),'Taskless D4 source physical identity differs')
        task_state._canonical_path(identity['project_root']);task_state._canonical_binding(identity['project_binding'])
        identities[scope_id]=identity;roots[d3._digest([identity['project_root'],identity['project_binding']])]=identity
    d3._require(len(roots)==1,'Taskless D4 source Project authority is ambiguous')
    identity=next(iter(roots.values()));root=Path(identity['project_root'])
    d3._require(evidence._binding(root)==identity['project_binding'],'Taskless D4 source Project physical ownership changed')
    observed=d3._authority_observation(root,{'paths':0,'bytes':0},canonical_only=True)
    record=journal['manifest']['enrollment']['record'];_,prior=_scoped_prior(journal['plan'])
    own=project==record['project_id'] and str(root)==record['project_root'] and identity['project_binding']==record['project_binding']
    d3._require(not observed['error'],'Taskless D4 source current Project authority is unavailable')
    candidates=[]
    if observed['record'] is not None:
        d3._require(observed['record']['project_id']==project,'Taskless D4 source current Project owner changed')
        candidates.append(('current',observed['record']))
    if own:
        candidates.extend((phase,value) for phase,value in (('before',prior),('after',record)) if value and value.get('schema_version')==2)
    else:d3._require(observed['record'] is not None,'Taskless D4 source current Project authority is unavailable')
    proofs={}
    for phase,value in candidates:
        decoded=decode_project_record(value)
        for key,(_,_,entry) in _scoped_entries(value).items():
            configuration=entry['configuration']
            if configuration is None or configuration['runtime']!=dict(schema_version=3,state_dir=str(context.directory)):continue
            if phase!='current' and not _d4_owned_transition(journal,key):continue
            if phase=='current' and entry['activation']['status']=='suspended':continue
            projection=canonical_scope_projection(decoded,repository_id=key[0],checkout_id=key[1],client=key[2])
            if projection is None:continue
            if phase=='current' and projection['membership']['included'] is not True:continue
            projection=_canonical_plain(projection);scope_id=d3._digest([3,project,key[0],key[1]])
            if identities.get(scope_id)!=task_state._physical_identity(projection):continue
            d3._require(configuration['runtime_digest']==task_state.runtime_fingerprint() and
                configuration['policy_digest']==policy_digest(task_state.PACKAGE),'Taskless D4 source current bookkeeping configuration changed')
            d3._require(all(evidence._binding(Path(projection[path]))==projection['bindings'][binding]
                for path,binding in (('repository_root','root'),('primary_root','primary_root'),('git_dir','git_dir'),('git_common_dir','git_common_dir'))),
                'Taskless D4 source physical member bindings changed')
            proof=dict(subject={k:projection[k] for k in ('project_id','repository_id','checkout_id','client')},phase=phase,identity=projection)
            proofs[d3._digest(proof)]=proof
    d3._require(proofs,'Taskless D4 source has no exact current or owned-transition bookkeeping permission')
    return dict(project_id=project,authority=_canonical_plain(context.authority_set[0]),current=observed,
        permitted=[proofs[key] for key in sorted(proofs)])


def _d4_recovery_bookkeeping(journal,source,projections,pointer,metadata,current):
    """Eligibility only: the real current host must independently consent to every inherited identity."""
    if projections is None:return None
    from runtime import evidence
    proofs=[];record=journal['manifest']['enrollment']['record']
    for database,projection in projections.items():
        for occurrence in projection.get('obligations') or []:
            dependency=occurrence['dependency']
            if recovery_uses._history_pointer(dependency)!=pointer:continue
            if dependency['kind']!='recovery' or occurrence['purpose']!='retention-only' or occurrence['requirement']!='current-permission':return None
            capture=projection['inputs'].get('history_capture')
            if capture is None or capture['authority']!=pointer['authority'] or capture['jobs']:return None
            root=projection['content']['use_contract']['roots'][occurrence['root']]
            if root['purpose']!='historical-stop':return None
            ancestors=[row for row in capture['observations'] if row['pointer']==pointer and
                recovery_uses.key(pointer) in capture['history_metadata']]
            if len(ancestors)!=1:return None
            ancestor=ancestors[0]
            d3._require(ancestor['metadata']==metadata and ancestor['content']['identity']==source.content['identity']
                and metadata['created_at']==source.created_at and metadata['owner']==dict(kind='project',project_id=record['project_id'],task=None),
                'Recovery bookkeeping predecessor lost its actual owner, identity or original age')
            support=[response for response in capture['job_results'] if response['job']==dict(kind='historical-anchor',root=root)]
            d3._require(len(support)==1 and support[0]['result']['anchor'] in capture['anchors'],
                'Recovery bookkeeping lacks its exact completed historical owner job')
            certificate=support[0]['result']['certificate'];_stopped_certificate_shape(certificate)
            identity=certificate['accepted_identity']
            d3._require(certificate['reference']==root['task'] and _stop_envelope(certificate)['proof']==root['proof']['artifact']
                and identity['project_id']==record['project_id'] and identity['project_root']==record['project_root']
                and identity['membership']['included'] and identity['configuration']['runtime']==dict(schema_version=3,state_dir=root['task']['authority']['directory']),
                'Recovery bookkeeping support is not this exact historical physical member')
            observed=d3._authority_observation(Path(identity['project_root']),{'paths':0,'bytes':0},canonical_only=True)
            d3._require(not observed['error'] and observed['record']==current,'Recovery bookkeeping current Project phase changed')
            proofs.append(dict(database=database,occurrence=occurrence,identity=identity,authority=pointer['authority'],source=pointer,
                head=capture['reference'],baseline=capture['baseline'],history=capture['history'],support=support[0],
                created_at=source.created_at,current=observed))
    return proofs or None


def _d4_permission_facts(journal,source,projections=None):
    from runtime import evidence
    record=journal['manifest']['enrollment']['record'];root=Path(record['project_root'])
    d3._require(_scoped_owned_authority(journal),'Current D4 Project authority changed outside its owned transition')
    _scoped_owner_bindings(journal['plan'])
    target=_scoped_target(root,str(PROJECT_FILE))
    current=d3.read_request(d3._read_file(root/PROJECT_FILE,128*1024)) if target['binding'] is not None else None
    subjects={};sources=[];current_permissions=[];project_permissions={};retention_bookkeeping=[]
    _,prior=_scoped_prior(journal['plan'])
    for candidate in (prior,record):
        if not candidate or candidate.get('schema_version')!=2:continue
        for key,(repository,checkout,entry) in _scoped_entries(candidate).items():
            configuration=entry['configuration']
            if configuration and configuration['runtime'] and any(
                    configuration['runtime']==dict(schema_version=3,state_dir=str(c.directory)) for c in source.contexts):
                subject=dict(project_id=candidate['project_id'],repository_id=key[0],checkout_id=key[1],client=key[2])
                subjects[d3._digest(subject)]=subject
    by_authority={d3._digest(_canonical_plain(c.authority_set[0])):c for c in source.contexts}
    refs={d3._digest(t['reference']):t['reference'] for t in _canonical_plain(source.content)['tasks']}
    artifacts={recovery_uses.key(item):item for item in _canonical_plain(source.content)['artifacts']}
    requirements={};occurrences=[]
    if projections is not None:
        for database,projection in projections.items():
            for row in projection.get('obligations') or []:
                occurrence=dict(database=database,**row);occurrences.append(occurrence)
                dependency=row['dependency'];name=recovery_uses.key(dependency)
                if requirements.get(name)!='current-permission':requirements[name]=row['requirement']
                pointer=recovery_uses._history_pointer(dependency)
                if pointer is not None:artifacts[recovery_uses.key(pointer)]=pointer
                elif dependency['kind']=='task':refs[d3._digest(dependency['reference'])]=dependency['reference']
    def needs_current(kind,reference):
        return requirements.get(recovery_uses.key(dict(kind=kind,reference=reference)))!='validated-stop-support'
    # Artifact ownership may be retained; explicit task requirements still need raw rows.
    owners={};owner_requirements={}
    for item in artifacts.values():
        context=by_authority[d3._digest(item['authority'])]
        with evidence._context_database(context) as db:
            metadata,_=evidence._managed_artifact(None,item['artifact'],context=context,db=db)
            d3._require(metadata['version']==2,'Legacy D4 body cannot gain prospective admission')
            if metadata['owner']['task'] is not None:
                ref=metadata['owner']['task']
                owners[d3._digest(ref)]=(ref,evidence._source_subject(context,db,metadata))
                uses=[requirements[name] for kind in ('artifact','recovery')
                    if (name:=recovery_uses.key(dict(kind=kind,reference=item))) in requirements]
                needed=not uses or 'current-permission' in uses
                owner_requirements[d3._digest(ref)]=owner_requirements.get(d3._digest(ref),False) or needed
            else:
                retained=_d4_recovery_bookkeeping(journal,source,projections,item,metadata,current)
                if retained is not None:
                    retention_bookkeeping.extend(retained)
                    for proof in retained:
                        subject={key:proof['identity'][key] for key in ('project_id','repository_id','checkout_id','client')}
                        subjects[d3._digest(subject)]=subject
                else:
                    key=(str(context.database),metadata['owner']['project_id'])
                    if key not in project_permissions:
                        project_permissions[key]=_d4_taskless_permission(journal,context,db,metadata['owner']['project_id'])
                        for proof in project_permissions[key]['permitted']:
                            subjects[d3._digest(proof['subject'])]=proof['subject']
            sources.append(dict(reference=item,metadata=metadata))
    for ref in refs.values():
        context=by_authority[d3._digest(ref['authority'])]
        with evidence._context_database(context) as db:
            task=task_state._task(db,ref['scope_id'],ref['client'],ref['session_id'],ref['task_id'])
            d3._require(task is not None,'Current D4 subject owner is unavailable')
            owners[d3._digest(ref)]=(ref,dict(identity=task['accepted_identity'],pins=task['pins']))
            owner_requirements[d3._digest(ref)]=owner_requirements.get(d3._digest(ref),False) or needs_current('task',ref)
    for ref,owner in owners.values():
        context=by_authority[d3._digest(ref['authority'])]
        with evidence._context_database(context) as db:
            identity=task_state._accepted_identity(owner['identity'])
            subject={k:identity[k] for k in ('project_id','repository_id','checkout_id','client')}
            subjects[d3._digest(subject)]=subject
            if not owner_requirements[d3._digest(ref)]:continue
            own=identity['project_id']==record['project_id'] and identity['project_root']==str(root)
            key=tuple(identity[k] for k in ('repository_id','checkout_id','client'))
            transition=own and _d4_owned_transition(journal,key)
            if transition:
                d3._require(evidence._binding(Path(identity['project_root']))==identity['project_binding'] and
                    all(evidence._binding(Path(identity[k]))==identity['bindings'][binding]
                    for k,binding in (('repository_root','root'),('primary_root','primary_root'),('git_dir','git_dir'),('git_common_dir','git_common_dir'))),
                    'D4 source subject physical bindings changed')
            else:
                from runtime.startup import decode_project_record,canonical_scope_projection
                observed=d3._authority_observation(Path(identity['project_root']),{'paths':0,'bytes':0},canonical_only=True)
                d3._require(not observed['error'] and observed['record'] is not None,'Current D4 source enrollment is unavailable')
                current_identity=canonical_scope_projection(decode_project_record(observed['record']),repository_id=identity['repository_id'],
                    checkout_id=identity['checkout_id'],client=identity['client'])
                d3._require(current_identity is not None and _canonical_plain(current_identity)==identity,
                    'Current D4 source subject enrollment changed')
                d3._require(_scoped_entries(observed['record'])[key][2]['activation']['status']!='suspended',
                    'Current D4 source bookkeeping permission is suspended')
                current_permissions.append(dict(subject=subject,authority=observed))
                for item in sources:
                    if item['metadata']['owner']['task']==ref:
                        permission=evidence._source_current_permission(context,dict(artifact=item['reference']['artifact'],
                            owner=item['metadata']['owner'],created_at=item['metadata']['created_at'],pins=owner['pins']),db=db)
                        current_permissions.append(dict(source=item['reference'],permission=permission))
    return dict(subjects=sorted(subjects.values(),key=d3._digest),record=dict(path=str(root/PROJECT_FILE),target=target,content=current),
        sources=sources,current_permissions=current_permissions,project_permissions=[project_permissions[key] for key in sorted(project_permissions)],
        occurrences=occurrences,retention_bookkeeping=retention_bookkeeping)


def _resolve_recovery_local_job(context,db,job,budget):
    """Validate original local raw data without opening another reader or granting authority."""
    from runtime import evidence,delegation
    evidence._check_context(context,db)
    d3._require(_held_authority_guards([context.directory]),'History job lacks its actual guarded owner')
    current=evidence._RETENTION_CAPTURE_BUDGET.get()
    d3._require(current is None or current is budget,'History job changed its lifetime budget')
    kind=job['kind'];reference=job['reference'];authority=_canonical_plain(context.authority_set[0])
    d3._require(kind in ('managed-source','task','assignment') and reference['authority']==authority,
        'History local job cannot resolve foreign or authority-bearing work')
    budget.retain(job)
    if kind in ('task','assignment'):
        budget.take(parents=1,bytes=64)
        if kind=='task':
            measured=db.execute('select length(cast(data as blob)) from tasks where scope_id=? and client=? and session_id=? and task_id=?',
                tuple(reference[k] for k in ('scope_id','client','session_id','task_id'))).fetchone()
        else:measured=db.execute('select length(cast(data as blob)) from assignments where id=?',(reference['id'],)).fetchone()
        d3._require(measured is not None and type(measured[0]) is int and measured[0]>=0,'History raw job material unavailable')
        budget.take(bytes=2*measured[0]+1024)
    if kind=='managed-source':
        observed=task_state._metadata(db,reference['artifact']['name'])
        d3._require(observed is not None,'History managed owner is unavailable')
        budget.take(bytes=observed['bytes']);budget.retain(observed)
        metadata,raw=evidence._managed_artifact(None,reference['artifact'],context=context,db=db)
        budget.take(bytes=256);budget.retain(reference)
        content=evidence._recovery_capture_json(raw,budget)
        result=dict(pointer=reference,metadata=metadata,content=content,
            stat=evidence._closure_file_stat(Path(metadata['managed']['path'])))
    elif kind=='task':
        task=task_state._task(db,reference['scope_id'],reference['client'],reference['session_id'],reference['task_id'])
        d3._require(task is not None,'History raw task obligation is unavailable')
        result=dict(reference=reference,task=task,identity=task_state._lifecycle_identity(db,'task',reference))
    elif kind=='assignment':
        row=task_state._lifecycle_assignment_row(db,reference['id'])
        d3._require(row is not None,'History raw assignment obligation is unavailable')
        record=delegation._record(None,row,db=db,context=context)
        d3._require(record['owner']==reference['owner'] and record['data']['assignment']==reference['assignment'],
            'History assignment owner returned another original assignment')
        result=dict(reference=reference,record=record,identity=task_state._lifecycle_identity(db,'assignment',reference))
    else:raise Pending('Unknown local history job')
    if kind=='task':
        task_state._task_reference(reference)
        d3._require(result['task']['pins']['accepted_scope_digest']==d3._digest(result['task']['accepted_identity']),
            'History raw task accepted identity differs from its pins')
    if kind=='assignment':
        owner=result['record']['owner'];task=task_state._task(db,owner['scope_id'],owner['client'],owner['session_id'],owner['task_id'])
        d3._require(task is not None and task['pins']==result['record']['body']['pins']
            and task['accepted_identity']==result['record']['body']['accepted_identity'],'History assignment original task pins differ')
    budget.retain(result);budget.retain(job);budget.take(bytes=128)
    return dict(job=deepcopy(job),result=_canonical_plain(result))


def _resolve_recovery_history_job(job,source,journal,budget):
    """Close the capture reader before asking the original responsible owner for its facts."""
    from runtime import evidence,delegation
    kind=job['kind']
    reference=job.get('reference',job.get('root',{}).get('task',job.get('certificate',{}).get('reference')))
    authority=reference['authority']
    contexts=[context for context in source.contexts if _canonical_plain(context.authority_set[0])==authority]
    d3._require(len(contexts)==1 and _held_authority_guards([contexts[0].directory]),'History job lacks its actual guarded owner')
    context=contexts[0]
    with evidence._context_database(context) as db:
        if kind in ('managed-source','task','assignment'):
            return _resolve_recovery_local_job(context,db,job,budget)
        elif kind=='stopped-certificate':
            certificate=job['certificate']
            result=dict(certificate=certificate,identity=_check_stopped_certificate(context,db,certificate))
        else:
            d3._require(kind=='historical-anchor','Unknown original history owner job')
            task=task_state._task(db,reference['scope_id'],reference['client'],reference['session_id'],reference['task_id'])
            d3._require(task is not None,'Historical anchor lacks its original responsible raw owner')
            roots=_historical_recovery_roots(journal['manifest'],context,db,task,reference)
            d3._require(job['root'] in roots,'History owner did not justify this exact historical occurrence')
            result=dict(anchor=dict(root=job['root'],support=[job['root']['reference'],dict(kind='artifact',reference=job['root']['proof'])]),
                certificate=_stopped_certificate(context,db,dict(reference=reference,pins=task['pins'])))
    response=dict(job=deepcopy(job),result=_canonical_plain(result));budget.retain(response)
    return response


def _capture_recovery_history(context,source,journal,budget):
    """Regenerate complete captures from exact original jobs and actual owner responses."""
    from runtime import evidence
    responses=[]
    for _ in range(recovery_uses.MAX_ROWS):
        with evidence._context_database(context) as db:
            baseline=task_state._source_baseline(None,db,context=context)
            heads=[edge['reference']['artifact'] for edge in baseline['dependencies'] if edge['reference']['identity']==source.content['identity']]
            d3._require(len(heads)<=1,'History original baseline head is ambiguous')
            captured=evidence._recovery_history_inputs(context,db,_canonical_plain(heads[0]) if heads else None,
                source,journal['manifest'],budget,responses=responses)
        if not captured['jobs']:return captured
        responses.extend(_resolve_recovery_history_job(job,source,journal,budget) for job in captured['jobs'])
    raise Pending('History owner job discovery did not converge')


def _d4_publication_lifetime(source):
    lifetime=getattr(_authority_context,'publication',None)
    d3._require(lifetime is not None and lifetime['active'] and lifetime['source'] is source,
        'Recovery publication requires its actual active owner lifetime')
    d3._require(lifetime.get('failure') is None,'Recovery publication sequence already failed')
    d3._require(all(observation['active'] and any(observation is held for held in getattr(_authority_context,'held',()))
        for observation in lifetime['guards']) and _held_authority_guards(c.directory for c in source.contexts),
        'Recovery publication guards expired or changed')
    if lifetime['kind']=='operation':
        _check_d4_grant(lifetime['grant'])
        approved=_canonical_plain(lifetime['grant'].facts)
        d3._require(approved['permissions']==lifetime['permissions'] and
            {item['authority']['database']:item['baseline'] for item in approved['baselines']}==lifetime['baselines'],
            'Recovery original permissions or baselines differ from actual approved acquisition')
        d3._require(approved['registration_projections']==lifetime['registration_projections'] and
            approved['local_chain_inputs']==lifetime['local_chain_inputs'],
            'Recovery registration inputs differ from actual approved acquisition')
        d3._require(approved.get('birth_certificate')==(_canonical_plain(source.birth_certificate) if source.birth_certificate is not None else None),
            'Birth observation differs from actual host acquisition')
        d3._require(approved['source']==_canonical_plain(source.content) and approved['generations']==_canonical_plain(source.generations)
            and approved['created_at']==source.created_at,'D4 source content differs from actual approved acquisition')
    return lifetime


def _taskless_chain_content(content):
    return content['version']==1 and all(content[key]==[] for key in ('tasks','assignments','artifacts')) and not any(
        key in content for key in ('stopped','supersedes','use_contract','birth_handoff'))


def _taskless_publication(source,lifetime):
    """Select only the acquired complete local-chain family; this is not permission."""
    from runtime import evidence
    if source.birth_certificate is not None or any('birth_handoff' in row['content'] for row in lifetime.get('registration_projections',{}).values()):return False
    if lifetime['kind']!='operation' or source.release is not None or not _taskless_chain_content(_canonical_plain(source.content)):return False
    acquired=lifetime['local_chain_inputs']
    if acquired is None:return False
    if set(acquired)!={str(c.database) for c in source.contexts}:return False
    for context in source.contexts:
        key=str(context.database);baseline=lifetime['baselines'][key];rows=acquired[key]
        authority=_canonical_plain(context.authority_set[0])
        if len(rows)!=len(baseline['dependencies']):return False
        for edge,inputs in zip(baseline['dependencies'],rows):
            if (inputs['authority']!=authority or inputs['baseline']!=baseline or inputs['identity']!=edge['reference']['identity'] or
                    inputs['reference']!=edge['reference']['artifact'] or inputs['jobs'] or inputs['metadata'] or inputs['local_sources']):return False
            if not inputs['history'] or inputs['history'][0]['reference']!=inputs['reference']:return False
            for item in inputs['history']:
                body=item['content'];dependencies=item['metadata']['dependencies']
                if not _taskless_chain_content(body) or dependencies!=evidence._recovery_source_dependencies(body,authority):return False
                if any(d['kind']!='recovery' or d['reference']['authority']!=authority for d in dependencies):return False
        projection=lifetime['registration_projections'][key]
        if (not _taskless_chain_content(projection['content']) or projection['inputs']['jobs'] or
                projection['inputs']['local_sources'] or projection['inputs']['metadata']):return False
    return True


def _check_local_chain_inputs(source,context,db,lifetime):
    from runtime import evidence
    evidence._check_context(context,db)
    key=str(context.database);registration=lifetime['registrations'].get(key)
    projection=lifetime['registration_projections'][key]
    expected=deepcopy(projection['inputs']['baseline'])
    d3._require(expected==lifetime['baselines'][key],'Recovery original baseline differs from its approved projection')
    if registration is not None:
        reference=registration['reference']
        if reference is not None and reference!=projection['reference']:
            identity=projection['content']['identity']
            edges=[edge for edge in expected['dependencies'] if edge['reference']['identity']!=identity]
            edges.append(dict(kind='recovery',reference=dict(identity=identity,artifact=reference)))
            expected['dependencies']=sorted(edges,key=evidence._raw)
        d3._require(registration['expected']==expected,'Recovery registration comparison differs from its exact owned overlay')
    d3._require(_canonical_plain(task_state._source_baseline(None,db,context=context))==expected,
        'Recovery original baseline changed after authenticated acquisition' if registration is None else
        'Recovery local chain baseline differs from its exact owned overlay')
    for inputs in lifetime['local_chain_inputs'][key]:
        for item in inputs['history']:
            metadata,raw=evidence._managed_artifact(None,item['reference'],context=context,db=db)
            d3._require(metadata==item['metadata'] and json.loads(raw)==item['content'],
                'Recovery acquired local chain metadata or content changed')


def _check_d4_current_facts(source,lifetime):
    d3._require(_d4_publication_lifetime(source) is lifetime,'Recovery publication lifetime changed')
    if lifetime['kind']=='operation':
        if lifetime.get('birth_reobserve') is not None:lifetime['birth_reobserve']()
        if 'use_contract' in source.content and lifetime.get('writer') is not None:
            _check_operation_permission_files(lifetime)
        else:
            d3._require(_d4_permission_facts(lifetime['journal'],source,
                lifetime['registration_projections'] if 'use_contract' in source.content else None)==lifetime['permissions'],
                'Current D4 subject, source or owned enrollment transition changed')
        for location in lifetime['markers']:
            d3._require(_authority_owner(location)==lifetime['owner'],'D4 protective operation marker changed')
        _check_operation_files(Path(source.content['identity']['plan_dir']),lifetime['journal'])
    else:
        d3._require(source.provenance is _RECOVERY_RELEASE_ISSUER and source.release is not None,
            'Recovery release requires its distinct actual factory')
        d3._require(_scoped_owned_authority(lifetime['journal']),'Recovery release owned effects changed')
        _scoped_recovery_files(Path(source.content['identity']['plan_dir']),lifetime['journal'])
        current=_load_journal(Path(source.content['identity']['plan_dir']))
        d3._require(current==lifetime['journal'],'Recovery release journal changed')
        for action in current['manifest']['actions']:
            receipt=current['receipts'][action['id']]
            for effect,file in zip(action['effects'],receipt['files']):
                root=Path(action['owner']['root']);target=_scoped_target(root,effect['path'])
                expected=effect['before'] if current['state']=='rolled_back' else effect['after']
                binding=file['before_binding'] if current['state']=='rolled_back' else file['result_binding']
                d3._require(_fingerprint(root,effect['path'])==expected and target['binding']==binding,
                    'Recovery release effect changed before commit')


def _check_d4_publication(source,context,db):
    from runtime import evidence
    lifetime=_d4_publication_lifetime(source)
    writer=(context,db)
    if lifetime['kind']=='operation' and 'use_contract' in source.content:
        held=lifetime.get('writer')
        d3._require(held is not None and held[0] is context and held[1] is db,'Annotated recovery requires its actual supplied writer')
        _check_operation_source_local(source,context,db,lifetime)
        for other in source.contexts:
            if other.database!=context.database:
                d3._require(_operation_database_files(other)==lifetime['serial_databases'][str(other.database)],
                    'Prepared remote recovery comparison changed inside supplied writer')
    elif _taskless_publication(source,lifetime):
        held=lifetime.get('writer')
        d3._require(held is not None and held[0] is context and held[1] is db,
            'Recovery publication requires its actual supplied writer')
        _check_recovery_source(source,context,db,writer=writer)
        _check_local_chain_inputs(source,context,db,lifetime)
    else:
        # Unmigrated families retain their complete original semantic path.
        _check_recovery_source(source,context,db,writer=writer)
        for other in source.contexts:
            if other.database!=context.database:
                with evidence._context_database(other) as reader:_check_recovery_source(source,other,reader,writer=writer)
    _check_d4_current_facts(source,lifetime)
    return lifetime


def _operation_database_files(context):
    from runtime import evidence
    evidence._check_context(context)
    paths=[context.database,*[Path(str(context.database)+suffix) for suffix in ('-wal','-shm','-journal')]]
    return {str(path):evidence._closure_file_stat(path) if path.exists() else None for path in paths}


def _check_operation_permission_files(lifetime):
    from runtime import evidence
    permissions=lifetime['permissions'];record=permissions['record'];path=Path(record['path'])
    root=Path(lifetime['journal']['manifest']['enrollment']['record']['project_root'])
    d3._require(path==root/PROJECT_FILE and _scoped_target(root,str(PROJECT_FILE))==record['target'] and
        (d3.read_request(d3._read_file(path,128*1024)) if path.exists() else None)==record['content'],
        'Current D4 acquired Project record changed')
    observations=[row['authority'] for row in permissions['current_permissions'] if 'authority' in row]
    observations.extend(row['current'] for row in permissions['project_permissions'])
    observations.extend(row['current'] for row in permissions['retention_bookkeeping'])
    for original in observations:
        value=original['record']
        d3._require(value is not None and d3._authority_observation(Path(value['project_root']),{'paths':0,'bytes':0},canonical_only=True)==original,
            'Current D4 acquired source authority changed')
    for row in permissions['current_permissions']:
        if 'permission' not in row:continue
        observed=row['permission'];file=Path(observed['path'])
        d3._require(evidence._closure_file_stat(file)==observed['stat'] and
            hashlib.sha256(d3._read_file(file,128*1024)).hexdigest()==observed['sha256'],
            'Current D4 acquired source permission file changed')


def _check_operation_source_local(source,context,db,lifetime):
    """Compare original local inputs and exact owned overlays; never open a peer reader."""
    from runtime import evidence
    evidence._check_context(context,db);authority=_canonical_plain(context.authority_set[0])
    content=_canonical_plain(source.content)
    evidence._recovery_source_shape(content,content['identity'])
    tasks=[row for row in content['tasks'] if row['reference']['authority']==authority]
    assignments=[row for row in content['assignments'] if row['authority']==authority]
    d3._require(_recovery_generation(db,tasks,assignments)==source.generations[d3._digest(authority)],
        'Recovery source selected owner generation changed')
    capture=lifetime['registration_projections'][str(context.database)]['inputs']['history_capture']
    for response in capture['job_results']:
        job=response['job'];result=response['result'];reference=job.get('reference')
        if reference is None or reference['authority']!=authority:continue
        if job['kind']=='task':
            task=task_state._task(db,reference['scope_id'],reference['client'],reference['session_id'],reference['task_id'])
            d3._require(task==result['task'] and task_state._lifecycle_identity(db,'task',reference)==result['identity'],
                'Recovery original local task job changed')
        elif job['kind']=='assignment':
            row=db.execute('select data from assignments where id=?',(reference['id'],)).fetchone()
            d3._require(row is not None and json.loads(row[0])==result['record']['data'] and
                task_state._lifecycle_identity(db,'assignment',reference)==result['identity'],'Recovery original local assignment job changed')
    expected=lifetime['registrations'].get(str(context.database),{}).get('expected',lifetime['baselines'][str(context.database)])
    d3._require(_canonical_plain(task_state._source_baseline(None,db,context=context))==expected,
        'Recovery baseline differs from its exact original or owned overlay')
    evidence._check_recovery_projection(context,db,source,lifetime)
    raw=d3._read_file(Path(content['identity']['plan_dir'])/'transaction.json',MAX_JOURNAL,private=True)
    d3._require(hashlib.sha256(raw).hexdigest()==content['journal']['sha256'],'Recovery journal changed before registration')


def _check_operation_publication_serial(source):
    """Run full original owner jobs with every capture/writer closed, then recollect comparisons."""
    from runtime import evidence
    lifetime=_d4_publication_lifetime(source)
    d3._require(lifetime['kind']=='operation' and 'use_contract' in source.content and lifetime.get('writer') is None,
        'Annotated serial check requires its actual operation acquisition')
    for context in source.contexts:
        projection=lifetime['registration_projections'][str(context.database)]
        for response in projection['inputs']['history_capture']['job_results']:
            current=_resolve_recovery_history_job(response['job'],source,lifetime['journal'],lifetime['capture_budget'])
            d3._require(current==response,'Original recovery owner job changed after capture')
    _check_d4_current_facts(source,lifetime)
    for context in source.contexts:
        with evidence._context_database(context) as db:
            _check_operation_source_local(source,context,db,lifetime)
            if str(context.database) in lifetime['registrations']:
                evidence._verify_recovery_registration(context,db,source,lifetime)
    _check_d4_current_facts(source,lifetime)
    lifetime['serial_databases']={str(context.database):_operation_database_files(context) for context in source.contexts}


def _check_taskless_publication_serial(source):
    from runtime import evidence
    lifetime=_d4_publication_lifetime(source)
    d3._require(_taskless_publication(source,lifetime) and lifetime.get('writer') is None,
        'Serial recovery validation requires closed local writers and its acquired family')
    _check_recovery_source_global(source)
    for context in source.contexts:
        with evidence._context_database(context) as db:
            _check_local_chain_inputs(source,context,db,lifetime)
            evidence._check_recovery_projection(context,db,source,lifetime)
            if str(context.database) in lifetime['registrations']:
                evidence._verify_recovery_registration(context,db,source,lifetime)
    _check_d4_current_facts(source,lifetime)


@contextmanager
def _d4_publication_batch(path,journal,contexts,request,host,owner,markers):
    from runtime import evidence
    if not contexts:
        for location in markers:_atomic(location.parent,location.name+'.json',json.dumps(owner,sort_keys=True).encode())
        yield None
        return
    if not isinstance(host,_D4HostCapability):raise Pending('Actual trusted D4 host authority is unavailable; bookkeeping remains pending')
    if journal.get('birth',{}).get('certificate'):
        _birth_current_history(journal['manifest']['enrollment']['record'], journal['birth']['intent']['history'])
    source=_observe_recovery_source(path,journal,contexts,())
    annotated='use_contract' in source.content
    budget=recovery_uses.CaptureBudget() if annotated else None
    captures={str(context.database):_capture_recovery_history(context,source,journal,budget) for context in contexts} if annotated else {}
    baselines=[];projections={};local_chains={};chain_rows=0
    for context in contexts:
        with evidence._context_database(context) as db:
            baseline=task_state._source_baseline(None,db,context=context)
            d3._require(baseline is not None and baseline['content']['source_contract']==2,'D4 requires original source_contract2 coverage')
            baselines.append(dict(authority=_canonical_plain(context.authority_set[0]),baseline=_canonical_plain(baseline)))
            content=_canonical_plain(source.content)
            if _taskless_chain_content(content) and local_chains is not None:
                rows=[]
                for edge in _canonical_plain(baseline)['dependencies']:
                    observed=evidence._recovery_use_inputs(context,db,edge['reference']['artifact'],edge['reference']['identity'])
                    rows.append(observed);chain_rows+=len(observed['history'])
                    if chain_rows>4096:
                        local_chains=None
                        break
                if local_chains is not None:local_chains[str(context.database)]=rows
            heads=[edge['reference']['artifact'] for edge in baseline['dependencies'] if _canonical_plain(edge['reference']['identity'])==content['identity']]
            d3._require(len(heads)<=1,'Recovery original baseline head is ambiguous')
            inputs=evidence._recovery_use_inputs(context,db,_canonical_plain(heads[0]) if heads else None,content['identity'],content=content)
            if annotated:
                inputs['history_capture']=captures[str(context.database)]
                authority=_canonical_plain(context.authority_set[0])
                for row in inputs['history_capture']['observations']:
                    if row['pointer']['authority']==authority:
                        observed=dict(reference=row['reference'],metadata=row['metadata'])
                        if observed not in inputs['local_sources']:inputs['local_sources'].append(observed)
            projections[str(context.database)]=evidence._recovery_registration_projection(content,source.created_at,inputs,
                birth_certificate=_canonical_plain(source.birth_certificate) if source.birth_certificate is not None else None,budget=budget)
    permissions=_d4_permission_facts(journal,source,projections if annotated else None)
    for item in baselines:
        project=item['baseline']['content']['project_id']
        d3._require(project==journal['manifest']['enrollment']['record']['project_id'] or any(
            subject['project_id']==project for subject in permissions['subjects']),'D4 Project owner lacks exact current subject coverage')
    facts=dict(operation='protective-recovery-bookkeeping',request=request,plan=journal['plan'],identity=_canonical_plain(source.content)['identity'],
        journal=_canonical_plain(source.content)['journal'],permissions=permissions,baselines=baselines,
        source=_canonical_plain(source.content),generations=_canonical_plain(source.generations),created_at=source.created_at,
        registration_projections=projections,local_chain_inputs=local_chains,
        birth_certificate=_canonical_plain(source.birth_certificate) if source.birth_certificate is not None else None)
    with ExitStack() as history_guard:
        reobserve_birth=None
        if source.birth_certificate is not None:
            from runtime import review_storage
            history,reobserve_birth=history_guard.enter_context(review_storage._capture_birth_history(journal['manifest']['enrollment']['record']))
            d3._require(history==journal['birth']['intent']['history'],'Birth history changed before host acquisition')
        with _acquire_d4_grant(host,facts) as grant:
            if reobserve_birth is not None:reobserve_birth()
            for location in markers:_atomic(location.parent,location.name+'.json',json.dumps(owner,sort_keys=True).encode())
            lifetime=dict(active=True,kind='operation',source=source,grant=grant,permissions=permissions,journal=deepcopy(journal),
                guards=tuple(getattr(_authority_context,'held',())),markers=tuple(markers),owner=owner,registrations={},
                baselines={str(context.database):item['baseline'] for context,item in zip(contexts,baselines)},
                registration_projections=_canonical_plain(grant.facts)['registration_projections'],
                local_chain_inputs=_canonical_plain(grant.facts)['local_chain_inputs'],writer=None,birth_reobserve=reobserve_birth,
                capture_budget=budget)
            previous=getattr(_authority_context,'publication',None);_authority_context.publication=lifetime
            try:yield lifetime
            finally:lifetime['active']=False;_authority_context.publication=previous


def _scoped_register_sources(path,journal,contexts):
    """Called only under the operation's complete ordered authority guards."""
    from runtime import evidence
    if not contexts: return
    lifetime=getattr(_authority_context,'publication',None)
    d3._require(lifetime is not None and lifetime['kind']=='operation','D4 source publication requires actual host acquisition')
    source=lifetime['source']
    d3._require(tuple(contexts)==source.contexts,'D4 registration omitted an acquired authority')
    if 'use_contract' in source.content:
        try:
            for context in contexts:
                _check_operation_publication_serial(source)
                try:
                    with evidence._context_database(context,write=True,durable=True,
                            before_commit=lambda db,context=context:evidence._check_recovery_registration(context,db,source)) as db:
                        lifetime['writer']=(context,db)
                        evidence._register_recovery_source(None,db,source,context=context)
                finally:lifetime['writer']=None
                _check_operation_publication_serial(source)
            d3._require(set(lifetime['registrations'])=={str(c.database) for c in contexts},'D4 all-peer registrations are incomplete')
            _check_operation_publication_serial(source)
            return
        except BaseException:
            lifetime['failure']='annotated-publication-failed'
            raise
    if _taskless_publication(source,_d4_publication_lifetime(source)):
        try:
            for context in contexts:
                _check_taskless_publication_serial(source)
                try:
                    with evidence._context_database(context,write=True,durable=True,
                            before_commit=lambda db,context=context:evidence._check_recovery_registration(context,db,source)) as db:
                        lifetime['writer']=(context,db)
                        evidence._register_recovery_source(None,db,source,context=context)
                finally:lifetime['writer']=None
                _check_taskless_publication_serial(source)
            d3._require(set(lifetime['registrations'])=={str(c.database) for c in contexts},'D4 all-peer registrations are incomplete')
            _check_taskless_publication_serial(source)
            return
        except BaseException:
            lifetime['failure']='serial-publication-failed'
            raise
    for context in contexts:
        _check_recovery_source_global(source)
        with evidence._context_database(context,write=True,durable=True,before_commit=lambda db,context=context:evidence._check_recovery_registration(context,db,source)) as db:
            # Legacy/baselineless lifetimes remain unknown; never retrofit a baseline.
            d3._require(task_state._source_baseline(None,db,context=context) is not None,
                'Existing schema3 source baseline is unavailable; preserve recovery markers')
            evidence._register_recovery_source(None,db,source,context=context)
    d3._require(set(lifetime['registrations'])=={str(context.database) for context in contexts},'D4 all-peer registrations are incomplete')
    for context in contexts:
        with evidence._context_database(context) as db:evidence._check_recovery_registration(context,db,source)


def _recovery_release_journal(identity):
    path=d3._plan_path(identity['plan_dir']); journal=_load_journal(path)
    d3._require(journal is not None and journal['schema_version']==2 and
        all(journal[k]==identity[k] for k in ('transaction_id','manifest_digest')),'Recovery release journal identity differs')
    d3._require(journal['state'] in ('applied','rolled_back'),'Recovery release requires a settled physical journal')
    _validate_scoped(dict(schema_version=2,manifest=journal['manifest'],approval=journal['approval']),journal['plan'])
    return path,journal


@contextmanager
def _recovery_release_guards(authorities,identity):
    path,journal=_recovery_release_journal(identity)
    manifest=journal['manifest']; plan=journal['plan']; root=Path(manifest['enrollment']['record']['project_root'])
    before,prior=_scoped_prior(plan)
    lock_root=d3._plan_path(LOCK_ROOT)
    keys={'destination:'+str(root/PROJECT_FILE),'parent:'+str(root)+':'+d3._digest(plan['identity']['binding'])}
    if before['binding']: keys.add('inode:'+d3._digest(before['binding']))
    with ExitStack() as locks:
        for key in sorted(keys):
            location=lock_root/('canonical-'+hashlib.sha256(key.encode()).hexdigest()+'.lock')
            try:
                fd=os.open(location,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
            except FileExistsError: pass
            locks.enter_context(_lock(location))
            d3._require(_authority_owner(location) is None,'Recovery release has an active canonical operation')
        git_dirs={a['owner']['git_dir'] for a in manifest['owners'] if a['owner']['kind']=='repository'}
        if plan['identity']['kind']=='standalone':
            git_dirs.update(a['owner']['git_common_dir'] for a in manifest['owners'] if a['owner']['kind']=='repository')
        for git_dir in sorted(git_dirs):
            locks.enter_context(_worktree_lock(git_dir))
            d3._require(_command_owner(git_dir) is None,'Recovery release has an active command')
        locations=_scoped_runtime_locations([prior,manifest['enrollment']['record']])
        supplied={Path(a['directory']):a for a in _canonical_plain(authorities)}
        d3._require(0<len(supplied)<=4096,'Recovery release authority set is empty or excessive')
        for directory,authority in supplied.items():
            d3._require(task_state._authority_identity(Path(authority['database']))==authority,'Recovery release physical authority differs')
            locations[directory]=(_recovery_context(authority),3)
        with _scoped_source_guards(locations,None,manifest) as (_,contexts):
            d3._require(_load_journal(path)==journal,'Recovery release journal changed while acquiring guards')
            _scoped_owner_bindings(plan);_scoped_recovery_files(path,journal)
            d3._require(_scoped_owned_authority(journal),'Recovery release canonical physical ownership changed')
            for action in manifest['actions']:
                receipt=journal['receipts'].get(action['id'])
                d3._require(receipt is not None,'Recovery release action receipt is missing')
                if receipt.get('command'):
                    d3._require(receipt['command']['stopped'] is True,'Recovery release command descendants remain uncertain')
                for effect,file in zip(action['effects'],receipt['files']):
                    root=Path(action['owner']['root']); current=_scoped_target(root,effect['path'])
                    expected=effect['before'] if journal['state']=='rolled_back' else effect['after']
                    binding=file['before_binding'] if journal['state']=='rolled_back' else file['result_binding']
                    d3._require(_fingerprint(root,effect['path'])==expected and current['binding']==binding,
                        'Recovery release effect physical ownership changed')
            roots,git_roots=_scoped_writer_roots(manifest)
            _scoped_writers(_scoped_writer_locations(locations,contexts),roots,git_roots,changed_paths=_scoped_changed_paths(manifest))
            yield path,journal,contexts


def _observe_recovery_release(authorities,identity):
    """Private stopped-proof replacement; neither general closure nor public release."""
    from runtime import evidence
    identity=_canonical_plain(identity)
    with _recovery_release_guards(authorities,identity) as (path,journal,contexts):
        read_context=None
        heads=[]; supersedes=[]; tasks={};assignments={};artifacts={};certificates={}; generations={};ages=set();contracts=[];legacy_sources=[];local_supersedes={}
        for context in contexts:
            authority=_canonical_plain(context.authority_set[0])
            read_context=None
            with evidence._context_database(context) as db:
                actual_baseline=task_state._source_baseline(None,db,context=context)
                for edge in actual_baseline['dependencies']:
                    if _canonical_plain(edge['reference']['identity'])!=identity:continue
                    head=evidence._read(None,_canonical_plain(edge['reference']['artifact']),context=context,db=db)
                    linked=next((c['linked'] for c in head.get('stopped',[]) if c.get('linked')),None)
                    if linked is not None:
                        read_context=evidence._bind_linked_read_context(evidence._linked_basis_root(linked),[_canonical_plain(c.authority_set[0]) for c in contexts],writer=(context,db),basis=linked)
            baseline,sources=_recovery_sources(context,read_context=read_context)
            d3._require(baseline is not None and baseline['content']['source_contract']==2,'Recovery release requires creation-time D4 coverage')
            matches=[(ref,c) for ref,c in sources if c['identity']==identity]
            d3._require(len(matches)==1,'Recovery release source head unavailable')
            reference,current=matches[0];heads.append(dict(authority=authority,artifact=reference))
            raw=d3._read_file(path/'transaction.json',MAX_JOURNAL,private=True)
            d3._require(current['journal']['sha256']==hashlib.sha256(raw).hexdigest() and current['journal']['state']==journal['state'],
                'Recovery release journal snapshot requires reconciliation')
            with evidence._context_database(context) as db:
                ages.add(task_state._metadata(db,reference['name'])['created_at'])
                history=evidence._recovery_source_history(context,db,reference,identity,read_context=read_context)
            local_receipts=[]
            for prior_ref,content in history:
                _validate_recovery_references(content,contexts,read_context=read_context)
                if 'use_contract' in content:contracts.append(content['use_contract'])
                else:
                    origin=dict(authority=authority,artifact=prior_ref,identity=content['identity'],journal_sha256=content['journal']['sha256'])
                    legacy_sources.append((origin,content))
                if content['version']==2:
                    for row in content['supersedes']: supersedes.append(row)
                    local_receipts.extend(content['supersedes'])
                    for c in content['stopped']:certificates[d3._digest(c['reference'])]=c
                else:
                    supersedes.append(dict(authority=authority,artifact=prior_ref,identity=identity,journal_sha256=content['journal']['sha256']))
                    local_receipts.append(supersedes[-1])
                for task in content['tasks']:tasks[d3._digest(task['reference'])]=task
                for assignment in content['assignments']:assignments[d3._digest(assignment)]=assignment
                for artifact in content['artifacts']:artifacts[d3._digest(artifact)]=artifact
            local_supersedes[str(context.database)]=sorted({d3._digest(row):row for row in local_receipts}.values(),key=d3._digest)
        d3._require(len(ages)==1,'Recovery release original source ages differ')
        for context in contexts:
            authority=_canonical_plain(context.authority_set[0])
            with evidence._context_database(context) as db:
                selected=[t for t in tasks.values() if t['reference']['authority']==authority]
                associated=[a for a in assignments.values() if a['authority']==authority]
                generations[d3._digest(authority)]=_recovery_generation(db,selected,associated)
                for task in selected:
                    read_context=None
                    actual=task_state._task(db,*[task['reference'][k] for k in ('scope_id','client','session_id','task_id')])
                    if actual.get('writer_stop',{}).get('linked'):
                        linked=evidence._read(None,actual['writer_stop']['linked'],context=context,db=db)
                        read_context=evidence._bind_linked_read_context(evidence._linked_basis_root(linked),[_canonical_plain(c.authority_set[0]) for c in contexts],writer=(context,db),basis=linked)
                    cert=_stopped_certificate(context,db,task,read_context=read_context);certificates[d3._digest(cert['reference'])]=cert
        canonical=lambda rows:sorted(rows,key=d3._digest)
        content=dict(version=2,kind='d4-recovery-source',identity=identity,
            journal=dict(sha256=hashlib.sha256(raw).hexdigest(),observed_at=task_state._owner_time(),state=journal['state']),
            authorities=canonical([_canonical_plain(c.authority_set[0]) for c in contexts]),tasks=canonical(list(tasks.values())),
            assignments=canonical(list(assignments.values())),artifacts=canonical(list(artifacts.values())),previous=None,
            supersedes=canonical(list({d3._digest(row):row for row in supersedes}.values())))
        if contracts:
            legacy_routes=[recovery_uses.legacy_projection(origin,body) for origin,body in legacy_sources]
            try:content['use_contract']=recovery_uses.merge([*contracts,recovery_uses.build([],[],legacy_routes)])
            except recovery_uses.UseContractError as exc:raise Pending(str(exc)) from None
        content=_cut_recovery_content(content,list(certificates.values()),contexts,read_context=read_context)
        original=dict(heads=heads,tasks=list(tasks.values()),assignments=list(assignments.values()),supersedes=local_supersedes)
        return _RecoverySourceObservation(_canonical_freeze(content),tuple(contexts),_canonical_freeze(generations),next(iter(ages)),_RECOVERY_ISSUER,_canonical_freeze(original),
            _recovery_ready_inputs(content,contexts,read_context=read_context),_RECOVERY_RELEASE_ISSUER)


def _recovery_release_template(source,context,*,planned=True):
    """Use only issuer-observed local receipts and the active owner's exact replay decision."""
    content=_canonical_plain(source.content)
    if source.release is None:return content
    d3._require(source.issuer is _RECOVERY_ISSUER and source.provenance is _RECOVERY_RELEASE_ISSUER
        and any(context is actual for actual in source.contexts),'Release template lacks its actual issuer/context')
    content['supersedes']=_canonical_plain(source.release['supersedes'][str(context.database)])
    lifetime=getattr(_authority_context,'publication',None)
    if planned and lifetime is not None and lifetime.get('source') is source:
        lifetime=_d4_publication_lifetime(source)
        decision=lifetime.get('release_decisions',{}).get(str(context.database))
        if decision is not None and decision['reference'] is None:
            prior=decision['inputs']['history'][0];body=prior['content']
            if body['version']==2:
                receipt=dict(authority=_canonical_plain(context.authority_set[0]),artifact=prior['reference'],
                    identity=body['identity'],journal_sha256=body['journal']['sha256'])
                content['supersedes']=sorted({d3._digest(row):row for row in [*content['supersedes'],receipt]}.values(),key=d3._digest)
    d3._require(len(content['supersedes'])<=4096,'Recovery source reference bound exceeded')
    return content


def _register_recovery_release(observation):
    """Sequential existing-store replacement; mixed heads retain old raw requirements."""
    from runtime import evidence
    replacements=[]
    try:
        d3._require(isinstance(observation,_RecoverySourceObservation) and observation.issuer is _RECOVERY_ISSUER and observation.provenance is _RECOVERY_RELEASE_ISSUER
            and observation.release is not None,'Recovery release requires owner-issued observation')
        content=_canonical_plain(observation.content)
        with _recovery_release_guards(content['authorities'],content['identity']) as (_,journal,fresh_contexts):
            d3._require(sorted([_canonical_plain(c.authority_set[0]) for c in fresh_contexts],key=d3._digest)==sorted([_canonical_plain(c.authority_set[0]) for c in observation.contexts],key=d3._digest),
                'Recovery release complete authority set changed')
            contexts=observation.contexts
            lifetime=dict(active=True,kind='release',source=observation,journal=deepcopy(journal),
                guards=tuple(getattr(_authority_context,'held',())),registrations={},baselines={},registration_projections={})
            budget=recovery_uses.CaptureBudget() if 'use_contract' in content else None
            lifetime['capture_budget']=budget
            previous=getattr(_authority_context,'publication',None);_authority_context.publication=lifetime
            try:
                def project(context):
                    template=_recovery_release_template(observation,context)
                    capture=_capture_recovery_history(context,observation,journal,budget) if budget is not None else None
                    with evidence._context_database(context) as db:
                        baseline=_canonical_plain(task_state._source_baseline(None,db,context=context))
                        key=str(context.database)
                        if key in lifetime['baselines']:
                            d3._require(baseline==lifetime['baselines'][key],'Release planning baseline changed')
                        else:lifetime['baselines'][key]=baseline
                        heads=[edge['reference']['artifact'] for edge in baseline['dependencies'] if edge['reference']['identity']==content['identity']]
                        d3._require(len(heads)==1,'Recovery release source head unavailable')
                        inputs=evidence._recovery_use_inputs(context,db,heads[0],content['identity'],content=template)
                        if capture is not None:inputs['history_capture']=capture
                        projection=evidence._recovery_registration_projection(template,observation.created_at,inputs,budget=budget)
                        d3._require(projection['reference'] in (None,heads[0]),'Release replay selected another head')
                        if projection['reference'] is not None:
                            prior=inputs['history'][0]
                            d3._require(projection['content']==prior['content']
                                and projection['dependencies']==prior['metadata']['dependencies']
                                and observation.created_at==prior['metadata']['created_at'],'Release replay differs from exact original head')
                        return projection
                decisions={str(context.database):project(context) for context in contexts}
                lifetime['release_decisions']=decisions
                for context in contexts:
                    key=str(context.database);decision=decisions[key]
                    lifetime['registration_projections'][key]=(project(context) if
                        _recovery_release_template(observation,context)!=decision['source'] else decision)
                for context in contexts:
                    _check_recovery_source_global(observation)
                    with evidence._context_database(context,write=True,durable=True,
                            before_commit=lambda db,context=context:evidence._check_recovery_registration(context,db,observation)) as db:
                        reference=evidence._register_recovery_source(None,db,observation,context=context)
                    replacements.append(dict(authority=_canonical_plain(context.authority_set[0]),artifact=_canonical_plain(reference)))
                _check_recovery_source_global(observation)
                for context in contexts:
                    with evidence._context_database(context) as db:evidence._check_recovery_registration(context,db,observation)
            finally:lifetime['active']=False;_authority_context.publication=previous
        return _canonical_freeze(dict(complete=True,replacements=replacements,blockers=[]))
    except (StartupError,task_state.StateError,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as error:
        return _canonical_freeze(dict(complete=False,replacements=replacements,blockers=[str(error)]))


def _recovery_current_requirements(root,history):
    """Origin-qualified edges; a cut never exempts another root's old source route."""
    from runtime import evidence
    requirements=[];authority=root['authority']
    for reference,content in history:
        owner=dict(authority=authority,artifact=reference)
        requirements.append(dict(root=root,owner=owner,kind='artifact',reference=owner,need='content'))
        for edge in evidence._recovery_source_dependencies(content,authority):
            kind=edge['kind'];ref=edge['reference']
            requirements.append(dict(root=root,owner=owner,kind=kind,reference=ref,need='raw' if kind in ('task','assignment') else 'content'))
        for certificate in content.get('stopped',[]):
            requirements.append(dict(root=root,owner=owner,kind='task',reference=certificate['reference'],need='identity'))
            for assignment in certificate.get('assignments',[]):
                requirements.append(dict(root=root,owner=owner,kind='assignment',reference=assignment['reference'],need='identity'))
    d3._require(len(requirements)<=4096,'Recovery current-root requirements exceed bound')
    return requirements


def _inspect_lifecycle_recovery_guarded(authorities,task_refs,*,read_roster=None):
    """One reader lifetime stays inside this read-only, caller-guarded invocation."""
    from runtime import evidence
    if read_roster is not None:evidence._check_linked_read_roster(read_roster,evidence._canonical_plain(read_roster.pointer))
    with evidence._linked_preparation_reader(authorities) as preparation_reader:
        return _inspect_lifecycle_recovery_read(authorities,task_refs,preparation_reader,read_roster=read_roster)


def _inspect_lifecycle_recovery_read(authorities,task_refs,preparation_reader,*,read_roster=None):
    """Inspect one fixed set under caller-held complete guards; never acquire/expand locks."""
    with ExitStack() as read_scopes:
        read_rosters=_RecoveryReadRosters(authorities,read_scopes,read_roster=read_roster)
        from runtime import evidence
        dependencies=[];blockers=[];roots=[];requirements=[];discovered={};contexts=[];observed=[];pending={};journals={}
        def result():
            return _canonical_freeze(dict(complete=not blockers and not discovered,dependencies=dependencies,blockers=blockers,
                roots=roots,requirements=requirements,authorities=sorted([_canonical_plain(c.authority_set[0]) for c in contexts],key=d3._digest),
                discovered_authorities=sorted(discovered.values(),key=d3._digest)))
        try:
            pending={d3._digest(_canonical_plain(a)):_canonical_plain(a) for a in authorities}
            d3._require(0<len(pending)<=4096,'Recovery authority selection is empty or exceeds bound')
            for authority in pending.values():
                seen_markers=set()
                for location in _authority_locations([Path(authority['directory'])]):
                    marker=_authority_owner(location)
                    if marker is not None and d3._digest(marker) not in seen_markers:
                        seen_markers.add(d3._digest(marker))
                        blockers.append(dict(reason='active_or_uncertain_operation',authority=authority,owner=marker))
                        dependencies.append(dict(authority=authority,**marker,source_artifact=None,journal=None,task_refs=[]))
                context=_recovery_context(authority); contexts.append(context)
                baseline,sources=_recovery_sources(context,validate_stops=False)
                if baseline is None or baseline['content']['source_contract']!=2:
                    blockers.append(dict(reason='unknown_creation_time_d4_coverage',authority=authority))
                for reference,content in sources:
                    with evidence._context_database(context) as db:
                        history=evidence._recovery_source_history(context,db,reference,content['identity'],validate_stops=False)
                    for _,prior in history:
                        for other in prior['authorities']:
                            key=d3._digest(other)
                            if key not in pending: discovered[key]=other
                    observed.append((context,reference,content))
            if discovered:
                d3._require(len(pending)+len(discovered)<=4096,'Recovery authority closure exceeds bound')
                blockers.append(dict(reason='recovery_authority_set_incomplete'))
                return result()
            for context,reference,content in observed:
                authority=_canonical_plain(context.authority_set[0])
                path=Path(content['identity']['plan_dir'])
                raw=d3._read_file(path/'transaction.json',MAX_JOURNAL,private=True)
                journal=_scoped_journal_shape(d3.read_request(raw,limit=MAX_JOURNAL))
                journals[path]=hashlib.sha256(raw).hexdigest()
                d3._require(journal is not None and journal['schema_version']==2 and
                    all(journal[k]==content['identity'][k] for k in ('transaction_id','manifest_digest')),
                    'Recovery journal stable identity differs')
                if hashlib.sha256(raw).hexdigest()!=content['journal']['sha256'] or journal['state']!=content['journal']['state']:
                    blockers.append(dict(reason='journal_snapshot_requires_reconciliation',identity=content['identity']))
                read_context=_content_linked_read_context(content,[_canonical_plain(c.authority_set[0]) for c in contexts])
                source_roster=read_rosters
                generations=_validate_recovery_references(content,contexts,preparation_reader=preparation_reader,read_context=read_context,read_roster=source_roster)
                with evidence._context_database(context) as db:
                    history=evidence._recovery_source_history(context,db,reference,content['identity'],preparation_reader=preparation_reader,read_context=read_context,read_roster=source_roster)
                for _,prior in history:
                    _validate_recovery_references(prior,contexts,preparation_reader=preparation_reader,read_context=read_context,read_roster=source_roster)
                current_root=dict(authority=authority,identity=content['identity'],source_artifact=reference)
                roots.append(current_root);requirements.extend(_recovery_current_requirements(current_root,history))
                d3._require(len(requirements)<=4096,'Recovery current-root requirements exceed bound')
                dependencies.append(dict(authority=authority,**content['identity'],source_artifact=reference,journal=content['journal'],
                    task_refs=[t['reference'] for t in content['tasks']],assignments=content['assignments'],artifacts=content['artifacts'],generation_digest=d3._digest(generations)))
            for original_ref in task_refs:
                ref=_canonical_plain(original_ref); task_state._task_reference(ref)
                context=next((c for c in contexts if _canonical_plain(c.authority_set[0])==ref['authority']),None)
                d3._require(context is not None,'Named task authority is outside observed closure')
                with evidence._context_database(context) as db:
                    identity=task_state._lifecycle_identity(db,'task',ref)
                    if identity['status']!='live':
                        certificates=[certificate for _,_,value in observed for certificate in value.get('stopped',[]) if certificate['reference']==ref]
                        d3._require(certificates,'Named retired recovery task lacks retained stopped support')
                        for certificate in certificates:
                            selected_context=(evidence._bind_linked_read_context(evidence._linked_basis_root(certificate['linked']),authorities,writer=(context,db),basis=certificate['linked']) if certificate.get('linked') else None)
                            source_roster=read_rosters
                            _check_stopped_certificate(context,db,certificate,preparation_reader=preparation_reader,
                                read_context=selected_context,read_roster=source_roster)
            # Sources/physical bindings must still be the exact point observations before return.
            for context,reference,content in observed:
                selected_context=_content_linked_read_context(content,authorities)
                source_roster=read_rosters
                _,latest=_recovery_sources(context,preparation_reader=preparation_reader,read_context=selected_context,read_roster=source_roster)
                d3._require(any(ref==reference and value==content for ref,value in latest),'Recovery source edge changed during inspection')
            for path,digest in journals.items():
                d3._require(hashlib.sha256(d3._read_file(path/'transaction.json',MAX_JOURNAL,private=True)).hexdigest()==digest,
                    'Recovery journal changed during inspection')
            preparation_reader.finish()
            read_rosters.finish()
            return result()
        except (StartupError,task_state.StateError,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as error:
            blockers.append(dict(reason=str(error) if isinstance(error,(StartupError,task_state.StateError)) else 'recovery_source_unavailable'))
            return result()
        finally:read_rosters.active=False


def inspect_lifecycle_recovery(authorities,task_refs):
    """Finite D4 coverage with release/reacquire on discovery; no closure authorization."""
    try:
        pending={d3._digest(_canonical_plain(a)):_canonical_plain(a) for a in authorities}
        d3._require(0<len(pending)<=4096,'Recovery authority selection is empty or exceeds bound')
        for _ in range(4096):
            with _authority_guards([Path(a['directory']) for a in pending.values()],observe=True):
                inspected=_inspect_lifecycle_recovery_guarded(tuple(pending.values()),task_refs)
                discovered=[_canonical_plain(a) for a in inspected['discovered_authorities']]
                if not discovered: return inspected
                d3._require(len(pending)+len(discovered)<=4096,'Recovery authority closure exceeds bound')
            # Never acquire a discovered lower-order guard while holding a partial set.
            pending.update({d3._digest(a):a for a in discovered})
        raise Pending('Recovery authority closure did not converge')
    except (StartupError,task_state.StateError,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as error:
        return _canonical_freeze(dict(complete=False,dependencies=[],blockers=[dict(reason=str(error))],roots=[],requirements=[],
            authorities=[],discovered_authorities=[]))


def _scoped_journal_shape(journal, *, file_only=False):
    d3._object(journal, ("schema_version", "transaction_id", "manifest_digest", "manifest", "approval", "plan", "state", "receipts", "enrollment", "blocked", "directories", "observations"), ("birth", "setup_receipts", "qualification_scope", "native_observations"))
    d3._require(type(journal["schema_version"]) is int and journal["schema_version"] == 2 and journal["manifest_digest"] == d3._digest(journal["manifest"])
                and journal["state"] in ("prepared", "applying", "interrupted", "applied", "blocked", "rollback_partial", "rolled_back"), "Invalid scoped journal")
    d3._require(isinstance(journal["receipts"], dict) and isinstance(journal["directories"], dict) and isinstance(journal["observations"], list), "Invalid scoped recovery containers")
    if 'qualification_scope' in journal:
        from runtime.setup_observation import _authorization_shape
        _authorization_shape(journal['qualification_scope'])
    if 'native_observations' in journal:
        from runtime.setup_observation import _native_rows_shape
        _native_rows_shape(journal)
    if 'setup_receipts' in journal:
        from runtime.setup_observation import receipt_shape
        d3._require(isinstance(journal['setup_receipts'], dict) and len(journal['setup_receipts']) <= 64, 'Invalid owner setup receipt collection')
        for identity, receipt in journal['setup_receipts'].items():
            receipt_shape(receipt)
            d3._require(identity == d3._digest(receipt['identity']) and receipt['transaction_id'] == journal['transaction_id'], 'Original setup receipt transaction changed')
    if 'birth' in journal and not file_only:
        birth = journal['birth']
        d3._object(birth, ('version','intent','intent_digest','phase','directories','allocation','committed','certificate'))
        _birth_intent_shape(birth['intent'])
        d3._require(type(birth['version']) is int and birth['version']==1 and birth['intent_digest']==d3._digest(birth['intent'])
            and birth['phase'] in ('intent','directories_owned','allocated','committed','registered','retained'), 'Invalid original birth journal')
        for row in birth['directories']:
            d3._object(row, ('path','parents','preimage','status','binding'))
            d3._require(row['path'] in birth['intent']['destination']['missing_directories'] and row['preimage']=='absent'
                and row['status'] in ('prepared','owned'), 'Invalid birth directory receipt')
        if birth['certificate'] is not None:
            _birth_certificate_shape(birth['certificate'])
            d3._require(birth['certificate']['intent']==birth['intent'] and birth['allocation']==birth['certificate']['allocation']
                and birth['committed']==dict(baseline=birth['certificate']['baseline'],seed_digest=birth['certificate']['seed_digest']), 'Birth completion receipt changed')
    actions = {a["id"]: a for a in journal["manifest"]["actions"]}
    d3._require(set(journal["receipts"]) <= set(actions), "Unknown scoped receipt")
    if not file_only:
        for observation in journal["observations"]: _scoped_observation_shape(observation,journal["manifest"])
    for identity, receipt in journal["receipts"].items():
        d3._object(receipt, ("owner", "status", "files", "returncode"), ("command", "setup_capture"))
        if 'setup_capture' in receipt:
            from runtime.setup_observation import _capture_shape
            _capture_shape(receipt['setup_capture'])
            d3._require('command' in receipt and receipt['setup_capture']['origin'] == dict(
                transaction_id=journal['transaction_id'],action_id=identity,attempt_id=receipt['command']['attempt_id']),
                'Native source lost its original command claim')
            d3._require(receipt['setup_capture']['launch']['command_digest']==d3._digest(actions[identity]['execution']),
                'Native source changed its original exact command')
        d3._require(receipt["owner"] == actions[identity]["owner"] and receipt["status"] in ("prepared", "started", "completed", "rolled_back"), "Invalid scoped receipt owner/status")
        d3._require(len(receipt["files"]) == len(actions[identity]["effects"]), "Incomplete scoped backup coverage")
        if "command" in receipt:
            command=receipt["command"]
            d3._object(command,("attempt_id","stopped"))
            d3._require(actions[identity]["execution"] is not None and isinstance(command["attempt_id"],str) and re.fullmatch(r"[0-9a-f]{32}",command["attempt_id"])
                and type(command["stopped"]) is bool,"Invalid durable scoped command identity")
            if command["stopped"]:
                d3._require(any(p["id"] == identity and p["attempt_id"] == command["attempt_id"] for o in journal["observations"] for p in o["command_reconciliation"]),
                    "Stopped command lacks its retained exact lifecycle proof")
        action_index = journal["manifest"]["actions"].index(actions[identity])
        assessment = next(a for a in d3._assessments(journal["plan"]["assessments"]) if a["owner"] == actions[identity]["owner"])
        for index, (file, effect) in enumerate(zip(receipt["files"], actions[identity]["effects"])):
            d3._object(file, ("backup", "temporary", "target", "before_binding", "prepared_binding", "result_binding", "owned_target"), ("restore", "delete_intent"))
            expected_temp = ".d4-" + journal["transaction_id"] + "-" + str(action_index) + "-" + str(index)
            d3._require(file["backup"] == ("backup-" + str(action_index) + "-" + str(index) if effect["before"] else None)
                and file["temporary"] == expected_temp and file["target"] == (effect["target"] if "target" in effect else assessment["identity"]["targets"].get(effect["path"]))
                and file["before_binding"] == file["target"]["binding"], "Scoped recovery file reference changed")
            for key in ("before_binding", "prepared_binding", "result_binding"):
                if file[key] is not None:
                    from runtime.startup import _canonical_binding
                    _canonical_binding(file[key])
            if file["owned_target"] is not None:
                from runtime.startup import _canonical_target
                _canonical_target(file["owned_target"])
                d3._require(file["owned_target"]["path"] == file["target"]["path"] and file["owned_target"]["binding"] == file["result_binding"], "Scoped owned result changed")
            if "restore" in file:
                d3._object(file["restore"], ("temporary", "before_binding", "prepared_binding", "result_binding"), ("delete_intent",))
                d3._require(file["restore"]["temporary"] == expected_temp + "-restore" and file["restore"]["before_binding"] == file["result_binding"], "Scoped restore ownership changed")
    for location, receipt in journal["directories"].items():
        d3._require(Path(location).is_absolute(), "Invalid scoped directory path")
        d3._object(receipt, ("status", "device", "inode"))
        allowed = {str(p) for action in actions.values() for effect in action["effects"] for p in (Path(action["owner"]["root"]) / effect["path"]).parents if p.is_relative_to(Path(action["owner"]["root"])) and p != Path(action["owner"]["root"])}
        allowed.add(str(Path(journal["manifest"]["enrollment"]["record"]["project_root"]) / PROJECT_FILE.parent))
        d3._require(location in allowed and receipt["status"] in ("prepared", "owned", "retained", "removed")
                    and all(v is None or type(v) is int and v >= 0 for v in (receipt["device"],receipt["inode"])), "Scoped directory ownership invalid")
    e = journal["enrollment"]
    if e is not None:
        d3._object(e, ("before", "backup", "status", "temporary", "postimages", "members"))
        d3._require(e["before"] == journal["manifest"]["enrollment"]["before"] and e["backup"] == ("enrollment.backup" if e["before"]["raw_sha256"] is not None else None)
            and e["temporary"] == ".d4-"+journal["transaction_id"]+"-enrollment" and e["status"] in ("prepared","inactive","activating","active","restoring","restored"), "Invalid canonical recovery preimage")
        d3._require(isinstance(e["members"],list) and len(e["members"]) == len(journal["manifest"]["enrollment"]["affected"]), "Incomplete scoped member outcome coverage")
        for member,accepted in zip(e["members"],journal["manifest"]["enrollment"]["affected"]):
            d3._object(member,("repository_id","checkout_id","client","status","blocked"))
            d3._require(all(member[k] == accepted[k] for k in accepted) and member["status"] in ("pending","configured","active","suspended","restored","blocked")
                and (member["blocked"] is None or isinstance(member["blocked"],str)), "Invalid scoped member outcome")
        d3._require(isinstance(e["postimages"], list) and len(e["postimages"]) <= 4, "Invalid canonical postimage sequence")
        phases=[]
        _,prior=_scoped_prior(journal["plan"])
        for post in e["postimages"]:
            d3._object(post, ("phase", "revision", "raw_sha256", "target", "backup", "temporary", "status", "before", "after", "before_binding", "prepared_binding", "result_binding"), ("delete_intent",))
            d3._require(post["phase"] in ("suspended", "final", "rollback_suspended", "restore") and post["temporary"].startswith(".d4-" + journal["transaction_id"] + "-")
                        and "/" not in post["temporary"], "Invalid canonical temporary scope")
            _fp_schema(post["before"]); _fp_schema(post["after"])
            phase=post["phase"]
            ranks={"suspended":0,"final":1,"rollback_suspended":2,"restore":3}
            d3._require(phase not in phases and (not phases or ranks[phase] > ranks[phases[-1]]) and (phase != "rollback_suspended" or "final" in phases), "Invalid canonical publication order")
            phases.append(phase)
            expected_record=journal["manifest"]["enrollment"]["record"] if phase == "final" else _scoped_suspended(journal,rollback=phase=="rollback_suspended") if phase in ("suspended","rollback_suspended") else prior
            expected_sha=hashlib.sha256(json.dumps(expected_record,sort_keys=True,ensure_ascii=True).encode()+b"\n").hexdigest() if expected_record is not None else None
            if phase == "restore": expected_sha=e["before"]["raw_sha256"]
            d3._require(post["raw_sha256"] == expected_sha and post["revision"] == (expected_record.get("revision") if expected_record else None)
                and post["temporary"] == ".d4-"+journal["transaction_id"]+"-"+phase and post["backup"] == e["backup"] and post["status"] in ("prepared","published")
                and (post["after"]["sha256"] if post["after"] else None) == expected_sha, "Canonical postimage differs from its frozen approved bytes")
            if post["after"] is not None and phase != "restore":
                d3._require(post["after"]["mode"] == 0o600, "Canonical postimage mode changed")
            for key in ("before_binding","prepared_binding","result_binding"):
                if post[key] is not None:
                    from runtime.startup import _canonical_binding
                    _canonical_binding(post[key])
            if post["target"] is not None:
                from runtime.startup import _canonical_target
                _canonical_target(post["target"])
                d3._require(post["target"]["path"] == str(Path(journal["manifest"]["enrollment"]["record"]["project_root"])/PROJECT_FILE)
                    and post["target"]["binding"] == post["result_binding"] and post["target"]["fingerprint"].get("sha256") == expected_sha,
                    "Canonical observed target differs from its owned result")
            d3._require(post["status"] != "published" or post["target"] is not None, "Published canonical target observation missing")
    return journal


def _scoped_owner_bindings(plan):
    for assessment in d3._assessments(plan["assessments"]):
        owner, identity = assessment["owner"], assessment["identity"]
        root = Path(owner["root"])
        d3._require(d3._filesystem_binding(root) == (plan["identity"]["binding"] if owner["kind"] == "project" else identity["binding"]["root"]), "Selected root physical identity changed")
        if owner["kind"] == "repository":
            current, error = d3._checkout_observation(str(root), {"paths":0,"bytes":0})
            d3._require(error is None and all(current[k] == identity[k] for k in ("root","primary_root","git_dir","git_common_dir","binding")), "Selected Git physical identity changed")


def _scoped_parent_guard(target, journal):
    for name, binding in target["parents"].items():
        d3._require(d3._filesystem_binding(Path(name)) == binding, "Scoped target parent physical identity changed")
    for name, receipt in journal["directories"].items():
        if Path(target["path"]).is_relative_to(Path(name)) and receipt["status"] == "owned":
            d3._require(d3._filesystem_binding(Path(name)) == {k:receipt[k] for k in ("device","inode")}, "Owned parent directory changed")


def _scoped_recovery_files(path, journal):
    for action in journal["manifest"]["actions"]:
        receipt = journal["receipts"].get(action["id"])
        if receipt:
            for effect, file in zip(action["effects"],receipt["files"]):
                _scoped_parent_guard(file["target"],journal)
                if file["backup"]:
                    raw = d3._read_file(path / file["backup"],d3.MAX_FILE,private=True)
                    d3._require(hashlib.sha256(raw).hexdigest() == effect["before"]["sha256"], "Scoped backup integrity failed")
    if journal["enrollment"] and journal["enrollment"]["backup"]:
        raw = d3._read_file(path / journal["enrollment"]["backup"],128*1024,private=True)
        d3._require(hashlib.sha256(raw).hexdigest() == journal["manifest"]["enrollment"]["before"]["raw_sha256"], "Canonical backup integrity failed")



def _check_operation_files(path, journal):
    """Refresh physical recovery facts without advancing any receipt phase."""
    _scoped_recovery_files(path, journal)
    originals, _, _ = _scoped_originals(journal['plan'])
    record = journal['manifest']['enrollment']['record']
    for action in journal['manifest']['actions']:
        original = originals[action['id']]
        if original['target'] == str(PROJECT_FILE):
            d3._require(original['kind'] == 'write' and original['owner'] == action['owner']
                and action['owner']['root'] == record['project_root']
                and len(action['effects']) == 1 and action['effects'][0]['path'] == str(PROJECT_FILE)
                and d3.read_request(original['text'].encode()) == record,
                'Recovery canonical action differs from its actual enrollment proposal')
            # Canonical writes use enrollment phase receipts instead of action file receipts.
            d3._require(_scoped_owned_authority(journal), 'Recovery canonical enrollment physical ownership changed')
            continue
        root = Path(action['owner']['root'])
        receipt = journal['receipts'].get(action['id'])
        assessment = next(a for a in d3._assessments(journal['plan']['assessments']) if a['owner'] == action['owner'])
        for index, effect in enumerate(action['effects']):
            file = receipt['files'][index] if receipt else None
            target = file['target'] if file else effect.get('target') or assessment['identity'].get('targets', {}).get(effect['path'])
            d3._require(target is not None, 'Recovery operation lacks its original target')
            _scoped_parent_guard(target, journal)
            actual = _scoped_target(root, effect['path'])
            for name, binding in actual['parents'].items():
                owned = journal['directories'].get(name, {})
                d3._require(binding == target['parents'].get(name) or
                    owned.get('status') in ('owned', 'retained') and binding == {k:owned[k] for k in ('device','inode')},
                    'Recovery operation target parent changed')
            fingerprint = _fingerprint(root, effect['path'])
            if file is None:
                valid = fingerprint == effect['before'] and actual['binding'] == target['binding']
            else:
                restore = file.get('restore')
                before, after = (effect['after'], effect['before']) if restore else (effect['before'], effect['after'])
                phase = restore or file
                original = fingerprint == before and actual['binding'] == phase['before_binding']
                binding = phase['result_binding'] or phase['prepared_binding']
                if binding is None and before == after:
                    binding = phase['before_binding']
                replaced = fingerprint == after and (actual['binding'] == binding if after is not None else
                    before is None or phase.get('delete_intent') is True)
                if receipt['status'] == 'rolled_back':
                    valid = replaced if restore else original
                elif receipt['status'] == 'completed' and restore is None:
                    valid = replaced
                else:
                    # Prepared forward and restore receipts can straddle the durable rename.
                    valid = original or replaced
            d3._require(valid, 'Recovery operation effect bytes or physical ownership changed')


def _scoped_fresh(plan, journal):
    for assessment in d3._assessments(plan["assessments"]):
        owner, identity = assessment["owner"], assessment["identity"]
        root = Path(owner["root"])
        d3._require(d3._filesystem_binding(root) == (plan["identity"]["binding"] if owner["kind"] == "project" else identity["binding"]["root"]), "Selected root physical identity changed")
        owned = {e["path"] for a in journal["manifest"]["actions"] if a["owner"] == owner and a["id"] in journal["receipts"] for e in a["effects"]}
        if root == Path(plan["identity"]["project_root"]) and journal["enrollment"] is not None: owned.add(str(PROJECT_FILE))
        if owner["kind"] == "repository":
            observed, error = d3._checkout_observation(str(root), {"paths": 0, "bytes": 0})
            d3._require(error is None and all(observed[k] == identity[k] for k in ("root", "primary_root", "git_dir", "git_common_dir", "binding")), "Selected Git physical identity changed")
            local_actions = [dict(a, id=d3._qualified(owner, a["id"])) for a in assessment["actions"]]
            local = dict(plan, identity=identity["snapshot"], actions=local_actions, schema_version=1)
            view = dict(journal, manifest=dict(journal["manifest"], actions=[a for a in journal["manifest"]["actions"] if a["owner"] == owner]),
                        enrollment=journal["enrollment"] if str(PROJECT_FILE) in owned else None)
            external_owned=[]
            for action in journal['manifest']['actions']:
                if action['owner']['kind']!='project': continue
                receipt=journal['receipts'].get(action['id'])
                if receipt is None: continue
                shared_root=Path(action['owner']['root'])
                for effect,file in zip(action['effects'],receipt['files']):
                    key='instruction:'+str(shared_root/effect['path'])
                    if key not in identity['snapshot']['fingerprints']: continue
                    if _fingerprint(shared_root,effect['path'])!=effect['after']: continue
                    actual=_scoped_target(shared_root,effect['path'])
                    _scoped_parent_guard(file['target'],journal)
                    owned_target=file['owned_target']
                    binding=file['result_binding'] or file['prepared_binding']
                    d3._require(actual==owned_target if owned_target is not None else
                        binding is not None and actual['binding']==binding,'Shared instruction physical ownership changed')
                    external_owned.append(key)
            _fresh(local, view, external_owned=external_owned)
        else:
            for name, fingerprint in identity["fingerprints"].items():
                if name not in owned: d3._require(d3._fingerprint(root, name) == fingerprint, "Unrelated Project file changed")
    d3._require(plan["package_pins"] == d3._package_pins() and not d3._source_blockers(plan, d3._sources(plan["sources"]["declared"])), "Installed package or Toolkit sources changed")


def _relevant_forward_inputs(plan, journal):
    if not d3._relevant_inspection(plan['project_scope']):
        return
    manifest = journal['manifest']
    for assessment in d3._assessments(plan['assessments']):
        root = Path(assessment['owner']['root'])
        old = assessment['identity']['snapshot']
        current = d3._snapshot(root, d3._relevant_inputs(plan['project_scope'], assessment), relevant=True)
        effects = {effect['path'] for action in manifest['actions'] if action['owner'] == assessment['owner']
                   for effect in action['effects']}
        if journal['enrollment'] is not None and root == Path(manifest['enrollment']['record']['project_root']):
            d3._require(_scoped_owned_authority(journal), 'Canonical enrollment physical ownership changed')
            effects.add(str(PROJECT_FILE))
        owned = effects | {'instruction:' + str(root / name) for name in effects}
        d3._require(all(old['fingerprints'].get(name) == current['fingerprints'].get(name)
                       for name in old['fingerprints'].keys() | current['fingerprints'].keys() if name not in owned),
                    'Relevant configuration or instruction inputs changed')


def _scoped_input_digest(manifest):
    root = Path(manifest["enrollment"]["record"]["project_root"])
    return d3._digest(dict(authority=_scoped_target(root, str(PROJECT_FILE)), package_pins=d3._package_pins(),
        effects=[dict(id=a["id"], files=[_scoped_target(Path(a["owner"]["root"]), e["path"]) for e in a["effects"]]) for a in manifest["actions"]]))


def _scoped_suspended(journal, *, rollback=False):
    _,prior=_scoped_prior(journal["plan"])
    final=journal["manifest"]["enrollment"]["record"]
    record=deepcopy(final if rollback else prior if prior and prior.get("schema_version")==2 else final)
    record["revision"]=final["revision"]+1 if rollback else final["revision"]-1
    affected={tuple(m[k] for k in ("repository_id","checkout_id","client")) for m in journal["manifest"]["enrollment"]["affected"]}
    for key,(_,_,entry) in _scoped_entries(record).items():
        if key in affected and entry["configuration"] is not None and (not rollback or entry["activation"]["status"]=="active"):
            entry["activation"]["status"]="suspended"
    return record


def _scoped_publish(path, journal, phase, record):
    root = Path(journal["manifest"]["enrollment"]["record"]["project_root"])
    receipt = journal["enrollment"]
    raw = json.dumps(record, sort_keys=True, ensure_ascii=True).encode() + b"\n" if record else b""
    mode = 0o600
    if phase == "restore" and receipt["backup"]:
        raw = d3._read_file(path / receipt["backup"],128*1024,private=True)
        d3._require(hashlib.sha256(raw).hexdigest() == receipt["before"]["raw_sha256"] and d3.read_request(raw) == record, "Prior authority raw backup changed")
        mode = next((p["before"]["mode"] for p in receipt["postimages"] if p["before"] is not None),0o600)
    after = dict(sha256=hashlib.sha256(raw).hexdigest(), mode=mode) if record else None
    post = next((p for p in receipt["postimages"] if p["phase"] == phase), None)
    if post is None:
        actual = _scoped_target(root, str(PROJECT_FILE))
        post = dict(phase=phase, revision=record.get("revision") if record else None, raw_sha256=after["sha256"] if after else None,
            target=None, backup=receipt["backup"], temporary=".d4-" + journal["transaction_id"] + "-" + phase,
            status="prepared", before=_fingerprint(root, str(PROJECT_FILE)), after=after,
            before_binding=actual["binding"], prepared_binding=None, result_binding=None)
        receipt["postimages"].append(post); _save(path, journal)
    d3._require(post["after"] == after, "Canonical prepared postimage changed")
    _ensure_parents(path, root, str(PROJECT_FILE), journal)
    _scoped_parent_guard(journal["manifest"]["enrollment"]["before"]["target"], journal)
    _scoped_replace(path, root, str(PROJECT_FILE), raw, post["before"], after, post, journal)
    post["target"] = _scoped_target(root, str(PROJECT_FILE)); post["status"] = "published"
    receipt["status"] = "restored" if phase == "restore" else "active" if phase == "final" else "inactive"
    if phase in ("suspended","rollback_suspended","restore"):
        for member in receipt["members"]:
            member["status"]="restored" if phase=="restore" else "suspended"
            member["blocked"]=None
    _save(path, journal)


def _scoped_owned_authority(journal):
    root = Path(journal["manifest"]["enrollment"]["record"]["project_root"])
    current = _scoped_target(root, str(PROJECT_FILE))
    enrollment = journal["enrollment"]
    if enrollment is None: return current == journal["manifest"]["enrollment"]["before"]["target"]
    for post in reversed(enrollment["postimages"]):
        expected = post["result_binding"] or post["prepared_binding"]
        if _fingerprint(root, str(PROJECT_FILE)) == post["after"] and current["binding"] == expected:
            if post["target"] is None or current == post["target"]: return True
            if (post["phase"] == "restore" and post["status"] == "published" and enrollment["status"] == "restored"
                    and post["after"] is None and post["before"] is not None and post["before_binding"] is not None
                    and post.get("delete_intent") is True and current == enrollment["before"]["target"]):
                # Restoring absence can remove the transaction's own recorded parent directories.
                parents = dict(post["target"]["parents"])
                removed = set(parents) - set(current["parents"])
                if not removed: return False
                for name in removed:
                    if journal["directories"].get(name) != dict(status="removed", **parents[name]): return False
                    del parents[name]
                return dict(post["target"], parents=parents) == current
            return False
    return not any(p["status"] == "published" for p in enrollment["postimages"]) and current == enrollment["before"]["target"]


def _scoped_apply_action(path, journal, action, original, observation, checkout_lock, *, observer_owner=None):
    root = Path(action["owner"]["root"])
    if action["recovery"] != "file-preimages" or not action["effects"]:
        raise Pending("Scoped operation lacks complete bounded recoverable effects")
    expected_resources = ["checkout"] if action["owner"]["kind"] == "repository" else ["project"]
    if action["resources"] != expected_resources or original["kind"] == "external":
        raise Pending("Shared/global or external effects require manual serialization")
    receipt = journal["receipts"].get(action["id"])
    allocation = (observer_owner.setup_command_allocation(action,journal)
        if original['kind'] == 'command' and callable(getattr(observer_owner,'setup_command_allocation',None)) else None)
    setup_action = any(attempt['task'] is None and attempt.get('command_action') == action['id']
        for attempt in journal.get('qualification_scope',{}).get('attempts',[]))
    for assessment in d3._assessments(journal["plan"]["assessments"]):
        for decision in assessment["decisions"]:
            if decision["disposition"] in ("pending","proposed") and (d3._qualified(assessment["owner"],decision["id"]) in original["decision_ids"] or action["id"] in decision["affected_actions"]):
                raise Pending("An applicable scoped adoption decision remains unresolved")
        if any(action["id"] in row["actions"] and (row["blocker"] or row["outcome"] == "blocked") for row in assessment["coverage"]):
            raise Pending("Scoped adoption coverage remains blocked")
    if receipt is None:
        assessment = next(a for a in d3._assessments(journal["plan"]["assessments"]) if a["owner"] == action["owner"])
        files = []
        for index, effect in enumerate(action["effects"]):
            target = effect["target"] if original["kind"] == "command" else assessment["identity"].get("targets", {}).get(effect["path"])
            d3._require(target is not None and _scoped_target(root, effect["path"]) == target, "Scoped target/parent preimage changed")
            backup = _backup(path, root, effect["path"], effect["before"], "backup-" + str(journal["manifest"]["actions"].index(action)) + "-" + str(index))
            files.append(dict(backup=backup, temporary=".d4-" + journal["transaction_id"] + "-" + str(journal["manifest"]["actions"].index(action)) + "-" + str(index),
                target=target, before_binding=target["binding"], prepared_binding=None, result_binding=None, owned_target=None))
        receipt = dict(owner=action["owner"], status="prepared", files=files, returncode=None)
        journal["receipts"][action["id"]] = receipt; _save(path, journal); _checkpoint("prepared")
    for condition in action["preconditions"]:
        d3._require(_fingerprint(root, condition["path"]) == condition["fingerprint"], "Scoped action precondition changed")
    if original["kind"] == "command":
        if action["owner"]["kind"] != "repository" or not action["execution"]["qualification"]:
            raise Pending("Scoped command lacks qualified Repository-local effects")
        if receipt.get("command") and not receipt["command"]["stopped"]:
            proof = next((p for p in observation.get("command_reconciliation", []) if p["id"] == action["id"] and p["attempt_id"] == receipt["command"]["attempt_id"]), None) if observation else None
            if not proof or proof.get("stopped") is not True or not _evidence(proof.get("evidence"), many=True):
                raise Pending("Scoped command and writable descendants remain uncertain")
            receipt["command"]["stopped"] = True; _save(path, journal)
            marker = _command_owner(action["owner"]["git_dir"])
            if marker is not None:
                d3._require(marker == dict(schema_version=1,plan_dir=str(path),transaction_id=journal["transaction_id"],id=action["id"],attempt_id=receipt["command"]["attempt_id"]), "Command owner does not match the reconciled attempt")
                location=_owner_path(action["owner"]["git_dir"]); os.unlink(location)
                descriptor=d3._open_path(location.parent,os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        actual = [_fingerprint(root, e["path"]) for e in action["effects"]]
        if receipt.get("command"):
            if actual == [e["after"] for e in action["effects"]] and receipt["returncode"] == 0:
                receipt["status"] = "completed"
                for effect, file in zip(action["effects"], receipt["files"]):
                    d3._require(file["owned_target"] is not None and _scoped_target(root,effect["path"]) == file["owned_target"], "Command output physical ownership is unverified or changed")
                _save(path, journal); return
            if not action["execution"]["idempotency"] or actual != [e["before"] for e in action["effects"]]:
                raise Pending("Scoped command outcome requires reviewed manual recovery")
            if allocation is not None or setup_action:
                raise Pending('Original bootstrap command allocation is consumed; do not relaunch it')
        if setup_action and allocation is None:
            raise Pending('Original bootstrap command requires its trusted observer and finite allocation')
        cwd_target=d3._physical_target(root,Path(action["execution"]["cwd"]),None,{"paths":0,"bytes":0},directory=True)
        d3._require(cwd_target == action["execution"]["cwd_target"], "Actual command cwd physical identity changed")
        receipt["command"] = dict(attempt_id=uuid.uuid4().hex, stopped=False); receipt["status"] = "started"; _save(path, journal)
        git_dir = action["owner"]["git_dir"]
        d3._require(_command_owner(git_dir) is None, "Another command owns this checkout")
        marker = dict(schema_version=1, plan_dir=str(path), transaction_id=journal["transaction_id"], id=action["id"], attempt_id=receipt["command"]["attempt_id"])
        location = _owner_path(git_dir); _atomic(location.parent, location.name, json.dumps(marker, sort_keys=True).encode())
        _checkpoint("command_started")
        for effect in action["effects"]:
            d3._require(_fingerprint(root, effect["path"]) == effect["before"] and
                _scoped_target(root, effect["path"]) == effect["target"],
                "Scoped command effect preimage or physical identity changed before launch")
        d3._require(d3._physical_target(root,Path(action["execution"]["cwd"]),None,{"paths":0,"bytes":0},directory=True)
            == action["execution"]["cwd_target"], "Actual command cwd physical identity changed before launch")
        if allocation is not None:
            from runtime.doctor import NativeProcess, ObservationError
            deadline = min(time.monotonic()+action['execution']['timeout_seconds'],
                time.monotonic()+max(0,allocation['limits']['deadline']-time.time()))
            process = None
            observation_failed = False
            try:
                process = NativeProcess(action['execution']['argv'],deadline,cwd=action['execution']['cwd'],
                    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')},
                    start_new_session=True,pass_fds=(checkout_lock,),stdin=(subprocess.PIPE
                        if tuple(allocation['operations']) == ('observe-setup-metadata',) else subprocess.DEVNULL))
                observer_owner.observe_setup_command(process,path,journal,action,receipt['command']['attempt_id'])
                if tuple(allocation['operations']) == ('observe-setup-metadata',):
                    process.process.stdin.close()
                process.buffer = b''
                while process.read(deadline):pass
                process.process.wait(timeout=max(0,deadline-time.monotonic()))
            except (ObservationError,subprocess.TimeoutExpired):
                observation_failed = True
            finally:
                try:
                    if process is not None:process.close()
                finally:
                    if process is not None:receipt['returncode'] = process.process.poll()
                    _save(path,journal)
            if observation_failed:
                raise Pending('Owned setup command observation is unavailable; reconcile its original consumed claim') from None
        else:
            process = subprocess.Popen(action["execution"]["argv"], cwd=action["execution"]["cwd"], env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, pass_fds=(checkout_lock,))
            try: receipt["returncode"] = process.wait(timeout=action["execution"]["timeout_seconds"])
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(); receipt["returncode"] = process.returncode
        if [_fingerprint(root,e["path"]) for e in action["effects"]] == [e["after"] for e in action["effects"]]:
            for effect,file in zip(action["effects"],receipt["files"]):
                file["owned_target"]=_scoped_target(root,effect["path"]); file["result_binding"]=file["owned_target"]["binding"]
        _save(path, journal); _checkpoint("command_finished")
        raise Pending("Scoped command direct process exited; exact descendant lifecycle proof is required")
    for effect, file in zip(action["effects"], receipt["files"]):
        _scoped_parent_guard(file["target"], journal)
        _ensure_parents(path, root, effect["path"], journal)
        raw = original["text"].encode() if original["kind"] == "write" else b""
        _scoped_replace(path, root, effect["path"], raw, effect["before"], effect["after"], file, journal)
        file["owned_target"] = _scoped_target(root, effect["path"])
    receipt["status"] = "completed"; _save(path, journal); _checkpoint("outcome")


def _scoped_restore(path, journal, current_record, authority_owned, observation):
    m = journal["manifest"]
    originals, _, dependencies = _scoped_originals(journal["plan"])
    failed = set()
    current_entries = _scoped_entries(current_record)
    enrollment=journal["enrollment"]
    affected={tuple(member[k] for k in ("repository_id","checkout_id","client")) for member in m["enrollment"]["affected"]}
    supporting=any(receipt["files"] and receipt["status"] != "rolled_back" for receipt in journal["receipts"].values())
    if authority_owned and enrollment and supporting and (any(p["phase"]=="rollback_suspended" for p in enrollment["postimages"]) or
            any(entry["activation"]["status"]=="active" for key,(_,_,entry) in current_entries.items() if key in affected)):
        _scoped_publish(path,journal,"rollback_suspended",_scoped_suspended(journal,rollback=True))
    if not authority_owned:
        _canonical_record(current_record,journal["plan"],current=False)
    for action in reversed(m["actions"]):
        receipt = journal["receipts"].get(action["id"])
        if receipt is None: continue
        try:
            if any(action["id"] in dependencies[i] for i in failed):
                raise Pending("A dependent member has not recovered safely")
            if not authority_owned:
                owner = action["owner"]
                if owner["kind"] == "project" or any(action["id"] in dependencies[i] for i in originals if originals[i]["owner"] != owner):
                    raise Pending("Newer authority blocks shared/dependent rollback")
                entries = [e for key, (_, _, e) in current_entries.items() if key[:2] == (owner["repository_id"], owner["checkout_id"])]
                if not entries or any(e["activation"]["status"] == "active" for e in entries):
                    raise Pending("Newer authority has not suspended this member")
                changed={str(Path(owner["root"])/effect["path"]) for effect in action["effects"]}
                if any(set(entry["configuration"]["shared_dependencies"]) & changed for key,(_,_,entry) in current_entries.items()
                       if key[:2] != (owner["repository_id"],owner["checkout_id"]) and entry["configuration"] is not None):
                    raise Pending("A newer dependent member requires shared-resource reconciliation")
            if receipt.get("command") and not receipt["command"]["stopped"]:
                raise Pending("Command descendants remain unreconciled")
            root = Path(action["owner"]["root"])
            for effect, file in reversed(list(zip(action["effects"], receipt["files"]))):
                actual = _scoped_target(root, effect["path"])
                restore = file.get("restore")
                if receipt["status"] == "rolled_back":
                    d3._require(_fingerprint(root, effect["path"]) == effect["before"] and actual["binding"] == (restore["result_binding"] if restore else file["before_binding"]), "Restored member changed externally")
                    if restore is None:
                        d3._require(actual == file["target"], "Restored original parent identity changed")
                    continue
                if restore is None and _fingerprint(root, effect["path"]) == effect["before"] and actual == file["target"]:
                    # A prepared receipt may precede any mutation; preserve the exact original inode.
                    continue
                if restore is None and file["owned_target"] is not None and actual != file["owned_target"]:
                    raise Pending("Scoped output physical ownership changed before rollback")
                if restore is None:
                    restore = dict(temporary=file["temporary"] + "-restore", before_binding=file["result_binding"], prepared_binding=None, result_binding=None)
                    file["restore"] = restore; _save(path, journal)
                raw = d3._read_file(path / file["backup"], d3.MAX_FILE, private=True) if file["backup"] else b""
                if effect["before"]:
                    d3._require(hashlib.sha256(raw).hexdigest() == effect["before"]["sha256"], "Scoped recovery backup changed")
                _scoped_replace(path, root, effect["path"], raw, effect["after"], effect["before"], restore, journal)
            receipt["status"] = "rolled_back"; _save(path, journal)
        except (Pending, StartupError) as error:
            failed.add(action["id"]); journal["blocked"].append(dict(id=action["id"], reason=str(error)))
    if journal["enrollment"]:
        for member in journal["enrollment"]["members"]:
            local=[a["id"] for a in m["actions"] if a["owner"]["kind"]=="repository" and (a["owner"]["repository_id"],a["owner"]["checkout_id"]) == (member["repository_id"],member["checkout_id"])]
            if local and all(journal["receipts"].get(i,{}).get("status")=="rolled_back" for i in local):
                member.update(status="restored",blocked="authority_recovery_pending")
            elif any(i in failed for i in local): member.update(status="blocked",blocked="recovery_dependency_or_ownership")
    if not authority_owned:
        journal["blocked"].append(dict(id="enrollment", reason="Canonical record changed after this transaction; preserve it for scoped reconciliation"))
    elif not failed and journal["enrollment"] is not None:
        root = Path(m["enrollment"]["record"]["project_root"])
        backup = journal["enrollment"]["backup"]
        prior = d3.read_request(d3._read_file(path / backup, d3.MAX_FILE, private=True)) if backup else None
        if prior and any(e[2]["activation"]["status"] == "active" for e in _scoped_entries(prior).values()):
            if not observation or not observation["prior_compatibility"]:
                raise Pending("Prior active configuration requires fresh trusted prior compatibility before restoration")
            _scoped_qualification(journal,observation,prior)
        _scoped_publish(path, journal, "restore", prior)
        if prior and any(e[2]["activation"]["status"] == "active" for e in _scoped_entries(prior).values()):
            _scoped_qualification(journal,observation,prior,published=True)
        _restore_directories(path, root, journal)


@contextmanager
def _scoped_failure_guard(path, ownership, operation):
    """Persist only validated owned failures before releasing transaction locks."""
    try:
        yield
    except (StartupError,OSError,ValueError,TypeError,KeyError,sqlite3.Error) as error:
        journal=ownership["journal"]
        if journal is not None:
            journal["state"]="rollback_partial" if operation=="rollback" else "blocked"
            journal["blocked"].append(dict(id="transaction",reason=str(error) if isinstance(error,StartupError) else "Scoped state or recovery material is unavailable"))
            if journal["enrollment"]:
                for member in journal["enrollment"]["members"]:
                    if member["status"] != "restored": member.update(status="blocked",blocked="transaction_incomplete")
            _save(path,journal)
        raise


def _run_scoped(plan_dir, request, operation, *, host=None, entry_authorizer=None):
    """Internal scoped transaction seam; public _run retains its schema-2 gate."""
    journal = None
    ownership={"journal":None}
    try:
        request=deepcopy(request)
        d3._require(operation in ("apply", "resume", "rollback"), "Unknown scoped operation")
        path = _public_plan_directory(plan_dir)[0] if entry_authorizer is not None else d3._plan_path(plan_dir)
        plan_lock = _public_plan_lock if entry_authorizer is not None else d3._locked
        with plan_lock(path):
            plan = d3._load(path)
            if entry_authorizer is not None:
                entry_authorizer(path, plan, request, operation)
        d3._require(plan is not None, "Scoped D3 proposal is required")
        m = request["manifest"]; root = Path(m["enrollment"]["record"]["project_root"])
        file_only = _file_configuration(m, plan) and (operation == 'rollback'
            or getattr(getattr(host, 'owner', None), '_creation', None) is None)
        d3._require(not d3._relevant_inspection(plan['project_scope']) or file_only,
            'Relevant inspection supports only file configuration; use a full inspection for managed execution')
        originals, dependencies, affected, suspend = _validate_scoped(request, plan,
            operation=operation if file_only else None)
        authority, prior = _scoped_prior(plan)
        locations = _scoped_runtime_locations([prior, m["enrollment"]["record"]], None if file_only else affected)
        affected_roots,affected_git=_scoped_writer_roots(m)
        changed_paths=_scoped_changed_paths(m)
        lock_root = d3._plan_path(LOCK_ROOT)
        destination_keys = {"destination:" + str(root / PROJECT_FILE), "parent:" + str(root) + ":" + d3._digest(plan["identity"]["binding"])}
        if authority["binding"]: destination_keys.add("inode:" + d3._digest(authority["binding"]))
        canonical_location = lock_root / ("canonical-" + hashlib.sha256(("destination:" + str(root / PROJECT_FILE)).encode()).hexdigest() + ".lock")
        operation_owner = dict(schema_version=1,plan_dir=str(path),transaction_id=m["transaction_id"],manifest_digest=d3._digest(m))
        with ExitStack() as locks:
            for key in sorted(destination_keys):
                location = lock_root / ("canonical-" + hashlib.sha256(key.encode()).hexdigest() + ".lock")
                if not location.exists():
                    try:
                        descriptor = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600); os.close(descriptor)
                    except FileExistsError: pass
                locks.enter_context(_lock(location))
            held = _authority_owner(canonical_location)
            if held is not None and held != operation_owner:
                raise Pending("Canonical authority has an interrupted or uncertain operation; resume its exact journal")
            git_dirs = {a["owner"]["git_dir"] for a in m["owners"] if a["owner"]["kind"] == "repository"
                and (Path(a['owner']['root']) in affected_roots or Path(a['owner']['git_common_dir']) in affected_git)}
            if plan["identity"]["kind"] == "standalone":
                git_dirs.update(str(common) for common in affected_git)
            descriptors = {git_dir: locks.enter_context(_worktree_lock(git_dir)) for git_dir in sorted(git_dirs)}
            owner = dict(schema_version=1, plan_dir=str(path), transaction_id=m["transaction_id"], manifest_digest=d3._digest(m))
            if entry_authorizer is None:
                locks.enter_context(_scoped_failure_guard(path,ownership,operation))
            journal = _load_journal(path, file_only=file_only)
            if journal is not None:
                d3._require(all(row['stopped'] for row in journal.get('native_observations',{}).values()),
                    'Original external native source claims remain uncertain; reconcile their exact source claims before project effects')
                d3._require(journal["schema_version"] == 2 and journal["manifest"] == m and journal["approval"] == request["approval"] and journal["plan"] == plan, "Scoped journal binding changed")
                if entry_authorizer is None:
                    ownership["journal"]=journal
            else:
                d3._require(operation == "apply" and _scoped_target(root, str(PROJECT_FILE)) == m["enrollment"]["before"]["target"], "Scoped authority preimage changed or no journal to recover")
                if prior:
                    raw = d3._read_file(root / PROJECT_FILE, 128 * 1024)
                    d3._require(hashlib.sha256(raw).hexdigest() == m["enrollment"]["before"]["raw_sha256"] and d3.read_request(raw) == prior, "Canonical approved bytes changed")
            if not file_only and entry_authorizer is not None and getattr(host.owner, '_creation', None) is not None:
                journal = _prepare_birth(path, plan, request, host, owner, journal)
            source_guards = _public_source_guards if entry_authorizer is not None else _scoped_source_guards
            retained_contexts = ()
            if file_only:
                # File recovery retains runtime history; hold exclusion through every mutation.
                retained_guards = locks.enter_context(_authority_guards(locations, owner))
                allocation = journal.get('birth', {}).get('allocation') if journal else None
                allocated = allocation['authority'] if allocation else None
                if allocated:
                    d3._require(Path(allocated['directory']) in locations
                        and task_state._authority_identity(Path(allocated['database'])) == allocated,
                        'Previously allocated runtime is missing or replaced')
                retained_contexts = tuple(_recovery_context(allocated if allocated and directory == Path(allocated['directory'])
                    else task_state._authority_identity(directory / 'runtime.sqlite3'))
                    for directory, (_, version) in locations.items() if version == 3
                    and os.path.lexists(directory / 'runtime.sqlite3'))
                source_guards = lambda *_: nullcontext((retained_guards, ()))
            with source_guards(locations, owner, m) as (authority_locations,source_contexts):
                if entry_authorizer is not None:
                    if not file_only:
                        authorities = _public_existing_authorities(locations, source_contexts, plan, m, journal=journal)
                        entry_authorizer(path, plan, request, operation, authorities=authorities)
                    locks.enter_context(_scoped_failure_guard(path, ownership, operation))
                    ownership['journal'] = journal
                writer_locations=_scoped_writer_locations(locations,retained_contexts if file_only else source_contexts)
                _scoped_writers(writer_locations, affected_roots, affected_git, changed_paths=changed_paths, live_only=file_only)
                for git_dir in git_dirs:
                    marker = _command_owner(git_dir)
                    d3._require(marker is None or marker["plan_dir"] == str(path) and marker["transaction_id"] == m["transaction_id"], "Unresolved command owns an affected checkout")
                if journal is None:
                    if not file_only:
                        current, blockers = d3._project_current(plan)
                        d3._require(not d3._project_changes(plan, current), "Scoped D3 inputs changed before journal")
                    for action in m["actions"]:
                        for effect in action["effects"]:
                            d3._require(_fingerprint(Path(action["owner"]["root"]),effect["path"]) == effect["before"], "Scoped approved effect preimage changed")
                            if "target" in effect:
                                selected_root=Path(action["owner"]["root"])
                                d3._require(_scoped_target(selected_root,effect["path"]) == effect["target"], "Approved command target/parent physical identity changed")
                                existing=next(p for p in ((selected_root/effect["path"]).parent,*(selected_root/effect["path"]).parent.parents) if p.is_dir())
                                d3._require(checkout(existing)==(selected_root,Path(action["owner"]["git_common_dir"])), "Command effect enters another checkout")
                    journal = dict(schema_version=2, transaction_id=m["transaction_id"], manifest_digest=d3._digest(m), manifest=deepcopy(m), approval=deepcopy(request["approval"]), plan=deepcopy(plan),
                        state="prepared", receipts={}, enrollment=None, blocked=[], directories={}, observations=[])
                    _save(path, journal)
                    ownership["journal"]=journal
                if operation != 'rollback' and entry_authorizer is not None:
                    _public_host(host)
                    if callable(getattr(host.owner, 'record_setup', None)):
                        host.owner.record_setup(path, journal)
                with _d4_publication_batch(path,journal,source_contexts,request,host,owner,[canonical_location,*authority_locations]):
                    _scoped_register_sources(path,journal,source_contexts)
                if not file_only and journal.get('birth'):
                    journal['birth']['phase']='registered'; _save(path,journal)
            def settle_sources():
                d3._require(not any(r.get("command") and not r["command"]["stopped"] for r in journal["receipts"].values()),
                    "Scoped command descendants remain uncertain; preserve recovery markers")
                with source_guards(locations, owner, m) as (final_locations,source_contexts):
                    writer_locations=_scoped_writer_locations(locations,retained_contexts if file_only else source_contexts)
                    _scoped_writers(writer_locations,affected_roots,affected_git,changed_paths=changed_paths, live_only=file_only)
                    with _d4_publication_batch(path,journal,source_contexts,request,host,owner,[canonical_location,*final_locations]):
                        _scoped_register_sources(path,journal,source_contexts)
                        for location in [canonical_location,*final_locations]:
                            d3._require(_authority_owner(location)==owner,'D4 marker changed before final removal')
                        for location in [canonical_location,*final_locations]:os.unlink(Path(str(location)+".json"))
                        descriptor=d3._open_path(lock_root,os.O_RDONLY|os.O_DIRECTORY)
                        try: os.fsync(descriptor)
                        finally: os.close(descriptor)
            journal["blocked"] = []
            observation = request.get("observations")
            if observation:
                _scoped_observation_shape(observation,m)
                d3._require(observation["manifest_digest"] == journal["manifest_digest"] and observation["input_digest"] == _scoped_input_digest(m), "Scoped observations are stale")
                journal["observations"].append(deepcopy(observation)); _save(path, journal)
            _scoped_owner_bindings(plan)
            _scoped_recovery_files(path, journal)
            if journal["state"] == "rolled_back":
                d3._require(operation == "rollback", "Rolled-back transaction cannot apply again")
                for action in m["actions"]:
                    for effect,file in zip(action["effects"],journal["receipts"].get(action["id"],{}).get("files",[])):
                        expected = file.get("restore",{}).get("result_binding",file["before_binding"])
                        d3._require(_fingerprint(Path(action["owner"]["root"]),effect["path"]) == effect["before"] and _scoped_target(Path(action["owner"]["root"]),effect["path"])["binding"] == expected, "Previously restored effect changed")
                last=journal["enrollment"]["postimages"][-1]
                d3._require(_fingerprint(root,str(PROJECT_FILE)) == last["after"] and (last["after"] is None or _scoped_target(root,str(PROJECT_FILE)) == last["target"]), "Restored authority changed")
                settle_sources()
                return dict(state="rolled_back",exit_code=0,blocked=[],activation_status="configured",qualified=False)
            authority_owned = _scoped_owned_authority(journal)
            current_record = d3.read_request(d3._read_file(root / PROJECT_FILE, 128 * 1024)) if (root / PROJECT_FILE).exists() else None
            current_locations = _scoped_runtime_locations([current_record], affected)
            d3._require(set(current_locations) <= set(locations), "New authority needs separately reconciled state guards")
            _scoped_writers(writer_locations, affected_roots, affected_git, changed_paths=changed_paths, live_only=file_only)
            if operation == "rollback":
                _scoped_restore(path, journal, current_record, authority_owned, observation)
                journal["state"] = "rollback_partial" if journal["blocked"] else "rolled_back"
                if journal.get('birth'):journal['birth']['phase']='retained'
            else:
                d3._require(journal["state"] not in ("rolled_back", "rollback_partial"), "Rolled back transaction cannot resume application")
                if not authority_owned: raise Pending("Canonical authority changed outside owned postimages")
                if file_only:
                    _relevant_forward_inputs(plan, journal)
                    _check_operation_files(path, journal)
                else:
                    _scoped_fresh(plan, journal)
                if journal["enrollment"] is None:
                    before = _fingerprint(root, str(PROJECT_FILE))
                    backup = _backup(path, root, str(PROJECT_FILE), before, "enrollment.backup")
                    journal["enrollment"] = dict(before=deepcopy(m["enrollment"]["before"]), backup=backup, status="prepared", temporary=".d4-"+m["transaction_id"]+"-enrollment", postimages=[], members=[dict(member,status="pending",blocked=None) for member in deepcopy(m["enrollment"]["affected"])])
                    _save(path, journal)
                if suspend and not any(p["phase"] == "final" for p in journal["enrollment"]["postimages"]):
                    suspended = _scoped_suspended(journal)
                    _scoped_publish(path, journal, "suspended", suspended)
                journal["state"] = "applying"; _save(path, journal)
                completed = set()
                for action in m["actions"]:
                    if action["id"] not in m["approved_scope"] or not dependencies[action["id"]] <= completed:
                        journal["blocked"].append(dict(id=action["id"], reason="Scoped action approval/dependency missing")); continue
                    try:
                        if originals[action["id"]]["target"] != str(PROJECT_FILE):
                            _relevant_forward_inputs(plan, journal)
                            _scoped_writers(writer_locations, affected_roots, affected_git, changed_paths=changed_paths, live_only=file_only)
                            if not file_only and journal.get('birth'):_birth_current_history(m['enrollment']['record'],journal['birth']['intent']['history'])
                            _scoped_apply_action(path, journal, action, originals[action["id"]], observation, descriptors.get(action["owner"].get("git_dir")),
                                observer_owner=getattr(host,'owner',None) if entry_authorizer is not None else None)
                        completed.add(action["id"])
                    except Pending as error:
                        journal["blocked"].append(dict(id=action["id"], reason=str(error)))
                if not set(m["enrollment"]["requires"]) <= completed:
                    raise Pending("Required scoped adoption effects are incomplete")
                activates = any(e[2]["activation"]["status"] == "active" for key, e in _scoped_entries(m["enrollment"]["record"]).items() if key in affected)
                if activates: _scoped_qualification(journal,observation,m["enrollment"]["record"])
                _relevant_forward_inputs(plan, journal)
                _scoped_writers(writer_locations, affected_roots, affected_git, changed_paths=changed_paths, live_only=file_only)
                if not _scoped_owned_authority(journal): raise Pending("Canonical preimage ownership changed before publication")
                _scoped_publish(path, journal, "final", m["enrollment"]["record"])
                if activates: _scoped_qualification(journal,observation,m["enrollment"]["record"],published=True)
                entries=_scoped_entries(m["enrollment"]["record"])
                for member in journal["enrollment"]["members"]:
                    member["status"]=entries[tuple(member[k] for k in ("repository_id","checkout_id","client"))][2]["activation"]["status"]
                    member["blocked"]=None
                journal["state"] = "blocked" if journal["blocked"] else "applied"
            _save(path, journal)
            unsettled = any(r.get("command") and not r["command"]["stopped"] for r in journal["receipts"].values())
            if not unsettled:settle_sources()
            from runtime.setup_observation import _coverage
            return dict(transaction_id=journal["transaction_id"],state=journal["state"],exit_code=0 if journal["state"] in ("applied","rolled_back") else 2,
                        blocked=deepcopy(journal["blocked"]),members=deepcopy(journal["enrollment"]["members"]),
                        activation_status="active" if operation != "rollback" and journal["enrollment"]["members"] and all(m["status"]=="active" for m in journal["enrollment"]["members"]) else "configured",
                        qualified=operation != "rollback" and bool(journal["enrollment"]["members"]) and all(m["status"]=="active" for m in journal["enrollment"]["members"]) and journal["state"] == "applied"
                            and all(_coverage(receipt)[1]
                                for receipt in journal.get('setup_receipts',{}).values()))
    except (StartupError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        owned=ownership["journal"]
        reason=str(error) if isinstance(error,StartupError) else "Scoped input or state is unavailable"
        return dict(state=owned["state"] if owned else "blocked",exit_code=2,
                    blocked=deepcopy(owned["blocked"]) if owned else [dict(id="transaction",reason=reason)],
                    activation_status="configured",qualified=False)


def _scoped_qualification(journal, observation, record, *, published=False):
    from runtime.startup import decode_project_record, preview_canonical_scope, resolve_canonical_scope
    if not observation or observation["input_digest"] != _scoped_input_digest(journal["manifest"]) and not published:
        raise Pending("Fresh scoped adoption qualification observations are required")
    qualified = []
    for key, (_, _, entry) in _scoped_entries(record).items():
        if entry["activation"]["status"] != "active": continue
        if key not in {tuple(m[k] for k in ("repository_id","checkout_id","client")) for m in journal["manifest"]["enrollment"]["affected"]}: continue
        proof = next((p for p in observation["qualification"] if tuple(p[k] for k in ("repository_id","checkout_id","client")) == key), None)
        if proof is None: raise Pending("Member/client qualification is missing")
        d3._object(proof, ("repository_id","checkout_id","client","requirements","general_review","security_review","scope_observations"))
        rule = next(r for r in journal["manifest"]["enrollment"]["qualification"] if tuple(r[k] for k in ("repository_id","checkout_id","client")) == key)
        seen = set()
        for row in proof["requirements"]:
            d3._object(row,("id","evidence")); d3._require(row["id"] not in seen and row["id"] in rule["requirements"], "Unknown or duplicate qualification requirement")
            seen.add(row["id"]); d3._require(_evidence(row["evidence"],many=True), "Empty mandatory qualification evidence")
        if not _evidence(proof["general_review"],many=True) or rule["security_required"] and not _evidence(proof["security_review"],many=True):
            raise Pending("Mandatory general/security/member qualification is incomplete")
        assessment = next(a for a in journal["plan"]["assessments"]["repositories"] if (a["owner"]["repository_id"],a["owner"]["checkout_id"]) == key[:2])
        d3._require(all(assessment["semantic_assessment"].get(k) for k in ("complete","catalog_complete","conflicts_checked","instructions_checked","source_provenance_checked")), "Complete member semantic adoption assessment is required")
        if any(d["disposition"] in ("pending","proposed") for d in assessment["decisions"]):
            raise Pending("Unresolved member adoption decisions prevent activation")
        facts = deepcopy(proof["scope_observations"])
        d3._object(facts,("authority","observation","client","installed"))
        d3._require(facts["client"] == key[2], "Qualification selected another client")
        partial = False
        if key[2] == 'codex':
            facts['installed'] = dict(policy_digest=policy_digest(task_state.PACKAGE), runtime_digest=task_state.runtime_fingerprint(),
                clients=[key[2]] if (task_state.PACKAGE/'clients'/(key[2]+'.md')).is_file() else [], package_root=str(task_state.PACKAGE))
            from runtime.setup_observation import read_qualification
            facts['observation']['qualification'] = read_qualification(record, facts,
                cwd=facts['observation']['root'], pending_journal=journal)
            if facts['observation']['qualification'] is None:
                raise Pending('Actual retained owner setup qualification is unavailable')
            capabilities = facts['observation']['qualification'].get('capabilities')
            partial = capabilities is not None and not capabilities['complete']
        if not partial and not set(rule['requirements']) <= seen:
            raise Pending('Mandatory general/security/member qualification is incomplete')
        root = Path(record["project_root"])
        actual = _scoped_target(root,str(PROJECT_FILE))
        if published:
            raw = d3._read_file(root / PROJECT_FILE,128*1024)
            d3._require(d3.read_request(raw) == record and hashlib.sha256(raw).hexdigest() == actual["fingerprint"].get("sha256"), "Published authority reread differs from intended bytes")
            facts["authority"] = dict(path=str(root / PROJECT_FILE),target=actual)
            result = resolve_canonical_scope(decode_project_record(d3.read_request(raw)),**facts)
            if result.status != "active": raise Pending("Published authority failed current qualification: " + result.reason)
            qualified.append(result.scope)
        else:
            d3._require(facts["authority"] == dict(path=str(root / PROJECT_FILE),target=actual), "Preview authority preimage provenance changed")
            result = preview_canonical_scope(decode_project_record(record),**facts)
            if not result.eligible: raise Pending("Proposed authority is not eligible: " + result.reason)
    return qualified


def _scoped_observation_shape(observation, manifest):
    d3._object(observation,("manifest_digest","input_digest","qualification","task_reconciliation","prior_compatibility","command_reconciliation"))
    d3._require(observation["manifest_digest"] == d3._digest(manifest),"Scoped observation belongs to another manifest")
    d3._hash(observation["input_digest"])
    for key in ("qualification","task_reconciliation","prior_compatibility","command_reconciliation"):
        d3._require(isinstance(observation[key],list) and len(observation[key]) <= d3.MAX_PATHS,"Invalid bounded scoped observation rows")
    commands={a["id"] for a in manifest["actions"] if a["execution"] is not None}
    seen=set()
    for proof in observation["command_reconciliation"]:
        d3._object(proof,("id","attempt_id","stopped","evidence"))
        d3._require(proof["id"] in commands and proof["id"] not in seen and isinstance(proof["attempt_id"],str)
            and re.fullmatch(r"[0-9a-f]{32}",proof["attempt_id"]) and proof["stopped"] is True
            and _evidence(proof["evidence"],many=True),"Invalid exact command lifecycle proof")
        seen.add(proof["id"])
    members={tuple(row[k] for k in ("repository_id","checkout_id","client")) for row in manifest["enrollment"]["affected"]}
    for collection in ("qualification","task_reconciliation","prior_compatibility"):
        seen=set()
        for proof in observation[collection]:
            fields=("repository_id","checkout_id","client","requirements","general_review","security_review","scope_observations") if collection=="qualification" else ("repository_id","checkout_id","client","state_dir","evidence")
            d3._object(proof,fields)
            key=tuple(proof[k] for k in ("repository_id","checkout_id","client"))
            identity=(key,proof.get("state_dir"))
            d3._require(key in members and identity not in seen,"Unknown or duplicate scoped proof owner")
            seen.add(identity)
            if collection != "qualification":
                if proof["state_dir"] is not None:
                    from runtime.startup import _canonical_path
                    _canonical_path(proof["state_dir"])
                d3._require(_evidence(proof["evidence"],many=True),"Missing scoped authority reconciliation evidence")
