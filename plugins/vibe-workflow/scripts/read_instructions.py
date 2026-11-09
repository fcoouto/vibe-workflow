#!/usr/bin/env python3
"""Prepare exact instruction sections at authoring time; read bounded pages locally."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MAP = Path("clients/instruction-map.json")
SOURCE_BYTES = 8000
OUTPUT_BYTES = 9500
BATCH_OUTPUT_BYTES = 24000
BOOTSTRAP = ["clients/codex.md", "policies/00-index.md"]


def instruction_name(name):
    path = Path(name)
    return (not path.is_absolute() and ".." not in path.parts and path.as_posix() == name
            and path.suffix == ".md" and len(path.parts) >= 2
            and (path.parts[0] == "skills" or (len(path.parts) == 2 and path.parts[0] in {"clients", "policies"})))


def source_path(root, name):
    path = (root / name).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Instruction source must stay inside the package")
    if name != str(MAP) and not instruction_name(path.relative_to(root.resolve()).as_posix()):
        raise ValueError("Resolved source must be a packaged instruction file")
    return path


def source_entry(raw, name):
    lines = raw.decode("utf-8").splitlines(keepends=True)
    ranges, start, size = [], 1, 0
    for number, line in enumerate(lines, 1):
        length = len(line.encode("utf-8"))
        if length > SOURCE_BYTES:
            raise ValueError(f"Instruction line exceeds the section budget in {name}; use bounded native reads")
        if size + length > SOURCE_BYTES:
            ranges.append([start, number - 1])
            start, size = number, 0
        size += length
    if lines:
        ranges.append([start, len(lines)])
    return {"sha256": hashlib.sha256(raw).hexdigest(), "ranges": ranges}


def build_manifest(root):
    files = {}
    paths = sorted([*(root / "clients").glob("*.md"), *(root / "policies").glob("*.md"),
                    *(root / "skills").rglob("*.md")])
    for path in paths:
        name = path.relative_to(root).as_posix()
        raw = source_path(root, name).read_bytes()
        files[name] = source_entry(raw, name)
    return {"version": 1, "files": files}


def automatic_source(root, name):
    path = Path(name)
    if not name.isprintable():
        raise ValueError("Select explicit printable instruction paths")
    if not path.is_absolute():
        return name, False
    package = root.resolve()
    target = path.resolve(strict=True)
    # A package alias followed by an escape must not become an external read.
    package_origin = any(parent.resolve(strict=True).is_relative_to(package) for parent in path.parents)
    if package_origin or target.is_relative_to(package):
        if not target.is_relative_to(package):
            raise ValueError("Instruction source must stay inside the package")
        local = path.parent.resolve(strict=True) / path.name
        key = local if local.is_relative_to(package) else target
        return key.relative_to(package).as_posix(), False
    return name, True


def _load_pages(root, paths, page, revision, external=False, auto=False):
    if external and auto:
        raise ValueError("Automatic and explicit external modes cannot be combined")
    manifest = None
    if not paths or type(page) is not int or page < 1 or (page > 1 and not revision):
        raise ValueError("Select sources and bind continuation pages to their revision")
    selected = list(dict.fromkeys(paths))
    identity, sections = [], []
    for name in selected:
        source, is_external = automatic_source(root, name) if auto else (name, external)
        if is_external:
            path = Path(name)
            if not path.is_absolute() or path.suffix.lower() != ".md" or not name.isprintable():
                raise ValueError("Select explicit absolute local Markdown instruction files")
            path = path.resolve(strict=True)
            if not path.is_file() or path.suffix.lower() != ".md" or path.is_relative_to(root.resolve()):
                raise ValueError("External instructions must be regular Markdown files outside the package")
            raw = path.read_bytes()
            entry = dict(source_entry(raw, name), resolved=str(path))
        else:
            if manifest is None:
                manifest = json.loads(source_path(root, str(MAP)).read_bytes())
                if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
                    raise ValueError("Invalid prepared instruction map")
            if source not in manifest["files"] or not instruction_name(source):
                raise ValueError("Select a packaged instruction path from the policy/client references")
            entry = manifest["files"][source]
            path = source_path(root, source)
            raw = path.read_bytes()
            if not isinstance(entry, dict) or hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                raise ValueError(f"Prepared source changed: {name}; use bounded native reads or update the package")
        lines = raw.decode("utf-8").splitlines(keepends=True)
        ranges = entry.get("ranges")
        if not isinstance(ranges, list):
            raise ValueError("Invalid prepared source ranges")
        expected = 1
        for span in ranges:
            if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                    or span[0] != expected or not span[0] <= span[1] <= len(lines)):
                raise ValueError("Prepared ranges must cover each source exactly once")
            start, end = span
            text = "".join(lines[start - 1:end])
            if len(text.encode("utf-8")) > SOURCE_BYTES:
                raise ValueError("Prepared instruction section exceeds the delivery budget")
            sections.append(dict(path=name, start_line=start, end_line=end, text=text,
                                 complete=end == len(lines)))
            expected = end + 1
        if expected != len(lines) + 1:
            raise ValueError("Prepared ranges omit instruction text")
        if auto:
            entry = dict(entry, authority="external" if is_external else "package", resolved=str(path),
                         prepared_key=None if is_external else source)
        identity.append([name, entry])
    if auto:
        identity = dict(mode="auto", sources=identity)
    current = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if revision is not None and revision != current:
        raise ValueError("Instruction selection changed; restart with the current source revision")
    pages, group, size = [], [], 0
    for section in sections:
        length = len(section["text"].encode("utf-8"))
        if group and size + length > SOURCE_BYTES:
            pages.append(group)
            group, size = [], 0
        group.append(section)
        size += length
    if group or not pages:
        pages.append(group)
    if page > len(pages):
        raise ValueError("Instruction page is outside the selected source range")
    return current, pages


def load_page(root, paths, page=1, revision=None, *, external=False, auto=False):
    current, pages = _load_pages(root, paths, page, revision, external, auto)
    result = dict(revision=current, page=page, pages=len(pages), sections=pages[page - 1])
    if external:
        result["external"] = True
    if auto:
        result["mode"] = "auto"
    return result


def load_batch(root, paths, page=1, revision=None, *, external=False, auto=False):
    current, pages = _load_pages(root, paths, page, revision, external, auto)
    result, sections = None, []
    for number in range(page, len(pages) + 1):
        single = dict(revision=current, page=number, pages=len(pages), sections=pages[number - 1])
        if external:
            single["external"] = True
        if auto:
            single["mode"] = "auto"
        render_page(single)  # Every included page must remain usable by the small-page fallback.
        candidate = dict(revision=current, page=page, last_page=number, pages=len(pages),
                         sections=sections + single["sections"])
        if external:
            candidate["external"] = True
        if auto:
            candidate["mode"] = "auto"
        try:
            render_page(candidate)
        except ValueError:
            break
        result, sections = candidate, candidate["sections"]
    if result is None:
        raise ValueError("Instruction page does not fit the batch output budget")
    return result


def render_page(result):
    last = result.get("last_page", result["page"])
    batch = "last_page" in result
    label = f"pages {result['page']}-{last}" if batch else f"page {result['page']}"
    source = "Selected" if result.get("mode") == "auto" else "External" if result.get("external") else "Vibe"
    output = [f"{source} instructions: {label}/{result['pages']}; revision {result['revision']}\n"]
    for section in result["sections"]:
        output.append(f"\n--- {section['path']} lines {section['start_line']}-{section['end_line']} ---\n")
        output.append(section["text"])
        output.append("\n--- source complete ---\n" if section["complete"] else "\n--- source continues ---\n")
    if last < result["pages"]:
        mode = ("--auto " if result.get("mode") == "auto" else "--external " if result.get("external") else "")
        mode += "--batch " if batch else ""
        output.append(f"\nContinue the same selection with {mode}--page {last + 1} --revision {result['revision']}.\n")
    else:
        output.append("\nSelection complete.\n")
    text = "".join(output)
    if len(text.encode("utf-8")) > (BATCH_OUTPUT_BYTES if batch else OUTPUT_BYTES):
        raise ValueError("Selection metadata exceeds the output budget; select fewer sources")
    return text


def envelope(text, kind, **metadata):
    return json.dumps(dict(version=1, kind=kind, text=text, characters=len(text), **metadata),
                      ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--revision")
    parser.add_argument("--batch", action="store_true", help="combine consecutive pages within one bounded response")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--external", action="store_true", help="read explicitly selected absolute local Markdown paths outside the package")
    mode.add_argument("--auto", action="store_true", help="validate each explicit source under its package or external authority")
    parser.add_argument("--json", action="store_true", help="return a complete native-reader envelope")
    parser.add_argument("--helper", action="store_true", help="load the fixed packaged native continuation helper")
    parser.add_argument("--prepare", action="store_true", help="authoring only: rebuild the package map")
    args = parser.parse_args()
    try:
        if args.helper:
            if args.paths or args.page != 1 or args.revision or args.batch or args.external or args.auto or args.prepare or args.json:
                raise ValueError("Helper loading cannot be combined with preparation or instruction reads")
            helper = (ROOT / "scripts/drain_instructions.js").resolve(strict=True)
            if not helper.is_relative_to(ROOT.resolve()) or not helper.is_file():
                raise ValueError("Native helper must stay inside the package")
            text = helper.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) > OUTPUT_BYTES:
                raise ValueError("Native helper exceeds its loading budget")
            print(envelope(text, "helper"))
            return 0
        if args.prepare:
            if args.paths or args.page != 1 or args.revision or args.batch or args.external or args.auto or args.json:
                raise ValueError("Preparation cannot be combined with a read")
            (ROOT / MAP).write_text(json.dumps(build_manifest(ROOT), indent=2) + "\n", encoding="utf-8")
            return 0
        paths = BOOTSTRAP if args.paths == ["bootstrap"] and not args.external else args.paths
        read = load_batch if args.batch else load_page
        result = read(ROOT, paths, args.page, args.revision, external=args.external, auto=args.auto)
        text = render_page(result)
        if args.json:
            text = envelope(text, "instructions", page=result["page"], last_page=result.get("last_page", result["page"]),
                            pages=result["pages"], revision=result["revision"], external=args.external, batch=args.batch,
                            mode="auto" if args.auto else "external" if args.external else "package")
        print(text, end="")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Prepared instruction read unavailable: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
