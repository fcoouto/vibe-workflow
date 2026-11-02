"""Disposable accepted scopes for managed OpenRouter tests; no live account state."""
from pathlib import Path
from unittest.mock import patch

from runtime import changes, startup

PACKAGE = Path(__file__).resolve().parents[1]


def enroll(testcase):
    """Genuine D4 birth with an explicit software-only owner observation."""
    from tests.fixture_canonical_birth import fresh_canonical
    fixture, acquisition, observation = fresh_canonical(testcase)
    scope = startup._entry_scope(acquisition)
    if not hasattr(testcase, '_optional_observations'):
        testcase._optional_observations = {}
        testcase._optional_fixtures = []
        actual = startup.acquire_project_entry

        def acquire(event, package, **kwargs):
            owner = testcase._optional_observations.get(str(event.cwd))
            if owner is not None and 'qualifier' not in kwargs:
                kwargs['qualifier'] = lambda record, facts: owner
            return actual(event, package, **kwargs)

        qualification = patch.object(startup, 'acquire_project_entry', side_effect=acquire)
        qualification.start()
        testcase.addCleanup(qualification.stop)
    testcase._optional_observations[str(scope.repository_root)] = observation
    testcase._optional_fixtures.append(fixture)
    return scope


def cli_driver(testcase, scope, script):
    """Run the real CLI with test-only owner injection, never a production flag."""
    path = scope.runtime.state_dir.parent / (script.stem + '-fixture-driver.py')
    observation = testcase._optional_observations[str(scope.repository_root)]
    path.write_text("import sys, runpy\n"
        + f"sys.path.insert(0, {str(PACKAGE)!r})\n"
        + "from runtime import changes, startup\n"
        + f"changes.LOCK_ROOT = __import__('pathlib').Path({str(changes.LOCK_ROOT)!r})\n"
        + f"observation = {observation!r}\n"
        + "actual = startup.acquire_project_entry\n"
        + "def acquire(event, package, **kwargs):\n"
        + "    return actual(event, package, qualifier=lambda record, facts: observation, **kwargs)\n"
        + "startup.acquire_project_entry = acquire\n"
        + f"runpy.run_path({str(script)!r}, run_name='__main__')\n")
    return path


def configure(repo, *, executable=None, auth=None, routes=('proposals', 'workers')):
    from runtime import openrouter_settings as settings
    report = settings.configure(repo, dict(expected_revision=0, routes=list(routes),
        opencode=str(executable) if executable else None, auth_data=str(auth) if auth else None))
    return settings.enable(repo, dict(expected_revision=report['revision']))
