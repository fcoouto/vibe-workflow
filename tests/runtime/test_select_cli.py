"""The select CLI keeps a pick alive when its answer is large: UTF-8 output, then a truncated unmatched list."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "select.py"
LIMIT = 65536


def request_bytes(entries):
    request = dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="reversible"), models=[],
                   constraints=dict(excluded_models=entries))
    return json.dumps(request, ensure_ascii=False).encode()


def run(root, entries):
    raw = request_bytes(entries)
    assert len(raw) <= LIMIT
    done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--state-dir", str(root)], input=raw,
                          capture_output=True, timeout=60)
    return done.stdout, json.loads(done.stdout)


class SelectCliSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_unicode_unmatched_is_echoed_in_full_when_it_fits_as_utf8(self):
        entries = ["字" * 100 + str(n) for n in range(110)]
        out, answer = run(self.root, entries)
        self.assertEqual((answer["state"], answer["reason"]), ("no_selection", "no_models"))
        self.assertEqual(answer["unmatched"], entries)
        self.assertNotIn("unmatched_more", answer)
        self.assertGreater(len(json.dumps(answer).encode()), LIMIT)
        self.assertLessEqual(len(out), LIMIT)

    def test_oversize_unmatched_is_truncated_not_blocked(self):
        entries = []
        while len(entries) < 256:
            more = entries + ["字" * 252 + f"{len(entries):03d}"]
            if len(request_bytes(more)) > LIMIT:
                break
            entries = more
        self.assertGreater(len(entries), 50)
        # Fill the request to the limit, so the larger answer cannot fit.
        while len(entries) < 256:
            room = LIMIT - len(request_bytes(entries + ["0"])) + 1
            if room < 6:
                break
            entries.append(f"{len(entries):03d}" + "x" * (min(250, room - 4) - 3))
        self.assertLessEqual(len(request_bytes(entries)), LIMIT)
        out, answer = run(self.root, entries)
        self.assertEqual((answer["state"], answer["reason"]), ("no_selection", "no_models"))
        kept = answer.get("unmatched", [])
        self.assertEqual(kept, entries[:len(kept)])
        self.assertEqual(len(kept) + answer["unmatched_more"], len(entries))
        self.assertGreater(answer["unmatched_more"], 0)
        self.assertLessEqual(len(out), LIMIT)


ROOT = Path(__file__).resolve().parents[2]
HARNESS = """
import json, runpy, sys
from pathlib import Path
script, state = sys.argv[1], sys.argv[2]
sys.argv = [script, "--state-dir", state, "--claude-limit"]
before = list(sys.path)
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit:
    pass
print(json.dumps(dict(first=before[0], kept=before[0] in sys.path, head=sys.path[0],
                      scripts=str(Path(script).resolve().parent) in sys.path)), file=sys.stderr)
"""
SCRIPT_RUN = "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))\n"


class SelectPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name).resolve() / "state"
        self.state.mkdir(mode=0o700)
        self.request = json.dumps({"reset_at": int(time.time()) + 3600}).encode()

    def run_harness(self, code):
        done = subprocess.run([sys.executable, *code], input=self.request, capture_output=True, timeout=60)
        return done.stdout, json.loads(done.stderr.decode().strip().splitlines()[-1])

    def test_isolated_mode_keeps_its_stdlib_entry_and_runs(self):
        out, path = self.run_harness(["-I", "-c", HARNESS, str(SCRIPT), str(self.state)])
        self.assertTrue(path["kept"], path)
        self.assertEqual(path["head"], str(ROOT))
        self.assertFalse(path["scripts"])
        self.assertEqual(out, b'{"state":"skipped"}\n')

    def test_script_run_drops_its_own_directory_and_answers(self):
        out, path = self.run_harness(["-B", "-c", SCRIPT_RUN + HARNESS, str(SCRIPT), str(self.state)])
        self.assertEqual(path["first"], str(SCRIPT.parent))
        self.assertEqual(path["head"], str(ROOT))
        self.assertFalse(path["scripts"])
        self.assertEqual(out, b'{"state":"skipped"}\n')


CODEX = """#!%s
import json, sys
open(%r, "a").write("entered\\n")
sys.stdin.readline(); print(json.dumps(dict(id=1, result={})), flush=True)
sys.stdin.readline(); sys.stdin.readline()
print(json.dumps(dict(id=2, result=dict(rateLimitsByLimitId=dict(codex=dict(limitId="codex", primary=dict(
    usedPercent=90, windowDurationMins=10080, resetsAt=%d), secondary=None))))), flush=True)
