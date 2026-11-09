// Load this function only from the trusted installed skill inside functions.exec.
(async (request, tools) => {
    const {root, python, checkout, threadId, hostId, action, plan, reviewer} = request;
    if (![root, python, checkout].every(path => typeof path === 'string' && path.startsWith('/'))
        || !threadId || !hostId || !['freeze', 'execute'].includes(action)) {
        throw new Error('Invalid Codex host invocation');
    }
    if (action === 'execute' && (!plan || plan.checkout !== checkout || plan.thread_id !== threadId
        || plan.host_id !== hostId || (request.approvalTurnId && plan.mode !== 'smart' && !plan.pilot_digest))) {
        throw new Error('Codex approval context changed');
    }

    const result = response => {
        if (response?.isError) throw new Error('Codex host observation unavailable');
        return response.structuredContent ?? JSON.parse(response.content.find(item => item.type === 'text').text);
    };
    const read = async (turnLimit, cursor) => {
        const selectionLength = action === 'execute' && typeof request.manualSelection === 'string'
            ? request.manualSelection.length : 0;
        const args = {threadId, hostId, turnLimit,
            maxOutputCharsPerItem: Math.max(350, Math.min(selectionLength + 1, 8001)), includeOutputs: false};
        if (cursor) args.cursor = cursor;
        const page = result(await tools.mcp__codex_app__read_thread(args));
        if (page.thread.kind !== 'codex' || page.thread.id !== threadId || page.thread.hostId !== hostId
            || page.thread.cwd !== checkout) throw new Error('Codex thread changed');
        return page;
    };
    const snapshot = (page, turn, includeApproval) => ({
        thread: {kind: page.thread.kind, id: page.thread.id, hostId: page.thread.hostId, cwd: page.thread.cwd},
        turns: [{id: turn.id, status: turn.status, startedAt: turn.startedAt,
            items: includeApproval ? turn.items.filter(item => item.type === 'userMessage') : []}]
    });
    const page = await read(1);
    const turn = page.turns[0];
    if (turn?.status !== 'inProgress') throw new Error('Active Codex turn unavailable');
    const historical = !!request.approvalTurnId && request.approvalTurnId !== turn.id;
    const current = snapshot(page, turn, action === 'execute' && !historical && !plan.standing_consent);
    let approval = null;
    if (historical) {
        let cursor = page.page?.nextCursor;
        for (let i = 0; i < 3 && cursor && !approval; i++) {
            const older = await read(4, cursor);
            const approved = older.turns.find(item => item.id === request.approvalTurnId);
            if (approved) approval = snapshot(older, approved, true);
            cursor = older.page?.nextCursor;
        }
        if (!approval) throw new Error('Prior Codex approval unavailable');
    }

    const frame = {action, checkout, thread_snapshot: current, reviewer};
    if (action === 'freeze') {
        Object.assign(frame, {proposals: request.proposals, maximum_usd_micros: request.maximumUsdMicros,
            expires_at: request.expiresAt, pool: request.pool ?? 'benefit',
            manual_price: request.manualPrice ?? null,
            pilot_digest: request.pilotDigest ?? null, standing_consent: request.standingConsent ?? false});
    } else {
        if (request.grant?.pool === 'benefit') {
            const {benefit} = result(await tools.mcp__openai_api_credit_promo__preview_api_credit_promo({}));
            if (benefit?.currency !== 'USD' || benefit.cadence !== 'monthly'
                || Date.parse(benefit.expires_at) / 1000 !== request.grant.expiry) {
                throw new Error('Subscription benefit expiry changed or unavailable');
            }
        }
        Object.assign(frame, {plan, proposal: request.proposal, approval_snapshot: approval,
            grant: request.grant, price: request.price,
            prior_review: request.priorReview ?? null, habblaud: request.habblaud,
            manual_selection: request.manualSelection ?? null});
    }

    const bridge = `from dataclasses import asdict
import json, sys
sys.path.insert(0, sys.argv[1])
from runtime import usage_codex_host as host, usage_admission
from runtime.usage_budget import BudgetError
frame = json.load(sys.stdin)
action = frame.pop('action')
checkout = frame.pop('checkout')
try:
    frame['reviewer'] = host.Reviewer(**frame['reviewer'])
    if action == 'freeze':
        if frame['manual_price'] is not None:
            frame['manual_price'] = host.Price(**frame['manual_price'])
        plan = host.freeze(checkout, **frame)
        value = dict(plan=asdict(plan), approval=None if plan.standing_consent else host.approval_text(plan))
    else:
        plan = host.Plan(**frame.pop('plan'))
        frame['grant'] = usage_admission.Grant(**frame['grant'])
        frame['price'] = host.Price(**frame['price'])
        if frame['prior_review'] is not None:
            frame['prior_review'] = host.PilotReview(**frame['prior_review'])
        if frame.get('habblaud') is not None:
            def generation_ready(result):
                print(json.dumps(dict(generation=result), allow_nan=False), flush=True)
            frame['generation_ready'] = generation_ready
            value = dict(visibility=host.execute(plan, **frame)['visibility'])
        else:
            value = host.execute(plan, **frame)
    print(json.dumps(value, allow_nan=False))
except BudgetError as error:
    print(json.dumps(dict(ok=False, reason=str(error), uncertain=action == 'execute')))
`;
    const quote = value => "'" + value.replace(/'/g, "'\"'\"'") + "'";
    const payload = JSON.stringify(frame);
    if (encodeURIComponent(payload).length > 120000) throw new Error('Codex host input too large');
    const cmd = "printf '%s' " + quote(payload) + ' | ' + quote(python) + ' -I -B -c '
        + quote(bridge) + ' ' + quote(root);
    const settledGeneration = output => {
        if (action !== 'execute' || request.habblaud == null) return;
        try {
            const generation = JSON.parse(output.split('\n', 1)[0]).generation;
            if (generation?.accepted === false && generation.requires_native_review === true) return generation;
        } catch {}
    };
    let execution = await tools.exec_command({cmd, workdir: checkout, login: false,
        shell: '/bin/sh', max_output_tokens: 12000, yield_time_ms: 1000});
    let output = execution.output;
    let generation = settledGeneration(output);
    if (execution.session_id && !generation) {
        execution = await tools.write_stdin({session_id: execution.session_id, chars: '',
            max_output_tokens: 12000, yield_time_ms: 300000});
        output += execution.output;
        generation = settledGeneration(output);
    }
    if (generation) {
        const newline = output.indexOf('\n');
        let observation = newline < 0 ? '' : output.slice(newline + 1);
        if (execution.session_id) {
            // The proposal is available, so bound only the remaining display wait.
            try {
                execution = await tools.write_stdin({session_id: execution.session_id, chars: '',
                    max_output_tokens: 12000, yield_time_ms: 15000});
                observation += execution.output;
            } catch {}
        }
        let visibility = {delivered: false, reason: 'visibility_unconfirmed'};
        if (execution.exit_code === 0 && !execution.session_id) {
            try {
                visibility = JSON.parse(observation).visibility ?? visibility;
            } catch {}
        }
        return {...generation, visibility, ...(!plan.standing_consent
            ? {approval_turn_id: (approval ?? current).turns[0].id} : {})};
    }
    if (execution.exit_code !== 0 || execution.session_id) {
        return {ok: false, reason: 'codex_host_outcome_unverified', uncertain: action === 'execute'};
    }
    try {
        const value = JSON.parse(output);
        if (action === 'execute' && value.requires_native_review === true && !plan.standing_consent) {
            value.approval_turn_id = (approval ?? current).turns[0].id;
        }
        return value;
    } catch {
        return {ok: false, reason: 'codex_host_outcome_unverified', uncertain: action === 'execute'};
    }
})
