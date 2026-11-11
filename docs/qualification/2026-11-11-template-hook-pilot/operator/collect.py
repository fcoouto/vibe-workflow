"""Collect observed counters and protected checks; semantic acceptance stays manual."""
import json
from pathlib import Path
import subprocess
import sys

from runner import ROOT, STATE, save, tree
from cases import preserved


def normalized(client, usage):
    if client=='opencode':
        values=dict(input=usage.get('input'),cache_read=usage.get('cache',{}).get('read'),
                    cache_write=usage.get('cache',{}).get('write'),output=usage.get('output'),reasoning=usage.get('reasoning'))
        parts=list(values.values())
    else:
        values=dict(input=usage.get('input_tokens'),cache_read=usage.get('cache_read_input_tokens'),
                    cache_write=usage.get('cache_creation_input_tokens'),output=usage.get('output_tokens'),
                    reasoning=usage.get('output_tokens_details',{}).get('thinking_tokens'))
        parts=[values[k] for k in ('input','cache_read','cache_write','output')]
    values['total']=sum(parts) if all(isinstance(v,(int,float)) for v in parts) else None
    values['reasoning_in_output']=client=='claude'
    return values


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def inspect(base):
    result=json.loads((base/'result.json').read_text())
    native=base.name.startswith('claude')
    row=dict(name=base.name,status=result['status'],stopped=result['stopped'],elapsed=result.get('elapsed'),
             error=result.get('error'),client='claude-code' if native else 'opencode')
    events=lines(base/'adapter/events.jsonl')
    row['hook']=dict(invocations=sum(r.get('kind')=='hook' for r in events),errors=sum(r.get('kind')=='hook_error' for r in events),
                     notices=sum(bool(r.get('notice')) for r in events),ignored=sum(r.get('kind')=='ignored' for r in events),
                     milliseconds=sum(r.get('milliseconds',0) for r in events if r.get('kind') in ('hook','hook_error')))
    row['helper_calls']=[{k:r.get(k) for k in ('operation','ok','milliseconds')} for r in events if r.get('kind')=='helper']
    row['hook_observations']=[{k:r.get(k) for k in ('kind','tool','ok','notice','milliseconds')} for r in events if r.get('kind')!='catalog']
    if native:
        stream=lines(base/'stdout.jsonl')
        final=next((r for r in reversed(stream) if r.get('type')=='result'),{})
        usage=final.get('usage',{})
        row.update(raw_tokens=[usage],tokens=normalized('claude',usage),model=list(final.get('modelUsage',{})),
                   requests=len({r.get('request_id') for r in stream if r.get('type')=='assistant' and r.get('request_id')}),
                   repair_responses=None,agentic_turns=final.get('num_turns'),permission_denials=final.get('permission_denials'),
                   handoff=final.get('result'),model_usage=final.get('modelUsage'))
        row['fixture_preserved']=(base/'project/README.md').read_bytes()==(base.parent/'before/probe/README.md').read_bytes()
        return row
    messages=json.loads((base/'messages.json').read_text()).get('data',[]) if (base/'messages.json').exists() else []
    assistants=[m for m in messages if m.get('type')=='assistant']
    counters=[m.get('tokens',{}) for m in assistants]
    guard=lines(base/'guard/hooks.jsonl')
    requests=[r for r in guard if r.get('decision')=='transmit']
    row['requests']=len(requests)
    row['raw_tokens']=counters
    values=[normalized('opencode',v) for v in counters]
    keys=('input','cache_read','cache_write','output','reasoning','total')
    row['tokens']={k:sum(v[k] for v in values) if values and all(v[k] is not None for v in values) else None for k in keys}
    if not requests and result.get('status')=='failed_start':
        row['tokens']={k:0 for k in keys};row['zero_basis']='authentication preflight refused before client/model startup'
    row['model']=sorted({r['provider']+'/'+r['model']+'#'+str(r['variant']) for r in requests})
    row['terminal']=messages[-1].get('outcome') if messages and messages[-1].get('type')=='idle' else None
    row['tools']=[];row['handoff']=[];row['repair_responses']=0
    failure_seen=False
    for message in assistants:
        contents=message.get('content',[])
        if failure_seen and any(c.get('type')=='tool' and c.get('name') in ('patch','edit','write') for c in contents):
            row['repair_responses']+=1;failure_seen=False
        for content in contents:
            if content.get('type')=='text': row['handoff'].append(content.get('text',''))
            if content.get('type')!='tool': continue
            state=content.get('state',{})
            row['tools'].append(dict(name=content.get('name'),status=state.get('status')))
            text='\n'.join(p.get('text','') for p in state.get('content',[]) if p.get('type')=='text')
            if 'Template check: inspect these findings' in text or (content.get('name')=='template_check' and '"ok": false' in text):
                failure_seen=True
    if (base/'initial.json').exists():
        manifest=json.loads((base/'manifest.json').read_text());initial=json.loads((base/'initial.json').read_text())
        row.update(case=manifest['case'],arm=manifest['arm'])
        row['preservation_findings']=preserved(base/'project',initial,manifest['allowed'],manifest['inputs'])
        row['protected_hashes']={name:digest for name,digest in tree(base/'project').items() if name not in manifest['allowed']}
        row['outputs']={name:(base/'project'/name).read_text() for name in manifest['allowed']}
        sys.path.insert(0,str(ROOT))
        from runtime.template_tools import check
        row['mechanical']=check(base/'project',manifest['allowed'],['project/product.md','project/overview.md','project/local-preparation.md'],
                                before=base.parent/'before'/manifest['case'])
        # Never execute candidate code after a protected-file change.
        if not row['preservation_findings']:
            done=subprocess.run([sys.executable,'-I','-B','-c',"import runpy,sys;sys.path.insert(0,'.');runpy.run_path('check.py')"],
                                cwd=base/'project',capture_output=True,text=True,timeout=10,
                                env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','HOME':str(base/'home')})
            row['fixture_check']=dict(returncode=done.returncode,stdout=done.stdout,stderr=done.stderr)
        row['semantic']='pending manual review'
    return row


if __name__=='__main__':
    state=json.loads(STATE.read_text());base=Path(state['base'])
    rows=[inspect(base/s['name']) for s in state['sessions'] if (base/s['name']/'result.json').exists()]
    save(base/'collected.json',dict(attempts=rows,started=state['started'],deadline=state['deadline'],cap=state.get('cap',12)))
    for r in rows:
        print(json.dumps({k:r.get(k) for k in ('name','status','terminal','tokens','requests','repair_responses','hook','helper_calls','preservation_findings','fixture_check')}))
