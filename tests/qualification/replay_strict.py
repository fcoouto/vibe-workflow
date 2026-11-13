"""The strict replay over exported admission records (plan Task F7.1; validation Edit 7 points 2, 3 and 6, Edit 8 point
3). It reads only the caller's export directory written by `replay_export.export` (owner-only, outside every
repository), calls no model and uses no network, and never opens a ledger, route table or history store.

Per ledger identity it walks the records in `at` order (file order breaks ties):

- **Ranked records** (point 2) are recomputed from their stored detail alone. `routing_funding.choose`, the function
  admission itself calls, gets the stored subscription candidates in stored order (each constraint given its
  candidate's pool back), `shares`, `bucket`, `pressure.epoch`, `pressure.feasible`, `at`, `pressure.minimum_horizon`
  and the earlier records filtered by `routing.decision_rows` (same settings revision, at most 14 days older). The
  candidates' strategy ids must be an order-preserving subsequence of `ranking.shortlist`, and the chosen candidate's
  id must equal `ranking.strategy_id`. A ranked record must be subscription-funded with the fixed subscription-only
  shares (point 6): a ranked paid record fails and is counted, never skipped. A ranked record without its snapshot
  detail (`pressure`, `snapshot_id`, `pressure.candidates`) fails and is counted; it is never replayed by the
  worker-choice rule instead. `ranking.routes_version` is audit data: None is accepted and no route is regenerated.
- **Worker-choice records** (point 3), the pilot's old shape and new ones alike: the class must be the funding class
  of the billing class and the record must hold its constraints. A new one (with `ranking.shortlist`) must have an
  empty shortlist and no `pressure.candidates`. They are counted apart and claim no reproduced ranked route.
- **Every record**: each stored constraint's cost and the record's reservation in that window fit its headroom
  (funding violations; Edit 8 point 3 removed the concurrency cap). A later record of the same ledger whose headroom in
  a pool/window is higher than the last one seen, with no reset between them, is resurrected capacity; a reset is a
  later `resets_at` than the one seen, or a record at or after the seen `resets_at`. A second record of one attempt
  (or a repeated export key) is a stray record.

The report holds counts, work kinds and bands only: no id, title, name or timestamp. A label is reported only when
it is a work kind of the shipped task-profile template (the one package file read besides the export) and a routing
complexity band; any other bucket text is counted under `invalid_labels` and fails the replay, never echoed. A record stores its specialist's
work kind (`bucket[0]`), not the specialist's name, so `specialists` counts work kinds; the three canary specialists
have three distinct ones. `python3 -B -m tests.qualification.replay_strict --dir <directory>` prints it and exits 1
unless `passed`.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from runtime import routing, routing_funding, task_profile
from tests.qualification import replay_export
from tests.qualification.safety import Refused

SUBSCRIPTION_SHARES = dict(api=0.0, subscription=1.0)
COUNTERS = ("records", "ranked", "legacy_records", "class_reproduced", "route_reproduced", "funding_violations",
            "resurrected_capacity", "stray_records", "ranked_paid", "missing_detail", "unknown_mode", "unreadable",
            "invalid_labels")
FAILURES = COUNTERS[5:]


def read(directory):
    """`(envelopes, unreadable)` from the export directory; nothing else is opened. `Refused` unless the directory
    passes `replay_export.check_dir` (absolute, outside every repository, owner-only, never created here)."""
    directory = replay_export.check_dir(Path(directory), create=False)
    path = directory / replay_export.RECORDS
    if not path.exists():
        return [], 0
    envelopes, unreadable = [], 0
    with os.fdopen(replay_export._private_file(path, os.O_RDONLY), "r") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if isinstance(item, dict) and isinstance(item.get("record"), dict) and type(item.get("key")) is str \
                    and type(item["record"].get("at")) in (int, float):
                envelopes.append(item)
            else:
                unreadable += 1
    return envelopes, unreadable


def _number(value):
    return type(value) in (int, float) and value == value


def _subsequence(items, sequence):
    position = 0
    for item in items:
        try:
            position = sequence.index(item, position) + 1
        except ValueError:
            return False
    return True


def _constraints(record):
    """The record's stored constraints, with pools: the chosen route's, and each ranked candidate's."""
    pressure = record.get("pressure") if isinstance(record.get("pressure"), dict) else {}
    found = [c for c in pressure.get("constraints") or [] if isinstance(c, dict)]
    for item in pressure.get("candidates") or []:
        if isinstance(item, dict):
            found += [dict(c, pool=item.get("pool")) for c in item.get("constraints") or [] if isinstance(c, dict)]
    return found


