"""Pure safety parts of the live qualification harness (no server, no network): the route rule, isolation and
version refusals, the durable write-ahead call ledger, the recorder plugin source, plugin-chain verification,
user-directory snapshots, leftover-process detection and redaction. Unit-tested in
tests/test_qualification_harness.py."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import traceback

PROVIDER, MODEL, VARIANT = "claude-agent", "haiku", "low"
ROUTE = "%s/%s#%s" % (PROVIDER, MODEL, VARIANT)
HARNESS_NAME = "claude-agent-sdk via opencode"
PROMPT_TEXT = "Reply with exactly: OK"
QUALIFIED_OPENCODE = "2.2.6"
# Durable caps per purpose (the user's authorizations): qualification 14, the bounded pilot canary 24.
CAPS = {"qualification": 14, "pilot": 24}
# The most a recorded cap line may raise each purpose to (pre-registration edit 4: pilot 24 -> 28 before attempt 2).
MAX_CAPS = {"qualification": 14, "pilot": 28}
# What the budget counts. A claude-agent SDK turn or an internal retry could make more upstream API calls than this.
COUNTING = ("OpenCode `model.request` hook invocations that passed the routing shim and the qual-post guard "
            "(transmitted by OpenCode); not upstream API calls, which the claude-agent SDK may retry or split")
# The five qualification calls already spent on 9 November 2026 (qual-call-ledger.json history), seeded once.
SEED_HISTORY = (
    dict(run=1, stage="1", label="P0.7 (a) prompt with derived ids", calls=1, note="harness stopped on message paging (cursor sent as an object)"),
    dict(run=2, stage="1", label="P0.7 (a) prompt with derived ids", calls=1, note="harness stopped on message paging (order with cursor)"),
    dict(run=3, stage="1", label="P0.7 (a) prompt with derived ids", calls=1, note=None),
    dict(run=3, stage="2", label="managed primary request (derived ids, attached)", calls=1, note=None),
    dict(run=3, stage="3", label="title request of the managed session", calls=1, note=None),
)
PLUGIN_CHAIN = ("qual-pre", "vibe-workflow.routing", "qual-post")
# Never an isolated dir: OpenCode's user directories and the macOS application data area.
PROTECTED = (".config/opencode", ".local/share/opencode", ".local/state/opencode", ".cache/opencode",
             "Library/Application Support")
# The user's real OpenCode directories compared before and after a run (names and mtimes only).
USER_OPENCODE_DIRS = (".config/opencode", ".local/share/opencode", ".local/state/opencode", ".cache/opencode")
# Files the user's running desktop OpenCode itself rewrites: reported as churn, never hidden, never failing.
DESKTOP_CHURN = re.compile(r"^(?:.*/)?(?:opencode\.db(?:-wal|-shm|-journal)?|[^/]*\.log(?:\.\d+)?)$|^log/")
DERIVED_SESSION = re.compile(r"ses_[0-9a-f]{32}")
DERIVED_MESSAGE = re.compile(r"msg_[0-9a-f]{32}")
# An id body has a digit or is at least 20 characters, so field names such as `call_ledger` are never taken for ids.
ID_TOKEN = re.compile(r"\b(ses|msg|prt|evt|cred|per|toolu|call|prj|loc)_(?=[A-Za-z0-9]*[0-9]|[A-Za-z0-9]{20})[A-Za-z0-9]{6,}\b")
PROBE_ENV = "QUAL_PROBE_TOKEN"


class Refused(Exception):
    """A safety precondition failed; nothing was started (or nothing more is sent)."""


class BudgetExceeded(Exception):
    pass


class Blocker(Exception):
    """A stage exposed a blocker: stop and report."""


def check_model(ref):
    """Accept only the qualification route, `claude-agent/haiku` with no variant or `#low`; refuse anything else."""
    if type(ref) is not str or ref not in ("%s/%s" % (PROVIDER, MODEL), ROUTE):
        raise Refused("only %s is allowed" % ROUTE)
    return ROUTE


def on_route(record):
    """Whether a recorded model request is exactly the qualification route, variant included."""
    return (record.get("provider"), record.get("model"), record.get("variant")) == (PROVIDER, MODEL, VARIANT)


def inside_git_worktree(path):
    """Whether `path` or an ancestor has `.git` (a repository or a linked worktree's `.git` file)."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return True
    return False


def check_isolated_dir(path, home=None):
    """The resolved isolated directory, or `Refused`: it must exist, be empty, owner-only, outside OpenCode's user
    directories and ~/Library/Application Support, outside any git worktree, and not the home directory."""
    if not path:
        raise Refused("an explicit --isolated-dir is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise Refused("--isolated-dir must be absolute")
    if not candidate.is_dir() or candidate.is_symlink():
        raise Refused("--isolated-dir must be an existing directory, not a symlink")
    resolved = candidate.resolve()
    home = Path(home or Path.home()).resolve()
    if resolved == home or resolved in home.parents:
        raise Refused("--isolated-dir must not be the home directory or above it")
    for protected in PROTECTED:
        guarded = (home / protected).resolve()  # a symlinked ~/.local or ~/.config cannot slip past
        if resolved == guarded or guarded in resolved.parents:
            raise Refused("--isolated-dir must be outside the user's OpenCode and application directories")
    if inside_git_worktree(resolved):
        raise Refused("--isolated-dir must not be inside a git worktree")
    info = resolved.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused("--isolated-dir must be owned by this user with mode 0700")
    if any(resolved.iterdir()):
        raise Refused("--isolated-dir must be empty")
    return resolved


def check_opencode_version(run=subprocess.run, env=None):
    """`opencode --version` on the server's PATH must be the qualified version; checked before anything starts."""
    try:
        done = run(["opencode", "--version"], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        raise Refused("opencode is not runnable") from None
    match = re.fullmatch(r"\s*(?:opencode\s+)?v?(\d+\.\d+\.\d+)\s*", done.stdout or "")
    if done.returncode != 0 or not match or match.group(1) != QUALIFIED_OPENCODE:
        raise Refused("opencode on PATH is not %s" % QUALIFIED_OPENCODE)
    return match.group(1)


# ---------------------------------------------------------------- durable call ledger

def _owner_only(path, mode):
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) is False and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode


class CallLedger:
    """Append-only JSON-lines record of real model requests per purpose, outside every run directory.

    Write-ahead: `reserve` (under an exclusive flock, after checking the purpose cap) is written before anything is
    sent; `settle` records what was actually transmitted. An unsettled reservation counts in full, so a crash or a
    killed harness never under-counts. The file and its directory must be owner-only; a missing or damaged ledger
    refuses (fail closed) and is never recreated implicitly."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise Refused("the call ledger path must be absolute")
        try:
            if not (_owner_only(self.path.parent, 0o700) and _owner_only(self.path, 0o600)):
                raise Refused("the call ledger and its directory must be owner-only (0600 in 0700)")
        except FileNotFoundError:
            raise Refused("the call ledger does not exist; create it once with --init-call-ledger") from None

    @classmethod
    def initialize(cls, path, seed=SEED_HISTORY):
        path = Path(path)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not _owner_only(path.parent, 0o700):
            raise Refused("the call ledger directory must be owner-only (0700)")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise Refused("the call ledger already exists; it is never re-seeded") from None
        with os.fdopen(descriptor, "w") as handle:
            for index, entry in enumerate(seed, 1):
                handle.write(json.dumps(dict(v=1, seq=index, kind="seed", purpose="qualification", at="2026-11-09",
                                             **entry), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return cls(path)

    def _locked(self, write):
        handle = open(self.path, "a+" if write else "r")
        fcntl.flock(handle, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        return handle

    @staticmethod
    def _parse(text):
        """Every record is validated here, so a malformed line is `Refused`, never a KeyError later."""
        records = []
        integer = lambda value: type(value) is int and value >= 0  # noqa: E731
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                raise Refused("the call ledger is damaged") from None
            if type(record) is not dict or record.get("v") != 1 or record.get("kind") not in ("seed", "reserve", "settle", "cap") \
                    or not integer(record.get("calls")) or not integer(record.get("seq")) \
                    or record.get("purpose") not in CAPS or (record["kind"] == "settle" and not integer(record.get("ref"))) \
                    or (record["kind"] == "cap" and (not integer(record.get("cap")) or record["cap"] > MAX_CAPS[record["purpose"]])):
                raise Refused("the call ledger is damaged")
            records.append(record)
        if len({record["seq"] for record in records}) != len(records):
            raise Refused("the call ledger is damaged")
        return records

    def _read(self, handle):
        handle.seek(0)
        return self._parse(handle.read())

    @staticmethod
    def _totals(records):
        reserved, settled = {}, {}
        for record in records:
            if record["kind"] == "reserve":
                reserved[record["seq"]] = record
            elif record["kind"] == "settle":
                settled[record["ref"]] = record["calls"]
        totals = {}
        caps = dict(CAPS)
        for record in records:  # the latest recorded cap line wins; history is never rewritten
            if record["kind"] == "cap":
                caps[record["purpose"]] = record["cap"]
        for purpose, cap in caps.items():
            spent = sum(r["calls"] for r in records if r["kind"] == "seed" and r["purpose"] == purpose)
            spent += sum(settled.get(seq, r["calls"]) for seq, r in reserved.items() if r["purpose"] == purpose)
            open_count = sum(1 for seq, r in reserved.items() if r["purpose"] == purpose and seq not in settled)
            totals[purpose] = dict(cap=cap, spent=spent, remaining=max(0, cap - spent), open_reservations=open_count)
        return totals, reserved, settled

    def _append(self, handle, records, entry):
        entry = dict(v=1, seq=1 + max([r.get("seq", 0) for r in records] or [0]),
                     at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry)
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return entry["seq"]

    def reserve(self, purpose, label, calls, run=None):
        """Write the intended calls before sending; refuses (writing nothing) past the purpose's cap."""
        if purpose not in CAPS:
            raise Refused("unknown call purpose")
        if type(calls) is not int or calls < 0:
            raise ValueError("calls must be a non-negative integer")
        with self._locked(True) as handle:
            records = self._read(handle)
            totals = self._totals(records)[0][purpose]
            if totals["spent"] + calls > totals["cap"]:
                raise BudgetExceeded("%s: %d more calls exceed the remaining %d of %d" % (purpose, calls, totals["remaining"],
                                                                                         totals["cap"]))
            return self._append(handle, records, dict(kind="reserve", purpose=purpose, label=str(label)[:120],
                                                      calls=calls, run=run, pid=os.getpid()))

    def settle(self, ref, actual, note=None):
        """Record what a reservation actually sent. More than reserved is recorded, then raises."""
        if type(actual) is not int or actual < 0:
            raise ValueError("actual must be a non-negative integer")
        with self._locked(True) as handle:
            records = self._read(handle)
            _, reserved, settled = self._totals(records)
            if ref not in reserved or ref in settled:
                raise Refused("unknown or already settled reservation")
            purpose = reserved[ref]["purpose"]
            self._append(handle, records, dict(kind="settle", ref=ref, purpose=purpose, calls=actual, note=note))
            totals = self._totals(self._read(handle))[0][purpose]
        if actual > reserved[ref]["calls"] or totals["spent"] > totals["cap"]:
            raise BudgetExceeded("%s: %d sent against %d reserved" % (purpose, actual, reserved[ref]["calls"]))
        return totals

    def set_cap(self, purpose, cap, reason):
        """Append a recorded cap change (a pre-registration edit); never above `MAX_CAPS`, never below what was spent."""
        if purpose not in CAPS:
            raise Refused("unknown call purpose")
        if type(cap) is not int or not reason or not str(reason).strip():
            raise Refused("a cap change needs an integer cap and a recorded reason")
        if cap > MAX_CAPS[purpose]:
            raise Refused("the cap may not exceed the recorded maximum %d" % MAX_CAPS[purpose])
        with self._locked(True) as handle:
            records = self._read(handle)
            if cap < self._totals(records)[0][purpose]["spent"]:
                raise Refused("the cap may not be below what was already spent")
            self._append(handle, records, dict(kind="cap", purpose=purpose, calls=0, cap=cap, reason=str(reason)[:300]))
            return self._totals(self._read(handle))[0][purpose]

    def settle_dead(self, ref, processes):
        """Settle a reservation whose run died, at its full reserved count (never under-counting).

        Refuses when the reservation is unknown or settled, when its recorded pid is still alive, or when any
        harness run appears in `processes` (`ps -axo pid=,command=` output), because that run might still send."""
        own = os.getpid()
        for line in (processes or "").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != own and "opencode_live.py" in parts[1] \
                    and "--settle-dead" not in parts[1]:
                raise Refused("a harness run is still alive; stop it before settling a dead reservation")
        with self._locked(True) as handle:
            records = self._read(handle)
            _, reserved, settled = self._totals(records)
            if ref not in reserved or ref in settled:
                raise Refused("unknown or already settled reservation")
            pid = reserved[ref].get("pid")
            if type(pid) is int and _alive(pid):
                raise Refused("the reserving process is still alive")
            purpose, calls = reserved[ref]["purpose"], reserved[ref]["calls"]
            self._append(handle, records, dict(kind="settle", ref=ref, purpose=purpose, calls=calls,
                                               note="dead run: settled at the reserved count"))
            return self._totals(self._read(handle))[0][purpose]

    def remaining(self, purpose):
        return self.summary()[purpose]["remaining"]

    def summary(self):
        with self._locked(False) as handle:
            return self._totals(self._read(handle))[0]

    def export(self):
        """The committed qual-call-ledger.json view: generated from this file, labels and counts only."""
        with self._locked(False) as handle:
            records = self._read(handle)
        totals, reserved, settled = self._totals(records)
        entries = []
        for record in records:
            if record["kind"] == "seed":
                entries.append({k: record.get(k) for k in ("purpose", "run", "stage", "label", "calls", "note")}
                               | dict(kind="seed"))
            elif record["kind"] == "reserve":
                entries.append(dict(kind="reservation", purpose=record["purpose"], label=record["label"],
                                    reserved=record["calls"], sent=settled.get(record["seq"]),
                                    settled=record["seq"] in settled))
            elif record["kind"] == "cap":
                entries.append(dict(kind="cap", purpose=record["purpose"], cap=record["cap"], reason=record.get("reason")))
        return {"_meta": {"caps": {purpose: totals[purpose]["cap"] for purpose in totals}, "caps_in_code": CAPS, "counting": COUNTING, "generated_from": "the durable call ledger (--export-call-ledger)",
                          "write_ahead": "an unsettled reservation counts in full"},
                "totals": totals, "entries": entries}


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- recorder plugins

RECORDER = r"""
// Qualification recorder (__PHASE__). Logs hook facts as JSON lines; never headers, environment values or prompt text.
// Fail closed: a log write that fails throws, which blocks the hook. `post` runs after the routing shim and is the
// last gate before OpenCode sends a request.
import { appendFileSync, readFileSync } from "node:fs"
const PHASE = "__PHASE__"
export default {
  id: "qual-__PHASE__",
  async setup(ctx) {
    const o = ctx?.options ?? {}
    const log = (record) => { appendFileSync(o.log, JSON.stringify({ ...record, phase: PHASE, t: performance.now(), wall: Date.now() }) + "\n") }
    const control = () => { try { return JSON.parse(readFileSync(o.control, "utf8")) } catch { return { budget: 0 } } }
    const lines = () => { try { return readFileSync(o.log, "utf8").split("\n") } catch { return null } }
    const sent = () => { const all = lines(); return all === null ? 1e9 : all.filter((l) => l.includes('"decision":"transmit"')).length }
    const titles = () => { const all = lines(); return all === null ? 1e9 : all.filter((l) => l.includes('"decision":"transmit"') && l.includes('"kind":"title"')).length }
    const guard = (reason, record) => { try { log({ ...record, decision: reason }) } finally { throw new Error(`qualification guard (${reason})`) } }
    await ctx.session.hook("model.request", async (event) => {
      const m = event?.model ?? {}
      const record = { hook: "model.request", kind: event?.kind ?? null, agent: event?.agent ?? null, provider: m.providerID ?? null,
        model: m.id ?? null, variant: m.variant ?? null, session: event?.sessionID ?? null }
      if (PHASE === "pre") return log(record)
      const c = control()
      if (m.providerID !== "__PROVIDER__" || m.id !== "__MODEL__") guard("guard_model", record)
      if (m.variant !== "__VARIANT__") guard("guard_variant", record)
      if ((c.hold ?? []).includes(event?.sessionID)) guard("guard_hold", record)
      if (event?.kind === "title" && !(c.title_session === event?.sessionID && titles() < 1)) guard("guard_title", record)
      if (event?.kind !== "primary" && event?.kind !== "title") guard("guard_kind", record)
      if (sent() >= (c.budget ?? 0)) guard("guard_budget", record)
      log({ ...record, decision: "transmit" })
    })
    await ctx.tool.hook("execute.before", async (event) => {
      const record = { hook: "tool.execute.before", tool: event?.tool ?? null, session: event?.sessionID ?? null,
        spawn_model: typeof event?.input?.model === "string" ? event.input.model : null }
      if (PHASE === "pre") return log(record)
      if (record.tool === "subagent" && record.spawn_model !== null && record.spawn_model !== "__ROUTE__") guard("guard_spawn_model", record)
      log({ ...record, decision: "passed" })
    })
    await ctx.permission.hook("evaluate", async (event) => {
      log({ hook: "permission.evaluate", action: event?.action ?? null, effect: event?.effect ?? null, session: event?.sessionID ?? null })
    })
    await ctx.shell.hook("create.before", (event) => {
      const env = event?.env
      log({ hook: "shell.create.before", session: null, env_count: env && typeof env === "object" ? Object.keys(env).length : null,
        probe_present: !!(env && typeof env === "object" && Object.prototype.hasOwnProperty.call(env, "__PROBE__")) })
    })
  },
}
"""


def recorder_source(phase):
    if phase not in ("pre", "post"):
        raise ValueError("phase must be pre or post")
    return (RECORDER.replace("__PHASE__", phase).replace("__PROVIDER__", PROVIDER).replace("__MODEL__", MODEL)
            .replace("__VARIANT__", VARIANT).replace("__ROUTE__", ROUTE).replace("__PROBE__", PROBE_ENV))


def verify_plugin_chain(plugins, guard, pending, guard_outcome, guard_reason, pending_reason):
    """Reasons the recorder/shim/recorder chain cannot be trusted (empty when it can).

    `plugins` is the server's plugin id list (presence only: its order is not documented). Order is proven by
    behavior: the guard probe (an interactive request on a variant the guard must refuse) must be seen by `qual-pre`
    and then blocked by `qual-post` with `guard_variant`; the pending probe (a ledger-owned derived session that is
    not attached) must be seen by `qual-pre` and blocked by the shim (`attach_required`) before `qual-post`."""
    reasons = ["plugin_missing:%s" % name for name in PLUGIN_CHAIN if name not in (plugins or [])]
    pre = [r for r in guard if r.get("phase") == "pre"]
    post = [r for r in guard if r.get("phase") == "post"]
    if not pre:
        reasons.append("guard_probe_not_seen_by_pre")
    if not post:
        reasons.append("guard_probe_not_blocked")
    elif any(r.get("decision") == "transmit" for r in post):
        reasons.append("guard_probe_transmitted")
    elif any(r.get("decision") != "guard_variant" for r in post):
        reasons.append("guard_variant_not_enforced")
    if pre and post and post[0]["t"] < pre[0]["t"]:
        reasons.append("recorders_out_of_order")
    if guard_outcome != "failed":
        reasons.append("guard_probe_outcome")
    if guard_reason != "guard_variant":
        reasons.append("guard_probe_reason")  # the session failed for another reason (a provider rejection, say)
    if not any(r.get("phase") == "pre" for r in pending):
        reasons.append("pending_probe_not_seen")
    if any(r.get("phase") == "post" for r in pending):
        reasons.append("shim_not_between_recorders")
    if pending_reason != "attach_required":
        reasons.append("shim_did_not_block")
    return reasons


# ---------------------------------------------------------------- user directories and cleanup

def snapshot_tree(root, max_entries=20000, max_depth=8):
    """`{relative name: mtime_ns}` for files and `{name/: None}` for directories under `root` (no contents)."""
    root = Path(root)
    if not root.is_dir():
        return {}
    found = {}
    for current, dirs, files in os.walk(root):
        relative = os.path.relpath(current, root)
        depth = 0 if relative == "." else relative.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        for name in dirs:
            found[os.path.normpath(os.path.join(relative, name)) + "/"] = None
        for name in files:
            try:
                found[os.path.normpath(os.path.join(relative, name))] = os.lstat(os.path.join(current, name)).st_mtime_ns
            except FileNotFoundError:
                continue
        if len(found) > max_entries:
            raise Refused("too many entries to snapshot under a user directory")
    return found


def compare_snapshots(before, after):
    return dict(added=sorted(set(after) - set(before)), removed=sorted(set(before) - set(after)),
                changed=sorted(k for k in set(before) & set(after) if before[k] != after[k]))


def classify_changes(diff):
    """`(unexpected, desktop_churn)` names; churn is the desktop app's own database and logs, reported not hidden."""
    names = diff["added"] + diff["removed"] + diff["changed"]
    churn = [name for name in names if DESKTOP_CHURN.search(name)]
    return [name for name in names if name not in churn], churn


def leftover_processes(ps_output, pgid, needles):
    """Pids from `ps -axo pid=,pgid=,command=` in the server's process group or naming a run path."""
    found = []
    for line in ps_output.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, group, command = int(parts[0]), parts[1], parts[2]
        if (pgid is not None and group == str(pgid)) or any(needle and needle in command for needle in needles):
            found.append(pid)
    return found


# ---------------------------------------------------------------- the project working tree (canary mode)

MAX_REPO_FILES = 5000


def _git(project, *args, run=subprocess.run):
    # No optional locks: a snapshot must never rewrite .git/index (git status refreshes it otherwise).
    done = run(["git", "-C", str(project), *args], capture_output=True, timeout=60,
               env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    return done.returncode, done.stdout


def repo_snapshot(project, run=subprocess.run):
    """HEAD, the paths `git status` reports (untracked files listed one by one) and a content hash of every file
    `git ls-files -m -o --exclude-standard` lists. Names and hashes only; nothing is read into the results."""
    code, head = _git(project, "rev-parse", "HEAD", run=run)
    code_status, status = _git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all", run=run)
    code_files, listed = _git(project, "ls-files", "-m", "-o", "--exclude-standard", "-z", run=run)
    if code_status or code_files:
        raise Refused("git cannot read the project working tree")
    entries, dirty = status.split(b"\0"), set()
    index = 0
    while index < len(entries):
        entry = entries[index].decode("utf-8", "surrogateescape")
        if len(entry) > 3:
            dirty.add(entry[3:])
            if entry[0] in "RC":
                index += 1  # a rename or copy is followed by its source path
        index += 1
    files = sorted({name.decode("utf-8", "surrogateescape") for name in listed.split(b"\0") if name} | dirty)
    if len(files) > MAX_REPO_FILES:
        raise Refused("too many changed files to snapshot the working tree")
    hashes = {}
    for name in files:
        path = Path(project) / name
        try:
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"
        except OSError:
            hashes[name] = "unreadable"
    return dict(head=head.decode().strip() if code == 0 else None, dirty=sorted(dirty), hashes=hashes,
                status_digest=hashlib.sha256(status).hexdigest())


def repo_changes(start, now):
    """Changes since `start`: `new` (paths not dirty at start: these abort the pilot), `dirty_changed` (paths that
    were already dirty, which other sessions may be editing: reported only) and whether HEAD moved."""
    paths = set(start["hashes"]) | set(now["hashes"]) | set(start["dirty"]) | set(now["dirty"])
    changed = {path for path in paths if start["hashes"].get(path) != now["hashes"].get(path)
               or (path in start["dirty"]) != (path in now["dirty"])}
    was_dirty = set(start["dirty"])
    return dict(new=sorted(changed - was_dirty), dirty_changed=sorted(changed & was_dirty),
                head_changed=start["head"] != now["head"])


# ---------------------------------------------------------------- abort diagnostics

HARNESS_DIR = Path(__file__).resolve().parent
MAX_SHAPE = 2048


def exception_origin(error):
    """Where an exception came from, sanitized: its type, the innermost harness frame (file name, line, function) and
    the innermost frame overall (file name and function). Never the message, arguments or locals."""
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    harness = [frame for frame in frames if Path(frame.filename).resolve().parent == HARNESS_DIR]
    pick, inner = (harness[-1] if harness else None), (frames[-1] if frames else None)
    return dict(type=type(error).__name__, file=Path(pick.filename).name if pick else None,
                line=pick.lineno if pick else None, function=pick.name if pick else None,
                innermost=None if inner is None else dict(file=Path(inner.filename).name, function=inner.name))


def shape_of(value, depth=0):
    """The structure of a payload without its content: field names and value types, lists as length plus the first
    three item shapes, bounded in depth and size. Strings (text, ids) are reduced to "str"."""
    if depth >= 4:
        return "..."
    if isinstance(value, dict):
        shape, hidden = {}, 0
        for key in sorted(value, key=str)[:24]:
            text = str(key)
            if ID_TOKEN.fullmatch(text) or re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):  # an id used as a key
                hidden += 1
                text = "<key#%d>" % hidden
            shape[text[:40]] = shape_of(value[key], depth + 1)
        if len(value) > 24:
            shape["..."] = len(value) - 24
    elif isinstance(value, list):
        shape = dict(list=len(value), items=[shape_of(item, depth + 1) for item in value[:3]])
    else:
        shape = "null" if value is None else type(value).__name__
    if depth == 0 and len(json.dumps(shape)) > MAX_SHAPE:
        return dict(truncated=True, type=type(value).__name__)
    return shape


# ---------------------------------------------------------------- output

class Redactor:
    """Sanitizes harness output: secrets and paths are removed, ids become stable placeholders (`<ses#1>`)."""

    def __init__(self, secrets=(), paths=(), names=()):
        self.secrets = [value for value in secrets if value]
        self.paths = sorted({str(value) for value in paths if value}, key=len, reverse=True)
        self.names = [value for value in names if value]
        self.ids = {}

    def add_secret(self, value):
        if value:
            self.secrets.append(value)

    def placeholder(self, value):
        prefix = value.split("_", 1)[0]
        if value not in self.ids:
            self.ids[value] = "<%s#%d>" % (prefix, 1 + sum(1 for known in self.ids if known.split("_", 1)[0] == prefix))
        return self.ids[value]

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "<secret>")
        for path in self.paths:
            value = value.replace(path, "<path>")
        value = ID_TOKEN.sub(lambda match: self.placeholder(match.group(0)), value)
        for name in self.names:
            value = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), "<user>", value)
        return value

    def __call__(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(key)): self(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        return value

    def leaks(self, text):
        """Fragments of `text` that should never appear in committed output."""
        found = [secret for secret in self.secrets if secret in text]
        found += [path for path in self.paths if path in text]
        found += [match.group(0) for match in ID_TOKEN.finditer(text)]
        found += [name for name in self.names if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), text)]
        return found


