"""Claude Code side of the D6 native route: controls, assignment observation and review collection.

Claude exposes no model catalog or thread API, so the catalog below is documentation, not observation, and an
assignment is observed from the hook-recorded spawn chain. Nothing here dispatches, resumes or messages an agent.
"""

import hashlib
import json
import shutil
import subprocess
import time

from runtime.adapters import claude_subagents as subagents
from runtime.adapters.claude_code import CLAUDE_VERSION
from runtime.startup import StartupError

SURFACE = "claude-code-cli"
MECHANISM = subagents.MECHANISM
CONTROLS = ("spawn", "explicit_model")
# The Agent tool's documented `model` aliases. Reasoning effort is not controllable, so the only effort is "default".
CATALOG = tuple(dict(model=alias, supported_efforts=["default"], default_effort="default")
                for alias in ("haiku", "sonnet", "opus"))
LIMITATIONS = ["The model list is the Agent tool's documented aliases; Claude exposes no catalog call.",
               "Reasoning effort cannot be requested or observed; it is recorded as default.",
               "Dispatch controls are coordinator observations; they grant no execution authority.",
               "Subagent provenance comes from hook-written state a subagent with a shell could alter; the coordinator's independent assessment is the control."]


def _executable():
    return shutil.which("claude")


def _observed_version():
    from runtime.doctor import ObservationError, version
    executable = _executable()
    if executable is None:
        raise StartupError("Claude Code is not installed")
    try:
        return version(executable, time.monotonic() + 10, CLAUDE_VERSION)
    except (ObservationError, OSError, ValueError, subprocess.TimeoutExpired):
        raise StartupError("Claude Code version is unobserved; dispatch capabilities remain unverified") from None


def discover_native_workers(scope, controls):
    """The documented dispatch surface, bound to the observed `claude --version`."""
    if (not isinstance(controls, dict) or controls.get("version") != 1 or controls.get("client_surface") != SURFACE
            or not isinstance(controls.get("dispatch_controls"), dict) or set(controls["dispatch_controls"]) != set(CONTROLS)
            or any(type(value) is not bool for value in controls["dispatch_controls"].values())):
        raise StartupError("Claude dispatch controls are unverified")
    parent = subagents._text(controls.get("parent_session_id"), "parent session", 256)
    capabilities = dict(client_surface=SURFACE, originator="claude-code", parent_model_provider="anthropic",
                        catalog=[dict(item) for item in CATALOG], dispatch_controls=dict(controls["dispatch_controls"]))
    digest = hashlib.sha256(json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dict(version=1, native_version=_observed_version(), parent_session_id=parent, **capabilities,
                capability_digest=digest, limitations=list(LIMITATIONS))


def _binding(scope, attempt_id, parent_turn_id, spawn_item_id):
    from runtime import delegation
    for value in (attempt_id, parent_turn_id, spawn_item_id):
        subagents._text(value, "identity", 256)
    binding = delegation.prepared_native_review_assignment(scope, attempt_id)
    agent = subagents.agent_for_spawn(scope, binding["parent_session_id"], parent_turn_id, spawn_item_id)
    if agent is None:
        raise StartupError("No recorded subagent was spawned by this call")
    return binding, agent


def observe_native_review_assignment(scope, attempt_id, parent_turn_id, spawn_item_id, *, cooperative=False):
    """The D6 worker-receipt shape for one prepared review, from a complete recorded chain."""
    if cooperative:
        raise StartupError("Claude Code has no cooperative activity observation")
    binding, agent = _binding(scope, attempt_id, parent_turn_id, spawn_item_id)
    seen = subagents.observe_worker(scope, session_id=binding["parent_session_id"], agent_id=agent,
                                    marker=binding["task_name"], native_version=binding["native_version"])
    verified = subagents.chain(scope, binding["parent_session_id"], agent)
    result = verified["stop"]["message"]
    if seen["spawn_requested_model"] != binding["requested_model"] or seen["turn_status"] != "completed":
        raise StartupError("The observed subagent differs from the prepared assignment")
    receipt = dict(version=2, mechanism=MECHANISM, attempt_id=attempt_id, task_name=binding["task_name"], agent_path=None,
                   native_version=binding["native_version"], client_surface=SURFACE, originator="claude-code",
                   parent_model_provider="anthropic", worker_model_provider="anthropic",
                   parent_session_id=binding["parent_session_id"], parent_turn_id=parent_turn_id,
                   spawn_item_id=spawn_item_id, worker_id=agent, worker_turn_id=agent, turn_status="completed",
                   result_item_id=seen["result_item_id"], checkout=str(scope.repository_root),
                   assignment_digest=binding["assignment_digest"], candidate_digest=binding["candidate_digest"],
                   requested_model=binding["requested_model"], requested_effort=binding["requested_effort"],
                   configured_model=None, configured_effort=None, spawn_observed_model=seen["spawn_observed_model"],
                   spawn_observed_effort=None, execution_model=None, execution_effort=None, descendants_stopped=False,
                   prompt_delivery="unverified", result_digest=seen["result_digest"])
    return dict(receipt=receipt, result=result)


def collect_native_review(scope, attempt_id, parent_turn_id, spawn_item_id):
    """The review receipt and report from the recorded chain; never dispatches or messages."""
    from runtime.evidence import prepared_native_review
    binding, agent = _binding(scope, attempt_id, parent_turn_id, spawn_item_id)
    review = prepared_native_review(scope, attempt_id)
    observed = subagents.collect_review(
        scope, session_id=binding["parent_session_id"], agent_id=agent, marker=binding["task_name"], attempt_id=attempt_id,
        assignment_digest=review["assignment_digest"], candidate_digest=review["candidate_digest"],
        coverage=review["requested_coverage"], native_version=binding["native_version"],
        requested_model=binding["requested_model"], requested_effort=binding["requested_effort"])
    if prepared_native_review(scope, attempt_id) != review:
        raise StartupError("The review assignment changed during collection")
    return observed
