"""Optional Habblaud delivery for native and routed assignments."""

import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from runtime import worker_visibility as visibility
from runtime.worker_visibility import (
    LABEL_LIMIT, LIMIT, context_path, declaration, hook_context_path, locked, private_path, read, read_context, save, text,
)
from runtime.adapters import codex
from runtime.startup import StartupError, json_object


MANAGED_DISPLAY_FIELD = 'vcw_display_label'
TERMINAL = frozenset({'accepted', 'rejected', 'failed', 'unresolved', 'completed', 'cancelled'})


def display_label(value, model_display_name):
    if value is None:
        return 'Subagente (Codex)'
    value = declaration(value)
    # Retain the existing model-display normalization; custom names remain exact.
    model = (' '.join(''.join(c for c in model_display_name if c.isprintable()).split())
             if isinstance(model_display_name, str) else '')
    return text(value.get('custom_role_name', value['role']) + ' · ' + text(model or 'Model unknown'), LABEL_LIMIT)


def transport(plugin_root):
    """Load only the already-installed optional transport, without its job store."""
    root = str(Path(plugin_root).resolve(strict=True))
    sys.path.insert(0, root)
    try:
        module = importlib.import_module('local_model_delegation.habblaud')
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError('visibility_adapter_mismatch')
        return module
    finally:
        sys.path.remove(root)


def publisher(module, endpoint, account, binding, visibility, receipt, *, managed_native_label=False):
    """Freeze before delivery; reuse actual native or explicitly external identity."""
    visibility = declaration(visibility) if visibility is not None else None
    binding = dict(binding)
    if set(binding) != {'parent', 'agent_id', 'label', 'cwd'}:
        raise ValueError('invalid_visibility_binding')
    for key, value in binding.items():
        text(value, LABEL_LIMIT if key == 'label' else 4096)
    text(account, 200)
    client = module.Client(endpoint)
    receipt = private_path(receipt)
    expected_roles = ({binding['label']} if visibility is not None else
                      {'Subagente (Codex)', 'Subagent (Codex)', 'Sous-agent (Codex)'})

    def observed_binding():
        snapshot = client.snapshot(account)
        matches = [item for item in snapshot['agents'] if item.get('id') == account + ':' + binding['agent_id']]
        if len(matches) > 1 or any(item.get('parentId') != account + ':' + binding['parent']
                or item.get('role') not in expected_roles for item in matches):
            raise ValueError('habblaud_binding_mismatch')
        return bool(matches)

    def publish(phase):
        status = dict(version=1, binding=binding, visibility=visibility, account=account,
                      delivered=False, phase=text(phase, 256), started=False, stopped=False)
        try:
            with locked(receipt.with_name(receipt.name + '.lock')):
                old = read(receipt)
                if old is not None and 'binding' in old:
                    if (not set(status) <= set(old) <= set(status) | {'reason'}
                            or type(old['version']) is not int or old['version'] != 1
                            or any(type(old[key]) is not bool for key in ('delivered', 'started', 'stopped'))):
                        return status | dict(reason='invalid_visibility_receipt')
                    text(old['phase'], 256)
                    if 'reason' in old:
                        text(old['reason'], 256)
                    old_binding = old['binding']
                    if visibility is None and isinstance(old_binding, dict):
                        old_label = old_binding.get('label')
                        if isinstance(old_label, str) and old_label.startswith('Role not reported · '):
                            old_binding = old_binding | dict(label=binding['label'])
                    if (old_binding != binding or old['visibility'] != visibility
                            or old['account'] != account):
                        return status | dict(reason='visibility_binding_changed')
                    status.update(started=old.get('started') is True, stopped=old.get('stopped') is True)
                elif old is not None:
                    if (type(old.get('delivered')) is not bool or set(old) - {
                            'delivered', 'phase', 'reason', 'agent_id', 'parent', 'account', 'observed_at'}):
                        return status | dict(reason='invalid_visibility_receipt')
                    if old['delivered']:
                        if (old.get('agent_id'), old.get('parent'), old.get('account')) != (
                                binding['agent_id'], binding['parent'], account):
                            return status | dict(reason='visibility_binding_changed')
                        status['started'] = True
                # Freeze even when the first delivery fails.
                save(receipt, status)
                if 'guardian' in binding['label'].casefold():
                    status['reason'] = 'unsupported_display_label'
                    save(receipt, status)
                    return status
                try:
                    present = observed_binding()
                    if not present and not status['stopped'] and (not status['started'] or phase not in TERMINAL):
                        start = module._event(binding, 'SubagentStart')
                        if managed_native_label:
                            start[MANAGED_DISPLAY_FIELD] = binding['label']
                        client.event(account, start)
                        status['started'] = True
                        save(receipt, status)
                        present = observed_binding()
                    if not present:
                        status['reason'] = 'visibility_unconfirmed'
                    else:
                        status['started'] = True
                        if not status['stopped']:
                            client.event(account, module._event(binding, 'PreToolUse', phase))
                            if phase in TERMINAL:
                                client.event(account, module._event(binding, 'SubagentStop'))
                                status['stopped'] = True
                        status['delivered'] = True
                except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError) as error:
                    reason = str(error)
                    status['reason'] = {
                        'habblaud_binding_mismatch': 'habblaud_binding_mismatch',
                        'habblaud_event_too_large': 'unsupported_display_label',
                    }.get(reason, 'visibility_unavailable')
                save(receipt, status)
                return status
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
            return status | dict(delivered=False, reason='visibility_unavailable')

    return publish


