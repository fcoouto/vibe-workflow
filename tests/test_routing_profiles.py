"""Task profiles: coordinator-supplied, floored, reusable and never lowered by Jev."""
import copy
import unittest

from runtime import routing


def complete_profile(**changes):
    profile = dict(task_family='implementation', complexity_band='medium', risk_band='medium',
                   context_tokens=4000, tools=['edit', 'shell'], review_policy='independent',
                   uncertainty='low')
    profile.update(changes)
    return profile


def job(**changes):
    value = dict(id='edit', role='worker', scope_ref='scope-a', objective='Implement the bounded edit',
                 context='Inspect callers first', claims=[dict(kind='path', key='/repo/app.py', mode='write')],
                 tools=['edit', 'shell'], profile=complete_profile())
    value.update(changes)
    return value


class Counter:
    def __init__(self, answer=None):
        self.calls, self.answer = [], answer or {}

    def __call__(self, view, fields):
        self.calls.append((view, tuple(fields)))
        return dict(self.answer)


class ProfileTests(unittest.TestCase):
    def test_complete_coordinator_profile_makes_no_jev_call(self):
        jev = Counter()
        profile = routing.ensure_profile(job(), jev=jev)
        self.assertEqual(jev.calls, [])
        self.assertEqual(profile['task_family'], 'implementation')
        self.assertEqual(profile['provenance']['complexity_band'], 'coordinator')
        self.assertEqual(profile['provenance']['task_family'], 'coordinator')
        self.assertEqual(profile['contract_version'], routing.CONTRACT_VERSION)
        self.assertRegex(profile['semantic_digest'], r'^[0-9a-f]{64}$')

    def test_digest_ignores_balance_progress_and_retries(self):
        base = routing.ensure_profile(job())['semantic_digest']
        noisy = job(balance=3, progress_at=99, attempts_consumed=2, retries=4, dependencies=['x'],
                    resource_units_consumed=7)
        self.assertEqual(routing.ensure_profile(noisy)['semantic_digest'], base)

    def test_digest_changes_with_role_scope_tools_objective_and_requirements(self):
        base = routing.ensure_profile(job())['semantic_digest']
        variants = [job(role='review', claims=[dict(kind='path', key='/repo/app.py', mode='read')]),
                    job(claims=[dict(kind='path', key='/repo/other.py', mode='write')]),
                    job(tools=['edit']), job(objective='Something else'),
                    job(requirement_floors=dict(risk_band='high'))]
        digests = {routing.ensure_profile(value)['semantic_digest'] for value in variants}
        self.assertNotIn(base, digests)
        self.assertEqual(len(digests), len(variants))

    def test_other_scope_profile_cannot_be_reused(self):
        original = routing.ensure_profile(job())
        foreign = job(scope_ref='scope-b', profile=dict(complete_profile(complexity_band='low', risk_band='low'),
                                                        scope_ref='scope-a',
                                                        semantic_digest=original['semantic_digest']))
        jev = Counter()
        profile = routing.ensure_profile(foreign, jev=jev)
        self.assertNotEqual(profile['provenance']['complexity_band'], 'coordinator')
        self.assertIn('profile_scope_mismatch', profile['reasons'])
        self.assertEqual(profile['complexity_band'], routing.FLOORS['complexity_band'])

    def test_stale_digest_invalidates_supplied_semantics(self):
        stale = job(profile=dict(complete_profile(), semantic_digest='0' * 64))
        profile = routing.ensure_profile(stale)
        self.assertEqual(profile['provenance']['risk_band'], 'floor')

    def test_matching_digest_is_reused(self):
        first = routing.ensure_profile(job())
        again = routing.ensure_profile(job(profile=dict(complete_profile(), semantic_digest=first['semantic_digest'],
                                                        scope_ref='scope-a')))
        self.assertEqual(again['provenance']['task_family'], 'coordinator')

    def test_missing_fields_take_conservative_floors_without_jev(self):
        profile = routing.ensure_profile(job(profile={}))
        for field in ('task_family', 'complexity_band', 'risk_band', 'review_policy'):
            self.assertEqual(profile['provenance'][field], 'floor', field)
        self.assertEqual(profile['complexity_band'], routing.FLOORS['complexity_band'])
        self.assertEqual(profile['risk_band'], routing.FLOORS['risk_band'])
        self.assertEqual(profile['review_policy'], routing.FLOORS['review_policy'])
        self.assertEqual(profile['uncertainty'], 'high')

    def test_invalid_fields_take_floors(self):
        profile = routing.ensure_profile(job(profile=complete_profile(complexity_band='enormous', risk_band=7)))
        self.assertEqual(profile['provenance']['complexity_band'], 'floor')
        self.assertEqual(profile['provenance']['risk_band'], 'floor')
        self.assertEqual(profile['provenance']['task_family'], 'coordinator')

    def test_jev_runs_once_only_for_unresolved_semantic_fields(self):
        jev = Counter(dict(complexity_band='high', risk_band='high'))
        profile = routing.ensure_profile(job(profile=complete_profile(complexity_band=None, risk_band=None)), jev=jev)
        self.assertEqual(len(jev.calls), 1)
        self.assertEqual(set(jev.calls[0][1]), {'complexity_band', 'risk_band'})
        self.assertEqual(profile['provenance']['complexity_band'], 'jev')
        self.assertEqual(profile['provenance']['task_family'], 'coordinator')

    def test_jev_view_is_bounded_and_carries_no_funding_state(self):
        jev = Counter(dict(complexity_band='high'))
        routing.ensure_profile(job(profile=complete_profile(complexity_band=None), balance=9, token='secret'), jev=jev)
        view = jev.calls[0][0]
        self.assertNotIn('balance', view)
        self.assertNotIn('token', view)

    def test_jev_failure_and_garbage_fall_back_to_floors(self):
        def broken(view, fields):
            raise RuntimeError('down')
        value = job(profile=complete_profile(complexity_band=None))
        self.assertEqual(routing.ensure_profile(value, jev=broken)['provenance']['complexity_band'], 'floor')
        garbage = Counter(dict(complexity_band='bogus', unknown_field='x'))
        profile = routing.ensure_profile(value, jev=garbage)
        self.assertEqual(profile['provenance']['complexity_band'], 'floor')
        self.assertNotIn('unknown_field', profile)

    def test_supplied_and_jev_values_cannot_lower_requirement_floors(self):
        floors = dict(complexity_band='high', risk_band='critical', review_policy='strict')
        low = complete_profile(complexity_band='low', risk_band='low', review_policy='none')
        profile = routing.ensure_profile(job(profile=low, requirement_floors=floors))
        self.assertEqual((profile['complexity_band'], profile['risk_band'], profile['review_policy']),
                         ('high', 'critical', 'strict'))
        self.assertEqual(profile['provenance']['risk_band'], 'floor')
        jev = Counter(dict(risk_band='low'))
        gap = job(profile=complete_profile(risk_band=None), requirement_floors=floors)
        self.assertEqual(routing.ensure_profile(gap, jev=jev)['risk_band'], 'critical')

    def test_higher_supplied_value_is_kept(self):
        profile = routing.ensure_profile(job(profile=complete_profile(risk_band='critical'),
                                             requirement_floors=dict(risk_band='high')))
        self.assertEqual(profile['risk_band'], 'critical')
        self.assertEqual(profile['provenance']['risk_band'], 'coordinator')

    def test_write_scope_sets_a_floor_a_read_only_role_does_not(self):
        written = routing.ensure_profile(job(profile=complete_profile(risk_band='low', complexity_band='low')))
        self.assertNotEqual(written['risk_band'], 'low')
        reader = routing.ensure_profile(job(role='review', claims=[dict(kind='path', key='/repo/app.py', mode='read')],
                                            profile=complete_profile(risk_band='low', complexity_band='low')))
        self.assertEqual(reader['risk_band'], 'low')

    def test_measured_context_beats_estimate_and_tools_are_never_dropped(self):
        profile = routing.ensure_profile(job(context_tokens_measured=9000,
                                             profile=complete_profile(context_tokens=10, tools=['edit'])))
        self.assertEqual(profile['context'], dict(tokens=9000, kind='measured'))
        self.assertEqual(profile['provenance']['context'], 'metadata')
        self.assertEqual(set(profile['tools']), {'edit', 'shell'})
        self.assertEqual(profile['provenance']['tools'], 'metadata')

    def test_estimated_context_when_only_supplied(self):
        profile = routing.ensure_profile(job())
        self.assertEqual(profile['context'], dict(tokens=4000, kind='estimated'))
        self.assertEqual(profile['provenance']['context'], 'coordinator')

    def test_unknown_role_or_missing_scope_is_profile_unavailable(self):
        with self.assertRaises(routing.ProfileUnavailable):
            routing.ensure_profile(job(role='oracle'))
        with self.assertRaises(routing.ProfileUnavailable):
            routing.ensure_profile(job(scope_ref=None))

    def test_guidance_profile_ids_stay_separate_from_task_profiles(self):
        selected = job(selection=dict(profile_ids=['g1']))
        profile = routing.ensure_profile(selected)
        self.assertNotIn('profile_ids', profile)
        self.assertEqual(profile['semantic_digest'], routing.ensure_profile(job())['semantic_digest'])

    def test_unreadable_requirement_floor_makes_the_profile_unavailable(self):
        for bad in (dict(risk_band='Critical'), 'high', dict(review_policy=3)):
            with self.assertRaises(routing.ProfileUnavailable):
                routing.ensure_profile(job(requirement_floors=bad))

    def test_reused_profile_with_a_digest_must_name_its_scope(self):
        first = routing.ensure_profile(job())
        unbound = job(profile=dict(complete_profile(), semantic_digest=first['semantic_digest']))
        profile = routing.ensure_profile(unbound)
        self.assertIn('profile_unbound', profile['reasons'])
        self.assertEqual(profile['provenance']['risk_band'], 'floor')

    def test_malformed_tools_and_uncertainty_are_dropped(self):
        profile = routing.ensure_profile(job(tools=None, profile=complete_profile(tools='edit', uncertainty='sure')))
        self.assertEqual(profile['tools'], [])
        self.assertEqual(profile['uncertainty'], 'low')
        self.assertEqual(profile['provenance']['tools'], 'floor')
        both = routing.ensure_profile(job(tools=['edit'], profile=complete_profile(tools=['shell'])))
        self.assertEqual(both['tools'], ['edit', 'shell'])
        self.assertEqual(both['provenance']['tools'], 'metadata')

    def test_input_is_not_mutated(self):
        value = job()
        before = copy.deepcopy(value)
        routing.ensure_profile(value, jev=Counter())
        self.assertEqual(value, before)


class PlanContractTests(unittest.TestCase):
    def test_validate_supplied_profile_shape(self):
        routing.validate_supplied_profile(complete_profile())
        routing.validate_supplied_profile({})
        for bad in (complete_profile(extra=1), complete_profile(complexity_band='x'),
                    complete_profile(tools='edit'), 'text'):
            with self.assertRaises(ValueError):
                routing.validate_supplied_profile(bad)


if __name__ == '__main__':
    unittest.main()