"""


class UsageRefreshCliTest(unittest.TestCase):
    """The private `--usage-refresh` mode: exact `{}` in, one fixed state out, never beside another mode."""
    def setUp(self):
        from runtime import opencode_account
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.entered = self.root / "entered.log"
        tools = self.root / "bin"
        tools.mkdir()
        (tools / "codex").write_text(CODEX % (sys.executable, str(self.entered), int(time.time()) + 7200))
        (tools / "codex").chmod(0o700)
        # Only the fake `codex` is on PATH, in a private home, without the test-home refusal: a read would be seen.
        self.env = dict(PATH=str(tools), HOME=str(self.root))
        self.account = opencode_account
        binding = dict(key="a" * 32, generation="b" * 32)
        opencode_account._transition(self.state / "router.json", "chatgpt", lambda part: dict(
            identity=binding, account=dict(binding, observed_at=int(time.time()))))

    def run_process(self, raw, *flags, state=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--state-dir", str(state or self.state), *flags],
                              input=raw, capture_output=True, timeout=60, cwd=self.root, env=self.env)

    def run_cli(self, raw, *flags, state=None):
        return self.run_process(raw, "--usage-refresh", *flags, state=state).stdout

    def usage(self):
        return json.loads((self.state / self.account.STORE_FILE).read_text())["chatgpt"].get("chatgpt_usage")

    def test_malformed_stdin_fails_before_the_codex_reader_is_entered(self):
        for raw in (b"", b"not json", b"[]", b"null", b'{"a": 1}', b'{"wanted": []}', b"{} ", b"{}\n", b"{}{}",
                    b"x" * 65537):
            with self.subTest(raw=raw[:24]):
                self.assertEqual(self.run_cli(raw), b'{"state":"failed"}\n')
        self.assertFalse(self.entered.exists())
        self.assertIsNone(self.usage())  # not even a start record

    def test_a_combined_mode_fails_and_does_neither_job(self):
        for flags in (["--council-pick"], ["--council-roster"], ["--check-setup"], ["--check-setup", "--allow-fetch"],
                      ["--allow-fetch"], ["--refresh"], ["--chatgpt-limit"], ["--claude-limit"], ["--feedback"],
                      ["--allow-fetch", "--refresh"], ["--allow-fetch", "--council-pick"]):
            with self.subTest(flags=flags):
                done = self.run_process(b"{}", "--usage-refresh", *flags)
                self.assertEqual((done.returncode, done.stdout), (0, b'{"state":"failed"}\n'))
        self.assertFalse(self.entered.exists())
        self.assertIsNone(self.usage())
        self.assertEqual(sorted(path.name for path in self.state.iterdir() if not path.name.startswith(".")),
                         [self.account.STORE_FILE])  # no limit mark, score cache or other mode's state

    def test_allow_fetch_keeps_its_rule_for_every_call_that_is_not_the_worker(self):
        for flags in (["--allow-fetch"], ["--allow-fetch", "--refresh"], ["--allow-fetch", "--council-pick"],
                      ["--allow-fetch", "--chatgpt-limit"], ["--allow-fetch", "--feedback"]):
            with self.subTest(flags=flags):
                done = self.run_process(b"{}", *flags)
                self.assertEqual((done.returncode, done.stdout), (2, b""))
                self.assertIn(b"--allow-fetch needs --check-setup", done.stderr)
        self.assertFalse(self.entered.exists())
        self.assertIsNone(self.usage())
        self.assertEqual(sorted(path.name for path in self.state.iterdir() if not path.name.startswith(".")),
                         [self.account.STORE_FILE])  # refused before any mode's work, as before

    def test_exact_stdin_runs_one_bound_read_and_answers_only_its_state(self):
        self.assertEqual(self.run_cli(b"{}"), b'{"state":"stored"}\n')
        self.assertEqual(self.entered.read_text().splitlines(), ["entered"])
        self.assertEqual(self.run_cli(b"{}"), b'{"state":"not_due"}\n')
        self.assertEqual(self.entered.read_text().splitlines(), ["entered"])
        self.assertEqual(self.usage()["ok"], True)

    def test_an_unbound_or_unsafe_state_answers_without_a_read(self):
        missing = self.root / "missing"
        self.assertEqual(self.run_cli(b"{}", state=missing), b'{"state":"unbound"}\n')
        self.assertFalse(missing.exists())
        link = self.root / "link"
        link.symlink_to(self.state)
        for unsafe in (link, Path("state"), self.state / ".." / "state"):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(self.run_cli(b"{}", state=unsafe), b'{"state":"failed"}\n')
        self.assertFalse(self.entered.exists())

    def test_the_test_home_refuses_the_read(self):
        self.env["VCW_TEST_HOME"] = str(self.root)
        self.assertEqual(self.run_cli(b"{}"), b'{"state":"failed"}\n')
        self.assertFalse(self.entered.exists())


if __name__ == "__main__":
    unittest.main()
