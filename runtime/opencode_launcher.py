"""The OpenCode managed launcher (C.3 design 3.1-3.7, slice C3-3; contract: clients/opencode.md#managed-launcher).

One delegated attempt on an OpenCode server, through the C3-1 core (`routing_launch.launch`) and the routing service
from `scripts.routing.managed_service`: the `status` preflight, `routing.health` and the enforced worker profile,
`prepare` (the service collects the observations), the host catalog and entitlement check, health again, `dispatch`,
`routing.attach`, the session and prompt with the derived ids, idle, re-read, `routing.descendants`, accounting,
`record complete`, then `routing.detach`. Every exit closes its hold or names the operator op that will.

It never runs a project command inside a managed session, never calls an operator op or `set managed_projects`, never
retries another route and never changes the coordinator. The dispatch token stays in memory and reaches only
`routing.attach`, `routing.descendants` and `routing.detach`. The server it starts gets an environment without
credential-like names, a worker profile that denies edits outside the project, and the routing state directories
in the shim's `protected_paths`.
"""
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from runtime import opencode_accounting, opencode_subscription, routing, routing_launch, routing_observations, \
    subscription_billing
from runtime.adapters import opencode as adapter

# Fixed refusal codes this slice defines (C.3 design 3.6). Codes from the core and the routing command pass through.
HOST_UNHEALTHY = 'host_unhealthy'
PROFILE_NOT_ENFORCED = 'profile_not_enforced'
API_FUNDING_UNQUALIFIED = 'api_funding_unqualified'
ATTACH_FAILED = 'attach_failed'
LAUNCH_UNCONFIRMED = 'launch_unconfirmed'
LAUNCH_OPTIONS_UNEXPRESSIBLE = 'launch_options_unexpressible'
LAUNCH_OPTIONS_MISMATCH = 'launch_options_mismatch'
AGENT_STEPS_UNCAPPED = 'agent_steps_uncapped'
# User decision 4 (C.3 design 7.1): an API-funded OpenCode route is refused until the S3.6 positive and negative
# descendants runs have been observed with real costs. Only a code change lifts it.
API_FUNDING_QUALIFIED = False

SHIM = Path(__file__).resolve().parent / 'adapters' / 'opencode_plugin.ts'
SESSION_TITLE = 'managed worker'  # titled at creation, so OpenCode sends no title request
# The worker profile as OpenCode session permission rules; the last matching rule wins (observed by the qualification
# harness). Reading and editing inside the project are allowed; edits outside it (`external_directory`), shells, web
# access, Code Mode, subagents, skills, image generation and every MCP or other tool are denied.
ALLOWED = ('read', 'glob', 'grep', 'list', 'edit', 'todoread', 'todowrite')
DENIED = ('external_directory', 'shell', 'bash', 'webfetch', 'websearch', 'execute', 'subagent', 'task', 'skill',
          adapter.IMAGE_ACTION)
WORKER_RULES = ([dict(action='*', resource='*', effect='deny')]
                + [dict(action=action, resource='*', effect='allow') for action in ALLOWED]
                + [dict(action=action, resource='*', effect='deny') for action in DENIED])
# Actions the shim also tightens to `deny` for the attached session (`deny_actions` only grow).
DENY_ACTIONS = ('external_directory', 'shell', 'bash', 'webfetch', 'websearch')

# The server's environment is an allowlist (as in the live-qualified harness, tests/qualification/server.py):
# HOME, because OpenCode and the Claude Agent provider find their own sign-ins and data under it; USER, which the
# macOS keychain lookup of a provider sign-in uses (the harness ran live with it); LANG for text decoding; TMPDIR for
# OpenCode's temporary files; XDG_DATA_HOME whenever it is set, because OpenCode then keeps its data (its `auth.json`
# sign-ins) there. PATH is fixed, never the caller's, so no project-local tool directory reaches the server.
ENV_ALLOWED = ('HOME', 'USER', 'LANG', 'TMPDIR', 'XDG_DATA_HOME')
SERVER_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
# Fixed switches (names present in the 2.2.6 binary; their effect on project config is unobserved).
SERVER_FLAGS = dict(OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_MODELS_FETCH='1',
                    OPENCODE_DISABLE_PROJECT_CONFIG='1')

REQUEST_TIMEOUT = 60  # seconds per HTTP request to the server
IDLE_TIMEOUT = 1800  # seconds an attempt may run before its launch counts as unconfirmed
IDLE_POLL = 0.5
REREADS = 3  # bounded re-reads while a message is still finishing
REREAD_WAIT = 1.0
SERVER_START = 30
MAX_PAGES = 200
# Claude Agent `native.rateLimit` windows -> (window id, length in seconds). Window units are thousandths of the
# provider's utilization, so a manifest route's `demand` for these windows is in the same unit.
# Single source: routing_observations (the collector's seed and host paths read the same values).
RATE_WINDOWS = routing_observations.RATE_WINDOWS
WINDOW_UNITS = routing_observations.WINDOW_UNITS
# The unit each stored window declares, in the field `unit`: the collector's own name, which it compares with the
# manifest route's `window_unit` and refuses when missing or different.
WINDOW_UNIT = routing_observations.WINDOW_UNIT


