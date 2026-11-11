"""Private, subscription-only pilot operator; never imports historical allocation state."""
import ast
import base64
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATE = HERE / 'state.json'
TEMP = Path('/private/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/opencode')
MODEL = 'gpt-6-luna'
ROUTE = ['openai', MODEL, 'low']


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)


def tree(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob('*')) if p.is_file()}


def initialize(path, base):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(dict(base=str(base), started=None, deadline=None, sessions=[]), f)
    path.chmod(0o600)


def reserve(path, name, now=None):
    now = time.time() if now is None else now
    with path.open('r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        s = json.load(f)
        if any(r['name'] == name for r in s['sessions']):
            raise ValueError('duplicate session')
        if len(s['sessions']) >= s.get('cap', 12):
            raise ValueError('sessions exhausted')
        if sum(r['status'] == 'reserved' for r in s['sessions']) >= 2:
            raise ValueError('concurrency exhausted')
        if s['deadline'] is not None and now >= s['deadline']:
            raise ValueError('deadline expired')
        if s['started'] is None:
            s.update(started=now, deadline=now+2700)
        s['sessions'].append(dict(name=name, at=now, status='reserved'))
        f.seek(0); json.dump(s, f, indent=2); f.truncate(); f.flush(); os.fsync(f.fileno())
        return s['deadline']


def settle(path, name, status):
    assert status != 'reserved'
    with path.open('r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        s = json.load(f)
        row = next(r for r in s['sessions'] if r['name'] == name)
        row.update(status=status, stopped_at=time.time())
        f.seek(0); json.dump(s, f, indent=2); f.truncate(); f.flush(); os.fsync(f.fileno())


def validate_auth(value, now=None, required_seconds=2760):
    now = time.time() if now is None else now
    if value.get('type') != 'oauth' or value.get('methodID') != 'chatgpt-token-sharing':
        raise ValueError('subscription authentication required')
    expiry = value['expires']/1000 if value['expires'] > 1e12 else value['expires']
    if expiry < now+required_seconds:
        raise ValueError('expiry cannot cover pilot')
    return {**value, 'refresh': ''}


def auth():
    values = json.loads(subprocess.check_output(['opencode','api','get','/api/credential'], timeout=15))['data']
    state = json.loads(STATE.read_text())
    required = max(60, state['deadline']-time.time()+60) if state['deadline'] else 2760
    return validate_auth(next(v['value'] for v in values if v.get('integrationID') == 'openai' and v.get('active')),
                         required_seconds=required)


def profile(base):
    return f'''(version 1)
(allow default)
(deny file-read* (subpath "/Users/fcouto"))
(deny file-write* (subpath "/"))
(allow file-write* (subpath "{base}") (literal "/dev/null"))
'''


class Runtime:
    def __init__(self, base):
        self.base, self.project = Path(base), Path(base)/'project'
        self.process = None
        self.env = dict(HOME=str(self.base/'home'), PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                        USER='qualification', LANG='en_US.UTF-8', TMPDIR=str(self.base/'tmp'),
                        XDG_CONFIG_HOME=str(self.base/'config'), XDG_DATA_HOME=str(self.base/'data'),
                        XDG_STATE_HOME=str(self.base/'state'), XDG_CACHE_HOME=str(self.base/'cache'),
                        OPENCODE_DISABLE_AUTOUPDATE='1')

    def prepare(self, allowed, arm, package, before, templates):
        b = self.base
        for name in ('home','config','data','state','cache','tmp','project','guard'):
            (b/name).mkdir(parents=True, exist_ok=True)
        b.chmod(0o700)
        (b/'profile.sb').write_text(profile(b))
        recorder = next(ast.literal_eval(n.value) for n in ast.parse((ROOT/'tests/qualification/safety.py').read_text()).body
                        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id == 'RECORDER' for t in n.targets))
        recorder = recorder.replace('__PHASE__', 'post').replace('const o = ctx?.options ?? {}', '''
    await ctx.session.hook("retry", e => { e.decision = { retry: false } })
    await ctx.session.hook("context", e => { e.options.maxTokens = 8000 })
    const o = ctx?.options ?? {}''')
        recorder = recorder.replace('if (sent() >= (c.budget ?? 0))',
                                    'if (Date.now() >= (c.deadline ?? 0)) guard("guard_deadline", record)\n       if (sent() >= (c.budget ?? 0))')
        (b/'guard/index.mjs').write_text(recorder)
        (b/'guard/hooks.jsonl').write_text('')
        save(b/'guard/package.json',dict(name='pilot-request-guard',type='module',main='index.mjs'))
        save(b/'guard/control.json',dict(budget=0,route=ROUTE,deadline=0))
        adapter = b/'adapter'
        adapter.mkdir(exist_ok=True)
        shutil.copyfile(HERE/'adapter.mjs', adapter/'index.mjs')
        shutil.copyfile(HERE/'helper.py', adapter/'helper.py')
        save(adapter/'package.json',dict(name='pilot-template-adapter',type='module',main='index.mjs'))
        (adapter/'events.jsonl').write_text('')
        options = dict(arm=arm,project=str(self.project),package=str(package),before=str(before),
                       documents=list(allowed),templates=templates,python=sys.executable,
                       helper=str(adapter/'helper.py'),log=str(adapter/'events.jsonl'))
        save(adapter/'selection.json',options)
        permissions = [dict(action='*',resource='*',effect='deny')]
        permissions += [dict(action=a,resource='*',effect='allow') for a in ('read','glob','grep')]
        permissions += [dict(action='external_directory',resource='*',effect='deny'),
                        dict(action='read',resource='opencode.json',effect='deny')]
        permissions += [dict(action='edit',resource=p,effect='allow') for p in allowed]
        if arm != 'baseline':
            permissions += [dict(action=a,resource='*',effect='allow') for a in ('template_prepare','template_check')]
        if arm == 'probe':
            permissions.append(dict(action='pilot_batch',resource='*',effect='allow'))
        config = {'$schema':'https://opencode.ai/config.json','model':'openai/'+MODEL,
                  'snapshots':False,'formatter':False,'compaction':{'auto':False},
                  'agents':{'build':{'steps':24}},'permissions':permissions,
                  'policies':[dict(action='provider.use',resource='*',effect='deny'),
                              dict(action='provider.use',resource='openai',effect='allow')],
                  'plugins':[dict(package=str(b/'guard'),options=dict(log=str(b/'guard/hooks.jsonl'),control=str(b/'guard/control.json'))),
                             dict(package=str(adapter),options=dict(selection=str(adapter/'selection.json')))]}
        save(self.project/'opencode.json',config)

    def start(self):
        credential = auth()
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); self.port=s.getsockname()[1]
        self.log = (self.base/'server.log').open('w')
        self.process = subprocess.Popen(['sandbox-exec','-f',str(self.base/'profile.sb'),'/opt/homebrew/bin/opencode',
                                         'serve','--hostname','127.0.0.1','--port',str(self.port)],cwd=self.project,
                                        env=self.env,stdin=subprocess.DEVNULL,stdout=self.log,stderr=subprocess.STDOUT,
                                        start_new_session=True)
        save(self.base/'process.json',dict(pid=self.process.pid,pgid=self.process.pid,port=self.port))
        until = time.monotonic()+30
        while time.monotonic() < until:
            text = (self.base/'server.log').read_text()
            match = re.search(r'(?:password[:=]?\s*)(\S+)',text,re.I)
            if match:
                self.password = match.group(1); break
            if self.process.poll() is not None:
                raise RuntimeError('isolated server startup failed; inspect private log')
            threading.Event().wait(.2)
        else:
            raise RuntimeError('isolated server startup timeout')
        created = self.api('POST','/api/credential',dict(integrationID='openai',value=credential,
                           label='Disposable template hook pilot',activate=True))
        self.credential_id = created['data']['id']
        self.api('POST','/api/location/reload',{})

    def api(self, method, path, body=None, timeout=20):
        c = http.client.HTTPConnection('127.0.0.1',self.port,timeout=timeout)
        headers = {'Authorization':'Basic '+base64.b64encode(('opencode:'+self.password).encode()).decode(),
                   'x-opencode-directory':str(self.project),'content-type':'application/json'}
        try:
            c.request(method,path,json.dumps(body) if body is not None else None,headers)
            response = c.getresponse(); raw = response.read()
            if response.status >= 400:
                raise RuntimeError(f'isolated API {path.split("?")[0]} HTTP {response.status}')
            return json.loads(raw) if raw else None
        finally:
            c.close()

    def discovery(self):
        models = self.api('GET','/api/model')['data']
        m = next(m for m in models if m['providerID']=='openai' and m['id']==MODEL)
        assert not m.get('cost') and m.get('enabled',True), 'subscription catalog mismatch'
        assert any(v['id']=='low' for v in m['variants']), 'variant absent'
        plugins = self.api('GET','/api/plugin')['data']
        assert {'qual-post','template-pilot'} <= {p['id'] for p in plugins}, 'plugin absent'
        credentials = self.api('GET','/api/credential')['data']
        assert len(credentials)==1 and credentials[0]['value']['type']=='oauth'
        return {k:m.get(k) for k in ('providerID','id','variants','cost')}

    def stop(self):
        if self.process and self.process.poll() is None:
            if getattr(self,'credential_id',None):
                try: self.api('DELETE','/api/credential/'+self.credential_id,timeout=2)
                except Exception: pass
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGKILL); self.process.wait(5)
        if hasattr(self,'log'): self.log.close()


