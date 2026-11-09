"""Explicit worker presentation, independent of execution authority and admission."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


ROLES = frozenset({'Coordinator', 'Scout', 'Builder', 'Tester', 'Reviewer', 'Scribe', 'Investigator'})
LIMIT = 128 * 1024
LABEL_LIMIT = 2 * 4096 + len(' · ')


def text(value, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise ValueError('invalid_visibility_text')
    return value


def declaration(value):
    if (not isinstance(value, dict) or not {'version', 'role', 'specialty'} <= set(value)
            or set(value) - {'version', 'role', 'specialty', 'custom_role_name'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['role'], str) or value['role'] not in ROLES):
        raise ValueError('invalid_visibility_declaration')
    text(value['specialty'])
    if 'custom_role_name' in value:
        text(value['custom_role_name'])
    return dict(value)


def optional_declaration(value):
    try:
        return declaration(value)
    except ValueError:
        return None


def private_path(path):
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve(strict=True) != path.parent or path.is_symlink():
        raise ValueError('unsafe_visibility_path')
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError('unsafe_visibility_path')
    return path


def read(path):
    path = private_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('unsafe_visibility_file')
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('visibility_size_limit')
    from runtime.startup import json_object
    return json_object(raw, limit=LIMIT)


def save(path, value, *, exclusive=False):
    path = private_path(path)
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    if len(raw) > LIMIT:
        raise ValueError('visibility_size_limit')
    fd, temporary = tempfile.mkstemp(prefix='.visibility-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path):
    path = private_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('unsafe_visibility_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


PACKAGE = Path(__file__).resolve().parents[1]
CONTEXT_FIELDS = {'parent_session_id', 'checkout', 'task_name', 'visibility'}
CONTEXT_OPTIONAL = {'version', 'prompt_marker', 'ledger_assignment_digest', 'observed'}


def context_path(path, checkout=None, plugin_root=None):
    path = private_path(path)
    protected = [PACKAGE]
    if checkout is not None:
        protected.append(Path(checkout).resolve(strict=True))
    if plugin_root is not None:
        protected.append(Path(plugin_root).resolve(strict=True))
    if any(path.is_relative_to(root) for root in protected) or any(
            os.path.lexists(parent / '.git') for parent in path.parents):
        raise ValueError('native_visibility_context_must_be_private')
    return path


def prepare_native(path, request, *, reuse=False, observed=None):
    if not isinstance(request, dict) or not CONTEXT_FIELDS <= set(request) <= CONTEXT_FIELDS | {'ledger_assignment_digest'}:
        raise ValueError('invalid_native_visibility_request')
    parent = request['parent_session_id']
    if not isinstance(parent, str) or str(uuid.UUID(parent)) != parent:
        raise ValueError('invalid_native_parent')
    checkout = Path(request['checkout'])
    if not checkout.is_absolute() or checkout.resolve(strict=True) != checkout or not checkout.is_dir():
        raise ValueError('invalid_native_checkout')
    text(request['task_name'], 128)
    context = dict(request, version=1, visibility=declaration(request['visibility']))
    if 'ledger_assignment_digest' in request:
        digest = request['ledger_assignment_digest']
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid_visibility_assignment_digest')
    else:
        context['prompt_marker'] = 'VCW worker visibility ' + uuid.uuid4().hex
    path = context_path(path, checkout)
    if reuse and path.exists():
        existing = read_context(path)
        if ({key: value for key, value in context.items() if key != 'prompt_marker'} !=
                {key: value for key, value in existing.items() if key not in {'prompt_marker', 'observed'}}):
            raise ValueError('native_visibility_context_changed')
        context = existing
    else:
        if observed is not None:
            context['observed'] = observed
        save(path, context, exclusive=True)
    return dict(prepared=True, visibility=context['visibility'],
                **({'prompt_marker': context['prompt_marker']} if 'prompt_marker' in context else {}))


def assignment_context_path(request, context=None, context_directory=None):
    path = context
    if path is None:
        directory = context_directory or default_context_directory(create=True)
        path = hook_context_path(directory, request['parent_session_id'], request['task_name'])
        context_path(path.parent, request['checkout']).mkdir(mode=0o700, exist_ok=True)
    return context_path(path, request['checkout'])


def prepare_context(request, *, context=None, context_directory=None, reuse=False):
    path = assignment_context_path(request, context, context_directory)
    return path, prepare_native(path, request, reuse=reuse)


def prepare_assignment(request, *, context=None, context_directory=None):
    """Assemble one native brief; display failure preserves its original spawn inputs."""
    if 'worker_id' in request:
        from runtime.habblaud import prepare_reuse
        return prepare_reuse(request, context=context, context_directory=context_directory)
    result = dict(prepared=False, task_name=request.get('task_name'), message=request.get('message'))
    try:
        message = request['message']
        if not isinstance(message, str) or len(message.encode('utf-8')) > LIMIT:
            raise ValueError('invalid_native_assignment_message')
        brief = {key: value for key, value in request.items() if key != 'message'}
        if 'visibility' not in brief:
            path = context if context is not None else hook_context_path(
                context_directory or default_context_directory(), brief['parent_session_id'], brief['task_name'])
            brief['visibility'] = read_context(context_path(path, brief['checkout']))['visibility']
        brief['visibility'] = declaration(brief['visibility'])
        path, prepared = prepare_context(brief, context=context,
                                         context_directory=context_directory, reuse=True)
        marker = prepared.get('prompt_marker')
        if marker is not None and message.partition('\n')[0] != marker:
            message = marker + '\n' + message
        return result | prepared | dict(context=str(path), message=message)
    except (ValueError, OSError, TypeError, RuntimeError, KeyError):
        return result | dict(reason='native_visibility_unavailable')


def prepare_managed(checkout, parent, report, *, prompt=None, context_directory=None):
    """Consume a prepared assignment or same-child intent outside its transaction."""
    request = dict(parent_session_id=parent, checkout=str(checkout), task_name=report.get('task_name'),
                   message=report.get('prompt') if prompt is None else prompt)
    if 'visibility' in report:
        request['visibility'] = report['visibility']
    if 'worker_id' in report:
        request['worker_id'] = report['worker_id']
    else:
        request['ledger_assignment_digest'] = report.get('ledger_assignment_digest', report.get('assignment_digest'))
    prepared = prepare_assignment(request, context_directory=context_directory)
    return report | dict(visibility_preparation={key: prepared[key] for key in ('prepared', 'context', 'reason')
                                                if key in prepared})


def read_context(path):
    context = read(path)
    if (not isinstance(context, dict) or type(context.get('version')) is not int or context['version'] != 1
            or not CONTEXT_FIELDS <= set(context) or set(context) - (CONTEXT_FIELDS | CONTEXT_OPTIONAL)
            or len({'prompt_marker', 'ledger_assignment_digest'} & set(context)) != 1):
        raise ValueError('invalid_native_visibility_context')
    return context


def hook_context_path(directory, parent, task_name):
    if not isinstance(parent, str) or str(uuid.UUID(parent)) != parent:
        raise ValueError('invalid_native_parent')
    key = hashlib.sha256(text(task_name, 128).encode()).hexdigest()
    return Path(directory) / parent / (key + '.json')


def default_context_directory(*, create=False):
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve(strict=True)
    directory = home / 'vibe-worker-visibility' / 'contexts'
    if create:
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        private_path(directory).mkdir(mode=0o700, exist_ok=True)
    return directory
