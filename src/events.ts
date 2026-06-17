import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";

import { StatsValidationError } from "./errors.js";
import type { Client, NormalizedStatsEvent, RuntimeOptions, StatsEventInput } from "./types.js";
import { expandHomePath } from "./utils.js";

type JsonRecord = Record<string, any>;

export const SCHEMA_VERSION = 1;
export const CLIENTS = new Set<Client>(["copilot", "codex"]);
const CLIENT_HOME_ENV: Record<Client, string> = {
  copilot: "COPILOT_HOME",
  codex: "CODEX_HOME",
};
const CLIENT_DEFAULT_DIR: Record<Client, string> = {
  copilot: ".copilot",
  codex: ".codex",
};
const REQUIRED_FIELDS = [
  "schema_version",
  "event_id",
  "timestamp",
  "event_type",
  "repo_path",
  "source",
  "metrics",
];
const OPTIONAL_FIELDS = [
  "session_id",
  "run_id",
  "repo_name",
  "branch",
  "skill",
  "status",
];
const TOKEN_SOURCES = new Set(["exact", "estimated", "unavailable"]);
const TOKEN_FIELDS = [
  "input_tokens",
  "output_tokens",
  "total_tokens",
  "cache_read_tokens",
  "cache_write_tokens",
  "ai_credits",
];
const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const CODEX_SESSION_MATCH_LIMIT = 8;
const CODEX_SESSION_DAY_DIR_LIMIT = 8;
const RELATIVE_RANGE_PATTERN = /^(?<amount>\d+)(?<unit>[dhm])$/;
const FINDING_LINE_PATTERN =
  /^- \[(?<severity>critical|high|medium|low)\] \[(?<lens>correctness|security|maintainability|test-coverage|simplicity)\] /;

const SENSITIVE_KEY_NAMES = new Set([
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
]);
const SENSITIVE_KEY_EXCEPTIONS = new Set(["token_source"]);
const SAFE_CONTENT_KEYS = new Set([
  "diff_count",
  "prompt_count",
  "prompt_counts",
  "prompt_id",
  "prompt_ids",
  "tool_output_count",
  "transcript_count",
]);
const PROHIBITED_CONTENT_KEYS = new Set([
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
]);
const SENSITIVE_VALUE_PATTERNS = [
  /ghp_[A-Za-z0-9_]{10,}/,
  /github_pat_[A-Za-z0-9_]{10,}/,
  /sk-[A-Za-z0-9]{10,}/,
  /Bearer\s+[A-Za-z0-9._~+/=-]{8,}/i,
];

const SKILL_MODES = new Set(["task-description", "plan-path", "manifest"]);
const GATE_TYPES = new Set([
  "plan-approval",
  "implementation-start",
  "major-refactor",
  "review-rerun",
  "user-testing",
  "pre-pr",
  "pr-selection",
  "push-decision",
]);
const GATE_DECISIONS = new Set([
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
]);
const REVIEW_LANES = new Set(["full", "standard", "sentinel", "probe", "hone", "vigil"]);
const VALIDATION_COMMAND_KINDS = new Set(["typecheck", "test", "build", "lint", "manual"]);
const VALIDATION_RESULTS = new Set(["pass", "fail", "skipped"]);
const VALIDATION_FAILURE_CATEGORIES = new Set([
  "type-error",
  "test-failure",
  "timeout",
  "missing-command",
  "unknown",
]);
const RERUN_TRIGGERS = new Set([
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
]);
const RERUN_DECISIONS = new Set([
  "run_vigil",
  "run_full_triad",
  "run_selected_lane",
  "skip",
  "not_needed",
]);
const INVOCATION_SOURCES = new Set(["standalone", "dreamers-full", "dreamers-lite", "dreamers-pr-resolve"]);
const HALT_REASON_CATEGORIES = new Set([
  "blocked_reviewer",
  "user_halt",
  "validation_failure",
  "missing_pr",
  "missing_artifact",
  "graphql_failure",
  "push_held",
  "other_safe",
]);
const CYCLE_STATUSES = new Set(["completed", "halted", "blocked"]);
const DOCS_STATUSES = new Set(["updated", "skipped", "not-needed"]);
const PUSH_STATUSES = new Set(["pushed", "held", "not-requested"]);
const FINAL_STATUSES = new Set(["completed", "resolved", "approved"]);
const FINDING_SEVERITY_ORDER = ["critical", "high", "medium", "low"];
const FINDING_LENS_ORDER = ["correctness", "security", "maintainability", "test-coverage", "simplicity"];
const FINDING_SEVERITIES = new Set(FINDING_SEVERITY_ORDER);
const FINDING_LENSES = new Set(FINDING_LENS_ORDER);
const TERMINAL_SKILL_EVENTS = new Set(["skill_completed", "skill_halted"]);
const ARTIFACT_SECTION_HEADINGS = new Set([
  "findings",
  "plan alignment",
  "ac coverage",
  "full refactor findings",
  "observations",
  "open questions",
]);

