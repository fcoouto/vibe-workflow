"""Strict masking for recorded upstream facts (the ChatGPT request's `session_facts`), whose results are committed.

Upstream error texts may name the account (emails, `org-`/`user-`/`acct_` ids), carry credentials (keys, JWTs,
`Bearer` values) or request ids. Every recorded string value goes through `mask`, every recorded key name through
`safe_keys` (a fixed allow-list), and code-like values through `code_value` (known codes and error class names only).
`leaks` checks for the same patterns so that facts still containing one are withheld instead of written. Inputs are
cut to `INPUT_CAP` before masking and every pattern is bounded, so adversarial texts stay linear.

Known gaps (see docs/smart-routing-qualification.md): personal or workspace names in prose, phone numbers, encoded
secrets shorter than 16 characters and homoglyphs are not recognized; the operator reads the results before committing.
"""
import hashlib
import re

CAP = 200
INPUT_CAP = 8192  # characters looked at before masking (text and message); leaks scans in overlapping windows
WINDOW, OVERLAP = 8192, 512
ZERO_WIDTH = re.compile("[­᠎​-‏‪-‮⁠-⁤﻿]")
ENCODED = ((re.compile(r"%40|&#0*64;|&#x0*40;|&commat;", re.I), "@"), (re.compile(r"%2d|&#0*45;|&#x0*2d;|&hyphen;|&dash;", re.I), "-"))
ID_PREFIXES = r"org|user|acct|proj|sess|ses|req|resp|chatcmpl|msg|thr|cred|file|asst|run|batch|cus"
# Ordered: the most specific shapes first, so a JWT is not reported as a long base64 run. Every repetition is bounded.
PATTERNS = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{4,4096}\.[A-Za-z0-9_-]{4,4096}\.[A-Za-z0-9_-]{4,4096}")),
    ("credential", re.compile(r"\b(?:Bearer|Basic|Token)[ \t]{1,4}([A-Za-z0-9._~+/=-]{6,1024})", re.I)),
    ("email", re.compile(r"(?<![\w.%+-])[\w.%+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){0,8}")),
    ("url", re.compile(r"\b[a-z][a-z0-9+.-]{0,15}://\S{1,2048}", re.I)),
    ("path", re.compile(r"(?:/Users|/home|/root|/private/var/folders|/var/folders)/[^\s'\"<>]{0,512}|[A-Za-z]:\\Users\\[^\s'\"<>]{0,512}")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("key", re.compile(r"(?<![A-Za-z0-9])sk[-_][A-Za-z0-9*_-]{1,256}", re.I)),
    ("id", re.compile(r"(?<![A-Za-z0-9])(?:%s)([-_])([A-Za-z0-9_-]{4,256})" % ID_PREFIXES, re.I)),
    ("model", re.compile(r"\bft:[A-Za-z0-9._:-]{4,256}")),
    ("ip", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])|(?<![\w:])(?=[0-9A-Fa-f:]{0,39}[0-9A-Fa-f])"
                      r"(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{0,4}::"
                      r"(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{0,4})(?![\w:])")),
    ("hex", re.compile(r"\b[0-9a-fA-F]{20,256}\b")),
)
# A run of base64url-ish characters is masked when it looks random (see `_random_segments`).
LONG_RUN = re.compile(r"[A-Za-z0-9+/_=-]{16,4096}")
# Key names from the OpenCode 2.2.6 session and error schemas and OpenAI error bodies; any other name is `<key>`.
SAFE_KEYS = frozenset((
    "code", "error", "error_description", "message", "reason", "param", "type", "status", "finish", "rawFinish",
    "retryAfterMs", "response", "body", "tokens", "input", "output", "reasoning", "cache", "read", "write", "total",
    "cost", "time", "completed", "created", "streamed", "updated", "busy", "idle", "attempt", "at", "retry", "next",
    "model", "providerID", "id", "variant", "agent", "content", "native", "snapshot", "metadata", "outcome",
    "sessionID", "parentID", "info", "data", "action", "provider", "title", "label", "link", "details", "cursor",
    "projectID", "location", "permissions", "fork", "subpath", "revert", "previous", "text", "files", "agents", "skills",
    "access", "refresh", "id_token", "expires"))  # OpenCode's OAuth record names (their values are never recorded)
