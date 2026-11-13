"""Reconcile one task's Codex usage with its completed verification (D9 O4).

Pure: reads no file, state or network. ``observations`` is the ``acquire_task_observations`` output from
``runtime.adapters.codex_usage`` (O3); ``verification`` is one ``verify_completion`` result. A counter total is
known only when every thread reports that counter; otherwise it is None, and coverage is incomplete. Malformed
input is refused with one fixed message that carries no input value.
"""

from copy import deepcopy

from runtime.adapters.codex_usage import COUNTERS, GAP_CODES, SOURCE


_OBSERVATION_KEYS = frozenset({"threads", "expected_threads", "coverage", "gaps"})
_THREAD_KEYS = frozenset({"role", "source", "cli_version", "thread_id", "baseline", "final", "usage",
                          "model_settings", "gaps"})
_TASK_KEYS = ("client", "session_id", "task_id")
_COVERAGE = ("complete", "incomplete")


def reconcile_task(observations, *, verification):
    """Return the reconciled task record; inputs are left unchanged and nothing in the result aliases them."""
    threads = _observations(observations)
    task = _verification(verification)
    totals = {key: (sum(item["usage"][key] for item in threads)
                    if threads and all(item["usage"][key] is not None for item in threads) else None)
              for key in COUNTERS}
    complete = (observations["coverage"] == "complete" and not observations["gaps"]
                and observations["expected_threads"] == len(threads)
                and all(value is not None for value in totals.values()))
    return {
        "task_ref": {key: task[key] for key in _TASK_KEYS},
        "verified": verification["verified"],
        "admission_mode": verification["admission_mode"],
        "reconciled_revision": task["revision"],
        "totals": totals,
        "counted_threads": [{"thread_id": item["thread_id"], "role": item["role"]}
                            for item in threads if not item["gaps"]],
        "coverage": "complete" if complete else "incomplete",
        "gaps": [dict(gap) for gap in observations["gaps"]],
        "source_versions": sorted({item["cli_version"] for item in threads
                                   if not item["gaps"] and item["cli_version"] is not None}),
        "model_settings": [{"thread_id": item["thread_id"], "role": item["role"],
                            "model_settings": deepcopy(item["model_settings"])} for item in threads],
    }


def _require(condition):
    if not condition:
        raise ValueError("Invalid task usage reconciliation input")


def _text(value):
    return type(value) is str and value != ""


def _count(value):
    return value is None or (type(value) is int and value >= 0)


def _observations(value):
    _require(type(value) is dict and set(value) == _OBSERVATION_KEYS)
    threads, expected, gaps = value["threads"], value["expected_threads"], value["gaps"]
    _require(type(threads) is list and type(expected) is int and expected >= 0 and value["coverage"] in _COVERAGE)
    _require(type(gaps) is list)
    for gap in gaps:
        _require(type(gap) is dict and set(gap) == {"thread_id", "code"})
        _require((gap["thread_id"] is None or _text(gap["thread_id"])) and type(gap["code"]) is str
                 and gap["code"] in GAP_CODES)
    seen = set()
    for item in threads:
        _thread(item)
        _require(item["thread_id"] not in seen)
        seen.add(item["thread_id"])
    return threads


def _thread(item):
    _require(type(item) is dict and set(item) == _THREAD_KEYS)
    _require(_text(item["role"]) and _text(item["thread_id"]) and item["source"] == SOURCE)
    _require(item["cli_version"] is None or _text(item["cli_version"]))
    _require(type(item["model_settings"]) is list and all(type(entry) is dict for entry in item["model_settings"]))
    _require(type(item["gaps"]) is list and all(type(code) is str and code in GAP_CODES for code in item["gaps"]))
    for field in ("baseline", "final", "usage"):
        counters = item[field]
        _require(type(counters) is dict and set(counters) == set(COUNTERS))
        _require(all(_count(counters[key]) for key in COUNTERS))
    # O3 leaves every counter unknown on a gapped thread and known on a gap-free one; anything else is malformed.
    _require(all((item["usage"][key] is None) == bool(item["gaps"]) for key in COUNTERS))


def _verification(value):
    _require(type(value) is dict and type(value.get("verified")) is bool and type(value.get("admission_mode")) is str)
    task = value.get("task")
    _require(type(task) is dict and all(type(task.get(key)) is str for key in _TASK_KEYS))
    _require(type(task.get("revision")) is int and task["revision"] >= 0)
    return task