const SKILL_EVENT_SPECS: Record<string, JsonRecord> = {
  skill_started: {
    enum_fields: {
      mode: SKILL_MODES,
      lane: REVIEW_LANES,
      invocation_source: INVOCATION_SOURCES,
    },
    int_fields: ["plan_count", "pr_number", "unresolved_thread_count"],
    string_fields: ["strategy", "plan_path", "pr_url"],
  },
  skill_completed: {
    enum_fields: {
      docs_status: DOCS_STATUSES,
      push_status: PUSH_STATUSES,
      final_status: FINAL_STATUSES,
    },
    int_fields: [
      "accepted_count",
      "rejected_count",
      "resolved_thread_count",
      "review_count",
      "rereview_count",
      "plan_count",
    ],
    string_fields: ["commit_hash", "plan_path", "pr_url"],
    bool_fields: ["docs_updated"],
  },
  skill_halted: {
    required_fields: ["halt_reason_category"],
    enum_fields: {
      halt_reason_category: HALT_REASON_CATEGORIES,
      gate_type: GATE_TYPES,
      lane: REVIEW_LANES,
    },
    int_fields: ["open_question_count", "unresolved_thread_count"],
    string_fields: ["plan_path", "reviewer", "artifact_path"],
    bool_fields: ["user_selected"],
  },
  phase_started: {
    required_fields: ["phase_name"],
    string_fields: ["phase_name", "plan_path", "step_name", "strategy"],
    int_fields: ["phase_index", "plan_position"],
  },
  gate_presented: {
    required_fields: ["gate_type"],
    enum_fields: { gate_type: GATE_TYPES },
    string_fields: [
      "plan_path",
      "reviewer",
      "severity",
      "lens",
      "location",
      "breadth_estimate",
      "trigger_category",
      "requested_lane",
    ],
    list_string_fields: ["option_categories"],
  },
  gate_decided: {
    required_fields: ["gate_type", "decision"],
    enum_fields: {
      gate_type: GATE_TYPES,
      decision: GATE_DECISIONS,
    },
    string_fields: ["plan_path", "follow_up_plan_path", "trigger_category", "requested_lane"],
    int_fields: ["bug_count", "follow_up_plan_count"],
    bool_fields: ["user_selected"],
  },
  validation_attempt: {
    required_fields: ["command_kind", "command_label", "attempt_number", "result"],
    enum_fields: {
      command_kind: VALIDATION_COMMAND_KINDS,
      result: VALIDATION_RESULTS,
      failure_category: VALIDATION_FAILURE_CATEGORIES,
    },
    string_fields: ["command_label", "scope", "plan_path"],
    int_fields: ["attempt_number", "duration_ms"],
  },
  review_pass_started: {
    required_fields: ["lane", "reviewers"],
    enum_fields: { lane: REVIEW_LANES, trigger: RERUN_TRIGGERS },
    string_fields: ["review_pass_id", "plan_path", "invocation_source"],
    bool_fields: ["is_rereview"],
    list_string_fields: ["reviewers"],
  },
  review_pass_completed: {
    required_fields: ["lane", "reviewers", "artifact_paths", "blocked", "open_question_count"],
    enum_fields: { lane: REVIEW_LANES, trigger: RERUN_TRIGGERS },
    string_fields: ["review_pass_id", "plan_path", "invocation_source"],
    bool_fields: ["is_rereview", "blocked"],
    int_fields: ["open_question_count"],
    list_string_fields: ["reviewers", "artifact_paths"],
    count_object_fields: {
      findings_by_severity: FINDING_SEVERITIES,
      findings_by_lens: FINDING_LENSES,
    },
  },
  review_findings_applied: {
    string_fields: ["review_pass_id", "follow_up_plan_path", "plan_path"],
    int_fields: [
      "applied_count",
      "deferred_count",
      "continued_count",
      "open_question_count",
      "accepted_count",
      "rejected_count",
    ],
    bool_fields: ["rereview_needed"],
    list_string_fields: ["follow_up_plan_paths"],
  },
  rerun_decision: {
    required_fields: ["trigger", "decision"],
    enum_fields: {
      trigger: RERUN_TRIGGERS,
      decision: RERUN_DECISIONS,
    },
    string_fields: ["reason_category", "requested_lane", "plan_path"],
    bool_fields: ["user_selected"],
  },
  cycle_completed: {
    required_fields: ["plan_path"],
    enum_fields: {
      cycle_status: CYCLE_STATUSES,
      validation_status: VALIDATION_RESULTS,
    },
    string_fields: ["plan_path"],
    int_fields: ["review_count", "rereview_count", "bug_count"],
  },
  pr_created: {
    string_fields: ["pr_url", "target_branch", "commit_hash"],
    int_fields: ["pr_number"],
    bool_fields: ["draft"],
  },
  retro_written: {
    required_fields: ["retro_path"],
    string_fields: ["retro_path"],
    int_fields: ["cycle_count"],
  },
};

