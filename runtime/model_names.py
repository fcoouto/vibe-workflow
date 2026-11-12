"""Model names as data: read from the compiled strategy table in user state, never written in code.

The table (`strategy-table.json` beside the routing settings) is the interim catalog until the downloaded catalog
exists: it supplies the cheapest priced model for a provider and the display names a catalog entry declares.
No table, or an unusable one, means no name: callers refuse or fall back; nothing is guessed.
"""
from pathlib import Path
import re

from runtime import routing_paths


def default_settings():
    """The user's routing settings file; the table lives beside it (as `scripts/routing.py` resolves it)."""
    return Path.home() / '.local/state/vibe-workflow/routing/settings.json'


def table(settings_path=None):
    """The compiled table beside `settings_path` (default: the user's), or {} when absent or unusable."""
    return routing_paths._table(settings_path or default_settings())


def display_name(model, settings_path=None):
    """The catalog's display name for `model` from the table's `display_names`, or None (show the vendor's)."""
    names = table(settings_path).get('display_names')
    name = names.get(model) if type(names) is dict and type(model) is str else None
    return name if type(name) is str and name else None


def cheapest(provider, settings_path=None):
    """The model of the cheapest priced strategy for `provider` in the table, or None. Ties go to the model id."""
    rows = table(settings_path).get('rows')
    priced = sorted((item['list_price_micros'], item['model'])
                    for strategies in (rows.values() if type(rows) is dict else ()) if type(strategies) is list
                    for item in strategies if type(item) is dict and item.get('provider') == provider
                    and type(item.get('list_price_micros')) is int and type(item.get('model')) is str and item['model'])
    return priced[0][1] if priced else None


# The areas that must name no model (design 5.14): docs/, tests/ and assets/ are outside, with invented names in tests.
COVERED = ('runtime', 'scripts', 'policies', 'clients', 'skills', 'templates')
OUTSIDE = ('docs', 'tests', 'assets')
SKIPPED = {'.git', '__pycache__', 'node_modules', 'venv', 'site-packages'}


def names(settings_path=None):
    """Every `model` in the table's rows and every display name, as the table spells them."""
    value = table(settings_path)
    rows, shown = value.get('rows'), value.get('display_names')
    found = {item['model'] for strategies in (rows.values() if type(rows) is dict else ()) if type(strategies) is list
             for item in strategies if type(item) is dict and type(item.get('model')) is str and item['model']}
    found |= {name for name in (shown.values() if type(shown) is dict else ()) if type(name) is str and name}
    return sorted(found)


def _covered(root):
    from runtime.package_manifest import excluded
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or SKIPPED & set(relative.parts) or excluded(relative.as_posix()):
            continue
        top = relative.parts[0]
        if top in COVERED or relative.as_posix() == 'AGENTS.md' or path.suffix == '.json' and top not in OUTSIDE:
            yield relative, path


def check(root, settings_path=None):
    """'passed', or 'not_checked' when the table names no model; a ValueError names the first file and line where a
    name appears as a whole token, ignoring case."""
    found = names(settings_path)
    if not found:
        return 'not_checked'
    spelled = {name.casefold(): name for name in found}
    pattern = re.compile(r'(?<![\w.-])(%s)(?![\w-]|\.\w)' % '|'.join(
        re.escape(name) for name in sorted(found, key=len, reverse=True)), re.IGNORECASE)
    for relative, path in _covered(Path(root)):
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            match = pattern.search(line)
            if match:
                raise ValueError('%s:%d names the model %s; take it from the catalog or table data instead'
                                 % (relative.as_posix(), number, spelled.get(match.group(1).casefold(), match.group(1))))
    return 'passed'
