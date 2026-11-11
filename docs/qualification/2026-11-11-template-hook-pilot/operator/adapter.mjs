import { appendFileSync, readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'

export default {
  id: 'template-pilot',
  async setup(ctx) {
    const selection = ctx.options.selection
    const o = JSON.parse(readFileSync(selection, 'utf8'))
    const log = row => appendFileSync(o.log, JSON.stringify({ ...row, wall: Date.now() }) + '\n')
    const call = operation => JSON.parse(execFileSync(o.python, ['-I', '-B', o.helper, selection, operation],
      { timeout: 5000, maxBuffer: 1024 * 1024, env: { PATH: '/opt/homebrew/bin:/usr/bin:/bin' } }).toString())
    const last = new Map()
    await ctx.tool.transform(editor => {
      // Keep network, arbitrary execution and delegation outside the pilot tool surface.
      const allowed = new Set(['read', 'glob', 'grep', 'patch', 'edit', 'write'])
      for (const tool of editor.list()) if (!allowed.has(tool.id)) editor.remove(tool.id)
      if (o.arm !== 'baseline') {
        for (const operation of ['prepare', 'check']) editor.add({
          name: 'template_' + operation,
          options: { codemode: false },
          description: operation === 'prepare' ? 'Read the three selected templates and context hashes.' : 'Check the selected document batch for preservation, residue and local links.',
          input: { type: 'object', properties: {}, additionalProperties: false },
          execute: async () => {
            const began = performance.now()
            const result = call(operation)
            log({ kind: 'helper', operation, ok: result.ok, milliseconds: performance.now() - began })
            return { content: JSON.stringify(result) }
          },
        })
      }
      if (o.arm === 'probe') editor.add({
        name: 'pilot_batch', description: 'Observe the selected document batch without changing any file.',
        options: { codemode: false },
        input: { type: 'object', properties: {}, additionalProperties: false },
        execute: async () => ({ content: 'batch observation complete' }),
      })
    })
    log({ kind: 'catalog', tools: (await ctx.tool.list()).map(tool => tool.id) })
    if (!['hook', 'probe'].includes(o.arm)) return
    await ctx.tool.hook('execute.after', e => {
      if (e.status !== 'completed') return
      if (!['patch','edit','write','pilot_batch'].includes(e.tool)) {
        log({ kind: 'ignored', tool: e.tool }); return
      }
      const began = performance.now()
      try {
        const result = call('check')
        const previous = last.get(e.sessionID)
        const text = result.ok || (result.fingerprint && result.fingerprint === previous) ? '' : result.notice
        const original = e.result?.content
        if (text) {
          if (Array.isArray(original)) e.result = { ...e.result, content: [...original, { type: 'text', text }] }
          else if (typeof original === 'string') e.result = { ...e.result, content: original + '\n\n' + text }
          else throw new Error('unsupported tool result shape')
        }
        if (result.fingerprint) last.set(e.sessionID, result.fingerprint)
        log({ kind: 'hook', tool: e.tool, ok: result.ok, fingerprint: result.fingerprint,
              notice: text, milliseconds: performance.now() - began,
              original_type: typeof original,
              original_sha256: createHash('sha256').update(JSON.stringify(original) ?? 'null').digest('hex') })
      } catch {
        log({ kind: 'hook_error', tool: e.tool, milliseconds: performance.now() - began })
      }
    })
  },
}
