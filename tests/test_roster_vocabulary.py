"""One roster vocabulary (F2.5): the old role names appear as values only where they mean something else.

The gate scans runtime/, scripts/, policies/, clients/, skills/ and templates/ for six old role names written as a
quoted or backticked value. `coordinator` and `check` are outside the gate: the first is also the product's word for
the user's main session and a provenance value, the second is an operation kind. Each allowance names the non-role
meaning. Role logic (a role field compared with, assigned or keyed by an old name) is refused everywhere except the
one reader of stored names, whatever the allowances say; that check also covers `coordinator`, whose role
logic is `task_profile.orchestrates`.
"""
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('runtime', 'scripts', 'policies', 'clients', 'skills', 'templates')
SUFFIXES = ('.py', '.js', '.mjs', '.ts', '.json', '.md')
NAMES = ('worker', 'review', 'implementer', 'reviewer', 'researcher', 'repair')
_NAME = '(' + '|'.join(NAMES) + ')'
_ROLE_NAME = '(' + '|'.join((*NAMES, 'coordinator')) + ')'
VALUE = re.compile(r"""(['"`])%s\1""" % _NAME)
ROLE_LOGIC = re.compile(
    r"""role['"]?\]?\s*(?:==|!=)\s*['"]%s['"]"""                         # role == 'worker'
    r"""|\brole\s*=\s*['"]%s['"]"""                                        # role='worker'
    r"""|['"]role['"]\s*:\s*['"]%s['"]"""                                  # "role": "worker"
    r"""|role['"]?\]?\s+(?:not\s+)?in\s+[(\[{][^)\]}]*['"]%s['"]""" % ((_ROLE_NAME,) * 4))
READER = 'runtime/task_profile.py'
_STAGES = 'accounting stages and the role labels of stored admission buckets (ROLE_STAGES)'
_ATTESTATION = 'the external attestation field naming the independently observed reviewer identity'
ALLOWED = (
    *((READER, name, 'the one reader of role names in stored records') for name in NAMES),
    *(('runtime/usage_budget.py', name, _STAGES) for name in ('worker', 'review', 'repair')),
    ('runtime/adapters/codex.py', 'worker', 'the native task-name prefix and Workstreams marker'),
    ('runtime/adapters/codex.py', 'review', 'the native task-name prefix and Workstreams marker'),
    ('runtime/delegation.py', 'worker', 'the Codex native task-name marker passed to native_task_name'),
    ('runtime/delegation.py', 'review', 'the D5 attempt kind, the assignment body `review` metadata key and the '
                                        'Codex native task-name marker'),
    ('runtime/evidence.py', 'review', 'the D5 attempt kind and the assignment body `review` metadata key'),
    ('runtime/setup_readiness.py', 'review', 'the D5 attempt kind'),
    ('runtime/external_delegation.py', 'worker', 'the external route mode (proposal or worker)'),
    ('runtime/external_delegation.py', 'reviewer', _ATTESTATION),
    ('runtime/openrouter_worker_managed.py', 'worker', 'the external route mode'),
    ('scripts/openrouter_workers.py', 'worker', 'the external route mode'),
    ('scripts/openrouter_workers.py', 'reviewer', _ATTESTATION),
    ('scripts/openrouter_proposals.py', 'reviewer', _ATTESTATION),
    ('scripts/usage_codex_host.js', 'reviewer', 'the host frame field carrying the observed reviewer identity'),
    ('scripts/workflow.py', 'review', 'the `evidence review` command action'),
    ('clients/external-delegation.md', 'worker', 'the external route mode'),
    ('clients/external-delegation.md', 'reviewer', _ATTESTATION),
    ('skills/manage-usage-routing/references/codex-host.md', 'reviewer', _ATTESTATION),
    ('skills/openrouter-proposals/references/procedure.md', 'reviewer', _ATTESTATION),
    ('skills/openrouter-workers/references/procedure.md', 'reviewer', _ATTESTATION),
    ('templates/task-profile.json', 'review', 'the `review` work kind'),
)


