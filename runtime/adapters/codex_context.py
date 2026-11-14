"""Source-backed Codex hook context formats; no caller text becomes native evidence."""

import hashlib
import xml.etree.ElementTree as xml


def native_hook_context(records, *, expected_context, turn_id):
    """Observe exact harness-hook context without inventing hook events or source paths."""
    from runtime.adapters.codex import _review_id
    from runtime.doctor import ObservationError
    from runtime.startup import MAX_INPUT, StartupError, _canonical_freeze, _canonical_plain
    try:
        if not isinstance(expected_context, str) or not expected_context or len(expected_context.encode()) > MAX_INPUT:
            raise ValueError
        data = _canonical_plain(records)
        found = []
        for row in data['records']:
            item = row['payload']
            if item['type'] != 'message' or item.get('role') not in ('user', 'developer'):
                continue
            content = item.get('content')
            passthrough = item.get('internal_chat_message_metadata_passthrough')
            metadata = passthrough.get('content_item_metadata') if isinstance(passthrough, dict) else None
            if not isinstance(content, list) or not isinstance(metadata, list) or len(content) != len(metadata):
                continue
            developer = item['role'] == 'developer'
            kinds = passthrough.get('content_item_kinds')
            if developer and (not isinstance(kinds, list) or len(kinds) != len(content)):
                continue
            for index, part in enumerate(content):
                provenance = metadata[index].get('provenance') if isinstance(metadata[index], dict) else None
                if (not isinstance(part, dict) or part.get('type') != 'input_text'
                        or not isinstance(provenance, dict) or provenance.get('type') != 'harness'
                        or provenance.get('hook') is not True):
                    continue
                text = part.get('text')
                if developer:
                    if kinds[index] != 'hooks.additional_context' or text != expected_context:
                        continue
                    if turn_id is None or passthrough.get('turn_id') != row['turn_id']:
                        raise ValueError
                    message_id = _review_id(item.get('id'))
                    hook_id = None
                else:
                    if not isinstance(text, str) or '<!' in text or '<?' in text:
                        raise ValueError
                    fragment = xml.fromstring(text)
                    if (fragment.tag != 'hook_prompt' or set(fragment.attrib) != {'hook_run_id'}
                            or len(fragment) or fragment.tail is not None):
                        raise ValueError
                    hook_id = _review_id(fragment.attrib['hook_run_id'])
                    if fragment.text != expected_context:
                        continue
                    message_id = _review_id(item['id']) if item.get('id') is not None else None
                if row['turn_id'] != turn_id:
                    raise ValueError
                found.append(dict(thread_id=data['thread_id'], turn_id=row['turn_id'],
                    hook_run_id=hook_id, message_id=message_id, content_index=index,
                    context_sha256=hashlib.sha256(expected_context.encode()).hexdigest(), sources=[row['source']]))
        if len(found) != 1:
            raise ValueError
        return _canonical_freeze(found[0])
    except (StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, xml.ParseError):
        raise ObservationError('native_hook_context_unobserved') from None
