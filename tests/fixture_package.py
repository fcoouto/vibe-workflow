"""Copy a checkout's export membership from its working tree, as scripts/package.py would export it."""
from pathlib import Path
import shutil

from runtime.package_manifest import excluded, export_members


def copy_package(source, target, *ignored):
    """Copy only tracked paths minus development-only ones, so untracked files never reach validation."""
    members = export_members(source)

    def ignore(directory, names):
        relative = Path(directory).relative_to(source)
        return {name for name in names if name in (".git", "__pycache__", *ignored)
                or (members is not None and (relative / name).as_posix() not in members)
                or excluded((relative / name).as_posix()) or excluded((relative / name).as_posix() + "/")}
    shutil.copytree(source, target, ignore=ignore)