const EVENT_SPECS: Record<string, JsonRecord> = {
  session_started: { allowed_sources: new Set(["hook"]), default_status: "started" },
  session_completed: { allowed_sources: new Set(["hook"]), default_status: "completed" },
  prompt_submitted: { allowed_sources: new Set(["hook"]), default_status: "submitted" },
  turn_completed: { allowed_sources: new Set(["hook"]), default_status: "completed" },
  tool_requested: { allowed_sources: new Set(["hook"]), default_status: "requested" },
  tool_completed: { allowed_sources: new Set(["hook"]), default_status: "completed" },
  tool_failed: { allowed_sources: new Set(["hook"]), default_status: "failed" },
  subagent_started: { allowed_sources: new Set(["hook"]), default_status: "started" },
  subagent_completed: { allowed_sources: new Set(["hook"]), default_status: "completed" },
  error_occurred: { allowed_sources: new Set(["hook"]), default_status: "terminal" },
  compaction_started: { allowed_sources: new Set(["hook"]), default_status: "started" },
  skill_started: {
    allowed_sources: new Set(["skill"]),
    default_status: "started",
    metric_spec: SKILL_EVENT_SPECS.skill_started,
  },
  skill_completed: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.skill_completed,
  },
  skill_halted: {
    allowed_sources: new Set(["skill"]),
    default_status: "halted",
    metric_spec: SKILL_EVENT_SPECS.skill_halted,
  },
  phase_started: {
    allowed_sources: new Set(["skill"]),
    default_status: "started",
    metric_spec: SKILL_EVENT_SPECS.phase_started,
  },
  gate_presented: {
    allowed_sources: new Set(["skill"]),
    default_status: "presented",
    metric_spec: SKILL_EVENT_SPECS.gate_presented,
  },
  gate_decided: {
    allowed_sources: new Set(["skill"]),
    default_status: "decided",
    metric_spec: SKILL_EVENT_SPECS.gate_decided,
  },
  validation_attempt: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.validation_attempt,
  },
  review_pass_started: {
    allowed_sources: new Set(["skill"]),
    default_status: "started",
    metric_spec: SKILL_EVENT_SPECS.review_pass_started,
  },
  review_pass_completed: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.review_pass_completed,
  },
  review_findings_applied: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.review_findings_applied,
  },
  rerun_decision: {
    allowed_sources: new Set(["skill"]),
    default_status: "decided",
    metric_spec: SKILL_EVENT_SPECS.rerun_decision,
  },
  cycle_completed: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.cycle_completed,
  },
  pr_created: {
    allowed_sources: new Set(["skill"]),
    default_status: "created",
    metric_spec: SKILL_EVENT_SPECS.pr_created,
  },
  retro_written: {
    allowed_sources: new Set(["skill"]),
    default_status: "completed",
    metric_spec: SKILL_EVENT_SPECS.retro_written,
  },
  token_usage_recorded: { allowed_sources: new Set(["summary", "skill"]), default_status: "completed" },
};