# Per-launch provider options (`launch_options`) go into the launch's own inline config, in OpenCode's V2 keys:
# `providers.<p>.models.<m>.body` request fields and `agents.<name>.steps` for every BUILTIN_AGENTS name. Only
# OpenRouter can pin an endpoint (its request body's `provider.only` with `allow_fallbacks: false`). V2 has no
# default step limit, so an agent the config does not name would run uncapped: the launch reads the host's agent
# listing once and refuses unless `uncapped_agents` finds none. Whether 2.2.6 honours each key, whether its
# listing includes the hidden agents, and that a prompt without an agent runs as PRIMARY_AGENT (recorded on 2.2.6),
# are live items (clients/opencode.md).
PRIMARY_AGENT = 'build'
# The V2 built-in agents: the visible `build`, `plan`, `general` and `explore`, and the hidden maintenance agents.
BUILTIN_AGENTS = (PRIMARY_AGENT, 'plan', 'general', 'explore', 'compaction', 'title', 'summary')
LISTING_UNREADABLE = 'agent listing unreadable'
ENDPOINT_PIN_PROVIDERS = ('openrouter',)
MAX_OUTPUT_TOKENS = 1_000_000
MAX_STEPS = 64


class Unexpressible(ValueError):
    """Required launch options this launcher cannot express in OpenCode's config: nothing is started or sent."""


class Unconfirmed(Exception):
    """The host launch cannot be confirmed: the attempt is recorded `uncertain` and never retried."""


class ServerUnavailable(Exception):
    """The launcher's own OpenCode server did not start."""


# -- identities, profile and server configuration ----------------------------------------------------------------
def choice(provider, model, variant, funding='subscription'):
    """An explicit `assignment.choice` for an OpenCode route, in the shim's route identity
    (`routing_launch.route_identity('opencode', ...)`)."""
    return routing_launch.choice('opencode', funding, provider=provider, model=model, effort=variant)


def rule_effect(rules, action):
    """The effect the last rule matching `action` (or `*`) for every resource gives, or None."""
    effect = None
    for rule in rules:
        if rule.get('resource') == '*' and rule.get('action') in (action, '*'):
            effect = rule.get('effect')
    return effect


def host_environment(environ):
    """The environment for a process the launcher starts: only the ENV_ALLOWED names the caller has, plus the fixed
    SERVER_PATH. Everything else stays out, credential-like names and the variables that load code or config into
    the server (`NODE_OPTIONS`, `BUN_*`, `PYTHON*`, `LD_*`/`DYLD_*`, `OPENCODE_*`, `XDG_CONFIG_HOME`) included."""
    found = {name: environ[name] for name in ENV_ALLOWED if isinstance(environ.get(name), str)
             and not adapter.credential_name(name)}
    return dict(found, PATH=SERVER_PATH)


def protected_paths(settings_path, ledger_path):
    found = []
    for path in (settings_path, ledger_path):
        folder = str(Path(path).absolute().parent.resolve())
        if folder not in found:
            found.append(folder)
    return found


def launch_options(provider, model, *, endpoint, max_output_tokens, steps):
    """One launch's provider options, validated: the endpoint `model` must stay on, its output cap and the step
    count. Raises Unexpressible for anything the inline config cannot carry exactly."""
    if not (isinstance(provider, str) and provider in ENDPOINT_PIN_PROVIDERS and adapter.NAME.fullmatch(provider)
            and isinstance(model, str) and adapter.MODEL_ID.fullmatch(model)
            and isinstance(endpoint, str) and adapter.MODEL_ID.fullmatch(endpoint)
            and type(max_output_tokens) is int and 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS
            and type(steps) is int and 1 <= steps <= MAX_STEPS):
        raise Unexpressible(LAUNCH_OPTIONS_UNEXPRESSIBLE)
    return dict(provider=provider, model=model, endpoint=endpoint, max_output_tokens=max_output_tokens, steps=steps)


def agents_config(steps):
    """The inline config's `agents`: every built-in agent capped at `steps`."""
    return {name: dict(steps=steps) for name in BUILTIN_AGENTS}


def uncapped_agents(listing, config_agents, steps):
    """The agents in a host's listing (`GET /api/agent` data) that are not capped at exactly `steps`; an empty list
    means every listed agent is. The one rule the launcher and the gateway harness share. An agent's name is its
    `id`, else its `name`. When it reports `steps`, they must equal `steps` (a bool never counts). When it does not,
    it is capped only if `config_agents` (the inline config's `agents`) sets exactly `steps` for that name. An empty,
    malformed or unreadable (None) listing answers `[LISTING_UNREADABLE]`, so a non-empty answer always refuses."""
    def exact(value):
        return type(value) is int and value == steps

    if not isinstance(listing, list) or not listing:
        return [LISTING_UNREADABLE]
    config_agents = config_agents if isinstance(config_agents, dict) else {}
    found = []
    for agent in listing:
        if not isinstance(agent, dict):
            return [LISTING_UNREADABLE]
        name = agent.get('id') if isinstance(agent.get('id'), str) else agent.get('name')
        if not isinstance(name, str):
            return [LISTING_UNREADABLE]
        if 'steps' in agent:
            capped = exact(agent['steps'])
        else:
            configured = config_agents.get(name)
            capped = isinstance(configured, dict) and exact(configured.get('steps'))
        if not capped:
            found.append(name)
    return found


