"""Run-state machinery for managed OpenRouter workers (C.3 design 3.1, 3.3, 6.3 C3-4b; scripts/openrouter_workers.py).

Holds are never stranded; every recovery settles only its own attempt id, and settling again changes nothing.
- `prepare` writes its attempt id to `<root>/.routing/<attempt>.json` BEFORE the routing `prepare` (`write_ahead`) and
  keeps that file locked until the run is written or its hold closed. An entry whose lock is free belongs to a dead
  `prepare`.
- `execute` writes `dispatching` to `<run>/routing.json` BEFORE the routing `dispatch` and `dispatched` after it
  (`Routed`), while it holds the run's execution lock. A started run whose lock is free and whose hold is open
  belongs to a dead `execute`.
- The next managed `prepare` or `execute` recovers both under `<root>/.routing.lock` (`recover`): the hold is
  released, or, once dispatched, completed with no usage (its whole reservation stays held as `cost_unknown`). An
  `uncertain` hold is the operator's (C.3 design 3.5). Nothing is sent on any of these paths.

The token never reaches this module's files: `Routed` keeps only the session id.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from runtime import opencode_launcher as launcher

PENDING, ROOT_LOCK, ROUTING = '.routing', '.routing.lock', 'routing.json'
OPEN_STATES = ('admitted', 'dispatching', 'dispatched')
READ_ONLY = ('read', 'glob', 'grep')
RECONCILIATION = 'reconciliation_required'  # an `uncertain` hold: only the operator settles it


def session_rules(permission):
    """A V1 read-only permission (`openrouter_workers.configuration`) as OpenCode session rules (the last match wins):
    `read` of the selected files, `glob` and `grep`; every other action, the launcher's own allowed and denied ones
    included, is denied. A permission that grants anything else is refused."""
    rules = [dict(action='*', resource='*', effect='deny')]
    for action, value in permission.items():
        if action != '*':
            items = sorted(value.items(), key=lambda item: item[0] != '*') if isinstance(value, dict) else [('*', value)]
            rules += [dict(action=action, resource=resource, effect=effect) for resource, effect in items]
    granted = {rule['action'] for rule in rules if rule['effect'] == 'allow'}
    if not granted <= set(READ_ONLY):
        raise ValueError('worker_profile_widened')
    return rules + [dict(action=action, resource='*', effect='deny')
                    for action in launcher.ALLOWED + launcher.DENIED if action not in granted]


class ReadOnlyHost:
    """The OpenCode host as the launcher sees it for a worker: every session it creates gets `rules`, never the
    launcher's editing profile (`opencode_launcher.WORKER_RULES`)."""

    def __init__(self, host, rules):
        self.host, self.rules, self.directory = host, rules, host.directory

    def rpc(self, method, payload):
        return self.host.rpc(method, payload)

    def request(self, method, path, body=None, query=None):
        if (method, path) == ('POST', '/api/session') and isinstance(body, dict):
            body = dict(body, permissions=[dict(rule) for rule in self.rules])
        return self.host.request(method, path, body, query)


def _save(path, value):
    temporary = path.with_suffix('.tmp')
    with open(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600), 'w') as handle:
        json.dump(value, handle)
    temporary.replace(path)


def admitted(run, attempt, checkout):
    """The run's dispatch state when its run is written: admitted, not yet dispatched."""
    _save(Path(run) / ROUTING, dict(attempt_id=attempt, checkout=str(checkout), state='admitted'))


def state(run):
    return json.loads((Path(run) / ROUTING).read_text())['state']


def set_state(run, value):
    path = Path(run) / ROUTING
    _save(path, dict(json.loads(path.read_text()), state=value))


def settled(run, reason):
    """Record a settlement attempt's outcome: None closed the hold; `reconciliation_required` left it to the operator;
    any other reason keeps the run open for the next recovery."""
    if reason is None or reason == RECONCILIATION:
        set_state(run, 'settled' if reason is None else 'uncertain')


