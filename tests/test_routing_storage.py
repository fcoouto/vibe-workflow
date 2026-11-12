"""F5.S: the approved storage amendment. UTF-8 byte guards for the shared ledger body, admissions, holds and bindings;
growing writes prepay every owner's future growth (R), including a hold's conversion into a run and native task.

Real admission and dispatch entry points, disposable ledgers and fake data only. Tests that shrink SOFT_RECORD are
labelled scaled: they prove the transition logic, not a fill of the 192 MiB default; the default is proved by the
source-backed encoded maxima and reserve inequalities below.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from runtime import ledger_versions, routing, routing_gates, usage_budget as budget
from tests import test_routing_admission as adm

KIB, MIB = 1024, 1024 * 1024
NOW = 1_800_000_000  # a realistic ten-digit clock
TEXTS = dict(ascii='x', bmp='一', non_bmp='\U0001F600')


def size(value):
    return len(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode())


def text(tag, length, char):
    return (tag + char * length)[:length]


def caller_maximum(char, now=NOW):
    """The largest admission the caller-input validators accept: ten shortlisted routes x MAX_WINDOWS windows, every
    caller text at MAX_ID, MAX_REASONS reason codes and a maximal workflow binding, all in `char`."""
    routes, pools = [], {}
    for i in range(routing.MAX_SHORTLIST):
        pool = text('p%02d' % i, routing.MAX_ID, char)
        windows = [text('w%d' % k, routing.MAX_ID, char) for k in range(routing.MAX_WINDOWS)]
        routes.append(dict(route=text('r%02d' % i, routing.MAX_ID, char), strategy_id=text('s%02d' % i, routing.MAX_ID, 'x'),
                           provider=text('pv', routing.MAX_ID, char), model=text('m%02d' % i, routing.MAX_ID, char),
                           effort=text('e', routing.MAX_ID, char), harness=text('h', routing.MAX_ID, char),
                           account=text('a%02d' % i, routing.MAX_ID, char), billing_class='included_subscription',
                           pool=pool, demand={w: 999 for w in windows}, capable=True, permitted=True,
                           binding_enforceable=True))
        pools[pool] = dict(windows=[dict(id=w, limit=10**9, used=0, length=10**9, reset_at=now + 10**8 + k,
                                         rolling=False, fresh=True) for k, w in enumerate(windows)])
    job = dict(locator={key: '/' + char * (limit - 1) for key, limit in routing_gates.LOCATOR_LIMITS.items()},
               role='r' * routing_gates.MAX_ROLE, revision='f' * 64, semantic_digest='f' * 64,
               dependencies=dict(count=routing_gates.MAX_DEPENDENCIES, digest='f' * 64), ownership='f' * 64,
               resource='f' * 64)
    request = dict(adm.assignment(text('a', routing.MAX_ID, char), risk_band='high', floor_filled=True,
                                  allowed_harnesses=[text('h', routing.MAX_ID, char)] +
                                  [text('h%d' % i, routing.MAX_ID, char) for i in range(1, 8)]),
                   task=text('t', routing.MAX_ID, char))
    ranked = dict(adm.ranking(*[r['strategy_id'] for r in routes]), table_version=text('tv', routing.MAX_ID, char),
                  profile_digest=text('pd', routing.MAX_ID, char),
                  reason_codes=[text('rc%02d' % i, routing.MAX_ID, char) for i in range(routing.MAX_REASONS)])
    snapshot = dict(adm.observations(routes, pools, routes_version=text('rv', routing.MAX_ID, char), epoch_at=now,
                                     snapshot_id=text('sn', routing.MAX_ID, char)), now=now)
    return request, ranked, snapshot, job


class Ledger(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'ledger'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)
        self.addCleanup(self.ledger.close)

    def row(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0]


class ConstantTests(unittest.TestCase):
    def test_the_approved_limits_are_utf8_bytes(self):
        self.assertEqual((budget.MAX_ADMISSION_BYTES, budget.MAX_HOLD_BYTES, budget.MAX_BINDING_BYTES,
                          budget.SOFT_RECORD, budget.MAX_RECORD), (256 * KIB, 32 * KIB, 16 * KIB, 192 * MIB, 256 * MIB))
        self.assertEqual((budget.MAX_EVENT_BYTES, budget.MAX_ROOT_BYTES), (16384, 98304))  # character rows, unchanged
        self.assertEqual((budget.DISPATCH_HOLD_BYTES, budget.SETTLEMENT_HOLD_BYTES, budget.MAX_INCOMPLETE_BYTES),
                         (256, 352, 1024))

    def test_contract_bounds_the_reserves_rely_on(self):
        """A change to any of these source bounds must revisit the measured maxima and reserves below."""
        self.assertEqual((routing.MAX_ID, routing.MAX_WINDOWS, routing.MAX_SHORTLIST, routing.MAX_REASONS),
                         (256, 8, 10, 16))
        self.assertEqual(routing_gates.LOCATOR_LIMITS, dict(client=32, cwd=1024, session_id=256, task_id=128,
                                                            job_id=128, attempt_id=128))
        self.assertEqual((budget.MAX_SETTLED_CYCLES, budget.MAX_SETTLED_TASKS, budget.MAX_HOLDS, budget.MAX_RUNS),
                         (16, 512, 256, 1000))


class AdmissionSizeTests(Ledger):
    """Public `routing.admit`: the maximal caller and shipped shapes fit their byte bounds with >= 25% room."""

    def admit(self, request, ranked, snapshot, job):
        return routing.admit(request, ranked, adm.settings(), snapshot, ledger=self.ledger, workflow=job)

    def test_caller_maximum_records_holds_and_bindings_fit_in_every_text_class(self):
        for label, char in TEXTS.items():
            with self.subTest(label):
                self.setUp()
                request, ranked, snapshot, job = caller_maximum(char)
                self.assertTrue(routing_gates.binding_fits(job))
                result = self.admit(request, ranked, snapshot, job)
                self.assertEqual(result['status'], 'admitted', result)
                data = self.ledger.snapshot()
                record, hold = data['admissions'][-1], data['holds'][request['attempt_id']]
                self.assertLessEqual(size(record), budget.MAX_ADMISSION_BYTES * 3 // 4)
                self.assertLessEqual(size(hold), budget.MAX_HOLD_BYTES * 3 // 4)
                self.assertLessEqual(size(record['binding']), budget.MAX_BINDING_BYTES * 3 // 4)
                self.assertEqual(record['attempt_id'], request['attempt_id'])  # exact Unicode values survive
                chosen = next(r for r in snapshot['routes'] if r['route'] == record['route'])
                self.assertEqual(record['strategy']['model'], chosen['model'])
                self.assertIn(char, self.row())  # stored as UTF-8, not escaped

    def test_shipped_maximum_fits_at_a_ten_digit_clock(self):
        adm.NOW = NOW
        try:
            request, ranked, snapshot, job = adm.largest_replay(unicode=True)
        finally:
            adm.NOW = 1_000_000
        result = self.admit(request, ranked, snapshot, job)
        self.assertEqual(result['status'], 'admitted', result)
        self.assertLessEqual(size(self.ledger.snapshot()['admissions'][-1]), budget.MAX_ADMISSION_BYTES * 3 // 4)

    def test_one_byte_over_the_record_or_hold_bound_refuses_before_any_hold(self):
        base = dict(attempt_id='big', task='t', bucket=[], shares={}, pad='')
        stored = lambda pad: dict(base, pad=pad, nonce='0' * 32)  # noqa: E731  the nonce the ledger adds
        filler = '\U0001F600' * ((budget.MAX_ADMISSION_BYTES - size(stored(''))) // 4)
        exact = filler + 'x' * (budget.MAX_ADMISSION_BYTES - size(stored(filler)))
        self.assertEqual(size(stored(exact)), budget.MAX_ADMISSION_BYTES)
        self.assertGreater(len(json.dumps(stored(exact))), budget.MAX_ADMISSION_BYTES)  # escaped it would not fit
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
            self.ledger.admit('big', lambda data: dict(record=dict(base, pad=exact + 'x'), vector={}, account='a'))
        self.assertEqual(self.ledger.snapshot(), before)
        self.ledger.admit('big', lambda data: dict(record=dict(base, pad=exact), vector={}, account='a'))
        self.assertIn('big', self.ledger.snapshot()['holds'])

    def test_the_maximal_hold_fits_and_one_byte_over_refuses_atomically(self):
        char = '\U0001F600'
        name = lambda tag: (tag + char * 256)[:256]  # noqa: E731  `name()` bound
        key = lambda i: budget.WIN + ('%d' % i + char * 256)[:256] + '/' + char * 256  # noqa: E731  MAX_KEY
        self.assertEqual(len(key(0)), budget.MAX_KEY)
        record = dict(attempt_id=name('h'), task=name('t'), bucket=[], shares={})
        decide = lambda data: dict(record=record, vector={key(i): 1 for i in range(8)}, account=name('a'))  # noqa: E731
        with mock.patch.object(budget, 'MAX_HOLD_BYTES', 1):  # scaled: measure the stored shape first
            with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
                self.ledger.admit(record['attempt_id'], decide)
        before = self.ledger.snapshot()
        self.ledger.admit(record['attempt_id'], decide)
        hold = self.ledger.snapshot()['holds'][record['attempt_id']]
        measured = size(hold)
        self.assertLessEqual(measured, budget.MAX_HOLD_BYTES * 3 // 4, measured)  # the largest valid hold
        self.assertGreater(len(json.dumps(hold, separators=(',', ':'))), measured)  # escaped is larger
        for limit, admitted in ((measured - 1, False), (measured, True)):  # scaled boundary, in bytes
            with self.subTest(limit=limit):
                self.tearDown(); self.setUp()
                with mock.patch.object(budget, 'MAX_HOLD_BYTES', limit):
                    if admitted:
                        self.ledger.admit(record['attempt_id'], decide)
                        self.assertIn(record['attempt_id'], self.ledger.snapshot()['holds'])
                    else:
                        snapshot = self.ledger.snapshot()
                        with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
                            self.ledger.admit(record['attempt_id'], decide)
                        self.assertEqual(self.ledger.snapshot(), snapshot)
        del before


class BindingTests(unittest.TestCase):
    def binding(self, char):
        _, _, _, job = caller_maximum(char)
        return job

    def test_unicode_bindings_are_measured_in_bytes_at_admission_and_dispatch(self):
        job = self.binding('\U0001F600')
        stored = routing_gates.binding(job, 'f' * 64)
        self.assertGreater(len(json.dumps(stored, separators=(',', ':'))), 2560)  # refused by the old escaped guard
        self.assertTrue(routing_gates.binding_fits(job))
        self.assertTrue(routing_gates.valid_binding(stored))  # the dispatch re-check uses the same byte guard
        self.assertLessEqual(size(stored), budget.MAX_BINDING_BYTES * 3 // 4)

    def test_the_binding_byte_guard_boundary_on_a_source_valid_binding(self):
        """Scaled: a valid binding cannot reach 16 KiB (its maximum is measured above), so the guard is isolated by
        setting the limit to a fully source-valid binding's own encoded size."""
        stored = routing_gates.binding(self.binding('\U0001F600'), 'f' * 64)
        measured = size(stored)
        for limit, valid in ((measured - 1, False), (measured, True)):
            with self.subTest(limit=limit), mock.patch.object(routing_gates, 'MAX_BINDING_BYTES', limit):
                self.assertIs(routing_gates.valid_binding(stored), valid)