def _funding_violation(record):
    """A stored constraint costing more than its headroom, or the reservation in that window exceeding it."""
    pressure = record.get("pressure") if isinstance(record.get("pressure"), dict) else {}
    reserved = record.get("reserved") if isinstance(record.get("reserved"), dict) else {}
    chosen = [c for c in pressure.get("constraints") or [] if isinstance(c, dict)]
    for item in _constraints(record):
        cost, headroom = item.get("cost"), item.get("headroom")
        if not (_number(cost) and _number(headroom)) or cost > headroom:
            return True
    for item in chosen:
        key = routing._window_key(item.get("pool"), item.get("window"))
        if record.get("class") == "subscription" and key not in reserved:
            return True
        hold = reserved.get(key) if key in reserved else max([v for v in reserved.values() if _number(v)] or [0])
        if not _number(hold) or hold > item["headroom"]:
            return True
    return False


def _resurrected(record, seen):
    """Whether a pool/window's headroom rose since the last record of this ledger without a reset; updates `seen`."""
    raised = False
    current = {}
    for item in _constraints(record):
        if not _number(item.get("headroom")):
            continue  # already a funding violation; no headroom to compare
        place = (item.get("pool"), item.get("window"))
        if place not in current or item["headroom"] < current[place]["headroom"]:
            current[place] = item
    for place, item in current.items():
        last = seen.get(place)
        if last is not None:
            reset = (_number(last["resets_at"]) and (record["at"] >= last["resets_at"] or (
                _number(item.get("resets_at")) and item["resets_at"] > last["resets_at"])))
            if item["headroom"] > last["headroom"] and not reset:
                raised = True
        seen[place] = dict(headroom=item["headroom"], resets_at=item.get("resets_at"))
    return raised


def _ranked(record, earlier, report):
    """Point 2 for one ranked record: updates the class/route/paid/detail counts."""
    pressure = record.get("pressure")
    ranking = record.get("ranking") if isinstance(record.get("ranking"), dict) else {}
    if not (all(key in record for key in replay_export.DETAIL) and isinstance(pressure, dict)
            and isinstance(pressure.get("candidates"), list) and isinstance(ranking.get("shortlist"), list)
            and type(record.get("settings_revision")) is int and _number(pressure.get("epoch"))
            and _number(pressure.get("minimum_horizon")) and pressure["minimum_horizon"] > 0):  # never defaulted
        report["missing_detail"] += 1
        return
    if record.get("billing_class") != routing_funding.SUBSCRIPTION or record.get("shares") != SUBSCRIPTION_SHARES \
            or pressure.get("feasible") != ["subscription"]:
        report["ranked_paid"] += 1
        return
    try:
        candidates = [dict(item, constraints=[dict(c, pool=item["pool"]) for c in item["constraints"]])
                      for item in pressure["candidates"]]
        rows = routing.decision_rows(dict(admissions=earlier), record["settings_revision"], record["at"])
        route, cls = routing_funding.choose(candidates, record["shares"], rows, record["bucket"],
                                            pressure["epoch"], pressure["feasible"], record["at"],
                                            pressure["minimum_horizon"])
    except ValueError as error:
        if str(error) == "billing_class_excluded":
            report["ranked_paid"] += 1
        else:
            report["missing_detail"] += 1
        return
    except (KeyError, TypeError):
        report["missing_detail"] += 1
        return
    report["class_reproduced"] += cls is not None and cls == record.get("class")
    chosen = next((item for item in candidates if item.get("route") == route), None)
    report["route_reproduced"] += bool(
        route is not None and route == record.get("route") and chosen is not None
        and chosen.get("pool") == record.get("pool") and chosen.get("strategy_id") == ranking.get("strategy_id")
        and _subsequence([item.get("strategy_id") for item in candidates], ranking["shortlist"]))