const ALLOWED_SOURCES = new Set<string>();
for (const spec of Object.values(EVENT_SPECS)) {
  for (const source of spec.allowed_sources as Set<string>) {
    ALLOWED_SOURCES.add(source);
  }
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function stableStringify(value: any): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

export function digest16(raw: string): string {
  return createHash("sha256").update(raw, "utf8").digest("hex").slice(0, 16);
}

export function isPlainObject(value: any): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isMetricInt(value: any): boolean {
  return Number.isInteger(value) && typeof value === "number" && value >= 0;
}

function envScope(env?: Record<string, string | undefined>): Record<string, string | undefined> {
  return env ?? process.env;
}

export function defaultClientHome(client: Client, env?: Record<string, string | undefined>): string {
  if (!CLIENTS.has(client)) {
    throw new StatsValidationError("invalid_client", "client must be copilot or codex");
  }
  const configured = envScope(env)[CLIENT_HOME_ENV[client]];
  if (configured) {
    return expandHomePath(configured);
  }
  return join(homedir(), CLIENT_DEFAULT_DIR[client]);
}

export function inferClient(payload?: JsonRecord, env?: Record<string, string | undefined>): Client {
  const candidates = new Set<Client>();
  const scope = envScope(env);
  const configured = scope.DREAMERS_STATS_CLIENT ?? scope.DREAMERS_CLIENT;
  if (configured) {
    if (!CLIENTS.has(configured as Client)) {
      throw new StatsValidationError("invalid_client", "client must be copilot or codex");
    }
    return configured as Client;
  }

  for (const client of CLIENTS) {
    if (scope[CLIENT_HOME_ENV[client]]) {
      candidates.add(client);
    }
  }

  if (payload) {
    const direct = payload.client ?? payload.runtime;
    if (typeof direct === "string" && CLIENTS.has(direct as Client)) {
      candidates.add(direct as Client);
    }
    if (isPlainObject(payload.metrics)) {
      const metricsClient = payload.metrics.client ?? payload.metrics.runtime;
      if (typeof metricsClient === "string" && CLIENTS.has(metricsClient as Client)) {
        candidates.add(metricsClient as Client);
      }
    }
    const normalizedKeys = new Set(Object.keys(payload).map((key) => normalizeKey(key)));
    if (normalizedKeys.has("codexhome") || normalizedKeys.has("codex")) {
      candidates.add("codex");
    }
    if (normalizedKeys.has("copilothome") || normalizedKeys.has("copilot")) {
      candidates.add("copilot");
    }
  }

  if (candidates.size === 1) {
    return [...candidates][0] as Client;
  }
  if (!candidates.size) {
    throw new StatsValidationError(
      "ambiguous_client",
      "client could not be inferred; pass --client or set DREAMERS_STATS_CLIENT",
    );
  }
  throw new StatsValidationError("ambiguous_client", "client inference was ambiguous; pass --client explicitly");
}

export function resolveClientContext(options: RuntimeOptions = {}, payload?: JsonRecord): { client: Client; home: string } {
  const client = options.client ?? inferClient(payload, options.env);
  if (!CLIENTS.has(client)) {
    throw new StatsValidationError("invalid_client", "client must be copilot or codex");
  }
  return {
    client,
    home: options.home ? expandHomePath(options.home) : defaultClientHome(client, options.env),
  };
}

export function statsDir(options: RuntimeOptions = {}, payload?: JsonRecord): string {
  const context = resolveClientContext(options, payload);
  return join(context.home, "dreamers", "stats");
}

export function eventsPath(options: RuntimeOptions = {}, payload?: JsonRecord): string {
  return join(statsDir(options, payload), "events.jsonl");
}

export async function recordEvent(event: StatsEventInput, options: RuntimeOptions = {}): Promise<string> {
  const normalized = normalizeEvent(event);
  const destination = eventsPath(options, event);
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, `${stableStringify(normalized)}\n`, { encoding: "utf8", flag: "a" });
  return normalized.event_id;
}

