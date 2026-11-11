"""Native Claude probe; Codex remains held for supported hook trust review."""
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

from runner import HERE, STATE, Runtime, profile, reserve, save, settle, tree


def prepare(name='claude-probe'):
    root=Path(json.loads(STATE.read_text())['base'])
    base=root/name; project=base/'project'
    for name in ('project','home','tmp','adapter'): (base/name).mkdir(parents=True,exist_ok=True)
    base.chmod(0o700)
    (base/'profile.sb').write_text(profile(base))
    shutil.copyfile(root/'before/probe/README.md',project/'README.md')
    (project/'unrelated.txt').write_text('Unrelated file.\n')
    shutil.copyfile(HERE/'native_hook.py',base/'adapter/native_hook.py')
    shutil.copyfile(HERE/'helper.py',base/'adapter/helper.py')
    selection=dict(project=str(project),package=str(root/'package'),before=str(root/'before/probe'),
                   documents=['README.md'],templates=[],python=sys.executable,helper=str(base/'adapter/helper.py'),
                   log=str(base/'adapter/events.jsonl'))
    save(base/'adapter/selection.json',selection)
    command=shlex.join([sys.executable,'-I','-B',str(base/'adapter/native_hook.py'),str(base/'adapter/selection.json')])
    save(base/'settings.json',dict(hooks={'PostToolUse':[dict(matcher='Read|Write|Edit',hooks=[dict(type='command',command=command,timeout=6)])]},
                                  permissions=dict(allow=['Read','Write(./README.md)','Edit(./README.md)'],
                                                   deny=['Bash','WebFetch','WebSearch','Agent'])))
    prompt='''Run this deliberate synthetic hook probe exactly, one tool at a time: Read README.md. Write README.md preserving its original lines and appending [Probe link](missing.md). Leave the broken link until after the next two calls. Read README.md twice to observe duplicate feedback suppression. Write README.md back to its exact original contents. Read README.md, then unrelated.txt. Stop and report observed link_missing feedback, duplicate suppression, and whether original tool output stayed visible. Edit no other file. No shell, network, delegation or configuration changes.'''
    (base/'prompt.txt').write_text(prompt)
    save(base/'manifest.json',dict(inputs=tree(project),settings=tree(base/'adapter')))
    print('Claude disposable hook/settings prepared; no inference')


def run(name='claude-probe'):
    root=Path(json.loads(STATE.read_text())['base']); base=root/name
    raw=subprocess.check_output(['/usr/bin/security','find-generic-password','-s','Claude Code-credentials','-w'],timeout=10)
    credential=json.loads(raw)['claudeAiOauth']
    assert credential.get('subscriptionType') in ('max','pro'), 'subscription required'
    assert credential['expiresAt']/1000 > time.time()+300, 'subscription token expiry'
    deadline=reserve(STATE,name)
    env=Runtime(base).env
    env['CLAUDE_CODE_OAUTH_TOKEN']=credential['accessToken']
    env['CLAUDE_CONFIG_DIR']=str(base/'home/.claude')
    command=['sandbox-exec','-f',str(base/'profile.sb'),'/opt/homebrew/bin/claude','-p',
             '--restricted','--strict-mcp-config','--setting-sources','', '--settings',str(base/'settings.json'),
             '--tools','Read,Write,Edit','--permission-mode','dontAsk','--permission-prompts','none',
             '--model','haiku','--effort','low','--max-turns','24','--no-chrome','--disable-slash-commands',
             '--output-format','stream-json','--verbose','--include-hook-events']
    result=dict(name=name,started=time.time(),argv=command,status='failed_start')
    process=None
    try:
        with (base/'stdout.jsonl').open('w') as out,(base/'stderr.txt').open('w') as err:
            process=subprocess.Popen(command,cwd=base/'project',env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,start_new_session=True,text=True)
            process.communicate((base/'prompt.txt').read_text(),timeout=min(180,max(.1,deadline-time.time())))
        result.update(status='finished' if process.returncode==0 else 'failed',returncode=process.returncode)
    except subprocess.TimeoutExpired:
        result['status']='timeout'
    except Exception as exc:
        result['error']=type(exc).__name__
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(5)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait(5)
        result.update(stopped=process is None or process.poll() is not None,elapsed=round(time.time()-result['started'],3),after=tree(base/'project'))
        save(base/'result.json',result)
        if result['stopped']: settle(STATE,name,result['status'])
    print(json.dumps({k:result.get(k) for k in ('name','status','returncode','stopped','elapsed')}))


if __name__=='__main__':
    prepare() if sys.argv[1:] == ['prepare'] else run()
