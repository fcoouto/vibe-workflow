"""The select CLI keeps a pick alive when its answer is large: UTF-8 output, then a truncated unmatched list."""
import json
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


if __name__ == "__main__":
    unittest.main()