class Routed:
    """The managed service as the OpenCode launcher sees it for one prepared run. It answers `prepare` and `dispatch`
    only for the run's own attempt (the launcher's `prepare` is the duplicate of the admission the run holds), writes
    the dispatch state around the routing `dispatch`, keeps the session id (never the token), passes a `complete`'s
    usage through `charge` (None: no usage, so the dollar hold stays `cost_unknown`) and notes when an event closed the
    hold. `closed` are the reasons that mean an earlier call already closed it."""

    def __init__(self, service, run, attempt, charge, closed):
        self.service, self.run, self.attempt, self.charge, self.closed = service, run, attempt, charge, closed
        self.project, self.settings_path, self.ledger_path = service.project, service.settings_path, service.ledger_path
        self.session = None

    def close(self):
        self.service.close()

    def handle(self, raw, op=None):
        value = json.loads(raw)
        kind = value.get('op')
        if kind in ('prepare', 'dispatch') and (value.get('assignment') or value).get('attempt_id') != self.attempt:
            return dict(v=1, state='blocked', reason='invalid_request')
        if kind == 'dispatch':
            set_state(self.run, 'dispatching')  # write-ahead: a death from here on is recovered as dispatched
            answer = self.service.handle(raw, op)
            sent = answer.get('state') in ('dispatched', 'already_dispatched')
            set_state(self.run, 'dispatched' if sent else 'admitted')
            self.session = answer.get('session_id') if sent else None
            return answer
        event = value.get('event') if kind == 'record' and type(value.get('event')) is dict else None
        if event is not None and event.get('kind') == 'complete' and 'usage' in event:
            usage = self.charge(event['usage'])
            event = dict({key: item for key, item in event.items() if key != 'usage'},
                         **({} if usage is None else dict(usage=usage)))
            raw = json.dumps(dict(value, event=event)).encode()
        answer = self.service.handle(raw, op)
        if event is not None and event.get('attempt_id') == self.attempt and \
                event.get('kind') in ('not_dispatched', 'complete', 'uncertain') and \
                (answer.get('state') == 'recorded' or answer.get('reason') in self.closed):
            set_state(self.run, 'uncertain' if event['kind'] == 'uncertain' else 'settled')
        return answer


@contextmanager
def root_lock(root):
    with open(os.open(Path(root) / ROOT_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def write_ahead(root, attempt, checkout):
    """Record `attempt` before the routing `prepare` (call under `root_lock`). Returns (open file, entry path): the file
    keeps its lock while this process lives; unlink the entry once the run is written or the hold closed."""
    folder = Path(root) / PENDING
    folder.mkdir(mode=0o700, exist_ok=True)
    entry = folder / (attempt + '.json')
    handle = open(os.open(entry, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+')
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.write(json.dumps(dict(attempt_id=attempt, checkout=str(checkout))))
    handle.flush()
    return handle, entry


def _unlocked(path):
    """An open handle on `path` locked by this process, or None while another process holds it."""
    try:
        handle = open(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'r+')
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def recover(root, settle_at):
    """Under `root_lock`: settle what dead processes left. `settle_at(checkout, attempt)` closes one attempt's own hold
    and answers None once nothing of it is open, else routing's fixed reason. A run still `prepared` keeps its
    admission, which expires unsent."""
    root = Path(root)
    for path in sorted((root / PENDING).glob('*.json')) if (root / PENDING).is_dir() else []:
        handle = _unlocked(path)
        if handle is None:
            continue  # its `prepare` is alive
        with handle:
            try:
                entry = json.loads(handle.read())
            except ValueError:
                entry = None  # written under the root lock before its `prepare` began: it never reached routing
            if entry is None or settle_at(entry['checkout'], entry['attempt_id']) is None:
                path.unlink()
    for run in sorted(root.iterdir()):
        try:
            current = json.loads((run / ROUTING).read_text())
            started = json.loads((run / 'result.json').read_text())['state'] != 'prepared'
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if current.get('state') not in OPEN_STATES or not started:
            continue
        handle = _unlocked(run / 'execution.lock')
        if handle is not None:  # its `execute` is dead
            with handle:
                settled(run, settle_at(current['checkout'], current['attempt_id']))


def response(host, session, limit):
    """The text parts of the session's assistant messages, at most `limit` bytes; provider text stays data for
    native review."""
    found = ''
    for message in launcher._messages(host, session):
        content = message.get('content') if isinstance(message, dict) and message.get('type') == 'assistant' else None
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str):
                found += part['text'] + '\n'
                if len(found.encode()) > limit:
                    raise ValueError('output_limit')
    return found
