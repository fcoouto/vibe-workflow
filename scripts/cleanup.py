#!/usr/bin/env python3
"""Inspect cleanup prerequisites. Read-only: eligibility is not deletion permission."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import cleanup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("primary", "primary-branch", "worktree", "branch", "head", "merge"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--removed", action="store_true", help="Recheck after native worktree removal")
    parser.add_argument("--remote", help="Also inspect the supported HTTPS remote configuration")
    parser.add_argument("--repository", help="Exact owner/repo from separately verified provider facts")
    args = vars(parser.parse_args())
    remote, repository = args.pop("remote"), args.pop("repository")
    result = cleanup.inspect(**args)
    if result["status"] == "eligible" and (remote is not None or repository is not None):
        if not result["delete_remote_branch"]:
            result["remote_status"] = "disabled"
        else:
            try:
                result["destination"] = cleanup.remote_destination(args["primary"], remote, repository)
                result["remote_status"] = "configuration-only"
            except (OSError, ValueError, subprocess.SubprocessError):
                result["remote_status"] = "retained"
                result["remote_reason"] = "unsupported-or-unavailable-destination"
    print(json.dumps(result))
    return 0 if result["status"] == "eligible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
