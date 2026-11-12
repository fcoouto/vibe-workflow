"""The model-name check: names come from the compiled table in user state; invented names only."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from runtime import model_names, routing_evidence


def strategy(model, provider='vendor-q', price=None):
    return dict(strategy_id=model, model=model, effort=None, harness='h', provider=provider, evidence_refs=['g'],
                list_price_micros=price)


class ModelNameCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / 'package'
        for name in ('runtime', 'scripts', 'policies', 'clients', 'skills/s', 'templates', 'docs', 'tests', 'assets'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'AGENTS.md').write_text('# Rules\n')
        self.settings = self.tmp / 'state' / 'settings.json'
        self.settings.parent.mkdir(mode=0o700)
        # An identity, its display name and an ordinary-word identity; `quick` is only an invocation alias the
        # harness accepts, so it is not a table model and is never searched for.
        self.table(rows={'a|low|low': [strategy('vendor-q/model-q'), strategy('light')]},
                   display_names={'vendor-q/model-q': 'Model Q Prime'})

    def table(self, **value):
        path = self.settings.parent / 'strategy-table.json'
        path.write_text(json.dumps(dict(dict(schema=2, version='t', rows={}, display_names={}), **value)))
        path.chmod(0o600)

    def check(self):
        return model_names.check(self.root, self.settings)

    def test_a_table_model_in_a_covered_file_fails_with_file_and_line(self):
        self.assertEqual(self.check(), 'passed')
        cases = [('runtime/x.py', 'x = 1\nuse = "vendor-q/model-q"\n', 2, 'vendor-q/model-q'),
                 ('policies/p.md', 'Pick the MODEL Q PRIME worker.\n', 1, 'Model Q Prime'),
                 ('AGENTS.md', '# Rules\n\nPrefer light.\n', 3, 'light'),  # an ordinary word is still a real name
                 ('skills/s/SKILL.md', 'one\n(vendor-q/model-q)\n', 2, 'vendor-q/model-q'),
                 ('package.json', '{"model": "light"}\n', 1, 'light'),
                 ('templates/t.json', '{"m": "Light"}\n', 1, 'light')]
        for relative, text, line, name in cases:
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_text() if path.exists() else None
                path.write_text(text)
                with self.assertRaises(ValueError) as caught:
                    self.check()
                self.assertIn('%s:%d' % (relative, line), str(caught.exception))
                self.assertIn(name, str(caught.exception))
                path.write_text(original) if original is not None else path.unlink()
        self.assertEqual(self.check(), 'passed')

    def test_the_same_name_under_docs_tests_or_assets_passes(self):
        for relative in ('docs/d.md', 'tests/t.py', 'assets/a.json', 'docs/nested/n.json', '.hidden/h.md',
                         'runtime/__pycache__/c.txt'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('vendor-q/model-q light Model Q Prime\n')
        (self.root / 'clients' / 'c.md').write_text('An invocation alias such as quick is not a model identity.\n')
        self.assertEqual(self.check(), 'passed')

    def test_a_name_inside_a_longer_word_does_not_match(self):
        (self.root / 'runtime' / 'x.py').write_text(
            'lightweight = "highlight"\nslight = "vendor-q/model-qx"\nother = "vendor-q/model-q-large"\n'
            'path = "my-light" "light_level" "light.py"\n')
        self.assertEqual(self.check(), 'passed')
        (self.root / 'runtime' / 'x.py').write_text('end of a sentence: light.\n')
        with self.assertRaisesRegex(ValueError, r'runtime/x\.py:1'):
            self.check()

    def test_without_a_table_the_check_is_not_run(self):
        (self.root / 'runtime' / 'x.py').write_text('light = "vendor-q/model-q"\n')
        (self.settings.parent / 'strategy-table.json').unlink()
        self.assertEqual(self.check(), 'not_checked')
        self.table()  # a table with no names cannot pass a check it never ran
        self.assertEqual(self.check(), 'not_checked')
        self.assertEqual(model_names.check(self.root, self.tmp / 'absent' / 'settings.json'), 'not_checked')

    def test_compiled_display_names_are_searched(self):
        catalog = [dict(model='vendor-z', harnesses=['h'], supported_efforts=[], display_name='Zeta Prime')]
        guidance = [dict(id='g', sha256='1' * 64, checked_at='2026-11-08T00:00:00Z', rows=[dict(
            work_kind='implementation', complexity_band='low', risk_band='low',
            strategies=[dict(model='vendor-z', effort=None, harness='h', evidence_refs=['g'])])])]
        compiled = routing_evidence.compile_table(guidance, catalog, [], now='2026-11-08T00:00:00Z')
        self.table(rows=compiled['rows'], display_names=compiled['display_names'])
        (self.root / 'clients' / 'c.md').write_text('Shown as Zeta Prime.\n')
        with self.assertRaisesRegex(ValueError, r'clients/c\.md:1.*Zeta Prime'):
            self.check()


class PackageMembershipTests(ModelNameCheckTests):
    def git(self, *args):
        import subprocess
        subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True,
                       env=dict(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@invalid', GIT_COMMITTER_NAME='t',
                                GIT_COMMITTER_EMAIL='t@invalid', GIT_CONFIG_GLOBAL='/dev/null', HOME=str(self.tmp),
                                PATH='/usr/bin:/bin'))

    def test_only_package_members_are_read(self):
        (self.root / 'runtime' / 'kept.py').write_text('x = 1\n')
        (self.root / '.gitignore').write_text('scratch/\n')
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-qm', 'base')
        for relative in ('scratch/cache.json', 'local.json', 'runtime/untracked.py', '.vibe-workflow/x.json'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"m": "light"}\n')  # private, ignored or untracked: never read
        self.assertEqual(self.check(), 'passed')
        (self.root / 'runtime' / 'kept.py').write_text('x = "light"\n')  # a tracked member is read from disk
        with self.assertRaisesRegex(ValueError, r'runtime/kept\.py:1'):
            self.check()
        (self.root / 'runtime' / 'kept.py').write_text('x = 1\n')
        self.git('add', 'local.json')
        with self.assertRaisesRegex(ValueError, r'local\.json:1'):
            self.check()

    def test_unusable_membership_fails_closed(self):
        (self.root / '.git').write_text('gitdir: /nonexistent\n')
        with self.assertRaisesRegex(ValueError, 'membership'):
            self.check()


class ValidateReportTests(unittest.TestCase):
    def test_validate_reports_the_model_name_check(self):
        from scripts import validate
        report = validate.validate()
        self.assertEqual(report['model_names'], 'not_checked')  # tests run under a private home with no table


if __name__ == '__main__':
    unittest.main()
