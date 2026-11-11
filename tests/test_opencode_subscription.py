"""F4.5: pure normalization of OpenCode's `GET /api/experimental/usage/limits` response.

The envelope and meter shapes are the installed OpenCode 2.2.6 schema (`UsageLimits.*`, identical to the public
OpenAPI document; capability matrix, OpenCode Go binding assessment). Weekly/monthly rolling semantics, the monthly
length, the original observation time and model demand are unknown, so no response yields an admission window.
Fixtures are invented; nothing here reads a client, account, network, file or clock.
"""
import copy
import json
import math
import unittest
from unittest import mock

from runtime import opencode_subscription as subscription

NOW = 1_800_000_000  # seconds; every reset below lies after it
WORKSPACE = 'wrk_invented0123456789'


def meter(percent=3, limited=False, resets_at=(NOW + 3600) * 1000):
    return dict(percent=percent, limited=limited, resetsAt=resets_at)


def payload(**data):
    info = dict(workspace=dict(id=WORKSPACE, name='Invented', usageURL='https://example.invalid/usage'),
                go=dict(rolling=meter(0), weekly=meter(3), monthly=meter(1)), budgets=[])
    info.update(data)
    return dict(location=dict(directory='/invented'), data=info)


def normalize(value, observed_at=NOW):
    return subscription.normalize_limits(value, observed_at=observed_at)


class EnvelopeTests(unittest.TestCase):
    def test_signature_is_exact(self):
        import inspect
        parameters = inspect.signature(subscription.normalize_limits).parameters
        self.assertEqual([(name, item.kind.name) for name, item in parameters.items()],
                         [('payload', 'POSITIONAL_OR_KEYWORD'), ('observed_at', 'KEYWORD_ONLY')])

    def test_signed_out_location_is_unavailable(self):
        self.assertEqual(normalize(dict(location={}, data=None)), dict(state='unavailable', reason='not_signed_in'))

    def test_a_malformed_envelope_is_unavailable(self):
        for value in (None, [], 'x', {}, dict(data=payload()['data']), dict(location={}),
                      dict(payload(), extra=1), dict(location={}, data=[]),
                      dict(location={}, data=dict(go=payload()['data']['go'], budgets=[]))):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), dict(state='unavailable', reason='usage_malformed'))

    def test_no_go_allowance_is_unavailable(self):
        value = payload()
        del value['data']['go']
        self.assertEqual(normalize(value), dict(state='unavailable', reason='go_unavailable'))
        self.assertEqual(normalize(payload(go=None)), dict(state='unavailable', reason='go_unavailable'))

    def test_a_malformed_workspace_is_unavailable(self):
        good = payload()['data']['workspace']
        for workspace in (None, 'wrk', {}, dict(good, id=''), dict(good, id=7), dict(good, id=None),
                          {key: value for key, value in good.items() if key != 'usageURL'}, dict(good, usageURL=3),
                          dict(good, name=4), dict(good, extra='x'), dict(good, id='x' * 300), dict(good, id='a\nb')):
            with self.subTest(workspace=workspace):
                self.assertEqual(normalize(payload(workspace=workspace)),
                                 dict(state='unavailable', reason='workspace_invalid'))

    def test_budgets_must_be_a_list_and_never_become_capacity(self):
        self.assertEqual(normalize(payload(budgets=None)), dict(state='unavailable', reason='usage_malformed'))
        cash = [dict(id='b1', limit=0, spent=0, scope='combined', per='set', coverage='all', models=[], vendors=[],
                     picked=True, shared=False, resetsAt=(NOW + 60) * 1000)]
        value = payload(budgets=cash)
        del value['data']['go']
        self.assertEqual(normalize(value), dict(state='unavailable', reason='go_unavailable'))  # cash is not quota
        self.assertEqual(normalize(payload(budgets=cash)), normalize(payload()))  # budgets change nothing
        self.assertNotIn('budget', json.dumps(normalize(payload(budgets=cash))))


