# Example layouts

Every tree here is an example, not a rule. A project's own layout, a framework's required
paths and a tool's conventions come first. Use these only when nothing else decides.

## Node or TypeScript app (example, not rule)

```
src/
  orders/        # one folder per domain: code, its types and its helpers
  billing/
  shared/        # only what two or more domains use
tests/           # follows the project's convention: mirrored, colocated or flat
docs/
scripts/         # command entry points and one-off tools
package.json
```

A new test goes beside its domain's tests. A new doc goes in `docs/`. A shipped asset goes
where the package's `files` list expects it.
Add it to that list when the list names files one by one.

## Python package (example, not rule)

```
src/
  inventory/     # the package; one module per responsibility
    __init__.py
    stock.py
    pricing.py
tests/
docs/
scripts/
pyproject.toml
```

A new test goes in `tests/` next to the one for its module. A new doc goes in `docs/`. Data
the package ships goes inside the package folder.

## Mixed JavaScript and Python plugin (example, not rule)

```
index.js         # the entry point named in package.json stays at the root
src/             # the plugin's JavaScript modules
runtime/         # Python library that ships; imported, never run directly
scripts/         # command entry points
skills/<id>/     # one folder per skill: SKILL.md and references/
assets/         # shipped non-code files, listed in the export inventory
tests/
docs/
```

A new test goes in `tests/`. A new doc goes in `docs/`. A shipped asset goes in
`assets/`, listed in the package's export inventory.

## Monorepo (example, not rule)

```
packages/
  api/           # each package has its own src/, tests/ and manifest
  web/
  shared/
tools/           # repo-wide scripts
docs/
```

A new file belongs to one package. Code two packages need goes in `shared/`, not copied.
A new test goes in its package's `tests/`. A repo-wide doc goes in `docs/`; a package's
doc stays in the package. A shipped asset goes inside the package that ships it.

## Folders a convention owns

Leave these where their tool or framework expects them: Next.js `app/` and `pages/`, a
Python `tests/` that the test runner discovers, `skills/<id>/SKILL.md`, `.github/`,
`node_modules/` and other generated folders. A folder holding one file is correct when
the ecosystem requires it.
