"""A stand-in for `codex app-server` (stdio JSON-RPC) for tests/test_routing_codex_rate_limits.py. No network.

Usage: codex_app_server_double.py LOG SCENARIO [RESULT_JSON]
It appends every message it receives, and the names of its environment variables, to LOG (JSON lines), then
answers according to SCENARIO. RESULT_JSON is the `account/rateLimits/read` result to return.
"""
import json
import os
import signal
import subprocess
import sys
import time

log_path, scenario = sys.argv[1], sys.argv[2]
result = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}


def log(**entry):
    with open(log_path, 'a', encoding='utf-8') as out:
        out.write(json.dumps(entry) + '\n')


def send(value):
    sys.stdout.write((value if isinstance(value, str) else json.dumps(value)) + '\n')
    sys.stdout.flush()


null, err = os.stat(os.devnull), os.fstat(2)
log(spawned=True, env=sorted(os.environ), cwd=os.getcwd(), pid=os.getpid(), pgid=os.getpgid(0),
    stderr_devnull=(null.st_dev, null.st_ino) == (err.st_dev, err.st_ino))
if scenario in ('graceful_timeout', 'hang_with_child'):
    signal.signal(signal.SIGTERM, lambda *_: log(signal='SIGTERM', at=time.monotonic()))  # ignored: logged only
if scenario in ('hang_with_child', 'exit_leaving_child'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    log(grandchild=child.pid)
sys.stderr.write('debug: access_token=token-xyz accountId=acct-secret-123\n')
sys.stderr.flush()
if scenario == 'exit_at_once':
    sys.exit(3)
if scenario == 'hang':
    time.sleep(60)
for line in sys.stdin:
    message = json.loads(line)
    log(received=message)
    method, ident = message.get('method'), message.get('id')
    if method == 'initialize':
        if scenario == 'init_error':
            send(dict(id=ident, error=dict(code=-32600, message='nope token-xyz')))
            continue
        send(dict(id=ident, result=dict(userAgent='codex_double/0.0', codexHome=os.environ.get('HOME', ''))))
    elif method == 'account/rateLimits/read':
        if scenario == 'slow':
            time.sleep(0.6)
        if scenario == 'hang_read':
            time.sleep(60)
        if scenario in ('graceful_timeout', 'hang_with_child', 'exit_leaving_child'):
            continue  # never answers; waits for stdin to close
        if scenario == 'touch_auth':
            os.utime(os.path.join(os.environ['HOME'], '.codex', 'auth.json'), (1, 1))
        if scenario == 'create_auth':
            with open(os.path.join(os.environ['HOME'], '.codex', 'auth.json'), 'w') as out:
                out.write('{"fake": true}')
        if scenario == 'notifications':
            for _ in range(3):
                send(dict(method='account/rateLimits/updated', params={}))
        if scenario == 'server_request':
            send(dict(id=99, method='item/commandExecution/requestApproval', params={}))
        elif scenario == 'garbage':
            send('not json')
        elif scenario == 'huge':
            send('{"id": 2, "result": "' + 'x' * (1 << 20) + '"}')
        elif scenario == 'read_error':
            send(dict(id=ident, error=dict(code=-32603, message='failed for acct-secret-123')))
        elif scenario == 'wrong_id':
            send(dict(id=7, result=result))
        else:
            send(dict(id=ident, result=result))
    elif ident is not None:
        send(dict(id=ident, error=dict(code=-32601, message='unexpected')))
log(stdin='eof', at=time.monotonic())
if scenario in ('graceful_timeout', 'hang_with_child'):
    end = time.monotonic() + 60
    while time.monotonic() < end:  # survives SIGTERM: only SIGKILL stops it
        time.sleep(0.05)
