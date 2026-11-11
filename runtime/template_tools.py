"""Read-only assistance for explicitly selected templates and project documents."""

from collections import Counter
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

from scripts import read_instructions


PACKAGE = Path(__file__).resolve().parents[1]
FILE_BYTES = 1024 * 1024
OPERATION_BYTES = 4 * FILE_BYTES
MAX_PATHS = 64
MAX_FINDINGS = 64
LIMITS = {
    "semantic": "No applicability, factual accuracy, semantic preservation or acceptance judgment.",
    "markdown": "Inline/reference links and ATX headings only; no HTML, setext headings or renderer extensions.",
    "preservation": "Exact nonblank lines and multiplicity; reordering allowed. Baseline trust is caller-owned.",
}


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _identity(value):
    return _digest(json.dumps(value, sort_keys=True, ensure_ascii=True).encode())


def _selection(names):
    selected = list(dict.fromkeys(names))
    if len(selected) > MAX_PATHS:
        raise ValueError("selection_limit")
    return selected


def _name(name):
    if (not isinstance(name, str) or not name or len(name) > 1024
            or not name.isprintable() or "\\" in name):
        raise ValueError("path_unsafe")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != name or name == ".":
        raise ValueError("path_unsafe")
    return name


def _root(root):
    try:
        path = Path(root).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("root_unavailable")
        return path
    except OSError:
        raise ValueError("root_unavailable") from None


class _Reads:
    """One bounded observation, including linked targets and before-images."""

    def __init__(self):
        self.cache = {}
        self.hashes = {}
        self.used = 0
        self.complete = True

    def read(self, root, name):
        _name(name)
        key = (str(root), name)
        if key in self.cache:
            value = self.cache[key]
            if isinstance(value, ValueError):
                raise value
            return value
        try:
            if len(self.cache) >= MAX_PATHS:
                raise ValueError("selection_limit")
            raw = self._read(root, name)
            self.hashes[key] = _digest(raw)
            self.cache[key] = raw
            return raw
        except ValueError as error:
            if str(error) == "file_missing":
                self.hashes[key] = "missing"
            else:
                self.complete = False
            if len(self.cache) < MAX_PATHS:
                self.cache[key] = error
            raise

    def _read(self, root, name):
        directory = descriptor = None
        try:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parts = Path(name).parts
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("path_unsafe")
            if info.st_size > FILE_BYTES:
                raise ValueError("file_too_large")
            remaining = OPERATION_BYTES - self.used
            if info.st_size > remaining:
                raise ValueError("operation_too_large")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                raw = stream.read(min(FILE_BYTES, remaining) + 1)
            self.used += len(raw)
            if len(raw) > FILE_BYTES:
                raise ValueError("file_too_large")
            if len(raw) > remaining:
                raise ValueError("operation_too_large")
            return raw
        except OSError as error:
            code = ("file_missing" if error.errno == errno.ENOENT else
                    "path_unsafe" if error.errno in {errno.ELOOP, errno.ENOTDIR} else "file_unavailable")
            raise ValueError(code) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)

    def text(self, root, name):
        try:
            return self.read(root, name).decode("utf-8")
        except UnicodeError:
            raise ValueError("content_unreadable") from None


def _templates(reads, templates):
    selected = _selection(templates)
    sources = {}
    for name in selected:
        _name(name)
        if not name.endswith(".md"):
            raise ValueError("template_invalid")
        sources["templates/" + name] = reads.text(PACKAGE, "templates/" + name)
    return sources


def prepare(root, templates, context, *, page=1, revision=None):
    """Observe consumer inputs, then deliver exact, revision-bound packaged shapes."""
    root, reads = _root(root), _Reads()
    context_hashes = {}
    for name in _selection(context):
        try:
            context_hashes[name] = _digest(reads.read(root, name))
        except ValueError:
            raise ValueError("context_unavailable") from None
    sources = _templates(reads, templates)
    if not sources or type(page) is not int or page < 1 or (page > 1 and not revision):
        raise ValueError("selection_invalid")
    try:
        reads.read(PACKAGE, str(read_instructions.MAP))
        # The installed package is trusted; its reader owns map validation and paging.
        instructions = read_instructions.load_batch(PACKAGE, list(sources))
        current = _identity([str(root), context_hashes, instructions["revision"]])
        if revision is not None and revision != current:
            raise ValueError("context_changed")
        if page != 1:
            instructions = read_instructions.load_batch(
                PACKAGE, list(sources), page, instructions["revision"])
    except (ValueError, OSError, KeyError, TypeError) as error:
        if str(error) == "context_changed":
            raise
        raise ValueError("prepared_source_unavailable") from None
    return dict(ok=True, observed_at=datetime.now(timezone.utc).isoformat(),
                context=context_hashes, revision=current, instructions=instructions)


def _prose(text):
    """Keep line positions while masking fenced and inline code examples."""
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            yield ""
        elif marker:
            fence = marker[1]
            yield ""
        else:
            yield re.sub(r"(`+)[^`]*?\1", "", line)


def _anchors(text):
    used = set()
    for line in _prose(text):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            slug = re.sub(r"[^\w\- ]", "", heading[1].lower()).replace(" ", "-")
            candidate, number = slug, 0
            while candidate in used:
                number += 1
                candidate = f"{slug}-{number}"
            used.add(candidate)
    return used


