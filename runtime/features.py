"""Feature manifests and project settings: one resolver for every feature's default and effective state."""

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
    value = settings_store.parse_object(path.read_bytes(), f"manifest {name}")
    keys = set(value)
    if not REQUIRED <= keys or keys - REQUIRED - OPTIONAL:
        raise ValueError(f"Manifest {name}: needs exactly {sorted(REQUIRED)} plus optional {sorted(OPTIONAL)}")
    if value["manifest_version"] != 1 or value["id"] != name or not FEATURE_ID.fullmatch(name):
        raise ValueError(f"Manifest {name}: unsupported version or an id that does not match its file name")
    if value["control"] not in CONTROLS or value["consent"] not in CONSENT:
        raise ValueError(f"Manifest {name}: unknown control or consent")
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
    return value


def manifests(package=PACKAGE):
    return {path.stem: _manifest(path) for path in sorted((Path(package) / "features").glob("*.json"))}


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
    loaded = manifests(package)
    errors, entries, broken = [], {}, {}
    try:
        settings, raw = settings_store.read(root)
    except SettingsError as error:
        settings, raw = None, None
        errors.append(str(error))
        broken = {name: str(error) for name in loaded}
    for name, entry in (settings or {}).get("features", {}).items():
        if name not in loaded:
            errors.append(f"Unknown feature in settings: {name}")
            continue
        if loaded[name]["control"] != "settings":
            entries[name] = entry
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
    for name, manifest in loaded.items():
        missing = [item for item in manifest["requires"] if report.get(item, {}).get("effective") is not True]
        if missing and report[name]["effective"]:
            report[name] = dict(report[name], effective=False, reason="prerequisite_missing")
    return dict(ok=True, checkout=str(root), settings=str(settings_store.path(root).relative_to(root)),
                revision=settings["revision"] if settings else 0, fingerprint=settings_store.digest(raw),
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
    if manifest["control"] != "settings" or manifest["consent"] != "none":
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

    _, warnings = settings_store.update(checkout_root(cwd), apply)
    return dict(resolve(cwd, package), warnings=warnings)


def migrate(cwd, action, backup=None):
    from runtime import features_migration
    return features_migration.run(checkout_root(cwd), action, backup)