def register_external(*, module, endpoint: str, account: str, parent: str, checkout: str,
                      run_id: str, execution_route: str, model_display_name: str,
                      visibility: dict | None, state_directory: Path) -> dict:
    """Publish display metadata using the transport and identity verified by the host."""
    try:
        if (str(UUID(text(parent, 36))) != parent
                or re.fullmatch(r'[A-Za-z0-9_-]+', text(execution_route, 256)) is None):
            raise ValueError('invalid_external_visibility_identity')
        text(run_id, 256)
        external_id = str(uuid5(NAMESPACE_URL, f'vcw:{execution_route}:{parent}:{run_id}'))
        metadata = declaration(visibility) if visibility is not None else None
        badge_role = {key: metadata[key] for key in ('version', 'role', 'specialty')} if metadata else None
        binding = dict(parent=parent, agent_id=external_id, cwd=checkout,
                       label=display_label(badge_role, text(model_display_name, 160)))
        receipt = state_directory / f'habblaud-{external_id}.json'
        status = publisher(module, endpoint, account, binding, metadata, receipt)('completed')
        return {key: status[key] for key in ('delivered', 'reason') if key in status}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, ImportError):
        return dict(delivered=False, reason='visibility_unavailable')


def specific_native_role(value):
    return isinstance(value, str) and value.strip().casefold() not in {'', 'default'}


def native_label(metadata, observation):
    # Prior observations froze the raw name before the catalog lookup was available.
    model = observation.get('model_display_name')
    if not model:
        model = observation['configured_model']
        if 'model_display_name' in observation and isinstance(model, str) and '-' in model and ' ' not in model:
            model = model.replace('-', ' ').title()
            if model.startswith('Gpt '):
                model = 'GPT-' + model[4:]
    composite = display_label(metadata, model)
    label = visibility.text(observation.get('forwarded_label', composite), visibility.LABEL_LIMIT)
    return label, label == composite


