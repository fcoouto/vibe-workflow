#!/usr/bin/env python3
"""Validate the thin package boundary without network, native readers or inference."""
import argparse
import ast
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import FILES, RUNTIME

ROOT = Path(__file__).resolve().parents[1]


def validate(root, exported=False):
    root = Path(root).resolve(strict=True)
    if exported:
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir()}
        if actual != set(FILES):
            raise ValueError("export_inventory_invalid")
    for name in FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or path.resolve() != path or path.stat().st_size > 1 << 20:
            raise ValueError("package_path_invalid")
        if path.suffix not in (".py", ".js", ".json", ".md"):
            continue
        text = path.read_text()
        if re.search(r"/Users/[^/]+/|/home/[^/]+/|[A-Z]:\\\\Users\\\\", text):
            raise ValueError("machine_path_in_export")
        if path.suffix == ".py":
            tree = ast.parse(text)
            if len(text.splitlines()) > 1000:
                raise ValueError("module_too_large")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        raise ValueError("relative_import_invalid")
                    modules = [node.module]
                    if node.module == "runtime":
                        modules = ["runtime." + alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    if module.startswith("runtime."):
                        if module.split(".")[1:] not in [[item] for item in RUNTIME]:
                            raise ValueError("local_import_outside_package")
                    elif module.split(".")[0] not in sys.stdlib_module_names:
                        raise ValueError("third_party_import")
        elif path.suffix == ".json":
            json.loads(text)
    package = json.loads((root / "package.json").read_text())
    if package.get("dependencies") or package.get("exports") != {".": "./index.js"}:
        raise ValueError("plugin_metadata_invalid")
    return len(FILES), sum((root / name).stat().st_size for name in FILES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    try:
        count, size = validate(args.root or ROOT, exported=args.root is not None)
    except (OSError, ValueError, SyntaxError):
        print("validation: failed (package contract)", file=sys.stderr)
        return 1
    print(f"validation: passed ({count} files, {size} bytes); native client execution not checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
