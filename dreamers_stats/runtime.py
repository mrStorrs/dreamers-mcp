from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import html
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO


SCHEMA_VERSION = 1

CLIENTS = {"copilot", "codex"}
CLIENT_HOME_ENV = {
    "copilot": "COPILOT_HOME",
    "codex": "CODEX_HOME",
}
CLIENT_DEFAULT_DIR = {
    "copilot": ".copilot",
    "codex": ".codex",
}

REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "timestamp",
    "event_type",
    "repo_path",
    "source",
    "metrics",
)

OPTIONAL_FIELDS = (
    "session_id",
    "run_id",
    "repo_name",
    "branch",
    "skill",
    "status",
)

TOKEN_SOURCES = {"exact", "estimated", "unavailable"}
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "ai_credits",
)

SENSITIVE_KEY_NAMES = {
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "credentials",
    "authorization",
    "auth_header",
    "bearer_token",
    "token",
}

SENSITIVE_KEY_EXCEPTIONS = {
    "token_source",
}

SAFE_CONTENT_KEYS = {
    "diff_count",
    "prompt_count",
    "prompt_counts",
    "prompt_id",
    "prompt_ids",
    "tool_output_count",
    "transcript_count",
}

PROHIBITED_CONTENT_KEYS = {
    "diff",
    "diff_text",
    "full_prompt",
    "patch",
    "prompt",
    "prompt_text",
    "request_body",
    "response_body",
    "tool_output",
    "tool_outputs",
    "tool_result",
    "tool_results",
    "transcript",
    "transcript_text",
}

SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)

HookSpecValue = str | Callable[[dict[str, Any]], Any]
HookSpec = dict[str, HookSpecValue]
MetricSpec = dict[str, Any]
EventSpec = dict[str, Any]

SKILL_MODES = {"task-description", "plan-path", "manifest"}
GATE_TYPES = {
    "plan-approval",
    "implementation-start",
    "major-refactor",
    "review-rerun",
    "user-testing",
    "pre-pr",
    "pr-selection",
    "push-decision",
}
GATE_DECISIONS = {
    "approved",
    "approved_start_implementation",
    "approved_start_incremental",
    "approved_start_atomic",
    "revise",
    "revise_plan",
    "halt",
    "other",
    "apply_now",
    "defer",
    "defer_follow_up_plan",
    "continue_lite_scope",
    "run_vigil",
    "run_full_triad",
    "run_selected_lane",
    "skip",
    "skip_reviewer_rerun",
    "bug_found",
    "push_to_pr",
    "hold",
}
REVIEW_LANES = {"full", "standard", "sentinel", "probe", "hone", "vigil"}
VALIDATION_COMMAND_KINDS = {"typecheck", "test", "build", "lint", "manual"}
VALIDATION_RESULTS = {"pass", "fail", "skipped"}
VALIDATION_FAILURE_CATEGORIES = {
    "type-error",
    "test-failure",
    "timeout",
    "missing-command",
    "unknown",
}
RERUN_TRIGGERS = {
    "post_triad_fixes",
    "user_testing_bug",
    "major_change_gate",
    "user_selected_full",
    "user_selected_lane",
    "validation_risk",
    "pr_feedback",
    "optional_maintenance_review",
    "skipped_small_fix",
    "skipped_user_approved",
}
RERUN_DECISIONS = {
    "run_vigil",
    "run_full_triad",
    "run_selected_lane",
    "skip",
    "not_needed",
}
INVOCATION_SOURCES = {"standalone", "dreamers-full", "dreamers-lite", "dreamers-pr-resolve"}
HALT_REASON_CATEGORIES = {
    "blocked_reviewer",
    "user_halt",
    "validation_failure",
    "missing_pr",
    "missing_artifact",
    "graphql_failure",
    "push_held",
    "other_safe",
}
CYCLE_STATUSES = {"completed", "halted", "blocked"}
DOCS_STATUSES = {"updated", "skipped", "not-needed"}
PUSH_STATUSES = {"pushed", "held", "not-requested"}
FINAL_STATUSES = {"completed", "resolved", "approved"}
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
FINDING_LENSES = {
    "correctness",
    "security",
    "maintainability",
    "test-coverage",
    "simplicity",
}
FINDING_SEVERITY_ORDER = ("critical", "high", "medium", "low")
FINDING_LENS_ORDER = (
    "correctness",
    "security",
    "maintainability",
    "test-coverage",
    "simplicity",
)
ARTIFACT_SECTION_HEADINGS = {
    "findings",
    "plan alignment",
    "ac coverage",
    "full refactor findings",
    "observations",
    "open questions",
}
RELATIVE_RANGE_PATTERN = re.compile(r"^(?P<amount>\d+)(?P<unit>[dhm])$")
FINDING_LINE_PATTERN = re.compile(
    r"^- \[(?P<severity>critical|high|medium|low)\] "
    r"\[(?P<lens>correctness|security|maintainability|test-coverage|simplicity)\] "
)


HOOK_EVENT_SPECS: dict[str, HookSpec] = {
    "sessionStart": {
        "event_type": "session_started",
        "metrics": lambda payload: {
            "session_source": hook_value(payload, "source"),
            "initial_input_present": bool(hook_value(payload, "initialPrompt")),
        },
    },
    "sessionEnd": {
        "event_type": "session_completed",
        "metrics": lambda payload: {
            "reason": hook_value(payload, "reason"),
        },
    },
    "userPromptSubmitted": {
        "event_type": "prompt_submitted",
        "metrics": lambda payload: {
            "prompt_count": 1,
            "input_char_count": len(hook_value(payload, "prompt", default="")),
            "starts_with_slash": hook_value(payload, "prompt", default="").lstrip().startswith("/"),
        },
    },
    "postToolUse": {
        "event_type": "tool_completed",
        "metrics": lambda payload: {
            "tool_name": hook_value(payload, "toolName", "tool_name"),
            "result_type": hook_nested_value(
                payload,
                ("toolResult", "tool_result"),
                "resultType",
                "result_type",
                default="success",
            ),
        },
    },
    "postToolUseFailure": {
        "event_type": "tool_failed",
        "metrics": lambda payload: {
            "tool_name": hook_value(payload, "toolName", "tool_name"),
            "error_present": bool(hook_value(payload, "error")),
        },
    },
    "agentStop": {
        "event_type": "turn_completed",
        "metrics": lambda payload: {
            "stop_reason": hook_value(payload, "stopReason", "stop_reason"),
        },
    },
    "subagentStart": {
        "event_type": "subagent_started",
        "metrics": lambda payload: {
            "agent_name": hook_value(payload, "agentName", "agent_name"),
            "agent_display_name": hook_value(payload, "agentDisplayName", "agent_display_name"),
        },
    },
    "subagentStop": {
        "event_type": "subagent_completed",
        "metrics": lambda payload: {
            "agent_name": hook_value(payload, "agentName", "agent_name"),
            "agent_display_name": hook_value(payload, "agentDisplayName", "agent_display_name"),
            "stop_reason": hook_value(payload, "stopReason", "stop_reason"),
        },
    },
    "errorOccurred": {
        "event_type": "error_occurred",
        "status": lambda payload: "recoverable" if bool(hook_value(payload, "recoverable")) else "terminal",
        "metrics": lambda payload: {
            "error_name": hook_nested_value(payload, ("error",), "name", default="unknown"),
            "error_context": hook_value(payload, "errorContext", "error_context"),
            "recoverable": bool(hook_value(payload, "recoverable")),
        },
    },
    "preCompact": {
        "event_type": "compaction_started",
        "metrics": lambda payload: {
            "trigger": hook_value(payload, "trigger"),
            "instructions_present": bool(
                hook_value(payload, "customInstructions", "custom_instructions")
            ),
        },
    },
    "SessionStart": {
        "event_type": "session_started",
        "metrics": lambda payload: {
            "session_source": hook_value(payload, "source"),
            "initial_input_present": bool(hook_value(payload, "initialPrompt")),
        },
    },
    "UserPromptSubmit": {
        "event_type": "prompt_submitted",
        "metrics": lambda payload: {
            "prompt_count": 1,
            "input_char_count": len(hook_value(payload, "prompt", default="")),
            "starts_with_slash": hook_value(payload, "prompt", default="").lstrip().startswith("/"),
        },
    },
    "PostToolUse": {
        "event_type": "tool_completed",
        "metrics": lambda payload: {
            "tool_name": hook_value(payload, "toolName", "tool_name"),
            "result_type": codex_result_type(payload),
        },
    },
    "PreCompact": {
        "event_type": "compaction_started",
        "metrics": lambda payload: {
            "trigger": hook_value(payload, "trigger"),
            "instructions_present": bool(
                hook_value(payload, "customInstructions", "custom_instructions")
            ),
        },
    },
    "SubagentStart": {
        "event_type": "subagent_started",
        "metrics": lambda payload: {
            "agent_name": hook_value(payload, "agentName", "agent_name", "agent_type"),
            "agent_display_name": hook_value(payload, "agentDisplayName", "agent_display_name", "agent_type"),
        },
    },
    "SubagentStop": {
        "event_type": "subagent_completed",
        "metrics": lambda payload: {
            "agent_name": hook_value(payload, "agentName", "agent_name", "agent_type"),
            "agent_display_name": hook_value(payload, "agentDisplayName", "agent_display_name", "agent_type"),
            "stop_reason": hook_value(payload, "stopReason", "stop_reason"),
        },
    },
    "Stop": {
        "event_type": "turn_completed",
        "metrics": lambda payload: {
            "stop_reason": hook_value(payload, "stopReason", "stop_reason"),
        },
    },
}