def server_config(settings_path, ledger_path, plugin, options=None):
    """The inline server config (`OPENCODE_CONFIG_CONTENT`): the routing shim as the only plugin, pinned to the
    managed service's own stores with the routing state directories protected from model edits; no MCP server,
    formatter or LSP server; and the worker profile's permission rules. With `options` (`launch_options`), the
    model's request body pins the endpoint and caps the output, and every built-in agent's steps are capped. Whether
    OpenCode lets a project or global config add to or override it is unobserved (clients/opencode.md#managed-launcher,
    pre-canary probe)."""
    shim = dict(python=sys.executable, settings=str(settings_path), ledger=str(ledger_path),
                protected_paths=protected_paths(settings_path, ledger_path))
    config = {'$schema': 'https://opencode.ai/config.json', 'plugins': [dict(package=str(plugin), options=shim)],
              'mcp': {}, 'formatter': False, 'lsp': False, 'permissions': [dict(rule) for rule in WORKER_RULES]}
    if options is not None:
        options = launch_options(**options)
        body = dict(provider=dict(only=[options['endpoint']], allow_fallbacks=False),
                    max_tokens=options['max_output_tokens'])
        config.update(providers={options['provider']: dict(models={options['model']: dict(body=body)})},
                      agents=agents_config(options['steps']))
    return config


# -- the host ----------------------------------------------------------------------------------------------------
class ServerHost:
    """A loopback OpenCode server. `request` and `rpc` answer `(status, body)`; the password stays in memory.
    `options` are the launch options its inline config carries (None: none)."""

    options = None

    def __init__(self, port, password, directory, timeout=REQUEST_TIMEOUT):
        self.port, self._password, self.directory, self.timeout = port, password, directory, timeout

    def request(self, method, path, body=None, query=None):
        import base64
        import urllib.parse
        headers = {'Authorization': 'Basic ' + base64.b64encode(('opencode:' + self._password).encode()).decode(),
                   'x-opencode-directory': self.directory, 'accept': 'application/json'}
        data = None
        if body is not None:
            data, headers['content-type'] = json.dumps(body).encode(), 'application/json'
        if query:
            path += '?' + urllib.parse.urlencode(query)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        finally:
            connection.close()
        try:
            return response.status, (json.loads(raw) if raw else None)
        except ValueError:
            return response.status, None

    def rpc(self, method, payload):
        status, body = self.request('POST', '/api/rpc/routing/%s' % method, {'input': payload})
        return status, (body.get('output', body) if isinstance(body, dict) else body)


