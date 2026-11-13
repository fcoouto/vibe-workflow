"""Disposable form-gating controller helpers; no live entry point."""

import time


def observe_unload(call, calls_left, deadline, query, before, *, clock=time.monotonic, sleep=time.sleep):
    """Baseline extraction of the original controller's observation sequence."""
    results = {'errors': [], 'inventory_subject_absent': False}
    for _ in range(4):
        data = call('get', '/api/plugin?' + query, timeout=2)['data']
        if not any(item.get('id') == 'question-box-gating' for item in data):
            results['inventory_subject_absent'] = True
            break
    if results['inventory_subject_absent']:
        response = call('post', '/api/rpc/question-box-gating/hello?' + query,
                        {'input': {}}, expected_status=400, timeout=2)
        results['rpc_unavailable'] = 'rpc.unavailable' in response['stdout']
    info = call('get', '/api/info', timeout=2)
    results['shared_process_unchanged'] = info['pid'] == before['pid'] and info['version'] == before['version']
    results['passed'] = all(results.get(key) for key in (
        'inventory_subject_absent', 'rpc_unavailable', 'shared_process_unchanged'))
    return results


def finalize(case_result, case_error, cleanup, save):
    """Baseline extraction of the original controller's finally block."""
    recovery = cleanup()
    if case_result is not None:
        case_result['recovery'] = recovery
        save('result.json', case_result)
    return case_result