# Refusal and error codes kept verbatim: the 2.2.6 chatgpt plugin's own list and OpenAI/Anthropic codes found in the
# 2.2.6 binary or the providers' documentation.
KNOWN_CODES = ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_user_not_eligible",
               "subscription_sharing_unsupported_capability", "subscription_sharing_route_not_supported",
               "subscription_sharing_invalid_user", "chatpass_v2_scope_not_authorized",
               "chatpass_v2_invalid_authorization_context", "usage_limit_reached", "rate_limit_exceeded",
               "insufficient_quota", "model_not_found", "unsupported_value", "unsupported_parameter",
               "invalid_request_error", "context_length_exceeded", "invalid_reasoning_effort", "server_error",
               "server_is_overloaded", "invalid_prompt", "usage_not_included", "context_window_exceeded", "invalid_value",
               "overloaded_error", "authentication_error", "permission_error", "api_error", "rate_limit_error",
               "invalid_api_key")
# Error class names in the 2.2.6 binary that a session, provider or request error may carry as its `type`.
ERROR_CLASSES = frozenset((
    "APIError", "AbortError", "AuthenticationError", "AuthorizationError", "BudgetLimitError", "ContentFilterError",
    "ContentPolicyError", "ContextOverflowError", "ExceededCapacityError", "ForbiddenError", "FreeUsageLimitError",
    "GoUsageLimitError", "HttpError", "InternalProviderError", "InternalServerError", "InvalidRequestError",
    "InvalidResponseError", "MessageAbortedError", "MessageOutputLengthError", "ModelNotFoundError", "NetworkError",
    "OAuthError", "ProviderAuthError", "ProviderError", "ProviderInitError", "ProviderModelNotFoundError",
    "ProviderNotFoundError", "ProviderRequestError", "QuotaExhaustedError", "RateLimitError", "RequestError",
    "ResponseError", "ServiceUnavailableError", "StatusCodeError", "StructuredOutputError", "TimeoutError",
    "TooManyRequestsError", "TransportError", "UnauthorizedError", "UnknownError", "UnsupportedModelError"))


def _random(value):
    """Digits, or mixed case that is not camelCase words: random base64 has single lowercase letters between
    capitals (`QWxhZGRpbjpvcGVu`), while `assistantMessageID` or `RpcInternalError` have lowercase runs of 2 or more."""
    if re.search(r"[0-9]", value):
        return True
    if not (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)):
        return False
    lower = re.findall(r"[a-z]+", value)
    return min(map(len, lower)) < 2 or len(lower) > 8


def _random_segments(value, minimum):
    """Whether a separated run has a segment that looks random (digits with letters, or mixed case), so snake_case
    names such as `user_dirs` or `p07_duplicate_prompt_note` are not taken for identifiers."""
    segments = [part for part in re.split(r"[_\-./+=]", value) if part]
    if len(segments) == 1:
        return _random(value)
    return any(len(part) >= minimum and ((re.search(r"[0-9]", part) and re.search(r"[A-Za-z]", part)) or
                                         (re.search(r"[a-z]", part) and re.search(r"[A-Z]", part)) or
                                         (part.isdigit() and len(part) >= 6)) for part in segments)


def _random_run(match):
    return "<redacted:token>" if _random_segments(match.group(0), 8) else match.group(0)


def _id(match):
    """`org-abcdefgh` (a hyphen and 6+ characters) is an id whatever its case; after an underscore the body must look
    random or be one long word, so harness names such as `user_dirs` or `run_id` stay."""
    separator, body = match.group(1), match.group(2)
    if separator == "-" and len(body) >= 6:
        return True
    return _random_segments(body, 4) or ("_" not in body and len(body) >= 8)


