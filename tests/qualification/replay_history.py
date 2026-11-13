"""Advisory historical replay over past session metadata (validation Edit 6; labels amended by Edit 7; store fields
registered by Edit 9 and its 12 November supplement; project-only scope in Edit 10).

`run(stores, ...)` reads each registered store through one `mode=ro` SQLite connection, selects only the metadata
columns the registration maps, labels each eligible session by the fixed rules and returns an aggregate report. It
holds titles in memory for the keyword match only and returns no session id, title, timestamp or path. Extracted
rows stay in memory; the only file written is a private temporary admission ledger, removed after the run.

`compose` routes the eligible sessions through the real router (`ensure_profile`, `rank`, `admit`) over the saved
qualified paths and strategy table beside `settings_path`; `run` calls it only after every gate passes. The gates
fail closed with a precise reason, in this order; the first two open no store:

- `exclusion_basis_missing`: no qualification and pilot session ids, sources and check method from an approved
  caller (shape-checked here, not verified; Edit 9);
- `_registered`: `store_unregistered` for any store outside the retained OpenCode/Codex mapping (including `opus`,
  excluded by Edit 10), then `store_missing` when either retained source is absent;
- `gather` (the first read): `qualification_session_found` when an eligible session id is one of those ids;
- `route_unqualified: <code>`: no settings path, or `routing_paths.gate` finds no qualified path or no generated route.

Callers supply only approved roots of this project's current and legacy checkouts, never another project's roots
or their shared parent. Each authorized read binds the observed schema by SHA-256 (`schemas`, by store name).
Registration is not permission: no historical run is authorized by this module.
"""
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import quote

from runtime import (routing, routing_launch, routing_observations, routing_paths, routing_settings, task_profile,
                     usage_budget)
from tests.qualification.safety import Refused

# Retained source mappings (Edit 9); Edit 10 excludes the other `opus` project rather than requiring its mapping.
MAPPED_STORES = dict(opencode="opencode", codex="codex")
HOSTS = dict(opencode="opencode", codex="codex_native")  # each mapped store's launching host (Edit 9 host formats)
WINDOW_UNIT = routing_paths.WINDOW_UNIT
WINDOW_LENGTHS = dict(routing_observations.RATE_WINDOWS.values())  # 5h and 7d, in seconds
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
# Codex source (codex-rs/state, epoch_millis_to_datetime): values below 2020-01-01 in milliseconds are legacy
# second-precision rows and are multiplied by 1000.
MIN_EPOCH_MILLIS = 1_577_836_800_000
EXCLUSIONS = ("no_parent", "no_model_id", "no_timestamp", "still_running", "host_marked_title_compaction_summary",
              "duplicate")
# The mapped columns per store (Edit 9). A missing or retyped one stops the run until a dated edit maps it again.
MAPPED = dict(
    codex=dict(threads=dict(id="TEXT", agent_role="TEXT", title="TEXT", model_provider="TEXT", model="TEXT",
                            reasoning_effort="TEXT", created_at_ms="INTEGER", updated_at_ms="INTEGER", cwd="TEXT",
                            attached_thread_parent_id="TEXT"),
               thread_spawn_edges=dict(parent_thread_id="TEXT", child_thread_id="TEXT")),
    opencode=dict(session_v2=dict(id="TEXT", parent_id="TEXT", project_id="TEXT", agent="TEXT", title="TEXT",
                                  model="TEXT", time_created="INTEGER", time_updated="INTEGER",
                                  tokens_output="INTEGER", tokens_reasoning="INTEGER"),
                  project=dict(id="TEXT", worktree="TEXT"),
                  project_directory=dict(project_id="TEXT", directory="TEXT")))
CODEX_READ = ("SELECT t.id, t.agent_role, t.title, t.model_provider, t.model, t.reasoning_effort, t.created_at_ms, "
              "t.updated_at_ms, e.parent_thread_id, t.attached_thread_parent_id FROM threads t "
              "LEFT JOIN thread_spawn_edges e ON e.child_thread_id = t.id WHERE t.cwd = ? OR substr(t.cwd, 1, ?) = ?")
