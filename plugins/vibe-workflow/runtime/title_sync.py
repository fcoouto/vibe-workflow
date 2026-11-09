"""Deterministic managed titles; user edits never change task authority."""
from copy import deepcopy
import re


from runtime.task_statuses import ATTENTION_STATES, STATES as STATUSES
from runtime.evidence import _digest
MAX_TITLE_BODY = 4096
MAX_STATUS_LENGTH = max(map(len, ATTENTION_STATES))
METER_PREFIX = '\uFFFB'  # Keeps the five cells in the compact Codex fallback font.
# Accept saved titles with routine labels, no meter prefix, or status at the end.
TITLE = re.compile(r'(?P<meter>\uFFFB?▰{0,5}▱{0,5}) (?:(?P<status>[A-Z ]+) · )?\[(?P<family>W[1-9][0-9]*)\] (?P<member>P|C) · (?P<topic>.+)')
LEGACY_TITLE = re.compile(r'(?P<meter>▰{0,5}▱{0,5}) \[(?P<family>W[1-9][0-9]*)\] (?P<member>P|C) · (?P<topic>.+) · (?P<status>[A-Z ]+)')


def format_title(family, member, topic, status, cells, *, user_attention=False):
    if not isinstance(family,str) or not re.fullmatch(r'W[1-9][0-9]*',family):
        raise ValueError('Invalid family code')
    if not isinstance(member,str) or not re.fullmatch(r'P|C',member):
        raise ValueError('Invalid member code')
    if not isinstance(topic,str) or not topic.strip() or topic!=topic.strip() or len(topic)>160 or not topic.isprintable() or ' · ' in topic:
        raise ValueError('Topic must be short, printable and contain no field separator')
    if status not in STATUSES or type(cells) is not int or not 0<=cells<=5:
        raise ValueError('Invalid canonical status or progress')
    filled=5 if status=='DONE' else min(cells,4)
    meter = '▰'*filled + '▱'*(5-filled) + ' '
    identity_topic = f'[{family}] {member} · {topic}'
    if len(meter) + MAX_STATUS_LENGTH + len(' · ') + len(identity_topic) > MAX_TITLE_BODY:
        raise ValueError('Managed title exceeds the supported length')
    label = f'{status} · ' if user_attention is True and status in ATTENTION_STATES else ''
    return METER_PREFIX + meter + label + identity_topic


def _parsed(title):
    match=(LEGACY_TITLE.fullmatch(title) or TITLE.fullmatch(title)) if isinstance(title,str) else None
    if match is None:return None
    fields=match.groupdict()
    status = fields['status']
    if len(fields['meter'].removeprefix(METER_PREFIX)) != 5 or (status is not None and status not in STATUSES):
        return None
    try:
        format_title(fields['family'], fields['member'], fields['topic'], status or 'WORKING', fields['meter'].count('▰'))
    except ValueError:
        return None
    fields['layout'] = 'status-after-meter' if match.re is TITLE else 'status-last'
    return fields


def reconcile(saved, current, task):
    """Return new presentation metadata and optional desired write; never edit task."""
    saved=deepcopy(saved)
    if saved['paused']:return saved,None
    if saved['pending'] is not None and current==saved['pending']:
        saved.update(applied=current,pending=None)
    if current!=saved['applied']:
        before,after=_parsed(saved['applied']),_parsed(current)
        pending = _parsed(saved['pending'])
        if pending and after and all(pending[k] == after[k] for k in ('meter','family','member','status','layout')):
            before = pending
        if before and after and all(before[k]==after[k] for k in ('meter','family','member','status','layout')):
            saved.update(topic=after['topic'],topic_workflow_owned=False,applied=current,pending=None)
        else:
            saved.update(paused=True,pending=None)
            return saved,None
    desired = format_title(saved['family'], saved['member'], saved['topic'], task['state'], saved['cells'],
                           user_attention=task.get('user_attention', False))
    return saved,None if current==desired else desired