class MeterTests(unittest.TestCase):
    def test_every_meter_is_required(self):
        for name in ('rolling', 'weekly', 'monthly'):
            with self.subTest(meter=name):
                go = payload()['data']['go']
                del go[name]
                self.assertEqual(normalize(payload(go=go)), dict(state='unavailable', reason='meter_missing'))
        self.assertEqual(normalize(payload(go=dict(payload()['data']['go'], hourly=meter()))),
                         dict(state='unavailable', reason='meter_invalid'))
        self.assertEqual(normalize(payload(go=[])), dict(state='unavailable', reason='meter_invalid'))

    def test_meter_values_are_strictly_bounded(self):
        bad = [dict(percent=-0.1), dict(percent=100.1), dict(percent=math.nan), dict(percent=math.inf),
               dict(percent=True), dict(percent='3'), dict(percent=None), dict(limited=1), dict(limited='false'),
               dict(limited=None), dict(resetsAt=None), dict(resetsAt=True), dict(resetsAt='1'), dict(resetsAt=-1),
               dict(resetsAt=0), dict(resetsAt=math.inf), dict(resetsAt=math.nan)]
        for change in bad:
            with self.subTest(change=change):
                go = payload()['data']['go']
                go['weekly'] = dict(go['weekly'], **change)
                self.assertEqual(normalize(payload(go=go)), dict(state='unavailable', reason='meter_invalid'))
        for broken in ({}, dict(percent=1, limited=False), dict(meter(), extra=1), 'meter', None):
            with self.subTest(meter=broken):
                go = dict(payload()['data']['go'], monthly=broken)
                self.assertEqual(normalize(payload(go=go)), dict(state='unavailable', reason='meter_invalid'))

    def test_usage_rounds_up_to_thousandths_and_limited_is_exhausted(self):
        for percent, limited, used in ((0, False, 0), (0.1, False, 1), (0.11, False, 2), (3, False, 30),
                                       (3.01, False, 31), (99.99, False, 1000), (100, False, 1000),
                                       (20, True, 1000), (0, True, 1000)):
            with self.subTest(percent=percent, limited=limited):
                go = dict(payload()['data']['go'], weekly=meter(percent, limited))
                found = normalize(payload(go=go))['meters']['weekly']
                self.assertEqual((found['used'], found['limit'], found['limited']), (used, 1000, limited))

    def test_usage_just_above_a_thousandth_rounds_up(self):
        """Checkpoint 10b: small positive usage is never removed before the ceiling."""
        value = dict(location={}, data=dict(workspace=dict(id='w', usageURL='u'), budgets=[], go=dict(
            rolling=dict(percent=0.10000000001, limited=False, resetsAt=5000000),
            weekly=dict(percent=0, limited=False, resetsAt=5000000),
            monthly=dict(percent=0, limited=False, resetsAt=5000000))))
        self.assertEqual(subscription.normalize_limits(value, observed_at=1000)['meters']['rolling']['used'], 2)
        for percent, used in ((2.0000000001, 21), (0.1000000000001, 2), (99.90000000001, 1000), (0.1, 1), (2, 20)):
            with self.subTest(percent=percent):
                go = dict(payload()['data']['go'], weekly=meter(percent))
                self.assertEqual(normalize(payload(go=go))['meters']['weekly']['used'], used)

    def test_oversized_numbers_are_unavailable_not_errors(self):
        for huge in (10**400, -10**400):
            for field in ('percent', 'resetsAt'):
                with self.subTest(field=field, value=huge):
                    go = payload()['data']['go']
                    go['monthly'] = dict(go['monthly'], **{field: huge})
                    self.assertEqual(normalize(payload(go=go)), dict(state='unavailable', reason='meter_invalid'))
            with self.subTest(field='observed_at', value=huge):
                self.assertEqual(normalize(payload(), huge), dict(state='unavailable', reason='observed_at_invalid'))

    def test_reset_milliseconds_convert_without_moving_later(self):
        for milliseconds, seconds in (((NOW + 3600) * 1000, NOW + 3600), ((NOW + 3600) * 1000 + 999, NOW + 3600.999),
                                      (float((NOW + 7) * 1000 + 1), NOW + 7.001)):
            with self.subTest(milliseconds=milliseconds):
                go = dict(payload()['data']['go'], monthly=meter(resets_at=milliseconds))
                reset = normalize(payload(go=go))['meters']['monthly']['reset_at']
                self.assertLessEqual(reset * 1000, milliseconds)  # never a later reset than reported
                self.assertAlmostEqual(reset, seconds, places=6)

    def test_a_reset_already_passed_is_unavailable(self):
        for milliseconds in (NOW * 1000, (NOW - 1) * 1000):
            with self.subTest(milliseconds=milliseconds):
                go = dict(payload()['data']['go'], rolling=meter(resets_at=milliseconds))
                self.assertEqual(normalize(payload(go=go)), dict(state='unavailable', reason='reset_elapsed'))


class AdmissionBoundaryTests(unittest.TestCase):
    def test_a_valid_response_is_parsed_but_not_an_admission_observation(self):
        found = normalize(payload())
        self.assertEqual(found['state'], 'unavailable')
        self.assertEqual(found['reason'], 'window_semantics_unknown')
        self.assertEqual(set(found['meters']), {'rolling', 'weekly', 'monthly'})
        self.assertEqual(found['meters']['weekly'], dict(used=30, limit=1000, limited=False, reset_at=NOW + 3600))
        self.assertEqual(set(found['unknown']), {'rolling_or_fixed', 'window_length', 'observation_time', 'demand'})
        for field in ('windows', 'observed_at', 'length', 'rolling', 'demand', 'id'):  # nothing an admission reads
            self.assertNotIn(field, found)
            for item in found['meters'].values():
                self.assertNotIn(field, item)

    def test_the_callers_time_never_dates_the_reading(self):
        first, later = normalize(payload(), NOW), normalize(payload(), NOW + 600)
        self.assertEqual(first, later)  # processing time is not an observation time
        self.assertNotIn(str(NOW), json.dumps(first))

    def test_observed_at_must_be_a_finite_time(self):
        for value in (None, True, '1', -1, 0, math.nan, math.inf):
            with self.subTest(observed_at=value):
                self.assertEqual(normalize(payload(), value), dict(state='unavailable', reason='observed_at_invalid'))

    def test_the_account_label_is_a_stable_digest_without_raw_identifiers(self):
        found = normalize(payload())
        self.assertRegex(found['account'], r'^opencode-go:[0-9a-f]{16}$')
        self.assertEqual(found['account'], normalize(payload(workspace=dict(
            id=WORKSPACE, usageURL='https://example.invalid/other')))['account'])  # name and URL do not matter
        self.assertNotEqual(found['account'], normalize(payload(workspace=dict(
            id=WORKSPACE + 'x', name='Invented', usageURL='https://example.invalid/usage')))['account'])
        text = json.dumps(found)
        for secret in (WORKSPACE, 'Invented', 'example.invalid', '/invented', 'directory'):
            self.assertNotIn(secret, text)

    def test_the_input_is_not_changed_and_nothing_is_read(self):
        value = payload()
        before = copy.deepcopy(value)
        with mock.patch('builtins.open', side_effect=AssertionError('file read')), \
                mock.patch('socket.socket', side_effect=AssertionError('network')), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('process')), \
                mock.patch('time.time', side_effect=AssertionError('clock read')):
            normalize(value)
        self.assertEqual(value, before)


if __name__ == '__main__':
    unittest.main()
