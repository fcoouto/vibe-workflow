"""Claude Code setup receipts: hook-relevant configuration reading and the staged content collector.

Kept out of setup_observation, which shares its helpers; this module only adds the Claude client's rules.
"""
import hashlib
import os
import sys
from pathlib import Path

from runtime import onboarding as d3, startup

# Claude auto-loads plugin components by name (agents/, commands/, .mcp.json, settings.json, bin/ and more), so
# everything is hashed except these top-level entries, which Claude never loads. A new component changes the digest.
UNLOADED = frozenset({'docs', 'assets', 'tests', '.agents', '.codex-plugin', '.git', '.gitignore',
                      'README.md', 'AGENTS.md', 'LICENSE'})
CONFIGURATION = frozenset({'package_root', 'claude_config', 'plugin_name', 'roots', 'digest', 'hook_owner'})


def settings_paths(config, root, budget):
    """Documented file-backed Claude settings sources, in precedence order; managed policy is files only."""
    from runtime import setup_observation as so
    from runtime.adapters import claude_code
    paths = [('user', Path(config['claude_config'])/'settings.json'), ('project', root/'.claude/settings.json'),
             ('local', root/'.claude/settings.local.json')]
    directory = claude_code.MANAGED_DIRECTORIES.get(sys.platform)
    if directory is not None:
        managed = Path(directory)
        paths.append(('managed', managed/'managed-settings.json'))
        drop = managed/'managed-settings.d'
        if drop.is_dir():
            paths.extend(('managed', child) for child in sorted(so._children(drop, budget)) if child.suffix == '.json')
    return paths


def projection(scope, value, config):
    """Only the documented hook controls and this plugin's enablement; no other settings are retained."""
    d3._require(isinstance(value, dict), 'Observable Claude configuration is not an object')
    result = {}
    for key in ('disableAllHooks', 'allowManagedHooksOnly'):
        if key in value:
            result[key] = value[key]
    if value.get('hooks') is not None:
        d3._require(isinstance(value['hooks'], dict), 'Unsupported Claude hook configuration')
        result['hooks'] = d3._digest(value['hooks'])
    enabled = value.get('enabledPlugins') or {}
    d3._require(isinstance(enabled, dict), 'Unsupported Claude plugin configuration')
    selected = {key: enabled[key] for key in sorted(enabled) if str(key).split('@')[0] == config['plugin_name']}
    if selected:
        result['enabledPlugins'] = selected
    return result


def current_configuration(config, cwd, budget=None, *, bundle_budget=None, runtime_sources=None):
    """Local observable inputs only: hook-relevant settings, the plugin bundle and its hook definitions."""
    from runtime import setup_observation as so
    budget = budget if budget is not None else so._budget()
    root = startup._canonical_path(str(cwd))
    selected = Path(config['roots'][0])
    d3._require(root.is_relative_to(selected), 'Configuration observation left its selected checkout')
    observed = []
    for scope, path in settings_paths(config, selected, budget):
        raw = so._read(path, budget, absent=True, limit=256 * 1024)
        value = {} if raw is None else startup.json_object(raw)
        observed.append(dict(path=str(path), scope=scope, values=projection(scope, value, config)))
    package = Path(config['package_root']); source = package/'hooks/claude-code.json'
    hook_owner = dict(root=str(package), binding=d3._filesystem_binding(package), source=str(source),
        sha256=hashlib.sha256(so._read(source, budget)).hexdigest())
    scan = bundle_budget if bundle_budget is not None else budget
    scan['paths'] = budget['paths']
    bundle, runtime_digest = so._package_scan(package, scan, runtime_sources=runtime_sources, label=b'claude-plugin-bundle-v1', exclude=UNLOADED)
    budget['paths'] = scan['paths']
    material = dict(files=observed, bundle=bundle)
    result = dict(digest=d3._digest(material), hook_owner=hook_owner, material=material)
    if runtime_sources is not None:
        result['runtime_digest'] = runtime_digest
    return result


