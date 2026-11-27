#!/usr/bin/env python3
"""List a worktree's leftover files by their stored names, fingerprint paths in it, check a worktree's identity,
or remove approved leftovers. `sort`, `expand`, `manifest` and `identity` only read; `discard` and `caches` remove."""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys

LISTED = (b"node_modules/", b"__pycache__/", b".pytest_cache/", b".mypy_cache/", b".ruff_cache/", b".DS_Store")
STATUS = ("status", "--porcelain=v1", "-z")
SORT = (*STATUS, "--ignored", "--untracked-files=normal")
EXPAND = (*STATUS, "--untracked-files=all")
EACH = (*STATUS, "--ignored", "--untracked-files=all")
ABSOLUTE = ("rev-parse", "--path-format=absolute")
BROKEN = "the worktree's Git pointer is broken"
USAGE = ("invalid arguments; give sort, expand, manifest, discard, caches or identity, then --worktree <dir> and "
         "what that mode takes: --path <relative path>, --digest <digest>, --primary <dir>, --branch <name>, --detached")
CAPABLE = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and hasattr(os, "fchdir")
           and os.listdir in os.supports_fd and os.stat in os.supports_follow_symlinks
           and {os.open, os.stat, os.readlink, os.unlink, os.rmdir} <= os.supports_dir_fd)
FOLDER = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