def enrich_start(event, directory, checkout, executable):
    if event.get('hook_event_name') != 'SubagentStart':
        return event
    try:
        cwd = Path(event['cwd'])
        if str(cwd) != event['cwd']:
            return event
        parent, child = event['session_id'], event['agent_id']
        executable = Path(executable)
        if (not executable.is_absolute() or executable.resolve(strict=True) != executable
                or not executable.is_file() or executable.stat().st_mode & 0o022):
            return event
        observed = codex.observe_worker_visibility(cwd, parent, child, deadline=time.monotonic() + 1,
                                                   executable=str(executable), resolve_hook_parent=True)
        task = observed['task_name']
        checkouts = {observed.get('parent_checkout', observed['checkout']),
                     observed.get('worker_checkout', observed['checkout'])}
        if (not task or observed.get('hook_session_id', observed['parent_session_id']) != parent
                or observed['worker_id'] != child or observed['checkout'] != str(cwd)
                or (checkout is not None and str(checkout) not in checkouts)):
            return event
        parent = observed['parent_session_id']
        path = context_path(hook_context_path(directory, parent, task), cwd)
        if not path.is_file():
            return event
        with visibility.locked(path.with_name(path.name + '.lock')):
            context = read_context(path)
            if ((context['parent_session_id'], context['task_name']) != (parent, task)
                    or context['checkout'] not in checkouts):
                return event
            context_path(path, context['checkout'])
            prior = context.get('observed')
            if prior is not None and prior['worker_id'] != child:
                return event
            incoming_role = event.get('agent_role')
            label, managed = native_label(context['visibility'], prior or observed)
            # The downstream hook can reduce every string to 200 UTF-16 units.
            if (len(label.encode('utf-16-le')) > 400 or label.strip() != label
                    or label[0].upper() != label[0] or 'guardian' in label.casefold()):
                return event
            if prior is None:
                visibility.save(path, context | dict(observed=observed | dict(checkout=context['checkout'],
                                                                           forwarded_label=label)))
            role = incoming_role if specific_native_role(incoming_role) else label
            return event | dict(session_id=parent, agent_role=role, agent_type=label) | (
                {MANAGED_DISPLAY_FIELD: label} if managed else {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return event


def forward_start(directory, checkout, executable, command):
    raw = sys.stdin.buffer.read(visibility.LIMIT + 1)
    if len(raw) <= visibility.LIMIT:
        try:
            event = json_object(raw, limit=visibility.LIMIT)
            enriched = enrich_start(event, directory, checkout, executable)
            if enriched != event:
                raw = json.dumps(enriched, ensure_ascii=False).encode()
        except (ValueError, RecursionError):
            pass
        return subprocess.run(command, input=raw).returncode
    with subprocess.Popen(command, stdin=subprocess.PIPE) as child:
        try:
            child.stdin.write(raw)
            shutil.copyfileobj(sys.stdin.buffer, child.stdin)
            child.stdin.close()
        except BrokenPipeError:
            pass
        return child.wait()


def _original_spawn_binding(context, parent_turn_id, spawn_item_id):
    if parent_turn_id is None and spawn_item_id is None:
        return None
    if context is None or not parent_turn_id or not spawn_item_id:
        raise ValueError('native_visibility_spawn_proof_unavailable')
    return {key: context[key] for key in (
        'task_name', 'visibility', 'ledger_assignment_digest', 'prompt_marker') if key in context
    } | dict(parent_turn_id=parent_turn_id, spawn_item_id=spawn_item_id)


def publish_native(path, worker_id, phase, plugin_root, endpoint, account, *, parent_turn_id=None, spawn_item_id=None):
    """Bind only to a real child of the declared parent; continuation reuses it."""
    try:
        path = context_path(path, plugin_root=plugin_root)
        context = read_context(path)
        context_path(path, context['checkout'], plugin_root)
        metadata = visibility.declaration(context['visibility'])
        spawn_binding = _original_spawn_binding(context, parent_turn_id, spawn_item_id)
        with visibility.locked(path.with_name(path.name + '.lock')):
            if visibility.read(path) != context:
                return dict(delivered=False, reason='native_visibility_context_changed')
            prior = context.get('observed')
            if prior is not None and prior['worker_id'] != worker_id:
                return dict(delivered=False, reason='native_visibility_identity_changed')
            try:
                observed = codex.observe_worker_visibility(context['checkout'], context['parent_session_id'], worker_id,
                                                           spawn_binding=spawn_binding)
            except StartupError:
                return dict(delivered=False, reason='native_visibility_identity_unobserved')
            if observed['task_name'] != context['task_name']:
                return dict(delivered=False, reason='native_visibility_identity_unobserved')
            if prior is None:
                visibility.save(path, context | dict(observed=observed))
            label, managed = native_label(metadata, prior or observed)
            binding = dict(parent=observed['parent_session_id'], agent_id=observed['worker_id'],
                           label=label, cwd=observed['checkout'])
            publish = publisher(transport(plugin_root), endpoint, account, binding, metadata,
                                path.with_name(path.name + '.delivery.json'), managed_native_label=managed)
            return publish(phase)
    except (ValueError, OSError, ImportError, KeyError, TypeError, AttributeError, RuntimeError):
        return dict(delivered=False, reason='native_visibility_unavailable')


def _saved_native_binding(context):
    observed = context['observed']
    keys = ('parent_session_id', 'checkout', 'task_name')
    if any(observed[key] != context[key] for key in keys):
        raise ValueError('native_visibility_identity_changed')
    parent, child = observed['parent_session_id'], observed['worker_id']
    if str(UUID(parent)) != parent or str(UUID(child)) != child or child == parent:
        raise ValueError('native_visibility_identity_changed')
    return observed


def prepare_reuse(request, *, context=None, context_directory=None):
    """Capture a missing role for a verified existing child, without changing its prompt."""
    result = dict(prepared=False, task_name=request.get('task_name'), message=request.get('message'))
    try:
        message, child = request['message'], request['worker_id']
        keys = ('parent_session_id', 'checkout', 'task_name')
        if (set(request) - {*keys, 'message', 'worker_id', 'visibility', 'parent_turn_id', 'spawn_item_id'}
                or not isinstance(message, str) or len(message.encode('utf-8')) > LIMIT
                or str(UUID(child)) != child):
            raise ValueError('invalid_native_visibility_request')
        identity = {key: request[key] for key in keys}
        path = visibility.assignment_context_path(identity, context, context_directory)
        with locked(path.with_name(path.name + '.lock')):
            existing = read_context(path) if path.exists() else None
            if existing is not None and any(existing[key] != value for key, value in identity.items()):
                raise ValueError('native_visibility_context_changed')
            metadata = declaration(existing['visibility'] if existing else request.get('visibility'))
            if request.get('visibility') is not None and declaration(request['visibility']) != metadata:
                raise ValueError('native_visibility_context_changed')
            spawn_binding = _original_spawn_binding(existing, request.get('parent_turn_id'), request.get('spawn_item_id'))
            if existing is not None and 'observed' in existing:
                if _saved_native_binding(existing)['worker_id'] != child:
                    raise ValueError('native_visibility_identity_changed')
            else:
                observed = codex.observe_worker_visibility(identity['checkout'], identity['parent_session_id'], child,
                                                           spawn_binding=spawn_binding)
                if observed['worker_id'] != child or any(observed[key] != value for key, value in identity.items()):
                    raise ValueError('native_visibility_identity_changed')
                label, _ = native_label(metadata, observed)
                observed = observed | dict(forwarded_label=label)
                if existing is None:
                    visibility.prepare_native(path, identity | dict(visibility=metadata), observed=observed)
                else:
                    save(path, existing | dict(observed=observed))
        return result | dict(prepared=True, context=str(path), visibility=metadata)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, StartupError):
        return result | dict(reason='native_visibility_unavailable')


def restore_badge(path, plugin_root, endpoint, account):
    """Replay saved display metadata only. The receiver verifies native lineage."""
    try:
        path = context_path(path, plugin_root=plugin_root)
        context = read_context(path)
        context_path(path, context['checkout'], plugin_root)
        observed = _saved_native_binding(context)
        metadata = declaration(context['visibility'])
        label, _ = native_label(metadata, observed)
        prefix = metadata.get('custom_role_name', metadata['role']) + ' · '
        if (not label.startswith(prefix) or not label[len(prefix):].strip()
                or 'guardian' in label.casefold()):
            return dict(delivered=False, reason='unsupported_display_label')
        client = transport(plugin_root).Client(endpoint)
        client.event(text(account, 200), dict(hook_event_name='SubagentBadge',
            session_id=observed['parent_session_id'], agent_id=observed['worker_id'], vcw_display_label=label))
        return dict(delivered=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, ImportError):
        return dict(delivered=False, reason='native_visibility_unavailable')


def openrouter_publisher(plugin_root, run, endpoint, account, declaration_for_brief):
    receipt = run / 'visibility.json'
    try:
        brief = json.loads((run / 'brief.json').read_text())
        metadata = declaration_for_brief(brief)
        display_role = {key: value for key, value in metadata.items() if key != 'custom_role_name'} if metadata else None
        model = brief.get('model_display_name') or brief['model']
        previous = read(receipt)
        if previous and 'binding' in previous:
            # A published run keeps its original label when verification resumes.
            legacy_label = display_label(metadata, model)
            if previous['binding'].get('label') == legacy_label:
                display_role = metadata
        binding = {'parent': brief['coordinator_chat_id'], 'agent_id': str(uuid5(NAMESPACE_URL, str(run))),
                   'label': display_label(display_role, model), 'cwd': brief['source_root']}
        return publisher(transport(plugin_root), endpoint, account, binding, metadata, receipt)
    except (ValueError, OSError, ImportError, AttributeError, TypeError, KeyError, RecursionError):
        try:
            save(receipt, {'delivered': False, 'reason': 'adapter_unavailable'}, exclusive=True)
        except (ValueError, OSError):
            pass
        return None



def publish_usage_badge(habblaud, *, model, thread_id, checkout, run_id, execution_route, visibility, state):
    from runtime.usage_budget import fields, require
    from runtime import worker_visibility as visibility_schema

    # Older display callers supplied a role here. Prefer the assignment's role when available.
    fields({key: value for key, value in habblaud.items() if key != 'visibility'},
           'plugin_root endpoint account model model_display_name')
    plugin_root = Path(habblaud['plugin_root'])
    require(plugin_root.is_absolute() and plugin_root.resolve(strict=True) == plugin_root
            and habblaud['model'] == model, 'visibility_unavailable')
    parent = str(UUID(thread_id))
    require(parent == thread_id, 'visibility_parent_unavailable')
    account = text(habblaud['account'], 200)
    module = transport(plugin_root)
    endpoint = habblaud['endpoint']
    agents = module.Client(endpoint).snapshot(account)['agents']
    matches = [agent for agent in agents if agent.get('id') == account + ':' + parent]
    require(len(matches) == 1 and matches[0].get('kind') == 'main'
            and matches[0].get('account') == account and matches[0].get('provider') == 'codex',
            'visibility_parent_unavailable')
    return register_external(
        module=module, endpoint=endpoint, account=account, parent=parent, checkout=checkout,
        run_id=run_id, execution_route=execution_route,
        model_display_name=habblaud['model_display_name'],
        visibility=visibility or visibility_schema.optional_declaration(habblaud.get('visibility')),
        state_directory=state)
