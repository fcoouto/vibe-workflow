"""Run the packaged coordinator helper with synthetic native tools only."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

_HELPER = Path(__file__).resolve().parents[1] / 'scripts' / 'usage_codex_host.js'
_NODE = shutil.which('node')
_VERIFY = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const run = eval(fs.readFileSync(process.argv[1], 'utf8'));
const checks = [];
const head = {kind:'codex', id:'parent', hostId:'local', cwd:'/fixture'};
const turn = (id, status) => ({id, status, startedAt:100, items:[
  {type:'agentMessage', id:'unrelated', text:'Unrelated assistant output'},
  {type:'userMessage', id:'user-'+id, content:[{type:'text', text:'Native receipt\n'}]}
]});
const tools = {
  mcp__codex_app__read_thread: async args => {
    assert.equal(args.cursor, undefined);
    assert.equal(args.turnLimit, 1);
    checks.push('current');
    assert.equal(args.hostId, 'local');
    assert.equal(args.threadId, 'parent');
    return {structuredContent:args.cursor ? {thread:head, turns:[turn('approval','completed')]}
      : {thread:head, turns:[turn('current','inProgress')], page:{nextCursor:'older',hasMore:true}}};
  },
  mcp__codex_app__get_usage_limits: async () => {
    throw new Error('Native quota observation is unavailable');
  },
  mcp__openai_api_credit_promo__preview_api_credit_promo: async () => {
    checks.push('expiry'); return {structuredContent:{benefit:{currency:'USD',cadence:'monthly',
      expires_at:'2026-11-17T07:56:26Z'}}};
  },
  exec_command: async args => {
    checks.push('python');
    assert.equal(args.login,false);
    assert.equal(args.workdir,'/fixture');
    assert.doesNotMatch(args.cmd, /Native receipt|Unrelated assistant output|forged input|approval_snapshot|manual_selection/);
    return {exit_code:0,output:'{"ok":true}'};
  }
};
const request = {root:'/installed', python:'/python', threadId:'parent', hostId:'local', checkout:'/fixture',
  action:'execute', approvalTurnId:'approval', plan:{mode:'smart',thread_id:'parent',host_id:'local',checkout:'/fixture'},
  proposal:{task_id:'task'}, reviewer:{coordinator:'parent',identity:'reviewer'}, price:{},
  thread_snapshot:{fake:'forged input'}, grant:{pool:'benefit',expiry:1794902186}};
(async () => {
  assert.deepEqual(await run(request,tools), {ok:true});
  assert.deepEqual(checks.sort(), ['current','expiry','python']);
  checks.length = 0;
  await assert.rejects(run({...request, grant:{pool:'benefit',expiry:0}},tools), /expiry/);
  assert.ok(!checks.includes('python'));
  checks.length = 0;
  await assert.rejects(run({...request, plan:{...request.plan,host_id:'other'}},tools), /context/);
  assert.ok(!checks.includes('python'));

  const fresh = {...request, approvalTurnId:undefined};
  for (const cap of [undefined, null]) {
    await run({...fresh, action:'freeze', maximumUsdMicros:cap}, {...tools, exec_command: async args => {
      if (cap === null) assert.match(args.cmd, /"maximum_usd_micros":null/);
      else assert.doesNotMatch(args.cmd, /"maximum_usd_micros"/);
      return {exit_code:0,output:'{"ok":true}'};
    }});
  }
  const local = {...tools, exec_command: async args => {
    const child = spawnSync('/bin/sh', ['-c',args.cmd], {encoding:'utf8'});
    assert.equal(child.stderr,'');
    return {exit_code:child.status,output:child.stdout};
  }};
  const rejected = await run({...request, root:process.argv[2], python:process.argv[3]},local);
  assert.deepEqual(rejected, {ok:false,reason:'counted_input_changed',uncertain:true});
  const visibility = {account: '.codex', endpoint: 'http://127.0.0.1:4747'};
  const optional = {...request, root:process.argv[2], python:process.argv[3], habblaud:visibility};
  const proposal = {text:'Settled proposal', accepted:false, requires_native_review:true, execution_route:'openai-api'};
  const observed = await run(optional, local);
  assert.deepEqual(observed, {...proposal, visibility});

  let pendingOutput, launches = 0, windows = [], observation = 'delayed';
  const deferred = {...local, exec_command: async args => {
    launches++;
    pendingOutput = (await local.exec_command(args)).output.trim().split('\n');
    return {session_id:123, output:''};
  }, write_stdin: async args => {
    assert.equal(args.session_id, 123);
    windows.push(args.yield_time_ms);
    if (windows.length === 1) {
      return {session_id:123, output:pendingOutput[0] + '\n'};
    }
    if (observation === 'error') throw new Error('receiver disconnected');
    if (observation === 'malformed') return {exit_code:0, output:'malformed receipt'};
    if (observation === 'ready') return {exit_code:0, output:pendingOutput[1]};
    return {session_id:123, output:''};
  }};
  const delayed = await run(optional, deferred);
  assert.deepEqual(delayed, {...proposal,
    visibility:{delivered:false, reason:'visibility_unconfirmed'}});
  assert.equal(launches, 1);
  assert.equal(windows.length, 2);
  assert.equal(windows[0], 300000);
  assert.ok(windows[1] > 0 && windows[1] < windows[0]);

  for (const unavailable of ['malformed', 'error']) {
    observation = unavailable;
    windows.length = 0;
    assert.deepEqual(await run(optional, deferred), delayed);
    assert.equal(windows.length, 2);
  }
  observation = 'ready';
  windows.length = 0;
  assert.deepEqual(await run(optional, deferred), observed);
  assert.equal(launches, 4);
  assert.equal(windows.length, 2);

  const earlyGeneration = {...local, exec_command: async args => {
    const output = (await local.exec_command(args)).output;
    return {session_id:124, output:output.slice(0, output.indexOf('\n') + 1)};
  }, write_stdin: async args => {
    assert.equal(args.session_id, 124);
    windows.push(args.yield_time_ms);
    throw new Error('receiver disconnected');
  }};
  windows.length = 0;
  assert.deepEqual(await run(optional, earlyGeneration), delayed);
  assert.deepEqual(windows, [15000]);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""


@unittest.skipUnless(_NODE, 'Node required for the coordinator helper fixture')
class CodexUsageBridgeTests(unittest.TestCase):
    def test_activation_path_does_not_read_approval_history(self):
        code = r"""
