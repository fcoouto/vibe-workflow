import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import time
import unittest

from runtime import chatgpt_limit, codex_reset, opencode_account

NOW = 1_800_000_000


def fake_codex(folder, body):
    """A fake `codex`: `body` runs after the three expected lines are read (prints answers on `out`)."""
    path = Path(folder) / 'codex'
    path.write_text('#!%s\n' % sys.executable + textwrap.dedent('''\
        import json, sys, time
        def out(value):
            print(json.dumps(value), flush=True)
        def read():
            return json.loads(sys.stdin.readline())
        ''') + textwrap.dedent(body))
    path.chmod(0o700)
    return [str(path)]


ANSWER = '''
read(); out({"id": 1, "result": {}})
read(); read()
out({"id": 2, "result": {"rateLimits": json.loads(%r)}})
'''


class ChatgptLimitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.state = self.dir / 'state'
        self.state.mkdir(mode=0o700)
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.dir)}  # no VCW_TEST_HOME: fake runs only

    def window(self, percent, reset):
        return dict(usedPercent=percent, windowDurationMins=300, resetsAt=reset)

    def read(self, body, **kw):
        return codex_reset.read_reset(now=NOW, environ=self.env, command=fake_codex(self.dir, body), **kw)

    def test_mark_uses_codex_reset(self):
        record = chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 7200)
        self.assertEqual(record, dict(version=1, marked_at=NOW, until=NOW + 7200, source='codex'))
        self.assertEqual(stat.S_IMODE((self.state / chatgpt_limit.FILE).stat().st_mode), 0o600)

    def test_mark_falls_back_to_one_hour(self):
        for reset in (None, NOW - 5):
            record = chatgpt_limit.mark(self.state, NOW, reader=lambda reset=reset, **kw: reset)
            self.assertEqual((record['until'], record['source']), (NOW + 3600, 'fallback'))

    def test_limited_window(self):
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))
        chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 100)
        self.assertTrue(chatgpt_limit.limited(self.state, NOW))
        self.assertFalse(chatgpt_limit.limited(self.state, NOW + 100))
        self.assertFalse(chatgpt_limit.limited(self.state, NOW - 1))
        path = self.state / chatgpt_limit.FILE
        path.chmod(0o644)
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))
        path.chmod(0o600)
        path.write_text('{bad')
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))

    def test_quota_gate_excludes_only_chatgpt(self):
        chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 100)
        anchor = self.state / 'router.json'
        gate = lambda path, pool: opencode_account._quota_gate(  # noqa: E731
            anchor, dict(route=path + ':m', billing_class='included_subscription', pool=pool), NOW)
        self.assertEqual(gate('opencode-chatgpt-subscription', 'chatgpt-subscription'), ('quota_exhausted', 100))
        self.assertEqual(gate('opencode-go-subscription', 'opencode-go-subscription'), (None, None))
        self.assertEqual(gate('opencode-claude-subscription', 'claude-subscription')[0], None)

    def test_read_reset_picks_highest_used_window(self):
        both = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=self.window(90, NOW + 200)))
        self.assertEqual(self.read(ANSWER % (both,)), NOW + 200)
        only = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=None))
        self.assertEqual(self.read(ANSWER % (only,)), NOW + 100)
        past = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=self.window(90, NOW - 1)))
        self.assertEqual(self.read(ANSWER % (past,)), NOW + 100)

    def test_read_reset_error_answer_is_none(self):
        body = 'read(); out({"id": 1, "result": {}})\nread(); read()\nout({"id": 2, "error": {"code": -32600, "message": "x"}})\n'
        self.assertIsNone(self.read(body))
        self.assertIsNone(self.read('print("not json", flush=True)\ntime.sleep(5)\n'))

    def test_read_reset_refuses_under_test_home_and_never_reads_auth(self):
        marker = self.dir / 'spawned'
        command = fake_codex(self.dir, 'open(%r, "w").close()\n' % str(marker))
        self.assertIsNone(codex_reset.read_reset(now=NOW, environ=dict(self.env, VCW_TEST_HOME=str(self.dir)),
                                                 command=command))
        self.assertFalse(marker.exists())
        self.assertIsNone(codex_reset.read_reset(now=NOW, environ=dict(self.env, VCW_TEST_HOME='/x')))
        opened = []
        sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == 'open' else None)
        both = json.dumps(dict(primary=self.window(40, NOW + 100)))
        self.assertEqual(self.read(ANSWER % (both,)), NOW + 100)
        self.assertFalse([path for path in opened if path.endswith('auth.json')])

    def test_read_reset_times_out(self):
        pidfile = self.dir / 'pid'
        body = 'import os\nopen(%r, "w").write(str(os.getpid()))\ntime.sleep(60)\n' % str(pidfile)
        start = time.monotonic()
        self.assertIsNone(self.read(body, timeout=0.5))
        self.assertLess(time.monotonic() - start, 1.5)
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == '__main__':
    unittest.main()