class BodyTests(Ledger):
    def test_the_body_is_compact_utf8_and_an_escaped_ledger_reads_and_shrinks_on_its_first_real_write(self):
        name = 'org-\U0001F600-一'
        self.ledger.configure(name, adm.FUND_POLICY, 0)
        self.assertIn(name, self.row())
        escaped = json.dumps(json.loads(self.row()), allow_nan=False, separators=(',', ':'))  # what old code wrote
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE ledger SET body=? WHERE id=1', (escaped,)); db.commit()
        snapshot = self.ledger.snapshot()
        self.assertIn(name, snapshot['organizations'])
        self.assertEqual(self.row(), escaped)  # a read-only or unchanged transaction writes nothing
        with self.ledger.transaction() as data:
            pass
        self.assertEqual(self.row(), escaped)
        self.ledger.observe(name, 10, adm.NOW, issued=10)
        self.assertLess(len(self.row().encode()), len(escaped.encode()) + 64)
        self.assertIn(name, self.row())
        self.assertEqual(self.ledger.snapshot()['organizations'][name]['balance'], 10)


class ReserveTests(Ledger):
    def test_remaining_growth_follows_the_approved_formula(self):
        from runtime import usage_budget_storage as storage
        data = self.ledger.snapshot()
        self.assertEqual(storage.remaining_growth(data), 262144)
        data['runs'] = {'r%d' % i: {} for i in range(3)}
        data['holds'] = {'a': dict(closed=False, amount=5, state='dispatched'), 'b': dict(closed=False, amount=0,
                         state='admitted'), 'c': dict(closed=True, amount=5, state='released'),
                         'd': dict(closed=False, amount=5, state='converted')}
        data['organizations'] = {'o': {}}
        data['native'] = {'k': {}}
        data['routing'] = {'s': {}, 't': {}}
        self.assertEqual(storage.remaining_growth(data), 4096 * 3 + 1024 * 4 + 65536 * 1 + 24576 + 8192 + 4096 * 2
                         + 262144)

    def test_measured_owner_growth_fits_each_reserve(self):
        """Source-backed maxima with 4-byte text and MAX_INTEGER numbers, encoded as the body stores them."""
        big, char = budget.MAX_INTEGER, '\U0001F600'
        t = lambda n=routing.MAX_ID: char * n  # noqa: E731
        key = json.dumps([t(), t()])  # `_native` keys keep their escaped text as the value
        run = {t(): dict(consent=dict(scope=t(), revision=big, qualification_revision=big), organization=t(), task=t(),
                         digest=t(), revision=big, native=key, closed=False, account=t(),
                         live_admission=dict(pin=t(), maximum=big),
                         calls=dict(count=dict(maximum=big, cost=None, digest=t(), state='reserved', cycle=t(),
                                               provision=big)))}
        grown = json.loads(json.dumps(run))
        call = next(iter(grown.values()))['calls']['count']
        call.update(cost=big, state='rejected-before-send', over_bound=big, covered=True,
                    incomplete=dict(reason='x' * (budget.MAX_INCOMPLETE_BYTES - 20)))
        call_growth = size(grown) - size(run)
        native = {key: dict(account=t(), task=t(), latest_at=big)}
        finished = size([key])
        hold_growth = budget.DISPATCH_HOLD_BYTES + budget.SETTLEMENT_HOLD_BYTES + 16
        conversion = size(run) - 2 + call_growth + size(native) - 2 + finished + hold_growth
        self.assertLessEqual(conversion, 65536 * 3 // 4, conversion)  # the convertible-hold reserve
        self.assertLessEqual(2 * call_growth, 4096)  # RUN_RES: two calls
        self.assertLessEqual(finished, 8192)  # NATIVE_RES
        cycles = budget.MAX_SETTLED_CYCLES * (size({t(): big}) - 1)
        org_scalars = size(dict(live_funding=dict(observed_at=big, digest='f' * 64), observation_source=t(64),
                                funding_state='unverified', covered=big, legacy_block=True, disabled=True))
        self.assertLessEqual(cycles + org_scalars, 24576)  # ORG_RES
        self.assertLessEqual(hold_growth, 1024)  # HOLD_RES


class ScaledSoftTests(Ledger):
    def stage(self, observed_at):
        from dataclasses import asdict
        from runtime import usage_admission
        binding = dict(organization='org', project='project', credential_ref='OPENAI_API_KEY')
        grant = usage_admission.Grant(organization='org', project='project', balance=999 * adm.M, expiry=2 * 10**9,
                                      observed_at=observed_at, source='platform-credit-grants', pool='prepaid')
        with self.ledger.transaction(grows=True) as data:
            data.setdefault('routing', {})['scope'] = dict(revision=3, binding=binding)
            org = data['organizations']['org']
            self.ledger._note_balance(org, grant.balance)
            org['observed_at'] = observed_at
            org['live_funding'] = dict(observed_at=observed_at, digest=usage_admission.digest(asdict(grant)))
        return grant, usage_admission.digest(binding)

    def coverage(self, grant, binding_digest, *charges):
        from runtime import usage_admission
        return usage_admission.Coverage(scope='scope', config_revision=3, binding_digest=binding_digest, grant=grant,
                                        charges=tuple(usage_admission.ChargeRef(*c) for c in charges))

    """Scaled: SOFT_RECORD is lowered to just above the current body plus R, so the next growing write is refused
    while every already-admitted owner still reaches its maximal shape through non-growing writers."""

    def fund(self):
        self.ledger.configure('org', adm.FUND_POLICY, 0)
        self.ledger.observe('org', 1000 * adm.M, adm.NOW, issued=1000 * adm.M)

    def test_growing_writes_stop_at_the_reserve_and_settlement_conversion_and_release_still_commit(self):
        from runtime import usage_budget_storage as storage
        self.fund()
        char = '\U0001F600'
        attempt, account, task = char * 200, 'acct-' + char * 200, 'task-' + char * 200
        self.ledger.admit(attempt, lambda data: dict(record=dict(attempt_id=attempt, task=task, bucket=[], shares={}),
                                                    vector={'org:org': 10 * adm.M}, account=account, now=adm.NOW,
                                                    revision=1, task=task))
        self.ledger.begin('org', 'other', 'run', 'packet', adm.M, 1, adm.NOW,
                          dict(account='acct', task='other', windows=[]))
        self.ledger.dispatch('run', 'count', 1, adm.NOW)
        grant, digest = self.stage(adm.NOW + 5)  # synthetic trusted staging, as the A2 coverage tests do
        data = self.ledger.snapshot()
        soft = storage.encoded_size(data) + storage.remaining_growth(data) + 64
        with mock.patch.object(budget, 'SOFT_RECORD', soft):
            with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
                self.ledger.configure('another-org-' + char * 50, adm.FUND_POLICY, 0)
            # Already-admitted work: the permit (dispatch_hold), conversion (new run + native), settlement,
            # uncertainty, typed coverage and native release.
            from tests.fixture_routing_funding import authorize
            self.assertTrue(authorize(self.ledger, attempt, now=adm.NOW)['issued'])
            self.assertFalse(authorize(self.ledger, attempt, now=adm.NOW)['issued'])  # the permit is issued once
            native = dict(account=account, task=task, windows=[])
            self.ledger.convert_hold(attempt, 'org', 'packet-' + char * 100, 9 * adm.M, 1, adm.NOW + 10, native)
            self.ledger.dispatch(attempt, 'count', 1, adm.NOW + 10)
            self.ledger.uncertain(attempt, 'count', incomplete=dict(reason=char * 200))
            self.ledger.complete(attempt, 'count', adm.M)
            self.ledger.close_run(attempt)
            self.ledger.complete('run', 'count', adm.M // 2)  # a known actual charge
            self.ledger.close_run('run')
            total = budget.Ledger._total(self.ledger.snapshot(), 'org')
            credit = self.ledger.credit_headroom('org', adm.NOW + 10)
            self.assertEqual(self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 'run', 'count', adm.M // 2)),
                                                        adm.NOW + 10)['marked'], 1)
            self.assertEqual(self.ledger.credit_headroom('org', adm.NOW + 10), credit + adm.M // 2)
            self.assertEqual(budget.Ledger._total(self.ledger.snapshot(), 'org'), total)  # authority still counts it
            self.ledger.release_native(account, task, native, adm.NOW)
            after = self.ledger.snapshot()
            room = soft - storage.encoded_size(after) - storage.remaining_growth(after)
            self.assertGreaterEqual(room, 0)  # the invariant holds after every non-growing write
            before = self.row()
            with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):  # storage, not money
                self.ledger.admit('late', lambda d: dict(record=dict(attempt_id='late', task='late', bucket=[], shares={},
                                                                     pad='x' * (room + 1)), vector={}, account='a'))
            self.assertEqual(self.row(), before)
            self.assertLessEqual(len(self.row().encode()), soft)
            self.assertIn(attempt, self.ledger.snapshot()['runs'])

    def test_lot_metadata_growth_is_a_growing_write_but_scalar_refresh_is_not(self):
        from runtime import usage_budget_storage as storage
        policy = dict(adm.FUND_POLICY)
        policy.pop('allowance_bp'); policy['allowance'] = 'no-limit'
        self.ledger.configure('org', policy, 0)
        lots = [dict(id='lot-a', balance=5, expiry=adm.NOW * 2, verified=True)]
        self.ledger.observe('org', 10, adm.NOW, issued=10, lots=lots)
        data = self.ledger.snapshot()
        soft = storage.encoded_size(data) + storage.remaining_growth(data) + 8
        with mock.patch.object(budget, 'SOFT_RECORD', soft):
            same_length = [dict(lots[0], id='lot-' + '\U0001F600' * 20)]  # one lot, more bytes
            before = self.row()
            with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
                self.ledger.observe('org', 9, adm.NOW + 1, lots=same_length)
            self.assertEqual(self.row(), before)
            self.ledger.observe('org', 9, adm.NOW + 2)  # scalar refresh: never a growing write
            self.ledger.observe('org', 8, adm.NOW + 3, lots=[dict(lots[0], balance=4)])  # equal size
            self.ledger.observe('org', 7, adm.NOW + 4, lots=[])  # shrink
        self.assertEqual(self.ledger.snapshot()['organizations']['org']['balance'], 7)


class SeparateRowTests(Ledger):
    def test_event_rows_keep_their_character_limit(self):
        char = '\U0001F600'
        with self.ledger.transaction() as data:
            budget._create_tables(self.ledger.db)
            body = dict(attempt_id='a', kind='observed', recorded_at=adm.NOW, note='')
            room = budget.MAX_EVENT_BYTES - len(budget._compact(body))
            self.ledger._store_event(data, 'fits', '0' * 64, dict(body, note=char * room), adm.NOW, effect=False)
            with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
                self.ledger._store_event(data, 'over', '0' * 64, dict(body, note=char * (room + 1)), adm.NOW,
                                         effect=False)


class RootRowTests(Ledger):
    def test_root_rows_keep_their_character_limit(self):
        char = '\U0001F600'
        with self.ledger.transaction() as data:
            budget._create_tables(self.ledger.db)
            room = budget.MAX_ROOT_BYTES - len(budget._compact(dict(note='')))
            self.ledger._save_root(data, 'root-fits', dict(note=char * room), adm.NOW)  # 4-byte text, character limit
            with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
                self.ledger._save_root(data, 'root-over', dict(note=char * (room + 1)), adm.NOW)
        with closing(sqlite3.connect(self.path)) as db:
            rows = [root for (root,) in db.execute('SELECT root FROM roots')]
        self.assertEqual(rows, ['root-fits'])  # the refused row wrote nothing


class OldReaderTests(unittest.TestCase):
    def test_old_escaped_guards_refuse_what_new_code_stores(self):
        """Pre-amendment readers counted escaped characters: a Unicode binding stored now fails their dispatch
        re-check (fail-closed), and a body above their 13,100,000-character limit is refused as invalid."""
        _, _, _, job = caller_maximum('\U0001F600')
        stored = routing_gates.binding(job, 'f' * 64)
        self.assertGreater(len(json.dumps(stored, separators=(',', ':'))), 2560)
        self.assertGreater(budget.MAX_RECORD, 13_100_000)



class UnicodeBindingDispatchTests(Ledger):
    """The real Service prepare and dispatch with a source-valid Unicode locator whose escaped binding exceeds the old
    2,560-character guard: it prepares, dispatches once, keeps its exact binding and still refuses a changed job."""

    def test_a_unicode_binding_prepares_and_dispatches_once(self):
        from tests import test_routing_gates as gates
        case = gates.GateBase('setUp')
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.addCleanup(case.service.close)
        char = '\U0001F600'
        job_id, session, task = 'job-' + char * 120, 's-' + char * 250, 't-' + char * 120
        where = gates.locator(job_id, session_id=session, task_id=task)
        case.workflow.write({job_id: gates.entry()})
        response = case.call(gates.prepare('u1', workflow=where))
        self.assertEqual(response['state'], 'admitted', response)
        binding = budget.Ledger.admission(case.snapshot(), 'u1')['binding']
        self.assertEqual(binding['job']['locator'], where)  # exact Unicode identity kept
        self.assertGreater(len(json.dumps(binding, separators=(',', ':'))), 2560)  # beyond the old escaped guard
        self.assertLessEqual(size(binding), budget.MAX_BINDING_BYTES * 3 // 4)
        first = case.call(gates.dispatch('u1'))
        self.assertEqual(first['state'], 'dispatched', first)
        again = case.call(gates.dispatch('u1'))
        self.assertEqual(again['state'], 'already_dispatched')
        self.assertNotIn('authority', again)  # no second permit
        self.assertEqual(budget.Ledger.admission(case.snapshot(), 'u1')['binding'], binding)
        self.assertEqual(case.call(gates.prepare('u2', workflow=where))['state'], 'admitted')
        case.workflow.update(job_id, revision=2)  # the canonical job changed after preparation
        case.assertRefused('u2', 'binding_stale')

if __name__ == '__main__':
    unittest.main()
