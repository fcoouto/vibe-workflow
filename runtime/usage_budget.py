"""Usage Routing accounting with transaction-bound coordinator admission."""
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import uuid

MAX_INTEGER = 2**53 - 1  # also the bound for every micro-unit amount (about 9e9 USD)
# Two record limits (compact JSON characters). A write that adds exposure or grows a table (`grows=True`:
# begin, top_up, reserve_generation, admit, configure, enable_organization) commits only while the record stays
# within SOFT_RECORD; past it those are refused with `ledger_capacity_reached`. Every other write (dispatch,
# dispatch_hold, complete, uncertain, reject_unsent, close_run, mark_*, close_attempt, require_authority,
# convert_hold, observe, disable_organization, release_native) commits up to MAX_RECORD, so a full ledger can always settle and
# never leaves a call in `dispatching`. MAX_RECORD is also the read limit. It covers the computed worst case
# (tests/test_routing_funding.py CapacityTests: every table and entry at its bound, 256-character ASCII
# identifiers, every native task finished, every call with a maximal `incomplete` record and one `over_bound` marker
# per run): 10,698,684, 89.16% with S3.6a's settlement fields (352 per hold) and a legacy block (10.53M, 87.8% after
# S3.5's binding allowance). The margin to the 90% test ceiling is thin: avoid further per-hold or per-call fields. S3.6a's event and root tables
# are separate rows with their own bounds (MAX_EVENTS, MAX_ROOTS), outside this record.
SOFT_RECORD = 6_000_000
MAX_RECORD = 12_000_000
FRESH_SECONDS = 900
STATE_VERSION = 4  # 4: S3.6a settled charges; older code must refuse a ledger that carries them
MAX_RUNS = 1000
MAX_ADMISSIONS = 256
MAX_HOLDS = 256  # open holds are bounded by the concurrency caps; released ones are pruned at the bound
MAX_SEEN = 1000  # recent attempt ids kept for reuse rejection; older ids are the caller's responsibility
# Per-entry bounds (compact JSON characters) so the worst case provably fits; see CapacityTests.
# A larger admission is refused with `record_too_large` and writes nothing. An admission carries its workflow
# binding (S3.5, runtime/routing_gates.py), bounded by MAX_BINDING_BYTES on top of the S3.4 record allowance.
MAX_BINDING_BYTES = 2560
MAX_ADMISSION_BYTES = 4096 + MAX_BINDING_BYTES
MAX_HOLD_BYTES = 2048
# Fixed-size fields `dispatch_hold` adds to an admitted hold (two derived ids, an authority hash and a time); the
# worst-case capacity test counts every hold at both bounds.
DISPATCH_HOLD_BYTES = 256
MAX_CLAIMS = 9  # one `org:` claim plus at most eight window claims per hold
# Uncertainty evidence kept on a call; a larger record is replaced by {'omitted': 'too_large'} so recording
# uncertainty never fails. `finished_native` needs no own bound: it holds only keys of `native`, which has at
# most one entry per run (MAX_RUNS).
MAX_INCOMPLETE_BYTES = 1024
# Organizations are optional and user-defined; none exists until the user enables one. `personal` is the
# built-in id for the user's own account; named organizations (a team or workspace) sit beside it. Enabling is
# `enable_organization` (the `configure` path), never a side effect of routing. An organization carries no key:
# credential bindings are opaque references checked per route, so an organization can be enabled before its
# key is ready.
PERSONAL = 'personal'
# Every claim has a typed funding key: `org:<organization>` in micro-USD (one shared total per organization,
# whatever pool, key or gateway reaches it) or `win:<pool>/<window>` in a subscription window's own units.
ORG = 'org:'
WIN = 'win:'
MAX_KEY = len(WIN) + 2 * 256 + 1
HOLD_OUTCOMES = ('not_dispatched', 'complete', 'uncertain')
BASIS_POINTS = 10000
DEFAULT_ALLOWANCE_BP = 9800
DEFAULT_MARGIN_BP = 2000
DEFAULT_CAPS = dict(total=4, per_account=2)
POLICY_FIELDS = 'cycle start end expiry stop_at ceiling amount grant_amount pilot_limit task_limit'
# S3.6a accounting. Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention
# Settled charges stay in R (like S1 run costs). Retiring one needs evidence that the observed balance reflects it
# (spec 8): an observation carrying a provider usage watermark. No observation shape carries one yet (`observe`,
# usage_routing's `observe`, usage_admission.Grant: balance, observed_at, source only), so none is retired and a
# charge counts twice against a balance that already reflects it: headroom errs low (S3.6b-2 deferral). Only the
# current cycle's entry feeds a cycle cap, so older cycle entries fold into `total` at the bound; per-task charges
# feed task limits and are evicted oldest first, never while their task holds an open claim (MAX_HOLDS < bound).
MAX_SETTLED_CYCLES = 16
MAX_SETTLED_TASKS = 512
# `settlement` a closed dollar hold carries: five integers, a source label or an operator `resolution` (basis,
# receipt count, 32-hex receipt digest) and an overrun state. Every field at its bound, counting both the source
# label and a resolution (never stored together), is 324 characters.
SETTLEMENT_HOLD_BYTES = 352
SUMMARY_IDS = 16  # ids `status` lists per block reason (plus `more`)
COST_SOURCES = ('host_message_totals', 'provider_receipt', 'estimate')
TOKEN_FIELDS = ('input', 'output', 'reasoning', 'cache_read', 'cache_write')
# Where an attempt's cost belongs in its root task. `rescue` is a reviewer's rework, kept apart from review.
STAGES = ('coordinator', 'profiling', 'routing', 'worker', 'review', 'rescue', 'repair', 'check', 'escalation',
          'auxiliary')
ROLE_STAGES = dict(implementer='worker', worker='worker', reviewer='review', review='review', repair='repair',
                   check='check', researcher='auxiliary')
# Lifecycle events live in their own table of the ledger file, written in the ledger transaction. A body holds
# ids, labels, integer totals and the outcome; never a prompt, body, transcript or credential. Worst case in
# characters: two 256-character ids, eight 517-character unit keys in the usage and in the outcome, nine claim
# keys released, fixed fields: about 15K, so 16K bounds it. Rows: MAX_EVENTS, oldest unneeded first.
MAX_EVENT_ID = 128
MAX_EVENTS = 4096
MAX_EVENT_BYTES = 16384
EVENT_BODY_FIELDS = ('attempt_id', 'kind', 'stage', 'request_kind', 'funding', 'at', 'recorded_at', 'root',
                     'outcome', 'usage')
# Per-root accounting aggregates (one row per root task): attempts keyed by a 16-hex digest, the receipts already
# counted, observed units. Worst case: 1024 attempts * 31 + 256 receipts * 131 + 16 units * 535 + ten stages
# * 260 + a 256-character root, about 79K characters; 96K bounds it.
MAX_ROOTS = 1024
MAX_ROOT_ATTEMPTS = 1024
MAX_USAGE_RECEIPTS = 256
MAX_UNIT_KEYS = 16
MAX_ROOT_BYTES = 98304
# Retention defaults (spec 11), in seconds. Authority state (open and uncertain holds, active cycle caps, settled
# charges, needed event ids, seen attempt ids, runs) is never removed by age.
DAY = 86400
RETENTION = dict(snapshot=DAY, diagnostic=14 * DAY, feature=30 * DAY, aggregate=60 * DAY)


class BudgetError(ValueError):
    """A bounded local operation cannot proceed safely."""


def require(condition, reason):
    if not condition:
        raise BudgetError(reason)


def integer(value):
    require(type(value) is int and 0 <= value <= MAX_INTEGER, 'invalid_integer')
    return value


def name(value):
    require(type(value) is str and 0 < len(value) <= 256 and value.isprintable(), 'invalid_identity')
    return value


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected.split()), 'invalid_fields')