def _worker_choice(record):
    """`(new, failed)` for one worker-choice record under point 3."""
    ranking = record.get("ranking") if isinstance(record.get("ranking"), dict) else {}
    pressure = record.get("pressure") if isinstance(record.get("pressure"), dict) else {}
    new = "shortlist" in ranking
    failed = (_funding_violation(record) or record.get("class") is None or record.get("class") != routing.FUNDING_OF.get(record.get("billing_class"))
              or not [c for c in pressure.get("constraints") or [] if isinstance(c, dict)]
              or (new and (ranking["shortlist"] != [] or "candidates" in pressure)))
    return new, failed


def replay_records(records, identity=None):
    """The report for the records of one ledger, as `run` reports an export."""
    return _report([(identity, None, record) for record in records], 0)


def _report(triples, unreadable):
    """`triples` are `(ledger identity, export key or None, record)`."""
    report = {name: 0 for name in COUNTERS}
    report.update(unreadable=unreadable, worker_choice=dict(old=0, new=0, failed=0), specialists={}, bands={})
    kinds = set(task_profile.load()["work_kinds"])  # the shipped template's work kinds, never export text
    ledgers, keys = {}, set()
    for position, (identity, key, record) in enumerate(triples):
        ledgers.setdefault(identity, []).append((record["at"], position, key, record))
    for identity, items in ledgers.items():
        earlier, attempts, seen = [], set(), {}
        for _, _, key, record in sorted(items, key=lambda item: item[:2]):
            report["records"] += 1
            bucket = record.get("bucket") if isinstance(record.get("bucket"), list) else []
            if len(bucket) == 2 and bucket[0] in kinds and bucket[1] in routing.COMPLEXITY_BANDS:
                report["specialists"][bucket[0]] = report["specialists"].get(bucket[0], 0) + 1
                report["bands"][bucket[1]] = report["bands"].get(bucket[1], 0) + 1
            else:
                report["invalid_labels"] += 1  # counted, never echoed: export text is untrusted
            attempt = record.get("attempt_id")
            report["stray_records"] += attempt in attempts or (key is not None and key in keys)
            attempts.add(attempt)
            keys.add(key)
            report["funding_violations"] += _funding_violation(record)
            report["resurrected_capacity"] += _resurrected(record, seen)
            if record.get("mode") == "ranked":
                report["ranked"] += 1
                _ranked(record, earlier, report)
            elif record.get("mode") == "worker_choice":
                report["legacy_records"] += 1
                new, failed = _worker_choice(record)
                report["worker_choice"]["new" if new else "old"] += 1
                report["worker_choice"]["failed"] += failed
            else:
                report["unknown_mode"] += 1
            earlier.append(record)
    report["specialists"] = dict(sorted(report["specialists"].items()))
    report["bands"] = dict(sorted(report["bands"].items()))
    report["passed"] = bool(
        report["records"] and report["class_reproduced"] == report["ranked"] == report["route_reproduced"]
        and not any(report[name] for name in FAILURES)
        and not report["worker_choice"]["failed"])
    return report


def run(directory):
    """The strict replay report over an export directory: counts only; `passed` is True only when every record
    replays, nothing is unreadable and at least one record exists."""
    envelopes, unreadable = read(directory)
    return _report([(item.get("ledger_identity"), item["key"], item["record"]) for item in envelopes], unreadable)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strict replay over an exported admission directory (0 calls).")
    parser.add_argument("--dir", required=True, help="the export directory written by replay_export.export")
    args = parser.parse_args(argv)
    try:
        report = run(args.dir)
    except Refused as refusal:
        print(json.dumps(dict(passed=False, refused=str(refusal)), sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
