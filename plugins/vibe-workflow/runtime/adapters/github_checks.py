"""Bounded Checks transport; the host owns credentials and evidence authority.

No default network, key lookup, executable discovery, or readiness grant exists.
Matching server fields is transport evidence, not protected-source qualification.
"""
import re

from runtime.startup import json_object


class ChecksError(ValueError):
    """Fixed diagnostics never expose provider bodies or credential material."""


def _require(value):
    if not value:
        raise ChecksError('Checks transport unavailable or inconsistent')


def _integer(value):
    return type(value) is int and 0 < value <= 2 ** 53


def _text(value, limit):
    return type(value) is str and 0 < len(value) <= limit and value.isprintable() and bool(value.strip())


class GitHubChecks:
    def __init__(self, *, repository, repository_id, app_id, request):
        _require(_text(repository, 256) and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
                 and repository.split('/')[1] not in ('.', '..'))
        _require(_integer(repository_id) and _integer(app_id) and callable(request))
        self.repository, self.repository_id, self.app_id = repository, repository_id, app_id
        self.request = request

    def _call(self, method, endpoint, *, payload=None, status=200):
        try:
            code, raw = self.request(method, endpoint, payload=payload, timeout=10, limit=128 * 1024)
            _require(type(code) is int and code == status and type(raw) is bytes and len(raw) <= 128 * 1024)
            return json_object(raw)
        except Exception:
            raise ChecksError('Checks transport unavailable or inconsistent') from None

    def _repository(self):
        value = self._call('GET', '/repos/' + self.repository)
        _require(value.get('id') == self.repository_id and type(value.get('id')) is int
                 and type(value.get('full_name')) is str
                 and value['full_name'].casefold() == self.repository.casefold())

    def _binding(self, head_sha, name, external_id):
        _require(type(head_sha) is str and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head_sha)
                 and _text(name, 128) and _text(external_id, 256))

    def _receipt(self, value, head_sha, name, external_id, run_id=None):
        _require(type(value) is dict and _integer(value.get('id'))
                 and (run_id is None or value['id'] == run_id)
                 and type(value.get('app')) is dict and type(value['app'].get('id')) is int
                 and value['app']['id'] == self.app_id and value.get('head_sha') == head_sha
                 and value.get('name') == name and value.get('external_id') == external_id
                 and value.get('status') in ('queued', 'in_progress', 'completed'))
        conclusion = value.get('conclusion')
        _require(_text(conclusion, 64) if value['status'] == 'completed' else conclusion is None)
        return dict(id=value['id'], repository_id=self.repository_id, app_id=self.app_id,
                    head_sha=head_sha, name=name, external_id=external_id, status=value['status'],
                    conclusion=conclusion, merge_authority=False)

    def create(self, *, head_sha, name, external_id):
        self._binding(head_sha, name, external_id)
        self._repository()
        value = self._call('POST', '/repos/' + self.repository + '/check-runs', status=201,
                           payload=dict(head_sha=head_sha, name=name, external_id=external_id, status='in_progress'))
        receipt = self._receipt(value, head_sha, name, external_id)
        _require(receipt['status'] == 'in_progress')
        return receipt

    def complete(self, *, run_id, conclusion, head_sha, name, external_id):
        self._binding(head_sha, name, external_id)
        _require(_integer(run_id) and conclusion in ('success', 'failure', 'cancelled', 'timed_out', 'action_required'))
        self._repository()
        endpoint = '/repos/' + self.repository + '/check-runs/' + str(run_id)
        self._receipt(self._call('GET', endpoint), head_sha, name, external_id, run_id)
        payload = dict(status='completed', conclusion=conclusion, output=dict(
            title='Local workflow verification', summary='Local workflow verification ' + conclusion + '.'))
        receipt = self._receipt(self._call('PATCH', endpoint, payload=payload), head_sha, name, external_id, run_id)
        _require(receipt['status'] == 'completed' and receipt['conclusion'] == conclusion)
        return receipt

    def reconcile(self, *, head_sha, name, external_id):
        """Observe one uncertain creation; external_id is not a server idempotency key."""
        self._binding(head_sha, name, external_id)
        self._repository()
        value = self._call('GET', '/repos/' + self.repository + '/commits/' + head_sha
                           + '/check-runs?filter=all&per_page=100')
        count, rows = value.get('total_count'), value.get('check_runs')
        _require(type(count) is int and 0 <= count <= 100 and type(rows) is list and len(rows) == count)
        matches = []
        for row in rows:
            _require(type(row) is dict and type(row.get('app')) is dict and _integer(row['app'].get('id')))
            if row.get('external_id') == external_id and row.get('name') == name and row['app']['id'] == self.app_id:
                matches.append(self._receipt(row, head_sha, name, external_id))
        _require(len(matches) <= 1)
        return matches[0] if matches else None
