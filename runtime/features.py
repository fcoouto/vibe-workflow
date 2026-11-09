"""Feature manifests and project settings: one resolver for every feature's default and effective state."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

from runtime import settings_store
from runtime.settings_store import SettingsError


PACKAGE = Path(__file__).resolve().parents[1]
REQUIRED = {"manifest_version", "id", "summary", "skills", "default", "settings", "scopes", "consent",
            "control", "requires", "procedure"}
OPTIONAL = {"adapter", "source"}
CONTROLS = {"settings", "adapter", "fixed"}
CONSENT = {"none", "transmits", "spends"}
# Only the project layer exists; user and checkout scopes are reserved by the design.
SCOPES = {"project"}
TYPES = {"boolean": (bool,), "integer": (int,), "number": (int, float), "string": (str,), "array": (list,),
         "object": (dict,)}
FEATURE_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
LIMIT = 2**53


def _typed(value, kind):
    if not isinstance(value, TYPES[kind]) or (kind != "boolean" and type(value) is bool):
        return False
    return kind not in ("integer", "number") or abs(value) < LIMIT


def _relative(value, field, name):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(f"Manifest {name}: {field} must be a path inside the package")


def _manifest(path):
    name = path.stem
    raw = settings_store.read_bytes(path)
    value = settings_store.parse_object(raw or b"", f"manifest {name}")
    keys = set(value)
    if not REQUIRED <= keys or keys - REQUIRED - OPTIONAL:
        raise ValueError(f"Manifest {name}: needs exactly {sorted(REQUIRED)} plus optional {sorted(OPTIONAL)}")
    if value["manifest_version"] != 1 or value["id"] != name or not FEATURE_ID.fullmatch(name):
        raise ValueError(f"Manifest {name}: unsupported version or an id that does not match its file name")
    if value["control"] not in CONTROLS or value["consent"] not in CONSENT:
        raise ValueError(f"Manifest {name}: unknown control or consent")
    if value["control"] == "fixed" and value["consent"] != "none":
        raise ValueError(f"Manifest {name}: a fixed feature cannot need consent")
    if not isinstance(value["default"], dict) or set(value["default"]) != {"enabled"} \
            or type(value["default"]["enabled"]) is not bool:
        raise ValueError(f"Manifest {name}: default must be {{\"enabled\": true|false}}")
    if not isinstance(value["scopes"], list) or not set(value["scopes"]) <= SCOPES:
        raise ValueError(f"Manifest {name}: scopes must be within {sorted(SCOPES)}")
    for field in ("skills", "requires"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            raise ValueError(f"Manifest {name}: {field} must be a list of names")
    if not isinstance(value["summary"], str) or not isinstance(value["settings"], dict):
        raise ValueError(f"Manifest {name}: summary must be text and settings an object")
    for key, spec in value["settings"].items():
        if not isinstance(spec, dict) or set(spec) != {"type", "default"} or spec["type"] not in TYPES \
                or not _typed(spec["default"], spec["type"]):
            raise ValueError(f"Manifest {name}: setting {key} needs a known type and a default of that type")
    _relative(value["procedure"], "procedure", name)
    if value["control"] == "adapter":
        source = value.get("source")
        if not isinstance(source, dict) or set(source) != {"store", "scope"}:
            raise ValueError(f"Manifest {name}: an adapter feature describes its source store and scope")
    return value, raw


def _load(package):
    found = {path.stem: _manifest(path) for path in sorted((Path(package) / "features").glob("*.json"))}
    return {name: value for name, (value, _) in found.items()}, \
        hashlib.sha256(b"".join(raw + b"\0" for _, raw in found.values())).hexdigest()


def manifests(package=PACKAGE):
    return _load(package)[0]


def checkout_root(cwd):
    result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise ValueError(f"{cwd} is not inside a Git checkout")
    return Path(result.stdout.strip()).resolve()


def _entry(manifest, entry):
    """Validate one feature's project entry; return (enabled or None, values) or raise SettingsError."""
    if not isinstance(entry, dict) or set(entry) - {"enabled", "values"}:
        raise SettingsError("an entry has only enabled and values")
    if "enabled" in entry and type(entry["enabled"]) is not bool:
        raise SettingsError("enabled must be true or false")
    values = entry.get("values", {})
    if not isinstance(values, dict):
        raise SettingsError("values must be an object")
    for key, value in values.items():
        spec = manifest["settings"].get(key)
        if spec is None:
            raise SettingsError(f"unknown setting {key}")
        if not _typed(value, spec["type"]):
            raise SettingsError(f"setting {key} must be of type {spec['type']}")
    return entry.get("enabled"), values