# Store A (supplement of 12 November 2026): `model` is JSON `{id, providerID, variant?}` and the timestamps are
# epoch milliseconds. Whether historical `tokens_output` includes `tokens_reasoning` stays unproved.
OPENCODE_READ = ("SELECT s.id, s.parent_id, s.agent, s.title, s.model, s.time_created, s.time_updated, "
                 "s.tokens_output, s.tokens_reasoning FROM session_v2 s WHERE s.project_id IN "
                 "(SELECT id FROM project WHERE worktree = ? UNION SELECT project_id FROM project_directory "
                 "WHERE directory = ?)")

ROLE_AGENT = (("reviewer", ("reviewer", "review")), ("researcher", ("explore", "explorer", "researcher")),
              ("repair", ("repair",)))
ROLE_TITLE = (("reviewer", ("review", "reviewer", "audit", "verify", "verification")),
              ("researcher", ("research", "investigate", "investigation", "explore", "survey", "scout", "diagnose")),
              ("repair", ("repair", "rescue")),
              ("implementer", ("implement", "implementation", "fix", "build", "add", "refactor", "write", "update",
                               "migrate")))
SPECIALIST = dict(reviewer="code-reviewer", researcher="explorer-agent", implementer="software-engineer",
                  repair="software-engineer")  # Edit 7 point 4
TOKEN_BANDS = ((5_000, "low"), (20_000, "medium"), (60_000, "high"))
DURATION_BANDS = ((5 * MINUTE_MS, "low"), (20 * MINUTE_MS, "medium"), (60 * MINUTE_MS, "high"))
BANDS = routing.COMPLEXITY_BANDS
SCOPE = "historical-replay"
LABEL_FIELDS = ("specialist", "work_kind", "complexity_band", "risk_band", "funding_class")


def _connect(path):
    """One read-only connection: a plain `mode=ro` URI, never `immutable=1` (which would skip a live WAL)."""
    return sqlite3.connect("file:%s?mode=ro" % quote(Path(path).resolve().as_posix()), uri=True, timeout=10)


def _check_schema(db, store):
    for table, columns in MAPPED[store["kind"]].items():
        found = {name: kind.upper() for name, kind in db.execute("SELECT name, type FROM pragma_table_info(?)",
                                                                 (table,))}
        for column, kind in columns.items():
            if found.get(column) != kind:
                raise Refused("schema_changed: %s %s.%s" % (store["name"], table, column))


def _millis(value):
    if type(value) is not int:
        return None
    return value * 1000 if value < MIN_EPOCH_MILLIS else value


def _codex_rows(db, store):
    rows = []
    for root in store["roots"]:
        prefix = root.rstrip("/") + "/"
        for (identifier, agent, title, provider, model, effort, start, end, parent,
             attached) in db.execute(CODEX_READ, (root, len(prefix), prefix)):
            rows.append(dict(id=identifier, parent=parent or attached, agent=agent, title=title, provider=provider,
                             model=model, effort=effort, start_ms=_millis(start), end_ms=_millis(end), output=None,
                             reasoning=None, output_includes_reasoning=None))
    return rows


def _opencode_model(text):
    """`(provider, model id, variant)` from the JSON `model` column; malformed or partial values are missing."""
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return None, None, None
    fields = [value.get(key) for key in ("providerID", "id", "variant")]
    return tuple(item if isinstance(item, str) and item else None for item in fields)


def _opencode_rows(db, store):
    rows = []
    for root in store["roots"]:
        for identifier, parent, agent, title, model, start, end, output, reasoning in db.execute(
                OPENCODE_READ, (root, root)):
            provider, model_id, variant = _opencode_model(model)
            rows.append(dict(id=identifier, parent=parent, agent=agent, title=title, provider=provider,
                             model=model_id, effort=variant, start_ms=start if type(start) is int else None,
                             end_ms=end if type(end) is int else None, output=output, reasoning=reasoning,
                             output_includes_reasoning=None))
    return rows


READERS = dict(codex=_codex_rows, opencode=_opencode_rows)


