"""Disposable native feedback: fixed selection, bounded output, no automatic repair."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

selection_path = Path(sys.argv[1])
o = json.loads(selection_path.read_text())
event = json.load(sys.stdin)
tool = event.get('tool_name')
name = event.get('tool_input', {}).get('file_path')
target = Path(name).resolve() if name else None
selected = {Path(o['project'])/p for p in o['documents']}
started = time.monotonic()
record = dict(tool=tool, event=event.get('hook_event_name'), session=event.get('session_id'))
output = ''
if tool not in ('Read','Write','Edit') or target not in selected:
    record['kind'] = 'ignored'
else:
    try:
        state = selection_path.parent/'previous.json'
        with os.fdopen(os.open(state, os.O_RDWR | os.O_CREAT, 0o600), 'r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            f.seek(0); previous=json.loads(f.read() or '{}')
            data = json.loads(subprocess.check_output([o['python'],'-I','-B',o['helper'],str(selection_path),'check'],
                              timeout=5,env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin'},stderr=subprocess.DEVNULL))
            old=previous.get(event.get('session_id'))
            output='' if data['ok'] or (data['fingerprint'] and data['fingerprint']==old) else data['notice']
            previous[event.get('session_id')]=data['fingerprint']
            f.seek(0); json.dump(previous,f); f.truncate()
        record.update(kind='hook',ok=data['ok'],notice=output,fingerprint=data['fingerprint'])
    except Exception:
        record['kind']='hook_error'
record['milliseconds']=1000*(time.monotonic()-started)
with open(o['log'],'a') as f: f.write(json.dumps(record)+'\n')
if output:
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PostToolUse','additionalContext':output}}))