def id_shape(value):
    """The shape of an id without its value: prefix, body length, character classes and derived-shape match."""
    if type(value) is not str or "_" not in value:
        return dict(prefix=None, body_length=None, classes=[], derived_shape=False)
    prefix, body = value.split("_", 1)
    classes = [name for name, pattern in (("digit", r"[0-9]"), ("lower", r"[a-z]"), ("upper", r"[A-Z]"))
               if re.search(pattern, body)]
    derived = (DERIVED_SESSION if prefix == "ses" else DERIVED_MESSAGE).fullmatch(value) is not None
    return dict(prefix=prefix, body_length=len(body), classes=classes, lower_hex_only=bool(re.fullmatch(r"[0-9a-f]+", body)),
                derived_shape=derived)


def latency_pairs(records):
    """Shim latency per hook: each `qual-pre` record matched with the next `qual-post` record of the same hook and
    session (FIFO). Unmatched pre records were blocked by the shim (or the post guard never saw them)."""
    waiting, pairs, blocked = {}, [], []
    for record in records:
        key = (record.get("hook"), record.get("session"))
        if record.get("phase") == "pre":
            waiting.setdefault(key, []).append(record)
        elif record.get("phase") == "post" and waiting.get(key):
            before = waiting[key].pop(0)
            pairs.append(dict(hook=record["hook"], kind=record.get("kind"), ms=round(record["t"] - before["t"], 3)))
    for queue in waiting.values():
        blocked.extend(queue)
    return pairs, blocked


def summarize_ms(values):
    values = sorted(values)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]  # noqa: E731
    return dict(n=len(values), median_ms=round(pick(0.5), 3), p95_ms=round(pick(0.95), 3), max_ms=round(values[-1], 3))