def _read(store):
    if store.get("kind") not in READERS:
        raise Refused("store_unregistered: %s has no registered field mapping" % store.get("name"))
    with closing(_connect(store["path"])) as db:
        _check_schema(db, store)
        schema = _schema_digest(db)
        rows = READERS[store["kind"]](db, store)
    unique = {}  # overlapping roots can select one row twice; that is one read, not a duplicate session
    for row in rows:
        row.update(store=store["name"], kind=store["kind"], path=str(store["path"]))
        unique.setdefault(row["id"], row)
    return list(unique.values()), schema


def _schema_digest(db):
    """SHA-256 of the schema this read observes, on the same read-only connection and by the same rule as the
    Step 1 fingerprints (`sqlite_schema` rows `type, name, tbl_name, sql`, ordered by type and name, as JSON). It
    binds a report to its input schema; it reads no row and authorizes nothing."""
    schema = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name").fetchall()
    return hashlib.sha256(json.dumps([list(item) for item in schema]).encode()).hexdigest()


def _first_copy(rows):
    """One row per session id: the earliest start (missing last), then store name, then path, in byte order."""
    def key(row):
        return (row["start_ms"] is None, row["start_ms"] or 0, row["store"].encode(), row["path"].encode())
    kept, duplicates = {}, 0
    for row in sorted(rows, key=key):
        if row["id"] in kept:
            duplicates += 1
        else:
            kept[row["id"]] = row
    return list(kept.values()), duplicates


def collect(stores, *, now_ms):
    """Read the mapped stores; return `sessions_found`, `excluded` by reason, the in-memory `eligible` rows and
    `schemas`, the observed schema digest of each store read, by store name (never a path or SQL)."""
    rows, schemas = [], {}
    for store in stores:
        found, schema = _read(store)
        rows.extend(found)
        schemas.setdefault(store["name"], []).append(schema)
    unique, duplicates = _first_copy(rows)
    excluded = dict.fromkeys(EXCLUSIONS, 0)
    excluded["duplicate"] = duplicates
    eligible = []
    for row in unique:
        if not row["parent"]:
            excluded["no_parent"] += 1
        elif not row["model"]:
            excluded["no_model_id"] += 1
        elif row["start_ms"] is None or row["end_ms"] is None:
            excluded["no_timestamp"] += 1
        elif row["end_ms"] > now_ms - HOUR_MS:
            excluded["still_running"] += 1
        else:  # Neither store has a title, compaction or summary marker (Edit 9): that count stays 0.
            eligible.append(row)
    return dict(sessions_found=len(unique), excluded=excluded, eligible=eligible, schemas=schemas)


def _words(text):
    return [word.lower() for word in "".join(c if c.isalnum() else " " for c in text or "").split()]


def _role(agent, title):
    words = set(_words(agent))
    for role, group in ROLE_AGENT:
        if words & set(group):
            return role
    words = set(_words(title))
    for role, group in ROLE_TITLE:
        if words & set(group):
            return role
    return "unknown"


def _band(value, bands):
    if value is None:
        return "unknown"
    for limit, band in bands:
        if value < limit:
            return band
    return "critical"


def _complexity(row):
    output, reasoning, includes = row.get("output"), row.get("reasoning"), row.get("output_includes_reasoning")
    if output is None or includes is None:
        tokens = None  # an unproved inclusion cannot count reasoning once (Edit 6 rule 3; Edit 9)
    elif includes:
        tokens = output
    else:
        tokens = None if reasoning is None else output + reasoning
    duration = None if row.get("start_ms") is None or row.get("end_ms") is None else row["end_ms"] - row["start_ms"]
    known = [band for band in (_band(tokens, TOKEN_BANDS), _band(duration, DURATION_BANDS)) if band != "unknown"]
    return max(known, key=BANDS.index) if known else "unknown"


def _funding(row):
    if row.get("provider") == "openrouter":
        return "dollar"
    auth = row.get("auth_mode")  # no mapped store records one (Edit 9)
    if auth in ("chatgpt", "claude"):
        return "included_subscription"
    if auth == "api_key":
        return "dollar"
    if row.get("kind") == "opencode" and row.get("provider") == "claude-agent":
        return "included_subscription"
    return "unknown"