_DESTINATION = r'(?:<([^<>\n]+)>|([^\s()<>]+))(?:\s+"[^"\n]*")?'
_INLINE = re.compile(r'!?\[[^\[\]\n]*\]\(\s*' + _DESTINATION + r'\s*\)')
_REFERENCE = re.compile(r'^ {0,3}\[([^\[\]\n]+)\]:\s*' + _DESTINATION)


def _links(text):
    lines = list(_prose(text))
    definitions = {}
    for line in lines:
        match = _REFERENCE.match(line)
        if match:
            definitions[" ".join(match[1].lower().split())] = match[2] or match[3]
    for number, line in enumerate(lines, 1):
        if _REFERENCE.match(line):
            continue
        for match in _INLINE.finditer(line):
            yield number, match[1] or match[2]
        line = _INLINE.sub("", line)
        for match in re.finditer(r'!?\[([^\[\]\n]+)\](?:\[([^\[\]\n]*)\])?', line):
            label = " ".join((match[2] or match[1]).lower().split())
            if label in definitions:
                yield number, definitions[label]


def _target(document, target):
    try:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return None
        path = unquote(parsed.path)
        if not path:
            return document, unquote(parsed.fragment)
        if path.startswith("/") or "\\" in path:
            raise ValueError("link_unsafe")
        parts = list(Path(document).parts[:-1])
        for part in path.split("/"):
            if part == "..":
                if not parts:
                    raise ValueError("link_unsafe")
                parts.pop()
            elif part not in {"", "."}:
                parts.append(part)
        name = _name("/".join(parts))
        return name, unquote(parsed.fragment)
    except ValueError:
        raise ValueError("link_unsafe") from None


def check(root, documents, templates=(), *, before=None, required=None):
    """Return mechanical findings without quoting document text or changing files."""
    root, reads = _root(root), _Reads()
    documents = _selection(documents)
    required = {} if required is None else required
    if not isinstance(required, dict) or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in required.values()):
        raise ValueError("digest_invalid")
    _selection(required)
    sources = _templates(reads, templates)
    baseline = _root(before) if before is not None else None
    findings, count = [], 0

    def add(code, path, line=None):
        nonlocal count
        count += 1
        if len(findings) < MAX_FINDINGS:
            # Paths are caller input, never source excerpts; escape controls in JSON/notice.
            findings.append(dict(code=code, path=str(path)[:1024], **({"line": line} if line else {})))

    for name, expected in required.items():
        try:
            if _digest(reads.read(root, name)) != expected:
                add("context_changed", name)
        except ValueError:
            add("context_unavailable", name)

    placeholders = set()
    for source in sources.values():
        placeholders.update(match[0] for match in re.finditer(
            r"\[[^\[\]\n]+\](?![(:\[])", source) if match[0] not in {"[x]", "[ ]", "[X]"})
    author_note = re.compile(r"\b(?:Omit (?:lines|sections) that (?:do not|don't) apply|never invent values|(?:Use|Adapt|Replace|Remove) this template)\b", re.I)
    preserved = "not_checked" if baseline is None else "checked"
    for name in documents:
        try:
            text = reads.text(root, name)
        except ValueError as error:
            add(str(error), name)
            if baseline is not None:
                preserved = "incomplete"
            continue
        original = Counter()
        if baseline is not None:
            try:
                original = Counter(line for line in reads.text(baseline, name).splitlines() if line.strip())
            except ValueError as error:
                if str(error) != "file_missing":
                    add("baseline_unavailable", name)
                    preserved = "incomplete"
            current = Counter(line for line in text.splitlines() if line.strip())
            if original - current:
                add("original_line_removed", name)
                preserved = "failed"
        remaining = original.copy()
        for number, line in enumerate(text.splitlines(), 1):
            if remaining[line]:
                remaining[line] -= 1
                continue
            if sources and author_note.search(line):
                add("author_note", name, number)
            if any(token in line for token in placeholders):
                add("placeholder", name, number)
        for number, target in _links(text):
            try:
                local = _target(name, target)
                if local is None:
                    continue
                destination, anchor = local
                raw = reads.read(root, destination)
                if anchor and anchor not in _anchors(raw.decode("utf-8")):
                    add("anchor_missing", name, number)
            except (ValueError, UnicodeError) as error:
                code = str(error)
                code = {"file_missing": "link_missing", "path_unsafe": "link_unsafe"}.get(code, code)
                if isinstance(error, UnicodeError):
                    code = "content_unreadable"
                add(code, name, number)
    identity = [str(root), str(baseline), documents, list(sources), required,
                sorted((list(key), value) for key, value in reads.hashes.items()), findings, count]
    return dict(ok=count == 0, findings=findings, finding_count=count,
                fingerprint=_identity(identity) if reads.complete else None,
                preservation=preserved, limits=LIMITS.copy())


def notice(result, previous=None):
    """Caller-owned hook feedback; never persist state, block a stop or repair files."""
    if result["ok"] or (result.get("fingerprint") and result["fingerprint"] == previous):
        return ""
    lines = ["Template check: inspect these findings; no files changed."]
    for finding in result["findings"]:
        path = json.dumps(finding["path"], ensure_ascii=True)
        lines.append(f"{finding['code']}: {path}" + (f":{finding['line']}" if "line" in finding else ""))
    text = "\n".join(lines)
    return text if len(text) <= 1000 else text[:970] + "\nSee check JSON for details."
