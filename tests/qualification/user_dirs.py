"""How the user's real OpenCode directories are judged after a live run (names and mtimes only, never contents).

Other running OpenCode sessions (the coordinator's own, the user's) write to these directories during a run, and a
names-and-mtimes check cannot tell their writes from the isolated server's. So concurrent-session stores are counted
(`concurrent_session_churn`), while credential and config names, deletions of the database, a store or a snapshot,
anything else directly in the data directory, and NEW names that carry this run's own session or message ids fail the
run. The user's real `opencode.db` is never opened: a write by the isolated server into it would not be visible here,
which is why isolation also rests on XDG pinning and the isolated server's own store (`isolated_store`).
"""
import re
from pathlib import Path

from tests.qualification import sanitize
from tests.qualification.safety import SYMLINK, classify_changes, compare_snapshots, session_output

USER_DIR_POLICY = {".config/opencode": "strict", ".local/share/opencode": "data", ".cache/opencode": "cache",
                   ".local/state/opencode": "state"}
DATA_STORES = ("log", "snapshot", "worktree", "repos", "storage", "shell", "tool-output")  # concurrent-session stores
DATA_DB = re.compile(r"opencode\.db(?:-wal|-shm|-journal)?")
# Credential and config words, matched against whole parts of a name split on `-`, `_` and `.` (so `author` and
# `monkey` are not `auth` and `key`).
STRICT_WORDS = dict(
    data={"auth", "token", "tokens", "credential", "credentials", "config", "settings", "keychain", "secret", "secrets",
          "key", "keys", "apikey", "pem", "env"},
    cache={"auth", "token", "tokens", "credential", "credentials", "keychain", "secret", "secrets", "key", "keys",
           "apikey", "pem", "env"},
    state={"auth", "token", "tokens", "credential", "credentials", "config", "settings", "keychain", "secret", "secrets",
           "key", "keys", "apikey", "pem", "env"})
# Inside repository checkouts and package trees ordinary names (`config.ts`, `js-tokens`) are content, so only these
# exact sensitive files fail there.
CONTENT_TREES = ("worktree", "repos", "node_modules", "npm")
SENSITIVE_FILE = re.compile(r"(?:mcp-)?auth\.json|\.env(?:\.[\w.-]+)?|[^/]*\.pem|[^/]*\.key", re.I)
OWN_ID_NAME = "<a new name containing this run's own session or message id>"


def _strict_word(policy, segment):
    return bool(set(re.split(r"[-_.]", segment.lower())) & STRICT_WORDS[policy])


def _named(policy, segments, is_dir):
    """Whether a name is credential- or config-like: in content trees only exact sensitive files; elsewhere any part."""
    if any(segment in CONTENT_TREES for segment in segments):
        return not is_dir and bool(SENSITIVE_FILE.fullmatch(segments[-1]))
    return any(_strict_word(policy, segment) for segment in segments)


PRUNED_STORES = ("shell", "tool-output", "log", "snapshot")  # OpenCode's own retention deletes old entries there


def _data_check(name, segments, kind, session, named):
    """`(ok, churn key, reason code)` for one changed name in the data directory."""
    store, nested = segments[0], segments[1:]
    if not nested:  # directly in the data directory: the database files and recognized store directories only
        if name.endswith("/"):
            if store not in DATA_STORES:
                return False, None, "outside_stores"
            return (True, store, None) if kind == "added" else (False, None, "deleted_store")  # created: counted
        if not DATA_DB.fullmatch(store):
            return False, None, "outside_stores"
        return (False, None, "deleted_db") if kind == "removed" and store == "opencode.db" else (True, "db", None)
    if store not in DATA_STORES:
        return False, None, "outside_stores"
    if named:
        return False, None, "sensitive_name"
    if kind == "removed":
        if store == "snapshot" and len(nested) == 1 and name.endswith("/"):
            return False, None, "deleted_snapshot_tree"
        return True, "pruned" if store in PRUNED_STORES else store, None
    if store in ("shell", "tool-output") and name not in session:
        return False, None, "not_plain_single_link"
    return True, store, None


