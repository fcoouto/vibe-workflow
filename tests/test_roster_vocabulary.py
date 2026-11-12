"""One roster vocabulary (F2.5): the old role names appear as values only where they mean something else.

The gate scans runtime/, scripts/, policies/, clients/, skills/ and templates/ for six old role names written as a
quoted or backticked value. `coordinator` and `check` are outside the gate: the first is also the product's word for
the user's main session and a provenance value, the second is an operation kind. Each allowance names the non-role
meaning. Role logic (a role field compared with, assigned or keyed by an old name) is refused everywhere except the
one reader of stored names, whatever the allowances say; that check also covers `coordinator`, whose role
logic is `task_profile.orchestrates`.

Python attempt-kind occurrences use bounded AST contexts rather than per-file allowances. Overlapping review
metadata/native-marker allowances apply only to their literal expression spans. Unknown syntax stays rejected.
"""
import ast
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
    ('runtime/delegation.py', 'worker', 'the Codex native task-name marker passed to native_task_name',
     ('native-marker',)),
    ('runtime/delegation.py', 'review', 'the assignment body metadata key and Codex native task-name marker',
     ('review-metadata', 'native-marker')),
    ('runtime/evidence.py', 'review', 'the assignment body metadata key', ('review-metadata',)),
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
        for number, line in enumerate(text.split('\n'), 1):
            for match in VALUE.finditer(line):
                yield path, number, match.group(2), line


def field(node, name):
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == name
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
            and 1 <= len(node.args) <= 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == name)


def role(node):
    return (isinstance(node, ast.Name) and node.id == 'role'
            or isinstance(node, ast.Attribute) and node.attr == 'role' or field(node, 'role'))


def role_context(node, parents):
    """Conservative direct role sinks override even a locally valid non-role expression."""
    child = node
    while child in parents:
        parent = parents[child]
        if isinstance(parent, ast.keyword) and parent.arg == 'role':
            return True
        if isinstance(parent, ast.Call) and field(parent, 'role') and len(parent.args) == 2 and parent.args[1] is child:
            return True
        if isinstance(parent, ast.Dict) and any(value is child and isinstance(key, ast.Constant) and key.value == 'role'
                                               for key, value in zip(parent.keys, parent.values)):
            return True
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is child:
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if any(role(target) for target in targets):
                return True
        if isinstance(parent, ast.Compare) and any(role(part) for part in [parent.left, *parent.comparators]):
            return True
        if isinstance(parent, ast.arguments):
            positional = [*parent.posonlyargs, *parent.args]
            defaults = list(zip(positional[-len(parent.defaults):], parent.defaults)) if parent.defaults else []
            if any(argument.arg == 'role' and default is child
                   for argument, default in [*defaults, *zip(parent.kwonlyargs, parent.kw_defaults)]):
                return True
        child = parent
    return False


def kind_context(node, parents):
    parent = parents.get(node)
    if isinstance(parent, ast.keyword):
        return parent.arg == 'kind'
    if isinstance(parent, ast.Dict):
        return any(value is node and isinstance(key, ast.Constant) and key.value == 'kind'
                   for key, value in zip(parent.keys, parent.values))
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        return all(field(target, 'kind') for target in targets)
    comparison = parents.get(parent) if isinstance(parent, (ast.Tuple, ast.List, ast.Set)) else parent
    if not isinstance(comparison, ast.Compare) or len(comparison.ops) != 1:
        return False
    left, right, operation = comparison.left, comparison.comparators[0], comparison.ops[0]
    if isinstance(operation, (ast.Eq, ast.NotEq)):
        return left is node and field(right, 'kind') or right is node and field(left, 'kind')
    return (isinstance(operation, (ast.In, ast.NotIn)) and field(left, 'kind')
            and isinstance(right, (ast.Tuple, ast.List, ast.Set)) and node in right.elts)


def body(node):
    return isinstance(node, ast.Name) and node.id == 'body' or field(node, 'body')


def review_metadata(node, parents):
    if node.value != 'review':
        return False
    parent = parents.get(node)
    if isinstance(parent, ast.Subscript) and parent.slice is node:
        return body(parent.value)
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
        return isinstance(parent.func, ast.Attribute) and parent.func.attr == 'get' and body(parent.func.value)
    if isinstance(parent, ast.Compare) and len(parent.ops) == 1:
        return parent.left is node and isinstance(parent.ops[0], (ast.In, ast.NotIn)) and body(parent.comparators[0])
    if isinstance(parent, (ast.Tuple, ast.List)):
        call = parents.get(parent)
        return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == '_fields'
                and call.args and body(call.args[0]) and parent in call.args[1:])
    return False


