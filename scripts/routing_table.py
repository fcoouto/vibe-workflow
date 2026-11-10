#!/usr/bin/env python3
"""Write the Smart Routing strategy table from local evidence files (plan Task S2.3). Maintenance only.

Usage:
  routing_table.py refresh --guidance PATH --catalog PATH --benchmarks PATH --terms PATH
                           [--settings PATH] [--table PATH]
  routing_table.py rollback [--settings PATH] [--table PATH]

The four inputs are the JSON shapes `runtime.routing_evidence` documents. The table goes where
`scripts/routing.py` reads it: `--table`, or `strategy-table.json` beside the settings file. The command
reads and writes local files only: no network, model, subprocess or secret access. Spawn paths, hooks and
startup never run it; the maintenance procedure is clients/guidance-publication.md#strategy-table-refresh.

Inputs must be regular files, not symlinks, FIFOs or devices (`unsafe_input`). It prints one JSON line.
Exit 0 when a fresh table was written (`refreshed`); 1 when the last verified table was kept (`kept`, marked
`refresh_failed` with its original `compiled_at`) or nothing was written (`blocked`); 2 for a usage error.
A `dir_sync_failed` warning means the table was written but the folder sync after the rename failed.

A refresh that writes a table of a new version first keeps the verified table it replaces, byte for byte, as
`strategy-table.previous.json` (`<stem>.previous<suffix>` for `--table`); only one previous version is kept.
`rollback` swaps the two (exit 0, `rolled_back`), so a second rollback returns to the newer table. It refuses
with `no_previous_table`, `unsafe_previous_table`, `invalid_previous_table` or `unsafe_table_path` and changes
nothing. It installs the previous version first; if keeping the replaced table then fails, the warning
`newer_table_not_kept` means the rollback still happened and a refresh recreates the newer table.
`current_table_unverified_discarded` means the replaced table was unverified, so it was not kept.
"""
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import routing_evidence, usage_budget  # noqa: E402
from runtime.usage_budget import BudgetError  # noqa: E402
from scripts import routing as reader  # noqa: E402

VERSION = 1
USAGE = 2
MAX_INPUT = 4 << 20
INPUTS = ('guidance', 'catalog', 'benchmarks', 'terms')


class Blocked(Exception):
    pass


