"""The activation gate of `scripts/routing.py set managed_projects` (C.3 design 4.2), moved out of that command.

A project joins `managed_projects` only when one of its enrolled clients has a managed delegation path
(`host_refusal`) and every capability's check passes (`activation_gate`). The command re-exports every name it
used to define here; it alone runs the gate, in a one-shot call. Removal never runs it.
"""
import importlib
import json
import os
from pathlib import Path
import re
import sys


# Activation gate (C.3 design 4.2): a project joins `managed_projects` only when every capability's check passes. In a
# child: `module` `<module>:<function>(workdir)`, `script` PACKAGE/<target>'s `managed_self_check(workdir)`, `paths`
# `<module>:<function>(settings path)`, `heading` a `Managed projects` heading; in process: `sources` (F4R-03) and
# `authority` (E2).
CAPABILITIES = (
    ('collector_and_launcher', 'module', 'runtime.routing_launch:managed_self_check'),  # C3-1
    ('route_manifest', 'paths', 'runtime.routing_paths:gate'),  # F4: path rules, qualifications and the table
    ('subscription_sources', 'sources', 'runtime.native_routing:activation_readiness'),  # F4R-03: per managed host
    ('native_delegation', 'module', 'runtime.delegation_routing:managed_self_check'),  # C3-2
    ('opencode_launcher', 'module', 'runtime.opencode_launcher:managed_self_check'),  # C3-3
    ('openrouter_proposals', 'script', 'scripts/openrouter_proposals.py'),  # C3-4a
    ('openrouter_workers', 'script', 'scripts/openrouter_workers.py'),  # C3-4b
    ('api_usage', 'module', 'runtime.usage_routing:managed_self_check'),  # C3-5
    ('instructions', 'heading', 'policies/03-agent-delegation.md'),  # C3-6
    ('authority_required', 'authority', 'ledger:authority_required'),  # E2: in process, the service's own ledger
)
# Clients with a managed delegation path (policy 03 `Managed projects`): codex (C3-2, the native bridge), opencode (the
# launcher, C3-3), claude-code (the native bridge, F4.6); a project enrolled only for others: `advisory_host_unmanaged`.
MANAGED_HOSTS = ('codex', 'opencode', 'claude-code')
SELF_CHECK_TIMEOUT = 10
SELF_CHECK_REAP = 5  # bound on collecting a killed check's output; a descendant outside its group cannot hold the gate
SELF_CHECK_MARK = 'managed-self-check:'
SELF_CHECK_CODE = re.compile(r'[a-z0-9_]{1,64}')
# Runs in the child: the package root (argv[1]) first on the import path, never the project checkout.
SELF_CHECK_CHILD = r'''
import sys
sys.path.insert(0, sys.argv[1])
import importlib, importlib.util, json, os, re
package, kind, target, workdir, settings = sys.argv[1:6]
def run():
    if kind in ('module', 'paths'):
        name, _, function = target.partition(':')
        try:
            check = getattr(importlib.import_module(name), function)
        except (ImportError, AttributeError):
            return ['self_check_missing']
        return check(settings if kind == 'paths' else workdir)
    path = os.path.join(package, target)
    if not os.path.isfile(path):
        return ['self_check_missing']
    if kind == 'script':
        spec = importlib.util.spec_from_file_location('managed_self_check_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        check = getattr(module, 'managed_self_check', None)
        return ['self_check_missing'] if check is None else check(workdir)
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    return [] if re.search(r'^#+ Managed projects[ \t]*$', text, re.M) else ['heading_missing']
try:
    result = run()
except Exception:
    result = ['self_check_error']
sys.stdout.write('\n' + %r + json.dumps(result, default=str) + '\n')
''' % SELF_CHECK_MARK


def _package():
    from runtime import state  # the plugin package root, never the project checkout
    return state.PACKAGE


