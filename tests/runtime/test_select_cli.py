"""The select CLI keeps a pick alive when its answer is large: UTF-8 output, then a truncated unmatched list."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