def label(row):
    """The registered labels of one eligible row. The title is read here only and never returned."""
    specialist = SPECIALIST.get(_role(row.get("agent"), row.get("title")), "unknown")
    work_kind = task_profile.specialist(specialist)["work_kind"] if specialist != "unknown" else "unknown"
    return dict(store=row["store"], specialist=specialist, work_kind=work_kind, complexity_band=_complexity(row),
                risk_band="unknown", funding_class=_funding(row),
                route=dict(model=row.get("model") or "unknown", effort=row.get("effort") or "unknown"))


def profile(labels):
    """The router's TaskProfile for a labelled session; unknown bands are missing, so the router's floors apply.
    An unknown specialist has no profile (None): the router cannot route it, and it is counted, not compared."""
    if labels["specialist"] == "unknown":
        return None
    supplied = {field: labels[field] for field in ("complexity_band", "risk_band") if labels[field] != "unknown"}
    return routing.ensure_profile(dict(role=labels["specialist"], scope_ref=SCOPE, profile=supplied))


def _tally(target, key, disagreed):
    entry = target.setdefault(key, dict(compared=0, disagreed=0))
    entry["compared"] += 1
    entry["disagreed"] += int(disagreed)


def summarize(rows, decisions):
    """The aggregate advisory report: label distributions, unknown counts, pending arrivals, historical dollar
    sessions and the disagreement rate overall and by specialist, band, funding class and store."""
    labels = {field: {} for field in LABEL_FIELDS}
    unknowns = dict.fromkeys(LABEL_FIELDS + ("effort",), 0)
    disagreement = dict(overall=dict(compared=0, disagreed=0), by_specialist={}, by_complexity_band={},
                        by_funding_class={}, by_store={})
    compared = pending = not_compared = 0
    if len(rows) != len(decisions):
        raise ValueError("one decision per labelled session")
    for row, decision in zip(rows, decisions):
        for field in LABEL_FIELDS:
            labels[field][row[field]] = labels[field].get(row[field], 0) + 1
            unknowns[field] += row[field] == "unknown"
        unknowns["effort"] += row["route"]["effort"] == "unknown"
        if decision.get("status") == "pending":
            pending += 1
            continue
        if decision.get("status") != "admitted":  # e.g. no profile for an unknown specialist
            not_compared += 1
            continue
        compared += 1
        differs = decision.get("model") != row["route"]["model"] or \
            decision.get("funding_class") != row["funding_class"] or \
            (row["route"]["effort"] != "unknown" and decision.get("effort") != row["route"]["effort"])
        _tally(disagreement, "overall", differs)
        for field, bucket in (("specialist", "by_specialist"), ("complexity_band", "by_complexity_band"),
                              ("funding_class", "by_funding_class"), ("store", "by_store")):
            _tally(disagreement[bucket], row[field], differs)
    return dict(labels=labels, unknowns=unknowns, compared=compared, pending=pending, not_compared=not_compared,
                historical_dollar=labels["funding_class"].get("dollar", 0), disagreement=disagreement)


def _basis(exclusions):
    if not isinstance(exclusions, dict):
        return None
    ids, sources = exclusions.get("ids"), exclusions.get("sources")
    if not (isinstance(ids, list) and ids and all(isinstance(i, str) and i for i in ids)):
        return None
    if not (isinstance(sources, list) and sources and all(isinstance(s, str) and s for s in sources)):
        return None
    if not isinstance(exclusions.get("checked"), str) or not exclusions["checked"]:
        return None
    return set(ids)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _harnesses(table):
    """The harnesses each mapped store's launching host can send on (`routing_launch.host_harnesses`): store A's
    OpenCode host over the table's strategies as its catalog, store B's native Codex host its fixed harness."""
    catalog = [dict(harness=item["harness"]) for item in routing_paths._strategies(table) if item.get("harness")]
    return dict(opencode=routing_launch.host_harnesses("opencode", catalog),
                codex=routing_launch.host_harnesses(HOSTS["codex"]))