def native_marker(node, parents):
    if node.value not in ('review', 'worker'):
        return False
    expression = node
    while isinstance(parents.get(expression), ast.IfExp) and expression is not parents[expression].test:
        expression = parents[expression]
    call = parents.get(expression)
    return (isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))
            and (call.func.id if isinstance(call.func, ast.Name) else call.func.attr) == 'native_task_name'
            and len(call.args) >= 2 and call.args[1] is expression)


def python_occurrences(text):
    """Classify exact literal spans, not lines. This is syntax recognition, not dataflow analysis."""
    tree = ast.parse(text)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or node.value not in (*NAMES, 'coordinator'):
            continue
        contexts = {name for name, predicate in (('kind', kind_context), ('review-metadata', review_metadata),
                                                  ('native-marker', native_marker)) if predicate(node, parents)}
        result.append((node, contexts, role_context(node, parents)))
    return result


def violations(root, allowed=ALLOWED):
    permitted = {(entry[0], entry[1]): entry[3] if len(entry) == 4 else None for entry in allowed}
    found = []
    for path, text in files(root):
        occurrences = []
        if path.endswith('.py'):
            try:
                occurrences = python_occurrences(text)
            except (SyntaxError, ValueError):
                found.append('%s:1: invalid Python syntax' % path)
                continue
        # read_text normalizes CR/CRLF; other Unicode separators do not advance AST line numbers.
        for number, line in enumerate(text.split('\n'), 1):
            for match in VALUE.finditer(line):
                start, end = (number, len(line[:match.start()].encode('utf-8'))), (number, len(line[:match.end()].encode('utf-8')))
                literals = [(contexts, blocked) for node, contexts, blocked in occurrences
                            if (node.lineno, node.col_offset) <= start and end <= (node.end_lineno, node.end_col_offset)]
                contexts = set().union(*(item[0] for item in literals))
                blocked = path != READER and any(item[1] for item in literals)
                key = (path, match.group(2))
                allowance = key in permitted and (permitted[key] is None or contexts.intersection(permitted[key]))
                if not blocked and ('kind' in contexts or allowance):
                    continue
                found.append('%s:%d: %s as a value: %s' % (path, number, match.group(2), line.strip()))
        if path != READER:
            found += ['%s:%d: role logic on an old name: %s' % (path, number, line.strip())
                      for number, line in enumerate(text.split('\n'), 1) if ROLE_LOGIC.search(line)]
            found += ['%s:%d: role logic on an old name: %s' % (path, node.lineno, node.value)
                      for node, _, blocked in occurrences if blocked]
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
        for entry in ALLOWED:
            path, name, reason = entry[:3]
            with self.subTest(path=path, name=name):
                self.assertIn((path, name), used, 'remove an allowance nothing needs')
                self.assertIn(name, NAMES)
                self.assertGreater(len(reason.split()), 2)
        self.assertEqual(len({entry[:2] for entry in ALLOWED}), len(ALLOWED))

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

    def test_python_line_boundaries_do_not_follow_unicode_text_separators(self):
        for separator in ('\v', '\f', '\x85', '\u2028', '\u2029'):
            with self.subTest(separator=repr(separator)):
                prefix = '# comment' + separator + '\n'
                self.assertEqual(self.python_violations(prefix + 'x = dict(kind="review")\n'), [])
                source = prefix + 'add(thread, "review")\nx=dict(kind="review"\n                    "")\n'
                found = self.python_violations(source)
                self.assertTrue(any(item.startswith('runtime/fresh.py:2: review as a value:') for item in found), found)

    def test_role_lookup_defaults_override_nested_non_role_contexts(self):
        for source in (
            'add(thread, data.get("role", dict(kind="review")["kind"]))',
            'add(thread, data.get("role", {"kind": "review"}["kind"]))',
            'add(thread, data.get("role", dict(kind="review") if missing else known))',
            'add(thread, data.get("role", native_task_name(identity, "review")))',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.python_violations(source, 'runtime/delegation.py', ALLOWED), source)
        for source in (
            'add(thread, data.get("kind", dict(kind="review")["kind"]))',
            'add(thread, data.get("kind", {"kind": "review"}["kind"]))',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.python_violations(source), [])

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