def progress_cells(task):
    """Render accepted milestone progress or the unmeasured working indicator."""
    milestones = task.get('milestones', [])
    total = sum(item['weight'] for item in milestones)
    if not total:
        return 1 if task.get('state') == 'WORKING' else 0
    accepted = sum(item['weight'] for item in milestones if item['accepted'])
    return min(4, accepted * 5 // total)


class NativeTitles:
    """Bounded supported native protocol; never start a model turn."""
    def __enter__(self):
        import shutil
        import time
        from runtime.doctor import NativeProcess, ObservationError, validate_initialize
        executable = shutil.which('codex')
        if executable is None:
            raise OSError('Codex title transport unavailable')
        self.process = None
        self.sequence = 0
        try:
            self.process = NativeProcess([executable, 'app-server', '--listen', 'stdio://'], time.monotonic() + 1)
            response = self.request('initialize', {
                'clientInfo': {'name': 'workstreams-title-sync', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            validate_initialize(response)
            self.process.send({'method': 'initialized'})
        except Exception as error:
            if self.process is not None:
                self.process.close()
            if isinstance(error, ObservationError):
                raise OSError('Native title transport unavailable') from None
            raise
        return self

    def __exit__(self, *args):
        self.process.close()

    def request(self, method, params):
        from runtime.doctor import ObservationError
        self.sequence += 1
        try:
            return self.process.request(self.sequence, method, params)
        except ObservationError:
            raise OSError('Native title transport unavailable') from None

    def read(self, session):
        response = self.request('thread/read', {'threadId': session, 'includeTurns': False})
        thread = response.get('thread') if isinstance(response, dict) else None
        if not isinstance(thread, dict):
            raise ValueError('Malformed native title response')
        if thread.get('id') != session or not isinstance(thread.get('name'), (str, type(None))):
            raise ValueError('Native title identity mismatch')
        return thread.get('name') or ''

    def write(self, session, title):
        self.request('thread/name/set', {'threadId': session, 'name': title})


def _task(scope, client, session, *, revalidate=False, presentation=False):
    from runtime import state
    if revalidate:
        return state.task_status(scope, client, session)["task"]
    with state._database(scope) as db:
        if db is None:
            raise ValueError('Title synchronization requires an existing task')
        task = state._task(db, state._scope_id(scope), client, session)
        if task is None:
            raise ValueError('No bound workflow task')
        state._compatible(scope, task)
        if presentation and task['state'] == 'WAITING' and 'user_attention' not in task:
            from runtime import delivery
            if delivery.manual_merge_needs_user(scope, db, task):
                return dict(task, user_attention=True)
        return task


def _path(scope, client, session):
    from runtime import state
    state._text(session, 'native session', 4096)
    if client != 'codex' or scope is None or scope.runtime is None:
        raise ValueError('Supported enrolled Codex task required')
    with state._database(scope) as db:
        if db is None:raise ValueError('Managed titles require current canonical authority')
    return scope.runtime.state_dir / ('title-' + _digest([state._scope_id(scope), client, session]) + '.json')


def _load(path):
    import json
    from runtime.delegation import _read_bytes
    raw = _read_bytes(path)
    if raw is None:
        return None
    saved = json.loads(raw)
    from runtime import state
    state._fields(saved, ('identity', 'family', 'member', 'topic', 'cells', 'applied',
                          'pending', 'paused', 'notified', 'applied_revision'), ('topic_workflow_owned',))
    format_title(saved['family'], saved['member'], saved['topic'], 'WORKING', saved['cells'])
    if not isinstance(saved['applied'], str) or len(saved['applied'].removeprefix(METER_PREFIX)) > MAX_TITLE_BODY:
        raise ValueError('Invalid saved title observation')
    if saved['pending'] is not None and (not isinstance(saved['pending'], str)
            or len(saved['pending'].removeprefix(METER_PREFIX)) > MAX_TITLE_BODY):
        raise ValueError('Invalid pending title')
    if type(saved['paused']) is not bool or type(saved['notified']) is not bool:
        raise ValueError('Invalid title ownership state')
    if type(saved.get('topic_workflow_owned', False)) is not bool:
        raise ValueError('Invalid saved topic ownership')
    if saved['applied_revision'] is not None:
        state._integer(saved['applied_revision'], 'applied title revision', 1)
    return saved


def operate(scope, action, request, transport=None, *, force=False):
    """Explicit adoption/resumption or next-event repair of presentation only."""
    import fcntl
    import os
    from runtime import state
    from runtime.delegation import _atomic
    client, session = request.get('client'), request.get('session_id')
    path = _path(scope, client, session)
    if action not in {'configure', 'resume', 'sync', 'status'}:
        raise ValueError('Unsupported title action')
    if action in {'sync', 'status'} and not path.exists():
        return dict(ok=True, managed=False)
    task = _task(scope, client, session, revalidate=force or action != 'sync')
    if task is None:
        raise ValueError("No bound workflow task")
    if action in {'sync', 'status'}:
        state._fields(request, ('client', 'session_id'))
    if action in {'configure', 'resume'}:
        optional = ('topic', 'parent_session_id', 'member')
        if action == 'configure':
            optional += ('workflow_topic',)
        state._fields(request, ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision', 'reference'),
                      optional)
        state._integer(request['expected_revision'], 'title task revision', 1)
        if any(request[key] != task[key] for key in ('task_id', 'scope_ref')) or request['expected_revision'] != task['revision']:
            raise ValueError('Title request differs from current task binding or revision')
        state._text(request['reference'], 'title decision reference')
    workflow_topic = request.get('workflow_topic')
    if 'workflow_topic' in request and ('topic' in request or not isinstance(workflow_topic, str)):
        raise ValueError('Workflow topic must be text supplied without an explicit topic')
    descriptor = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        state._private(path.with_suffix('.lock'))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(ok=True, managed=True, pending=True, reason='busy')
        saved = _load(path)
        identity = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref', 'pins')}
        rebind = saved is not None and saved.get('identity') != identity
        if rebind:
            if action not in {'configure', 'resume'}:
                raise ValueError('Presentation belongs to a previous task; explicitly configure or resume it')
            if any(key in request for key in ('parent_session_id', 'member')):
                raise ValueError('Task replacement retains the native family/member binding')
            topic = request.get('topic', workflow_topic)
            if topic is not None:
                format_title(saved['family'], saved['member'], topic, task['state'], 0)
            history = path.with_name('title-history-' + _digest(saved) + '.json')
            _atomic(history, saved)
            saved = dict(saved, identity=identity, applied_revision=None)
            if request.get('topic') is not None:
                saved.update(topic=topic, topic_workflow_owned=False)
        if action == 'status':
            return dict(ok=True, managed=saved is not None, presentation=saved)
        if action == 'sync' and saved is not None and saved['paused'] and saved['notified']:
            return dict(ok=True, managed=True, pending=False, paused=True, notification=None)
        if action == 'sync' and not force and saved is not None and not saved['paused'] and saved['pending'] is None and saved.get('applied_revision') == task['revision']:
            return dict(ok=True, managed=True, pending=False, paused=saved['paused'], notification=None)
        if action == 'resume' and any(key in request for key in ('parent_session_id', 'member')):
            raise ValueError('Resume cannot change family or member identity')
        with (transport or NativeTitles)() as native:
            current = native.read(session)
            if len(current.removeprefix(METER_PREFIX)) > MAX_TITLE_BODY and (action == 'resume' or (action == 'configure' and saved is None)):
                raise ValueError('Native title is too long for managed adoption')
            if action == 'configure' and rebind:
                # Compare the prior managed observation before adopting the new task.
                saved, _ = reconcile(saved, current, task)
                if workflow_topic is not None and saved.get('topic_workflow_owned') and not saved['paused']:
                    saved['topic'] = workflow_topic
            elif action == 'configure':
                if saved is not None:
                    raise ValueError('Title already managed; use explicit resume')
                from scripts.workstreams_family import reserve
                from pathlib import Path
                import sqlite3
                parent = request.get('parent_session_id', session)
                state._text(parent, 'native family parent', 4096)
                member = request.get('member', 'P')
                if (member == 'P') != (parent == session):
                    raise ValueError('Member role differs from declared family parent')
                topic = request.get('topic', workflow_topic)
                format_title('W1', member, topic, task['state'], 0)
                try:
                    family = reserve(Path.home() / '.vibe-workflow' / 'workstreams-families.sqlite3', client, parent)
                except sqlite3.Error:
                    raise OSError('Family code register unavailable') from None
                saved = dict(identity=identity, family=family, member=member, topic=topic,
                    topic_workflow_owned=workflow_topic is not None,
                    cells=0, applied=current, pending=None, paused=False, notified=False, applied_revision=None)
            elif action == 'resume':
                if saved is None:
                    raise ValueError('Title is not managed')
                if 'topic' in request:
                    format_title(saved['family'], saved['member'], request['topic'], task['state'], 0)
                    saved.update(topic=request['topic'], topic_workflow_owned=False)
                saved.update(paused=False, applied=current, pending=None, notified=False)
            task = _task(scope, client, session, presentation=True)
            if {key: task[key] for key in identity} != identity:
                raise ValueError('Task binding changed during title observation')
            saved['cells'] = progress_cells(task)
            saved, desired = reconcile(saved, current, task)
            notification = None
            if desired is not None:
                _atomic(path, dict(saved, pending=desired))
                ready_to_write = False
                try:
                    # Re-read immediately before mutation to detect edits during preparation.
                    if native.read(session) != current:
                        return dict(ok=True, managed=True, pending=True, reason='title changed before write')
                    latest = _task(scope, client, session)
                    if latest['revision'] != task['revision'] or any(latest[key] != value for key, value in identity.items()):
                        return dict(ok=True, managed=True, pending=True, reason='task advanced')
                    ready_to_write = True
                finally:
                    if not ready_to_write:
                        _atomic(path, dict(saved, applied_revision=None))
                native.write(session, desired)
                observed = native.read(session)
                if observed == desired:
                    saved.update(applied=observed, pending=None, applied_revision=task['revision'])
                else:
                    saved.update(paused=True, pending=None)
            if saved['paused'] and not saved['notified']:
                saved['notified'] = True
                notification = 'Manual title edit preserved. Automatic title updates are paused until you request resumption.'
            if not saved['paused'] and saved['pending'] is None:
                saved['applied_revision'] = task['revision']
            _atomic(path, saved)
            return dict(ok=True, managed=True, pending=saved['pending'] is not None,
                        paused=saved['paused'], notification=notification)
    finally:
        os.close(descriptor)


def repair(scope, client, session, *, force=False):
    """Hook-safe presentation failure; retain pending data for the next event."""
    try:
        return operate(scope, 'sync', dict(client=client, session_id=session), force=force)
    except Exception:
        try:
            managed = _path(scope, client, session).exists()
        except Exception:
            managed = False
        return dict(ok=False, managed=managed, pending=True, reason='Title synchronization deferred')