export function normalizeEvent(event: StatsEventInput): NormalizedStatsEvent {
  if (!isPlainObject(event)) {
    throw new StatsValidationError("invalid_event", "event must be a JSON object");
  }
  const normalized = deepClone(event);
  validateEvent(normalized);
  normalizeTokenMetrics(normalized);
  fillBestEffortMetadata(normalized);
  return redactEvent(normalized);
}

export function validateEvent(event: StatsEventInput): void {
  const rawEvent = event as JsonRecord;
  for (const field of REQUIRED_FIELDS) {
    if (!(field in rawEvent) || rawEvent[field] === "" || rawEvent[field] === null || rawEvent[field] === undefined) {
      throw new StatsValidationError("missing_required_field", `missing required field: ${field}`);
    }
  }
  if (event.schema_version !== SCHEMA_VERSION) {
    throw new StatsValidationError("unsupported_schema_version", "unsupported schema_version");
  }
  for (const field of ["event_id", "timestamp", "event_type", "repo_path", "source"]) {
    requireString(event, field);
  }
  const eventId = event.event_id as string;
  const eventType = event.event_type as string;
  const source = event.source as string;
  const timestamp = event.timestamp as string;
  validateEventId(eventId);
  const eventSpec = EVENT_SPECS[eventType];
  if (!eventSpec) {
    throw new StatsValidationError("invalid_event_type", "event_type is not recognized");
  }
  if (!ALLOWED_SOURCES.has(source) || !(eventSpec.allowed_sources as Set<string>).has(source)) {
    throw new StatsValidationError("invalid_source", "source is not allowed for this event_type");
  }
  if (!isPlainObject(event.metrics)) {
    throw new StatsValidationError("invalid_metrics", "metrics must be a JSON object");
  }
  for (const field of OPTIONAL_FIELDS) {
    if (field in rawEvent && rawEvent[field] !== null && rawEvent[field] !== undefined && typeof rawEvent[field] !== "string") {
      throw new StatsValidationError("invalid_optional_field", `${field} must be a string when present`);
    }
  }
  parseIsoTimestamp(timestamp);
  if (eventSpec.metric_spec) {
    validateCheckpointMetrics(eventSpec.metric_spec, event.metrics);
  }
}

function requireString(event: JsonRecord, field: string): void {
  if (typeof event[field] !== "string" || !event[field].trim()) {
    throw new StatsValidationError("invalid_field_type", `${field} must be a non-empty string`);
  }
}

function validateEventId(value: string): void {
  if (value.length > 96 || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/.test(value)) {
    throw new StatsValidationError("invalid_event_id", "event_id must be a compact identifier");
  }
}