def _observation(cut, rules, now):
    """The registered window values for one arrival (Edit 6: every subscription window fresh at 0% use) over the
    routes the shared pre-window shortlist kept. Only window values are supplied: entitlement, subscription-only
    billing, per-model demand, host harnesses and the subscription shortlist were already decided by the real
    `routing_observations.shortlist`. Returns `(observation, routes left out for an unknown window length)`."""
    chosen, pools, unknown = [], {}, 0
    for spec in cut["routes"]:
        windows = sorted(routing_paths.pool_windows(rules, spec["pool"]))
        if not all(window in WINDOW_LENGTHS for window in windows):
            unknown += 1  # no static length for this window: it cannot take the registered value
            continue
        route = {key: copy.deepcopy(spec[key]) for key in routing_observations.ROUTE_FIELDS if key in spec}
        route.update(capable=True, permitted=True, binding_enforceable=spec.get("binding_enforceable", True) is True)
        pools.setdefault(spec["pool"], dict(windows=[
            dict(id=window, limit=routing_observations.WINDOW_UNITS, used=0, length=WINDOW_LENGTHS[window],
                 reset_at=None, rolling=True, unit=WINDOW_UNIT, fresh=True) for window in windows]))
        chosen.append(route)
    observation = dict(now=now, routes=chosen, pools=pools, shortlist=cut["shortlist"], snapshot_id="historical-replay")
    return observation, unknown


def _release(ledger, attempt, now):
    """The recorded session ended: its admission's reservation is no longer held."""
    result = routing.record_event(ledger, dict(event_id="end-" + attempt, attempt_id=attempt, kind="not_dispatched"),
                                  now)
    if result.get("state") == "blocked":
        raise Refused("replay_ledger_refused: %s" % result.get("reason"))


def _qualified(settings_path):
    if settings_path is None:
        raise Refused("route_unqualified: settings_path_missing")
    codes = routing_paths.gate(settings_path)
    if codes:
        raise Refused("route_unqualified: %s" % codes[0])