SKILL_EVENT_SPECS: dict[str, MetricSpec] = {
    "skill_started": {
        "enum_fields": {
            "mode": SKILL_MODES,
            "lane": REVIEW_LANES,
            "invocation_source": INVOCATION_SOURCES,
        },
        "int_fields": ("plan_count", "pr_number", "unresolved_thread_count"),
        "string_fields": ("strategy", "plan_path", "pr_url"),
    },
    "skill_completed": {
        "enum_fields": {
            "docs_status": DOCS_STATUSES,
            "push_status": PUSH_STATUSES,
            "final_status": FINAL_STATUSES,
        },
        "int_fields": (
            "accepted_count",
            "rejected_count",
            "resolved_thread_count",
            "review_count",
            "rereview_count",
            "plan_count",
        ),
        "string_fields": ("commit_hash", "plan_path", "pr_url"),
        "bool_fields": ("docs_updated",),
    },
    "skill_halted": {
        "required_fields": ("halt_reason_category",),
        "enum_fields": {
            "halt_reason_category": HALT_REASON_CATEGORIES,
            "gate_type": GATE_TYPES,
            "lane": REVIEW_LANES,
        },
        "int_fields": ("open_question_count", "unresolved_thread_count"),
        "string_fields": ("plan_path", "reviewer", "artifact_path"),
        "bool_fields": ("user_selected",),
    },
    "phase_started": {
        "required_fields": ("phase_name",),
        "string_fields": ("phase_name", "plan_path", "step_name", "strategy"),
        "int_fields": ("phase_index", "plan_position"),
    },
    "gate_presented": {
        "required_fields": ("gate_type",),
        "enum_fields": {"gate_type": GATE_TYPES},
        "string_fields": (
            "plan_path",
            "reviewer",
            "severity",
            "lens",
            "location",
            "breadth_estimate",
            "trigger_category",
            "requested_lane",
        ),
        "list_string_fields": ("option_categories",),
    },
    "gate_decided": {
        "required_fields": ("gate_type", "decision"),
        "enum_fields": {
            "gate_type": GATE_TYPES,
            "decision": GATE_DECISIONS,
        },
        "string_fields": (
            "plan_path",
            "follow_up_plan_path",
            "trigger_category",
            "requested_lane",
        ),
        "int_fields": ("bug_count", "follow_up_plan_count"),
        "bool_fields": ("user_selected",),
    },
    "validation_attempt": {
        "required_fields": ("command_kind", "command_label", "attempt_number", "result"),
        "enum_fields": {
            "command_kind": VALIDATION_COMMAND_KINDS,
            "result": VALIDATION_RESULTS,
            "failure_category": VALIDATION_FAILURE_CATEGORIES,
        },
        "string_fields": ("command_label", "scope", "plan_path"),
        "int_fields": ("attempt_number", "duration_ms"),
    },
    "review_pass_started": {
        "required_fields": ("lane", "reviewers"),
        "enum_fields": {"lane": REVIEW_LANES, "trigger": RERUN_TRIGGERS},
        "string_fields": ("review_pass_id", "plan_path", "invocation_source"),
        "bool_fields": ("is_rereview",),
        "list_string_fields": ("reviewers",),
    },
    "review_pass_completed": {
        "required_fields": ("lane", "reviewers", "artifact_paths", "blocked", "open_question_count"),
        "enum_fields": {"lane": REVIEW_LANES, "trigger": RERUN_TRIGGERS},
        "string_fields": ("review_pass_id", "plan_path", "invocation_source"),
        "bool_fields": ("is_rereview", "blocked"),
        "int_fields": ("open_question_count",),
        "list_string_fields": ("reviewers", "artifact_paths"),
        "count_object_fields": {
            "findings_by_severity": FINDING_SEVERITIES,
            "findings_by_lens": FINDING_LENSES,
        },
    },
    "review_findings_applied": {
        "string_fields": ("review_pass_id", "follow_up_plan_path", "plan_path"),
        "int_fields": (
            "applied_count",
            "deferred_count",
            "continued_count",
            "open_question_count",
            "accepted_count",
            "rejected_count",
        ),
        "bool_fields": ("rereview_needed",),
        "list_string_fields": ("follow_up_plan_paths",),
    },
    "rerun_decision": {
        "required_fields": ("trigger", "decision"),
        "enum_fields": {
            "trigger": RERUN_TRIGGERS,
            "decision": RERUN_DECISIONS,
        },
        "string_fields": ("reason_category", "requested_lane", "plan_path"),
        "bool_fields": ("user_selected",),
    },
    "cycle_completed": {
        "required_fields": ("plan_path",),
        "enum_fields": {
            "cycle_status": CYCLE_STATUSES,
            "validation_status": VALIDATION_RESULTS,
        },
        "string_fields": ("plan_path",),
        "int_fields": ("review_count", "rereview_count", "bug_count"),
    },
    "pr_created": {
        "string_fields": ("pr_url", "target_branch", "commit_hash"),
        "int_fields": ("pr_number",),
        "bool_fields": ("draft",),
    },
    "retro_written": {
        "required_fields": ("retro_path",),
        "string_fields": ("retro_path",),
        "int_fields": ("cycle_count",),
    },
}

EVENT_SPECS: dict[str, EventSpec] = {
    "session_started": {"allowed_sources": {"hook"}, "default_status": "started"},
    "session_completed": {"allowed_sources": {"hook"}, "default_status": "completed"},
    "prompt_submitted": {"allowed_sources": {"hook"}, "default_status": "submitted"},
    "turn_completed": {"allowed_sources": {"hook"}, "default_status": "completed"},
    "tool_requested": {"allowed_sources": {"hook"}, "default_status": "requested"},
    "tool_completed": {"allowed_sources": {"hook"}, "default_status": "completed"},
    "tool_failed": {"allowed_sources": {"hook"}, "default_status": "failed"},
    "subagent_started": {"allowed_sources": {"hook"}, "default_status": "started"},
    "subagent_completed": {"allowed_sources": {"hook"}, "default_status": "completed"},
    "error_occurred": {"allowed_sources": {"hook"}, "default_status": "terminal"},
    "compaction_started": {"allowed_sources": {"hook"}, "default_status": "started"},
    "skill_started": {
        "allowed_sources": {"skill"},
        "default_status": "started",
        "metric_spec": SKILL_EVENT_SPECS["skill_started"],
    },
    "skill_completed": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["skill_completed"],
    },
    "skill_halted": {
        "allowed_sources": {"skill"},
        "default_status": "halted",
        "metric_spec": SKILL_EVENT_SPECS["skill_halted"],
    },
    "phase_started": {
        "allowed_sources": {"skill"},
        "default_status": "started",
        "metric_spec": SKILL_EVENT_SPECS["phase_started"],
    },
    "gate_presented": {
        "allowed_sources": {"skill"},
        "default_status": "presented",
        "metric_spec": SKILL_EVENT_SPECS["gate_presented"],
    },
    "gate_decided": {
        "allowed_sources": {"skill"},
        "default_status": "decided",
        "metric_spec": SKILL_EVENT_SPECS["gate_decided"],
    },
    "validation_attempt": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["validation_attempt"],
    },
    "review_pass_started": {
        "allowed_sources": {"skill"},
        "default_status": "started",
        "metric_spec": SKILL_EVENT_SPECS["review_pass_started"],
    },
    "review_pass_completed": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["review_pass_completed"],
    },
    "review_findings_applied": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["review_findings_applied"],
    },
    "rerun_decision": {
        "allowed_sources": {"skill"},
        "default_status": "decided",
        "metric_spec": SKILL_EVENT_SPECS["rerun_decision"],
    },
    "cycle_completed": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["cycle_completed"],
    },
    "pr_created": {
        "allowed_sources": {"skill"},
        "default_status": "created",
        "metric_spec": SKILL_EVENT_SPECS["pr_created"],
    },
    "retro_written": {
        "allowed_sources": {"skill"},
        "default_status": "completed",
        "metric_spec": SKILL_EVENT_SPECS["retro_written"],
    },
    "token_usage_recorded": {"allowed_sources": {"summary", "skill"}, "default_status": "completed"},
}

ALLOWED_EVENT_TYPES = set(EVENT_SPECS)
ALLOWED_SOURCES = {source for spec in EVENT_SPECS.values() for source in spec["allowed_sources"]}


@dataclass(frozen=True)
class ClientContext:
    client: str
    home: Path


