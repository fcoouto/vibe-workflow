"""Disposable enrolled Council scopes; no live runtime or account changes."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from runtime import changes
from tests.openrouter_fixtures import configure, enroll


class CouncilCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='council ')
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name).resolve()
        self.scope = enroll(self)
        self.repo = self.scope.repository_root
        guard = patch.object(changes, 'LOCK_ROOT', self.temporary / 'locks')
        guard.start()
        self.addCleanup(guard.stop)
        self.executable = self.temporary / 'opencode'
        self.executable.write_text('#!/bin/sh\nexit 0\n')
        self.executable.chmod(0o700)
        self.auth = self.temporary / 'auth'
        self.auth.mkdir(mode=0o700)
        self.settings = configure(self.repo, executable=self.executable, auth=self.auth, routes=('council',))
        self.root = Path(self.settings['council_root'])
        self.root.mkdir(mode=0o700)

    def approved(self, paths: list[str]) -> dict:
        return {name: dict(sha256=hashlib.sha256((self.repo / name).read_bytes()).hexdigest(),
                           size=(self.repo / name).stat().st_size) for name in paths}

    def historical_run(self):
        """Exercise persisted sessions created before per-request/full-panel contracts."""
        from runtime import council
        with council._locked(self.run) as value:
            value.pop('timing_policy', None)
            value.pop('completion_policy', None)
            council._save(self.run, value)

    def native_seat(self, model='native-balanced'):
        return dict(kind='native', model=model, effort='max', supported_efforts=['max'],
            input_tokens=10000, output_tokens=4096, steps=12,
            selection='Approved native reasoning', supervisor_id='coordinator')

    def native_receipt(self, packet, report='Cited evidence and uncertainty.', *, accepted=True, child_id=None):
        return dict(issued_id=packet['issued_id'], child_id=child_id or 'child-' + packet['issued_id'],
            supervisor_id='coordinator', observed_model=packet['model'], observed_effort=packet['reasoning_effort'],
            fork_turns='none', local_execution_stopped=True, accepted=accepted,
            checks=['Host checked source and scope'], report=report)

    def git(self, *arguments):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        return subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'user.name=Council fixture', '-c', 'user.email=council@example.invalid',
            '-C', str(self.repo), *arguments], env=environment, check=True, capture_output=True)
