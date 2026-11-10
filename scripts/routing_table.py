#!/usr/bin/env python3
"""Write the Smart Routing strategy table from local evidence files (plan Task S2.3). Maintenance only.

Usage:
  routing_table.py refresh --guidance PATH --catalog PATH --benchmarks PATH --terms PATH
                           [--settings PATH] [--table PATH]

The four inputs are the JSON shapes `runtime.routing_evidence` documents. The table goes where
`scripts/routing.py` reads it: `--table`, or `strategy-table.json` beside the settings file. The command
reads and writes local files only: no network, model, subprocess or secret access. Spawn paths, hooks and
startup never run it; the maintenance procedure is clients/guidance-publication.md#strategy-table-refresh.

It prints one JSON line. Exit 0 when a fresh table was written; 1 when the last verified table was kept
(`kept`, marked `refresh_failed` with its original `compiled_at`) or nothing could be written (`blocked`);
2 for a usage error.
"""
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


def _read_input(path):
    with open(path, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('Evidence input is not a regular file')
        raw = handle.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError('Evidence input too large')
    return raw


def _previous(path):
    """The last verified table, or None. A target the reader would refuse is never replaced."""
    try:
        usage_budget.private(path.parent, True)
    except FileNotFoundError:
        return None
    except BudgetError:
        raise Blocked('unsafe_table_path') from None
    if not os.path.lexists(path):
        return None
    try:
        info = usage_budget.private(path)
    except BudgetError:
        raise Blocked('unsafe_table_path') from None
    if info.st_size > reader.MAX_TABLE:
        return None
    try:
        value = _parse(path.read_bytes())
    except (ValueError, RecursionError, reader.Blocked, OSError):
        return None
    compiled = (type(value) is dict and reader._table_shape(value) and value.get('schema') == routing_evidence.SCHEMA
                and type(value.get('sources')) is list and type(value.get('compiled_at')) is str)
    return value if compiled else None


def _encode(table):
    data = json.dumps(table, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > reader.MAX_TABLE or not reader._table_shape(table):
        raise ValueError('Compiled table unusable by the reader')
    return data


def _write(path, data):
    """Atomic replace: private temp file in the same folder, fsync, rename, fsync the folder."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        usage_budget.private(path.parent, True)
        if os.path.lexists(path):
            usage_budget.private(path)
    except BudgetError:
        raise Blocked('unsafe_table_path') from None
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.strategy-table.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        Path(temp).unlink(missing_ok=True)
        raise Blocked('write_failed') from None
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def refresh(table_path, sources, now=None):
    """Compile from local files and write the table; returns the one-line report."""
    path = reader._resolved(table_path)
    previous = _previous(path)
    digests = {}

    def load():
        values = []
        for name in INPUTS:
            raw = _read_input(sources[name])
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
        raise Blocked('no_verified_table') from None
    if data is not None:
        _write(path, data)
    state = 'kept' if table.get('refresh_failed') else 'refreshed'
    return dict(v=VERSION, state=state, version=table['version'], compiled_at=table['compiled_at'],
                rows=len(table['rows']), dropped=len(table.get('dropped', [])))


def _arguments(argv):
    items, paths = list(argv), {}
    if not items or items.pop(0) != 'refresh':
        return None
    allowed = {'--settings', '--table', *('--' + name for name in INPUTS)}
    while items:
        item = items.pop(0)
        if item not in allowed or item in paths or not items or items[0].startswith('-'):
            return None
        paths[item] = items.pop(0)
    if not {'--' + name for name in INPUTS} <= set(paths):
        return None
    return paths


def main(argv=None):
    paths = _arguments(sys.argv[1:] if argv is None else argv)
    if paths is None:
        report, code = dict(v=VERSION, state='blocked', reason='usage_error'), USAGE
    else:
        settings = paths.get('--settings') or reader.default_settings()
        table = paths.get('--table') or Path(settings).absolute().parent / 'strategy-table.json'
        try:
            report = refresh(table, {name: Path(paths['--' + name]) for name in INPUTS})
            code = 0 if report['state'] == 'refreshed' else 1
        except Blocked as error:
            report, code = dict(v=VERSION, state='blocked', reason=str(error)), 1
        except Exception:  # noqa: BLE001 - fixed reason; never echo evidence or a traceback
            report, code = dict(v=VERSION, state='blocked', reason='internal_error'), 1
    print(json.dumps(report), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