def _self_check(kind, target, settings_path):
    """One capability's self-check in a child of this interpreter, in a fresh owner-only sandbox with a fixed
    environment, deleted afterwards; `paths` checks read the service's absolute `settings_path`, the others ignore
    it. Returns its failure codes ([] when it passes)."""
    import shutil, signal, subprocess, tempfile  # noqa: E401 - only `set managed_projects` runs self-checks
    folder = Path(tempfile.mkdtemp(prefix='routing-self-check-')).resolve()
    try:
        for name in ('home', 'tmp', 'work'):
            (folder / name).mkdir(mode=0o700)
        env = dict(PATH='/usr/bin:/bin', HOME=str(folder / 'home'), TMPDIR=str(folder / 'tmp'), LANG='C.UTF-8')
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', SELF_CHECK_CHILD, str(_package()), kind, target,
                                  str(folder / 'work'), str(settings_path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, env=env, cwd=folder / 'work', start_new_session=True)
        try:
            output, _ = child.communicate(timeout=SELF_CHECK_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)  # the whole process group, not just the child
            except OSError:
                pass
            try:
                child.communicate(timeout=SELF_CHECK_REAP)
            except subprocess.TimeoutExpired:
                child.stdout.close()
                child.wait()
            return ['self_check_timeout']
        lines = [line for line in output.decode('utf-8', 'replace').splitlines() if line.startswith(SELF_CHECK_MARK)]
        try:
            codes = json.loads(lines[-1][len(SELF_CHECK_MARK):])
        except (IndexError, ValueError):
            return ['self_check_error']
        if type(codes) is not list or len(codes) > 16 or not all(
                type(code) is str and SELF_CHECK_CODE.fullmatch(code) for code in codes):
            return ['self_check_invalid']
        return codes if child.returncode == 0 or codes else ['self_check_error']
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def host_refusal(delegation_routing, projects):
    """Refuse adding a project none of whose configured or active clients is in MANAGED_HOSTS. A project with neither
    a canonical record nor a resolving legacy codex scope is `not_enrolled`; an unreadable enrolment fails closed
    (`enrolment_unavailable`). Read-only, before the self-checks and any ledger lock. Returns the first refusal as
    (reason, projects), or None."""
    unreadable, unenrolled, advisory = [], [], []
    for project in projects:
        try:
            hosts = delegation_routing.enrolled_hosts(project)
        except delegation_routing.NotEnrolled:
            unenrolled.append(project)
            continue
        except Exception:  # noqa: BLE001 - e.g. RuntimeScopeError or OSError: unknown enrolment never joins
            unreadable.append(project)
            continue
        if not set(hosts) & set(MANAGED_HOSTS):
            advisory.append(project)
    for reason, found in (('enrolment_unavailable', unreadable), ('not_enrolled', unenrolled),
                          ('advisory_host_unmanaged', advisory)):
        if found:
            return reason, found
    return None


def _authority_codes(authority):
    """E2: every managed prepare refuses `authority_not_required` until the ledger's one-way switch is on
    (`routing.py set {"authority_required": true}`), so a project joins only after it, never half-enabled. `authority`
    is the service's reader of that switch; a ledger that cannot be read is `ledger_unavailable`."""
    from runtime.routing_launch import AUTHORITY_NOT_REQUIRED
    try:
        return [] if authority is not None and authority() is True else [AUTHORITY_NOT_REQUIRED]
    except Exception:  # noqa: BLE001 - an unreadable ledger never admits a project
        return ['ledger_unavailable']


def activation_gate(settings_path, projects=(), authority=None):
    """The failing capabilities as {name: codes}; empty when every check passes. `authority` reads the adding
    service's ledger switch (E2); without it that capability fails."""
    failures = {}
    for name, kind, target in CAPABILITIES:
        if kind == 'sources':
            module, _, function = target.partition(':')
            codes = getattr(importlib.import_module(module), function)(list(projects), settings_path)
        elif kind == 'authority':
            codes = _authority_codes(authority)
        else:
            codes = _self_check(kind, target, settings_path)
        if codes:
            failures[name] = codes
    return failures
