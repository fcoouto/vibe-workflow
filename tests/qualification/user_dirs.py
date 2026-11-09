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


def _data_ok(name, segments, kind, session, named):
    store, nested = segments[0], segments[1:]
    if not nested:  # directly in the data directory: the database files and recognized store directories only
        if name.endswith("/"):
            return store in DATA_STORES and kind == "added"  # a store created during the run is counted
        return bool(DATA_DB.fullmatch(store)) and not (kind == "removed" and store == "opencode.db")
    if store not in DATA_STORES or named:
        return False
    if kind == "removed" and store == "snapshot" and len(nested) == 1 and name.endswith("/"):
        return False  # a whole snapshot tree deleted
    if store in ("shell", "tool-output"):
        return name in session
    return True


def classify_user_dir(policy, before, after, root, own_ids=()):
    """`(unexpected names, {store: count})` for one user OpenCode directory under its policy (see the module doc)."""
    diff = compare_snapshots(before, after)
    if policy == "strict":
        unexpected, churn = classify_changes(diff)
        unexpected = [OWN_ID_NAME if _own(n, own_ids) and n in diff["added"] else n for n in unexpected]
        return unexpected, ({"desktop": len(churn)} if churn else {})
    session = set(session_output(diff, before, after, root))  # also files of a shell/ or tool-output/ created now
    unexpected, churn = [], {}
    for kind in ("added", "changed", "removed"):
        for name in diff[kind]:
            segments = (name[:-1] if name.endswith("/") else name).split("/")
            linked = any(str(side.get(name, "")).startswith(SYMLINK) for side in (before, after))
            bad = any(s in ("", ".", "..") for s in segments) or linked
            if kind == "added" and _own(name, own_ids):
                unexpected.append(OWN_ID_NAME)
                continue
            named = _named(policy, segments, name.endswith("/"))
            if policy == "data":
                ok = not bad and _data_ok(name, segments, kind, session, named)
                key = segments[0] if len(segments) > 1 or name.endswith("/") else "db"
            else:
                ok, key = not bad and not named, policy
            if ok:
                churn[key] = churn.get(key, 0) + 1
            else:
                unexpected.append(name)
    return sorted(unexpected), churn


def _own(name, own_ids):
    return any(own and own in name for own in own_ids)


def user_dir_entry(name, before, after, root, own_ids=()):
    """The results entry for one user OpenCode directory: masked unexpected names (at most 50; names carrying this
    run's ids are replaced), counts of concurrent-session churn by store (never file names)."""
    unexpected, churn = classify_user_dir(USER_DIR_POLICY.get(name, "strict"), before, after, root, own_ids)
    return dict(unexpected=[sanitize.text(n) for n in unexpected][:50], unexpected_count=len(unexpected),
                concurrent_session_churn=churn, desktop_churn_count=churn.get("desktop", 0),
                session_output_count=churn.get("shell", 0) + churn.get("tool-output", 0)), unexpected


def isolated_store(base):
    """Whether the isolated server wrote its own store where XDG pinning sends it (`<isolated>/data/opencode`). It
    proves the server used its pinned store; it does not prove that nothing reached the user's real stores."""
    data = Path(base) / "data" / "opencode"
    return dict(data_dir=data.is_dir(), session_store=(data / "opencode.db").is_file() or (data / "storage").is_dir())
