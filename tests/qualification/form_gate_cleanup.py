"""Disposable form-gating controller helpers; no live entry point."""

import json
import time


def observe_unload(call, calls_left, deadline, query, before, *, clock=time.monotonic, sleep=time.sleep):
    """Observe this fixture only, inside the caller's existing time/count budget.

    ``call`` charges every attempted operation and honors its timeout. Reserve
    two operations and four seconds for RPC/process verification. Spread the
    remaining inventory reads through the window, leaving two more seconds for
    the last inventory call. Errors stay unknown; no failed API call is retried.
    """
    started = clock()
    results = {'errors': [], 'inventory_subject_absent': None, 'rpc_unavailable': None,
               'shared_process_unchanged': None, 'observations': []}

    def observe(key, method, path, body=None, expected_status=None, reserve=0):
        began = clock()
        try:
            if calls_left() <= reserve or began >= deadline:
                raise RuntimeError('Recovery observation budget exhausted')
            timeout = min(2, (deadline - began) / (reserve + 1))
            value = call(method, path, body, expected_status=expected_status, timeout=timeout)
            if clock() >= deadline:
                raise TimeoutError('Recovery response arrived outside observation deadline')
            if key == 'inventory_subject_absent':
                data = value['data']
                if not isinstance(data, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
                                                      for item in data):
                    raise ValueError('Invalid plugin inventory')
                results[key] = not any(item['id'] == 'question-box-gating' for item in data)
            elif key == 'rpc_unavailable':
                error = json.loads(value['stdout'])
                results[key] = (value['returncode'] != 0 and 'HTTP 400 ' in value['stderr']
                                and error.get('_tag') == 'RpcError' and error.get('type') == 'rpc.unavailable')
            else:
                results[key] = value['pid'] == before['pid'] and value['version'] == before['version']
        except Exception as error:
            # A previous negative observation cannot stand in for a failed read.
            results[key] = None
            results['errors'].append({'condition': key, 'error': repr(error)})
        results['observations'].append({'condition': key, 'started': began - started,
                                        'finished': clock() - started, 'value': results[key]})

    inventory_reads = max(0, calls_left() - 2)
    for index in range(inventory_reads):
        observe('inventory_subject_absent', 'get', '/api/plugin?' + query, reserve=2)
        if results['inventory_subject_absent'] is not False:
            break
        remaining_reads = inventory_reads - index - 1
        remaining_time = deadline - 6 - clock()
        if remaining_reads == 0 or remaining_time <= 0:
            break
        sleep(remaining_time / remaining_reads)
    observe('rpc_unavailable', 'post', '/api/rpc/question-box-gating/hello?' + query,
            {'input': {}}, expected_status=400, reserve=1)
    observe('shared_process_unchanged', 'get', '/api/info')
    results['elapsed_seconds'] = clock() - started
    results['passed'] = not results['errors'] and all(results[key] is True for key in (
        'inventory_subject_absent', 'rpc_unavailable', 'shared_process_unchanged'))
    return results


def finalize(case_result, case_error, cleanup, save):
    """Always report case and cleanup outcomes independently, before exiting."""
    try:
        recovery = cleanup()
        if not isinstance(recovery, dict):
            raise ValueError('Missing cleanup receipt')
    except BaseException as error:
        recovery = {'passed': False, 'errors': [repr(error)]}
    case_status = ('failed' if case_error is not None else
                   'passed' if case_result is not None and case_result.get('verdict') == 'passed' else 'unknown')
    passed = case_status == 'passed' and recovery.get('passed') is True and not recovery.get('errors')
    result = {'verdict': 'passed' if passed else 'failed',
              'cases': {'status': case_status, 'result': case_result, 'error': case_error}, 'recovery': recovery}
    save('result.json', result)
    return result
