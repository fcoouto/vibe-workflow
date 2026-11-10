"""Best-effort OpenCode session titles for ordinary native work; never a model turn."""
import json
import re
import shutil
import subprocess
import time

from runtime.adapters.opencode import SESSION
from runtime.title_sync import MAX_TITLE_BODY, METER_PREFIX, _parsed

ATTEMPTS = 3
BACKOFF = (0.2, 0.6)  # Pause in seconds before the second and third attempts.
CALL_TIMEOUT = 2.0
DEADLINE = 5.0  # No new call starts after this many seconds.
OPERATIONS = frozenset({'session.get', 'session.update', 'session.messages'})
RETRIED = frozenset({'failed', 'timeout'})  # Other failures cannot change on retry.
DEFAULT_TITLE = re.compile(r'(?:New|Child) session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z')
PROOF = re.compile(r'[A-Za-z0-9_-]{16,64}')
PROOF_PAGE = 10  # Newest messages read by the --proof check; the calling command is in the newest one.


class TitleTransportError(OSError):
    """Bounded failure code without identifiers or client output."""
    def __init__(self, reason):
        super().__init__('OpenCode title transport unavailable')
        self.reason = reason


class OpenCodeTitles:
    """`opencode api` session.get/session.update only; argv lists, bounded calls."""
    def __init__(self, *, timeout=CALL_TIMEOUT, deadline=None, clock=time.monotonic):
        self.timeout, self.deadline, self.clock = timeout, deadline, clock

    # Context manager to match the NativeTitles transport accepted by title_sync.operate.
    def __enter__(self):
        self.executable = shutil.which('opencode')
        if self.executable is None:
            raise TitleTransportError('unavailable')
        return self

    def __exit__(self, *args):
        pass

    def call(self, operation, session, body=None):
        if operation not in OPERATIONS:
            raise ValueError('Unsupported OpenCode title operation')
        _session(session)
        timeout = self.timeout
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - self.clock())
        if timeout <= 0:
            raise TitleTransportError('timeout')
        if operation == 'session.messages':  # 2.2.6 exposes no operation ID for this route.
            argv = [self.executable, 'api', 'GET', f'/api/session/{session}/message?limit={PROOF_PAGE}']
        else:
            argv = [self.executable, 'api', operation, '--param', f'sessionID={session}']
        if body is not None:
            argv += ['-d', json.dumps(body)]
        # On timeout only the direct child is killed: `opencode` is one executable, and a
        # process-group kill could also stop the background server OpenCode may start.
        try:
            completed = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                       timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise TitleTransportError('timeout') from None
        except OSError:
            raise TitleTransportError('unavailable') from None
        if completed.returncode != 0:
            raise TitleTransportError('not_found' if _missing(completed) else 'failed')
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

    def verify(self, session, proof):
        """Require a running shell call in `session` whose command carries `--proof <proof>`.

        Binds an `OPENCODE_SESSION_ID` value to the session executing this command: an
        inherited value names another session, which is not running it. Reads the newest
        messages only; the calling command is still running, so it is among them. The
        token may be wrapped in one pair of matching quotes.
        """
        marker = re.compile(r'(?<![A-Za-z0-9_-])--proof\s+([\'"]?)' + re.escape(proof) + r'\1(?![A-Za-z0-9_-])')
        try:
            output = self.call('session.messages', session)
        except TitleTransportError as error:
            # A session this server does not know cannot be the one running this command.
            raise TitleTransportError('session_unverified' if error.reason == 'not_found' else error.reason) from None
        try:
            messages = json.loads(output)['data']
            if not isinstance(messages, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise TitleTransportError('malformed') from None
        for message in messages:
            if not isinstance(message, dict) or message.get('type') != 'assistant':
                continue
            for part in message.get('content') if isinstance(message.get('content'), list) else ():
                if not isinstance(part, dict) or part.get('type') != 'tool' or part.get('name') != 'shell':
                    continue
                state = part.get('state') if isinstance(part.get('state'), dict) else {}
                command = state.get('input') if isinstance(state.get('input'), dict) else {}
                command = command.get('command')
                if state.get('status') == 'running' and isinstance(command, str) and marker.search(command):
                    return
        raise TitleTransportError('session_unverified')


def _missing(completed):
    """Prefer the error tag in the response; fall back to the CLI's HTTP status line."""
    try:
        tag = json.loads(completed.stdout).get('_tag')
    except (ValueError, AttributeError):
        tag = None
    return tag == 'SessionNotFoundError' or (tag is None and b'HTTP 404' in completed.stderr)


def _session(session):
    if not isinstance(session, str) or not SESSION.fullmatch(session):
        raise ValueError('Invalid OpenCode session')


def _title(title):
    parsed = _parsed(title)
    if (parsed is None or not title.startswith(METER_PREFIX)
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
    if before and after and all(before[k] == after[k] for k in ('meter', 'status')):
        return dict(ok=False, deferred=True, reason='topic_changed', topic=after['topic'])
    return dict(ok=False, deferred=True, reason='manual_title')


def _done(desired, adopt, replaced):
    result = dict(ok=True, deferred=False, reason=None, baseline=desired)
    if adopt:
        bounded = replaced is not None and len(replaced) <= MAX_TITLE_BODY
        result['previous'] = replaced if bounded else None
    return result


def deliver(session, desired, baseline=None, *, adopt=False, proof=None, transport=None, attempts=ATTEMPTS,
            backoff=BACKOFF, timeout=CALL_TIMEOUT, deadline=DEADLINE, sleep=time.sleep, clock=time.monotonic):
    """Write one managed title; never raise or print. A deferral leaves the next update to retry.

    `adopt=True` is the one-time initial adoption: it may replace the title first
    observed by this call and reports it as `previous` (None if nothing was replaced
    or it exceeds the title bound). The caller keeps the returned baseline afterwards.
    With `proof`, `session` comes from the environment: a missing or unusable value is
    `session_unknown`, and nothing is read or written until `OpenCodeTitles.verify` binds
    the session to the running command; a failed binding is final (`session_unverified`).
    """
    try:
        _title(desired)
        if baseline is not None and not isinstance(baseline, str):
            raise ValueError('Invalid baseline')
        if type(adopt) is not bool or (adopt and baseline is not None):
            raise ValueError('Adoption applies only before a baseline exists')
        if proof is not None and (not isinstance(proof, str) or not PROOF.fullmatch(proof)):
            raise ValueError('Invalid proof')
        if proof is not None and not (isinstance(session, str) and SESSION.fullmatch(session)):
            return dict(ok=False, deferred=False, reason='session_unknown')
        _session(session)
    except ValueError:
        return dict(ok=False, deferred=False, reason='invalid_request')
    reason = 'failed'
    verified = proof is None
    adopted = None  # The unknown-owner title this call is authorized to replace.
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
                    if not verified:
                        native.verify(session, proof)
                        verified = True
                    current = native.read(session)
                    if current == desired:
                        return _done(desired, adopt, None)
                    if adopt and adopted is None:
                        adopted = current
                    if not adopt or current != adopted:
                        blocked = _blocked(current, baseline)
                        if blocked is not None:
                            return blocked
                    native.write(session, desired)
                    if native.read(session) != desired:
                        # A concurrent edit may own the title now; never write over it.
                        return dict(ok=False, deferred=True, reason='readback')
                    return _done(desired, adopt, current)
            except TitleTransportError as error:
                reason = error.reason
                if reason == 'session_unverified':
                    return dict(ok=False, deferred=False, reason=reason)
                if reason not in RETRIED:
                    break
    except Exception:
        reason = 'failed'
    return dict(ok=False, deferred=True, reason=reason)
