#!/usr/bin/env python3
"""Export this checkout's tracked files, minus development-only trees, as a clean local plugin package; no network."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = Path("~/.cache/vibe-workflow/dev-package")
VERSION = re.compile(r"\d+\.\d+\.\d+")
# Development-only trees: nothing installed reads them, and Codex's bundle scan allows 8 MiB in total.
EXCLUDED = ("tests/",)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def manifest(root):
    return read_json(root / ".codex-plugin/plugin.json")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


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


def marker(output):
    return output.parent / f".{output.name}.build"


def is_build(output, name):
    # A manifest alone also matches checkouts and client caches; only builds have the marker.
    try:
        recorded = marker(output).read_text(encoding="utf-8")
        return (recorded == str(output.resolve()) and not (output / ".git").exists()
                and read_json(output / ".codex-plugin/plugin.json").get("name") == name)
    except (OSError, ValueError, AttributeError):
        return False


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


def build(root, output):
    """Replace output with a validated copy of root's tracked files; remove older builds."""
    from scripts.validate import validate

    root = root.resolve(strict=True)
    name = manifest(root)["name"]
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
            files = tracked_files(root)
            for relative in files:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, target)
            report = validate(staging)
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
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    finally:
        os.close(lock)
    untracked = [item for item in git(root, "ls-files", "-z", "--others", "--exclude-standard")
                 .decode("utf-8").split("\0") if item]
    return {"package": name, "version": manifest(root)["version"], "output": str(output),
            "files": len(files), "bytes": sum((output / relative).stat().st_size for relative in files),
            "commit": git(root, "rev-parse", "HEAD").decode().strip(),
            "tracked_changes": bool(git(root, "status", "--porcelain", "--untracked-files=no")),
            "untracked_excluded": len(untracked), "removed_builds": removed,
            "validation": report["validation"], "client_execution": "not_checked"}


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
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"package directory outside the checkout (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--prune-caches", action="store_true",
                        help="also remove unused cached copies from Claude Code and Codex")
    parser.add_argument("--dry-run", action="store_true", help="with --prune-caches, list without removing")
    args = parser.parse_args()
    if args.dry_run and not args.prune_caches:
        parser.error("--dry-run applies only with --prune-caches")
    report = build(ROOT, args.output)
    if args.prune_caches:
        report["pruned_caches"] = prune(ROOT, Path.home(), args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        sys.exit(1)
