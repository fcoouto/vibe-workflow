"""OpenCode 2.2.6 hook, event and message payloads normalized for Smart Routing.

Pure functions: no I/O, no subprocess, no network. Output carries IDs, names, model choice, counts and
telemetry only; prompts, message text, command text, header and environment values never leave. A field
the payload does not carry stays None (unknown); a malformed payload raises AdapterError with a fixed reason.
"""

import math
import re

SCHEMA = 1
CLIENT = "opencode"
QUALIFIED_VERSION = "2.2.6"
HOOKS = ("model.request", "tool.execute.before", "permission.evaluate", "shell.create.before")
REQUEST_KINDS = {"primary", "title", "compaction", "generate"}
# Built-in tool names recorded in the probe's tool lists; anything else is an MCP or plugin tool.
BUILTIN_TOOLS = {"edit", "glob", "grep", "question", "read", "skill", "webfetch", "websearch", "write"}
TOOL_CLASSES = {"subagent": "subagent", "execute": "codemode", "shell": "shell"}
HARNESS = {"claude-agent": "claude-agent-sdk via opencode"}
# Matrix facts that hold on every OpenCode 2.2.6 profile.
ALWAYS_UNSUPPORTED = ("plugin_load_fail_open", "shell_command_contents", "resolved_model", "provider_retries",
                      "model_switch_before_request", "session_id_environment", "event_replay")
WORKER_TOOLS = ("shell", "mcp", "execute", "webfetch")

MAX_DEPTH = 64
MAX_NODES = 50_000
MAX_CHARS = 4_000_000
MAX_TEXT = 128
MAX_ITEMS = 64
MAX_COUNT = 10 ** 15  # token counts and epoch milliseconds

SESSION = re.compile(r"ses_[A-Za-z0-9]{1,128}")
MESSAGE = re.compile(r"msg_[A-Za-z0-9]{1,128}")
EVENT = re.compile(r"evt_[A-Za-z0-9]{1,128}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
HEADER = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
CREDENTIAL_ENV = re.compile(r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|COOKIE|PRIVATE)(?:_|$)",
                            re.IGNORECASE)
ORIGIN = re.compile(r"(https?)://(?:[^/?#@\s]*@)?([A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::(\d{1,5}))?(?:[/?#]|$)")
SPAWN_MODEL = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})/([A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}?)"
                         r"(?:#([A-Za-z0-9][A-Za-z0-9._-]{0,63}))?")
# Best effort only: commands inside the shell cannot be enforced (capability matrix).
LAUNCH_MARKERS = re.compile(r"(?<![A-Za-z0-9_.-])(opencode|claude|codex|curl|wget)(?![A-Za-z0-9_-])"
                            r"|(?<![A-Za-z0-9-])(anthropic\.com|openai\.com|chatgpt\.com|opencode\.ai)(?![A-Za-z0-9-])")


class AdapterError(ValueError):
    """An invalid OpenCode payload. The message is fixed text and never includes payload content."""


def _guard(function):
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AdapterError as error:
            raise AdapterError(str(error)) from None
        except Exception:
            raise AdapterError("invalid payload") from None
    guarded.__name__, guarded.__doc__ = function.__name__, function.__doc__
    return guarded