def run_one(name, seconds=240):
    state = json.loads(STATE.read_text())
    base = Path(state['base'])/name
    manifest = json.loads((base/'manifest.json').read_text())
    assert tree(base/'project') == manifest['inputs'], 'frozen project changed'
    deadline = reserve(STATE,name)
    runtime = Runtime(base)
    result = dict(name=name,started=time.time(),model=ROUTE,status='failed_start')
    def kill():
        if runtime.process and runtime.process.poll() is None:
            try: os.killpg(runtime.process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
    watchdog = threading.Timer(min(seconds,max(.1,deadline-time.time())),kill)
    watchdog.daemon=True; watchdog.start()
    try:
        runtime.start(); result['discovery']=runtime.discovery()
        created = runtime.api('POST','/api/session',dict(title='Template hook pilot '+name,agent='build',
                              model=dict(providerID='openai',id=MODEL,variant='low'),location=dict(directory=str(runtime.project))))
        result['session']=sid=created['data']['id']
        save(base/'guard/control.json',dict(budget=24,route=ROUTE,deadline=1000*min(deadline,result['started']+seconds)))
        runtime.api('POST',f'/api/session/{sid}/prompt',dict(text=(base/'prompt.txt').read_text()))
        runtime.api('POST',f'/api/experimental/session/{sid}/wait',{},timeout=max(1,min(seconds,deadline-time.time())))
        result['status']='finished'
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)[:300]
    finally:
        if result.get('session') and runtime.process and runtime.process.poll() is None:
            try: save(base/'messages.json',runtime.api('GET',f'/api/session/{sid}/message?limit=200&order=asc'))
            except Exception: result['transcript_unavailable']=True
        runtime.stop(); watchdog.cancel()
        result['stopped']=runtime.process is None or runtime.process.poll() is not None
        result['elapsed']=round(time.time()-result['started'],3)
        result['after']=tree(runtime.project)
        save(base/'result.json',result)
        if result['stopped']: settle(STATE,name,result['status'])
    print(json.dumps({k:result.get(k) for k in ('name','status','error','stopped','elapsed')}),flush=True)
    return result


if __name__ == '__main__':
    run_one(sys.argv[1],180 if sys.argv[1]=='opencode-probe' else 240)