def compose(rows, *, settings_path):
    """Route every eligible session through the real router and return the aggregate report. Shared by `run`, which
    calls it only after every registration gate passes.

    Inputs are the saved qualified paths and strategy table beside `settings_path`, through the production source
    (`routing_observations.default_source`). Each arrival's routes pass the real pre-window
    `routing_observations.shortlist` for its store's launching host, so entitlement, subscription-only billing and
    per-model demand stay real (the current local source verifies no billing, so it fails closed); only the window
    values are the registered ones. `collect`, `refresh_needed`, window reads and readers are never called. Settings
    are the registered defaults (Smart Routing on, API Usage off, no organization). The report counts route
    exclusions (`eligibility`) apart from arrival outcomes (`pending_reasons`: `no_eligible_route` is an eligibility
    blocker, router reasons are hypothetical capacity). Arrivals run in start order, ties by session id, over a
    private temporary ledger:
    each admission reserves its claim until the session's recorded end, so overlapping sessions compete for the same
    windows and an arrival that does not fit is `pending`. Session ids never reach the ledger."""
    _qualified(settings_path)
    table = routing_paths._table(settings_path)
    source = routing_observations.default_source(settings_path)
    rules, harnesses = routing_paths.load_rules(), _harnesses(table)
    settings = dict(routing_settings.resolve({}), revision=1)
    ordered = sorted(rows, key=lambda row: (row["start_ms"], row["id"].encode()))
    labelled, decisions, reasons, eligibility = [label(row) for row in ordered], [], {}, {}
    with tempfile.TemporaryDirectory() as folder:
        private = Path(folder) / "ledger"
        os.mkdir(private, 0o700)
        path = private / "ledger.sqlite3"
        ledger = usage_budget.Ledger(path, usage_budget.initialize(path))
        try:
            open_until = []  # (end_ms, attempt) of admitted sessions still running
            for index, (row, labels) in enumerate(zip(ordered, labelled)):
                for end, attempt in sorted(item for item in open_until if item[0] <= row["start_ms"]):
                    _release(ledger, attempt, end // 1000)
                open_until = [item for item in open_until if item[0] > row["start_ms"]]
                task = profile(labels)
                if task is None:
                    decisions.append(dict(status="no_profile"))
                    continue
                ranking = routing.rank(task, table, None)
                attempt = "arrival-%d" % index
                assignment = dict(attempt_id=attempt, task=attempt, role=task["role"], work_kind=task["work_kind"],
                                  complexity_band=task["complexity_band"], scope=SCOPE)
                cut = routing_observations.shortlist(None, assignment, settings_path=settings_path, table=table,
                                                     strategy_ids=ranking["strategy_ids"],
                                                     harnesses=harnesses[row["kind"]], source=source)
                for item in cut["excluded"]:
                    eligibility[item["reason"]] = eligibility.get(item["reason"], 0) + 1
                observation, unknown = _observation(cut, rules, row["start_ms"] // 1000)
                if unknown:
                    eligibility["window_length_unknown"] = eligibility.get("window_length_unknown", 0) + unknown
                if not observation["routes"]:  # a real eligibility blocker, not hypothetical capacity
                    reasons["no_eligible_route"] = reasons.get("no_eligible_route", 0) + 1
                    decisions.append(dict(status="pending", reason_code="no_eligible_route"))
                    continue
                outcome = routing.admit(assignment, ranking, settings, observation, ledger=ledger)
                if outcome.get("status") != "admitted":
                    reasons[outcome.get("reason_code")] = reasons.get(outcome.get("reason_code"), 0) + 1
                    decisions.append(dict(status="pending", reason_code=outcome.get("reason_code")))
                    continue
                route = next(item for item in observation["routes"] if item["route"] == outcome["route"])
                decisions.append(dict(status="admitted", model=route["model"], effort=route["effort"],
                                      funding_class="included_subscription"
                                      if route["billing_class"] == routing_paths.SUBSCRIPTION else "dollar"))
                open_until.append((row["end_ms"], attempt))
        finally:
            ledger.close()
    report = summarize(labelled, decisions)
    report.update(pending_reasons=reasons, eligibility=eligibility, inputs=dict(
        table_version=table.get("version"), table_digest=_digest(table), routes_version=source.routes_version(table),
        settings="registered_defaults", settings_digest=_digest(settings), observation="fixed_synthetic_fresh_zero"))
    return report


def run(stores, *, settings_path=None, exclusions=None, now_ms=None):
    """Gate, read and report. Refuses with the first unmet gate; see the module docstring for the order. `now_ms`
    defaults to the current time; it only places the one-hour still-running cut."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    blocked = _basis(exclusions)
    if blocked is None:
        raise Refused("exclusion_basis_missing: an approved caller must supply qualification and pilot session ids "
                      "with their sources and check method (shape-checked here, not verified; Edit 9)")
    _registered(stores)  # every registration and completeness gate, before any store is opened
    found = gather(stores, blocked, now_ms=now_ms)
    report = compose(found["eligible"], settings_path=settings_path)
    report.update(sessions_found=found["sessions_found"], excluded=found["excluded"])
    report["inputs"]["store_schemas"] = found["schemas"]
    return report


def _registered(stores):
    """Refuse unregistered sources before any read, then require both retained mappings (Edit 10).
    The excluded `opus` project is neither required nor silently dropped if supplied."""
    for store in stores:
        if store.get("name") not in MAPPED_STORES or MAPPED_STORES[store["name"]] != store.get("kind"):
            raise Refused("store_unregistered: a store outside the registered mapping is never read or dropped")
    names = {store.get("name") for store in stores}
    missing = [name for name in MAPPED_STORES if name not in names]
    if missing:
        raise Refused("store_missing: %s; the registered sample would be incomplete" % ", ".join(missing))


def gather(stores, blocked, *, now_ms):
    """The authorized read `run` performs after `_registered`: `collect`, then refuse while any eligible session id
    is a qualification or pilot session id (Edit 6)."""
    found = collect(stores, now_ms=now_ms)
    if any(row["id"] in blocked for row in found["eligible"]):
        raise Refused("qualification_session_found: an eligible session is a qualification or pilot session")
    return found
