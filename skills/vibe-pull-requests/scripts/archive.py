#!/usr/bin/env python3
"""Move leftover paths out of a linked worktree into its main checkout's private archive, or refuse; nothing is overwritten."""
import argparse
import errno
import os
import stat
import sys
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if not os.path.isfile(os.path.join(ROOT, "runtime", "private_folder.py")):
    print("refused: the plugin's files are incomplete", file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, ROOT)
from runtime.private_folder import BASE, Refused, git, main_checkout, open_private_folder

ARCHIVES = "archives"
CAPABLE = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "geteuid") and os.listdir in os.supports_fd
           and {os.open, os.mkdir, os.lstat, os.rename} <= os.supports_dir_fd)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid arguments; give --worktree <dir>, --branch <name> and one or more --path <relative path>")


def _text(result):
    return os.fsdecode(result.stdout.removesuffix(b"\n"))


def _same(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _worktree(worktree):
    """The main checkout is the first record of the worktree's own repository. The worktree must be a linked
    worktree, named by its top folder, and not a link; the main checkout itself is refused."""
    try:
        if stat.S_ISLNK(os.lstat(worktree).st_mode):
            raise Refused("--worktree is a symbolic link")
        real = os.path.realpath(worktree, strict=True)
    except OSError as error:
        raise Refused("--worktree is not a folder") from error
    main = main_checkout(real)
    results = [git(real, "rev-parse", "--show-toplevel"),
               git(real, "rev-parse", "--path-format=absolute", "--git-dir"),
               git(real, "rev-parse", "--path-format=absolute", "--git-common-dir")]
    if any(result.returncode for result in results):
        raise Refused("--worktree is not a Git worktree")
    top, own_dir, own_common = map(_text, results)
    if not (_same(top, real) and not _same(own_dir, own_common)):
        raise Refused("--worktree is not a linked worktree")
    return real, main


def _branch(main, branch):
    checked = git(main, "check-ref-format", "--branch", branch)
    if checked.returncode or _text(checked) != branch:
        raise Refused(f"{branch!r} is not a valid branch name")


def _exists(name, folder):
    try:
        os.lstat(name, dir_fd=folder)
    except FileNotFoundError:
        return False
    return True


def _open_dir(parent, name, create=False):
    """Open one folder below parent without following a link; a missing folder is made first when asked."""
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError as error:
        raise Refused(f"{name} does not exist") from error
    except OSError as error:
        raise Refused(f"{name} is a symbolic link or not a folder") from error


def _run_folder(archives, branch, stamp, opened):
    """Make this run's folder named after the branch, with the next free suffix when that name is taken."""
    *parents, leaf = branch.split("/")
    parent = archives
    for part in parents:
        parent = _open_dir(parent, part, create=True)
        opened.append(parent)
    for suffix in ("", f"-{stamp}", *(f"-{stamp}-{number}" for number in range(2, 100))):
        try:
            os.mkdir(leaf + suffix, 0o700, dir_fd=parent)
        except FileExistsError:
            continue
        run = _open_dir(parent, leaf + suffix)
        opened.append(run)
        return os.path.join(*parents, leaf + suffix), parent, leaf + suffix, run
    raise Refused("no free archive folder name is left for this second")


def _source(path, root, worktree, git_entry, opened):
    """Open the folders above a path from the worktree and refuse any path that must not leave it.
    `git_entry` is the device and inode of the worktree's own `.git`: a case-folded name reaches it too."""
    parts = path.split("/")
    if os.path.isabs(path) or any(part in ("", ".", "..", ".git") for part in parts):
        raise Refused(f"{path} must be a relative path with no empty, '.', '..' or '.git' part")
    source = root
    for part in parts[:-1]:
        source = _open_dir(source, part)
        opened.append(source)
        if _is_git_entry(os.fstat(source), git_entry):
            raise Refused(f"{path} is the worktree's Git entry")
    try:
        status = os.lstat(parts[-1], dir_fd=source)
    except FileNotFoundError as error:
        raise Refused(f"{path} does not exist") from error
    if _is_git_entry(status, git_entry):
        raise Refused(f"{path} is the worktree's Git entry")
    listed = git(worktree, "--literal-pathspecs", "ls-files", "--", path)
    if listed.returncode:
        raise Refused("the worktree's index could not be read")
    if listed.stdout:
        raise Refused(f"{path} is tracked by Git")
    return source


def _is_git_entry(status, git_entry):
    return (status.st_dev, status.st_ino) == git_entry


def _move(path, source, run, run_path, main, opened):
    """Move one checked path into this run's folder with its layout kept. Every refusal comes before the move."""
    destination = os.path.join(run_path, path)
    if git(main, "check-ignore", "-q", "--", destination).returncode:
        raise Refused(f"Git does not ignore the archive destination for {path}")
    target = run
    for part in path.split("/")[:-1]:
        target = _open_dir(target, part, create=True)
        opened.append(target)
    leaf = path.rsplit("/", 1)[-1]
    if _exists(leaf, target):
        raise Refused(f"{destination} already exists; nothing was overwritten")
    try:
        os.rename(leaf, leaf, src_dir_fd=source, dst_dir_fd=target)
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise Refused(f"{path} is on another device; it was kept and nothing was copied") from error
        raise Refused(f"{path} could not be moved ({type(error).__name__})") from error
    return destination


def archive(worktree, branch, paths, moved):
    """Append each moved destination to `moved` as it goes, so a refusal still reports what already moved."""
    if not CAPABLE:
        raise Refused("this system cannot archive safely")
    real, main = _worktree(worktree)
    _branch(main, branch)
    entry = os.lstat(os.path.join(real, ".git"))
    git_entry = entry.st_dev, entry.st_ino
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder, opened = open_private_folder(main, ARCHIVES, "archives folder")
    run = None
    try:
        opened.append(os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        root = opened[-1]
        for path in paths:
            path = path[:-1] if path.endswith("/") else path
            fds = []
            try:
                source = _source(path, root, real, git_entry, fds)
                if run is None:
                    rel, parent, name, run = _run_folder(folder, branch, stamp, opened)
                    run_path = os.path.join(main, BASE, ARCHIVES, rel)
                moved.append(_move(path, source, run, run_path, main, fds))
            finally:
                for descriptor in fds:
                    os.close(descriptor)
    except BaseException:
        if run is not None and not moved:
            try:
                os.rmdir(name, dir_fd=parent)
            except OSError:
                pass
        raise
    finally:
        for descriptor in opened:
            os.close(descriptor)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--path", dest="paths", action="append", required=True)
    moved = []
    try:
        try:
            args = parser.parse_args(argv)
            archive(args.worktree, args.branch, args.paths, moved)
        except OSError as error:
            raise Refused(f"the archive could not be written ({type(error).__name__})") from error
    except Refused as refusal:
        for destination in moved:
            print(f"archived: {destination}", flush=True)
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    for destination in moved:
        print(destination, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