const assert = require('node:assert/strict');
const run = eval(require('node:fs').readFileSync(process.argv[1], 'utf8'));
let reads = 0;
const request = {root:'/installed', python:'/python', checkout:'/fixture', threadId:'parent', hostId:'local',
  action:'execute', approvalTurnId:'old-approval', manualSelection:'old selection',
  plan:{checkout:'/fixture',thread_id:'parent',host_id:'local',mode:'smart'}, grant:{pool:'prepaid'}};
run(request, {
  mcp__codex_app__read_thread: async args => {
    assert.equal(++reads, 1);
    assert.equal(args.cursor, undefined);
    return {structuredContent:{thread:{kind:'codex',id:'parent',hostId:'local',cwd:'/fixture'},
      turns:[{id:'current',status:'inProgress',startedAt:100,items:[]}], page:{nextCursor:'older'}}};
  },
  exec_command: async args => {
    assert.doesNotMatch(args.cmd, /approval_snapshot|manual_selection|old selection|approval_text/);
    return {exit_code:0,output:'{"requires_native_review":true}'};
  }
}).then(result => assert.deepEqual(result, {requires_native_review:true}))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run([_NODE, '-e', code, str(_HELPER)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bridge_reads_native_sources_and_refuses_conflicting_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / 'runtime'
            runtime.mkdir()
            (runtime / 'usage_budget.py').write_text('class BudgetError(ValueError): pass\n')
            (runtime / 'usage_admission.py').write_text('Grant = dict\n')
            (runtime / 'usage_codex_host.py').write_text(
                'from runtime.usage_budget import BudgetError\n'
                'Plan = Price = Reviewer = dict\n'
                'def execute(plan, **frame):\n'
                '    if "habblaud" in frame:\n'
                '        proposal = dict(text="Settled proposal", accepted=False, requires_native_review=True,\n'
                '                        execution_route="openai-api")\n'
                '        if "generation_ready" in frame: frame["generation_ready"](proposal)\n'
                '        return proposal | dict(visibility=frame["habblaud"])\n'
                '    raise BudgetError("counted_input_changed")\n'
            )
            result = subprocess.run([_NODE, '-e', _VERIFY, str(_HELPER), folder, sys.executable],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