def _feature(manifest, entry, broken, legacy):
    default = manifest["default"]["enabled"]
    values = {key: spec["default"] for key, spec in manifest["settings"].items()}
    result = dict(control=manifest["control"], consent="not_required" if manifest["consent"] == "none" else "missing",
                  desired=dict(enabled=default, source="default"), observed="unknown", procedure=manifest["procedure"])
    if manifest["control"] == "settings":
        result["values"] = values
    if manifest["control"] != "settings":
        effective = default if manifest["control"] == "fixed" else None
        reason = "ignored_override" if entry is not None else ("default" if manifest["control"] == "fixed" else "unchecked")
        return dict(result, effective=effective, reason=reason)
    if broken is not None:
        return dict(result, effective=False, reason="invalid_settings", error=broken)
    if entry is not None:
        enabled, overrides = entry
        if enabled is not None:
            result["desired"] = dict(enabled=enabled, source="project")
        result["values"] = dict(values, **overrides)
        reason = "project"
    elif legacy is not None:
        result["desired"] = dict(enabled=legacy, source="legacy")
        reason = "migration_pending"
    else:
        reason = "default"
    effective = result["desired"]["enabled"]
    if effective and result["consent"] == "missing":
        return dict(result, effective=False, reason="needs_consent")
    return dict(result, effective=effective, reason=reason)


def resolve(cwd, package=PACKAGE):
    """Report every feature's desired, consent, effective and observed state; never writes."""
    from runtime import features_migration

    root = checkout_root(cwd)
    loaded, manifest_digest = _load(package)
    errors, entries, broken = [], {}, {}
    try:
        settings, raw = settings_store.read(root)
        settings_input = settings_store.digest(raw)
    except SettingsError as error:
        settings, raw = None, None
        # Only bytes from the validated read path are hashed; an invalid file contributes its error.
        settings_input = "invalid:" + str(error)
        errors.append(str(error))
        broken = {name: str(error) for name in loaded}
    for name, entry in (settings or {}).get("features", {}).items():
        if name not in loaded:
            errors.append(f"Unknown feature in settings: {name}")
            continue
        if loaded[name]["control"] != "settings":
            entries[name] = entry
            continue
        if "project" not in loaded[name]["scopes"]:
            errors.append(f"{name}: not settable in project settings")
            broken[name] = "not settable in project settings"
            continue
        try:
            entries[name] = _entry(loaded[name], entry)
        except SettingsError as error:
            errors.append(f"{name}: {error}")
            broken[name] = str(error)
    legacy = {}
    if "workstreams" in loaded:
        found = features_migration.legacy_workstreams(root)
        if found is not None and "error" in found:
            errors.append(found["error"])
            broken.setdefault("workstreams", found["error"])
        elif found is not None:
            legacy["workstreams"] = found["value"]
    report = {name: _feature(manifest, entries.get(name), broken.get(name), legacy.get(name))
              for name, manifest in loaded.items()}
    changed = True
    while changed:
        changed = False
        for name, manifest in loaded.items():
            if report[name]["effective"] and any(report.get(item, {}).get("effective") is not True
                                                 for item in manifest["requires"]):
                report[name] = dict(report[name], effective=False, reason="prerequisite_missing")
                changed = True
    inputs = dict(settings=settings_input, legacy=legacy, manifests=manifest_digest)
    return dict(ok=not errors, checkout=str(root), settings=settings_store.SETTINGS,
                revision=settings["revision"] if settings else 0, settings_digest=settings_store.digest(raw),
                fingerprint="sha256:" + hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
                errors=errors, warnings=[], features=report)


def summary(report, only=None):
    parts = []
    for name, feature in report["features"].items():
        if only is not None and name != only:
            continue
        if feature["reason"] == "invalid_settings":
            state = "unavailable"
        elif feature["effective"] is None:
            state = "unchecked"
        else:
            state = "on" if feature["effective"] else "off"
        source = feature["desired"]["source"]
        note = " (needs consent)" if feature["reason"] == "needs_consent" else (
            f" ({source})" if feature["control"] == "settings" and source != "default" else "")
        parts.append(f"{name} {state}{note}")
    return "Features: " + " · ".join(parts)


def change(cwd, name, *, enabled=None, values=None, unset=None, package=PACKAGE):
    """Write one settings-controlled feature's project entry; refuse anything else."""
    loaded = manifests(package)
    manifest = loaded.get(name)
    if manifest is None:
        raise SettingsError(f"Unknown feature: {name}")
    if manifest["control"] != "settings" or manifest["consent"] != "none" or "project" not in manifest["scopes"]:
        raise SettingsError(f"{name} is not managed through project settings; use {manifest['procedure']}")
    if values:
        _entry(manifest, dict(values=values))
    if unset is not None and unset not in manifest["settings"]:
        raise SettingsError(f"{name} has no setting {unset}")

    def apply(current):
        features = current["features"]
        entry = dict(features.get(name, {}))
        _entry(manifest, entry)
        if enabled is not None:
            entry["enabled"] = enabled
        merged = dict(entry.get("values", {}), **(values or {}))
        merged.pop(unset, None)
        entry.pop("values", None)
        if merged:
            entry["values"] = merged
        if entry:
            features[name] = entry
        else:
            features.pop(name, None)
        return current

    warnings, backup = settings_store.update(checkout_root(cwd), apply)
    return dict(resolve(cwd, package), warnings=warnings, backup=backup)


def migrate(cwd, action):
    from runtime import features_migration
    return features_migration.run(checkout_root(cwd), action)


def undo(cwd, backup):
    return dict(ok=True, **settings_store.undo(checkout_root(cwd), backup))
