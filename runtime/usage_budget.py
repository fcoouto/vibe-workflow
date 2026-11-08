"""Usage Routing accounting with transaction-bound coordinator admission."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

MAX_INTEGER = 2**53 - 1  # also the bound for every micro-unit amount (about 9e9 USD)
# Two record limits (compact JSON characters). A write that adds exposure or grows a table (`grows=True`:
# begin, top_up, reserve_generation, admit, configure, enable_organization) commits only while the record stays
# within SOFT_RECORD; past it those are refused with `ledger_capacity_reached`. Every other write (dispatch,
# dispatch_hold, complete, uncertain, reject_unsent, close_run, mark_*, close_attempt, release_unlaunched,
# convert_hold, observe, disable_organization, release_native) commits up to MAX_RECORD, so a full ledger can always settle and
# never leaves a call in `dispatching`. MAX_RECORD is also the read limit. It covers the computed worst case
# (tests/test_routing_funding.py CapacityTests: every table and entry at its bound, 256-character ASCII
# identifiers, every native task finished, every call with a maximal `incomplete` record): 9.83M, 82%.
SOFT_RECORD = 6_000_000
MAX_RECORD = 12_000_000
FRESH_SECONDS = 900
STATE_VERSION = 3
MAX_RUNS = 1000
MAX_ADMISSIONS = 256
MAX_HOLDS = 256  # open holds are bounded by the concurrency caps; released ones are pruned at the bound
MAX_SEEN = 1000  # recent attempt ids kept for reuse rejection; older ids are the caller's responsibility
# Per-entry bounds (compact JSON characters) so the worst case provably fits; see CapacityTests.
# A larger admission is refused with `record_too_large` and writes nothing.
MAX_ADMISSION_BYTES = 4096
MAX_HOLD_BYTES = 2048
# Fixed-size fields `dispatch_hold`, `mark_uncertain` and `release_unlaunched` add to an admitted hold (two derived
# ids, an authority hash and three times); the worst-case capacity test counts every hold at both bounds.
DISPATCH_HOLD_BYTES = 384
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
        db.commit()
    return identity


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
            require(data['version'] in (1, 2, STATE_VERSION) and data['identity'] == self.identity,
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
        """
        if data['version'] == 1:
            data.pop('slot', None)
            for org in data['organizations'].values():
                floor = org.pop('floor', None)
                if org.get('issued') is None:
                    org['issued'] = floor * 5 if floor is not None else None
            data['version'] = 2
        if data['version'] == 2:
            data['version'] = STATE_VERSION
        data.setdefault('holds', {})
        data.setdefault('admissions', [])
        data.setdefault('seen_attempts', [])
        for hold in data['holds'].values():
            hold.setdefault('state', 'admitted')
            if 'windows' not in hold:
                vector = hold.pop('vector', {})
                hold.update(organization=None, amount=0, cycle=None, windows={WIN + k: v for k, v in vector.items()})

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
            org = dict(revision=0, balance=None, observed_at=None, issued=None, blocked=False)
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
        """R: every run call plus every open hold on this organization (uncertain holds included)."""
        runs = sum(cls._cost(call) for run in data['runs'].values()
                   if run['organization'] == organization and (task is None or run['task'] == task)
                   for call in run['calls'].values() if cycle is None or call['cycle'] == cycle)
        return runs + sum(hold['amount'] for hold in data['holds'].values()
                          if not hold['closed'] and hold['organization'] == organization
                          and (task is None or hold['task'] == task) and (cycle is None or hold['cycle'] == cycle))

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
        return max(0, cls._credit_raw(data, organization, now))

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
        require(not org['blocked'], 'manual_recovery_required')
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

        S3 must use this (not `begin`) to dispatch a funded attempt. The organization, task and provider account
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
            require(hold is not None and not hold['closed'] and hold['state'] == 'admitted' and hold['amount'] > 0
                    and attempt_id not in data['runs'], 'convert_forbidden')
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
            if actual > call['maximum']:
                data['organizations'][run['organization']]['blocked'] = True
                call['state'] = 'unresolved'; overrun = True
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
            hold = dict(account=account, task=task, organization=organization, amount=amount, cycle=cycle,
                        windows={k: v for k, v in vector.items() if k.startswith(WIN)}, closed=False, state='admitted')
            for entry, limit in ((record, MAX_ADMISSION_BYTES), (hold, MAX_HOLD_BYTES)):
                try:
                    size = len(json.dumps(entry, allow_nan=False, separators=(',', ':')))
                except (TypeError, ValueError, RecursionError) as error:
                    raise BudgetError('invalid_admission') from error
                require(size <= limit, 'record_too_large')
            self._prune(data)
            require(len(data['holds']) < MAX_HOLDS, 'ledger_capacity_reached')
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
        """Drop the oldest released holds once the table is full; open (incl. uncertain) holds are never dropped."""
        if len(data['holds']) >= MAX_HOLDS:
            for key in [k for k, hold in data['holds'].items() if hold['closed']][:MAX_HOLDS // 2]:
                del data['holds'][key]

    def _hold(self, data, attempt_id, states):
        hold = data['holds'].get(attempt_id)
        require(hold is not None, 'attempt_missing')
        require(not hold['closed'] and hold['state'] in states, 'hold_state_forbidden')
        return hold

    def mark_dispatched(self, attempt_id):
        """The send may have begun: from here the hold is released only by completion or reconciliation."""
        with self.transaction() as data:
            self._hold(data, attempt_id, ('admitted',))['state'] = 'dispatched'

    def dispatch_hold(self, attempt_id, authority, ids, gate, now):
        """Move an admitted hold to `dispatched` with a single-use authority in ONE write transaction (spec 8).

        `authority` is the SHA-256 hex of the caller's token; the token itself is never stored. `ids` are the
        attempt's derived host ids, refused with `id_collision` when another hold already owns either of them.
        `gate(data)` is pure, runs on the locked state and returns None or a fixed refusal, which writes nothing.
        A hold already `dispatched` or `uncertain` returns `issued=False` and never gains another authority."""
        name(attempt_id); integer(now)
        require(type(authority) is str and len(authority) == 64, 'invalid_authority')
        fields(ids, 'session_id message_id')
        name(ids['session_id']); name(ids['message_id'])
        with self.transaction() as data:
            hold = data['holds'].get(attempt_id)
            require(hold is not None, 'unknown_attempt')
            require(not hold['closed'], 'attempt_closed')
            if hold['state'] in ('dispatched', 'uncertain'):
                return dict(issued=False, state=hold['state'])
            require(hold['state'] == 'admitted', 'hold_state_unsupported')
            owned = {value for other, entry in data['holds'].items() if other != attempt_id and 'dispatch' in entry
                     for value in (entry['dispatch']['session_id'], entry['dispatch']['message_id'])}
            require(not owned & set(ids.values()), 'id_collision')
            reason = gate(data)
            require(reason is None, reason)
            hold['state'] = 'dispatched'
            hold['dispatch'] = dict(session_id=ids['session_id'], message_id=ids['message_id'], authority=authority,
                                    at=now)
            return dict(issued=True, state='dispatched')

    def mark_uncertain(self, attempt_id, now=None):
        """Delivery cannot be established; the hold keeps its exposure and its concurrency place.

        A hold dispatched with an authority records the first uncertain time (`now`); `release_unlaunched` needs it."""
        if now is not None:
            integer(now)
        with self.transaction() as data:
            hold = self._hold(data, attempt_id, ('admitted', 'dispatched'))
            hold['state'] = 'uncertain'
            if now is not None and 'dispatch' in hold:
                hold['dispatch'].setdefault('uncertain_at', now)

    def release_unlaunched(self, attempt_id, proof, now):
        """Release an uncertain dispatched hold on proof that the host never admitted its message.

        Applies only to a hold dispatched with an authority (`dispatch_hold`) and marked uncertain with a time.
        From that mark every check of the attempt is blocked, so the derived message being absent at a host read
        made strictly after it (`uncertain_at < observed_at <= now`) proves no request of the attempt was sent.
        `proof` is exactly `{kind: 'message_absent', session_id, message_id, observed_at}` naming the hold's
        derived ids; anything else is `proof_invalid` and changes nothing."""
        integer(now)
        with self.transaction() as data:
            hold = data['holds'].get(attempt_id)
            require(hold is not None, 'attempt_missing')
            require(not hold['closed'] and hold['state'] in ('dispatched', 'uncertain'), 'close_forbidden')
            issued = hold.get('dispatch')
            require(issued is not None, 'proof_not_applicable')
            require(hold['state'] == 'uncertain', 'uncertain_required')
            require('uncertain_at' in issued, 'proof_not_applicable')
            require(type(proof) is dict and set(proof) == {'kind', 'session_id', 'message_id', 'observed_at'}
                    and proof['kind'] == 'message_absent' and proof['session_id'] == issued['session_id']
                    and proof['message_id'] == issued['message_id'] and type(proof['observed_at']) is int
                    and issued['uncertain_at'] < proof['observed_at'] <= now, 'proof_invalid')
            hold.update(closed=True, state='released')
            issued['released'] = dict(proof='message_absent', observed_at=proof['observed_at'])

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
            require(reconciled or outcome != 'complete' or hold['state'] != 'uncertain', 'reconciliation_required')
            if outcome == 'uncertain':
                hold['state'] = 'uncertain'
            else:
                hold.update(closed=True, state='released')

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
