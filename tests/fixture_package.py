"""Copy a checkout's working tree without the paths scripts/package.py never exports."""
from pathlib import Path
import shutil

from runtime.package_manifest import excluded


def copy_package(source, target, *ignored):
    """Validation rejects a package directory that still holds development-only paths such as tests/."""
    def ignore(directory, names):
        relative = Path(directory).relative_to(source)
        return {name for name in names if name in (".git", "__pycache__", *ignored)
                or excluded((relative / name).as_posix()) or excluded((relative / name).as_posix() + "/")}
    shutil.copytree(source, target, ignore=ignore)
