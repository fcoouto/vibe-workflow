"""The task profile template and its loader; temporary files only, no network or model call."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from runtime import package_manifest, routing, task_profile

ROOT = Path(__file__).resolve().parents[1]


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, value, name='task-profile.json'):
        path = Path(self.temp.name) / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def test_template_pins_the_routing_constants(self):
        fields = task_profile.load()['fields']
        self.assertEqual(tuple(fields['complexity_band']['values']), routing.COMPLEXITY_BANDS)
        self.assertEqual(tuple(fields['risk_band']['values']), routing.RISK_BANDS)
        self.assertEqual(tuple(fields['review_policy']['values']), routing.REVIEW_POLICIES)
        self.assertEqual(tuple(fields['uncertainty']['values']), routing.UNCERTAINTY)
        for name in ('complexity_band', 'risk_band', 'review_policy'):
            self.assertEqual(fields[name]['floor'], routing.FLOORS[name])
        self.assertEqual(task_profile.load()['write_floors'], routing.WRITE_FLOORS)
        self.assertEqual(fields['context_tokens']['maximum'], routing.MAX_TOKENS)

    def test_roster_is_the_24_specialists(self):
        roster = task_profile.roster()
        self.assertEqual(len(roster), 24)
        self.assertEqual(list(roster)[:2], ['orchestrator', 'code-reviewer'])
        self.assertEqual(roster['code-reviewer']['label'], 'Code Reviewer')
        self.assertEqual((roster['debugger']['work_kind'], roster['debugger']['writes'], roster['debugger']['reviews']),
                         ('investigation', False, False))
        self.assertEqual(task_profile.specialist('software-engineer')['work_kind'], 'implementation')
        self.assertIsNone(task_profile.specialist('worker'))
        self.assertIsNone(task_profile.specialist('check'))
        self.assertIsNone(task_profile.specialist(None))

    def test_roster_attributes_follow_the_design(self):
        roster = task_profile.roster()
        reviewers = {'code-reviewer', 'security-reviewer', 'typescript-reviewer', 'react-reviewer',
                     'penetration-tester', 'security-auditor'}
        writers = {'react-build-resolver', 'test-engineer', 'qa-automation-engineer', 'database-architect',
                   'devops-engineer', 'backend-specialist', 'frontend-specialist', 'seo-specialist',
                   'performance-optimizer', 'software-engineer', 'documentation-writer'}
        self.assertEqual({name for name, entry in roster.items() if entry['reviews']}, reviewers)
        self.assertEqual({name for name, entry in roster.items() if entry['writes']}, writers)
        kinds = {name: entry['work_kind'] for name, entry in roster.items()}
        self.assertEqual({name for name, kind in kinds.items() if kind == 'planning'},
                         {'orchestrator', 'product-manager', 'product-owner', 'project-planner'})
        self.assertEqual({name for name, kind in kinds.items() if kind == 'testing'},
                         {'test-engineer', 'qa-automation-engineer'})
        self.assertEqual({name for name, kind in kinds.items() if kind == 'writing'}, {'documentation-writer'})

    def test_every_level_has_a_definition_and_an_example(self):
        template = task_profile.load()
        for field in ('complexity_band', 'risk_band'):
            levels = template['levels'][field]
            self.assertEqual(list(levels), template['fields'][field]['values'])
            for level, text in levels.items():
                with self.subTest(field=field, level=level):
                    self.assertEqual(set(text), {'definition', 'example'})
                    self.assertTrue(0 < len(text['definition']) <= 200 and 0 < len(text['example']) <= 200)
        self.assertEqual(template['levels']['risk_band']['critical']['example'],
                         'Credential handling, destructive operations, release steps.')

    def test_template_without_the_roster_stays_under_3_kb(self):
        template = task_profile.load()
        self.assertLess(len(json.dumps(dict(template, roster=[]), indent=2).encode()), 3 * 1024)
        self.assertLessEqual(task_profile.TEMPLATE.stat().st_size, task_profile.MAX_BYTES)

    def test_load_returns_a_copy_and_rereads_a_changed_file(self):
        good = task_profile.load()
        good['roster'].clear()
        self.assertEqual(len(task_profile.roster()), 24)
        path = self.write(task_profile.load())
        self.assertEqual(task_profile.load(path)['contract_version'], 2)
        path.write_text(json.dumps(dict(task_profile.load(), contract_version=3)))
        os.utime(path, ns=(1, 1))  # same size, so only the modification time can reveal the change
        self.assertEqual(task_profile.load(path)['contract_version'], 3)

    def test_missing_or_invalid_template_fails_closed(self):
        good = task_profile.load()
        roster = good['roster']
        cases = {
            'absent': Path(self.temp.name) / 'absent.json',
            'invalid json': self.write('{not json'),
            'not an object': self.write([]),
            'boolean version': self.write(dict(good, contract_version=True)),
            'duplicate roster id': self.write(dict(good, roster=[*roster, roster[0]])),
            'bad roster id': self.write(dict(good, roster=[dict(roster[0], id='Code Reviewer')])),
            'unknown work kind': self.write(dict(good, roster=[dict(roster[0], work_kind='research')])),
            'empty label': self.write(dict(good, roster=[dict(roster[0], label='')])),
            'long when_to_use': self.write(dict(good, roster=[dict(roster[0], when_to_use='x' * 201)])),
            'string writes': self.write(dict(good, roster=[dict(roster[0], writes='no')])),
            'writes and reviews': self.write(dict(good, roster=[dict(roster[1], writes=True)])),
            'review kind without reviews': self.write(dict(good, roster=[dict(roster[1], reviews=False)])),
            'reviews without review kind': self.write(dict(good, roster=[dict(roster[0], reviews=True)])),
            'unknown roster key': self.write(dict(good, roster=[dict(roster[0], model='m')])),
            'missing level': self.write(dict(good, levels=dict(good['levels'], risk_band={
                level: text for level, text in good['levels']['risk_band'].items() if level != 'critical'}))),
            'empty example': self.write(dict(good, levels=dict(good['levels'], complexity_band=dict(
                good['levels']['complexity_band'], low=dict(definition='d', example=''))))),
            'long definition': self.write(dict(good, levels=dict(good['levels'], complexity_band=dict(
                good['levels']['complexity_band'], low=dict(definition='d' * 201, example='e'))))),
            'table separator in text': self.write(dict(good, levels=dict(good['levels'], complexity_band=dict(
                good['levels']['complexity_band'], low=dict(definition='a | b', example='e'))))),
            'over 16 KB': self.write(json.dumps(good) + ' ' * task_profile.MAX_BYTES),
            'directory': Path(self.temp.name),
        }
        for label, path in cases.items():
            with self.subTest(label), self.assertRaises(task_profile.TemplateUnavailable):
                task_profile.load(path)

    def test_non_floor_enum_values_are_validated(self):
        good = task_profile.load()
        for label, values in {'non-string': ['low', 3], 'table separator': ['low', 'a|b'], 'empty': [],
                              'duplicate': ['low', 'low'], 'not a list': 'low'}.items():
            fields = dict(good['fields'], uncertainty={'values': values})
            with self.subTest(label), self.assertRaises(task_profile.TemplateUnavailable):
                task_profile.load(self.write(dict(good, fields=fields), name='%s.json' % label.replace(' ', '-')))

    def test_attributes_answer_for_roster_ids(self):
        self.assertEqual((task_profile.ORCHESTRATOR, task_profile.CHECK), ('orchestrator', 'check'))
        self.assertTrue(task_profile.routed('code-reviewer') and task_profile.reviews('code-reviewer'))
        self.assertFalse(task_profile.writes('code-reviewer'))
        self.assertTrue(task_profile.routed('software-engineer') and task_profile.writes('software-engineer'))
        self.assertFalse(task_profile.reviews('software-engineer'))
        self.assertEqual(task_profile.work_kind('debugger'), 'investigation')
        self.assertFalse(task_profile.routed('orchestrator'))
        self.assertTrue(task_profile.orchestrates('orchestrator'))
        self.assertFalse(task_profile.orchestrates('software-engineer'))
        self.assertEqual(task_profile.stored('code-reviewer'), task_profile.specialist('code-reviewer'))
        self.assertNotIn('legacy', task_profile.stored('code-reviewer'))

    def test_old_names_read_as_history(self):
        self.assertEqual(task_profile.LEGACY, ('worker', 'review', 'coordinator', 'implementer', 'reviewer',
                                               'researcher', 'repair'))
        self.assertIs(task_profile.stored('review')['legacy'], True)
        self.assertTrue(task_profile.reviews('review') and task_profile.reviews('reviewer'))
        self.assertTrue(task_profile.orchestrates('coordinator'))
        self.assertIsNone(task_profile.specialist('review'))
        for name in ('worker', 'implementer', 'repair'):
            with self.subTest(name):
                self.assertEqual((task_profile.work_kind(name), task_profile.writes(name),
                                  task_profile.reviews(name)), ('implementation', True, False))
                self.assertFalse(task_profile.routed(name))
        self.assertEqual((task_profile.work_kind('researcher'), task_profile.writes('researcher')),
                         ('investigation', False))
        self.assertEqual(task_profile.work_kind('coordinator'), 'planning')
        self.assertIs(task_profile.stored('coordinator')['legacy'], True)
        entry = task_profile.stored('worker')
        entry['writes'] = False  # a copy: callers cannot change the fixed entries
        self.assertTrue(task_profile.writes('worker'))

    def test_native_roles_include_stored_names(self):
        # Delegation's native worker and reviewer jobs: roster ids, and the stored old names, never the orchestrator.
        for name in ('software-engineer', 'debugger', 'worker'):
            self.assertTrue(task_profile.native(name) and task_profile.native_worker(name), name)
        for name in ('code-reviewer', 'review'):
            self.assertTrue(task_profile.native(name) and not task_profile.native_worker(name), name)
        for name in ('orchestrator', 'coordinator', 'check', 'wizard', None):
            self.assertFalse(task_profile.native(name) or task_profile.native_worker(name), name)

    def test_check_is_reserved_and_not_a_specialist(self):
        self.assertIsNone(task_profile.specialist('check'))
        self.assertFalse(task_profile.routed('check'))
        self.assertIsNone(task_profile.stored('check'))
        self.assertFalse(task_profile.orchestrates('check'))

    def test_unknown_names_have_no_attributes(self):
        for name in ('wizard', None, 3, ''):
            with self.subTest(name):
                self.assertIsNone(task_profile.stored(name))
                self.assertFalse(task_profile.routed(name) or task_profile.reviews(name)
                                 or task_profile.writes(name) or task_profile.orchestrates(name))
                self.assertIsNone(task_profile.work_kind(name))

    def test_template_ships_in_the_package(self):
        self.assertIn('templates/task-profile.json', package_manifest.export_members(ROOT))


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'templates').mkdir()
        for name in ('task-profile.json', 'worker-brief.md'):
            (self.root / 'templates' / name).write_bytes((ROOT / 'templates' / name).read_bytes())

    def test_brief_section_matches_the_template(self):
        self.assertEqual(task_profile.brief_drift(ROOT), [])
        self.assertEqual(task_profile.brief_drift(self.root), [])

    def test_rendered_section_holds_the_profile_line_levels_and_floors(self):
        block = task_profile.render_brief(task_profile.load())
        lines = block.splitlines()
        self.assertEqual((lines[0], lines[-1]), (task_profile.BRIEF_START, task_profile.BRIEF_END))
        self.assertTrue(lines[2].startswith('- Task profile: '))
        self.assertIn('| complexity_band | low | One file or one mechanical change with an obvious solution. '
                      '| Fix a typo, rename a local variable, update a constant. |', lines)
        self.assertEqual(sum(line.startswith(('| complexity_band |', '| risk_band |')) for line in lines), 8)
        self.assertIn('high', block[block.index('Floors'):])

    def test_rendered_section_holds_the_specialist_line_and_roster_table(self):
        template = task_profile.load()
        lines = task_profile.render_brief(template).splitlines()
        self.assertTrue(lines[1].startswith('- Specialist: '))
        self.assertIn('when_to_use', lines[1])
        self.assertIn('orchestrator', lines[1])  # named only to say it is never routed
        self.assertIn('| Specialist | Label | When to use | Work kind | Writes | Reviews |', lines)
        rows = [line for line in lines if line.startswith('| `')]
        self.assertEqual(len(rows), len(template['roster']) - 1)  # every routed specialist, not the orchestrator
        self.assertIn('| `code-reviewer` | Code Reviewer | Reviews any code change for bugs, error handling and test '
                      'coverage. | review | no | yes |', lines)
        self.assertFalse(any(line.startswith('| `orchestrator`') for line in lines))

    def test_validate_fails_on_brief_drift(self):
        from unittest import mock
        from scripts import validate
        drift = ['templates/worker-brief.md: the task profile section differs from templates/task-profile.json']
        with mock.patch.object(task_profile, 'brief_drift', return_value=drift):
            with self.assertRaisesRegex(ValueError, 'the task profile section differs'):
                validate.validate()

    def test_brief_drift_is_reported(self):
        brief = self.root / 'templates' / 'worker-brief.md'
        definition = 'A mistake could break one feature and is easy to revert.'
        brief.write_text(brief.read_text().replace(definition, 'A mistake could break one feature.'))
        drift = task_profile.brief_drift(self.root)
        self.assertEqual(len(drift), 1)
        self.assertIn('templates/worker-brief.md', drift[0])

    def test_missing_markers_or_template_are_reported(self):
        brief = self.root / 'templates' / 'worker-brief.md'
        text = brief.read_text()
        brief.write_text(text.replace(task_profile.BRIEF_END, ''))
        self.assertIn('templates/worker-brief.md', task_profile.brief_drift(self.root)[0])
        brief.write_text(text + text)
        self.assertIn('templates/worker-brief.md', task_profile.brief_drift(self.root)[0])
        brief.write_text(text + ' ' * task_profile.MAX_BRIEF_BYTES)
        self.assertIn('larger than', task_profile.brief_drift(self.root)[0])
        brief.write_text(text)
        (self.root / 'templates' / 'task-profile.json').write_text('{}')
        self.assertIn('templates/task-profile.json', task_profile.brief_drift(self.root)[0])

    def test_malformed_enum_values_report_a_template_problem(self):
        template = task_profile.load()
        template['fields']['uncertainty']['values'] = ['low', 1, 'a|b']
        (self.root / 'templates' / 'task-profile.json').write_text(json.dumps(template))
        drift = task_profile.brief_drift(self.root)
        self.assertEqual(len(drift), 1)
        self.assertTrue(drift[0].startswith('templates/task-profile.json: '))


if __name__ == '__main__':
    unittest.main()