def private(path, directory=False):
    info = path.lstat()
    require(not path.is_symlink() and path.resolve() == path and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0
            and (stat.S_ISDIR(info.st_mode) if directory else
                 stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'unsafe_ledger_path')
    return info


def initialize(path):
    """Explicitly create a private local ledger; never replace an existing one."""
    path = Path(path).absolute()
    path = path.parent.resolve() / path.name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(path.parent, True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise BudgetError('ledger_exists') from error
    os.close(fd)
    identity = uuid.uuid4().hex
    state = dict(version=STATE_VERSION, identity=identity, organizations={}, runs={}, native={}, finished_native=[],
                 holds={}, admissions=[], seen_attempts=[])
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        db.execute('INSERT INTO ledger VALUES (1, ?)', (json.dumps(state),))
        _create_tables(db)
        db.commit()
    return identity


def _create_tables(db):
    """S3.6a tables; created by `initialize` and on the first accounting write of an older ledger."""
    db.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, '
               'recorded_at INTEGER NOT NULL, attempt_id TEXT NOT NULL, effect INTEGER NOT NULL, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS roots (root TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, '
               'body TEXT NOT NULL)')


def _compact(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'), ensure_ascii=False)


def _digest16(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def root_key(locator=None, task=None):
    """The accounting key of a root task: a digest of the bound job's full (client, cwd, session, task) locator,
    or, for an attempt with no workflow job, of its task id alone (settled without a workflow locator)."""
    parts = ['bound', locator['client'], locator['cwd'], locator['session_id'], locator['task_id']] \
        if locator is not None else ['unbound', task]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()


def task_key(organization, task):
    """The `settled_tasks` key of one organization's task: a digest, so the table stays small."""
    return hashlib.sha256(json.dumps([organization, task]).encode()).hexdigest()


class Ledger:
    """Identity-bound accounting. Only the trusted coordinator can supply a live gate."""
    def __init__(self, path, identity, *, timeout=5):
        """`timeout` is the SQLite busy wait in seconds; `busy_timeout` changes it on an open ledger."""
        self.path = Path(path).absolute()
        self.path = self.path.parent.resolve() / self.path.name
        self.identity = name(identity)
        private(self.path.parent, True)
        try:
            info = private(self.path)
        except FileNotFoundError as error:
            raise BudgetError('manual_recovery_required') from error
        self.file_identity = (info.st_dev, info.st_ino)
        self.db = sqlite3.connect(f'{self.path.as_uri()}?mode=rw', uri=True, timeout=timeout,
                                  isolation_level=None)
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.snapshot()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.db.close()

    def busy_timeout(self, seconds):
        self.db.execute('PRAGMA busy_timeout=%d' % int(seconds * 1000))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _check(self):
        try:
            info = private(self.path)
            private(self.path.parent, True)
        except FileNotFoundError as error:
            raise BudgetError('manual_recovery_required') from error
        require((info.st_dev, info.st_ino) == self.file_identity, 'ledger_replaced')

    @contextmanager
    def transaction(self, write=True, grows=False):
        self._check()
        self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            row = self.db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
            require(row is not None and len(row[0]) <= MAX_RECORD, 'invalid_ledger')
            data = json.loads(row[0])
            original = json.loads(row[0]) if write else None
            require(data['version'] in (1, 2, 3, STATE_VERSION) and data['identity'] == self.identity,
                    'ledger_identity_changed')
            self._migrate(data)
            yield data
            self._check()
            if write and data != original:
                raw = json.dumps(data, allow_nan=False, separators=(',', ':'))
                require(len(raw) <= (SOFT_RECORD if grows else MAX_RECORD), 'ledger_capacity_reached')
                self.db.execute('UPDATE ledger SET body=? WHERE id=1', (raw,))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    @staticmethod
    def _migrate(data):
        """Upgrade older state in place, version by version.

        1 -> 2 drops the global slot and the 20% floor: open runs keep their exposure through their own
        calls, and the old floor seeds the issued allowance (a floor was one fifth of the first balance).
        2 -> 3 adds the multi-pool `holds` and the bounded `admissions` records; both start empty, so
        every in-flight run claim is preserved untouched. Version 3 never shipped with its first hold shape, so
        written v3 ledgers are repaired in place: missing tables are added and a `vector` hold becomes a
        window-only hold (it named no organization, so it carries no dollar amount).
        3 -> 4 (S3.6a) changes no claim, hold or admission: it marks the ledger as one whose R may include settled
        charges (`organizations.*.settled`, `settled_tasks`), so code that would ignore them refuses it
        (`ledger_identity_changed`) instead of resetting caps and credit. A stored `blocked: true` becomes
        `legacy_block` (the block is derived from now on); every unresolved S1 call of such an organization is
        marked `over_bound` at its maximum (v3 recorded no marker, so an over-bound call cannot be told from an
        uncertain one: fail closed); every hold gets its accounting root. The event and root tables are created
        idempotently on the first accounting write.
        """
        if data['version'] == 1:
            data.pop('slot', None)
            for org in data['organizations'].values():
                floor = org.pop('floor', None)
                if org.get('issued') is None:
                    org['issued'] = floor * 5 if floor is not None else None
            data['version'] = 2
        if data['version'] == 2:
            data['version'] = 3
        upgraded = data['version'] == 3
        if upgraded:
            data['version'] = 4
        data.setdefault('holds', {})
        data.setdefault('admissions', [])
        data.setdefault('seen_attempts', [])
        for hold in data['holds'].values():
            hold.setdefault('state', 'admitted')
            if 'windows' not in hold:
                vector = hold.pop('vector', {})
                hold.update(organization=None, amount=0, cycle=None, windows={WIN + k: v for k, v in vector.items()})
        if upgraded:  # 3 -> 4, once per migrated ledger (the upgrade is persisted by its first write)
            for organization, org in data['organizations'].items():
                if not org.get('blocked'):
                    continue
                # v3's run path left an over-bound call `unresolved` with no marker; it cannot be told apart from an
                # uncertain one, so every unresolved call of a blocked organization is treated as over-bound at its
                # maximum: a later lower report can never settle it (case C) and the books keep the maximum.
                for run in data['runs'].values():
                    if run['organization'] == organization:
                        for call in run['calls'].values():
                            if call['state'] == 'unresolved':
                                call.setdefault('over_bound', call['maximum'])
            # Every hold admitted before v4 gets its accounting root, while its admission record still exists.
            for attempt, hold in data['holds'].items():
                if 'root' not in hold and 'task' in hold:
                    hold.update(zip(('root', 'stage'), Ledger._record_root(Ledger.admission(data, attempt) or {},
                                                                           hold['task'])))
        for org in data['organizations'].values():
            # v4 derives the admission block (`block_reasons`) and stores no flag. A stored `blocked: true` (written
            # by v3, or by S1's run path before v4) has no recorded source, so it becomes the sticky `legacy_block`.
            if org.pop('blocked', False):
                org['legacy_block'] = True
            org.pop('overrun_block', None)

    def snapshot(self):
        with self.transaction(False) as data:
            return data

    def configure(self, organization, policy, expected_revision):
        with self.transaction(grows=True) as data:
            return self._configure(data, organization, policy, expected_revision)

    def enable_organization(self, organization, policy, expected_revision):
        """Explicit user action: create or re-enable an organization (for example `PERSONAL`) with its policy."""
        with self.transaction(grows=True) as data:
            revision = self._configure(data, organization, policy, expected_revision)
            data['organizations'][organization]['disabled'] = False
            return revision

    def disable_organization(self, organization):
        """Stop new admissions, conversions, top-ups and sends; every open claim stays counted until settled."""
        with self.transaction() as data:
            require(name(organization) in data['organizations'], 'organization_unconfigured')
            data['organizations'][organization]['disabled'] = True

    @staticmethod
    def _configure(data, organization, policy, expected_revision):
        name(organization); integer(expected_revision)
        optional = [key for key in ('allowance_bp', 'allowance') if isinstance(policy, dict) and key in policy]
        fields(policy, POLICY_FIELDS + ' ' + ' '.join(optional))
        require(len(optional) <= 1, 'invalid_allowance')
        if optional == ['allowance_bp']:
            require(type(policy['allowance_bp']) is int and 0 <= policy['allowance_bp'] <= BASIS_POINTS,
                    'invalid_allowance')
        elif optional:
            require(policy['allowance'] == 'no-limit', 'invalid_allowance')
        name(policy['cycle'])
        for key in ('start', 'end', 'expiry', 'stop_at', 'grant_amount'):
            integer(policy[key])
        require(policy['start'] < policy['stop_at'] < min(policy['end'], policy['expiry']), 'invalid_expiry_window')
        require(policy['ceiling'] in ('none', 'custom', 'subscription_benefit'), 'invalid_ceiling')
        if policy['ceiling'] == 'custom':
            require(integer(policy['amount']) > 0, 'invalid_ceiling')
        else:
            require(policy['amount'] is None, 'unexpected_amount')
        for limit in (policy['pilot_limit'], policy['task_limit']):
            require(limit is None or integer(limit) > 0, 'invalid_limits')
        if policy['ceiling'] == 'subscription_benefit':
            require(policy['grant_amount'] > 0, 'missing_grant_coverage')
        org = data['organizations'].get(organization)
        require((org['revision'] if org else 0) == expected_revision, 'policy_revision_changed')
        if org:
            old = org['policy']
            if old == policy:
                return org['revision']
            if old['cycle'] == policy['cycle']:
                require((old['start'], old['end']) == (policy['start'], policy['end']), 'cycle_changed')
            else:
                require(policy['start'] >= old['end'], 'overlapping_cycle')
            # A new cycle does not create credits or clear retained exposure.
        else:
            org = dict(revision=0, balance=None, observed_at=None, issued=None)
            data['organizations'][organization] = org
        org['revision'] += 1
        org['policy'] = policy.copy()
        return org['revision']

    def observe(self, organization, balance, observed_at, *, issued=None, lots=None):
        """Record a balance. `issued` is an observed issuance (G); `lots` itemize eligible credit."""
        integer(balance); integer(observed_at)
        if issued is not None:
            integer(issued)
        with self.transaction() as data:
            org = data['organizations'][organization]
            require(org['observed_at'] is None or observed_at > org['observed_at'], 'observation_not_newer')
            lots = self._lots(org, lots)
            self._note_balance(org, balance, issued)
            if lots is not None:
                org['lots'] = lots
            org['observed_at'] = observed_at

    @staticmethod
    def _note_balance(org, balance, issued=None):
        """Lower the usable balance; only an observed issuance (or the first balance) sets G."""
        org['balance'] = min(org['balance'], balance) if org['balance'] is not None else balance
        if issued is not None:
            org['issued'] = issued
        elif org.get('issued') is None:
            org['issued'] = balance

    @staticmethod
    def _lots(org, lots):
        if lots is None:
            return None
        require(type(lots) is list and len(lots) <= 64, 'invalid_lots')
        known = {lot['id']: lot for lot in org.get('lots', [])}
        result = []
        for lot in lots:
            fields(lot, 'id balance expiry verified')
            name(lot['id']); integer(lot['balance']); integer(lot['expiry'])
            require(type(lot['verified']) is bool, 'invalid_lots')
            old = known.get(lot['id'])
            balance = min(old['balance'], lot['balance']) if old else lot['balance']
            result.append(dict(lot, balance=balance))
        require(len({lot['id'] for lot in result}) == len(result), 'invalid_lots')
        return result

    @staticmethod
    def _cost(call):
        return call['cost'] if call['cost'] is not None else call['maximum']

    @classmethod
    def _total(cls, data, organization, task=None, cycle=None):
        """R: every run call, every open hold on this organization (uncertain holds included) and every charge
        settled from a hold (S3.6a): by task, by cycle, or in total."""
        runs = sum(cls._cost(call) for run in data['runs'].values()
                   if run['organization'] == organization and (task is None or run['task'] == task)
                   for call in run['calls'].values() if cycle is None or call['cycle'] == cycle)
        held = sum(hold['amount'] for hold in data['holds'].values()
                   if not hold['closed'] and hold['organization'] == organization
                   and (task is None or hold['task'] == task) and (cycle is None or hold['cycle'] == cycle))
        settled = (data['organizations'].get(organization) or {}).get('settled')
        if settled is None:
            charged = 0
        elif task is not None:
            charged = data.get('settled_tasks', {}).get(task_key(organization, task), 0)
        elif cycle is not None:
            charged = settled['cycles'].get(cycle, 0)
        else:
            charged = settled['total']
        return runs + held + charged

    @staticmethod
    def _protected(org):
        """Share of the issued allowance G that stays unspent: ceil((10000 - bp) * G / 10000)."""
        policy = org['policy']
        if policy.get('allowance') == 'no-limit':
            return 0
        bp = policy.get('allowance_bp', DEFAULT_ALLOWANCE_BP)
        return -(-((BASIS_POINTS - bp) * (org.get('issued') or 0)) // BASIS_POINTS)

    @staticmethod
    def _available(org, now=None):
        """B: the reconciled balance, or in no-limit mode the verified eligible lots only."""
        if org['policy'].get('allowance') == 'no-limit':
            return sum(lot['balance'] for lot in org.get('lots', [])
                       if lot['verified'] and (now is None or now < lot['expiry']))
        return org['balance']

    @classmethod
    def _credit_raw(cls, data, organization, now=None):
        org = data['organizations'][organization]
        require(org['balance'] is not None, 'balance_unverified')
        return cls._available(org, now) - cls._protected(org) - cls._total(data, organization)

    @classmethod
    def _credit(cls, data, organization, now=None):
        """Credit headroom; zero while an overrun excess on this organization awaits the operator (S3.6a): the
        unconfirmed excess never enters R, so it lowers headroom by at most the headroom that remains."""
        if cls.pending_overruns(data, organization):
            return 0
        return max(0, cls._credit_raw(data, organization, now))

    @staticmethod
    def block_reasons(data, organization):
        """Why `organization` is admission-blocked (`manual_recovery_required`), DERIVED from the records that cause
        it, never stored: `overrun` (closed holds whose excess is unconfirmed; `resolve_overrun`), `over_bound` (S1
        run calls whose reported cost exceeded their maximum; `resolve_block`) and `legacy` (a block migrated from
        before v4; `resolve_block` with the current `legacy_marker`). Clearing one source can never clear another."""
        reasons = set()
        if Ledger.pending_overruns(data, organization):
            reasons.add('overrun')
        if Ledger.over_bound_runs(data, organization):
            reasons.add('over_bound')
        if (data['organizations'].get(organization) or {}).get('legacy_block'):
            reasons.add('legacy')
        return reasons

    @staticmethod
    def unresolved_items(data, organization):
        """What the operator reviews before clearing a legacy block (S3.6b-2), as sorted rows: the organization's open
        holds, its closed holds with an unconfirmed overrun, and its open S1 run calls that are reserved, in flight,
        unresolved or over-bound."""
        items = []
        for attempt, hold in data['holds'].items():
            if hold.get('organization') != organization:
                continue
            if not hold['closed']:
                items.append(['hold', attempt, hold['state'], hold['amount']])
            elif Ledger._unconfirmed(hold):
                items.append(['overrun', attempt, hold['settlement']['overrun']])
        for run_id, run in data['runs'].items():
            if run['organization'] == organization and not run['closed']:
                for phase, call in run['calls'].items():
                    if call['state'] in ('reserved', 'dispatching', 'unresolved') or 'over_bound' in call:
                        items.append(['run', run_id, phase, call['state'], call['maximum'], call.get('over_bound')])
        return sorted(items, key=json.dumps)

    @staticmethod
    def legacy_marker(data, organization):
        """The handshake value `resolve_block` needs to clear a legacy block: a digest of the ledger identity, the
        organization, its policy revision and `unresolved_items`, shown only by `status`. Any change to what the
        operator reviewed makes it stale, so a block is never cleared blind or against a listing that moved."""
        org = data['organizations'][organization]
        payload = [data['identity'], organization, org['revision'], bool(org.get('legacy_block')),
                   Ledger.unresolved_items(data, organization)]
        raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode()
        return 'lb_' + hashlib.sha256(raw).hexdigest()[:32]

    @staticmethod
    def over_bound_runs(data, organization):
        return sorted(run_id for run_id, run in data['runs'].items() if run['organization'] == organization
                      and any('over_bound' in call for call in run['calls'].values()))

    @staticmethod
    def overrun_backlog(data):
        """The hold table is full and at least one pending overrun (never pruned) occupies it, even when open holds
        fill the rest. `admit` then refuses every new admission with `overrun_backlog`: any organization's and
        subscription admissions alike, until an operator resolves overruns or holds close."""
        return len(data['holds']) >= MAX_HOLDS and any(Ledger._unconfirmed(h) for h in data['holds'].values())

    @staticmethod
    def _unconfirmed(hold):
        return (hold.get('settlement') or {}).get('overrun_state') == 'unconfirmed'

    @staticmethod
    def pending_overruns(data, organization):
        """Attempts of `organization` whose reported excess over their reservation is still unconfirmed."""
        return sorted(attempt for attempt, hold in data['holds'].items() if hold.get('organization') == organization
                      and Ledger._unconfirmed(hold))

    @classmethod
    def _headroom(cls, data, organization, task=None, now=None):
        org = data['organizations'][organization]; policy = org['policy']
        limits = [cls._credit(data, organization, now)]
        if policy['pilot_limit'] is not None:
            limits.append(policy['pilot_limit'] - cls._total(data, organization))
        if task is not None and policy['task_limit'] is not None:
            limits.append(policy['task_limit'] - cls._total(data, organization, task))
        cap = policy['amount'] if policy['ceiling'] == 'custom' else policy['grant_amount']
        if policy['ceiling'] != 'none':
            limits.append(cap - cls._total(data, organization, cycle=policy['cycle']))
        return max(0, min(limits))

    def headroom(self, organization):
        with self.transaction(False) as data:
            return self._headroom(data, organization)

    def credit_headroom(self, organization, now=None):
        """max(0, B - protected - R - U) in micro-units; R is every hold and retained charge."""
        with self.transaction(False) as data:
            return self._credit(data, organization, now)

    @classmethod
    def _admission_checks(cls, data, organization, revision, now):
        """Every admission condition except the final headroom comparison; shared by `_admit` and `admit`."""
        integer(revision); integer(now)
        require(organization in data['organizations'], 'organization_unconfigured')
        org = data['organizations'][organization]; policy = org['policy']
        require(not org.get('disabled', False), 'organization_disabled')
        require(not cls.block_reasons(data, organization), 'manual_recovery_required')
        require(org['revision'] == revision, 'policy_revision_changed')
        require(org['observed_at'] is not None and 0 <= now - org['observed_at'] <= FRESH_SECONDS,
                'balance_stale_or_missing')
        require(policy['start'] <= now < policy['stop_at'], 'expiry_or_cycle_boundary')

    @classmethod
    def _admit(cls, data, organization, task, maximum, revision, now):
        integer(maximum)
        cls._admission_checks(data, organization, revision, now)
        require(maximum <= cls._headroom(data, organization, task, now), 'insufficient_headroom')

    @classmethod
    def org_headroom(cls, data, organization, task, revision, now):
        """Headroom for an organization-funded pool, after the same gates `begin` applies.

        Raises BudgetError for an unconfigured, disabled, blocked, stale, expired or revision-mismatched organization.
        """
        cls._admission_checks(data, organization, revision, now)
        return cls._headroom(data, organization, task, now)

    @classmethod
    def _live_admit(cls, data, organization, added, now, gate, run=None):
        if run is not None:
            require(('live_admission' in run) == (gate is not None), 'live_admission_mismatch')
        if gate is None:
            return None
        require(callable(gate), 'invalid_live_gate')
        integer(added); integer(now)
        # The gate may stage verified credit in this transaction before _admit reads it.
        admission = gate(data, now)
        fields(admission, 'pin maximum')
        name(admission['pin'])
        require(integer(admission['maximum']) > 0, 'invalid_live_cap')
        if run is not None:
            require(admission == run['live_admission'], 'live_admission_mismatch')
        require(cls._total(data, organization) + added <= admission['maximum'], 'live_cap_exceeded')
        return admission.copy()

    @staticmethod
    def _native(data, evidence, now, reserve=True):
        fields(evidence, 'account task windows')
        account, task = name(evidence['account']), name(evidence['task'])
        key = json.dumps([account, task])
        require(key not in data['finished_native'], 'native_task_closed')
        if reserve and key not in data['native']:
            data['native'][key] = dict(account=account, task=task, latest_at=now)
        return key

    @staticmethod
    def _reserve(estimate, next_request_bound, margin_bp):
        """max(ceil(estimate * (10000 + margin) / 10000), next_request_bound); a bare bound is held as given."""
        integer(estimate)
        if next_request_bound is None and margin_bp is None:
            return estimate
        margin = DEFAULT_MARGIN_BP if margin_bp is None else margin_bp
        require(type(margin) is int and 0 <= margin <= BASIS_POINTS, 'invalid_margin')
        bound = integer(next_request_bound or 0)
        return max(-(-(estimate * (BASIS_POINTS + margin)) // BASIS_POINTS), bound)

    @staticmethod
    def _caps(data):
        caps = data.get('caps', DEFAULT_CAPS)
        return caps['total'], caps['per_account']

    def set_caps(self, total, per_account):
        """Persist the concurrency caps (spec 7.4); defaults are 4 total and 2 per account."""
        require(integer(total) > 0 and integer(per_account) > 0, 'invalid_caps')
        with self.transaction() as data:
            data['caps'] = dict(total=total, per_account=per_account)

    @staticmethod
    def open_attempts(data):
        """One concurrency place per open attempt id, with its provider account.

        A run converted from a hold keeps the hold's account and id, so the attempt is counted once whether its
        hold, its run or both are open. A run begun directly counts under its organization."""
        attempts = {attempt: hold['account'] for attempt, hold in data['holds'].items() if not hold['closed']}
        for attempt, run in data['runs'].items():
            if not run['closed']:
                attempts[attempt] = run.get('account', run['organization'])
        return attempts

    @classmethod
    def open_accounts(cls, data):
        return list(cls.open_attempts(data).values())

    @classmethod
    def _check_caps(cls, data, account):
        total, per_account = cls._caps(data)
        accounts = cls.open_accounts(data)
        require(len(accounts) < total and accounts.count(account) < per_account, 'concurrency_cap')

    @staticmethod
    def _active_call(run):
        """The call that still holds an envelope: reserved or in flight."""
        active = [call for call in run['calls'].values() if call['state'] in ('reserved', 'dispatching')]
        return active[-1] if active else None

    @classmethod
    def _transfer(cls, data, organization, transfer_from, amount_limit):
        """Move part of a parent's review provision to a new child claim; total holds are unchanged."""
        require(type(transfer_from) is tuple and len(transfer_from) == 2, 'invalid_transfer')
        parent_id, amount = transfer_from
        name(parent_id); integer(amount)
        parent = data['runs'].get(parent_id)
        require(parent is not None and not parent['closed'] and parent['organization'] == organization,
                'transfer_forbidden')
        call = cls._active_call(parent)
        require(call is not None, 'transfer_forbidden')
        require(amount <= call.get('provision', 0) and amount <= amount_limit, 'provision_exceeded')
        call['maximum'] -= amount
        call['provision'] = call.get('provision', 0) - amount
        return amount

    def begin(self, organization, task, run_id, digest, estimate, revision, now, native, *,
              next_request_bound=None, margin_bp=None, provision=0, transfer_from=None,
              consent=None, live_gate=None):
        """Admit one task's own claim. Reserve max(padded estimate, next request bound).

        Without `next_request_bound` and `margin_bp` the estimate is already a hard bound and is held unpadded.
        `provision` marks part of the hold as review budget a child may take over via `transfer_from`.
        """
        for value in (organization, task, run_id, digest): name(value)
        maximum = self._reserve(estimate, next_request_bound, margin_bp)
        require(integer(provision) <= maximum, 'provision_exceeded')
        with self.transaction(grows=True) as data:
            return self._begin(data, organization, task, run_id, digest, maximum, revision, now, native,
                               provision, transfer_from, consent, live_gate)

    def _begin(self, data, organization, task, run_id, digest, maximum, revision, now, native, provision,
               transfer_from, consent, live_gate):
        if run_id in data['runs']:
            run = data['runs'][run_id]
            require(live_gate is None and 'live_admission' not in run, 'live_run_id_already_used')
            require((run['organization'], run['task'], run['digest']) == (organization, task, digest), 'run_identity_changed')
            return run
        require(run_id not in data['holds'], 'attempt_id_in_use')  # an admitted attempt becomes a run only by conversion
        require(len(data['runs']) < MAX_RUNS, 'ledger_capacity_reached')
        self._check_caps(data, organization)
        moved = 0
        if transfer_from is not None:
            moved = self._transfer(data, organization, transfer_from, maximum)
        admission = self._live_admit(data, organization, maximum, now, live_gate)
        self._admit(data, organization, task, maximum, revision, now)
        run = self._open_run(data, run_id, organization, task, digest, maximum, revision, now, native, provision,
                             consent, admission)
        if moved:
            run['transferred_from'] = dict(run=transfer_from[0], amount=moved)
        return run

    def _open_run(self, data, run_id, organization, task, digest, maximum, revision, now, native, provision,
                  consent, admission, account=None):
        require(native.get('task') == task, 'native_task_mismatch')
        key = self._native(data, native, now)
        data['native'][key]['latest_at'] = now
        if consent is not None:
            self._consent(data, consent)
        run = dict(consent=consent, organization=organization, task=task, digest=digest, revision=revision,
                   native=key, closed=False, calls={})
        if account is not None:
            run['account'] = account
        if admission is not None:
            run['live_admission'] = admission
        data['runs'][run_id] = run
        run['calls']['count'] = self._call(data, run, maximum, digest)
        if provision:
            run['calls']['count']['provision'] = provision
        return run

    def convert_hold(self, attempt_id, organization, digest, estimate, revision, now, native, *,
                     next_request_bound=None, margin_bp=None, provision=0, consent=None, live_gate=None):
        """Turn an admitted hold's dollar amount into the attempt's run claim in ONE transaction.

        S3 must use this (not `begin`) for a funded attempt, and only after `dispatch_hold`: the hold must be
        `dispatched` with an issued authority (never admitted, bare-dispatched or uncertain), and it keeps its
        dispatch record. The organization, task and provider account
        come from the hold; another `organization` raises `hold_organization_mismatch`. The run's own bound
        (`estimate` padded as in `begin`) must fit the hold, else `hold_exceeded`: a larger need goes through
        an explicit `top_up` and its headroom check. The whole hold amount moves 1:1 into the run's envelope,
        so R and the attempt's single concurrency place are unchanged; window claims stay on the hold. Any
        refusal leaves the ledger unchanged.
        """
        for value in (attempt_id, organization, digest): name(value)
        maximum = self._reserve(estimate, next_request_bound, margin_bp)
        require(integer(provision) <= maximum, 'provision_exceeded')
        with self.transaction() as data:
            hold = data['holds'].get(attempt_id)
            require(hold is not None and not hold['closed'] and hold['state'] == 'dispatched' and 'dispatch' in hold
                    and hold['amount'] > 0 and attempt_id not in data['runs'], 'convert_forbidden')
            require(organization == hold['organization'], 'hold_organization_mismatch')
            require(maximum <= hold['amount'], 'hold_exceeded')
            require(len(data['runs']) < MAX_RUNS, 'ledger_capacity_reached')
            org = data['organizations'][organization]
            require(hold['cycle'] == org['policy']['cycle'], 'hold_cycle_changed')
            self._admission_checks(data, organization, revision, now)
            amount = hold['amount']
            hold['amount'] = 0  # leaves R here and re-enters it as the run's call below: R is unchanged
            admission = self._live_admit(data, organization, amount, now, live_gate)
            run = self._open_run(data, attempt_id, organization, hold['task'], digest, amount, revision, now, native,
                                 provision, consent, admission, account=hold['account'])
            # Window claims stay held (state `converted`) until the run closes; the run now owns the attempt, so
            # close_attempt refuses it and `_close_run` releases it.
            hold.update(state='converted', closed=not hold['windows'])
            return run

    def top_up(self, run_id, added, revision, now, *, live_gate=None):
        """Extend the open envelope atomically, or raise insufficient_headroom and change nothing."""
        integer(added)
        with self.transaction(grows=True) as data:
            run = data['runs'][run_id]
            call = self._active_call(run)
            require(not run['closed'] and call is not None, 'top_up_forbidden')
            require(run['revision'] == revision, 'policy_revision_changed')
            self._live_admit(data, run['organization'], added, now, live_gate, run)
            self._admit(data, run['organization'], run['task'], added, revision, now)
            call['maximum'] += added

    @staticmethod
    def _call(data, run, maximum, digest):
        return dict(maximum=maximum, cost=None, digest=digest, state='reserved',
                    cycle=data['organizations'][run['organization']]['policy']['cycle'])

    def reserve_generation(self, run_id, digest, maximum, revision, now, *, live_gate=None):
        name(digest)
        with self.transaction(grows=True) as data:
            run = data['runs'][run_id]
            require(not run['closed'] and run['calls']['count']['state'] == 'completed', 'count_not_completed')
            require('generation' not in run['calls'], 'attempt_exists')
            require(run['revision'] == revision, 'policy_revision_changed')
            self._live_admit(data, run['organization'], maximum, now, live_gate, run)
            self._admit(data, run['organization'], run['task'], maximum, revision, now)
            run['calls']['generation'] = self._call(data, run, maximum, digest)

    def dispatch(self, run_id, phase, revision, now, *, live_gate=None):
        with self.transaction() as data:
            run = data['runs'][run_id]; call = run['calls'][phase]
            if run.get('consent') is not None:
                self._consent(data, run['consent'])
            require(not run['closed'] and call['state'] == 'reserved', 'dispatch_forbidden')
            require(run['revision'] == revision, 'policy_revision_changed')
            # The call's own reservation has already reduced headroom.
            self._live_admit(data, run['organization'], 0, now, live_gate, run)
            self._admit(data, run['organization'], run['task'], 0, revision, now)
            require(self._headroom(data, run['organization'], run['task']) >= 0, 'insufficient_headroom')
            policy = data['organizations'][run['organization']]['policy']
            require((policy['pilot_limit'] is None or self._total(data, run['organization']) <= policy['pilot_limit'])
                    and (policy['task_limit'] is None
                         or self._total(data, run['organization'], run['task']) <= policy['task_limit']),
                    'limits_changed')
            cap = policy['amount'] if policy['ceiling'] == 'custom' else policy['grant_amount']
            require(policy['ceiling'] == 'none' or self._total(data, run['organization'], cycle=policy['cycle']) <= cap, 'limits_changed')
            require(self._credit_raw(data, run['organization'], now) >= 0, 'credit_changed')
            call['state'] = 'dispatching'
            data['native'][run['native']]['latest_at'] = now

    def complete(self, run_id, phase, actual):
        integer(actual)
        overrun = False
        with self.transaction() as data:
            run = data['runs'][run_id]; call = run['calls'][phase]
            require(call['state'] in ('dispatching', 'unresolved'), 'completion_forbidden')
            # Over-bound is sticky: a later (lower) report never settles the call or unblocks its organization.
            require('over_bound' not in call, 'over_bound_unresolved')
            if actual > call['maximum']:
                call.update(state='unresolved', over_bound=actual); overrun = True  # blocks via block_reasons
            else:
                call.update(cost=actual, state='completed')  # Charges actual; unused padding is released.
                call.pop('provision', None)
                if phase == 'generation':
                    self._close_run(data, run_id)
        require(not overrun, 'cost_exceeds_bound')

    def uncertain(self, run_id, phase, *, incomplete=None):
        with self.transaction() as data:
            call = data['runs'][run_id]['calls'][phase]
            require(call['state'] == 'dispatching', 'uncertainty_forbidden')
            call['state'] = 'unresolved'
            if incomplete is not None:
                try:
                    size = len(json.dumps(incomplete, allow_nan=False, separators=(',', ':')))
                except (TypeError, ValueError, RecursionError):
                    size = MAX_INCOMPLETE_BYTES + 1
                call['incomplete'] = incomplete if size <= MAX_INCOMPLETE_BYTES else dict(omitted='too_large')

    def close_run(self, run_id):
        with self.transaction() as data:
            run = data['runs'][run_id]
            require(not any(c['state'] in ('dispatching', 'unresolved') for c in run['calls'].values()), 'request_may_be_running')
            for call in run['calls'].values():
                if call['state'] == 'reserved': call.update(cost=0, state='rejected-before-send')
            self._close_run(data, run_id)

    @staticmethod
    def held(data):
        """Outstanding hold claims per typed key; closed holds contribute nothing, uncertain ones everything."""
        held = {}
        for hold in data['holds'].values():
            if not hold['closed']:
                claims = dict(hold['windows'])
                if hold['amount']:
                    claims[ORG + hold['organization']] = hold['amount']
                for key, amount in claims.items():
                    held[key] = held.get(key, 0) + amount
        return held

    @staticmethod
    def open_holds(data):
        """Every hold still carrying exposure (admitted, dispatched or uncertain), for recovery to enumerate."""
        return {attempt: hold for attempt, hold in data['holds'].items() if not hold['closed']}

    @staticmethod
    def admission(data, attempt_id):
        for record in data.get('admissions', []):
            if record['attempt_id'] == attempt_id:
                return record
        return None

    def admit(self, attempt_id, decide):
        """Reserve and record one admission in a single short transaction.

        `decide(data)` is pure and runs against the locked state. It returns either a block
        (any dict without `record`) or `{record, vector, account, now?, revision?}`. `vector` maps typed keys
        to integer amounts: at most one `org:<organization>` key (micro-USD) and any `win:<pool>/<window>`
        keys. A dollar claim is re-admitted here through `_admit` (every gate plus headroom, with R including
        all holds) using `now` and the organization `revision`, and the ledger caps are rechecked, so a
        faulty decision can never overdraw. A block, an error or a duplicate attempt writes nothing: a
        duplicate returns its existing record without another hold.
        """
        name(attempt_id)
        with self.transaction(grows=True) as data:
            known = self.admission(data, attempt_id)
            if known is not None:
                return dict(known, duplicate=True)
            require(attempt_id not in data['holds'] and attempt_id not in data['runs']
                    and attempt_id not in data['seen_attempts'], 'attempt_id_in_use')
            outcome = decide(data)
            if 'record' not in outcome:
                return outcome
            record, vector, account = outcome['record'], outcome['vector'], name(outcome['account'])
            require(type(record) is dict and record.get('attempt_id') == attempt_id and type(vector) is dict,
                    'invalid_admission')
            task = name(record['task'])
            require(len(vector) <= MAX_CLAIMS, 'record_too_large')
            for key, amount in vector.items():
                require(type(key) is str and key[:4] in (ORG, WIN) and 4 < len(key) <= MAX_KEY and key.isprintable(),
                        'invalid_admission')
                integer(amount)
            dollars = [key for key in vector if key.startswith(ORG)]
            require(len(dollars) <= 1, 'invalid_admission')
            organization, amount, cycle = None, 0, None
            if dollars:
                organization, amount = name(dollars[0][len(ORG):]), vector[dollars[0]]
                self._admit(data, organization, task, amount, outcome.get('revision'), outcome.get('now'))
                cycle = data['organizations'][organization]['policy']['cycle']
            self._check_caps(data, account)
            # A random nonce per admission keeps a reused attempt id from deriving the same host ids (S3.4).
            record = dict(record, nonce=secrets.token_hex(16))
            if data.get('authority_required'):
                record['authority_required'] = True  # one-way rule this admission keeps for its life
            hold = dict(account=account, task=task, organization=organization, amount=amount, cycle=cycle,
                        windows={k: v for k, v in vector.items() if k.startswith(WIN)}, closed=False, state='admitted')
            # S3.6a: the accounting root and stage are fixed at admission, so pruning the record never moves them.
            hold.update(zip(('root', 'stage'), self._record_root(record, task)))
            for entry, limit in ((record, MAX_ADMISSION_BYTES), (hold, MAX_HOLD_BYTES)):
                try:
                    size = len(json.dumps(entry, allow_nan=False, separators=(',', ':')))
                except (TypeError, ValueError, RecursionError) as error:
                    raise BudgetError('invalid_admission') from error
                require(size <= limit, 'record_too_large')
            self._prune(data)
            require(len(data['holds']) < MAX_HOLDS, 'overrun_backlog' if self.overrun_backlog(data)
                    else 'ledger_capacity_reached')
            data['holds'][attempt_id] = hold
            data['admissions'].append(record)
            data['seen_attempts'].append(attempt_id)
            del data['seen_attempts'][:-MAX_SEEN]
            self._trim_admissions(data)
            return dict(record)

    @staticmethod
    def _trim_admissions(data):
        """Keep MAX_ADMISSIONS records, dropping the oldest records of closed attempts first.

        Open attempts are bounded by MAX_HOLDS <= MAX_ADMISSIONS, so a full table never blocks an admission."""
        records = data['admissions']
        excess = len(records) - MAX_ADMISSIONS
        if excess <= 0:
            return
        open_ids = set(Ledger.open_holds(data))
        order = sorted(range(len(records)), key=lambda i: (records[i]['attempt_id'] in open_ids, i))
        drop = set(order[:excess])
        data['admissions'] = [record for i, record in enumerate(records) if i not in drop]

    @staticmethod
    def _prune(data):
        """Drop the oldest released holds once the table is full; open (incl. uncertain) holds and closed holds with
        an unconfirmed overrun (they await `resolve_overrun`) are never dropped."""
        if len(data['holds']) >= MAX_HOLDS:
            for key in [k for k, hold in data['holds'].items() if hold['closed'] and
                        (hold.get('settlement') or {}).get('overrun_state') != 'unconfirmed'][:MAX_HOLDS // 2]:
                del data['holds'][key]

    def _hold(self, data, attempt_id, states):
        hold = data['holds'].get(attempt_id)
        require(hold is not None, 'attempt_missing')
        require(not hold['closed'] and hold['state'] in states, 'hold_state_forbidden')
        return hold

    def mark_dispatched(self, attempt_id):
        """The send may have begun: from here the hold is released only by completion or reconciliation.

        An admission stamped `authority_required` refuses this bare transition (`dispatch_required`): it is
        dispatched only through `dispatch_hold`, which issues an authority."""
        with self.transaction() as data:
            hold = self._hold(data, attempt_id, ('admitted',))
            require(not (self.admission(data, attempt_id) or {}).get('authority_required'), 'dispatch_required')
            hold['state'] = 'dispatched'

    def dispatch_hold(self, attempt_id, authority, ids, gate, now):
        """Move an admitted hold to `dispatched` with a single-use authority in ONE write transaction (spec 8).

        `authority` is the SHA-256 hex of the caller's token; the token itself is never stored. `ids` are the
        attempt's derived host ids, or a callable `ids(data)` read inside the transaction; they are refused with
        `id_collision` when another hold already owns either of them. The outcome carries the ids.
        `now` is an integer or a clock callable read inside the transaction. `gate(data, now)` runs on the locked
        state and returns None or a fixed refusal, which writes nothing. A hold already `dispatched` or
        `uncertain` returns `issued=False` and never gains another authority."""
        name(attempt_id)
        require(type(authority) is str and len(authority) == 64, 'invalid_authority')
        with self.transaction() as data:
            hold = data['holds'].get(attempt_id)
            require(hold is not None, 'unknown_attempt')
            require(not hold['closed'], 'attempt_closed')
            stored = hold.get('dispatch')
            if stored is not None:
                ids = dict(session_id=stored['session_id'], message_id=stored['message_id'])
            elif callable(ids):
                ids = ids(data)
            fields(ids, 'session_id message_id')
            name(ids['session_id']); name(ids['message_id'])
            if hold['state'] in ('dispatched', 'uncertain'):
                return dict(issued=False, state=hold['state'], ids=dict(ids))
            require(hold['state'] == 'admitted', 'hold_state_unsupported')
            moment = integer(now() if callable(now) else now)
            taken = [(entry['dispatch']['session_id'], entry['dispatch']['message_id'])
                     for other, entry in data['holds'].items() if other != attempt_id and 'dispatch' in entry]
            require(all(ids['session_id'] != session and ids['message_id'] != message for session, message in taken),
                    'id_collision')
            reason = gate(data, moment)
            require(reason is None, reason)
            hold['state'] = 'dispatched'
            hold['dispatch'] = dict(session_id=ids['session_id'], message_id=ids['message_id'], authority=authority,
                                    at=moment)
            return dict(issued=True, state='dispatched', ids=dict(ids))

    def mark_uncertain(self, attempt_id):
        """Delivery cannot be established; the hold keeps its exposure and its concurrency place.

        No proof of non-launch is accepted: an uncertain dollar hold stays counted until the operator's
        `reconcile_uncertain` settles it from receipts or charges its full reservation (S3.6b-2)."""
        with self.transaction() as data:
            self._hold(data, attempt_id, ('admitted', 'dispatched'))['state'] = 'uncertain'

    def require_authority(self):
        """One-way operator switch: every later admission is stamped `authority_required` (S3.4). Never cleared."""
        with self.transaction() as data:
            data['authority_required'] = True

    def close_attempt(self, attempt_id, outcome, *, reconciled=True):
        """Release a hold only on an established outcome: `not_dispatched` or `complete`.

        `uncertain` records that the outcome is unknown and keeps the hold; a timeout never frees capacity.
        `not_dispatched` is valid only before dispatch; `complete` after dispatch or once an uncertain
        attempt is reconciled. A caller without reconciliation evidence passes `reconciled=False`, and
        `complete` on an uncertain hold is then refused with `reconciliation_required`.
        """
        require(outcome in HOLD_OUTCOMES, 'invalid_outcome')
        with self.transaction() as data:
            hold = data['holds'].get(attempt_id)
            require(hold is not None, 'attempt_missing')
            require(not hold['closed'], 'close_forbidden')
            allowed = dict(not_dispatched=('admitted',), complete=('dispatched', 'uncertain'),
                           uncertain=('admitted', 'dispatched', 'uncertain'))[outcome]
            require(hold['state'] in allowed, 'close_forbidden')
            # A dollar hold completes only through settlement (`record_event`), which charges its cost.
            require(outcome != 'complete' or not hold['amount'], 'settlement_required')
            require(reconciled or outcome != 'complete' or hold['state'] != 'uncertain', 'reconciliation_required')
            if outcome == 'uncertain':
                hold['state'] = 'uncertain'
            else:
                hold.update(closed=True, state='released')

    # -- S3.6a: lifecycle events, completion settlement, root aggregates and retention ----------------------------
    # Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention
    def record_event(self, event, digest, now):
        """Apply one validated lifecycle event, its settlement and its root aggregate in ONE write transaction.

        `event` is already shape-checked (`runtime.routing.event_error`); `digest` is its canonical SHA-256 and
        `now` the command's clock. A remembered event id with the same digest returns its original outcome with
        `duplicate: True` and writes nothing; another digest is `event_conflict`. A refusal raises BudgetError and
        stores nothing, so the event can be retried once it applies."""
        integer(now)
        with self.transaction() as data:
            _create_tables(self.db)
            if event['kind'] == 'observed':
                self._observed_anchor(data, event)  # a replay is re-authorized, never answered as a duplicate
            known = self._event(data, event['event_id'], now)
            if known is not None:
                require(known[0] == digest, 'event_conflict')
                return dict(json.loads(known[1])['outcome'], duplicate=True)
            outcome, effect, body = self._apply_event(data, event, now)
            self._store_event(data, event['event_id'], digest, body, now, effect=effect)
            return outcome

    def lookup_event(self, event_id, now):
        """The stored body of an event id still in force at `now`, or None (expired evidence is never read)."""
        with self.transaction(False) as data:
            if not self._has_tables():
                return None
            known = self._event(data, event_id, now)
            return None if known is None else json.loads(known[1])

    def resolve_block(self, event, digest, now):
        """S3.6b-2 operator recovery of ONE derived block source in one write transaction, idempotent by event id
        exactly like `record_event` (`event` from `runtime.routing.block_event`, `digest` its canonical SHA-256).

        `over_bound` closes the listed S1 run calls (every id must be an over-bound run of the organization, else
        `block_not_pending` and nothing changes): `confirm` charges the reported cost (the excess enters R as the
        call's cost), `dismiss` keeps the maximum the books already held. The run closes either way: its pre-send
        bound failed, so it never continues to another call. `legacy` removes `legacy_block` only when `marker` is
        the current `legacy_marker` (`marker_stale` otherwise). Nothing else changes, so other reasons keep the
        organization blocked. The audit is the event row (no field is added to the record)."""
        integer(now)
        with self.transaction() as data:
            _create_tables(self.db)
            known = self._event(data, event['event_id'], now)
            if known is not None:
                require(known[0] == digest, 'event_conflict')
                return dict(json.loads(known[1])['outcome'], duplicate=True)
            outcome = self._resolve_block(data, event)
            body = dict(attempt_id=ORG + event['organization'], kind=event['kind'], recorded_at=now,
                        outcome=dict(outcome))
            self._store_event(data, event['event_id'], digest, body, now, effect=True)
            return outcome

    def _resolve_block(self, data, event):
        organization, reason, decision = event['organization'], event['reason'], event['decision']
        require(organization in data['organizations'], 'organization_unconfigured')
        org = data['organizations'][organization]
        ids, charged, dropped = [], 0, 0
        if reason == 'legacy':
            require(decision == 'confirm', 'invalid_request')
            require(org.get('legacy_block'), 'block_not_pending')
            require(hmac.compare_digest(str(event['marker']).encode(), self.legacy_marker(data, organization).encode()),
                    'marker_stale')
            del org['legacy_block']
        else:
            require(reason == 'over_bound' and decision in ('confirm', 'dismiss'), 'invalid_request')
            ids = sorted(event['ids'])
            pending = []
            for run_id in ids:  # all or nothing: every id is checked before anything changes
                run = data['runs'].get(run_id)
                require(run is not None and run['organization'] == organization, 'block_not_pending')
                calls = [call for call in run['calls'].values() if 'over_bound' in call]
                require(calls, 'block_not_pending')
                pending.append((run_id, calls))
            for run_id, calls in pending:
                for call in calls:
                    actual = call.pop('over_bound')
                    excess = max(0, actual - call['maximum'])
                    if decision == 'confirm':
                        charged += excess
                    else:
                        dropped += excess
                    call.update(cost=actual if decision == 'confirm' else call['maximum'], state='completed')
                    call.pop('provision', None)
                self._close_run(data, run_id)
        return dict(state='recorded', duplicate=False, outcome='block_resolved', organization=organization,
                    reason=reason, decision=decision, resolved=dict(ids=ids, count=len(ids) or 1),
                    charged=min(MAX_INTEGER, charged), dropped=min(MAX_INTEGER, dropped),
                    remaining_reasons=sorted(self.block_reasons(data, organization)))

    def _has_tables(self):
        return self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('events','roots')"
                               ).fetchone()[0] == 2

    def _event(self, data, event_id, now):
        """(digest, body) of a remembered event, filtered by retention: an expired event whose attempt holds no
        open claim is treated as absent. Applying it again cannot settle twice: its hold is closed or gone."""
        row = self.db.execute('SELECT digest, body, recorded_at, attempt_id, effect FROM events WHERE event_id=?',
                              (event_id,)).fetchone()
        if row is None:
            return None
        if row[2] < now - RETENTION['diagnostic'] and not (row[4] and row[3] in self.open_holds(data)):
            return None
        return row[0], row[1]

    def _store_event(self, data, event_id, digest, body, now, *, effect):
        raw = _compact(body)
        require(len(raw) <= MAX_EVENT_BYTES, 'record_too_large')
        self.db.execute('INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?)',
                        (event_id, digest, now, body['attempt_id'], int(bool(effect)), raw))
        excess = self.db.execute('SELECT count(*) FROM events').fetchone()[0] - MAX_EVENTS
        if excess > 0:
            # Oldest first, keeping every event that changed a still-open claim (at most three per open hold).
            protected = set(self.open_holds(data))
            drop = []
            for rowid, attempt, changed in self.db.execute('SELECT rowid, attempt_id, effect FROM events ORDER BY rowid'):
                if not (changed and attempt in protected):
                    drop.append((rowid,))
                    if len(drop) == excess:
                        break
            self.db.executemany('DELETE FROM events WHERE rowid=?', drop)

    @staticmethod
    def _record_root(record, task):
        """(root key, stage from the admitted role) of an admission: the bound workflow job's full root locator,
        else its task id."""
        binding = record.get('binding') if isinstance(record.get('binding'), dict) else {}
        job = binding.get('job') if isinstance(binding.get('job'), dict) else None
        locator = job.get('locator') if job else None
        bucket = record.get('bucket')
        role = bucket[0] if isinstance(bucket, list) and bucket else None
        return root_key(locator) if isinstance(locator, dict) else root_key(task=task), ROLE_STAGES.get(role, 'worker')

    @staticmethod
    def _root_of(data, attempt_id, hold):
        """(root key, stage) of a ledger attempt, as stored on its hold at admission (older holds: derived)."""
        if 'root' in hold:
            return hold['root'], hold.get('stage', 'worker')
        return Ledger._record_root(Ledger.admission(data, attempt_id) or {}, hold['task'])

    def _apply_event(self, data, event, now):
        """(outcome, effect, stored body). Hold transitions keep the S3.1/S3.4 rules; `complete` settles."""
        attempt, kind = event['attempt_id'], event['kind']
        usage = event.get('usage')
        hold = data['holds'].get(attempt)
        if kind == 'observed':
            # Report-only usage joins the root of an OPEN ledger attempt (its own hold or `root_attempt_id`'s) and
            # must carry that hold's dispatch authority, so it cannot be aimed at another session's or a closed root.
            stage, funding = event['stage'], event.get('funding')
            anchor_id, anchor = self._observed_anchor(data, event)
            root = self._root_of(data, anchor_id, anchor)[0]
            require(hold is None or self._root_of(data, attempt, hold)[0] == root, 'root_mismatch')
        else:
            require(hold is not None, 'unknown_attempt')
            require(kind == 'resolve_overrun' or not hold['closed'], 'attempt_closed')
            root, stage = self._root_of(data, attempt, hold)
            stage = event.get('stage', stage)
            funding = 'api' if hold.get('organization') else 'subscription'
        aggregate = self._root_row(data, root, now)
        if kind == 'observed':
            outcome = dict(state='recorded', duplicate=False, outcome='observed')
        else:
            outcome = self._transition(data, attempt, hold, kind, event)
        attempts = aggregate['attempts']
        key = _digest16(attempt)
        if key in attempts or len(attempts) < MAX_ROOT_ATTEMPTS:
            attempts[key] = stage
        else:
            aggregate['attempts_overflow'] += 1
        summary = None if usage is None else self._summarize(usage, [])
        settled = outcome.get('outcome') in ('complete', 'overrun', 'overrun_confirmed')
        if not outcome['duplicate'] and (settled or kind == 'observed'):
            # The REPORT counts each receipt once per attempt (one attempt reporting it twice, e.g. observed and
            # then completed); another attempt's same id is its own request. Money uses only the one report.
            report = None if usage is None else self._summarize(usage, aggregate['receipts'], _digest16(attempt) + ':')
            usd = 0
            if outcome['outcome'] == 'overrun_confirmed':
                usd = outcome['settlement']['overrun']
            elif outcome.get('settlement', {}).get('kind') == 'usd_micros':
                repeated = summary['cost'] - report['cost'] if report is not None else 0
                usd = max(0, outcome['settlement']['charged'] - repeated)
            elif kind == 'observed' and funding == 'api' and report['cost'] is not None:
                usd = report['cost']
            self._aggregate(aggregate, stage, report, usd, usage is not None and usage['source'] == 'estimate')
        if not outcome['duplicate']:
            self._save_root(data, root, aggregate, now)
        body = dict(attempt_id=attempt, kind=kind, stage=stage, root=root, recorded_at=now, outcome=dict(outcome))
        for field in ('request_kind', 'at'):
            if field in event:
                body[field] = event[field]
        if funding is not None:
            body['funding'] = funding
        if summary is not None:
            body['usage'] = {k: summary[k] for k in ('source', 'tokens', 'cost', 'units', 'requests', 'duplicates')}
        # Only a transition of a ledger claim is a needed idempotency record (at most three per open hold:
        # dispatched, uncertain or cost_unknown, and the closing one); `observed` events are diagnostics.
        return outcome, kind != 'observed' and not outcome['duplicate'], body

    @staticmethod
    def _observed_anchor(data, event):
        """(id, hold) an `observed` event joins: open, dispatched with an authority, and `authority` its token."""
        anchor_id = event.get('root_attempt_id', event['attempt_id'])
        anchor = data['holds'].get(anchor_id)
        require(anchor is not None, 'unknown_attempt')
        require(not anchor['closed'], 'attempt_closed')
        issued = anchor.get('dispatch')
        require(issued is not None, 'dispatch_required')
        require(hmac.compare_digest(hashlib.sha256(event['authority'].encode()).hexdigest(), issued['authority']),
                'authority_invalid')
        return anchor_id, anchor

    def _transition(self, data, attempt, hold, kind, event):
        state = hold['state']
        if kind == 'dispatched':
            if state in ('dispatched', 'uncertain'):
                return dict(state='recorded', duplicate=True)
            require(state == 'admitted', 'hold_state_forbidden')
            require(not (self.admission(data, attempt) or {}).get('authority_required'), 'dispatch_required')
            hold['state'] = 'dispatched'
        elif kind == 'uncertain':
            if state == 'uncertain':
                return dict(state='recorded', duplicate=True)
            require(state in ('admitted', 'dispatched'), 'hold_state_forbidden')
            hold['state'] = 'uncertain'
        elif kind == 'not_dispatched':
            require(state == 'admitted', 'hold_state_forbidden')  # S3.4: no proof of non-launch after dispatch
            hold.update(closed=True, state='released')
        elif kind == 'resolve_cost_unknown':
            # Operator recovery: only a `cost_unknown` hold. The full reservation is charged unless the cost is
            # receipt-backed; a cost above the reservation is an overrun either way.
            require(state == 'cost_unknown', 'hold_state_forbidden')
            cost, receipts = event['cost_usd_micros'], event.get('receipts') or []
            # A receipt-backed zero would release the whole reservation with no spend at all (S3.6b-2 review).
            require(cost > 0 or not receipts, 'evidence_invalid')
            charge = cost if receipts else max(cost, hold['amount'])
            return self._settle_cost(data, attempt, hold, cost, charge, None, self._resolution(receipts))
        elif kind == 'reconcile_uncertain':
            # S3.6b-2 operator reconciliation of an uncertain DOLLAR hold. Provider receipts settle it like a measured
            # completion (charged as reported, the unspent rest released on the operator's word, an overrun above the
            # reservation); `charge_reservation` charges the full reservation. A zero cost is refused (`evidence_invalid`):
            # it would release the whole hold without spend, i.e. the refused no-spend proof.
            require(state == 'uncertain' and hold['amount'] > 0, 'hold_state_forbidden')
            evidence = event['evidence']
            require(evidence['kind'] in ('receipts', 'charge_reservation'), 'evidence_unsupported')
            if evidence['kind'] == 'receipts':
                cost = evidence['cost_usd_micros']
                require(cost > 0, 'evidence_invalid')
                return self._settle_cost(data, attempt, hold, cost, cost, None, self._resolution(evidence['receipts']))
            return self._settle_cost(data, attempt, hold, None, hold['amount'], None, self._resolution([]))
        elif kind == 'resolve_overrun':
            return self._resolve_overrun(data, hold, event['decision'])
        else:
            require(state != 'uncertain', 'reconciliation_required')
            require(state in ('dispatched', 'cost_unknown'), 'hold_state_forbidden')
            return self._settle(data, attempt, hold, event.get('usage'))
        return dict(state='recorded', duplicate=False)

    def _settle(self, data, attempt, hold, usage):
        """Completion settlement. A window-only hold releases its claims (headroom comes from telemetry). A dollar
        hold charges its full measured cost (receipts repeated within this one report count once; receipts seen
        elsewhere under the root never reduce it), reduces its remaining reservation by the same amount and
        releases the unused rest exactly once; an unknown cost keeps the whole exposure (`cost_unknown`)."""
        amount = hold['amount']
        summary = self._summarize(usage, []) if usage is not None else None
        if not amount:
            released = dict(hold['windows'])
            hold.update(closed=True, state='released')
            outcome = dict(state='recorded', duplicate=False)
            if summary is not None:
                outcome.update(outcome='complete', settlement=dict(kind='window', released=released,
                                                                   observed_units=summary['units']))
            return outcome
        source = summary['source'] if summary is not None else None
        cost = summary['cost'] if summary is not None else None
        if cost is None:
            repeated = hold['state'] == 'cost_unknown'  # already took effect: no new idempotency record
            hold['state'] = 'cost_unknown'
            return dict(state='recorded', duplicate=repeated, outcome='cost_unknown', settlement=dict(
                kind='usd_micros', reserved=amount, charged=0, released=0, overrun=0, remaining=amount, cost=None,
                cost_source=source))
        charge = cost
        if source == 'estimate':
            # An estimate is not a measurement: never charge below the admitted estimate, so at most the padding
            # (reservation minus admitted estimate) is released. Without a readable estimate nothing is released.
            charge = max(cost, self._admitted_estimate(data, attempt, amount))
        return self._settle_cost(data, attempt, hold, cost, charge, source)

    def _settle_cost(self, data, attempt, hold, cost, charge, source, resolution=None):
        """Charge `charge` against a dollar hold and close it. Above the reservation it is an overrun: the pre-send
        guard failed or was bypassed, and the report is unverified (`record` has no caller authentication). The
        reservation is charged (certain); the excess is kept on the hold as `overrun_state: unconfirmed`, outside
        R and every settled total, while the organization's headroom reads zero (`_credit`) and `block_reasons`
        reports `overrun` (`manual_recovery_required`) until `resolve_overrun`. Nothing is topped up. An operator
        resolution carries its audit `resolution` instead of a cost source."""
        amount = hold['amount']
        overrun, released = max(0, charge - amount), max(0, amount - charge)
        settlement = dict(reserved=amount, charged=min(charge, amount), released=released, overrun=overrun, cost=cost)
        if resolution is None:
            settlement['cost_source'] = source
        else:
            settlement['resolution'] = resolution
        if overrun:
            settlement['overrun_state'] = 'unconfirmed'  # blocks the organization through `block_reasons`
        hold.update(amount=0, closed=True, state='released', settlement=settlement)
        self._charge(data, hold, settlement['charged'])
        return dict(state='recorded', duplicate=False, outcome='overrun' if overrun else 'complete',
                    settlement=dict(settlement, kind='usd_micros', remaining=0))

    @staticmethod
    def _resolution(receipts):
        """The audit an operator settlement stores instead of a cost source: the basis, the receipt count and the
        first 32 hex characters of the SHA-256 of the sorted receipt ids (the ids themselves are never stored)."""
        digest = hashlib.sha256(json.dumps(sorted(receipts), separators=(',', ':')).encode()).hexdigest()[:32]
        return dict(basis='receipts' if receipts else 'reservation_floor', receipts=len(receipts),
                    receipts_digest=digest if receipts else None)

    def _resolve_overrun(self, data, hold, decision):
        """Operator decision on an unconfirmed overrun excess: `confirm` charges it as settled, `dismiss` drops it.
        Changes only this hold's `overrun_state` (plus the charge on confirm); the organization's block is derived,
        so it lifts only when no reason (another overrun, an over-bound call, a legacy block) remains."""
        settlement = hold.get('settlement') or {}
        require(settlement.get('overrun_state') == 'unconfirmed', 'overrun_not_pending')
        if decision == 'confirm':
            self._charge(data, hold, settlement['overrun'])
            settlement['charged'] = min(MAX_INTEGER, settlement['charged'] + settlement['overrun'])
        settlement['overrun_state'] = 'confirmed' if decision == 'confirm' else 'dismissed'
        return dict(state='recorded', duplicate=False, outcome='overrun_' + settlement['overrun_state'],
                    settlement=dict(settlement, kind='usd_micros', remaining=0))

    @staticmethod
    def _admitted_estimate(data, attempt, amount):
        record = Ledger.admission(data, attempt) or {}
        constraints = (record.get('pressure') or {}).get('constraints')
        costs = [item.get('cost') for item in constraints] if isinstance(constraints, list) else []
        if not costs or not all(type(cost) is int and 0 <= cost <= MAX_INTEGER for cost in costs):
            return amount
        return min(max(costs), amount)

    def _charge(self, data, hold, charge):
        """Add a settled charge to the organization's total, its cycle and its task: R keeps it (spec 7.4)."""
        organization, task, cycle = hold['organization'], hold['task'], hold['cycle']
        org = data['organizations'][organization]
        settled = org.setdefault('settled', dict(total=0, cycles={}))
        settled['total'] = min(MAX_INTEGER, settled['total'] + charge)
        cycles = settled['cycles']
        cycles[cycle] = min(MAX_INTEGER, cycles.pop(cycle, 0) + charge)  # most recently charged last
        current = org['policy']['cycle']
        while len(cycles) > MAX_SETTLED_CYCLES:  # an older cycle's cap is never read again; `total` keeps it
            del cycles[next(c for c in cycles if c != current)]
        tasks = data.setdefault('settled_tasks', {})
        key = task_key(organization, task)
        tasks[key] = min(MAX_INTEGER, tasks.pop(key, 0) + charge)
        if len(tasks) > MAX_SETTLED_TASKS:
            busy = {task_key(h['organization'], h['task']) for h in self.open_holds(data).values() if h['organization']}
            for old in [k for k in tasks if k not in busy][:len(tasks) - MAX_SETTLED_TASKS]:
                del tasks[old]

    @staticmethod
    def _summarize(usage, seen, prefix=''):
        """Totals of one usage report, counting each receipt once against `seen` (keys `prefix + receipt`, extended
        in place). A receipt repeated within the report counts once, at its highest cost (an unknown cost wins).
        `cost` is None when any counted request has no cost. Units stay per typed window key."""
        def price(request):
            value = request.get('cost_usd_micros')
            return float('inf') if value is None else value
        best = {}
        for request in usage['requests']:
            receipt = request.get('receipt')
            if receipt is not None and (receipt not in best or price(request) > price(best[receipt])):
                best[receipt] = request
        tokens = dict.fromkeys(TOKEN_FIELDS, 0)
        units, cost, counted, duplicates = {}, 0, 0, 0
        for request in usage['requests']:
            receipt = request.get('receipt')
            if receipt is not None:
                if best[receipt] is not request or prefix + receipt in seen:
                    duplicates += 1
                    continue
                seen.append(prefix + receipt)
            counted += 1
            for field in TOKEN_FIELDS:
                tokens[field] = min(MAX_INTEGER, tokens[field] + request['tokens'][field])
            for key, amount in (request.get('units') or {}).items():
                units[key] = min(MAX_INTEGER, units.get(key, 0) + amount)
            price = request.get('cost_usd_micros')
            cost = None if cost is None or price is None else min(MAX_INTEGER, cost + price)
        del seen[:-MAX_USAGE_RECEIPTS]
        return dict(source=usage['source'], tokens=tokens, cost=cost, units=units, requests=counted,
                    duplicates=duplicates, receipts=seen)

    @staticmethod
    def _aggregate(aggregate, stage, summary, usd, estimated):
        if summary is None:  # a settlement with no usage report (operator resolution): dollars only
            summary = dict(tokens=dict.fromkeys(TOKEN_FIELDS, 0), units={}, receipts=aggregate['receipts'])
        entry = aggregate['stages'].setdefault(stage, dict(usd_micros=0, usd_micros_estimated=0,
                                                           tokens=dict.fromkeys(TOKEN_FIELDS, 0)))
        entry['usd_micros'] = min(MAX_INTEGER, entry['usd_micros'] + usd)
        if estimated:
            entry['usd_micros_estimated'] = min(MAX_INTEGER, entry['usd_micros_estimated'] + usd)
        for field in TOKEN_FIELDS:
            entry['tokens'][field] = min(MAX_INTEGER, entry['tokens'][field] + summary['tokens'][field])
        for key, amount in summary['units'].items():
            if key in aggregate['units'] or len(aggregate['units']) < MAX_UNIT_KEYS:
                aggregate['units'][key] = min(MAX_INTEGER, aggregate['units'].get(key, 0) + amount)
            else:
                aggregate['units_dropped'] += 1
        aggregate['receipts'] = summary['receipts']

    def _root_row(self, data, root, now):
        """The root's aggregate to extend: an expired one starts afresh unless the root still has an open claim."""
        row = self.db.execute('SELECT body, updated_at FROM roots WHERE root=?', (root,)).fetchone()
        if row is not None and (row[1] >= now - RETENTION['aggregate'] or root in self._open_roots(data)):
            return json.loads(row[0])
        return dict(root=root, attempts={}, attempts_overflow=0, stages={}, units={}, units_dropped=0, receipts=[])

    def _save_root(self, data, root, aggregate, now):
        raw = _compact(aggregate)
        require(len(raw) <= MAX_ROOT_BYTES, 'record_too_large')
        self.db.execute('INSERT OR REPLACE INTO roots VALUES (?, ?, ?)', (root, now, raw))
        excess = self.db.execute('SELECT count(*) FROM roots').fetchone()[0] - MAX_ROOTS
        if excess > 0:  # oldest first, never a root with an open claim (MAX_HOLDS < MAX_ROOTS)
            busy = self._open_roots(data)
            drop = [(name,) for (name,) in self.db.execute('SELECT root FROM roots ORDER BY updated_at, root')
                    if name not in busy and name != root][:excess]
            self.db.executemany('DELETE FROM roots WHERE root=?', drop)

    def _open_roots(self, data):
        return {self._root_of(data, attempt, hold)[0] for attempt, hold in self.open_holds(data).items()}

    def root_summary(self, root, now):
        """The root's accounting aggregate still within retention, or None. An open hold keeps it readable."""
        with self.transaction(False) as data:
            return self._root_summary(data, root, now)

    def _root_summary(self, data, root, now):
        if not self._has_tables():
            return None
        row = self.db.execute('SELECT body, updated_at FROM roots WHERE root=?', (root,)).fetchone()
        if row is None or (row[1] < now - RETENTION['aggregate'] and root not in self._open_roots(data)):
            return None
        aggregate = json.loads(row[0])
        return dict(aggregate, attempts=len(aggregate['attempts']) + aggregate['attempts_overflow'],
                    stage_attempts={stage: list(aggregate['attempts'].values()).count(stage)
                                    for stage in set(aggregate['attempts'].values())})

    def root_report(self, root, now):
        """One read transaction: the root aggregate and the root's open (unresolved) holds."""
        with self.transaction(False) as data:
            summary = self._root_summary(data, root, now)
            holds = [dict(hold) for attempt, hold in self.open_holds(data).items()
                     if self._root_of(data, attempt, hold)[0] == root]
            return summary, holds

    def expire_evidence(self, now):
        """Delete expired diagnostics in one write transaction; idempotent (spec 11).

        Events after 14 days unless they changed a still-open claim; root aggregates after 60 days unless the root
        still has an open claim; admission records of closed attempts after 14 days, and their quota snapshot detail
        (`pressure`, `snapshot_id`) after 24 hours. Never removed by age: open, uncertain and cost-unknown holds,
        settled charges and cycle caps, seen attempt ids, runs and native tasks. Reads filter the same way, so an
        expired record cannot influence a decision whether or not this has run."""
        integer(now)
        removed = dict(events=0, aggregates=0, admissions=0, snapshots=0)
        with self.transaction() as data:
            _create_tables(self.db)
            open_ids = set(self.open_holds(data))
            diagnostic, snapshot = now - RETENTION['diagnostic'], now - RETENTION['snapshot']
            old = [(event_id,) for event_id, attempt, changed in self.db.execute(
                'SELECT event_id, attempt_id, effect FROM events WHERE recorded_at < ?', (diagnostic,))
                if not (changed and attempt in open_ids)]
            self.db.executemany('DELETE FROM events WHERE event_id=?', old)
            removed['events'] = len(old)
            busy = self._open_roots(data)
            old = [(root,) for (root,) in self.db.execute('SELECT root FROM roots WHERE updated_at < ?',
                                                          (now - RETENTION['aggregate'],)) if root not in busy]
            self.db.executemany('DELETE FROM roots WHERE root=?', old)
            removed['aggregates'] = len(old)
            kept = []
            for record in data['admissions']:
                at = record.get('at')
                closed = record.get('attempt_id') not in open_ids
                if closed and type(at) is int and at < diagnostic:
                    removed['admissions'] += 1
                    continue
                if closed and type(at) is int and at < snapshot and ('pressure' in record or 'snapshot_id' in record):
                    record.pop('pressure', None)
                    record.pop('snapshot_id', None)
                    removed['snapshots'] += 1
                kept.append(record)
            data['admissions'] = kept
            protected = sum(1 for _, attempt, changed in self.db.execute(
                'SELECT event_id, attempt_id, effect FROM events') if changed and attempt in open_ids)
            holds = self.open_holds(data).values()
            safety = dict(open_holds=len(open_ids), uncertain_holds=sum(h['state'] == 'uncertain' for h in holds),
                          cost_unknown_holds=sum(h['state'] == 'cost_unknown' for h in holds),
                          idempotency_events=protected, seen_attempts=len(data['seen_attempts']),
                          settled_cycles=sum(len((org.get('settled') or {}).get('cycles', {}))
                                             for org in data['organizations'].values()),
                          settled_tasks=len(data.get('settled_tasks', {})),
                          open_runs=sum(not run['closed'] for run in data['runs'].values()))
        return dict(removed=removed, retained_safety_records=safety)

    @staticmethod
    def _close_run(data, run_id):
        """Close a run and release the window claims of the hold it was converted from."""
        data['runs'][run_id]['closed'] = True
        hold = data['holds'].get(run_id)
        if hold is not None and hold['state'] == 'converted' and not hold['closed']:
            hold.update(closed=True, state='released')

    def release_native(self, account, task, evidence, now):
        with self.transaction() as data:
            key = json.dumps([name(account), name(task)])
            runs = [run for run in data['runs'].values() if run['native'] == key]
            require(all(run['closed'] for run in runs), 'native_task_active')
            require(evidence['account'] == account and evidence['task'] == task, 'native_task_mismatch')
            require(key in data['native'], 'native_task_missing')
            if key not in data['finished_native']:
                data['finished_native'].append(key)

    def reject_unsent(self, run_id, phase):
        """Host transport established no send; not a user/worker receipt."""
        with self.transaction() as data:
            run = data['runs'][run_id]; call = run['calls'][phase]
            require(call['state'] == 'dispatching', 'rejection_forbidden')
            call.update(cost=0, state='rejected-before-send')
            self._close_run(data, run_id)

    @staticmethod
    def _consent(data, pin):
        fields(pin, 'scope revision qualification_revision')
        config = data.get('routing', {}).get(pin['scope'])
        require(config is not None and config['revision'] == pin['revision']
                and not config['disabled'] and (pin['qualification_revision'] is None
                or config['qualification_revision'] == pin['qualification_revision']), 'consent_changed')

    def reconcile(self, *, scope=None):
        """Report scoped recovery evidence without altering reservations."""
        result = {'state': 'manual_recovery_required'}
        if scope is None:
            return result
        pending = [dict(attempt_id=run_id + ':' + phase, reserved_usd_micros=call['maximum'],
                        incomplete=call.get('incomplete'))
                   for run_id, run in sorted(self.snapshot()['runs'].items())
                   if (run.get('consent') or {}).get('scope') == scope
                   for phase, call in run['calls'].items() if call['state'] == 'unresolved']
        if pending:
            result['pending'] = pending[0]
            result['pending_attempts'] = pending
        return result
