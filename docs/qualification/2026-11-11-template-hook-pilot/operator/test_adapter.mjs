import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import adapter from './adapter.mjs'

const root = mkdtempSync(join(tmpdir(), 'pilot-adapter-test-'))
try {
  for (const dir of ['project', 'before']) mkdirSync(join(root, dir))
  for (const dir of ['project', 'before']) writeFileSync(join(root, dir, 'README.md'), '# Keep\n')
  writeFileSync(join(root, 'project', 'AGENTS.md'), 'Preserve README.md.\n')
  const selection = join(root, 'selection.json')
  writeFileSync(selection, JSON.stringify({ arm: 'probe', project: join(root,'project'),
    before: join(root,'before'), package: process.cwd(), documents: ['README.md'], templates: [],
    helper: join(process.cwd(),'.superpowers/template-hook-pilot/helper.py'), python: process.argv[2], log: join(root,'events') }))
  let hook
  const tools = new Map(['read','patch','shell','webfetch'].map(id => [id,{id}]))
  await adapter.setup({ options: { selection }, tool: {
    transform: async callback => callback({ list: () => [...tools.values()], remove: id => tools.delete(id), add: tool => tools.set(tool.name,{id:tool.name,...tool}) }),
    list: async () => [...tools.values()], hook: async (_,callback) => { hook=callback },
  } })
  assert.equal(tools.has('shell'), false)
  assert.equal(tools.has('webfetch'), false)
  const event = tool => ({ tool, status:'completed', sessionID:'test', result:{content:'ORIGINAL',metadata:{keep:true}} })
  writeFileSync(join(root,'project','README.md'), '# Keep\n[Broken](absent.md)\n')
  const first=event('patch'); hook(first)
  assert.match(first.result.content,/^ORIGINAL\n\nTemplate check:/)
  assert.match(first.result.content,/link_missing/)
  assert.equal(first.result.metadata.keep,true)
  const native=event('patch'); native.sessionID='native-shape'
  native.result.content=[{type:'text',text:'ORIGINAL'},{type:'file',uri:'file:///synthetic',mime:'text/plain'}]
  hook(native)
  assert.deepEqual(native.result.content.slice(0,2),[{type:'text',text:'ORIGINAL'},{type:'file',uri:'file:///synthetic',mime:'text/plain'}])
  assert.match(native.result.content[2]?.text ?? '', /link_missing/)
  const repeat=event('pilot_batch'); hook(repeat); assert.equal(repeat.result.content,'ORIGINAL')
  const ignored=event('read'); hook(ignored); assert.equal(ignored.result.content,'ORIGINAL')
  writeFileSync(join(root,'project','README.md'),'# Keep\n')
  const success=event('patch'); hook(success); assert.equal(success.result.content,'ORIGINAL')
  // An unavailable helper is advisory: retain the original tool result and record the failure.
  writeFileSync(selection,'{}')
  const failed=event('patch'); hook(failed); assert.equal(failed.result.content,'ORIGINAL')
  const rows=readFileSync(join(root,'events'),'utf8').trim().split('\n').map(JSON.parse)
  assert.equal(rows.filter(r=>r.kind==='hook_error').length,1)
  assert.equal(rows.filter(r=>r.kind==='hook' && r.notice).length,2)
  console.log('adapter boundaries passed: bounded feedback, preservation, deduplication, silence, ignored tools, helper failure')
} finally { rmSync(root,{recursive:true,force:true}) }
