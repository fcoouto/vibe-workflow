"""Bounded GitHub facts through a host-selected executable and executor.

The host executor must enforce the supplied deadline/output limits and retain its
actual execution provenance. JSON observations alone establish no check or merge
authority. No credential lookup or executable discovery occurs in this adapter.
Unsafe operations require a host-qualified write source; merge additionally
requires independently qualified target enforcement. Ordinary REST provides no
atomic merge-target condition. Callables alone authenticate neither capability;
there are no default mutation sources. Metadata uses labels and append-only
comments so concurrent edits to shared titles/descriptions are preserved.
"""
from pathlib import Path
import json
import re
import subprocess
from urllib.parse import quote, urlencode

from runtime import delivery, evidence, state
from runtime.startup import json_object


def _require(condition):
    if not condition:
        raise state.StateError('GitHub delivery observation is unavailable or inconsistent')


def _commit(value):
    _require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value))
    return value


class GitHubDelivery:
    def __init__(self, *, executable, host, run, mutate=None, merge=None):
        _require(isinstance(executable, str) and Path(executable).is_absolute()
                 and Path(executable).is_file() and callable(run))
        _require(isinstance(host, str) and re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', host))
        self.executable, self.host, self.run = executable, host, run
        _require((mutate is None or callable(mutate)) and (merge is None or callable(merge)))
        self.mutate, self.merge_source = mutate, merge

    def _run(self, argv, *, payload=None, preconditions=None, merging=False):
        try:
            bounds = dict(timeout=10, limit=128 * 1024)
            if payload is not None:
                bounds['input'] = evidence._raw(payload)
            execute = self.run
            if preconditions is not None:
                execute = self.merge_source if merging else self.mutate
                _require(callable(execute))
                bounds['preconditions'] = json_object(evidence._raw(preconditions))
            code, raw = execute(argv, **bounds)
            _require(type(code) is int and code == 0 and isinstance(raw, bytes)
                     and len(raw) <= 128 * 1024)
            return raw
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            # Provider diagnostics may contain credentials or private response text.
            raise state.StateError('GitHub delivery observation is unavailable or inconsistent') from None

    def _response(self, endpoint, *, method='GET', payload=None, include=False, preconditions=None, merging=False):
        _require(method in {'GET', 'POST', 'DELETE', 'PUT'} and (payload is None or method != 'GET')
            and (method == 'GET' or preconditions is not None))
        # The supported 2022 API retains merge_commit_sha; 2026 removes it.
        argv = [self.executable, 'api', '--hostname', self.host, '-H', 'X-GitHub-Api-Version: 2022-11-28']
        if method != 'GET':
            argv += ['--method', method]
        if payload is not None:
            argv += ['--input', '-']
        if include:
            argv += ['--include']
        return self._run([*argv, endpoint], payload=payload, preconditions=preconditions, merging=merging)

    def _read(self, endpoint, *, method='GET', payload=None, preconditions=None, merging=False):
        try:
            return json_object(self._response(endpoint, method=method, payload=payload, preconditions=preconditions, merging=merging))
        except (ValueError, TypeError):
            raise state.StateError('GitHub delivery observation is unavailable or inconsistent') from None

    def _list(self, endpoint):
        try:
            header, body = re.split(br'\r?\n\r?\n', self._response(endpoint, include=True), maxsplit=1)
            _require(re.match(br'HTTP/[0-9.]+ 200(?: |\r?$)', header.splitlines()[0]))
            _require(not any(line.lower().startswith(b'link:') for line in header.splitlines()[1:]))
            rows = json.loads(body)
            _require(isinstance(rows, list) and len(rows) < 100 and all(isinstance(row, dict) for row in rows))
            return rows
        except (ValueError, TypeError, IndexError):
            raise state.StateError('GitHub discovery is incomplete or unavailable') from None

    def observe(self, publication: dict) -> dict:
        evidence._bounded(publication)
        state._fields(publication, ('host', 'repository', 'target', 'branch', 'pull_request'),
                      ('id', 'provider', 'candidate', 'dependencies', 'required'))
        _require(publication['host'] == self.host)
        delivery._branch(publication['target'])
        delivery._branch(publication['branch'])
        repository = publication['repository']
        _require(isinstance(repository, str)
                 and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
                 and repository.split('/')[1] not in ('.', '..'))
        number = publication['pull_request']
        _require(type(number) is int and 0 < number <= 2 ** 53)
        repo = self._read('repos/' + repository)
        repo_id = repo.get('id')
        _require(type(repo_id) is int and repo_id > 0
                 and isinstance(repo.get('full_name'), str)
                 and repo['full_name'].casefold() == repository.casefold())
        pr = self._read(f'repos/{repository}/pulls/{number}')
        _require(type(pr.get('number')) is int and pr['number'] == number
                 and pr.get('state') in ('open', 'closed') and type(pr.get('draft')) is bool
                 and type(pr.get('merged')) is bool)
        for side, expected in (('base', publication['target']), ('head', publication['branch'])):
            row = pr.get(side)
            _require(isinstance(row, dict) and row.get('ref') == expected
                     and isinstance(row.get('repo'), dict)
                     and type(row['repo'].get('id')) is int and row['repo']['id'] == repo_id)
            _commit(row.get('sha'))
        if pr['merged']:
            _require(pr['state'] == 'closed' and isinstance(pr.get('merged_at'), str)
                     and bool(pr['merged_at'].strip()))
            _commit(pr.get('merge_commit_sha'))
        else:
            _require(pr.get('merged_at') is None)
        mergeability = pr.get('mergeable')
        _require(mergeability is None or type(mergeability) is bool)
        return dict(host=self.host, repository=repo['full_name'], repository_id=repo_id,
                    pull_request=number, state=pr['state'], draft=pr['draft'], merged=pr['merged'],
                    head=pr['head']['sha'], base=pr['base']['sha'], target=pr['base']['ref'],
                    branch=pr['head']['ref'], merge_commit=pr.get('merge_commit_sha') if pr['merged'] else None,
                    merged_at=pr.get('merged_at'), mergeability=mergeability,
                    checks='unobserved', reviews='unobserved')

    def _labels(self, publication):
        endpoint = f"repos/{publication['repository']}/issues/{publication['pull_request']}/labels?per_page=100"
        labels = self._list(endpoint)
        _require(all(isinstance(row.get('name'), str) for row in labels))
        return {row['name'] for row in labels}

    def _label_phase(self, publication, phase, binding):
        repository = 'repos/' + publication['repository']
        wanted, other = '[' + phase + ']', '[READY]' if phase == 'BLOCKED' else '[BLOCKED]'
        endpoint = repository + '/labels/' + quote(wanted, safe='')
        try:
            label = self._read(endpoint)
        except state.StateError:
            # Label names are unique; failed reads do not assert absence or grant authority.
            try:
                label = self._read(repository + '/labels', method='POST', payload=dict(name=wanted,
                    color='d73a4a' if phase == 'BLOCKED' else '0e8a16'), preconditions=dict(binding=binding))
            except state.StateError:
                label = self._read(endpoint)
        _require(label.get('name') == wanted)
        endpoint = repository + '/issues/' + str(publication['pull_request']) + '/labels'
        labels = self._labels(publication)
        if other in labels:
            try:
                self._response(endpoint + '/' + quote(other, safe=''), method='DELETE', preconditions=dict(binding=binding))
            except state.StateError:
                _require(other not in self._labels(publication))
        if wanted not in labels:
            self._response(endpoint, method='POST', payload=dict(labels=[wanted]), preconditions=dict(binding=binding))
        after = self._labels(publication)
        _require(wanted in after and other not in after)

    def _comment(self, publication, operation, phase, binding, *, append=True):
        marker = '<!-- vibe-delivery:operation:' + operation['operation_id'] + ' -->'
        _require(marker not in operation['body'])
        candidate = {key: binding[key] for key in ('host', 'repository', 'target', 'head')}
        body = marker + '\n[' + phase + ']\n\n' + operation['body'] + '\n\n```json\n' + json.dumps(candidate, sort_keys=True) + '\n```'
        endpoint = f"repos/{publication['repository']}/issues/{publication['pull_request']}/comments"
        comments = self._list(endpoint + '?per_page=100')
        matches = [row for row in comments if isinstance(row.get('body'), str) and marker in row['body']]
        if matches:
            _require(len(matches) == 1 and matches[0]['body'] == body)
            _require(type(matches[0].get('id')) is int and matches[0]['id'] > 0)
            return matches[0]['id']
        if not append:
            return None
        comment = self._read(endpoint, method='POST', payload=dict(body=body), preconditions=dict(binding=binding))
        _require(type(comment.get('id')) is int and comment['id'] > 0 and comment.get('body') == body)
        return comment['id']

    def execute(self, action, operation):
        """Transport only; the coordinator owns fresh authority, D5/D6/D7 and intent."""
        evidence._bounded(operation)
        state._fields(operation, ('publication', 'head', 'repository_id', 'marker', 'operation_id', 'title', 'body'), ('method',))
        _require(action in {'checkpoint', 'ready', 'reopen', 'merge'})
        if not callable(self.merge_source if action == 'merge' else self.mutate):
            raise state.StateError('A separately qualified merge-target source is required' if action == 'merge'
                else 'Provider metadata needs a qualified write source')
        _commit(operation['head'])
        _require(type(operation['repository_id']) is int and operation['repository_id'] > 0
            and isinstance(operation['marker'], str) and re.fullmatch(r'[0-9a-f]{64}', operation['marker']))
        _require(isinstance(operation['operation_id'], str) and re.fullmatch(r'[0-9a-f]{64}', operation['operation_id']))
        state._text(operation['title'], 'publication title', 256)
        publication = dict(operation['publication'])
        marker, content = operation['marker'], operation['body']
        _require(isinstance(content, str) and len(content) <= 64 * 1024 and '\x00' not in content)
        if action == 'merge':
            _require(operation.get('method') in {'merge', 'squash', 'rebase'})
        if publication['pull_request'] is None:
            _require(action == 'checkpoint' and publication['host'] == self.host)
            delivery._branch(publication['branch']);delivery._branch(publication['target'])
            repository = publication['repository']
            _require(isinstance(repository, str) and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository)
                and repository.split('/')[1] not in ('.', '..'))
            rows = self._list('repos/' + repository + '/pulls?' + urlencode(dict(state='all',
                head=repository.split('/')[0] + ':' + publication['branch'], base=publication['target'], per_page=100)))
            if rows:
                _require(len(rows) == 1 and isinstance(rows[0].get('body'), str)
                    and '<!-- vibe-delivery:publication:' + marker + ' -->' in rows[0]['body'])
                publication['pull_request'] = rows[0]['number']
            else:
                repo = self._read('repos/' + repository)
                _require(type(repo.get('id')) is int and repo['id'] == operation['repository_id']
                    and isinstance(repo.get('full_name'), str) and repo['full_name'].casefold() == repository.casefold())
                remote = self._read('repos/' + repository + '/git/ref/' + quote('heads/' + publication['branch'], safe=''))
                _require(remote.get('ref') == 'refs/heads/' + publication['branch'] and remote.get('object', {}).get('sha') == operation['head'])
                created = self._read('repos/' + repository + '/pulls', method='POST', payload=dict(
                    title=operation['title'], body='<!-- vibe-delivery:publication:' + marker + ' -->\n\n' + content,
                    head=publication['branch'], base=publication['target'], draft=True), preconditions=dict(binding=dict(
                        host=self.host, repository=repository, repository_id=operation['repository_id'],
                        pull_request=None, head=operation['head'], target=publication['target'], branch=publication['branch'])))
                publication['pull_request'] = created.get('number')
        facts = self.observe(publication)
        _require(facts['head'] == operation['head'] and facts['repository_id'] == operation['repository_id'])
        if action == 'merge' and facts['merged']:
            return facts
        _require(facts['state'] == 'open' and not facts['merged'])
        binding = {key: facts[key] for key in ('host', 'repository', 'repository_id', 'pull_request',
                                               'head', 'base', 'target', 'branch', 'state', 'merged')}
        if action != 'merge':
            self._comment(publication, operation, 'READY' if action == 'ready' else 'BLOCKED', binding, append=False)
        if action == 'merge':
            _require(not facts['draft'])
            # Direct REST merge has expected-SHA semantics and never implicitly enqueues.
            self._read(f"repos/{publication['repository']}/pulls/{publication['pull_request']}/merge", method='PUT',
                       payload=dict(sha=operation['head'], merge_method=operation['method']), preconditions=dict(binding=binding), merging=True)
        else:
            _require(action != 'checkpoint' or facts['draft'])
            if action != 'ready':
                self._label_phase(publication, 'BLOCKED', binding)
            if action in {'ready', 'reopen'}:
                wanted_draft = action == 'reopen'
                if facts['draft'] != wanted_draft:
                    self._run([self.executable, 'pr', 'ready', str(publication['pull_request']), '--repo',
                        self.host + '/' + publication['repository'], *(['--undo'] if wanted_draft else [])],
                        preconditions=dict(binding=binding))
                if action == 'ready':
                    self._label_phase(publication, 'READY', binding)
            comment_id = self._comment(publication, operation, 'READY' if action == 'ready' else 'BLOCKED', binding)
        result = self.observe(publication)
        _require(result['head'] == operation['head'] and result['repository_id'] == operation['repository_id'])
        if action in {'ready', 'reopen', 'checkpoint'}:
            _require(result['state'] == 'open' and not result['merged'] and result['draft'] == (action != 'ready'))
        return result | (dict(comment_id=comment_id) if action != 'merge' else {})