def _replace(kind):
    def replace(match):
        if kind == "credential" and not (_random(match.group(1)) or len(match.group(1)) >= 16):
            return match.group(0)  # "Token limit exceeded" is prose, not a credential
        if kind == "id" and not _id(match):
            return match.group(0)
        return "<redacted:%s>" % kind
    return replace


def normalize(text):
    """Zero-width characters dropped and encoded `@`/`-` decoded, so they cannot hide a shape from the patterns."""
    text = ZERO_WIDTH.sub("", text)
    for pattern, plain in ENCODED:
        text = pattern.sub(plain, text)
    return text


def mask(text):
    """`(masked text, whether anything was replaced)`; callers cut long inputs first (`INPUT_CAP`)."""
    masked = normalize(text)
    for kind, pattern in PATTERNS:
        masked = pattern.sub(_replace(kind), masked)
    masked = LONG_RUN.sub(_random_run, masked)
    return masked, masked != text


def text(value, cap=CAP):
    """A masked, capped string, or None."""
    return mask(value[:INPUT_CAP])[0][:cap] if isinstance(value, str) and value.strip() else None


def message(value):
    """A message record: masked and capped text, whether it was masked or cut, a 12-hex SHA-256 prefix of the masked
    text (to correlate repeats without storing the original) and the known codes it contains."""
    if not isinstance(value, str) or not value.strip():
        return None
    masked, changed = mask(value[:INPUT_CAP])
    kept = masked[:CAP]
    return dict(text=kept, masked=changed or len(masked) > CAP or len(value) > INPUT_CAP,
                sha256=hashlib.sha256(kept.encode()).hexdigest()[:12],
                codes=[c for c in KNOWN_CODES if c in value[:INPUT_CAP]])


def code_value(value, type_name=False):
    """A known code (or, with `type_name`, a known error class name) verbatim; any other string becomes `<code:` plus
    a 12-hex hash of its masked form `>`; a non-string is None."""
    if not isinstance(value, str):
        return None
    if value in KNOWN_CODES or (type_name and value in ERROR_CLASSES):
        return value
    return "<code:%s>" % hashlib.sha256(mask(value[:INPUT_CAP])[0].encode()).hexdigest()[:12]


def safe_keys(mapping, limit=40):
    """Key names from the allow-list; every other name is recorded as `<key>`."""
    if not isinstance(mapping, dict):
        return None
    return sorted(str(key) if key in SAFE_KEYS else "<key>" for key in mapping)[:limit]


def leaks(value):
    """The pattern kinds still present in `value` (placeholders excluded), scanned in overlapping windows."""
    found = set()
    for start in range(0, max(len(value), 1), WINDOW):
        chunk = normalize(value[max(0, start - OVERLAP):start + WINDOW])
        found.update(kind for kind, pattern in PATTERNS
                     if any(_replace(kind)(m) != m.group(0) for m in pattern.finditer(chunk)))
        if any(_random_run(match) != match.group(0) for match in LONG_RUN.finditer(chunk)):
            found.add("token")
    return sorted(found)


def skeleton(results, kinds):
    """The minimal results written when the leak check refuses the full file: masked blockers, the cleanup status,
    the call ledger totals and the leak kinds, never an id or a value."""
    kinds = sorted({kind if str(kind).startswith("pattern:") else "secret" for kind in kinds})
    cleanup = results.get("cleanup") if isinstance(results.get("cleanup"), dict) else {}
    ledger = results.get("call_ledger") if isinstance(results.get("call_ledger"), dict) else {}
    return dict(_meta=dict(sanitized=True, skeleton=True, note="the full results matched the leak check and were withheld"),
                blockers=[text(str(b)) for b in results.get("blockers") or []][:20], results_withheld=True,
                cleanup={k: v for k, v in cleanup.items() if type(v) in (bool, int) or v is None},
                call_ledger={p: {k: v for k, v in (t or {}).items() if type(v) is int} for p, t in ledger.items()
                             if isinstance(t, dict)}, facts_withheld=kinds)
