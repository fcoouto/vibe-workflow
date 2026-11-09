#!/usr/bin/env python3
"""Export this checkout's tracked files, minus development-only trees, as a clean local plugin package; no network."""

import argparse
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = Path("~/.cache/vibe-workflow/dev-package")
RELEASE_OUTPUT = Path("~/.cache/vibe-workflow/release-package")
VERSION = re.compile(r"\d+\.\d+\.\d+")
# Development-only trees: nothing installed reads them, and Codex's bundle scan allows 8 MiB in total.
# Local client configuration stays with the checkout.
EXCLUDED = ("tests/", "scripts/test_sharded.py", ".vibe-workflow/", ".codex/", "CLAUDE.md")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def manifest(root):
    return read_json(root / ".codex-plugin/plugin.json")


# Inherited repository variables (for example from a Git hook) would override -C.
GIT_ENVIRONMENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def git(root, *args, index=None):
    env = {key: value for key, value in os.environ.items() if key not in GIT_ENVIRONMENT}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env).stdout


def resolve_commit(root, ref):
    try:
        return git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError:
        raise ValueError(f"Unknown release parent: {ref}") from None


def tracked_changes(root):
    return bool(git(root, "status", "--porcelain", "--untracked-files=no"))


def tracked_files(root):
    # Unmerged files appear once per conflict stage.
    names = dict.fromkeys(name for name in git(root, "ls-files", "-z").decode("utf-8").split("\0") if name)
    files = []
    for name in names:
        if name.startswith(EXCLUDED):
            continue
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Tracked symlinks cannot be packaged: {name}")
        if path.is_file():
            files.append(name)
        elif path.exists():
            raise ValueError(f"Tracked entry is not a regular file: {name}")
    return files


def committed_files(root, commit, staging):
    """Write commit's tracked files, minus EXCLUDED, into staging; the working tree is not read."""
    excludes = [f":(exclude){item.rstrip('/')}" for item in EXCLUDED]
    files = []
    with tarfile.open(fileobj=io.BytesIO(git(root, "archive", "--format=tar", commit, "--", ".", *excludes))) as archive:
        for member in archive:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe path in release archive: {member.name}")
            if member.isdir():
                (staging / path).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                (staging / path).parent.mkdir(parents=True, exist_ok=True)
                (staging / path).write_bytes(archive.extractfile(member).read())
                (staging / path).chmod(0o755 if member.mode & 0o111 else 0o644)
                files.append(path.as_posix())
            elif member.type != tarfile.XGLTYPE:
                raise ValueError(f"Tracked symlinks cannot be packaged: {member.name}")
    return files


def marker(output):
    return output.parent / f".{output.name}.build"


def is_build(output, name):
    # A manifest alone also matches checkouts and client caches; only builds have the marker.
    try:
        recorded = marker(output).read_text(encoding="utf-8")
        plugin = output / "plugins" / name if (output / "plugins").is_dir() else output
        return (recorded == str(output.resolve()) and not (output / ".git").exists()
                and read_json(plugin / ".codex-plugin/plugin.json").get("name") == name)
    except (OSError, ValueError, AttributeError):
        return False