class UnsafeInput(ValueError):
    pass


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _finite(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Non-finite number')
    return value


def _parse(raw):
    """Strict JSON: no NaN or infinities, no duplicate keys."""
    return json.loads(raw.decode('utf-8'), object_pairs_hook=reader._pairs, parse_constant=reader._constant,
                      parse_float=_finite)


def _read_regular(path, limit):
    """At most ``limit + 1`` bytes of a regular file that is not a symlink. The path is checked before it is
    opened, so a link, FIFO or device is never opened; the open descriptor is checked again against a swap."""
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise UnsafeInput('Not a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafeInput('Not a regular file')
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _read_input(path):
    try:
        raw = _read_regular(path, MAX_INPUT)
    except OSError as error:
        if error.errno == errno.ELOOP:  # O_NOFOLLOW met a link swapped in after the check
            raise UnsafeInput('Symlinked input') from None
        raise
    if len(raw) > MAX_INPUT:
        raise ValueError('Evidence input too large')
    return raw


def _folder(path):
    """Whether the table's folder exists; a folder the reader would refuse is never written to."""
    try:
        usage_budget.private(path.parent, True)
    except FileNotFoundError:
        return False
    except BudgetError:
        raise Blocked('unsafe_table_path') from None
    return True


def _stored(path, unsafe):
    """The verified table at ``path`` and its exact bytes, or None when absent or unverified.

    A path the reader would refuse raises ``Blocked(unsafe)``: it is never replaced or kept."""
    if not os.path.lexists(path):
        return None
    try:
        info = usage_budget.private(path)
    except BudgetError:
        raise Blocked(unsafe) from None
    if info.st_size > reader.MAX_TABLE:
        return None
    try:
        raw = _read_regular(path, reader.MAX_TABLE)
        value = None if len(raw) > reader.MAX_TABLE else _parse(raw)
    except (ValueError, RecursionError, reader.Blocked, OSError):
        return None
    compiled = (type(value) is dict and reader._table_shape(value) and value.get('schema') == routing_evidence.SCHEMA
                and type(value.get('sources')) is list and type(value.get('compiled_at')) is str)
    return (value, raw) if compiled else None


def previous_path(path):
    """Where the one previous version lives: `strategy-table.previous.json` beside `strategy-table.json`."""
    return path.with_name(path.stem + '.previous' + path.suffix)


def _encode(table):
    data = json.dumps(table, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > reader.MAX_TABLE or not reader._table_shape(table):
        raise ValueError('Compiled table unusable by the reader')
    return data


def _write(path, data):
    """Atomic replace: private temp file in the same folder, fsync, rename, fsync the folder.

    Returns warnings. Once the rename has happened the table is written, so a failed folder sync is a
    warning, not a failure."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        usage_budget.private(path.parent, True)
        if os.path.lexists(path):
            usage_budget.private(path)
    except BudgetError:
        raise Blocked('unsafe_table_path') from None
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.strategy-table.', suffix='.tmp')
    try:
        try:
            handle = os.fdopen(fd, 'wb')
        except OSError:
            os.close(fd)
            raise
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        Path(temp).unlink(missing_ok=True)
        raise Blocked('write_failed') from None
    try:
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    except OSError:
        return ['dir_sync_failed']
    return []


def refresh(table_path, sources, now=None):
    """Compile from local files and write the table; returns the one-line report."""
    path = reader._resolved(table_path)
    stored = _stored(path, 'unsafe_table_path') if _folder(path) else None
    previous = stored[0] if stored else None
    digests, problem = {}, {}

    def load():
        values = []
        for name in INPUTS:
            try:
                raw = _read_input(sources[name])
            except UnsafeInput:
                problem['reason'] = 'unsafe_input'
                raise
            digests[name] = hashlib.sha256(raw).hexdigest()
            values.append(_parse(raw))
        return values

    def fail():
        raise ValueError('Compiled table rejected')

    try:
        table = routing_evidence.refresh_table(previous, load, now=now or _now())
        if not table.get('refresh_failed'):
            table = dict(table, input_digests=dict(digests))
        try:
            data = _encode(table)
        except ValueError:
            table = routing_evidence.refresh_table(previous, fail)
            try:
                data = _encode(table)
            except ValueError:  # the failure mark does not fit: leave the verified file exactly as it is
                table, data = dict(previous, refresh_failed=True), None
    except ValueError:
        raise Blocked(problem.get('reason', 'no_verified_table')) from None
    warnings = []
    if data is not None and not table.get('refresh_failed') and stored and previous.get('version') != table['version']:
        warnings += _write(previous_path(path), stored[1])  # before the table: a failure leaves the table as it was
    if data is not None:
        warnings += [item for item in _write(path, data) if item not in warnings]
    report = dict(v=VERSION, state='refreshed', version=table['version'], compiled_at=table['compiled_at'],
                  rows=len(table['rows']), dropped=len(table.get('dropped', [])))
    if table.get('refresh_failed'):
        report.update(state='kept', reason=problem.get('reason', 'refresh_failed'))
    if warnings:
        report['warnings'] = warnings
    return report


def rollback(table_path):
    """Swap the previous version into place; a second rollback returns to the newer table."""
    path = reader._resolved(table_path)
    other = previous_path(path)
    if not _folder(path) or not os.path.lexists(other):
        raise Blocked('no_previous_table')
    current = _stored(path, 'unsafe_table_path')
    prior = _stored(other, 'unsafe_previous_table')
    if prior is None:
        raise Blocked('invalid_previous_table')
    warnings = _write(path, prior[1])  # the table first: an interruption never leaves no table
    if current:
        try:
            warnings += [item for item in _write(other, current[1]) if item not in warnings]
        except Blocked:
            warnings.append('newer_table_not_kept')
    elif os.path.lexists(path):  # an unverified table is never kept
        warnings.append('current_table_unverified_discarded')
    table = prior[0]
    report = dict(v=VERSION, state='rolled_back', version=table['version'], compiled_at=table['compiled_at'],
                  rows=len(table['rows']), replaced_version=current[0].get('version') if current else None)
    if warnings:
        report['warnings'] = warnings
    return report


def _arguments(argv):
    items, paths = list(argv), {}
    if not items or items[0] not in ('refresh', 'rollback'):
        return None, None
    command = items.pop(0)
    inputs = INPUTS if command == 'refresh' else ()
    allowed = {'--settings', '--table', *('--' + name for name in inputs)}
    while items:
        item = items.pop(0)
        if item not in allowed or item in paths or not items or items[0].startswith('-'):
            return None, None
        paths[item] = items.pop(0)
    if not {'--' + name for name in inputs} <= set(paths):
        return None, None
    return command, paths


def main(argv=None):
    command, paths = _arguments(sys.argv[1:] if argv is None else argv)
    if paths is None:
        report, code = dict(v=VERSION, state='blocked', reason='usage_error'), USAGE
    else:
        settings = paths.get('--settings') or reader.default_settings()
        table = paths.get('--table') or Path(settings).absolute().parent / 'strategy-table.json'
        try:
            if command == 'rollback':
                report = rollback(table)
            else:
                report = refresh(table, {name: Path(paths['--' + name]) for name in INPUTS})
            code = 0 if report['state'] in ('refreshed', 'rolled_back') else 1
        except Blocked as error:
            report, code = dict(v=VERSION, state='blocked', reason=str(error)), 1
        except Exception:  # noqa: BLE001 - fixed reason; never echo evidence or a traceback
            report, code = dict(v=VERSION, state='blocked', reason='internal_error'), 1
    print(json.dumps(report), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