class Refused(Exception):
    """The answer cannot be trusted, or the removal must not go on."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(USAGE)


def _git(*args, at=None, codes=(0,)):
    """Run Git in the current folder, or in `at`, and return its exit code and output.
    It takes no optional lock, so it never rewrites the index; the Git variables this process inherited are dropped.
    Pathspecs are literal names. Anything Git writes to standard error makes the answer incomplete."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"}
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "--literal-pathspecs"]
    try:
        done = subprocess.run([*command, *(("-C", at) if at else ()), *args], env=env, capture_output=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("Git could not run") from error
    if done.returncode not in codes or done.stderr:
        raise Refused("Git could not read the worktree completely")
    return done.returncode, done.stdout


def _out(*args, at=None):
    return _git(*args, at=at)[1]


def _line(*args, at=None):
    return _out(*args, at=at).removesuffix(b"\n")


def _same(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _enter(worktree, linked=False):
    """Open the worktree's top folder without following a link and make it the current folder, so Git and every
    descriptor work on the same folder whatever the name points to later."""
    if not CAPABLE:
        raise Refused("this system cannot read a worktree safely")
    try:
        if stat.S_ISLNK(os.lstat(worktree).st_mode):
            raise Refused("--worktree is a symbolic link")
        top = os.open(worktree, FOLDER)
    except OSError as error:
        raise Refused("--worktree is not a folder") from error
    try:
        os.fchdir(top)
        if not os.path.samestat(os.stat(_line("rev-parse", "--show-toplevel")), os.fstat(top)):
            raise Refused("--worktree is not the worktree's top folder")
        if linked and _same(_line(*ABSOLUTE, "--git-dir"), _line(*ABSOLUTE, "--git-common-dir")):
            raise Refused("--worktree is not a linked worktree")
    except BaseException:
        os.close(top)
        raise
    return top


def _relative(path):
    """One stored name below the top folder, as its parts. One trailing slash is accepted, as Git prints a folder."""
    name = os.fsencode(path)
    name = name[:-1] if name.endswith(b"/") else name
    parts = name.split(b"/")
    if name.startswith(b"/") or any(part in (b"", b".", b"..") or part.lower() == b".git" for part in parts):
        raise Refused("each --path must be a relative path with no empty, '.', '..' or '.git' part")
    return parts


def _unsafe(name):
    """A control character or bytes that are not UTF-8: such a name cannot be typed into a command safely."""
    try:
        name.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(byte < 0x20 or byte == 0x7F for byte in name)


def _rebuildable(status, name):
    """Ignored, and the last path component, as Git printed it, is a listed name or a compiled Python file."""
    if status != "!!" or _unsafe(name):
        return False
    folder = name.endswith(b"/")
    last = name[:-1].rsplit(b"/", 1)[-1] + b"/" if folder else name.rsplit(b"/", 1)[-1]
    return last in LISTED or (not folder and last.endswith(b".pyc"))


def _fields(raw):
    """Each NUL-ended status entry as (status, name, old name). A rename or copy has one more field, its old name."""
    fields = iter(raw.split(b"\0"))
    entry = next(fields)
    for following in fields:
        if len(entry) < 4 or entry[2:3] != b" ":
            raise Refused("Git's listing could not be read")
        status, old = entry[:2].decode("ascii", "replace"), None
        if "R" in status or "C" in status:
            old, following = following, next(fields, None)
            if following is None:
                raise Refused("Git's listing is incomplete")
        yield status, entry[3:], old
        entry = following
    if entry != b"":
        raise Refused("Git's listing is incomplete")


def _entries(raw, only=None):
    """One row per status entry, with its group."""
    rows = []
    for status, name, old in _fields(raw):
        row = {"status": status, "path": os.fsdecode(name)}
        if old is not None:
            row["from"] = os.fsdecode(old)
        row["group"] = "rebuildable" if _rebuildable(status, name) else "real work"
        if _unsafe(name):
            row["unsafe"] = True
        if only is None or status == only:
            rows.append(row)
    return rows


class _Node:
    """One entry found below an approved path: its name in its folder, what it was, and its row."""

    def __init__(self, name, info, row):
        self.name, self.row, self.children = name, row, []
        self.folder = stat.S_ISDIR(info.st_mode)
        self.seen = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
        if not self.folder:
            self.seen += (info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def unchanged(self, info):
        seen = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
        return self.seen == (seen if self.folder else (*seen, info.st_size, info.st_mtime_ns, info.st_ctime_ns))

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def _open_folder(parent, name, node=None):
    """Open one folder below an open folder, never through a link, and check it is the entry that was seen."""
    try:
        descriptor = os.open(name, FOLDER, dir_fd=parent)
    except OSError as error:
        raise Refused(f"{os.fsdecode(name)!r} is a symbolic link, not a folder, or cannot be opened") from error
    if node is not None and not node.unchanged(os.fstat(descriptor)):
        os.close(descriptor)
        raise Refused(f"{os.fsdecode(name)!r} changed while it was read")
    return descriptor


def _parent(top, parts, opened):
    """Open every folder above a path from the top folder; a linked folder on the way is refused."""
    folder = top
    for part in parts[:-1]:
        folder = _open_folder(folder, part)
        opened.append(folder)
    return folder


def _scan(parent, name, shown, hashed):
    """Read one entry and everything below it through descriptors opened from its own folder."""
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    node = _Node(name, info, {"path": os.fsdecode(shown)})
    if node.folder:
        node.row["type"] = "folder"
        descriptor = _open_folder(parent, name, node)
        try:
            for child in sorted(os.fsencode(child) for child in os.listdir(descriptor)):
                node.children.append(_scan(descriptor, child, shown + b"/" + child, hashed))
        finally:
            os.close(descriptor)
    elif stat.S_ISLNK(info.st_mode):
        node.row["type"], node.row["target"] = "link", os.fsdecode(os.readlink(name, dir_fd=parent))
    elif stat.S_ISREG(info.st_mode):
        node.row["type"] = "file"
        if hashed:
            with open(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent), "rb") as file:
                if not node.unchanged(os.fstat(file.fileno())):
                    raise Refused(f"{os.fsdecode(shown)!r} changed while it was read")
                node.row["sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
                if not node.unchanged(os.fstat(file.fileno())):
                    raise Refused(f"{os.fsdecode(shown)!r} changed while it was read")
    else:
        node.row["type"] = "other"
    if _unsafe(name) or name.lower() == b".git":
        node.row["unsafe"] = True
    return node


def _statuses(shown):
    """What Git says about each file at and below a path: its status, and whether the index holds it."""
    exact, folders = {}, {}
    for status, name, _ in _fields(_out(*EACH, "--", shown)):
        if name.endswith(b"/"):
            folders[name] = status
        else:
            exact[name] = status
    tracked = {name for name in _out("ls-files", "-z", "--", shown).split(b"\0") if name}
    return exact, folders, tracked


def _inspect(top, parts, hashed=True):
    """One path's entries with their Git status, and the digest row that changes when any of it changes."""
    shown = b"/".join(parts)
    opened = []
    try:
        root = _scan(_parent(top, parts, opened), parts[-1], shown, hashed)
    finally:
        for descriptor in opened:
            os.close(descriptor)
    exact, folders, tracked = _statuses(shown)
    nodes = list(root.walk())
    for node in nodes:
        name = os.fsencode(node.row["path"])
        if node.folder:
            continue
        inside = [folder for folder in folders if name.startswith(folder)]
        status = exact.get(name) or (folders[max(inside, key=len)] if inside else None)
        if status is not None:
            node.row["status"] = status
        if name in tracked:
            node.row["tracked"] = True
    lines = [json.dumps(node.row, sort_keys=True) for node in nodes]
    summary = {"digest": hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest(), "path": os.fsdecode(shown),
               "entries": len(lines), "statuses": sorted({node.row["status"] for node in nodes if "status" in node.row}),
               "tracked": len(tracked)}
    if any(node.row.get("unsafe") for node in nodes):
        summary["unsafe"] = True
    return root, lines, summary


def _delete(parent, node):
    """Remove one scanned entry through its folder's descriptor. Anything that is not what was scanned stops it."""
    if not node.unchanged(os.stat(node.name, dir_fd=parent, follow_symlinks=False)):
        raise Refused(f"{node.row['path']!r} changed before it was removed")
    if not node.folder:
        os.unlink(node.name, dir_fd=parent)
        return
    descriptor = _open_folder(parent, node.name, node)
    try:
        if {os.fsencode(name) for name in os.listdir(descriptor)} != {child.name for child in node.children}:
            raise Refused(f"{node.row['path']!r} changed before it was removed")
        for child in node.children:
            _delete(descriptor, child)
    finally:
        os.close(descriptor)
    os.rmdir(node.name, dir_fd=parent)


def _remove(top, parts, root):
    opened = []
    try:
        _delete(_parent(top, parts, opened), root)
    finally:
        for descriptor in opened:
            os.close(descriptor)


def _discard(top, parts, digest):
    """Check one approved path again, then remove it: same digest, nothing tracked or staged, nothing unsafe."""
    root, _, summary = _inspect(top, parts)
    path = summary["path"]
    if summary["digest"] != digest:
        raise Refused(f"{path!r} is not what was approved: its digest changed")
    if summary["tracked"] or summary.get("unsafe"):
        raise Refused(f"{path!r} holds a path Git tracks or stages, a repository, or a name that cannot be typed")
    if any(node.row.get("status") not in ("??", "!!") for node in root.walk() if node.row["type"] in ("file", "link")):
        raise Refused(f"{path!r} holds a file that is neither untracked nor ignored")
    _remove(top, parts, root)


def _cache(top, name):
    """Check one rebuildable row again, then remove it: still the same ignored entry, with nothing tracked in it."""
    parts = _relative(os.fsdecode(name))
    shown = b"/".join(parts)
    if list(_fields(_out(*SORT, "--", shown))) != [("!!", name, None)] or _out("ls-files", "-z", "--", shown):
        raise Refused(f"{os.fsdecode(name)!r} is no longer one ignored entry")
    opened = []
    try:
        root = _scan(_parent(top, parts, opened), parts[-1], shown, hashed=False)
    finally:
        for descriptor in opened:
            os.close(descriptor)
    if root.folder != name.endswith(b"/"):
        raise Refused(f"{os.fsdecode(name)!r} changed before it was removed")
    _remove(top, parts, root)


def _records(primary):
    """The worktrees a repository registers: path, branch, locked. The first one is its main checkout."""
    records = []
    for field in _out("worktree", "list", "--porcelain", "-z", at=primary).split(b"\0"):
        if field.startswith(b"worktree "):
            records.append({"path": os.fsdecode(field[len(b"worktree "):]), "branch": None, "locked": False})
        elif records and field.startswith(b"branch refs/heads/"):
            records[-1]["branch"] = os.fsdecode(field[len(b"branch refs/heads/"):])
        elif records and (field == b"locked" or field.startswith(b"locked ")):
            records[-1]["locked"] = True
    return records


def identity(worktree, primary, branch):
    """The named folder is the worktree this repository registers under that name, on that branch, and nothing else."""
    if not os.path.isabs(worktree) or os.path.realpath(worktree) != worktree:
        raise Refused("--worktree must be an absolute path that is not, and does not pass through, a symbolic link")
    primary = os.path.abspath(primary)
    records = _records(primary)
    if not records or not _same(records[0]["path"], primary):
        raise Refused("--primary is not the repository's main checkout")
    mine = [record for record in records[1:] if record["path"] == worktree]
    if len(mine) != 1 or mine[0]["locked"] or mine[0]["branch"] != branch:
        raise Refused("the repository does not register this path as an unlocked worktree on that branch")
    if any(record is not mine[0] and os.path.realpath(record["path"]) == worktree for record in records):
        raise Refused("another registered worktree resolves to this path")
    pointer = os.path.join(worktree, ".git")
    try:
        if not stat.S_ISREG(os.lstat(pointer).st_mode):
            raise Refused("the path's .git is not a worktree pointer file")
    except OSError as error:
        raise Refused("the path is not a worktree folder") from error
    top = None
    try:
        try:
            top = _enter(worktree)
            git_dir, common = _line(*ABSOLUTE, "--git-dir"), _line(*ABSOLUTE, "--git-common-dir")
        except Refused as refusal:
            raise Refused(BROKEN) from refusal
        if not _same(common, _line(*ABSOLUTE, "--git-common-dir", at=primary)):
            raise Refused("the path belongs to another repository")
        try:
            with open(os.path.join(git_dir, b"gitdir"), "rb") as file:
                backlink = os.fsdecode(file.read().removesuffix(b"\n"))
        except OSError as error:
            raise Refused("the worktree's backlink could not be read") from error
        if not _same(os.path.dirname(git_dir), os.path.join(common, b"worktrees")) or backlink != pointer:
            raise Refused("the repository's backlink does not point at this path")
        code, head = _git("symbolic-ref", "--short", "-q", "HEAD", codes=(0, 1))
        if (os.fsdecode(head.removesuffix(b"\n")) if code == 0 else None) != branch:
            raise Refused("the worktree is not on that branch")
        return {"worktree": worktree, "branch": branch, "head": os.fsdecode(_line("rev-parse", "HEAD"))}
    finally:
        if top is not None:
            os.close(top)


def run(args, done):
    """Do one mode. Read modes return every line at once; removing modes report each removed path as they go."""
    mode, paths = args.mode, [_relative(path) for path in args.paths]
    takes = {"sort": (), "expand": ("paths",), "manifest": ("paths",), "discard": ("paths", "digests"), "caches": (),
             "identity": ("primary", "branch", "detached")}[mode]
    given = {"paths": args.paths, "digests": args.digests, "primary": args.primary, "branch": args.branch,
             "detached": args.detached}
    if any(value and name not in takes for name, value in given.items()):
        raise Refused(USAGE)
    if mode == "identity":
        if not args.primary or bool(args.branch) == args.detached:
            raise Refused(USAGE)
        return [json.dumps(identity(args.worktree, args.primary, args.branch))]
    if (mode in ("manifest", "discard") and not paths) or (mode == "discard" and len(paths) != len(args.digests)):
        raise Refused(USAGE)
    top = _enter(args.worktree, linked=mode in ("discard", "caches"))
    try:
        if mode == "sort":
            return [json.dumps(row) for row in _entries(_out(*SORT))]
        if mode == "expand":
            return [json.dumps(row) for row in _entries(_out(*EXPAND, "--", *(b"/".join(path) for path in paths)), only="??")]
        if mode == "manifest":
            lines = []
            for path in paths:
                _, rows, summary = _inspect(top, path)
                lines += [*rows, json.dumps(summary)]
            return lines
        if mode == "discard":
            for path, digest in zip(paths, args.digests):
                _discard(top, path, digest)
                done(os.fsdecode(b"/".join(path)))
            return []
        for status, name, _ in list(_fields(_out(*SORT))):
            if _rebuildable(status, name):
                _cache(top, name)
                done(os.fsdecode(name))
        return []
    finally:
        os.close(top)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("mode", choices=("sort", "expand", "manifest", "discard", "caches", "identity"))
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--path", dest="paths", action="append", default=[])
    parser.add_argument("--digest", dest="digests", action="append", default=[])
    parser.add_argument("--primary")
    parser.add_argument("--branch")
    parser.add_argument("--detached", action="store_true")
    try:
        try:
            lines = run(parser.parse_args(argv), lambda path: print(json.dumps({"discarded": path}), flush=True))
        except (OSError, RecursionError) as error:
            raise Refused(f"a path could not be read or removed ({type(error).__name__})") from error
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