class Server(ServerHost):
    """An `opencode serve` the launcher started for one checkout; `stop` ends its process group and removes its
    private directory (plugin wrapper and log)."""

    def __init__(self, port, password, directory, process, workdir, options=None):
        super().__init__(port, password, directory)
        self.process, self.workdir, self.options = process, workdir, options

    def stop(self):
        try:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(10)
        except (ProcessLookupError, PermissionError):
            pass
        finally:
            shutil.rmtree(self.workdir, ignore_errors=True)


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def start_server(checkout, *, settings_path, ledger_path, opencode='opencode', environ=None, timeout=SERVER_START,
                 options=None):
    """Start `opencode serve` on a free loopback port in the checkout's canonical directory, with the routing shim
    pinned to the given stores, a stripped environment and this launch's `options`. Raises Unexpressible before
    starting anything for options it cannot carry, and ServerUnavailable when the server does not come up."""
    options = None if options is None else launch_options(**options)
    directory = os.path.realpath(checkout)
    workdir = Path(tempfile.mkdtemp(prefix='vibe-opencode-launch-')).resolve()
    process = None
    try:
        plugin = workdir / 'plugins' / 'routing'
        plugin.mkdir(parents=True, mode=0o700)
        (plugin / 'index.ts').write_text('export { default } from %s\n' % json.dumps(str(SHIM)))
        password = secrets.token_urlsafe(32)
        env = host_environment(os.environ if environ is None else environ)
        env.update(SERVER_FLAGS, OPENCODE_CONFIG_CONTENT=json.dumps(server_config(settings_path, ledger_path, plugin,
                                                                                  options)),
                   OPENCODE_SERVER_PASSWORD=password)
        port, log = _free_port(), workdir / 'server.log'
        executable = shutil.which(opencode) or opencode  # resolved with the caller's PATH; the server gets SERVER_PATH
        with open(os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as out:
            process = subprocess.Popen([executable, 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                       cwd=directory, env=env, stdout=out, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        while True:
            text = log.read_text(errors='replace')
            if 'server listening' in text:
                found = re.search(r'^server password (\S+)$', text, re.M)
                return Server(port, found.group(1) if found else password, directory, process, workdir, options)
            if process.poll() is not None or time.monotonic() > deadline:
                raise ServerUnavailable('server_not_started')
            time.sleep(0.1)
    except BaseException:
        if process is not None:
            Server(0, '', directory, process, workdir).stop()
        shutil.rmtree(workdir, ignore_errors=True)
        raise


# -- host reads --------------------------------------------------------------------------------------------------
def _data(body):
    return body.get('data', body) if isinstance(body, dict) else body


def _get(host, path, query=None):
    status, body = host.request('GET', path, query=query)
    if status != 200:
        raise Unconfirmed('host_read_failed')
    return body


def health(host):
    """The `routing.health` answer when it is `ok`, else None (an RPC error counts as unhealthy)."""
    try:
        status, body = host.rpc('health', {})
    except Exception:  # noqa: BLE001 - any failure means "do not send"
        return None
    return body if status == 200 and isinstance(body, dict) and body.get('state') == 'ok' else None


def enforcement(host, answer):
    """The adapter's `probe` of this host with the worker profile's own tool states: `enforced` or `advisory`."""
    try:
        status, info = host.request('GET', '/api/info')
    except Exception:  # noqa: BLE001
        status, info = None, None
    version = info.get('version') if status == 200 and isinstance(info, dict) else None
    denies = answer.get('managed_denies') or []
    tools = {tool: rule_effect(WORKER_RULES, tool) for tool in adapter.WORKER_TOOLS}
    tools.update({name: 'deny' if name in denies else 'allow' for name in adapter.MANAGED_DENIES})
    try:  # health ok means the shim loaded, and it registers every hook routing uses
        probed = adapter.probe(dict(version=version if isinstance(version, str) else 'unknown',
                                    hooks=list(adapter.HOOKS), plugin_health='ok', worker_tools=tools))
    except adapter.AdapterError:
        return 'advisory'
    return probed['enforcement_profile']


def agents_capped(host, steps):
    """Whether every agent the host lists is capped at `steps` (`uncapped_agents`; one `GET /api/agent`)."""
    try:
        status, body = host.request('GET', '/api/agent')
    except Exception:  # noqa: BLE001 - an unreadable listing proves no cap
        return False
    return not uncapped_agents(_data(body) if status == 200 else None, agents_config(steps), steps)


def catalog(host):
    """The host's actual model listing as route identities (every listed variant)."""
    try:
        status, body = host.request('GET', '/api/model')
    except Exception:  # noqa: BLE001 - an unreadable listing is an empty catalog: `catalog_mismatch`
        return []
    entries = []
    for model in (_data(body) if status == 200 else None) or []:
        if not isinstance(model, dict) or not isinstance(model.get('providerID'), str) or \
                not isinstance(model.get('id'), str):
            continue
        variants = [item if isinstance(item, str) else (item or {}).get('id') if isinstance(item, dict) else None
                    for item in model.get('variants') or []]
        for variant in [item for item in variants if isinstance(item, str)] or [None]:
            try:
                entries.append(routing_launch.route_identity('opencode', provider=model['providerID'],
                                                             model=model['id'], effort=variant))
            except ValueError:  # an entry no admitted identity could ever match (routing.route_identity is None)
                continue
    return entries


def _messages(host, session):
    """Every page of a session's messages, ascending (the cursor carries the order; paging ends on an empty page)."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        body = _get(host, '/api/session/%s/message' % session, {'cursor': cursor} if cursor else {'order': 'asc'})
        page = _data(body) or []
        found.extend(page)
        cursor = (body.get('cursor') or {}).get('next') if isinstance(body, dict) else None
        if not page or not cursor:
            return found
    raise Unconfirmed('message_paging_unbounded')


def _descendants(host, root):
    """Every listed session under `root` by `parentID` (command children included) and the lineage list."""
    listed = [item for item in (_data(_get(host, '/api/session')) or []) if isinstance(item, dict) and item.get('id')]
    parents = {item['id']: item.get('parentID') for item in listed}
    found, frontier = [], [root]
    while frontier:
        current = frontier.pop()
        for child, parent in parents.items():
            if parent == current and child not in found and child != root:
                found.append(child)
                frontier.append(child)
    return found, [dict(session_id=sid, parent_session_id=parent) for sid, parent in parents.items()][:1024]


def _not_entitled(messages, route):
    """Whether one of the primary session's own assistant messages, on the admitted route itself, carries the
    provider's refusal of that route for this account: HTTP 400 with `model_not_found` in the upstream body (C.3
    design 3.4; clients/opencode.md#route-identity). A child session's or another model's refusal is not this route's."""
    if not isinstance(route, dict):
        return False
    admitted = dict(providerID=route.get('provider'), id=route.get('model'), variant=route.get('variant'))
    for message in messages:
        error = message.get('error') if isinstance(message, dict) and message.get('type') == 'assistant' else None
        if not isinstance(error, dict) or error.get('status') != 400:
            continue
        model = message.get('model') if isinstance(message.get('model'), dict) else {}
        if {key: model.get(key) for key in admitted} != admitted:
            continue
        response = error.get('response') if isinstance(error.get('response'), dict) else {}
        if 'model_not_found' in str(response.get('body') or ''):
            return True
    return False


def account(host, root, *, funding, attempt, authority, route=None):
    """Re-read the attempt and account it (clients/opencode.md wiring steps 2-5): the root and every page of its
    messages, every descendant by `parentID`, then `routing.descendants` while the root is still attached (None when
    the call failed), then `usage_from_messages` with `children`, `lineage` and `shim_descendants`. `route` is the
    admitted route (`provider`, `model`, `variant`); only the root's refusal on it counts as `not_entitled`."""
    raw = _messages(host, root)
    total = opencode_accounting.normalize_session(_data(_get(host, '/api/session/%s' % root)))
    found, lineage = _descendants(host, root)
    children = []
    for child in found:
        messages = _messages(host, child)
        children.append(dict(opencode_accounting.normalize_session(_data(_get(host, '/api/session/%s' % child))),
                             messages=[adapter.normalize_message(item) for item in messages], complete=True))
    try:
        status, shim = host.rpc('descendants', dict(session_id=root, attempt_id=attempt, authority=authority))
        shim = shim if status == 200 and isinstance(shim, dict) and shim.get('state') == 'ok' else None
    except Exception:  # noqa: BLE001 - no answer: the cross-check is recorded as not done
        shim = None
    result = opencode_accounting.usage_from_messages(
        [adapter.normalize_message(item) for item in raw], total['tokens'], session_id=root, funding=funding,
        complete=True, session_cost=total['cost'], children=children, lineage=lineage, shim_descendants=shim)
    return dict(result=result, not_entitled=_not_entitled(raw, route), children=len(children))


def _wait_idle(host, root, after_ms, timeout):
    """Until the root has an idle record created after the prompt and neither it nor a descendant is active."""
    deadline = time.monotonic() + timeout
    while True:
        status, active = host.request('GET', '/api/session/active')
        listed = json.dumps(active) if status == 200 else None
        idle = any(item.get('type') == 'idle' and ((item.get('time') or {}).get('created') or 0) >= after_ms
                   for item in _messages(host, root) if isinstance(item, dict))
        if idle and listed is not None and not any(sid in listed for sid in [root, *_descendants(host, root)[0]]):
            return
        if time.monotonic() > deadline:
            raise Unconfirmed('idle_timeout')
        time.sleep(IDLE_POLL)


def windows(observation):
    """Window observations for the collector's store from a `rate_limit_observation`, in WINDOW_UNITS."""
    found = []
    for name, window in sorted(((observation or {}).get('windows') or {}).items()):
        fraction, reset = window.get('utilization_fraction'), window.get('resets_at_s')
        if name not in RATE_WINDOWS or fraction is None or not reset:
            continue
        key, length = RATE_WINDOWS[name]
        found.append(dict(id=key, limit=WINDOW_UNITS, used=min(WINDOW_UNITS, math.ceil(fraction * WINDOW_UNITS)),
                          length=length, reset_at=reset, rolling=False, unit=WINDOW_UNIT,
                          observed_at=observation['observed_ms'] // 1000))
    return found


# -- the launch --------------------------------------------------------------------------------------------------
class _Gate:
    """The managed service as the core sees it. Immediately before `dispatch` it refuses an API-funded admission
    (`api_funding_unqualified`) and asks `routing.health` again (`host_unhealthy`); the core then closes the hold
    with `not_dispatched`. It remembers the admission, the attempt id and how far `dispatch` got (`dispatch`: None,
    `sending`, `dispatched` or `refused`), never the token."""

    def __init__(self, service, host, options=None):
        self.service, self.host, self.options = service, host, options
        self.project, self.settings_path, self.ledger_path = service.project, service.settings_path, service.ledger_path
        self.admission, self.attempt_id, self.dispatch = None, None, None

    def _options_reason(self, request):
        """`launch_options_mismatch` when the launch's options pin another model than the one being dispatched."""
        if self.options is None:
            return None
        request = request if isinstance(request, dict) else {}
        same = (request.get('provider'), request.get('model')) == (self.options['provider'], self.options['model'])
        return None if same else LAUNCH_OPTIONS_MISMATCH

    def handle(self, raw, op=None):
        value = json.loads(raw)
        if value.get('op') == 'dispatch':
            self.attempt_id = value.get('attempt_id')
            funded = (self.admission or {}).get('billing_class') in routing.FUNDED
            reason = API_FUNDING_UNQUALIFIED if funded and not API_FUNDING_QUALIFIED else \
                self._options_reason(value.get('request')) or (None if health(self.host) is not None else HOST_UNHEALTHY)
            if reason is not None:
                return dict(v=1, state='blocked', reason=reason)
            self.dispatch = 'sending'  # unknown until the service answers: an interrupt here asks the ledger
            response = self.service.handle(raw, op)
            self.dispatch = 'dispatched' if response.get('state') in ('dispatched', 'already_dispatched') else 'refused'
            return response
        response = self.service.handle(raw, op)
        if value.get('op') == 'prepare' and response.get('state') == 'admitted':
            self.admission = response.get('admission')
            self.attempt_id = (self.admission or {}).get('attempt_id')
        return response


class _Attempt:
    """The host transport for one attempt: attach, create the derived session, prompt, wait, re-read and account.
    It keeps the token for `descendants` and `detach` only, and forgets it after detach."""

    def __init__(self, host, gate, prompt, idle_timeout):
        self.host, self.gate, self.prompt, self.idle_timeout = host, gate, prompt, idle_timeout
        self._token, self.session, self.attached, self.refusal, self.read = None, None, False, None, None

    def __call__(self, route, *, authority, session_id, message_id):
        self._token, self.session = authority, session_id
        attempt = self.gate.attempt_id
        # An omitted effort (None) is sent as no variant, to the shim and the session alike; the routing check reads
        # a missing variant as the admitted identity's `none` sentinel.
        model = '%s/%s' % (route['provider'], route['model']) + ('' if route['variant'] is None else '#' + route['variant'])
        try:
            status, body = self.host.rpc('attach', dict(session_id=session_id, attempt_id=attempt, authority=authority,
                                                        funding_pool=route['funding_pool'], model=model,
                                                        deny_actions=list(DENY_ACTIONS)))
        except Exception:  # noqa: BLE001 - treated like a refusal: nothing was created
            status, body = None, None
        if status != 200 or not isinstance(body, dict) or body.get('state') != 'attached':
            self.refusal = ATTACH_FAILED
            return dict(kind='complete')  # no session was created and no prompt sent, so nothing was spent
        self.attached = True
        status, created = self.host.request('POST', '/api/session', dict(
            id=session_id, title=SESSION_TITLE, permissions=WORKER_RULES, location=dict(directory=self.host.directory),
            model=dict(id=route['model'], providerID=route['provider'],
                       **({} if route['variant'] is None else dict(variant=route['variant'])))))
        if status != 200 or (_data(created) or {}).get('id') != session_id:
            raise Unconfirmed('session_unconfirmed')
        began = int(time.time() * 1000)
        status, _ = self.host.request('POST', '/api/session/%s/prompt' % session_id,
                                      dict(text=self.prompt, id=message_id))
        if status != 200:
            raise Unconfirmed('prompt_unconfirmed')
        _wait_idle(self.host, session_id, began, self.idle_timeout)
        funding = routing.FUNDING_OF.get((self.gate.admission or {}).get('billing_class'), 'api')
        read = account(self.host, session_id, funding=funding, attempt=attempt, authority=authority, route=route)
        for _ in range(REREADS):  # a message still finishing: re-read, bounded; still unfinished settles unknown
            if 'message_in_progress' not in (read['result'].get('unknown') or []):
                break
            time.sleep(REREAD_WAIT)
            read = account(self.host, session_id, funding=funding, attempt=attempt, authority=authority, route=route)
        self.read = read
        usage = read['result'].get('usage')
        return dict(kind='complete', **({} if usage is None else dict(usage=usage)))

    def detach(self):
        """After `record complete` (or `uncertain`): release the shim's attachment, then forget the token."""
        detached = None
        if self.attached:
            try:
                status, body = self.host.rpc('detach', dict(session_id=self.session, attempt_id=self.gate.attempt_id,
                                                            authority=self._token))
                detached = status == 200 and isinstance(body, dict) and body.get('state') == 'detached'
            except Exception:  # noqa: BLE001 - a refused detach needs no retry (clients/opencode.md#handshake)
                detached = False
        self._token, self.attached = None, False
        return detached


def _record(service, attempt, kind):
    """One lifecycle event under the core's own event id (`<kind>:<attempt>`), so it is a duplicate when the core
    already recorded it; the answer's state and reason, or None when the call failed."""
    try:
        answer = service.handle(json.dumps(dict(v=1, op='record', event=dict(
            event_id='%s:%s' % (kind, attempt), attempt_id=attempt, kind=kind))).encode())
    except Exception:  # noqa: BLE001 - the re-raised interrupt is the outcome; `status` shows the hold
        return None
    return answer.get('state'), answer.get('reason')


def _interrupted(service, gate):
    """After an interrupt: a confirmed dispatch is recorded `uncertain`. Otherwise the hold is closed with
    `not_dispatched`, which the ledger accepts only while the hold is still `admitted` (a dispatch commits atomically,
    so a refused or interrupted `dispatch` left it so). `hold_state_forbidden` there proves the dispatch committed,
    so it is recorded `uncertain`; any other doubt after a dispatch was sent is `uncertain` too (conservative). A
    `complete` that already won makes both `attempt_closed`, which changes nothing."""
    attempt = gate.attempt_id
    if attempt is None:
        return  # nothing was admitted
    if gate.dispatch != 'dispatched':
        answer = _record(service, attempt, 'not_dispatched')
        if answer is not None and (answer[0] == 'recorded' or answer[1] == 'attempt_closed'):
            return
        if gate.dispatch is None and (answer is None or answer[1] != 'hold_state_forbidden'):
            return  # never sent: the hold is only admitted and expires unsent; `uncertain` would keep it
    _record(service, attempt, 'uncertain')


def _account_of(settings_path, source, route):
    """The account of the admitted route, from the source's read-only lookup (the qualified path's for a generated
    route), or None."""
    try:
        spec = (source or routing_observations.default_source(settings_path)).admitted_route(route)
    except (OSError, ValueError):
        return None
    return spec['account'] if isinstance(spec, dict) and isinstance(spec.get('account'), str) else None


def _finish(result, gate, attempt, detached, settings_path, source):
    if result.get('state') == 'uncertain':
        recovery = result.get('recovery')
        return dict(state='uncertain', reason=LAUNCH_UNCONFIRMED, attempt_id=result.get('attempt_id'),
                    recovery=recovery, command='scripts/routing.py %s' % recovery, detached=detached)
    if attempt.refusal is not None:
        return dict(state='blocked', reason=attempt.refusal, attempt_id=result.get('attempt_id'),
                    closed=result.get('state') == 'recorded')
    if result.get('state') != 'recorded' or attempt.read is None:
        return result
    result = dict(result, detached=detached, not_entitled=attempt.read['not_entitled'])
    admission = result.get('admission') or {}
    if admission.get('billing_class') == 'included_subscription':
        try:  # telemetry for the collector; an unusable observation is skipped, never fatal
            for window in windows(attempt.read['result'].get('rate_limit')):
                routing_observations.store_window(settings_path, admission['pool'], window)
        except (OSError, ValueError, KeyError, TypeError):
            result['windows_stored'] = False
    if attempt.read['not_entitled']:
        account_name = _account_of(settings_path, source, admission.get('route'))
        if account_name is None:  # never a mark without its account: the route stays a candidate, the result says why
            result['entitlement_mark'] = 'account_unknown'
            return result
        try:
            routing_observations.mark_not_entitled(settings_path, admission['route'], account_name, int(time.time()))
            result['entitlement_mark'] = 'written'
        except (OSError, ValueError, KeyError, TypeError):
            result['entitlement_mark'] = 'unavailable'
    return result


def launch(checkout, *, host, assignment, job, prompt, workflow=None, constraints=None, planned=False, profile=None,
           service=None, source=None, idle_timeout=IDLE_TIMEOUT, options=None):
    """Run one managed attempt on `host` (a ServerHost for the checkout's canonical directory). `service` defaults
    to `scripts.routing.managed_service(checkout)`. Returns the core's result plus `not_entitled`; never the token.

    An unmanaged project returns `project_not_managed` without touching the host. Refusals before `dispatch` send
    nothing and close any admitted hold with `not_dispatched`; a refused attach creates nothing and closes the hold
    with `record complete`; a launch that cannot be confirmed is recorded `uncertain` and names the operator op.
    With `options` (`launch_options`), `host.options` must be exactly those options (`launch_options_unexpressible`,
    before anything is prepared), every agent the host lists must carry their step cap (`agent_steps_uncapped`, also
    before anything is prepared) and the admitted model theirs (`launch_options_mismatch`, before `dispatch`)."""
    owned = service is None
    if owned:
        from scripts import routing as command
        service = command.managed_service(checkout)
    try:
        if getattr(service, 'project', None) is None:
            return dict(state='blocked', reason=routing_launch.PROJECT_REQUIRED)
        if not routing_launch.managed(service.project, service.settings_path):
            return dict(state='unmanaged', reason=routing_launch.PROJECT_NOT_MANAGED)
        if options is not None:
            try:
                options = launch_options(**options)
            except (TypeError, ValueError):
                options = None
            if options is None or getattr(host, 'options', None) != options:
                return dict(state='blocked', reason=LAUNCH_OPTIONS_UNEXPRESSIBLE)
            if not agents_capped(host, options['steps']):
                return dict(state='blocked', reason=AGENT_STEPS_UNCAPPED)
        reason = routing_launch.preflight(service.handle(json.dumps(dict(v=1, op='status')).encode()))
        if reason is not None:
            return dict(state='blocked', reason=reason)
        answer = health(host)
        if answer is None:
            return dict(state='blocked', reason=HOST_UNHEALTHY)
        if enforcement(host, answer) != 'enforced':
            return dict(state='blocked', reason=PROFILE_NOT_ENFORCED)
        gate = _Gate(service, host, options)
        attempt = _Attempt(host, gate, prompt, idle_timeout)
        detached = None
        listed = catalog(host)  # read once: the host's harnesses before `prepare` and the admitted check after it
        try:
            result = routing_launch.launch(gate, host='opencode', assignment=assignment, job=job, catalog=listed,
                                           transport=attempt, workflow=workflow, constraints=constraints,
                                           planned=planned, profile=profile, settings_path=service.settings_path,
                                           source=source, harnesses=routing_launch.host_harnesses(
                                               'opencode', listed, dollar=API_FUNDING_QUALIFIED),
                                           before_prepare=lambda request: opencode_subscription.refresh(  # F4.5 Go
                                               service, lambda: host.request('GET', opencode_subscription.USAGE_PATH),
                                               request, now=int(time.time())))
        except BaseException:
            # An interrupt (KeyboardInterrupt, the command's signal exception) passes the core's `except Exception`;
            # close or mark the hold it leaves before re-raising.
            _interrupted(service, gate)
            raise
        finally:
            detached = attempt.detach()  # after `record complete`: `descendants` answers only while attached
        return _finish(result, gate, attempt, detached, service.settings_path, source)
    finally:
        if owned:
            service.close()


# -- activation-gate self-check ----------------------------------------------------------------------------------
class _SelfCheckHost:
    """A fake OpenCode server for the self-check: attach goes through the real routing command's strict check."""

    def __init__(self, shim, directory):
        self.shim, self.directory, self.created, self.tokens, self.sessions = shim, directory, 0, [], {}
        self.healthy, self.denies, self.attach_ok = True, list(adapter.MANAGED_DENIES), True
        self.models = [dict(providerID=SELF_CHECK_PROVIDER, id='self-check-model', variants=['high'])]

    def rpc(self, method, payload):
        if method == 'health':
            return (200, dict(state='ok', managed_denies=list(self.denies))) if self.healthy else (500, None)
        self.tokens.append(payload.get('authority'))
        if method == 'attach':
            facts = dict(provider=SELF_CHECK_PROVIDER, model='self-check-model', variant='high',
                         harness=routing_launch.shim_harness(SELF_CHECK_PROVIDER),
                         kind='primary', funding_pool=payload['funding_pool'])
            answer = self.shim.handle(json.dumps(dict(v=1, op='check', attempt_id=payload['attempt_id'],
                                                      session_id=payload['session_id'], authority=payload['authority'],
                                                      request=facts)).encode())
            ok = self.attach_ok and answer.get('authority') == 'verified'
            return (200, dict(state='attached')) if ok else (500, None)
        if method == 'descendants':
            return 200, dict(state='ok', root=payload['session_id'], descendants=[], truncated=False, complete=True,
                             reason=None)
        return 200, dict(state='detached')

    def request(self, method, path, body=None, query=None):
        now, parts = int(time.time() * 1000), path.strip('/').split('/')
        tokens = dict(input=1, output=1, reasoning=0, cache=dict(read=0, write=0))
        if path == '/api/info':
            return 200, dict(version=adapter.QUALIFIED_VERSION)
        if path == '/api/model':
            return 200, dict(data=self.models)
        if path == '/api/session/active':
            return 200, dict(data={})
        if path == '/api/session':
            if method == 'POST':
                self.created += 1
                self.sessions[body['id']] = []
                return 200, dict(data=dict(id=body['id']))
            return 200, dict(data=[dict(id=sid, parentID=None) for sid in self.sessions])
        if len(parts) == 3:
            return 200, dict(data=dict(id=parts[2], parentID=None, tokens=tokens, cost=0))
        if parts[3] == 'prompt':
            self.sessions[parts[2]] += [
                dict(id=body['id'], type='user', time=dict(created=now)),
                dict(id='msg_selfcheckassistant', type='assistant', finish='stop', cost=0, tokens=tokens,
                     model=dict(id='self-check-model', providerID=SELF_CHECK_PROVIDER, variant='high'),
                     time=dict(created=now, completed=now + 1)),
                dict(id='msg_selfcheckidle', type='idle', outcome='succeeded', time=dict(created=now + 2))]
            return 200, dict(data=dict(id=body['id']))
        page = [] if query and 'cursor' in query else list(self.sessions.get(parts[2], []))
        return 200, dict(data=page, cursor=dict(previous=None, next=None if not page else 'n'))


_SELF_CHECK_CHILD = False  # set only by SELF_CHECK_CHILD's code, never from the environment or a request
# The self-check's provider: one a shipped subscription path names, so the host's harness set reaches `prepare`.
SELF_CHECK_PROVIDER = 'openai'
SELF_CHECK_MARK = 'opencode-launcher-self-check:'
SELF_CHECK_CHILD = (
    'import json, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from runtime import opencode_launcher\n'
    'opencode_launcher._SELF_CHECK_CHILD = True\n'
    'print(opencode_launcher.SELF_CHECK_MARK + json.dumps(opencode_launcher._self_check_in_child(sys.argv[2])))\n')


def managed_self_check(workdir):
    """C.3 design 4.2 capability "OpenCode launcher": `[]` when it holds, else fixed failure codes. Against a fake
    server and the real routing command on stores in `workdir`, every refusal happens before a session is created
    and no token appears in output. It swaps module globals, so it runs in a child of this interpreter."""
    root = routing_launch._sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    for name in ('home', 'tmp'):
        (root / name).mkdir(mode=0o700)
    env = dict(PATH='/usr/bin:/bin', HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), LANG='C.UTF-8')
    package = Path(__file__).resolve().parents[1]
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', SELF_CHECK_CHILD, str(package), str(root / 'check')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                             cwd=root, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=routing_launch.SELF_CHECK_BUDGET)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.communicate()
        return ['self_check_timeout']
    lines = [line for line in output.decode('utf-8', 'replace').splitlines() if line.startswith(SELF_CHECK_MARK)]
    try:
        codes = json.loads(lines[-1][len(SELF_CHECK_MARK):])
    except (IndexError, ValueError):
        return ['self_check_error']
    if type(codes) is not list or not all(type(code) is str for code in codes):
        return ['self_check_error']
    return codes if child.returncode == 0 or codes else ['self_check_error']


def _self_check_in_child(workdir):
    """The check body. It refuses to run unless its child entry point set `_SELF_CHECK_CHILD`."""
    if _SELF_CHECK_CHILD is not True:
        return ['self_check_not_in_child']
    root = routing_launch._sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    from runtime import routing_settings
    from scripts import routing as command  # the gate puts the plugin package root first on sys.path
    checkout = root / 'checkout'
    checkout.mkdir(mode=0o700)
    picked = choice(SELF_CHECK_PROVIDER, 'self-check-model', 'high')
    spec = dict(route='self-check', status='qualified', billing_class='included_subscription', pool='self-check',
                account='self-check', demand={'5h': 1}, binding_enforceable=True, window_unit=WINDOW_UNIT,
                **{key: picked[key] for key in routing.ROUTE_IDENTITY})
    source = routing_observations.StaticSource([spec], lambda pool, at: [dict(
        id='5h', limit=1000, used=0, length=18000, reset_at=at + 3600, rolling=False, observed_at=at,
        unit=WINDOW_UNIT)], billing=subscription_billing.double('self-check'))  # sandbox only: no real producer
    saved = routing_settings.managed_project, routing_observations.default_source
    routing_settings.managed_project = lambda path, *args, **kwargs: Path(path).resolve() == checkout
    routing_observations.default_source = lambda *args, **kwargs: source
    settings, ledger = root / 'routing' / 'settings.json', root / 'usage' / 'organizations.sqlite3'
    service = command.Service(settings, ledger, project=checkout)
    shim = command.Service(settings, ledger, serving=True)
    codes, outputs = [], []
    try:
        if service.handle(json.dumps(dict(v=1, op='set', authority_required=True)).encode()).get('state') != 'updated':
            return ['self_check_store_unavailable']
        cases = (('unhealthy', dict(healthy=False), HOST_UNHEALTHY, 'host_not_checked'),
                 ('advisory', dict(denies=[]), PROFILE_NOT_ENFORCED, 'profile_not_checked'),
                 ('catalog', dict(models=[dict(providerID=SELF_CHECK_PROVIDER, id='self-check-model', variants=['low'])]),
                  routing_launch.CATALOG_MISMATCH, 'catalog_not_checked'),
                 ('attach', dict(attach_ok=False), ATTACH_FAILED, 'attach_not_checked'))
        for name, knobs, reason, code in cases + (('launch', {}, None, 'managed_launch_failed'),):
            host = _SelfCheckHost(shim, str(checkout))
            host.__dict__.update(knobs)
            result = launch(checkout, host=host, service=service, prompt='self-check', job=routing_launch.unplanned_job(
                'software-engineer', routing_launch.assignment_scope(checkout), 'self-check'),
                assignment=dict(attempt_id=name, task='self-check-' + name, scope=routing_launch.assignment_scope(
                    checkout), choice=picked))
            outputs.append(json.dumps(result))
            if reason is not None and (result.get('reason') != reason or host.created):
                codes.append(code)
            if reason is None and (result.get('state') != 'recorded' or host.created != 1):
                codes.append(code)
            if any(token and token in text for token in host.tokens for text in outputs):
                codes.append('token_exposed')
    except Exception:  # noqa: BLE001 - any failure is a fixed code, never a traceback
        codes.append('self_check_error')
    finally:
        routing_settings.managed_project, routing_observations.default_source = saved
        service.close()
        shim.close()
    return sorted(set(codes))