function validateCheckpointMetrics(spec: JsonRecord, metrics: JsonRecord): void {
  const requiredFields = new Set<string>(spec.required_fields ?? []);
  const enumFields = spec.enum_fields ?? {};
  const stringFields = new Set<string>(spec.string_fields ?? []);
  const intFields = new Set<string>(spec.int_fields ?? []);
  const boolFields = new Set<string>(spec.bool_fields ?? []);
  const listStringFields = new Set<string>(spec.list_string_fields ?? []);
  const countObjectFields = spec.count_object_fields ?? {};
  const allowedKeys = new Set<string>([
    ...requiredFields,
    ...Object.keys(enumFields),
    ...stringFields,
    ...intFields,
    ...boolFields,
    ...listStringFields,
    ...Object.keys(countObjectFields),
  ]);

  for (const field of requiredFields) {
    if (!(field in metrics)) {
      throw new StatsValidationError("missing_metric", `missing required metric: ${field}`);
    }
  }
  for (const key of Object.keys(metrics)) {
    if (!allowedKeys.has(key)) {
      throw new StatsValidationError("invalid_metric_key", `metric is not allowed: ${key}`);
    }
  }
  for (const [field, values] of Object.entries(enumFields)) {
    if (field in metrics && !(values as Set<string>).has(metrics[field])) {
      throw new StatsValidationError("invalid_metric_enum", `invalid value for metric: ${field}`);
    }
  }
  for (const field of stringFields) {
    if (field in metrics && metrics[field] !== null && (typeof metrics[field] !== "string" || !metrics[field].trim())) {
      throw new StatsValidationError("invalid_metric_type", `${field} must be a non-empty string`);
    }
  }
  for (const field of intFields) {
    if (field in metrics) {
      validateMetricInt(metrics[field], field);
    }
  }
  for (const field of boolFields) {
    if (field in metrics && typeof metrics[field] !== "boolean") {
      throw new StatsValidationError("invalid_metric_type", `${field} must be a boolean`);
    }
  }
  for (const field of listStringFields) {
    if (field in metrics) {
      validateMetricStringList(metrics[field], field);
    }
  }
  for (const [field, allowed] of Object.entries(countObjectFields)) {
    if (field in metrics) {
      validateMetricCountObject(metrics[field], field, allowed as Set<string>);
    }
  }
}

export function validateMetricInt(value: any, field: string): void {
  if (typeof value === "boolean" || !Number.isInteger(value)) {
    throw new StatsValidationError("invalid_metric_type", `${field} must be an integer`);
  }
}

export function validateMetricNumber(value: any, field: string): void {
  if (typeof value === "boolean" || typeof value !== "number" || Number.isNaN(value)) {
    throw new StatsValidationError("invalid_metric_type", `${field} must be numeric`);
  }
}

function validateMetricStringList(value: any, field: string): void {
  if (!Array.isArray(value)) {
    throw new StatsValidationError("invalid_metric_type", `${field} must be a list of strings`);
  }
  for (const item of value) {
    if (typeof item !== "string" || !item.trim()) {
      throw new StatsValidationError("invalid_metric_type", `${field} must be a list of strings`);
    }
  }
}

function validateMetricCountObject(value: any, field: string, allowedKeys?: Set<string>): void {
  if (!isPlainObject(value)) {
    throw new StatsValidationError("invalid_metric_type", `${field} must be an object`);
  }
  for (const [key, item] of Object.entries(value)) {
    if (allowedKeys?.size && !allowedKeys.has(key)) {
      throw new StatsValidationError("invalid_metric_enum", `invalid metric category: ${key}`);
    }
    validateMetricInt(item, `${field}.${key}`);
  }
}

export function defaultStatusForEvent(eventType: string): string {
  const eventSpec = EVENT_SPECS[eventType];
  if (!eventSpec) {
    throw new StatsValidationError("invalid_event_type", "event_type is not recognized");
  }
  return eventSpec.default_status;
}

