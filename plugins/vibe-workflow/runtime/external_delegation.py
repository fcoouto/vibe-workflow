"""Shared scoped packets; native callers own truthful prose provenance and review.

No transport, discovery, retry, funding or acceptance authority lives here.
"""
import hashlib
from pathlib import Path

from runtime import onboarding
from runtime.usage_admission import digest

LIMIT = 65536
SOURCE_LIMIT = 128 * 1024


def require(value, reason):
    if not value:
        raise ValueError('external_' + reason)


def _path(value):
    require(isinstance(value, str) and value and str(Path(value)) == value
            and not Path(value).is_absolute() and '..' not in Path(value).parts,
            'invalid_source_path')
    return value


def validate_permission(permission):
    require(isinstance(permission, dict) and set(permission) == {
        'version', 'approval_ref', 'classes', 'sources', 'exclusions', 'destinations',
        'automatic', 'funding_ref', 'valid_until'}, 'invalid_permission')
    require(type(permission['version']) is int and permission['version'] == 1
            and type(permission['automatic']) is bool, 'invalid_permission')
    for key in ('approval_ref', 'funding_ref'):
        require(isinstance(permission[key], str) and permission[key].strip(), 'invalid_permission')
    require(permission['valid_until'] is None or type(permission['valid_until']) is int,
            'invalid_permission')
    for key in ('classes', 'sources', 'exclusions', 'destinations'):
        require(isinstance(permission[key], list), 'invalid_permission')
    require(permission['classes'] and all(isinstance(item, str) and item for item in permission['classes']),
            'invalid_permission')
    for path in permission['sources'] + permission['exclusions']:
        _path(path)
    require(permission['destinations'], 'invalid_destination')
    for route in permission['destinations']:
        require(isinstance(route, dict) and set(route) == {'service', 'mode', 'model', 'provider', 'effort'}
                and route['service'] in ('openai-api', 'openrouter')
                and route['mode'] in ('proposal', 'worker')
                and all(isinstance(route[key], str) and route[key] for key in ('model', 'provider'))
                and (route['effort'] is None or isinstance(route['effort'], str)), 'invalid_destination')


def select(explicit, local_preferred, candidates, preference=None, *, api_only=False):
    """Offline consideration only; local preference includes its wait/native fallback."""
    if explicit == 'native':
        return dict(route='native', reason='explicit_choice')
    if explicit is None and local_preferred:
        return dict(route='local-preference', reason='local_wait_or_native_fallback')
    eligible = [row['route'] for row in candidates if row.get('enabled') is True
                and row.get('suitable') is True and (explicit is not None or row.get('automatic') is True)]
    wanted = explicit if explicit is not None else preference
    if wanted in eligible:
        return dict(route=wanted, reason='explicit_choice' if explicit else 'saved_preference')
    if explicit is None and len(eligible) == 1:
        return dict(route=eligible[0], reason='only_approved_suitable_route')
    return dict(route='paused' if api_only else 'native', reason='route_unavailable_or_unresolved')


def _read(root, permission, path):
    path = _path(path)
    target = root / path
    require(target.resolve() == target and target.is_file(), 'unsafe_source')
    require(all((root / area).resolve() == root / area
                for area in permission['sources'] + permission['exclusions']), 'unsafe_source_area')
    def contains(area):
        selected = root / area
        return selected.exists() and any(parent.samefile(selected) for parent in (target, *target.parents))
    require(any(contains(area) for area in permission['sources'])
            and not any(contains(area) for area in permission['exclusions']), 'source_denied')
    info = target.stat()
    raw = onboarding._read_file(target, SOURCE_LIMIT,
        expected_binding=dict(device=info.st_dev, inode=info.st_ino))
    return raw, dict(path=path, sha256=hashlib.sha256(raw).hexdigest())


def prepare(root, permission, parts):
    """Build text from scoped bytes. Text parts explicitly name derived sources."""
    validate_permission(permission)
    root = Path(root)
    require(root.is_absolute() and root.resolve() == root, 'invalid_checkout')
    require(isinstance(parts, list) and 0 < len(parts) <= 32, 'invalid_parts')
    bound, texts, snapshots = [], [], {}
    for part in parts:
        require(isinstance(part, dict), 'unknown_provenance')
        if part.get('kind') == 'source':
            require({'kind', 'path'} <= set(part) <= {'kind', 'path', 'sha256', 'start', 'end'}, 'invalid_part')
            raw, source = _read(root, permission, part['path'])
            require('sha256' not in part or part['sha256'] == source['sha256'], 'source_changed')
            lines = raw.decode('utf-8').splitlines(keepends=True)
            start, end = part.get('start', 1), part.get('end', max(1, len(lines)))
            require(type(start) is int and type(end) is int and 1 <= start <= end <= max(1, len(lines)),
                    'invalid_excerpt')
            bound.append(dict(kind='source', **source, start=start, end=end))
            texts.append(f"Source: {source['path']}:{start}-{end}\n" + ''.join(lines[start-1:end]))
            snapshots[source['path']] = source['sha256']
        elif part.get('kind') == 'text':
            require(set(part) == {'kind', 'text', 'sources'} and isinstance(part['text'], str)
                    and isinstance(part['sources'], list), 'unknown_provenance')
            sources = []
            for item in part['sources']:
                raw, source = _read(root, permission, item['path'] if isinstance(item, dict) else item)
                require(not isinstance(item, dict) or item == source, 'source_changed')
                sources.append(source)
                snapshots[source['path']] = source['sha256']
            bound.append(dict(kind='text', text=part['text'], sources=sources))
            texts.append(part['text'])
        else:
            raise ValueError('external_unknown_provenance')
    text = '\n\n'.join(texts)
    require(0 < len(text.encode()) <= LIMIT, 'packet_size')
    return dict(text=text, binding=dict(permission_digest=digest(permission), parts=bound),
                snapshots=[dict(path=str(root / path), sha256=sha) for path, sha in sorted(snapshots.items())])


def check(root, permission, binding, *, service, mode, model, provider, effort, class_name, now):
    require(permission is not None, 'permission_required')
    validate_permission(permission)
    require(isinstance(binding, dict) and binding.get('permission_digest') == digest(permission),
            'permission_changed')
    require(type(binding.get('automatic')) is bool and type(binding.get('local_preferred')) is bool,
            'selection_required')
    require(not binding['local_preferred'], 'local_preference')
    require(not binding['automatic'] or permission['automatic'], 'automatic_selection_denied')
    require(permission['valid_until'] is None or now < permission['valid_until'], 'permission_expired')
    require(class_name in permission['classes'], 'task_class_denied')
    route = dict(service=service, mode=mode, model=model, provider=provider, effort=effort)
    require(route in permission['destinations'], 'destination_denied')
    return prepare(root, permission, binding['parts'])


def native_review(binding):
    require(isinstance(binding, dict), 'independent_review_required')
    require(binding.get('integration_available') is True
            and isinstance(binding.get('coordinator'), str) and binding['coordinator']
            and isinstance(binding.get('reviewer'), str) and binding['reviewer']
            and binding['coordinator'] != binding['reviewer'], 'independent_review_required')
