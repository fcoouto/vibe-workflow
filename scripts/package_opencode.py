"""Prepare a separate OpenCode artifact without installing dependencies."""

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import package

ADAPTER = Path("runtime/adapters/opencode_workflow")
SOURCES = ("index.ts", "gate.ts", "stop.ts", "connector.ts", "rpc.ts", "server.ts", "viewer.ts",
           "gui.tsx", "package.json", "package-lock.json", "vite.config.ts")


def prepare(root, output):
    root = Path(root).resolve(strict=True)
    output = Path(output).expanduser().absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output and its parents must not be symlinks")
    output = output.resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Output must be a new directory outside the checkout")
    # Reuse generic export membership; include only this adapter's declared, possibly uncommitted source files.
    files = set(package.tracked_files(root))
    files.update((ADAPTER / name).as_posix() for name in SOURCES if (root / ADAPTER / name).exists())
    for name in files:
        source = root / name
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError(f"Unsafe source: {name}")
        if "node_modules" in source.parts or "dist" in source.relative_to(root).parts:
            raise ValueError(f"Generated dependency or bundle in clean export: {name}")
    manifest = json.loads((root / ADAPTER / "package.json").read_text(encoding="utf-8"))
    manifest["exports"] = {key: f"./{ADAPTER.as_posix()}/{value.removeprefix('./')}"
                           for key, value in manifest["exports"].items()}
    manifest["exports"]["."] = f"./{ADAPTER.as_posix()}/server.ts"
    manifest["exports"]["./gui-experimental"] = f"./{ADAPTER.as_posix()}/dist/gui-experimental.cjs"
    manifest["scripts"] = {"build": f"vite build --config {ADAPTER.as_posix()}/vite.config.ts"}
    output.mkdir(mode=0o700)
    for name in sorted(files):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    (output / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lock = output / ADAPTER / "package-lock.json"
    if lock.is_file():
        shutil.copy2(lock, output / "package-lock.json")
    return {"output": str(output), "files": len(files) + 1,
            "bytes": sum(path.stat().st_size for path in output.rglob("*") if path.is_file()),
            "dependencies": "not_prepared", "client_execution": "not_checked"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(ROOT, args.output), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"OpenCode packaging failed: {error}\n")


if __name__ == "__main__":
    main()
