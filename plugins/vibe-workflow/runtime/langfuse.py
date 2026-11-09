"""Bounded, content-free Langfuse connection verification and synthetic OTLP proof."""

import base64
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
import re
import ssl
import time
from urllib.parse import urlencode, urlsplit
import uuid

from runtime.startup import StartupError, json_object


MAX_RESPONSE = 128 * 1024
REQUEST_SECONDS = 3
PROBE_SECONDS = 20
READ_ATTEMPTS = 5
PROBE_NAME = "vibe-observability-synthetic-probe"
SERVICE_NAME = "vibe-coding-workflow"


class LangfuseError(StartupError):
    """Fixed diagnostic code; never retain remote bodies or credentials."""


def validate_credentials(pair):
    if (type(pair) is not tuple or len(pair) != 2
            or not all(isinstance(value, str) and 1 <= len(value) <= 512
                       and all(33 <= ord(character) <= 126 for character in value) for value in pair)
            or ":" in pair[0]):
        raise LangfuseError("credentials_unavailable")
    return pair


def validate_destination(value):
    if (not isinstance(value, str) or len(value) > 512 or not value
            or any(ord(character) <= 32 or ord(character) >= 127 for character in value)
            or "\\" in value):
        raise LangfuseError("invalid_destination")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or "?" in value or "#" in value or parsed.path not in ("", "/")
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                                         for label in host.split("."))):
                raise ValueError
        canonical = "[" + host.lower() + "]" if ":" in host else host.lower()
        if parsed.port is not None and parsed.port != 443:
            canonical += ":" + str(parsed.port)
        return "https://" + canonical
    except ValueError:
        raise LangfuseError("invalid_destination") from None


