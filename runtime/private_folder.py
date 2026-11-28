"""Private folders under the main checkout's settings folder, opened only when every existing piece is safe."""
import os
import stat
import subprocess
import unicodedata

BASE = ".vibe-workflow"
IGNORE = b"*\n"


class Refused(Exception):
    pass


def git(directory, *args):
    """Run Git for one checkout, without the Git variables this process inherited. Output stays bytes:
    a path may hold any byte but NUL, and text decoding would turn a carriage return into a newline."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", directory, *args], env={**env, "LC_ALL": "C"},
                              capture_output=True, timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("Git could not run") from error


def main_checkout(cwd):
    """The main checkout is the first worktree record; a bare or unresolved one has no place for reports."""
    listed = git(cwd, "worktree", "list", "--porcelain", "-z")
    if listed.returncode:
        if b"not a git repository" in listed.stderr:
            raise Refused("not a Git repository")
        raise Refused("the main checkout could not be resolved")
    path, *attributes = listed.stdout.split(b"\0\0")[0].split(b"\0")
    if not path.startswith(b"worktree ") or b"bare" in attributes:
        raise Refused("the repository has no main checkout")
    try:
        main = os.path.realpath(os.fsdecode(path[len(b"worktree "):]), strict=True)
        top = git(main, "rev-parse", "--show-toplevel")
        # Git ends the line with one newline; nothing else is stripped from the name.
        if top.returncode or not os.path.samefile(os.fsdecode(top.stdout.removesuffix(b"\n")), main):
            raise Refused("the main checkout could not be resolved")
    except OSError as error:
        raise Refused("the main checkout could not be resolved") from error
    return main


def _kind(path):
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None


def _trusted(descriptor, name):
    """Whoever can write a folder can rename what is inside it, so it must belong to this user and not be
    open to everyone. A group-writable folder passes: that is what umask 002 gives, and the group is the
    user's own choice. The modes are never changed to make this pass."""
    status = os.fstat(descriptor)
    if status.st_uid != os.geteuid():
        raise Refused(f"{name} is owned by another user")
    if status.st_mode & 0o002:
        raise Refused(f"{name} is writable by everyone")


def _open_folder(name, parent):
    """Create the folder when missing, then open it without following a link. Also says whether this run made it."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        made = True
    except FileExistsError:
        made = False
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent), made
    except OSError as error:
        raise Refused(f"{name} is a symlink or not a directory") from error


def _listed(name, parent, shown):
    """A name counts only when its parent lists it byte for byte. On a case-insensitive volume another spelling
    reaches the same folder, and Git, asked about the exact name, does not see the files it tracks there."""
    if name not in os.listdir(parent):
        raise Refused(f"{shown} is not stored under exactly that name, so it was left untouched")


def _folded(name):
    """A name as a case-insensitive volume compares it: decomposed, then without letter case."""
    return unicodedata.normalize("NFD", os.fsdecode(name)).casefold()


def _tracks(main, path):
    """Whether the index holds a path at or below `path` under any spelling a case-insensitive volume would
    take for it. Asked by the exact name, Git does not list a file it tracks under another letter case, yet
    after a rename on disk that file is the one in the folder. An index that cannot be read counts as tracking."""
    listed = git(main, "ls-files", "-z")
    folder = _folded(path)
    return bool(listed.returncode) or any(name == folder or name.startswith(folder + "/")
                                          for name in map(_folded, listed.stdout.split(b"\0")))


def _shared(label):
    return f"the {label} already exists under a group-writable {BASE}, so it is not repaired"


def _ensure_ignore(folder, label, replace=True, create=True):
    """The folder's own .gitignore must be the single line `*`. A differing regular file is replaced, never edited.
    Without `replace` or `create` the file is only checked: what would need that change is refused instead."""
    try:
        mode = os.lstat(".gitignore", dir_fd=folder).st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if not stat.S_ISREG(mode):
            raise Refused(f"the {label}'s .gitignore is a symlink or not a regular file")
        current = os.open(".gitignore", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        with os.fdopen(current, "rb") as file:
            if file.read(len(IGNORE) + 1) == IGNORE:
                return
    if not (replace if mode is not None else create):
        raise Refused(f"{_shared(label)}; its .gitignore is not exactly `*`, and it was left untouched")
    temporary = f".gitignore.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=folder)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(IGNORE)
        os.replace(temporary, ".gitignore", src_dir_fd=folder, dst_dir_fd=folder)
    except BaseException:
        os.unlink(temporary, dir_fd=folder)
        raise


def open_private_folder(main, name, label):
    """Open `.vibe-workflow/<name>` under the main checkout and return its descriptor with every descriptor the
    caller must close. Nothing is created until every existing piece has been looked at; `label` names the folder
    in refusals."""
    path = f"{BASE}/{name}"
    for item in (BASE, path):
        kind = _kind(os.path.join(main, item))
        if kind is not None and stat.S_ISLNK(kind):
            raise Refused(f"{item} is a symlink")
        if kind is not None and not stat.S_ISDIR(kind):
            raise Refused(f"{item} is not a directory")
    if _tracks(main, path):
        raise Refused(f"the main checkout tracks files in the {label}, or its index could not be read")
    opened = []
    try:
        opened.append(os.open(main, os.O_RDONLY | os.O_DIRECTORY))
        _trusted(opened[-1], "the main checkout")
        opened.append(_open_folder(BASE, opened[-1])[0])
        _listed(BASE, opened[-2], BASE)
        _trusted(opened[-1], BASE)
        shared = bool(os.fstat(opened[-1]).st_mode & 0o020)
        folder, made = _open_folder(name, opened[-1])
        opened.append(folder)
        _listed(name, opened[-2], f"the {label}")
        found = os.fstat(folder)
        if found.st_uid != os.geteuid():
            raise Refused(f"the {label} is owned by another user")
        # `made` says a folder was created at that name, not that the open returned it. A folder this run
        # created is empty; one with anything in it took its place in between and is not this run's to set up.
        if made and os.listdir(folder):
            raise Refused(f"the folder opened as the new {label} is not empty, so it is not the one "
                          "this run created; it was left untouched")
        if shared:
            # A group member can rename another of this user's folders into this place. Owning the folder is
            # then no proof that it is the private folder, so nothing in it is changed to make it fit: it must
            # already be private, and only a folder made in this run gets its .gitignore written.
            if found.st_mode & 0o777 != 0o700:
                raise Refused(f"{_shared(label)}; its mode is not 0700, and it was left untouched")
            _ensure_ignore(folder, label, replace=False, create=made)
        else:
            os.fchmod(folder, 0o700)
            _ensure_ignore(folder, label)
        return folder, opened
    except BaseException:
        for descriptor in opened:
            os.close(descriptor)
        raise
