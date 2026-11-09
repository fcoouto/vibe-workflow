"""Strict masking for recorded upstream facts (the ChatGPT request's `session_facts`), whose results are committed.

Upstream error texts may name the account (emails, `org-`/`user-`/`acct_` ids), carry credentials (keys, JWTs,
`Bearer` values) or request ids. Every recorded string value goes through `mask`, every recorded key name through
`safe_keys` (a fixed allow-list), and code-like values through `code_value`. `leaks` checks for the same patterns
so that facts still containing one are withheld instead of written.
"""
import hashlib
import re

CAP = 200
# Ordered: the most specific shapes first, so a JWT is not reported as a long base64 run.
PATTERNS = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")),
    ("credential", re.compile(r"\b(?:Bearer|Basic|Token)\s+([A-Za-z0-9._~+/=-]{6,})", re.I)),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("url", re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)),
    ("path", re.compile(r"(?:/Users|/home|/root|/private/var/folders|/var/folders)/[^\s'\"<>]*|[A-Za-z]:\\Users\\[^\s'\"<>]*")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("id", re.compile(r"\b(?:org|user|acct|proj|sk|sess|ses|req|resp|chatcmpl|msg|thr|cred)[-_]([A-Za-z0-9_-]{4,})")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")),
    ("hex", re.compile(r"\b[0-9a-fA-F]{20,}\b")),
)
# A long run of base64url-ish characters is masked when it looks random: it has a digit, or both letter cases.
LONG_RUN = re.compile(r"[A-Za-z0-9+/_=-]{24,}")
# Key names from the OpenCode 2.2.6 session and error schemas and OpenAI error bodies; any other name is `<key>`.
SAFE_KEYS = frozenset((
    "code", "error", "error_description", "message", "reason", "param", "type", "status", "finish", "rawFinish",
    "retryAfterMs", "response", "body", "tokens", "input", "output", "reasoning", "cache", "read", "write", "total",
    "cost", "time", "completed", "created", "streamed", "updated", "busy", "idle", "attempt", "at", "retry", "next",
    "model", "providerID", "id", "variant", "agent", "content", "native", "snapshot", "metadata", "outcome",
    "sessionID", "parentID", "info", "data", "action", "provider", "title", "label", "link", "details", "cursor",
    "projectID", "location", "permissions", "fork", "subpath", "revert", "previous", "text", "files", "agents", "skills",
    "access", "refresh", "id_token", "expires"))  # OpenCode's OAuth record names (their values are never recorded)
# Refusal and error codes kept verbatim (the 2.2.6 chatgpt plugin's own list and common OpenAI codes).
KNOWN_CODES = ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_user_not_eligible",
               "subscription_sharing_unsupported_capability", "subscription_sharing_route_not_supported",
               "subscription_sharing_invalid_user", "chatpass_v2_scope_not_authorized",
               "chatpass_v2_invalid_authorization_context", "usage_limit_reached", "rate_limit_exceeded",
               "insufficient_quota", "model_not_found", "unsupported_value", "unsupported_parameter",
               "invalid_request_error", "context_length_exceeded", "invalid_reasoning_effort")
CODE = re.compile(r"[a-z][a-z0-9_]{3,60}")
TYPE_NAME = re.compile(r"[A-Z][A-Za-z0-9]{2,60}")  # OpenCode error class names such as APIError


def _random(value):
    return bool(re.search(r"[0-9]", value) or (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)))


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


def _replace(kind):
    def replace(match):
        if kind == "credential" and not (_random(match.group(1)) or len(match.group(1)) >= 16):
            return match.group(0)  # "Token limit exceeded" is prose, not a credential
        if kind == "id" and not _random_segments(match.group(1), 4):
            return match.group(0)  # `user_dirs` is a name, not an account id
        return "<redacted:%s>" % kind
    return replace


def mask(text):
    """`(masked text, whether anything was replaced)`."""
    masked = text
    for kind, pattern in PATTERNS:
        masked = pattern.sub(_replace(kind), masked)
    masked = LONG_RUN.sub(_random_run, masked)
    return masked, masked != text


def text(value, cap=CAP):
    """A masked, capped string, or None."""
    return mask(value)[0][:cap] if isinstance(value, str) and value.strip() else None


def message(value):
    """A message record: masked and capped text, whether it was masked, a 12-hex SHA-256 prefix of the raw text (to
    correlate repeats without storing it) and the known codes it contains."""
    if not isinstance(value, str) or not value.strip():
        return None
    masked, changed = mask(value)
    return dict(text=masked[:CAP], masked=changed or len(masked) > CAP,
                sha256=hashlib.sha256(value.encode()).hexdigest()[:12], codes=[c for c in KNOWN_CODES if c in value])


def code_value(value, type_name=False):
    """A code-like value kept verbatim (a known code, a snake_case word, or with `type_name` a class name such as
    APIError) only when masking leaves it unchanged; anything else is None."""
    if not isinstance(value, str):
        return None
    if value in KNOWN_CODES:
        return value
    shape = TYPE_NAME if type_name and value[:1].isupper() else CODE
    return value if shape.fullmatch(value) and not mask(value)[1] else None


def safe_keys(mapping, limit=40):
    """Key names from the allow-list; every other name is recorded as `<key>`."""
    if not isinstance(mapping, dict):
        return None
    return sorted(str(key) if key in SAFE_KEYS else "<key>" for key in mapping)[:limit]


def leaks(value):
    """The pattern kinds still present in `value` (placeholders excluded)."""
    found = [kind for kind, pattern in PATTERNS if any(_replace(kind)(m) != m.group(0) for m in pattern.finditer(value))]
    if any(_random_run(match) != match.group(0) for match in LONG_RUN.finditer(value)):
        found.append("token")
    return found
