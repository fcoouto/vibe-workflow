"""Where a Codex installation's local inputs sit on disk: the system configuration and installed package versions."""
import os
from pathlib import Path
import stat
import sys

from runtime import onboarding as d3


def system_layer_path(path):
    alias = Path('/etc')
    if sys.platform != 'darwin' or path != alias/'codex/config.toml' or not alias.is_symlink():
        return path
    # Codex reports the Unix system location even on macOS, where /etc is an OS alias.
    d3._require(alias.readlink() == Path('private/etc') and alias.lstat().st_uid == 0,
        'Unexpected macOS system configuration alias')
    directory = Path('/private/etc')
    for parent in (directory, directory.parent):
        info = parent.lstat()
        d3._require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0,
            'Unexpected macOS system configuration parent')
    return directory/'codex/config.toml'


def development_checkout(root):
    """A package root with its own .git outside any plugins/cache folder.

    Call only after the bundle scan: package_manifest.export_members fails unless Git confirms root as the
    top level of its checkout. A plugin cache copy stays an installation even when it carries a copied .git.
    """
    return os.path.lexists(root/'.git') and not any(p.name == 'cache' and p.parent.name == 'plugins' for p in root.parents)


def installation_versions(root, budget, children):
    """Folder names beside an installed copy (other installed versions); None for a development checkout.

    A checkout's siblings are unrelated worktrees. children is the caller's budgeted directory listing.
    """
    if development_checkout(root):
        return None
    return sorted(p.name for p in children(root.parent, budget) if p.is_dir())
