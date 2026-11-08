"""Best-effort OpenCode session titles for ordinary native work; never a model turn."""
import json
import re
import time

from runtime.title_sync import MAX_TITLE_BODY, METER_PREFIX, _parsed

ATTEMPTS = 3
BACKOFF = (0.2, 0.6)  # Pause in seconds before the second and third attempts.
CALL_TIMEOUT = 2.0
DEADLINE = 5.0  # No new call starts after this many seconds.
OPERATIONS = frozenset({'session.get', 'session.update'})
SESSION = re.compile(r'ses_[A-Za-z0-9]{1,128}')
DEFAULT_TITLE = re.compile(r'(?:New|Child) session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z')


class TitleTransportError(OSError):
    """Bounded failure code without identifiers or client output."""
    def __init__(self, reason):
        super().__init__('OpenCode title transport unavailable')
        self.reason = reason


class OpenCodeTitles:
    """`opencode api` session.get/session.update only; argv lists, bounded calls."""
    def __init__(self, *, timeout=CALL_TIMEOUT, deadline=None, clock=time.monotonic):
        self.timeout, self.deadline, self.clock = timeout, deadline, clock

    def __enter__(self):
        import shutil
        self.executable = shutil.which('opencode')
        if self.executable is None:
            raise TitleTransportError('unavailable')
        return self

    def __exit__(self, *args):
        pass

    def call(self, operation, session, body=None):
        import subprocess
        if operation not in OPERATIONS:
            raise ValueError('Unsupported OpenCode title operation')
        _session(session)
        timeout = self.timeout
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - self.clock())
        if timeout <= 0:
            raise TitleTransportError('timeout')
        argv = [self.executable, 'api', operation, '--param', f'sessionID={session}']
        if body is not None:
            argv += ['-d', json.dumps(body)]
        try:
            completed = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                       timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise TitleTransportError('timeout') from None
        except OSError:
            raise TitleTransportError('unavailable') from None
        if completed.returncode != 0:
            raise TitleTransportError('not_found' if b'HTTP 404' in completed.stderr else 'failed')
        return completed.stdout

    def read(self, session):
        try:
            data = json.loads(self.call('session.get', session))['data']
            title = data.get('title')
            if not isinstance(data.get('id'), str) or not isinstance(title, (str, type(None))):
                raise TypeError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TitleTransportError('malformed') from None
        if data['id'] != session:
            raise TitleTransportError('identity')
        return title or ''

    def write(self, session, title):
        _title(title)
        self.call('session.update', session, {'title': title})


def _session(session):
    if not isinstance(session, str) or not SESSION.fullmatch(session):
        raise ValueError('Invalid OpenCode session')


def _title(title):
    parsed = _parsed(title)
    if (parsed is None or parsed['layout'] != 'status-after-meter' or not title.startswith(METER_PREFIX)
            or len(title.removeprefix(METER_PREFIX)) > MAX_TITLE_BODY):
        raise ValueError('Invalid managed title')


def _blocked(current, baseline):
    """Apply the manual-title rules; return a deferral result or None to write."""
    if baseline is None:
        if current == '' or DEFAULT_TITLE.fullmatch(current) or _parsed(current):
            return None
        return dict(ok=False, deferred=True, reason='manual_title')
    if current == baseline:
        return None
    before, after = _parsed(baseline), _parsed(current)
    if before and after and all(before[k] == after[k] for k in ('meter', 'family', 'member', 'status', 'layout')):
        return dict(ok=False, deferred=True, reason='topic_changed', topic=after['topic'])
    return dict(ok=False, deferred=True, reason='manual_title')


def deliver(session, desired, baseline=None, *, transport=None, attempts=ATTEMPTS, backoff=BACKOFF,
            timeout=CALL_TIMEOUT, deadline=DEADLINE, sleep=time.sleep, clock=time.monotonic):
    """Write one managed title; never raise or print. A deferral leaves the next update to retry."""
    try:
        _session(session)
        _title(desired)
        if baseline is not None and not isinstance(baseline, str):
            raise ValueError('Invalid baseline')
    except ValueError:
        return dict(ok=False, deferred=False, reason='invalid_request')
    reason = 'failed'
    try:
        end = clock() + deadline
        for attempt in range(attempts):
            if attempt:
                pause = backoff[min(attempt, len(backoff)) - 1]
                if clock() + pause >= end:
                    break
                sleep(pause)
            try:
                with (transport or (lambda: OpenCodeTitles(timeout=timeout, deadline=end, clock=clock)))() as native:
                    current = native.read(session)
                    if current == desired:
                        return dict(ok=True, deferred=False, reason=None)
                    blocked = _blocked(current, baseline)
                    if blocked is not None:
                        return blocked
                    native.write(session, desired)
                    if native.read(session) != desired:
                        # A concurrent edit may own the title now; never write over it.
                        return dict(ok=False, deferred=True, reason='readback')
                    return dict(ok=True, deferred=False, reason=None)
            except TitleTransportError as error:
                reason = error.reason
    except Exception:
        reason = 'failed'
    return dict(ok=False, deferred=True, reason=reason)