def release_layout(staging, name):
    """Move the plugin below plugins/<name> so a clone's .git stays outside the installed copy."""
    plugin = staging / "plugins" / name
    plugin.mkdir(parents=True)
    for item in list(staging.iterdir()):
        if item.name != "plugins":
            item.rename(plugin / item.name)
    source = f"./plugins/{name}"
    codex = read_json(plugin / ".agents/plugins/marketplace.json")
    claude = read_json(plugin / ".claude-plugin/marketplace.json")
    # Any other entry would keep "./" and install the whole release root.
    if [entry["name"] for entry in codex["plugins"]] != [name] or [entry["name"] for entry in claude["plugins"]] != [name]:
        raise ValueError(f"Each marketplace must list only {name}")
    codex["plugins"][0]["source"]["path"] = source
    claude["plugins"][0]["source"] = source
    for path, value in ((".agents/plugins/marketplace.json", codex), (".claude-plugin/marketplace.json", claude)):
        (staging / path).parent.mkdir(parents=True, exist_ok=True)
        (staging / path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(plugin / "LICENSE", staging / "LICENSE")
    return plugin


def check_output(root, output, name):
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    resolved = output.resolve()
    if resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError("Output must be outside the checkout")
    if output.exists() and not (output.is_dir() and (not any(output.iterdir()) or is_build(output, name))):
        raise ValueError("Output exists and is not an earlier package build")
    return resolved


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def build(root, output, release=False, commit_onto=None):
    """Replace output with a validated copy of root's tracked files; remove older builds.

    A release copies the committed tree of HEAD, pinned once, and can write it as one commit on commit_onto.
    """
    from scripts.validate import validate

    root = root.resolve(strict=True)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    if release:
        if tracked_changes(root):
            raise ValueError("A release needs a clean checkout; commit or stash tracked changes")
        details = json.loads(git(root, "show", f"{head}:.codex-plugin/plugin.json"))
        if commit_onto:
            resolve_commit(root, commit_onto)
    else:
        details = manifest(root)
    name = details["name"]
    output = Path(output).expanduser().absolute()
    check_output(root, output, name)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = os.open(output.parent / f".{output.name}.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        output = check_output(root, output, name)
        removed = 0
        for leftover in output.parent.glob(f".{output.name}.*-*"):
            if leftover.name.startswith((f".{output.name}.tmp-", f".{output.name}.old-")):
                remove(leftover)
                removed += 1
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        try:
            if release:
                files = committed_files(root, head, staging)
            else:
                files = tracked_files(root)
                for relative in files:
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / relative, target)
            report = validate(staging)
            if release:
                release_layout(staging, name)
            if output.exists():
                previous = Path(tempfile.mkdtemp(prefix=f".{output.name}.old-", dir=output.parent))
                output.rename(previous / "build")
                try:
                    staging.rename(output)
                except OSError:
                    (previous / "build").rename(output)
                    shutil.rmtree(previous, ignore_errors=True)
                    raise
                # A leftover is removed by the next build.
                shutil.rmtree(previous, ignore_errors=True)
                removed += 1
            else:
                staging.rename(output)
            record = os.open(marker(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(record, "w", encoding="utf-8") as handle:
                handle.write(str(output))
            # Still under the lock, so another build cannot swap the output first.
            committed = (release_commit(root, output, commit_onto,
                                        f"release {details['version']} from {head[:12]}")
                         if commit_onto else None)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    finally:
        os.close(lock)
    untracked = [item for item in git(root, "ls-files", "-z", "--others", "--exclude-standard")
                 .decode("utf-8").split("\0") if item]
    plugin = output / "plugins" / name if release else output
    return {"package": name, "version": details["version"], "output": str(output),
            "plugin_root": str(plugin), "release": release,
            "files": len(files), "bytes": sum((plugin / relative).stat().st_size for relative in files),
            "commit": head, "tracked_changes": tracked_changes(root),
            "untracked_excluded": len(untracked), "removed_builds": removed,
            "validation": report["validation"], "client_execution": "not_checked",
            **({"release_commit": committed} if commit_onto else {})}


def release_commit(repository, tree, parent, message):
    """Write tree as one commit on parent in repository; no ref, index or working tree changes."""
    parent = resolve_commit(repository, parent)
    with tempfile.TemporaryDirectory(prefix="vibe-release-index-") as temporary:
        index = Path(temporary) / "index"
        # Force: tracked files may match the package's own ignore patterns.
        git(repository, f"--work-tree={tree}", "add", "--all", "--force", ".", index=index)
        written = git(repository, "write-tree", index=index).decode().strip()
    try:
        return git(repository, "commit-tree", written, "-p", parent, "-m", message).decode().strip()
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Could not write the release commit: {error.stderr.decode(errors='replace').strip()}") from None


def cached_versions(directory, manifest_path, name):
    if directory.is_symlink() or not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_dir() or not VERSION.fullmatch(path.name):
            continue
        try:
            if read_json(path / manifest_path).get("name") == name:
                found.append(path)
        except (OSError, ValueError, AttributeError):
            continue
    return found


def size(path):
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def prune(root, home, dry_run):
    """Remove cached copies of this plugin's local marketplace that no client uses."""
    root = root.resolve(strict=True)
    details = manifest(root)
    name, current = details["name"], details["version"]
    removed, skipped, total, candidates = [], [], 0, []

    marketplace = read_json(root / ".claude-plugin/marketplace.json")["name"]
    claude = home / ".claude/plugins/cache" / marketplace / name
    versions = cached_versions(claude, ".claude-plugin/plugin.json", name)
    try:
        records = read_json(home / ".claude/plugins/installed_plugins.json")
        if records.get("version") != 2:
            raise ValueError("unknown install record version")
        keep = {claude / current} | {Path(entry["installPath"])
                                     for entry in records["plugins"].get(f"{name}@{marketplace}", [])}
        candidates += [(path, keep) for path in versions]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Without the record, the copy Claude uses is unknown.
        if versions:
            skipped.append("claude-code: unreadable install record")

    # Codex records no install path; keep its newest copy so an older install survives.
    marketplace = read_json(root / ".agents/plugins/marketplace.json")["name"]
    codex = home / ".codex/plugins/cache" / marketplace / name
    versions = cached_versions(codex, ".codex-plugin/plugin.json", name)
    keep = {codex / current} | ({max(versions, key=lambda path: path.stat().st_mtime)} if versions else set())
    candidates += [(path, keep) for path in versions]

    for path, kept in candidates:
        if path in kept:
            continue
        total += size(path)
        removed.append(str(path))
        if not dry_run:
            shutil.rmtree(path)
    return {"removed": removed, "skipped": skipped, "bytes": total, "dry_run": dry_run}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help=f"package directory outside the checkout (default: {DEFAULT_OUTPUT}, "
                             f"or {RELEASE_OUTPUT} with --release)")
    parser.add_argument("--release", action="store_true",
                        help="build the Git release layout from a clean checkout: marketplace files at the root, "
                             "the plugin under plugins/<name>")
    parser.add_argument("--commit-onto", metavar="REF",
                        help="with --release, write the build as one commit on REF and print it; moves no ref")
    parser.add_argument("--prune-caches", action="store_true",
                        help="also remove unused cached copies from Claude Code and Codex")
    parser.add_argument("--dry-run", action="store_true", help="with --prune-caches, list without removing")
    args = parser.parse_args()
    if args.dry_run and not args.prune_caches:
        parser.error("--dry-run applies only with --prune-caches")
    if args.commit_onto and not args.release:
        parser.error("--commit-onto applies only with --release")
    output = args.output or (RELEASE_OUTPUT if args.release else DEFAULT_OUTPUT)
    report = build(ROOT, output, release=args.release, commit_onto=args.commit_onto)
    if args.prune_caches:
        report["pruned_caches"] = prune(ROOT, Path.home(), args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"Packaging failed: {error}: {(error.stderr or b'').decode(errors='replace').strip()}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        sys.exit(1)
