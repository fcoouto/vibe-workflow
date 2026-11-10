"""Writing a live run's results safely: the full results (redacted twice, so secrets the Redactor learns partway
through its first pass are removed everywhere), a leak-checked skeleton when the leak check refuses them (falling back
to a fixed minimal record), the operator's owner-only withheld copy, and `--out` kept outside every repository."""
import json
import os
from pathlib import Path
import stat

from tests.qualification import sanitize
from tests.qualification.safety import CAPS, Refused


def inside_repository(path):
    """Whether `path` (or the place it would be created) lies inside a git work tree (a `.git` in any parent)."""
    current = Path(os.path.abspath(path))
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return True
    return False


def check_out(out):
    """`--out` for a live run: outside every repository, so results are committed only by an operator's choice."""
    if out and inside_repository(Path(out).parent):
        raise Refused("--out must be outside every repository (results are committed only after the operator reads them)")


def minimal(results):
    """The fixed record written when even the skeleton matches the leak check: counts and booleans only."""
    cleanup = results.get("cleanup") if isinstance(results.get("cleanup"), dict) else {}
    ledger = results.get("call_ledger") if isinstance(results.get("call_ledger"), dict) else {}
    totals = {}
    for purpose in CAPS:
        if isinstance(ledger.get(purpose), dict):
            totals[purpose] = {k: v for k, v in ledger[purpose].items() if type(v) is int}
    return dict(blockers_count=len(results.get("blockers") or []), results_withheld=True, call_ledger=totals,
                cleanup={k: v for k, v in cleanup.items() if k in ("verified_by_harness", "processes_left",
                                                                    "isolated_dir_removed") and type(v) in (bool, int)})


def skeleton_text(clean, leaks, redact):
    """The skeleton (masked blockers, cleanup status, ledger totals, leak kinds, flagged JSON paths), itself
    leak-checked; if it still matches, the fixed minimal record instead."""
    flags = sanitize.flagged(clean, redact.secrets)
    text = json.dumps(redact(sanitize.skeleton(clean, leaks, flags)), indent=2, sort_keys=True)
    if redact.leaks(text):
        text = json.dumps(minimal(clean), indent=2, sort_keys=True)
    return text + "\n"


def withhold(results_text, ledger_path, run_id):
    """Keep refused full results for the operator in `<ledger dir>/withheld/<run-id>.json` (directory 0700, file
    0600, never a symlink, never inside a repository); returns its path. Never committed; deleted after reading."""
    directory = Path(ledger_path).parent / "withheld"
    if inside_repository(directory):
        raise Refused("the withheld results directory must be outside every repository")
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        os.mkdir(directory, 0o700)
        info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Refused("the withheld results directory must be a real directory owned by the user")
    os.chmod(directory, 0o700)
    name = "".join(c if c.isalnum() or c in "_.-" else "_" for c in str(run_id)) + ".json"
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(results_text + "\n")
    return directory / name
