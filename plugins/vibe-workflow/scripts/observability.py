#!/usr/bin/env python3
"""Explicit optional connection controls and native credential entry; no collector."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def _read_pair():
    import getpass
    import warnings
    from runtime.observability_credentials import CredentialError

    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise CredentialError("credential_entry_requires_terminal")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            public = getpass.getpass("Langfuse public key: ", stream=sys.stderr)
            secret = getpass.getpass("Langfuse secret key: ", stream=sys.stderr)
            return public, secret
    except getpass.GetPassWarning:
        raise CredentialError("credential_entry_cannot_hide_input") from None
    except (KeyboardInterrupt, EOFError):
        raise CredentialError("credential_entry_cancelled") from None


def main():
    if sys.version_info < (3, 11):
        print(json.dumps(dict(ok=False, error="python_3_11_required")))
        return 1
    from runtime import langfuse, observability_settings as settings
    from runtime.observability_credentials import CredentialError
    from runtime.startup import StartupError, json_object, acquire_project_entry, _entry_scope, AcceptedScope

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise StartupError("invalid_observability_arguments")

    try:
        parser = Parser(description=__doc__)
        parser.add_argument("action", choices=("configure", "status", "enable", "disable", "probe",
                                              "store-credentials", "forget-credentials"))
        parser.add_argument("--cwd", required=True)
        parser.add_argument("--expected-revision", type=int)
        args = parser.parse_args()
        credential_action = args.action in ("store-credentials", "forget-credentials")
        if credential_action:
            if args.expected_revision is None:
                raise StartupError("credential_revision_required")
            request = dict(expected_revision=args.expected_revision)
        else:
            if args.expected_revision is not None:
                raise StartupError("invalid_observability_arguments")
            raw = b"{}" if args.action == "status" and sys.stdin.isatty() else sys.stdin.buffer.read(settings.MAX_CONFIG + 1)
            request = json_object(raw or b"{}", limit=settings.MAX_CONFIG)
        if args.action == "status" and request:
            raise StartupError("invalid_observability_status_request")
        scope = Path(args.cwd)
        if args.action not in ('status', 'disable'):
            scope = _entry_scope(acquire_project_entry(SimpleNamespace(client="codex", cwd=scope), PACKAGE))
            if (type(scope) is not AcceptedScope or scope.client != "codex" or scope.runtime is None
                    or scope.runtime.schema_version != 3):
                raise StartupError("existing_enrolled_runtime_required")
        if args.action == "status":
            result = settings.status(scope)
        elif args.action == "store-credentials":
            result = settings.store_credentials(scope, request, _read_pair)
        elif args.action == "forget-credentials":
            result = settings.forget_credentials(scope, request)
        else:
            operation = {"configure": settings.configure, "enable": settings.enable,
                         "disable": settings.disable, "probe": settings.probe}[args.action]
            result = operation(scope, request)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (settings.SettingsError, langfuse.LangfuseError, CredentialError) as error:
        reason = str(error)
    except (StartupError, OSError, ValueError, TypeError, KeyError, ImportError,
            RecursionError, subprocess.SubprocessError):
        reason = "invalid_observability_input_or_runtime"
    print(json.dumps(dict(ok=False, error=reason), separators=(",", ":")))
    return 1


if __name__ == "__main__":
    sys.exit(main())