def classify_user_dir(policy, before, after, root, own_ids=(), reasons=None):
    """`(unexpected names, {store: count})` for one user OpenCode directory under its policy (see the module doc);
    `reasons` (a dict) receives a count per reason code of what failed."""
    reasons = {} if reasons is None else reasons

    def fail(name, reason):
        reasons[reason] = reasons.get(reason, 0) + 1
        unexpected.append(name)
    diff = compare_snapshots(before, after)
    if policy == "strict":
        unexpected, churn = classify_changes(diff)
        unexpected = [OWN_ID_NAME if _own(n, own_ids) and n in diff["added"] else n for n in unexpected]
        if unexpected:
            reasons["strict_dir"] = len(unexpected)
        return unexpected, ({"desktop": len(churn)} if churn else {})
    session = set(session_output(diff, before, after, root))  # also files of a shell/ or tool-output/ created now
    unexpected, churn = [], {}
    for kind in ("added", "changed", "removed"):
        for name in diff[kind]:
            segments = (name[:-1] if name.endswith("/") else name).split("/")
            linked = any(str(side.get(name, "")).startswith(SYMLINK) for side in (before, after))
            bad = any(s in ("", ".", "..") for s in segments) or linked
            if kind == "added" and _own(name, own_ids):
                fail(OWN_ID_NAME, "own_id")
                continue
            if bad:
                fail(name, "symlink_or_dot_segment")
                continue
            named = _named(policy, segments, name.endswith("/"))
            if policy == "data":
                ok, key, reason = _data_check(name, segments, kind, session, named)
            else:
                ok, key, reason = not named, policy, "sensitive_name"
            if ok:
                churn[key] = churn.get(key, 0) + 1
            else:
                fail(name, reason)
    return sorted(unexpected), churn


def _own(name, own_ids):
    return any(own and own in name for own in own_ids)


def user_dir_entry(name, before, after, root, own_ids=()):
    """The results entry for one user OpenCode directory: masked unexpected names (at most 50; names carrying this
    run's ids are replaced), counts of concurrent-session churn by store (never file names)."""
    reasons = {}
    unexpected, churn = classify_user_dir(USER_DIR_POLICY.get(name, "strict"), before, after, root, own_ids, reasons)
    return dict(unexpected=[sanitize.text(n) for n in unexpected][:50], unexpected_count=len(unexpected),
                unexpected_reasons=reasons,
                concurrent_session_churn=churn, desktop_churn_count=churn.get("desktop", 0),
                session_output_count=churn.get("shell", 0) + churn.get("tool-output", 0)), unexpected


def isolated_store(base):
    """Whether the isolated server wrote its own store where XDG pinning sends it (`<isolated>/data/opencode`). It
    proves the server used its pinned store; it does not prove that nothing reached the user's real stores."""
    data = Path(base) / "data" / "opencode"
    return dict(data_dir=data.is_dir(), session_store=(data / "opencode.db").is_file() or (data / "storage").is_dir())


# ---------------------------------------------------------------- whole-run snapshots (opencode_live.py, live_modes.py)

REPORT_ONLY_HOME = (".claude", ".opencode")  # written by the claude CLI / OpenCode under HOME: reported, not compared


def snapshot(home):
    """The user's OpenCode directories (names and mtimes) and the watched OpenAI/Codex login directories."""
    from tests.qualification import safety
    return dict({name: safety.snapshot_tree(Path(home) / name) for name in safety.USER_OPENCODE_DIRS},
                watched=safety.watched_home(home))


def report_only(home):
    from tests.qualification import safety
    found = {}
    for name in REPORT_ONLY_HOME:
        try:
            found[name] = safety.snapshot_tree(Path(home) / name, max_depth=2)
        except safety.Refused:
            found[name] = None
    claude_json = Path(home) / ".claude.json"
    found[".claude.json"] = {"": claude_json.stat().st_mtime_ns} if claude_json.exists() else {}
    return found


def compare(home, before, home_before, blockers, own_ids=(), after=None, home_after=None):
    """The results' `user_dirs` entry; unexpected changes append to `blockers` (concurrent sessions' churn is counted;
    credentials and config fail)."""
    from tests.qualification import safety
    after = snapshot(home) if after is None else after
    changes = {}
    for name in safety.USER_OPENCODE_DIRS:
        changes[name], unexpected = user_dir_entry(name, before[name], after[name], Path(home) / name, own_ids)
        if unexpected:
            blockers.append("the user's %s changed during the run" % name)
    codex, unexpected = safety.watched_changes(before["watched"], after["watched"])
    if unexpected:
        blockers.append("the user's ~/.codex credentials or config changed during the run")
    home_after = report_only(home) if home_after is None else home_after
    report = {}
    for name, value in home_before.items():
        report[name] = "not snapshotted (too many entries)" if value is None or home_after.get(name) is None else \
            {key: len(items) for key, items in compare_snapshots(value, home_after[name]).items()}
    return dict(opencode=changes, codex=codex, home_report_only=report)