def _check_bounds(payload):
    """Reject non-JSON data and anything too deep, wide or long before reading fields."""
    stack, nodes, chars = [(payload, 0)], 0, 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise AdapterError("payload too large")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise AdapterError("payload is not JSON data")
                chars += len(key)
                stack.append((item, depth + 1))
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            chars += len(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise AdapterError("payload is not JSON data")
        elif value is not None and not isinstance(value, (bool, int)):
            raise AdapterError("payload is not JSON data")
        if chars > MAX_CHARS:
            raise AdapterError("payload too large")


def _object(value, reason="expected a JSON object"):
    if not isinstance(value, dict):
        raise AdapterError(reason)
    return value


def _optional_object(data, field):
    value = data.get(field)
    return None if value is None else _object(value, f"invalid {field}")


def _text(data, field, pattern=NAME, reason=None):
    """Return a validated string, None when absent; never echo an invalid value."""
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise AdapterError(reason or f"invalid {field}")
    return value


def _count(value, reason):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise AdapterError(reason)
    return value


def _number(value, reason):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= MAX_COUNT:
        raise AdapterError(reason)
    return value


def _flag(value, reason):
    if value is not None and type(value) is not bool:
        raise AdapterError(reason)
    return value


def _keys(data):
    return sorted(key for key in data if NAME.fullmatch(key))[:MAX_ITEMS]


def _model(value, families):
    """Requested model and route identity; the provider's resolved model is never observable."""
    if value is None:
        return None, None
    model = _object(value, "invalid model")
    requested = {"provider_id": _text(model, "providerID", reason="invalid model"),
                 "model_alias": _text(model, "id", reason="invalid model"),
                 "variant": _text(model, "variant", reason="invalid model")}
    if requested["provider_id"] is None or requested["model_alias"] is None:
        raise AdapterError("invalid model")
    return requested, _route(requested, families)


def _route(requested, families):
    provider, alias = requested["provider_id"], requested["model_alias"]
    family = (families or {}).get(f"{provider}/{alias}")
    if family is not None and (not isinstance(family, str) or not NAME.fullmatch(family)):
        raise AdapterError("invalid catalog family")
    return {"harness": HARNESS.get(provider, f"opencode provider {provider}"), "provider_id": provider,
            "model_alias": alias, "variant": requested["variant"], "catalog_family": family,
            "resolved_model": "unknown"}


def _tokens(value):
    if value is None:
        return None
    tokens = _object(value, "invalid token counts")
    cache = tokens.get("cache")
    cache = {} if cache is None else _object(cache, "invalid token counts")
    return {"input": _count(tokens.get("input"), "invalid token counts"),
            "output": _count(tokens.get("output"), "invalid token counts"),
            "reasoning": _count(tokens.get("reasoning"), "invalid token counts"),
            "cache_read": _count(cache.get("read"), "invalid token counts"),
            "cache_write": _count(cache.get("write"), "invalid token counts")}


def _rate_limit(native):
    """Claude Agent `native.rateLimit`; utilization is a 0-1 fraction, not a percentage."""
    if native is None:
        return None
    limit = _optional_object(_object(native, "invalid native"), "rateLimit")
    if limit is None:
        return None
    windows = _optional_object(limit, "unifiedWindows") or {}
    if len(windows) > 8:
        raise AdapterError("invalid rate limit")
    normalized = {}
    for name, window in windows.items():
        window = _object(window, "invalid rate limit")
        if not NAME.fullmatch(name):
            raise AdapterError("invalid rate limit")
        normalized[name] = {"utilization": _number(window.get("utilization"), "invalid rate limit"),
                            "unit": "fraction",
                            "resets_at": _count(window.get("resetsAt"), "invalid rate limit"),
                            "status": _text(window, "status", reason="invalid rate limit")}
    return {"status": _text(limit, "status", reason="invalid rate limit"),
            "rate_limit_type": _text(limit, "rateLimitType", reason="invalid rate limit"),
            "resets_at": _count(limit.get("resetsAt"), "invalid rate limit"),
            "overage_status": _text(limit, "overageStatus", reason="invalid rate limit"),
            "overage_disabled_reason": _text(limit, "overageDisabledReason", reason="invalid rate limit"),
            "is_using_overage": _flag(limit.get("isUsingOverage"), "invalid rate limit"),
            "windows": normalized}


def _error_type(data):
    error = _optional_object(data, "error")
    return None if error is None else _text(error, "type", reason="invalid error")


def _tool_class(tool):
    if tool in TOOL_CLASSES:
        return TOOL_CLASSES[tool]
    return "builtin" if tool in BUILTIN_TOOLS else "other"


def _spawn(tool_input):
    """Subagent spawn request: the agent name and an optional `provider/model#variant` override."""
    agent = _text(tool_input, "agent", reason="invalid subagent input")
    model = tool_input.get("model")
    requested = None
    if model is not None:
        match = SPAWN_MODEL.fullmatch(model) if isinstance(model, str) else None
        if match is None:
            raise AdapterError("invalid subagent model")
        requested = {"provider_id": match[1], "model_alias": match[2], "variant": match[3]}
    return {"agent": agent, "follow_up_session_id": _text(tool_input, "sessionID", SESSION, "invalid session id"),
            "requested_model": requested}


def _markers(command):
    if not isinstance(command, str):
        return []
    return sorted({match[1] or match[2] for match in LAUNCH_MARKERS.finditer(command.lower())})


def _origin(value):
    match = ORIGIN.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    port = f":{match[3]}" if match[3] else ""
    return f"{match[1]}://{match[2].lower()}{port}"


def _names(value, pattern, case=None):
    """Bounded names of a mapping; invalid names are counted, never echoed. Values are dropped."""
    if value is None:
        return [], 0
    names = sorted({case(key) if case else key for key in _object(value, "invalid name map")
                    if pattern.fullmatch(key)})
    return names[:MAX_ITEMS * 4], len(value) - len(names)


def _unknown(source, name):
    return {"schema": SCHEMA, "client": CLIENT, "kind": "unknown", "source": source,
            "name" if source == "hook" else "type": name if isinstance(name, str) and NAME.fullmatch(name) else None}


@_guard
def normalize_hook(hook, payload, families=None):
    """Normalize one plugin hook payload. `hook` is the name the plugin registered; payloads do not carry it.

    `families` maps "provider/alias" to its catalog family. Task, stage and attempt are not in OpenCode
    payloads; the dispatch record binds them by message ID (S3.4). Parent sessions come from lineage events.
    """
    if hook not in HOOKS:
        return _unknown("hook", hook)
    data = _object(payload)
    _check_bounds(data)
    result = {"schema": SCHEMA, "client": CLIENT, "kind": "hook", "hook": hook,
              "session_id": _text(data, "sessionID", SESSION, "invalid session id"),
              "message_id": _text(data, "messageID", MESSAGE, "invalid message id"),
              "parent_session_id": None, "task_id": None, "stage": None, "attempt_id": None,
              "agent": _text(data, "agent"), "tool": None, "tool_class": None, "tool_call_id": None,
              "input": {"keys": _keys(data)}, "request_kind": None, "requested": None, "route_identity": None,
              "spawn": None}
    if hook == "model.request":
        if result["session_id"] is None:
            raise AdapterError("invalid session id")
        kind = data.get("kind")
        result["request_kind"] = kind if kind in REQUEST_KINDS else "unknown"
        result["requested"], result["route_identity"] = _model(data.get("model"), families)
        names, dropped = _names(data.get("headers"), HEADER, str.lower)
        result["input"].update(header_names=names, header_names_dropped=dropped,
                               base_url_origin=_origin(data.get("baseURL")))
    elif hook == "tool.execute.before":
        tool = _text(data, "tool", reason="invalid tool")
        if result["session_id"] is None or tool is None:
            raise AdapterError("invalid tool call")
        tool_input = _optional_object(data, "input") or {}
        result.update(tool=tool, tool_class=_tool_class(tool), input={"keys": _keys(tool_input)},
                      tool_call_id=_text(data, "id", CALL_ID, "invalid tool call id"))
        if tool == "subagent":
            result["spawn"] = _spawn(tool_input)
        elif tool == "shell":
            result["command_markers"] = _markers(tool_input.get("command"))
    elif hook == "permission.evaluate":
        action = _text(data, "action", reason="invalid permission action")
        resources = data.get("resources") or []
        if result["session_id"] is None or action is None or not isinstance(resources, list):
            raise AdapterError("invalid permission request")
        if action == "subagent":
            kept = [item for item in resources if isinstance(item, str) and NAME.fullmatch(item)]
        elif action == "webfetch":
            kept = sorted({origin for origin in map(_origin, resources) if origin})
        else:
            kept = []
        result.update(action=action, effect=_text(data, "effect", reason="invalid permission effect"),
                      resources=kept[:MAX_ITEMS], resource_count=len(resources))
    else:  # shell.create.before carries no session ID (capability matrix).
        names, dropped = _names(data.get("env"), ENV_NAME)
        result.update(env_names=names, env_names_dropped=dropped,
                      credential_env_names=[name for name in names if CREDENTIAL_ENV.search(name)],
                      command_markers=_markers(data.get("command")))
    return result


EVENT_TYPES = {"session.created", "session.step.started", "session.step.ended", "session.usage.updated",
               "session.model.selected", "session.execution.started", "session.execution.succeeded",
               "session.execution.failed", "session.execution.interrupted"}


@_guard
def normalize_event(event, families=None):
    """Normalize one `/api/event` record. Durable events carry a per-session `durable_seq` for gap detection;
    volatile events (such as usage updates) have none, and the stream has no replay, so resync by re-reading."""
    record = _object(event)
    _check_bounds(record)
    data = _optional_object(record, "data") or {}
    durable = _optional_object(record, "durable")
    event_type = record.get("type")
    base = {"schema": SCHEMA, "client": CLIENT, "event_id": _text(record, "id", EVENT, "invalid event id"),
            "created": _count(record.get("created"), "invalid event time"),
            "session_id": _text(data, "sessionID", SESSION, "invalid session id"),
            "durable_seq": None if durable is None else _count(durable.get("seq"), "invalid durable sequence"),
            "durable_aggregate_id": None if durable is None else _text(durable, "aggregateID", SESSION,
                                                                       "invalid durable sequence"),
            "durable_version": None if durable is None else _count(durable.get("version"), "invalid durable sequence")}
    if event_type not in EVENT_TYPES:
        return {**_unknown("event", event_type), **base}
    requested, route = _model(data.get("model"), families)
    previous = _model(data.get("previous"), families)[0]
    native = data.get("native")
    return {**base, "kind": "event", "type": event_type,
            "parent_session_id": _text(data, "parentID", SESSION, "invalid session id"),
            "agent": _text(data, "agent"),
            "assistant_message_id": _text(data, "assistantMessageID", MESSAGE, "invalid message id"),
            "requested": requested, "route_identity": route, "previous": previous,
            "tokens": _tokens(data.get("tokens")), "cost": _number(data.get("cost"), "invalid cost"),
            "finish": _text(data, "finish"), "raw_finish": _text(data, "rawFinish"),
            "rate_limit": _rate_limit(native), "error_type": _error_type(data)}


MESSAGE_TYPES = {"assistant", "user", "idle", "compaction", "model-switched"}


def _tool_part(part):
    tool = _text(part, "name", reason="invalid tool part")
    state = _optional_object(part, "state") or {}
    metadata = _optional_object(state, "metadata") or {}
    tool_input = _optional_object(state, "input") or {}
    return {"tool": tool, "tool_class": None if tool is None else _tool_class(tool),
            "tool_call_id": _text(part, "id", CALL_ID, "invalid tool call id"),
            "executed": _flag(part.get("executed"), "invalid tool part"),
            "status": _text(state, "status", reason="invalid tool part"),
            "child_session_id": _text(metadata, "sessionID", SESSION, "invalid session id"),
            "spawn": _spawn(tool_input) if tool == "subagent" else None}


@_guard
def normalize_message(message, families=None):
    """Normalize one stored session message; text, reasoning, summaries and tool output are dropped."""
    record = _object(message)
    _check_bounds(record)
    message_type = record.get("type")
    message_id = _text(record, "id", MESSAGE, "invalid message id")
    if message_type not in MESSAGE_TYPES:
        return {**_unknown("message", message_type), "message_id": message_id}
    time = _optional_object(record, "time") or {}
    requested, route = _model(record.get("model"), families)
    content = record.get("content") or []
    if not isinstance(content, list):
        raise AdapterError("invalid message content")
    parts = [_object(part, "invalid message content") for part in content]
    calls = [_tool_part(part) for part in parts if part.get("type") == "tool"]
    return {"schema": SCHEMA, "client": CLIENT, "kind": "message", "type": message_type,
            "message_id": message_id, "created": _count(time.get("created"), "invalid message time"),
            "completed": _count(time.get("completed"), "invalid message time"),
            "agent": _text(record, "agent"), "requested": requested, "route_identity": route,
            "previous": _model(record.get("previous"), families)[0],
            "finish": _text(record, "finish"), "raw_finish": _text(record, "rawFinish"),
            "outcome": _text(record, "outcome"), "error_type": _error_type(record),
            "tokens": _tokens(record.get("tokens")), "cost": _number(record.get("cost"), "invalid cost"),
            "rate_limit": _rate_limit(record.get("native")), "tool_calls": calls[:MAX_ITEMS],
            "tool_calls_dropped": max(0, len(calls) - MAX_ITEMS)}


@_guard
def probe(capabilities):
    """Classify observed OpenCode capabilities without launching OpenCode.

    `capabilities`: {"version", "hooks": [registered hook names], "plugin_health": "ok" when the
    `routing.health` RPC answered for the canonical directory, "worker_tools": {shell|mcp|execute|webfetch:
    "deny"|"narrow"|"allow"}}. Managed launches refuse unless health is ok and every hook S3 uses is present;
    enforcement also needs the qualified version and a worker profile without unmediated tools.
    """
    caps = _object(capabilities, "invalid capabilities")
    version = _text(caps, "version", reason="invalid capabilities")
    hooks = caps.get("hooks") or []
    tools = caps.get("worker_tools") or {}
    if (not isinstance(hooks, list) or not isinstance(tools, dict) or len(hooks) > MAX_ITEMS
            or any(not isinstance(hook, str) for hook in hooks)
            or any(key not in WORKER_TOOLS or value not in ("deny", "narrow", "allow") for key, value in tools.items())):
        raise AdapterError("invalid capabilities")
    supported = sorted(set(hooks) & set(HOOKS))
    missing = sorted(set(HOOKS) - set(hooks))
    unsupported = list(ALWAYS_UNSUPPORTED)
    healthy = caps.get("plugin_health") == "ok"
    if not healthy:
        unsupported.append("plugin_health_failed")
    if missing:
        unsupported.append("hooks_missing")
    if version != QUALIFIED_VERSION:
        unsupported.append("version_unqualified")
    if any(tools.get(tool) not in ("deny", "narrow") for tool in WORKER_TOOLS):
        unsupported.append("worker_unmediated_tools")
    managed = "allow" if healthy and not missing else "refuse"
    enforced = managed == "allow" and unsupported == list(ALWAYS_UNSUPPORTED)
    return {"schema": SCHEMA, "client": CLIENT, "version": version, "qualified_version": QUALIFIED_VERSION,
            "supported_hooks": supported, "missing_hooks": missing, "unsupported": unsupported,
            "managed_launch": managed, "interactive_sessions": "unguarded",
            "enforcement_profile": "enforced" if enforced else "advisory"}