class StatsValidationError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def default_client_home(client: str) -> Path:
    if client not in CLIENTS:
        raise StatsValidationError("invalid_client", "client must be copilot or codex")
    configured = os.environ.get(CLIENT_HOME_ENV[client])
    if configured:
        return Path(configured).expanduser()
    return Path.home() / CLIENT_DEFAULT_DIR[client]


def default_copilot_home() -> Path:
    return default_client_home("copilot")


def resolve_copilot_home(copilot_home: str | Path | None = None) -> Path:
    if copilot_home is None:
        return default_copilot_home()
    return Path(copilot_home).expanduser()


def infer_client(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    candidates: set[str] = set()
    scope = env if env is not None else os.environ

    configured = scope.get("DREAMERS_STATS_CLIENT") or scope.get("DREAMERS_CLIENT")
    if configured:
        if configured not in CLIENTS:
            raise StatsValidationError("invalid_client", "client must be copilot or codex")
        return configured

    for client, env_name in CLIENT_HOME_ENV.items():
        if scope.get(env_name):
            candidates.add(client)

    if payload is not None:
        direct = payload.get("client") or payload.get("runtime")
        if isinstance(direct, str) and direct in CLIENTS:
            candidates.add(direct)
        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            metrics_client = metrics.get("client") or metrics.get("runtime")
            if isinstance(metrics_client, str) and metrics_client in CLIENTS:
                candidates.add(metrics_client)
        normalized_keys = {normalize_key(key) for key in payload}
        if "codexhome" in normalized_keys or "codex" in normalized_keys:
            candidates.add("codex")
        if "copilothome" in normalized_keys or "copilot" in normalized_keys:
            candidates.add("copilot")

    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        raise StatsValidationError(
            "ambiguous_client",
            "client could not be inferred; pass --client or set DREAMERS_STATS_CLIENT",
        )
    raise StatsValidationError(
        "ambiguous_client",
        "client inference was ambiguous; pass --client explicitly",
    )


def resolve_client_context(
    client: str | None = None,
    home: str | Path | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> ClientContext:
    resolved_client = client or infer_client(payload=payload, env=env)
    if resolved_client not in CLIENTS:
        raise StatsValidationError("invalid_client", "client must be copilot or codex")
    resolved_home = Path(home).expanduser() if home is not None else default_client_home(resolved_client)
    return ClientContext(client=resolved_client, home=resolved_home)


def stats_dir(
    *,
    client: str | None = None,
    home: str | Path | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    context = resolve_client_context(client=client, home=home, payload=payload, env=env)
    return context.home / "dreamers" / "stats"


def events_path(
    *,
    client: str | None = None,
    home: str | Path | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    return stats_dir(client=client, home=home, payload=payload, env=env) / "events.jsonl"


def record_event(
    event: dict[str, Any],
    *,
    client: str | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    normalized = normalize_event(event)
    destination = events_path(client=client, home=home, payload=event, env=env)
    destination.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    with destination.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")
    return normalized["event_id"]


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise StatsValidationError("invalid_event", "event must be a JSON object")
    validate_event(event)
    normalize_token_metrics(event)
    fill_best_effort_metadata(event)
    return redact_event(event)


def validate_event(event: dict[str, Any]) -> None:
    for field in REQUIRED_FIELDS:
        if field not in event or event[field] in ("", None):
            raise StatsValidationError("missing_required_field", f"missing required field: {field}")

    if event["schema_version"] != SCHEMA_VERSION:
        raise StatsValidationError("unsupported_schema_version", "unsupported schema_version")

    _require_string(event, "event_id")
    _require_string(event, "timestamp")
    _require_string(event, "event_type")
    _require_string(event, "repo_path")
    _require_string(event, "source")
    _validate_event_id(event["event_id"])

    event_spec = EVENT_SPECS.get(event["event_type"])
    if event_spec is None:
        raise StatsValidationError("invalid_event_type", "event_type is not recognized")

    if event["source"] not in ALLOWED_SOURCES or event["source"] not in event_spec["allowed_sources"]:
        raise StatsValidationError("invalid_source", "source is not allowed for this event_type")

    if not isinstance(event["metrics"], dict):
        raise StatsValidationError("invalid_metrics", "metrics must be a JSON object")

    for field in OPTIONAL_FIELDS:
        if field in event and event[field] is not None and not isinstance(event[field], str):
            raise StatsValidationError("invalid_optional_field", f"{field} must be a string when present")

    _validate_timestamp(event["timestamp"])
    metric_spec = event_spec.get("metric_spec")
    if metric_spec is not None:
        validate_checkpoint_metrics(metric_spec, event["metrics"])


def _require_string(event: dict[str, Any], field: str) -> None:
    if not isinstance(event[field], str) or not event[field].strip():
        raise StatsValidationError("invalid_field_type", f"{field} must be a non-empty string")


def _validate_event_id(value: str) -> None:
    if len(value) > 96 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value):
        raise StatsValidationError("invalid_event_id", "event_id must be a compact identifier")


def _validate_timestamp(value: str) -> None:
    parse_iso_timestamp(value)


def validate_checkpoint_metrics(spec: MetricSpec, metrics: dict[str, Any]) -> None:
    required_fields = set(spec.get("required_fields", ()))
    enum_fields = spec.get("enum_fields", {})
    string_fields = set(spec.get("string_fields", ()))
    int_fields = set(spec.get("int_fields", ()))
    bool_fields = set(spec.get("bool_fields", ()))
    list_string_fields = set(spec.get("list_string_fields", ()))
    count_object_fields = spec.get("count_object_fields", {})

    allowed_keys = set(required_fields)
    allowed_keys.update(enum_fields.keys())
    allowed_keys.update(string_fields)
    allowed_keys.update(int_fields)
    allowed_keys.update(bool_fields)
    allowed_keys.update(list_string_fields)
    allowed_keys.update(count_object_fields.keys())

    for field in required_fields:
        if field not in metrics:
            raise StatsValidationError("missing_metric", f"missing required metric: {field}")

    for key in metrics:
        if key not in allowed_keys:
            raise StatsValidationError("invalid_metric_key", f"metric is not allowed: {key}")

    for field, values in enum_fields.items():
        if field in metrics and metrics[field] not in values:
            raise StatsValidationError("invalid_metric_enum", f"invalid value for metric: {field}")
    for field in string_fields:
        if field in metrics and metrics[field] is not None and (
            not isinstance(metrics[field], str) or not metrics[field].strip()
        ):
            raise StatsValidationError("invalid_metric_type", f"{field} must be a non-empty string")
    for field in int_fields:
        if field in metrics:
            validate_metric_int(metrics[field], field)
    for field in bool_fields:
        if field in metrics and not isinstance(metrics[field], bool):
            raise StatsValidationError("invalid_metric_type", f"{field} must be a boolean")
    for field in list_string_fields:
        if field in metrics:
            validate_metric_string_list(metrics[field], field)
    for field, allowed_names in count_object_fields.items():
        if field in metrics:
            validate_metric_count_object(metrics[field], field, allowed_names)


def validate_metric_int(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatsValidationError("invalid_metric_type", f"{field} must be an integer")


def validate_metric_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise StatsValidationError("invalid_metric_type", f"{field} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise StatsValidationError("invalid_metric_type", f"{field} must be a list of strings")


def validate_metric_count_object(
    value: Any,
    field: str,
    allowed_keys: set[str] | Iterable[str] | None = None,
) -> None:
    if not isinstance(value, dict):
        raise StatsValidationError("invalid_metric_type", f"{field} must be an object")
    allowed = set(allowed_keys or ())
    for key, item in value.items():
        if allowed and key not in allowed:
            raise StatsValidationError("invalid_metric_enum", f"invalid metric category: {key}")
        validate_metric_int(item, f"{field}.{key}")


def build_checkpoint_event(args: argparse.Namespace) -> dict[str, Any]:
    metrics = load_metrics_json(args.metrics_json)
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "",
        "timestamp": resolve_checkpoint_timestamp(args.timestamp),
        "event_type": args.event_type,
        "repo_path": str(Path(args.repo_path).resolve()) if args.repo_path else str(Path.cwd().resolve()),
        "source": "skill",
        "status": args.status or default_status_for_event(args.event_type),
        "skill": args.skill,
        "run_id": args.run_id,
        "session_id": args.session_id,
        "branch": args.branch,
        "metrics": metrics,
    }
    event["event_id"] = checkpoint_event_id(event)
    return event


def load_metrics_json(raw: str | None) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise StatsValidationError("invalid_metrics", "metrics must be a JSON object")
    return payload


def resolve_checkpoint_timestamp(value: str | None) -> str:
    if value is None:
        return utc_now_iso()
    parse_iso_timestamp(value)
    return value


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def checkpoint_event_id(event: dict[str, Any]) -> str:
    raw = "|".join(
        (
            event["event_type"],
            event.get("skill") or "",
            event.get("run_id") or "",
            event["timestamp"],
            json.dumps(event["metrics"], sort_keys=True),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"skill_{event['event_type']}_{digest}"


def build_hook_event(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise StatsValidationError("invalid_event", "event must be a JSON object")
    spec = HOOK_EVENT_SPECS.get(event_name)
    if spec is None:
        raise StatsValidationError("invalid_hook_event", "unsupported hook event")

    event_type = spec["event_type"]
    repo_path = hook_value(payload, "cwd", "repoPath", "repo_path")
    if not isinstance(repo_path, str) or not repo_path.strip():
        raise StatsValidationError("invalid_field_type", "repo_path must be a non-empty string")

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "",
        "timestamp": hook_timestamp(payload),
        "event_type": event_type,
        "repo_path": repo_path,
        "source": "hook",
        "status": resolve_hook_status(spec, payload, event_type),
        "session_id": hook_value(payload, "sessionId", "session_id", "turn_id", "turnId"),
        "metrics": resolve_hook_spec_value(spec["metrics"], payload),
    }
    event["event_id"] = hook_event_id(event_name, event)
    return event


def build_hook_events(event_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    primary = build_hook_event(event_name, payload)
    events = [primary]
    if event_name == "Stop":
        events.append(build_unavailable_token_event(primary))
    return events


def build_unavailable_token_event(primary_event: dict[str, Any]) -> dict[str, Any]:
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "",
        "timestamp": primary_event["timestamp"],
        "event_type": "token_usage_recorded",
        "repo_path": primary_event["repo_path"],
        "source": "summary",
        "status": default_status_for_event("token_usage_recorded"),
        "session_id": primary_event.get("session_id"),
        "metrics": {
            "token_source": "unavailable",
            "attribution_scope": "turn",
        },
    }
    raw = "|".join(
        (
            primary_event["event_id"],
            event["timestamp"],
            json.dumps(event["metrics"], sort_keys=True),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    event["event_id"] = f"summary_token_usage_{digest}"
    return event


def codex_result_type(payload: dict[str, Any]) -> str:
    for parent_key in ("toolResult", "tool_result", "toolResponse", "tool_response"):
        parent = hook_value(payload, parent_key)
        if not isinstance(parent, dict):
            continue
        for child_key in ("resultType", "result_type", "status"):
            value = parent.get(child_key)
            if isinstance(value, str) and value.strip():
                return value
    return "success"


def hook_event_id(event_name: str, event: dict[str, Any]) -> str:
    raw = "|".join(
        (
            event_name,
            event["event_type"],
            event.get("session_id") or "",
            event["timestamp"],
            json.dumps(event["metrics"], sort_keys=True),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"hook_{event['event_type']}_{digest}"


def hook_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def hook_nested_value(
    payload: dict[str, Any],
    parent_keys: tuple[str, ...],
    *child_keys: str,
    default: Any = None,
) -> Any:
    parent = hook_value(payload, *parent_keys)
    if not isinstance(parent, dict):
        return default
    return hook_value(parent, *child_keys, default=default)


def resolve_hook_spec_value(value: HookSpecValue, payload: dict[str, Any]) -> Any:
    if callable(value):
        return value(payload)
    return value


def resolve_hook_status(spec: HookSpec, payload: dict[str, Any], event_type: str) -> str:
    if "status" not in spec:
        return default_status_for_event(event_type)
    return resolve_hook_spec_value(spec["status"], payload)


def default_status_for_event(event_type: str) -> str:
    event_spec = EVENT_SPECS.get(event_type)
    if event_spec is None:
        raise StatsValidationError("invalid_event_type", "event_type is not recognized")
    return event_spec["default_status"]


def hook_timestamp(payload: dict[str, Any]) -> str:
    value = hook_value(payload, "timestamp")
    if value in (None, ""):
        return utc_now_iso()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC).replace(microsecond=0).isoformat().replace(
            "+00:00",
            "Z",
        )
    if isinstance(value, str):
        parse_iso_timestamp(value)
        return value
    raise StatsValidationError("invalid_timestamp", "hook timestamp must be epoch milliseconds or ISO text")


def parse_iso_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise StatsValidationError("invalid_timestamp", "timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise StatsValidationError("invalid_timestamp", "timestamp must include a timezone")
    return parsed


def normalize_token_metrics(event: dict[str, Any]) -> None:
    if event["event_type"] != "token_usage_recorded":
        return
    metrics = event["metrics"]
    source_quality = metrics.get("token_source")
    if source_quality not in TOKEN_SOURCES:
        raise StatsValidationError("invalid_token_source", "token_source must be exact, estimated, or unavailable")

    if source_quality == "unavailable":
        for field in TOKEN_FIELDS:
            metrics[field] = None
        return

    for field in TOKEN_FIELDS:
        if field not in metrics or metrics[field] is None:
            continue
        if field == "ai_credits":
            validate_metric_number(metrics[field], field)
        else:
            validate_metric_int(metrics[field], field)

    model = metrics.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise StatsValidationError("invalid_metric_type", "model must be a non-empty string")


def validate_metric_number(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StatsValidationError("invalid_metric_type", f"{field} must be numeric")


def fill_best_effort_metadata(event: dict[str, Any]) -> None:
    event.setdefault("repo_name", derive_repo_name(event["repo_path"]))
    for field in OPTIONAL_FIELDS:
        event.setdefault(field, None)


def derive_repo_name(repo_path: str) -> str | None:
    name = Path(repo_path).name
    return name or None


def redact_event(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: redact_event(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_event(item, key) for item in value]
    if is_prohibited_content_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        if is_sensitive_key(key) or contains_sensitive_value(value):
            return "[REDACTED]"
    return value


def is_sensitive_key(key: str | None) -> bool:
    normalized = normalize_key(key)
    if normalized is None or normalized in SENSITIVE_KEY_EXCEPTIONS:
        return False
    tokens = key_tokens(normalized)
    return any(token in SENSITIVE_KEY_NAMES for token in tokens)


def is_prohibited_content_key(key: str | None) -> bool:
    normalized = normalize_key(key)
    if normalized is None:
        return False
    if normalized in SAFE_CONTENT_KEYS:
        return False
    tokens = key_tokens(normalized)
    if normalized in PROHIBITED_CONTENT_KEYS:
        return True
    return any(token in PROHIBITED_CONTENT_KEYS for token in tokens)


def normalize_key(key: str | None) -> str | None:
    if key is None:
        return None
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_") or None


def key_tokens(normalized_key: str) -> list[str]:
    return [token for token in normalized_key.split("_") if token]


def contains_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS)


def doctor(
    *,
    client: str | None = None,
    home: str | Path | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    directory = stats_dir(client=client, home=home, payload=payload, env=env)
    event_log = events_path(client=client, home=home, payload=payload, env=env)
    report = {
        "writable": False,
        "stats_dir": str(directory),
        "events_file": str(event_log),
        "event_count": 0,
        "malformed_line_count": 0,
        "error": None,
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        report["writable"] = True
        if event_log.exists():
            for line in event_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    report["malformed_line_count"] += 1
                    continue
                if isinstance(payload, dict):
                    report["event_count"] += 1
                else:
                    report["malformed_line_count"] += 1
    except OSError as exc:
        report["error"] = str(exc)
    return report


def load_report_events(
    *,
    client: str | None = None,
    home: str | Path | None = None,
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    event_log = events_path(client=client, home=home, payload=payload, env=env)
    if not event_log.exists():
        return [], 0

    events: list[dict[str, Any]] = []
    warning_count = 0
    for line in event_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            warning_count += 1
            continue
        normalized = normalize_report_event(payload)
        if normalized is None:
            warning_count += 1
            continue
        events.append(normalized)
    return events, warning_count


def normalize_report_event(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("metrics"), dict):
        return None
    if not isinstance(payload.get("repo_path"), str) or not payload["repo_path"].strip():
        return None
    if not isinstance(payload.get("event_type"), str) or not payload["event_type"].strip():
        return None
    if not isinstance(payload.get("timestamp"), str) or not payload["timestamp"].strip():
        return None

    try:
        parsed_timestamp = parse_iso_timestamp(payload["timestamp"])
    except StatsValidationError:
        return None

    normalized = dict(payload)
    normalized["metrics"] = dict(payload["metrics"])
    normalized["_parsed_timestamp"] = parsed_timestamp.astimezone(UTC)
    return normalized


def build_report_filters(
    *,
    repo: str = "current",
    skill: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    if repo not in {"current", "all"}:
        raise StatsValidationError("invalid_report_filter", "repo must be current or all")
    parsed_since = parse_report_boundary(since, is_end=False) if since else None
    parsed_until = parse_report_boundary(until, is_end=True) if until else None
    if parsed_since is not None and parsed_until is not None and parsed_since > parsed_until:
        raise StatsValidationError("invalid_date_range", "--since must be earlier than --until")
    current_repo = detect_repo_root(cwd or Path.cwd()) if repo == "current" else None
    return {
        "repo": repo,
        "skill": skill,
        "since": datetime_to_iso(parsed_since),
        "until": datetime_to_iso(parsed_until),
        "current_repo": str(current_repo) if current_repo is not None else None,
        "_since": parsed_since,
        "_until": parsed_until,
        "_current_repo": current_repo,
    }


def parse_report_boundary(value: str, *, is_end: bool) -> datetime:
    match = RELATIVE_RANGE_PATTERN.fullmatch(value)
    if match is not None:
        amount = int(match.group("amount"))
        unit = match.group("unit")
        delta = {
            "d": timedelta(days=amount),
            "h": timedelta(hours=amount),
            "m": timedelta(minutes=amount),
        }[unit]
        return datetime.now(UTC) - delta

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = datetime.fromisoformat(value).replace(tzinfo=UTC)
        if is_end:
            return parsed + timedelta(days=1) - timedelta(microseconds=1)
        return parsed

    parsed = parse_iso_timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def detect_repo_root(start: str | Path) -> Path:
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return current
        current = current.parent


def filter_report_events(events: Iterable[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    since = filters["_since"]
    until = filters["_until"]
    current_repo = filters["_current_repo"]
    skill = filters["skill"]
    repo_mode = filters["repo"]
    for event in events:
        if repo_mode == "current" and current_repo is not None and not event_matches_repo(event, current_repo):
            continue
        if skill is not None and event.get("skill") != skill:
            continue
        timestamp = event["_parsed_timestamp"]
        if since is not None and timestamp < since:
            continue
        if until is not None and timestamp > until:
            continue
        filtered.append(event)
    return filtered


def event_matches_repo(event: dict[str, Any], current_repo: Path) -> bool:
    event_path = Path(event["repo_path"]).expanduser().resolve(strict=False)
    return event_path == current_repo or current_repo in event_path.parents or event_path in current_repo.parents


def datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def event_range(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [event["_parsed_timestamp"] for event in events]
    if not timestamps:
        return {"first_timestamp": None, "last_timestamp": None}
    return {
        "first_timestamp": datetime_to_iso(min(timestamps)),
        "last_timestamp": datetime_to_iso(max(timestamps)),
    }


def empty_count_dict(keys: Iterable[str]) -> dict[str, int]:
    return {key: 0 for key in keys}


def merge_count_dicts(target: dict[str, int], source: dict[str, Any], keys: Iterable[str]) -> None:
    for key in keys:
        target[key] += int(source.get(key, 0) or 0)


def build_runs_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    runs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events:
        run_id = event.get("run_id")
        skill = event.get("skill")
        if not run_id or not skill:
            continue
        run_key = (run_id, event["repo_path"], skill)
        run = runs.setdefault(
            run_key,
            {
                "run_id": run_id,
                "repo_path": event["repo_path"],
                "skill": skill,
                "status": "in_progress",
                "first_timestamp": event["_parsed_timestamp"],
                "last_timestamp": event["_parsed_timestamp"],
                "start_timestamp": None,
                "end_timestamp": None,
            },
        )
        run["first_timestamp"] = min(run["first_timestamp"], event["_parsed_timestamp"])
        run["last_timestamp"] = max(run["last_timestamp"], event["_parsed_timestamp"])
        if event["event_type"] == "skill_started":
            run["start_timestamp"] = event["_parsed_timestamp"]
        elif event["event_type"] == "skill_completed":
            run["end_timestamp"] = event["_parsed_timestamp"]
            run["status"] = event["metrics"].get("final_status") or event.get("status") or "completed"
        elif event["event_type"] == "skill_halted":
            run["end_timestamp"] = event["_parsed_timestamp"]
            run["status"] = event.get("status") or "halted"

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs.values():
        start_timestamp = run["start_timestamp"] or run["first_timestamp"]
        end_timestamp = run["end_timestamp"] or run["last_timestamp"]
        duration_seconds = int((end_timestamp - start_timestamp).total_seconds())
        duration_seconds = max(duration_seconds, 0)
        key = (run["skill"], run["status"])
        group = groups.setdefault(
            key,
            {
                "skill": run["skill"],
                "status": run["status"],
                "run_count": 0,
                "total_duration_seconds": 0,
                "first_timestamp": start_timestamp,
                "last_timestamp": end_timestamp,
            },
        )
        group["run_count"] += 1
        group["total_duration_seconds"] += duration_seconds
        group["first_timestamp"] = min(group["first_timestamp"], start_timestamp)
        group["last_timestamp"] = max(group["last_timestamp"], end_timestamp)

    group_rows = []
    for key in sorted(groups):
        group = groups[key]
        average_duration = 0
        if group["run_count"]:
            average_duration = int(group["total_duration_seconds"] / group["run_count"])
        group_rows.append(
            {
                "skill": group["skill"],
                "status": group["status"],
                "run_count": group["run_count"],
                "total_duration_seconds": group["total_duration_seconds"],
                "average_duration_seconds": average_duration,
                "first_timestamp": datetime_to_iso(group["first_timestamp"]),
                "last_timestamp": datetime_to_iso(group["last_timestamp"]),
            }
        )

    run_range = {"first_timestamp": None, "last_timestamp": None}
    if runs:
        run_range = {
            "first_timestamp": datetime_to_iso(min(run["first_timestamp"] for run in runs.values())),
            "last_timestamp": datetime_to_iso(max(run["last_timestamp"] for run in runs.values())),
        }
    return {
        "report_type": "runs",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "run_count": len(runs),
        "range": run_range,
        "groups": group_rows,
    }


def resolve_review_artifacts(event: dict[str, Any]) -> list[Path]:
    repo_root = Path(event["repo_path"]).expanduser()
    seen: set[str] = set()
    resolved: list[Path] = []
    for path_text in event["metrics"].get("artifact_paths", []):
        if not isinstance(path_text, str) or not path_text.strip():
            continue
        path = Path(path_text)
        if not path.is_absolute():
            path = repo_root / path
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def parse_review_artifact(path: Path) -> dict[str, Any]:
    summary = {
        "found": False,
        "blocked": False,
        "open_question_count": 0,
        "findings_by_severity": empty_count_dict(FINDING_SEVERITY_ORDER),
        "findings_by_lens": empty_count_dict(FINDING_LENS_ORDER),
    }
    if not path.exists():
        return summary

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return summary
    summary["found"] = True
    status_line = next((line.strip() for line in lines if line.strip()), "")
    normalized_status = status_line
    if normalized_status.lower().startswith("status:"):
        normalized_status = normalized_status.split(":", 1)[1].strip()
    summary["blocked"] = normalized_status.startswith("Blocked")

    sections = split_artifact_sections(lines)
    for line in sections.get("findings", []):
        match = FINDING_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        summary["findings_by_severity"][match.group("severity")] += 1
        summary["findings_by_lens"][match.group("lens")] += 1

    open_question_lines = [line.strip() for line in sections.get("open questions", []) if line.strip()]
    if open_question_lines and not (len(open_question_lines) == 1 and open_question_lines[0].lower() == "none"):
        summary["open_question_count"] = sum(
            1
            for line in open_question_lines
            if line.startswith("- ")
            or re.match(r"^\d+\.\s+", line) is not None
            or line.lower() != "none"
        )

    return summary


def normalize_heading(value: str) -> str:
    if value.endswith(":"):
        value = value[:-1]
    return re.sub(r"\s+", " ", value.strip().lower())


def split_artifact_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    for line in lines:
        heading = line.strip().lower()
        if heading in ARTIFACT_SECTION_HEADINGS:
            current_section = heading
            sections.setdefault(current_section, [])
            continue
        if current_section is not None:
            sections[current_section].append(line)
    return sections


def review_event_has_artifact_mismatch(metrics: dict[str, Any], parsed: dict[str, Any]) -> bool:
    stored_severity = empty_count_dict(FINDING_SEVERITY_ORDER)
    stored_lens = empty_count_dict(FINDING_LENS_ORDER)
    merge_count_dicts(stored_severity, metrics.get("findings_by_severity", {}), FINDING_SEVERITY_ORDER)
    merge_count_dicts(stored_lens, metrics.get("findings_by_lens", {}), FINDING_LENS_ORDER)
    if stored_severity != parsed["findings_by_severity"]:
        return True
    if stored_lens != parsed["findings_by_lens"]:
        return True
    if bool(metrics.get("blocked")) != parsed["blocked"]:
        return True
    return int(metrics.get("open_question_count", 0) or 0) != parsed["open_question_count"]


def build_reviews_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    review_events = [event for event in events if event["event_type"] == "review_pass_completed"]
    lane_counts: Counter[str] = Counter()
    reviewer_counts: Counter[str] = Counter()
    trigger_counts: Counter[str] = Counter()
    findings_by_severity = empty_count_dict(FINDING_SEVERITY_ORDER)
    findings_by_lens = empty_count_dict(FINDING_LENS_ORDER)
    initial_review_count = 0
    rereview_count = 0
    blocked_count = 0
    open_question_count = 0
    artifact_cache: dict[str, dict[str, Any]] = {}
    parsed_artifact_paths: set[str] = set()
    missing_artifact_paths: set[str] = set()
    mismatches: list[dict[str, Any]] = []

    for event in review_events:
        metrics = event["metrics"]
        lane = metrics.get("lane")
        if isinstance(lane, str) and lane:
            lane_counts[lane] += 1
        reviewers = metrics.get("reviewers", [])
        if isinstance(reviewers, list):
            reviewer_counts.update([reviewer for reviewer in reviewers if isinstance(reviewer, str)])

        is_rereview = bool(metrics.get("is_rereview"))
        if is_rereview:
            rereview_count += 1
            trigger = metrics.get("trigger")
            if isinstance(trigger, str) and trigger:
                trigger_counts[trigger] += 1
        else:
            initial_review_count += 1

        event_artifacts = resolve_review_artifacts(event)
        event_missing = False
        artifact_aggregate = {
            "blocked": False,
            "open_question_count": 0,
            "findings_by_severity": empty_count_dict(FINDING_SEVERITY_ORDER),
            "findings_by_lens": empty_count_dict(FINDING_LENS_ORDER),
        }
        for artifact_path in event_artifacts:
            parsed = artifact_cache.get(str(artifact_path))
            if parsed is None:
                parsed = parse_review_artifact(artifact_path)
                artifact_cache[str(artifact_path)] = parsed
            if not parsed["found"]:
                event_missing = True
                missing_artifact_paths.add(str(artifact_path))
                continue
            parsed_artifact_paths.add(str(artifact_path))
            if parsed["blocked"]:
                artifact_aggregate["blocked"] = True
            merge_count_dicts(
                artifact_aggregate["findings_by_severity"],
                parsed["findings_by_severity"],
                FINDING_SEVERITY_ORDER,
            )
            merge_count_dicts(
                artifact_aggregate["findings_by_lens"],
                parsed["findings_by_lens"],
                FINDING_LENS_ORDER,
            )
            artifact_aggregate["open_question_count"] += parsed["open_question_count"]
        count_source = artifact_aggregate if event_artifacts and not event_missing else metrics
        if bool(count_source.get("blocked")):
            blocked_count += 1
        open_question_count += int(count_source.get("open_question_count", 0) or 0)
        merge_count_dicts(findings_by_severity, count_source.get("findings_by_severity", {}), FINDING_SEVERITY_ORDER)
        merge_count_dicts(findings_by_lens, count_source.get("findings_by_lens", {}), FINDING_LENS_ORDER)

        if event_artifacts and not event_missing and review_event_has_artifact_mismatch(metrics, artifact_aggregate):
            mismatches.append(
                {
                    "review_pass_id": metrics.get("review_pass_id"),
                    "artifact_paths": [str(path) for path in event_artifacts],
                }
            )

    return {
        "report_type": "reviews",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "review_count": len(review_events),
        "initial_review_count": initial_review_count,
        "rereview_count": rereview_count,
        "lane_counts": dict(lane_counts),
        "reviewer_counts": dict(reviewer_counts),
        "blocked_count": blocked_count,
        "open_question_count": open_question_count,
        "findings_by_severity": findings_by_severity,
        "findings_by_lens": findings_by_lens,
        "rereview_trigger_counts": dict(trigger_counts),
        "artifact_summary": {
            "parsed_count": len(parsed_artifact_paths),
            "missing_count": len(missing_artifact_paths),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        },
        "range": event_range(review_events),
    }


def build_validation_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    validation_events = [event for event in events if event["event_type"] == "validation_attempt"]
    command_kinds: dict[str, dict[str, int]] = {}
    final_attempts: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in validation_events:
        metrics = event["metrics"]
        kind = metrics.get("command_kind")
        label = metrics.get("command_label")
        if not isinstance(kind, str) or not isinstance(label, str):
            continue
        stats = command_kinds.setdefault(
            kind,
            {
                "attempt_count": 0,
                "failure_count": 0,
                "retry_count": 0,
                "final_pass_count": 0,
                "final_fail_count": 0,
            },
        )
        stats["attempt_count"] += 1
        if metrics["result"] == "fail":
            stats["failure_count"] += 1
        if int(metrics.get("attempt_number", 0) or 0) > 1:
            stats["retry_count"] += 1
        key = (
            event.get("run_id"),
            event.get("repo_path"),
            kind,
            label,
            metrics.get("scope"),
            metrics.get("plan_path"),
        )
        previous = final_attempts.get(key)
        if previous is None or should_replace_validation_attempt(previous, event):
            final_attempts[key] = event

    for event in final_attempts.values():
        kind = event["metrics"]["command_kind"]
        result = event["metrics"].get("result")
        if result == "pass":
            command_kinds[kind]["final_pass_count"] += 1
        elif result == "fail":
            command_kinds[kind]["final_fail_count"] += 1
    return {
        "report_type": "validation",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "attempt_count": len(validation_events),
        "command_kinds": command_kinds,
        "range": event_range(validation_events),
    }


def should_replace_validation_attempt(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    previous_attempt = int(previous["metrics"].get("attempt_number", 0) or 0)
    candidate_attempt = int(candidate["metrics"].get("attempt_number", 0) or 0)
    if candidate_attempt != previous_attempt:
        return candidate_attempt > previous_attempt
    return candidate["_parsed_timestamp"] > previous["_parsed_timestamp"]


def build_gates_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    gate_events = [event for event in events if event["event_type"] == "gate_decided"]
    gate_type_counts: Counter[str] = Counter()
    decision_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in gate_events:
        gate_type = event["metrics"]["gate_type"]
        decision = event["metrics"]["decision"]
        gate_type_counts[gate_type] += 1
        decision_counts[gate_type][decision] += 1
    return {
        "report_type": "gates",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "gate_type_counts": dict(sorted(gate_type_counts.items())),
        "decision_counts": {
            gate_type: dict(sorted(counter.items()))
            for gate_type, counter in sorted(decision_counts.items())
        },
        "range": event_range(gate_events),
    }


def summarize_token_source(source_quality: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [event for event in events if event["metrics"].get("token_source") == source_quality]
    totals = empty_token_totals()
    skills: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    sessions: dict[str, dict[str, Any]] = {}

    for event in rows:
        metrics = event["metrics"]
        if source_quality != "unavailable":
            merge_token_totals(totals, metrics)
        skill = event.get("skill") or "unknown"
        skills.setdefault(skill, empty_token_totals())
        if source_quality != "unavailable":
            merge_token_totals(skills[skill], metrics)
        model = metrics.get("model")
        if model:
            models.setdefault(model, empty_token_totals())
            if source_quality != "unavailable":
                merge_token_totals(models[model], metrics)
        session_id = event.get("session_id") or f"event:{event['event_id']}"
        session = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "row_count": 0,
                **empty_token_totals(),
            },
        )
        session["row_count"] += 1
        if source_quality != "unavailable":
            merge_token_totals(session, metrics)

    if source_quality == "unavailable":
        totals = {field: None for field in TOKEN_FIELDS}

    return {
        "source_quality": source_quality,
        "row_count": len(rows),
        "session_count": len(sessions),
        "totals": totals,
        "sessions": sorted(sessions.values(), key=lambda item: item["session_id"]),
        "skills": skills,
        "models": models,
    }


def empty_token_totals() -> dict[str, int | float]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "ai_credits": 0.0,
    }


def merge_token_totals(target: dict[str, Any], metrics: dict[str, Any]) -> None:
    for field in TOKEN_FIELDS:
        value = metrics.get(field)
        if value is None:
            continue
        target[field] = target.get(field, 0) + value


def build_tokens_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    token_events = [event for event in events if event["event_type"] == "token_usage_recorded"]
    return {
        "report_type": "tokens",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "exact": summarize_token_source("exact", token_events),
        "estimated": summarize_token_source("estimated", token_events),
        "unavailable": summarize_token_source("unavailable", token_events),
        "range": event_range(token_events),
    }


def build_summary_report(events: list[dict[str, Any]], warning_count: int, filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_type": "summarize",
        "warning_count": warning_count,
        "filters": report_filters_public(filters),
        "runs": build_runs_report(events, warning_count, filters),
        "reviews": build_reviews_report(events, warning_count, filters),
        "validation": build_validation_report(events, warning_count, filters),
        "gates": build_gates_report(events, warning_count, filters),
        "tokens": build_tokens_report(events, warning_count, filters),
    }


def report_filters_public(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": filters["repo"],
        "skill": filters["skill"],
        "since": filters["since"],
        "until": filters["until"],
    }


def format_runs_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers runs report", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    lines.append(f"Runs: {report['run_count']}")
    for group in report["groups"][:8]:
        duration = group["average_duration_seconds"]
        duration_text = format_duration(duration) if duration is not None else "n/a"
        lines.append(
            f"- {group['skill']} [{group['status']}] runs={group['run_count']} avg={duration_text}"
        )
    return "\n".join(lines)


def format_reviews_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers reviews report", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    lines.append(f"Reviews: {report['review_count']} rereviews={report['rereview_count']}")
    lines.append(f"Findings: {format_counter_map(report['findings_by_severity'])}")
    lines.append(f"Artifact mismatches: {report['artifact_summary']['mismatch_count']}")
    return "\n".join(lines)


def format_validation_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers validation report", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    lines.append(f"Attempts: {report['attempt_count']}")
    for kind, summary in report["command_kinds"].items():
        if summary["attempt_count"] == 0:
            continue
        lines.append(
            f"- {kind} attempts={summary['attempt_count']} fails={summary['failure_count']} retries={summary['retry_count']}"
        )
    return "\n".join(lines)


def format_gates_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers gates report", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    lines.append(f"Gates: {format_counter_map(report['gate_type_counts'])}")
    return "\n".join(lines)


def format_tokens_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers tokens report", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    for source_quality in ("exact", "estimated", "unavailable"):
        summary = report[source_quality]
        total_tokens = summary["totals"]["total_tokens"] if source_quality != "unavailable" else "n/a"
        lines.append(
            f"- {source_quality}: rows={summary['row_count']} sessions={summary['session_count']} total_tokens={total_tokens}"
        )
    return "\n".join(lines)


def format_summary_report(report: dict[str, Any]) -> str:
    lines = ["Dreamers stats summary", format_filter_header(report["filters"])]
    lines.extend(format_warning_lines(report["warning_count"]))
    lines.append("Skill runs")
    lines.extend(format_summary_block_from_runs(report["runs"]))
    lines.append("Reviews")
    lines.extend(format_summary_block_from_reviews(report["reviews"]))
    lines.append("Validation")
    lines.extend(format_summary_block_from_validation(report["validation"]))
    lines.append("Gates")
    lines.extend(format_summary_block_from_gates(report["gates"]))
    lines.append("Tokens")
    lines.extend(format_summary_block_from_tokens(report["tokens"]))
    return "\n".join(lines[:30])


def format_summary_block_from_runs(report: dict[str, Any]) -> list[str]:
    if report["run_count"] == 0:
        return ["- none"]
    lines = [f"- {report['run_count']} runs"]
    if report["groups"]:
        first = report["groups"][0]
        lines.append(f"- {first['skill']} [{first['status']}] x{first['run_count']}")
    return lines


def format_summary_block_from_reviews(report: dict[str, Any]) -> list[str]:
    return [f"- {report['review_count']} reviews", f"- findings {format_counter_map(report['findings_by_severity'])}"]


def format_summary_block_from_validation(report: dict[str, Any]) -> list[str]:
    return [f"- {report['attempt_count']} attempts"]


def format_summary_block_from_gates(report: dict[str, Any]) -> list[str]:
    return [f"- {format_counter_map(report['gate_type_counts']) or 'none'}"]


def format_summary_block_from_tokens(report: dict[str, Any]) -> list[str]:
    return [f"- exact total {report['exact']['totals']['total_tokens']}"]


def html_text(value: Any) -> str:
    return html.escape(str(value), quote=True)


def html_count(value: Any) -> str:
    if value is None:
        return "n/a"
    return html_text(value)


def html_metric_card(label: str, value: Any, detail: str) -> str:
    return (
        '<section class="metric-card">'
        f"<span>{html_text(label)}</span>"
        f"<strong>{html_count(value)}</strong>"
        f"<small>{html_text(detail)}</small>"
        "</section>"
    )


def html_table(headers: list[str], rows: list[list[Any]], empty_text: str) -> str:
    header_cells = "".join(f"<th>{html_text(header)}</th>" for header in headers)
    if rows:
        body_rows = []
        for row in rows:
            body_rows.append("<tr>" + "".join(f"<td>{html_count(cell)}</td>" for cell in row) + "</tr>")
        body = "".join(body_rows)
    else:
        body = f'<tr><td colspan="{len(headers)}" class="empty">{html_text(empty_text)}</td></tr>'
    return f"<table><thead><tr>{header_cells}</tr></thead><tbody>{body}</tbody></table>"


def html_definition_list(items: list[tuple[str, Any]]) -> str:
    if not items:
        return '<p class="empty">none</p>'
    rows = []
    for label, value in items:
        rows.append(f"<div><dt>{html_text(label)}</dt><dd>{html_count(value)}</dd></div>")
    return "<dl>" + "".join(rows) + "</dl>"


def counter_items(values: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(key, value) for key, value in values.items() if value]


def nested_counter_items(values: dict[str, dict[str, Any]]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for group, counters in values.items():
        formatted = format_counter_map(counters)
        if formatted:
            items.append((group, formatted))
    return items


def render_dashboard_html(report: dict[str, Any], *, client: str, generated_at: str | None = None) -> str:
    generated = generated_at or utc_now_iso()
    runs = report["runs"]
    reviews = report["reviews"]
    validation = report["validation"]
    gates = report["gates"]
    tokens = report["tokens"]
    filters = report["filters"]
    warning_count = int(report.get("warning_count", 0) or 0)
    warning_html = ""
    if warning_count:
        warning_html = (
            '<section class="warning">'
            f"<strong>Warnings</strong>"
            f"<span>skipped {warning_count} malformed historical lines</span>"
            "</section>"
        )

    filter_text = " ".join(
        f"{name}={value}"
        for name, value in {
            "client": client,
            "repo": filters.get("repo"),
            "skill": filters.get("skill") or "all",
            "since": filters.get("since") or "beginning",
            "until": filters.get("until") or "now",
        }.items()
    )
    run_rows = [
        [
            group["skill"],
            group["status"],
            group["run_count"],
            format_duration(group["average_duration_seconds"]),
            group["last_timestamp"],
        ]
        for group in runs["groups"]
    ]
    validation_rows = [
        [
            kind,
            summary["attempt_count"],
            summary["failure_count"],
            summary["retry_count"],
            summary["final_pass_count"],
            summary["final_fail_count"],
        ]
        for kind, summary in validation["command_kinds"].items()
    ]
    token_items = [
        (
            source_quality,
            f"rows={summary['row_count']} sessions={summary['session_count']} total_tokens="
            + ("n/a" if source_quality == "unavailable" else str(summary["totals"]["total_tokens"])),
        )
        for source_quality, summary in (
            ("exact", tokens["exact"]),
            ("estimated", tokens["estimated"]),
            ("unavailable", tokens["unavailable"]),
        )
    ]

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Dreamers Stats</title>",
            "<style>",
            ":root{color-scheme:light;--ink:#17211b;--muted:#5d6b62;--line:#d8e0da;--paper:#f8faf7;--panel:#ffffff;--accent:#0f6f5f;--warn:#9a5b00}",
            "body{margin:0;background:linear-gradient(180deg,#eef5ef,#f8faf7 34%);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,sans-serif}",
            "main{max-width:1120px;margin:0 auto;padding:32px 20px 48px}",
            "header{margin-bottom:24px}h1{font-size:34px;line-height:1.1;margin:0 0 8px}h2{font-size:18px;margin:0 0 12px}",
            ".filters,.generated,small{color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}",
            ".metric-card,section.panel,.warning{border:1px solid var(--line);background:var(--panel);border-radius:8px;box-shadow:0 1px 1px rgba(23,33,27,.04)}",
            ".metric-card{padding:14px}.metric-card span{display:block;color:var(--muted);font-size:12px;text-transform:uppercase}.metric-card strong{display:block;font-size:26px;margin:4px 0}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.panel{padding:18px;margin:16px 0;overflow:auto}.warning{padding:12px 14px;color:var(--warn);margin:16px 0}",
            "table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}",
            "dl{display:grid;gap:8px;margin:0}dl div{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding:6px 0}dt{color:var(--muted)}dd{margin:0;text-align:right}.empty{color:var(--muted)}",
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            "<header>",
            "<h1>Dreamers Stats</h1>",
            f'<div class="filters">Filters: {html_text(filter_text)}</div>',
            f'<div class="generated">Generated: {html_text(generated)}</div>',
            "</header>",
            warning_html,
            '<section class="metrics">',
            html_metric_card("Runs", runs["run_count"], "skill invocations"),
            html_metric_card("Validation", validation["attempt_count"], "attempts"),
            html_metric_card("Reviews", reviews["review_count"], f"{reviews['blocked_count']} blocked"),
            html_metric_card("Gates", sum(gates["gate_type_counts"].values()), "decisions"),
            html_metric_card("Tokens", tokens["exact"]["totals"]["total_tokens"], "exact total"),
            "</section>",
            '<section class="panel"><h2>Runs by skill</h2>',
            html_table(["Skill", "Status", "Runs", "Avg duration", "Last seen"], run_rows, "no runs"),
            "</section>",
            '<section class="panel"><h2>Validation</h2>',
            html_table(
                ["Kind", "Attempts", "Failures", "Retries", "Final passes", "Final failures"],
                validation_rows,
                "no validation attempts",
            ),
            "</section>",
            '<section class="grid">',
            '<section class="panel"><h2>Reviews</h2>',
            html_definition_list(
                [
                    ("initial reviews", reviews["initial_review_count"]),
                    ("rereviews", reviews["rereview_count"]),
                    ("open questions", reviews["open_question_count"]),
                    ("findings", format_counter_map(reviews["findings_by_severity"]) or "none"),
                    ("artifact mismatches", reviews["artifact_summary"]["mismatch_count"]),
                ]
            ),
            "</section>",
            '<section class="panel"><h2>Gates</h2>',
            html_definition_list(counter_items(gates["gate_type_counts"]) + nested_counter_items(gates["decision_counts"])),
            "</section>",
            '<section class="panel"><h2>Tokens</h2>',
            html_definition_list(token_items),
            "</section>",
            "</section>",
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def format_filter_header(filters: dict[str, Any]) -> str:
    parts = [f"repo={filters['repo']}"]
    if filters.get("skill"):
        parts.append(f"skill={filters['skill']}")
    if filters.get("since"):
        parts.append(f"since={filters['since']}")
    if filters.get("until"):
        parts.append(f"until={filters['until']}")
    return "Filters: " + " ".join(parts)


def format_warning_lines(warning_count: int) -> list[str]:
    if warning_count == 0:
        return []
    return [f"Warnings: skipped {warning_count} malformed historical lines"]


def format_duration(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes}m{remainder}s"
    if minutes:
        return f"{minutes}m{remainder}s"
    return f"{remainder}s"


def format_counter_map(values: dict[str, Any]) -> str:
    items = [f"{key}={value}" for key, value in values.items() if value]
    return ", ".join(items)


REPORT_BUILDERS = {
    "runs": build_runs_report,
    "reviews": build_reviews_report,
    "validation": build_validation_report,
    "gates": build_gates_report,
    "tokens": build_tokens_report,
    "summarize": build_summary_report,
}

REPORT_FORMATTERS = {
    "runs": format_runs_report,
    "reviews": format_reviews_report,
    "validation": format_validation_report,
    "gates": format_gates_report,
    "tokens": format_tokens_report,
    "summarize": format_summary_report,
}


def run_report(
    command: str,
    *,
    client: str | None = None,
    home: str | Path | None = None,
    repo: str = "current",
    skill: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    events, warning_count = load_report_events(client=client, home=home)
    filters = build_report_filters(repo=repo, skill=skill, since=since, until=until, cwd=cwd)
    filtered = filter_report_events(events, filters)
    return REPORT_BUILDERS[command](filtered, warning_count, filters)


def run_report_command(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    context = resolve_args_context(args)
    report = run_report(
        args.command,
        client=context.client,
        home=context.home,
        repo=args.repo,
        skill=args.skill,
        since=args.since,
        until=args.until,
        cwd=Path.cwd(),
    )
    if args.json:
        return report, json.dumps(report, sort_keys=True)
    return report, REPORT_FORMATTERS[args.command](report)


def load_event(args: argparse.Namespace, stdin: TextIO) -> dict[str, Any]:
    if args.event_json is not None:
        payload = json.loads(args.event_json)
    else:
        payload = json.loads(stdin.read())
    if not isinstance(payload, dict):
        raise StatsValidationError("invalid_event", "event must be a JSON object")
    return payload


def add_client_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client", choices=sorted(CLIENTS))
    parser.add_argument("--home")
    parser.add_argument("--copilot-home")
    parser.add_argument("--codex-home")


def resolve_args_context(
    args: argparse.Namespace,
    payload: dict[str, Any] | None = None,
) -> ClientContext:
    explicit_client = getattr(args, "client", None)
    explicit_home = getattr(args, "home", None)
    copilot_home = getattr(args, "copilot_home", None)
    codex_home = getattr(args, "codex_home", None)

    alias_client: str | None = None
    alias_home: str | None = None
    if copilot_home and codex_home:
        raise StatsValidationError("conflicting_home", "choose only one client-specific home flag")
    if copilot_home:
        alias_client = "copilot"
        alias_home = copilot_home
    if codex_home:
        alias_client = "codex"
        alias_home = codex_home
    if alias_client is not None:
        if explicit_client is not None and explicit_client != alias_client:
            raise StatsValidationError("conflicting_client", "home flag conflicts with --client")
        explicit_client = alias_client
    if alias_home is not None:
        if explicit_home is not None and Path(explicit_home).expanduser() != Path(alias_home).expanduser():
            raise StatsValidationError("conflicting_home", "home flag conflicts with --home")
        explicit_home = alias_home
    return resolve_client_context(client=explicit_client, home=explicit_home, payload=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dreamers-stats")
    subcommands = parser.add_subparsers(dest="command", required=True)

    record_parser = subcommands.add_parser("record")
    add_client_options(record_parser)
    input_group = record_parser.add_mutually_exclusive_group()
    input_group.add_argument("--event-json")
    record_parser.add_argument("--print-event-id", action="store_true")

    doctor_parser = subcommands.add_parser("doctor")
    add_client_options(doctor_parser)
    doctor_parser.add_argument("--json", action="store_true")

    checkpoint_parser = subcommands.add_parser("checkpoint")
    add_client_options(checkpoint_parser)
    checkpoint_parser.add_argument("--event-type", required=True)
    checkpoint_parser.add_argument("--skill", required=True)
    checkpoint_parser.add_argument("--run-id", required=True)
    checkpoint_parser.add_argument("--status")
    checkpoint_parser.add_argument("--session-id")
    checkpoint_parser.add_argument("--branch")
    checkpoint_parser.add_argument("--repo-path")
    checkpoint_parser.add_argument("--timestamp")
    checkpoint_parser.add_argument("--metrics-json")
    checkpoint_parser.add_argument("--print-event-id", action="store_true")

    hook_parser = subcommands.add_parser("hook")
    add_client_options(hook_parser)
    hook_parser.add_argument("--event-name", required=True)
    hook_parser.add_argument("--event-json")

    for name in ("summarize", "runs", "reviews", "validation", "gates", "tokens"):
        report_parser = subcommands.add_parser(name)
        add_report_arguments(report_parser)

    dashboard_parser = subcommands.add_parser("dashboard")
    add_report_filter_arguments(dashboard_parser)
    dashboard_parser.add_argument("--output")

    return parser


def add_report_arguments(parser: argparse.ArgumentParser) -> None:
    add_report_filter_arguments(parser)
    parser.add_argument("--json", action="store_true")


def add_report_filter_arguments(parser: argparse.ArgumentParser) -> None:
    add_client_options(parser)
    parser.add_argument("--repo", choices=("current", "all"), default="current")
    parser.add_argument("--skill")
    parser.add_argument("--since")
    parser.add_argument("--until")


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_stream = stdin if stdin is not None else sys.stdin

    if args.command == "record":
        try:
            event = load_event(args, input_stream)
            context = resolve_args_context(args, payload=event)
            event_id = record_event(event, client=context.client, home=context.home)
        except json.JSONDecodeError:
            print("invalid_json", file=sys.stderr)
            return 2
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        except OSError:
            print("write_failed", file=sys.stderr)
            return 1
        if args.print_event_id:
            print(event_id)
        return 0

    if args.command == "checkpoint":
        try:
            context = resolve_args_context(args)
            event = build_checkpoint_event(args)
            event_id = record_event(event, client=context.client, home=context.home)
        except json.JSONDecodeError:
            print("invalid_json", file=sys.stderr)
            return 2
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        except OSError:
            print("write_failed", file=sys.stderr)
            return 1
        if args.print_event_id:
            print(event_id)
        return 0

    if args.command == "doctor":
        try:
            context = resolve_args_context(args)
            report = doctor(client=context.client, home=context.home)
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            status = "ok" if report["writable"] else "error"
            print(
                f"{status} writable={str(report['writable']).lower()} "
                f"events={report['event_count']} malformed={report['malformed_line_count']} "
                f"path={report['events_file']}"
            )
        return 0 if report["writable"] else 1

    if args.command == "hook":
        try:
            payload = load_event(args, input_stream)
            context = resolve_args_context(args, payload=payload)
            for event in build_hook_events(args.event_name, payload):
                record_event(event, client=context.client, home=context.home)
        except json.JSONDecodeError:
            print("invalid_json", file=sys.stderr)
            return 2
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        except OSError:
            print("write_failed", file=sys.stderr)
            return 1
        return 0

    if args.command in REPORT_BUILDERS:
        try:
            _report, output = run_report_command(args)
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        except OSError:
            print("read_failed", file=sys.stderr)
            return 1
        print(output)
        return 0

    if args.command == "dashboard":
        try:
            context = resolve_args_context(args)
            report = run_report(
                "summarize",
                client=context.client,
                home=context.home,
                repo=args.repo,
                skill=args.skill,
                since=args.since,
                until=args.until,
                cwd=Path.cwd(),
            )
            output = render_dashboard_html(report, client=context.client)
        except StatsValidationError as exc:
            print(exc.category, file=sys.stderr)
            return 2
        except OSError:
            print("read_failed", file=sys.stderr)
            return 1
        if args.output:
            try:
                output_path = Path(args.output).expanduser()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(output, encoding="utf-8")
            except OSError:
                print("write_failed", file=sys.stderr)
                return 1
        else:
            print(output)
        return 0

    parser.error("unknown command")
    return 2
