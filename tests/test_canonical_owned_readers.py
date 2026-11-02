"""Current D4-born acquisition-owner controls; qualification observations are synthetic fixtures."""
import json
import unittest

from runtime import changes, evidence, recovery_uses, review_storage, startup, state
from runtime import resource_accounting as accounting
from tests.fixture_canonical_birth import CanonicalBirthFixture


class CanonicalOwnedReaderTests(unittest.TestCase):
    def setUp(self):
        self.authorities = []
        for _ in range(2):
            birth = CanonicalBirthFixture('runTest')
            def cleanup(birth=birth):
                self.assertTrue(birth.doCleanups(), 'Canonical birth fixture cleanup failed')
            self.addCleanup(cleanup)
            fixture, acquired, _ = birth.accepted_birth()
            def cleanup_nested(fixture=fixture):
                self.assertTrue(fixture.doCleanups(), 'Nested canonical birth fixture cleanup failed')
            self.addCleanup(cleanup_nested)
            scope = startup._entry_scope(acquired)
            context = evidence._evidence_context(scope)
            with evidence._context_database(context) as db:
                baseline = state._source_baseline(None, db, context=context)
            self.assertIsNotNone(baseline, 'SETUP HOLD: genuine D4 source baseline absent')
            expected_owner = dict(kind='project', project_id=scope.project_id, task=None)
            self.authorities.append((context, baseline['reference'], expected_owner))
        self.assertNotEqual(self.authorities[0][0].database, self.authorities[1][0].database)
        self.assertNotEqual(self.authorities[0][0].authority_set, self.authorities[1][0].authority_set)
        self.context, self.reference, self.expected_owner = self.authorities[0]

    def resources(self):
        comparison = recovery_uses.CaptureBudget(); discovery = review_storage._budget()
        result = accounting.Resources(comparison, accounting.Discovery(discovery),
            accounting.Counter(1 << 30), accounting.Counter(64 << 20))
        self.assertIs(result.comparison, comparison)
        self.assertIs(result.discovery.state, discovery)
        return result


    def acquire(self, resources, db):
        return resources.acquisition().bind(state._metadata, db, self.reference['name'])


    @staticmethod
    def payload(trace):
        return [sql for sql in trace if sql.lower().startswith('select name,digest,owner_key,class,')]


    @staticmethod
    def leases(resources):
        return {key: (lease, lease.amount) for key, lease in resources._leases.items()}


    def assert_leases(self, resources, snapshot):
        self.assertEqual(set(resources._leases), set(snapshot))
        for key, (lease, amount) in snapshot.items():
            self.assertIs(resources._leases[key], lease)
            self.assertEqual(resources._leases[key].amount, amount)


    def assert_failures(self, resources, snapshot):
        self.assertEqual(set(resources._failed), set(snapshot))
        for key, (lease, error) in snapshot.items():
            self.assertIs(resources._failed[key][0], lease)
            self.assertIs(resources._failed[key][1], error)


    def test_wrong_context_reuse_rejects_before_metadata_and_matching_reuse_preserves_db(self):
        r = self.resources(); trace = []; rejected = None
        with changes._authority_guards([item[0].directory for item in self.authorities]):
            with evidence._context_database(self.context, resources=r) as db:
                db.set_trace_callback(lambda sql: trace.append(sql[:2048]))
                with evidence._captured_existing_database(self.context, db, r) as supplied:
                    self.assertIs(supplied, db); self.assertTrue(db.in_transaction)
                    owner = self.acquire(r, db)
                    with owner as value:
                        self.assertEqual(value['owner'], self.expected_owner)
                    del value; owner.dispose()
                self.assertTrue(db.in_transaction)
                trace.clear()
                try:
                    with evidence._captured_existing_database(self.authorities[1][0], db, r):
                        owner = self.acquire(r, db)
                        with owner as value:
                            pass
                        del value; owner.dispose()
                except state.StateError as error:
                    rejected = error
                self.wrong_context_trace = tuple(trace)
                self.causal = dict(rejected=type(rejected) is state.StateError,
                    actual_metadata_payload=bool(self.payload(trace)), transaction_live=db.in_transaction)
                self.assertIsNotNone(rejected, 'Wrong genuine authority accepted supplied database')
                self.assertFalse(self.payload(trace), 'Metadata payload read preceded context rejection')
                self.assertTrue(db.in_transaction)


    def test_foreign_output_lease_rejects_disposal_before_any_mutation(self):
        first, second = self.resources(), self.resources()
        owners = []; values = []
        with changes._authority_guards([self.context.directory]):
            for r in (first, second):
                with evidence._context_database(self.context, resources=r) as db:
                    owner = self.acquire(r, db)
                    with owner as value:
                        values.append(value)
                    owners.append(owner)
        foreign = max(owners[1].leases.values(), key=lambda lease: lease.amount)
        self.assertGreater(foreign.amount, 4096, 'SETUP HOLD: decoded output lease not acquired')
        self.assertIs(foreign.owner, second); self.assertIs(second._leases[id(foreign)], foreign)
        owners[0].leases[id(foreign)] = foreign
        before = [(r.live.used, self.leases(r), dict(r._failed)) for r in (first, second)]
        registries = [dict(r._acquisitions) for r in (first, second)]
        owner_leases = [dict(owner.leases) for owner in owners]
        outputs = tuple(owner.value for owner in owners); rejected = None
        try:
            owners[0].dispose()
        except accounting.Exhausted as error:
            rejected = error
        self.foreign_anchors = (owners, values, outputs, foreign)
        self.causal = dict(rejected=type(rejected) is accounting.Exhausted,
            foreign_live_decreased=second.live.used < before[1][0],
            foreign_lease_removed=id(foreign) not in second._leases,
            foreign_output_anchored=outputs[1] is values[1] and owners[1].value is outputs[1])
        self.assertIsNotNone(rejected, 'Foreign real output lease was refunded')
        for r, snapshot, registry in zip((first, second), before, registries):
            self.assertEqual(r.live.used, snapshot[0]); self.assert_leases(r, snapshot[1])
            self.assert_failures(r, snapshot[2]); self.assertEqual(set(r._acquisitions), set(registry))
            for key, owner in registry.items(): self.assertIs(r._acquisitions[key], owner)
        for owner, registry in zip(owners, owner_leases):
            self.assertEqual(set(owner.leases), set(registry))
            for key, lease in registry.items(): self.assertIs(owner.leases[key], lease)
        self.assertTrue(all(owner.value is value for owner, value in zip(owners, outputs)))


    def test_retained_real_decoder_error_blocks_reuse_despite_missing_failed_entry(self):
        r = self.resources(); retained = None
        with changes._authority_guards([self.context.directory]):
            with evidence._context_database(self.context, write=True, resources=r) as db:
                stored = db.execute('select data from evidence_metadata where name=?',
                    (self.reference['name'],)).fetchone()[0]
                malformed = stored[:-1]; owner = self.acquire(r, db)
                db.execute('update evidence_metadata set data=? where name=?', (malformed, self.reference['name']))
                try:
                    try:
                        with owner:
                            self.fail('Malformed genuine metadata accepted')
                    except state.StateError as error:
                        retained = error
                finally:
                    db.execute('update evidence_metadata set data=? where name=?', (stored, self.reference['name']))
                self.assertIsNotNone(retained, 'SETUP HOLD: actual decoder error absent')
                decoder = retained
                for _ in range(16):
                    if isinstance(decoder, json.JSONDecodeError): break
                    decoder = decoder.__cause__ or decoder.__context__
                    self.assertIsNotNone(decoder, 'SETUP HOLD: decoder chain absent')
                self.assertIsInstance(decoder, json.JSONDecodeError)
                self.assertEqual(decoder.doc, malformed); self.assertIsNotNone(retained.__traceback__)
                before = (r.live.used, self.leases(r)); failed = dict(r._failed)
                self.assertTrue(failed); self.assertIsNotNone(owner.error)
                self.error_anchors = (owner, retained, decoder, failed)
                r._failed.clear()  # Deliberate registry corruption, not an accounting reset.
                rejected = None
                try:
                    try: r.check_reusable()
                    except accounting.Exhausted as error: rejected = error
                finally: r._failed.update(failed)  # Restore genuine entries for mandatory cleanup.
                self.causal = dict(actual_decoder=type(decoder) is json.JSONDecodeError,
                    document_matches=decoder.doc == malformed, traceback_retained=retained.__traceback__ is not None,
                    owner_error_retained=owner.error is retained, reuse_accepted=rejected is None)
                self.assertIsNotNone(rejected, 'Retained acquisition error escaped reuse gate')
                self.assertEqual(r.live.used, before[0]); self.assert_leases(r, before[1])
                self.assert_failures(r, failed)
