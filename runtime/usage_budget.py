"""Usage Routing accounting with transaction-bound coordinator admission."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

MAX_INTEGER = 2**53 - 1
MAX_RECORD = 2_000_000
FRESH_SECONDS = 900
STATE_VERSION = 2
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
    state = dict(version=STATE_VERSION, identity=identity, organizations={}, runs={}, native={}, finished_native=[])
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        db.execute('INSERT INTO ledger VALUES (1, ?)', (json.dumps(state),))
        db.commit()
    return identity


class Ledger:
    """Identity-bound accounting. Only the trusted coordinator can supply a live gate."""
    def __init__(self, path, identity):
        self.path = Path(path).absolute()
        self.path = self.path.parent.resolve() / self.path.name
        self.identity = name(identity)
        private(self.path.parent, True)
        try:
            info = private(self.path)
        except FileNotFoundError as error:
            raise BudgetError('manual_recovery_required') from error
        self.file_identity = (info.st_dev, info.st_ino)
        self.db = sqlite3.connect(f'{self.path.as_uri()}?mode=rw', uri=True, timeout=5,
                                  isolation_level=None)
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.snapshot()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.db.close()

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
    def transaction(self, write=True):
        self._check()
        self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            row = self.db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
            require(row is not None and len(row[0]) <= MAX_RECORD, 'invalid_ledger')
            data = json.loads(row[0])
            require(data['version'] in (1, STATE_VERSION) and data['identity'] == self.identity,
                    'ledger_identity_changed')
            self._migrate(data)
            yield data
            self._check()
            if write:
                raw = json.dumps(data, allow_nan=False, separators=(',', ':'))
                require(len(raw) <= MAX_RECORD, 'ledger_capacity_reached')
                self.db.execute('UPDATE ledger SET body=? WHERE id=1', (raw,))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    @staticmethod
    def _migrate(data):
        """Upgrade version 1 state in place: drop the global slot and the 20% floor.

        Open runs keep their exposure through their own calls; the old floor
        seeds the issued allowance (a floor was one fifth of the first balance).
        """
        if data['version'] == STATE_VERSION:
            return
        data.pop('slot', None)
        for org in data['organizations'].values():
            floor = org.pop('floor', None)
            if org.get('issued') is None:
                org['issued'] = floor * 5 if floor is not None else None
        data['version'] = STATE_VERSION

    def snapshot(self):
        with self.transaction(False) as data:
            return data

    def configure(self, organization, policy, expected_revision):
        with self.transaction() as data:
            return self._configure(data, organization, policy, expected_revision)

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
        return sum(cls._cost(call) for run in data['runs'].values()
                   if run['organization'] == organization and (task is None or run['task'] == task)
                   for call in run['calls'].values() if cycle is None or call['cycle'] == cycle)

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
    def _admit(cls, data, organization, task, maximum, revision, now):
        integer(maximum); integer(revision); integer(now)
        org = data['organizations'][organization]; policy = org['policy']
        require(not org['blocked'], 'manual_recovery_required')
        require(org['revision'] == revision, 'policy_revision_changed')
        require(org['observed_at'] is not None and 0 <= now - org['observed_at'] <= FRESH_SECONDS,
                'balance_stale_or_missing')
        require(policy['start'] <= now < policy['stop_at'], 'expiry_or_cycle_boundary')
        require(maximum <= cls._headroom(data, organization, task, now), 'insufficient_headroom')

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

    @classmethod
    def _check_caps(cls, data, organization):
        total, per_account = cls._caps(data)
        open_runs = [run for run in data['runs'].values() if not run['closed']]
        require(len(open_runs) < total and
                sum(run['organization'] == organization for run in open_runs) < per_account, 'concurrency_cap')

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
        with self.transaction() as data:
            if run_id in data['runs']:
                run = data['runs'][run_id]
                require(live_gate is None and 'live_admission' not in run, 'live_run_id_already_used')
                require((run['organization'], run['task'], run['digest']) == (organization, task, digest), 'run_identity_changed')
                return run
            require(len(data['runs']) < 1000, 'ledger_capacity_reached')
            self._check_caps(data, organization)
            moved = 0
            if transfer_from is not None:
                moved = self._transfer(data, organization, transfer_from, maximum)
            admission = self._live_admit(data, organization, maximum, now, live_gate)
            self._admit(data, organization, task, maximum, revision, now)
            require(native.get('task') == task, 'native_task_mismatch')
            key = self._native(data, native, now)
            data['native'][key]['latest_at'] = now
            if consent is not None:
                self._consent(data, consent)
            run = dict(consent=consent, organization=organization, task=task, digest=digest, revision=revision,
                       native=key, closed=False, calls={})
            if admission is not None:
                run['live_admission'] = admission
            if moved:
                run['transferred_from'] = dict(run=transfer_from[0], amount=moved)
            data['runs'][run_id] = run
            run['calls']['count'] = self._call(data, run, maximum, digest)
            if provision:
                run['calls']['count']['provision'] = provision
            return run

    def top_up(self, run_id, added, revision, now, *, live_gate=None):
        """Extend the open envelope atomically, or raise insufficient_headroom and change nothing."""
        integer(added)
        with self.transaction() as data:
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
        with self.transaction() as data:
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
                    run['closed'] = True
        require(not overrun, 'cost_exceeds_bound')

    def uncertain(self, run_id, phase, *, incomplete=None):
        with self.transaction() as data:
            call = data['runs'][run_id]['calls'][phase]
            require(call['state'] == 'dispatching', 'uncertainty_forbidden')
            call['state'] = 'unresolved'
            if incomplete is not None:
                call['incomplete'] = incomplete

    def close_run(self, run_id):
        with self.transaction() as data:
            run = data['runs'][run_id]
            require(not any(c['state'] in ('dispatching', 'unresolved') for c in run['calls'].values()), 'request_may_be_running')
            for call in run['calls'].values():
                if call['state'] == 'reserved': call.update(cost=0, state='rejected-before-send')
            run['closed'] = True

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
            run['closed'] = True

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
