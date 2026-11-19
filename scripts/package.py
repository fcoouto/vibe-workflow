#!/usr/bin/env python3
"""Create a new allowlisted local plugin export. Never replace existing paths."""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import FILES
from validate import validate

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = Path(output).absolute()
    if output.is_symlink() or output.exists() or output.parent.resolve() != output.parent:
        raise ValueError("output_must_be_new_canonical_path")
    validate(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".smart-router-build-", dir=output.parent))
    try:
        for name in FILES:
            source, target = ROOT / name, staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            target.chmod(0o644)
        validate(staging, exported=True)
        # mkdir refuses races and leaves every pre-existing destination untouched.
        output.mkdir()
        for item in staging.iterdir():
            item.rename(output / item.name)
    finally:
        shutil.rmtree(staging)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        output = build(args.output)
    except (OSError, ValueError):
        print("package: blocked (invalid source or output)", file=sys.stderr)
        return 1
    print(f"package: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