def files(root):
    for directory in DIRECTORIES:
        for path in sorted((root / directory).rglob('*')):
            if path.suffix in SUFFIXES and path.is_file() and not {'__pycache__', 'node_modules'} & set(path.parts):
                yield path.relative_to(root).as_posix(), path.read_text(encoding='utf-8', errors='replace')


def uses(root):
    """(path, line number, name, line) for every old role name written as a quoted or backticked value."""
    for path, text in files(root):
        for number, line in enumerate(text.splitlines(), 1):
            for match in VALUE.finditer(line):
                yield path, number, match.group(2), line


def violations(root, allowed=ALLOWED):
    permitted = {(path, name) for path, name, _ in allowed}
    found = ['%s:%d: %s as a value: %s' % (path, number, name, line.strip())
             for path, number, name, line in uses(root) if (path, name) not in permitted]
    for path, text in files(root):
        if path != READER:
            found += ['%s:%d: role logic on an old name: %s' % (path, number, line.strip())
                      for number, line in enumerate(text.splitlines(), 1) if ROLE_LOGIC.search(line)]
    return found


class RosterVocabularyTests(unittest.TestCase):
    def python_violations(self, source, path='runtime/fresh.py', allowed=()):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / path
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding='utf-8')
            return violations(root, allowed)

    def test_no_old_role_name_outside_the_allowlist(self):
        self.assertEqual(violations(ROOT), [])

    def test_every_allowance_is_used_and_names_its_meaning(self):
        used = {(path, name) for path, _, name, _ in uses(ROOT)}
        for path, name, reason in ALLOWED:
            with self.subTest(path=path, name=name):
                self.assertIn((path, name), used, 'remove an allowance nothing needs')
                self.assertIn(name, NAMES)
                self.assertGreater(len(reason.split()), 2)
        self.assertEqual(len({(path, name) for path, name, _ in ALLOWED}), len(ALLOWED))

    def test_gate_rejects_role_logic_and_permits_named_non_role_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'runtime').mkdir()
            (root / 'clients').mkdir()
            (root / 'templates').mkdir()
            (root / 'runtime' / 'kinds.py').write_text("if attempt['kind'] == 'review':\n    pass\n")
            (root / 'runtime' / 'roles.py').write_text(
                "if record['data']['role'] == 'worker':\n    pass\njob = dict(role='review')\n"
                "if role not in {'worker', 'review'}:\n    pass\nif data['role'] == 'coordinator':\n    pass\n"
                "if attempt['kind'] == 'check' and operation == 'coordinator-work':\n    pass\n")
            (root / 'templates' / 'plan.json').write_text('{"jobs": [{"role": "implementer"}]}\n')
            (root / 'clients' / 'guide.md').write_text('Use a `reviewer` job.\nA reviewer reads the diff.\n')
            allowed = (('runtime/roles.py', 'worker', 'pretend non-role use'),
                       ('runtime/roles.py', 'review', 'pretend non-role use'))
            found = violations(root, allowed)
            self.assertFalse(any(item.startswith('runtime/kinds.py') for item in found), found)
            for expected in ('runtime/roles.py:1: role logic', 'runtime/roles.py:3: role logic',
                             'runtime/roles.py:4: role logic', 'runtime/roles.py:6: role logic',
                             'templates/plan.json:1: implementer as a value',
                             'templates/plan.json:1: role logic', 'clients/guide.md:1: reviewer as a value'):
                self.assertTrue(any(item.startswith(expected) for item in found), (expected, found))
            self.assertFalse(any(item.startswith('clients/guide.md:2') for item in found))  # plain prose
            self.assertFalse(any(item.startswith('runtime/roles.py:8') for item in found))  # kinds, not roles
            self.assertFalse(any(item.startswith('runtime/kinds.py') for item in violations(root, ())))

    def test_attempt_kind_syntax_needs_no_file_allowance(self):
        cases = (
            'attempt = dict(kind="review")',
            'attempt = {"kind": "review"}',
            'attempt["kind"] = "review"',
            'attempt["kind"]: str = "review"',
            'value = attempt["kind"] == "review"',
            'value = attempt.get("kind") != "review"',
            'value = "review" == attempt["kind"]',
            'value = "review" != attempt.get("kind")',
            'value = attempt.get("kind") in {"check", "review"}',
            'value = attempt["kind"] not in ("review", "check")',
            'attempt = dict(\n    kind="review",\n)',
            'label = "ação"; attempt = dict(kind="review")',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(self.python_violations(source), [])

    def test_role_values_and_unclassified_literals_remain_rejected(self):
        cases = (
            'job = dict(role="reviewer")',
            'job = {"role": "reviewer"}',
            'role = "reviewer"',
            'role: str = "reviewer"',
            'job["role"] = "reviewer"',
            'value = role == "reviewer"',
            'value = role in {"reviewer"}',
            'def job(role="reviewer"):\n    pass',
            'add(thread, stored_role if linked else "reviewer")',
            'job = dict(kind="review", role="reviewer")',
            'attempt = dict(kind="review"); add(thread, "review")',
            'value = attempt.get("kind") == "review" and role == "reviewer"',
            'job = dict(kind="review", role="review")',
            'job = {"kind": "review", "role": "review"}',
            'value = other("review")',
            'value = "review"',
            'role = dict(kind="review")["kind"]',
            'job = dict(role={"kind": "review"}["kind"])',
            'role = attempt["kind"] = "review"',
            'value = ((attempt["kind"] == "review") == "reviewer")',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(self.python_violations(source), source)

    def test_overlapping_allowances_cover_only_metadata_or_native_markers(self):
        accepted = (
            ('runtime/delegation.py', "review = record['body']['review']"),
            ('runtime/delegation.py', "body['review'] = {}"),
            ('runtime/delegation.py', "value = 'review' not in body"),
            ('runtime/delegation.py', "state._fields(body, ('version',), ('review',))"),
            ('runtime/delegation.py',
             "native_task_name(identity, 'review' if reviewing else 'worker', context)"),
            ('runtime/evidence.py', "child(body.get('review', {}).get('plan'))"),
        )
        for path, source in accepted:
            with self.subTest(path=path, source=source):
                self.assertEqual(self.python_violations(source, path, ALLOWED), [])
        for path in ('runtime/delegation.py', 'runtime/evidence.py'):
            for source in (
                'add(thread, "review")',
                'add(thread, known if linked else "worker")',
                'attempt = dict(kind="review"); add(thread, "review")',
                'review = body["review"]; add(thread, "review")',
                'native_task_name(identity, "worker"); add(thread, "worker")',
                'role = body["review"]',
                'job = dict(role=native_task_name(identity, "review"))',
            ):
                with self.subTest(path=path, source=source):
                    self.assertTrue(self.python_violations(source, path, ALLOWED), source)

    def test_occurrence_spans_do_not_exempt_neighbors_or_chained_role_comparisons(self):
        cases = (
            'label="ação"; a=dict(kind="review"); add(thread,"review")',
            'a = dict(\n    kind="review",\n    role="reviewer",\n)',
            'value = attempt["kind"] == "review" == role',
            'role = attempt["kind"] = "review"',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(self.python_violations(source), source)

    def test_parse_failures_report_a_violation_even_with_an_allowance(self):
        found = self.python_violations('attempt = dict(kind="review"', 'runtime/evidence.py', ALLOWED)
        self.assertTrue(any('invalid Python syntax' in item for item in found), found)

    def test_non_python_files_keep_their_lexical_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, source in (
                ('templates/job.json', '{"role": "reviewer"}'),
                ('clients/job.md', 'Use a `reviewer` job.'),
                ('scripts/job.js', 'add(thread, "reviewer");'),
            ):
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_text(source)
            found = violations(root, ())
            for path in ('templates/job.json', 'clients/job.md', 'scripts/job.js'):
                self.assertTrue(any(item.startswith(path + ':') for item in found), found)


if __name__ == '__main__':
    unittest.main()