export function parseIsoTimestamp(value: string): Date {
  if (typeof value !== "string") {
    throw new StatsValidationError("invalid_timestamp", "timestamp must be ISO-8601");
  }
  const text = value.trim();
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) {
    throw new StatsValidationError("invalid_timestamp", "timestamp must include a timezone");
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    throw new StatsValidationError("invalid_timestamp", "timestamp must be ISO-8601");
  }
  return parsed;
}

export function isoNoMillis(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function utcNowIso(): string {
  return isoNoMillis(new Date());
}

export function normalizeTokenMetrics(event: JsonRecord): void {
  if (event.event_type !== "token_usage_recorded") {
    return;
  }
  const metrics = event.metrics;
  const sourceQuality = metrics.token_source;
  if (!TOKEN_SOURCES.has(sourceQuality)) {
    throw new StatsValidationError("invalid_token_source", "token_source must be exact, estimated, or unavailable");
  }
  if (sourceQuality === "unavailable") {
    for (const field of TOKEN_FIELDS) {
      metrics[field] = null;
    }
    return;
  }
  for (const field of TOKEN_FIELDS) {
    if (!(field in metrics) || metrics[field] === null || metrics[field] === undefined) {
      continue;
    }
    if (field === "ai_credits") {
      validateMetricNumber(metrics[field], field);
    } else {
      validateMetricInt(metrics[field], field);
    }
  }
  if (metrics.model !== null && metrics.model !== undefined && (typeof metrics.model !== "string" || !metrics.model.trim())) {
    throw new StatsValidationError("invalid_metric_type", "model must be a non-empty string");
  }
}

function fillBestEffortMetadata(event: JsonRecord): void {
  if (!("repo_name" in event)) {
    event.repo_name = deriveRepoName(event.repo_path);
  }
  for (const field of OPTIONAL_FIELDS) {
    if (!(field in event)) {
      event[field] = null;
    }
  }
}

export function deriveRepoName(repoPath: string): string | null {
  const name = basename(repoPath);
  return name || null;
}

export function redactEvent(value: any, key?: string): any {
  if (Array.isArray(value)) {
    return value.map((item) => redactEvent(item, key));
  }
  if (isPlainObject(value)) {
    const output: JsonRecord = {};
    for (const [itemKey, itemValue] of Object.entries(value)) {
      output[itemKey] = redactEvent(itemValue, itemKey);
    }
    return output;
  }
  if (isProhibitedContentKey(key)) {
    return "[REDACTED]";
  }
  if (typeof value === "string" && (isSensitiveKey(key) || containsSensitiveValue(value))) {
    return "[REDACTED]";
  }
  return value;
}

export function normalizeKey(key?: string | null): string | null {
  if (key === undefined || key === null) {
    return null;
  }
  return key.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || null;
}

function keyTokens(normalizedKey: string): string[] {
  return normalizedKey.split("_").filter(Boolean);
}

function isSensitiveKey(key?: string): boolean {
  const normalized = normalizeKey(key);
  if (!normalized || SENSITIVE_KEY_EXCEPTIONS.has(normalized)) {
    return false;
  }
  return keyTokens(normalized).some((token) => SENSITIVE_KEY_NAMES.has(token));
}

function isProhibitedContentKey(key?: string): boolean {
  const normalized = normalizeKey(key);
  if (!normalized || SAFE_CONTENT_KEYS.has(normalized)) {
    return false;
  }
  const tokens = keyTokens(normalized);
  return PROHIBITED_CONTENT_KEYS.has(normalized) || tokens.some((token) => PROHIBITED_CONTENT_KEYS.has(token));
}

function containsSensitiveValue(value: string): boolean {
  return SENSITIVE_VALUE_PATTERNS.some((pattern) => pattern.test(value));
}