def validate_project(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise LangfuseError("invalid_langfuse_project")
    return value


class _Connection:
    def __init__(self, destination, project_id, seconds, credentials=None):
        self.destination = validate_destination(destination)
        self.project_id = validate_project(project_id)
        pair = tuple(os.environ.get(key) for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")) if credentials is None else credentials
        public, secret = validate_credentials(pair)
        self.authorization = "Basic " + base64.b64encode((public + ":" + secret).encode()).decode()
        self.deadline = time.monotonic() + seconds

    def request(self, method, path, body=None):
        parsed = urlsplit(self.destination)
        deadline = min(self.deadline, time.monotonic() + REQUEST_SECONDS)
        connection = response = None

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise LangfuseError("connection_timeout")
            return seconds

        try:
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port,
                timeout=remaining(), context=ssl.create_default_context())
            headers = {"Authorization": self.authorization, "Accept": "application/json"}
            if body is not None:
                body = json.dumps(body, separators=(",", ":")).encode()
                headers.update({"Content-Type": "application/json", "X-Langfuse-Ingestion-Version": "4"})
            connection.request(method, path, body=body, headers=headers)
            wire = connection.sock
            wire.settimeout(remaining())
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise LangfuseError("redirect_rejected")
            if response.status in (401, 403):
                raise LangfuseError("authentication_failed")
            if response.status != 200:
                raise LangfuseError("destination_rejected_request")
            if response.headers.get_content_type() != "application/json":
                raise LangfuseError("invalid_response")
            chunks, size = [], 0
            while not response.isclosed():
                wire.settimeout(remaining())
                chunk = response.read1(min(4096, MAX_RESPONSE + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise LangfuseError("response_too_large")
            return json_object(b"".join(chunks), limit=MAX_RESPONSE)
        except LangfuseError:
            raise
        except (OSError, http.client.HTTPException):
            raise LangfuseError("connection_unavailable") from None
        except (StartupError, ValueError, TypeError, UnicodeError, RecursionError):
            raise LangfuseError("invalid_response") from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    def verify(self):
        projects = self.request("GET", "/api/public/projects").get("data")
        if (not isinstance(projects, list) or len(projects) != 1 or not isinstance(projects[0], dict)
                or projects[0].get("id") != self.project_id):
            raise LangfuseError("langfuse_project_mismatch")


def verify_project(destination, project_id, *, credentials=None):
    """Verify the supplied pair or current environment, without ingesting data."""
    _Connection(destination, project_id, REQUEST_SECONDS, credentials).verify()


def synthetic_probe(destination, project_id, *, before_ingest, credentials=None):
    """Send one fixed synthetic span; no task payload or automatic ingestion retry."""
    connection = _Connection(destination, project_id, PROBE_SECONDS, credentials)
    connection.verify()
    trace_id, observation_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    started = time.time_ns()
    metadata = {"vibe_synthetic": "true", "vibe_probe_id": observation_id}
    attributes = {"langfuse.observation.type": "span"} | {
        "langfuse.observation.metadata." + key: value for key, value in metadata.items()}
    span = dict(traceId=trace_id, spanId=observation_id, name=PROBE_NAME, kind=1,
        startTimeUnixNano=str(started), endTimeUnixNano=str(max(started + 1, time.time_ns())),
        attributes=[dict(key=key, value=dict(stringValue=value)) for key, value in attributes.items()])
    body = {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]},
        "scopeSpans": [{"scope": {"name": "vibe-coding-workflow.observability"}, "spans": [span]}]}]}
    before_ingest()
    try:
        response = connection.request("POST", "/api/public/otel/v1/traces", body)
    except LangfuseError:
        # A broken acknowledgement cannot prove whether this one write reached ingestion.
        raise LangfuseError("ingestion_outcome_unknown") from None
    partial = response.get("partialSuccess", {})
    rejected = partial.get("rejectedSpans", 0) if isinstance(partial, dict) else None
    if type(rejected) is not int and not (isinstance(rejected, str) and re.fullmatch(r"[0-9]{1,20}", rejected)):
        raise LangfuseError("invalid_ingestion_acknowledgement")
    if int(rejected) != 0:
        raise LangfuseError("ingestion_rejected")
    result = dict(accepted=True, verified=False, status="pending", trace_id=trace_id,
        observation_id=observation_id, source="synthetic_smoke", native_collection_qualified=False,
        usage=None, charges=None)
    start_second = started // 1_000_000_000
    path = "/api/public/v2/observations?" + urlencode(dict(
        fields="core,basic,metadata,io", traceId=trace_id, limit=2,
        fromStartTime=datetime.fromtimestamp(start_second, timezone.utc).isoformat().replace("+00:00", "Z"),
        toStartTime=datetime.fromtimestamp(start_second + 1, timezone.utc).isoformat().replace("+00:00", "Z")))
    for attempt in range(READ_ATTEMPTS):
        try:
            rows = connection.request("GET", path).get("data")
        except LangfuseError:
            break
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            expected = dict(id=observation_id, traceId=trace_id, projectId=project_id,
                type="SPAN", name=PROBE_NAME)
            expected_metadata = {
                "attributes.langfuse.observation.type": "span",
                "resourceAttributes.service.name": SERVICE_NAME,
                "scope.name": "vibe-coding-workflow.observability",
                "vibe_synthetic": True,
                "vibe_probe_id": observation_id,
            }
            actual_metadata = row.get("metadata")
            if (all(type(row.get(key)) is type(value) and row[key] == value
                    for key, value in expected.items())
                    and type(actual_metadata) is dict and set(actual_metadata) == set(expected_metadata)
                    and all(type(actual_metadata[key]) is type(value) and actual_metadata[key] == value
                            for key, value in expected_metadata.items())
                    and all(row.get(key) is None for key in ("input", "output"))
                    and all(row.get(key) is None or type(row[key]) is str and row[key] == ""
                            for key in ("userId", "sessionId"))):
                return dict(result, verified=True, status="verified")
        if attempt < READ_ATTEMPTS - 1:
            remaining = connection.deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))
    return result