def collect_content(observed, identity, transaction_id, verified_assessment, *, predecessor=None):
    """A staged Claude receipt from facts the client itself reported; nothing beyond them is verified."""
    from runtime import setup_observation as so
    from runtime.adapters import claude_setup
    from runtime.state import _runtime_sources, PACKAGE
    d3._require(predecessor is None, 'Claude successors need a managed behavior source, which is not available yet')
    binding = observed['binding']
    d3._object(binding, ('accepted_scope_digest','attempt_ref','transaction_id'))
    d3._require(binding['accepted_scope_digest'] == d3._digest(identity) and binding['transaction_id'] == transaction_id,
        'Native witness belongs to another setup operation or scope')
    startup._canonical_ref(binding['attempt_ref'])
    native = observed['native']
    d3._object(native, ('surface', 'version', 'executable'))
    d3._require(native['surface'] == claude_setup.SURFACE, 'This setup source does not qualify the selected frontend')
    startup._canonical_text(native['version'], 128); startup._canonical_path(native['executable'])
    root = startup._canonical_path(identity['repository_root']); package = startup._canonical_path(observed['package_root'])
    budget = so._budget()
    assessment = so._assessment(verified_assessment, staged=True)
    claude_setup.verify_facts(observed['claude_observation'], observed['observations'], observed['bootstrap'])
    from runtime.changes import _evidence
    d3._require(_evidence(observed['bootstrap'], many=True) and set(observed['observations']) <= set(so.BEHAVIOR_CASES),
        'Genuine frontend observations are missing')
    for case in assessment['cases']:
        actual = observed['observations'].get(case['id'], [])
        _evidence(actual, many=True)
        if case['status'] == 'verified':
            d3._require(actual and all(proof in case['evidence'] for proof in actual),
                'A verified behavior lacks its actual frontend observations')
    d3._require(observed['claude_observation']['init']['claude_version'] == native['version']
        and observed['claude_observation']['init']['cwd'] == str(root)
        and observed['claude_observation']['init']['plugin']['path'] == str(package),
        'The observed run differs from the selected checkout, package or version')
    d3._require(identity['configuration']['policy_digest'] == startup.policy_digest(package, budget=budget),
        'Setup candidate policy/runtime identity differs')
    d3._require(d3._filesystem_binding(Path(identity['project_root'])) == identity['project_binding']
        and all(d3._filesystem_binding(Path(identity['repository_root' if key == 'root' else key])) == value
            for key, value in identity['bindings'].items()), 'Setup physical project/checkout identity differs')
    for dependency in identity['shared_policy']:
        d3._require(hashlib.sha256(so._read(Path(dependency['path']), budget)).hexdigest() == dependency['sha256'],
            'Setup shared instruction source differs')
    plugin_name = startup.json_object(so._read(package/'.claude-plugin/plugin.json', budget))['name']
    config = dict(package_root=str(package), claude_config=str(Path(os.environ.get('CLAUDE_CONFIG_DIR')
        or Path.home()/'.claude').resolve()), plugin_name=plugin_name, roots=[str(root), identity['primary_root']])
    current = current_configuration(config, root, budget,
        runtime_sources=[source.relative_to(PACKAGE) for source in _runtime_sources()])
    d3._require(identity['configuration']['runtime_digest'] == current['runtime_digest'],
        'Setup candidate policy/runtime identity differs')
    for row in current['material']['files']:
        values = row['values']
        d3._require(values.get('disableAllHooks') is not True and not any(value is False for value in
            values.get('enabledPlugins', {}).values()), 'Hooks or this plugin are disabled in the observed configuration')
        d3._require(row['scope'] != 'managed' or not values,
            'Managed hook or plugin policy is outside this personal setup source')
        d3._require(row['scope'] not in ('project', 'local') or 'hooks' not in values,
            'Project hooks could have produced the observed hook events')
    config.update(digest=current['digest'], hook_owner=current['hook_owner'])
    content = dict(version=2, kind='owner-observed-setup', transaction_id=transaction_id, identity=identity,
        native=dict(native), configuration=config, runs=[], assessment=assessment, predecessor=None,
        source=dict(binding=binding, bootstrap=observed['bootstrap'], observations=observed['observations'],
            pending_reason=observed['pending_reason'], claude_observation=observed['claude_observation']))
    d3._bounded(content)
    return so._SetupObservation(startup._canonical_freeze(content), so._ISSUER)


def check_receipt(content):
    """Per-client retained-observation rules: a Claude receipt is exactly its client's reported events."""
    client, source, keys = content['identity']['client'], content.get('source', {}), set(content['configuration'])
    if client != 'claude-code':
        d3._require('claude_observation' not in source and not keys & {'claude_config', 'plugin_name'},
            'Unsupported retained observation family')
        return
    d3._require(keys == CONFIGURATION, 'Claude setup configuration has unexpected fields')
    d3._require(content['version'] == 2 and 'claude_observation' in source and not {'native_origin', 'native_capture',
        'capture_reference', 'command_origin', 'command_origins', 'source_capture', 'native_safety', 'managed'} & set(source),
        'Claude setup needs its observed client events')
    from runtime.adapters import claude_setup
    try:
        claude_setup.verify_facts(source['claude_observation'], source['observations'], source['bootstrap'])
    except startup.StartupError:
        raise
    except LookupError:
        raise startup.StartupError('Invalid retained Claude Code observation') from None
