export type Client = "copilot" | "codex";
export type ReportCommand = "runs" | "reviews" | "validation" | "gates" | "tokens" | "summarize";
export type StatsSource = "hook" | "skill" | "summary";
export type TokenSourceQuality = "exact" | "estimated" | "unavailable";
export type TokenAttributionScope = "turn" | "session";
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface RuntimeOptions {
  client?: Client;
  home?: string;
  env?: Record<string, string | undefined>;
}

export interface ReportOptions extends RuntimeOptions {
  repo?: "current" | "all";
  skill?: string;
  since?: string;
  until?: string;
  cwd?: string;
}

export type UnknownRecord = Record<string, unknown>;

export type SkillMode = "task-description" | "plan-path" | "manifest";
export type GateType =
  | "plan-approval"
  | "implementation-start"
  | "major-refactor"
  | "review-rerun"
  | "user-testing"
  | "pre-pr"
  | "pr-selection"
  | "push-decision";
export type GateDecision =
  | "approved"
  | "approved_start_implementation"
  | "approved_start_incremental"
  | "approved_start_atomic"
  | "revise"
  | "revise_plan"
  | "halt"
  | "other"
  | "apply_now"
  | "defer"
  | "defer_follow_up_plan"
  | "continue_lite_scope"
  | "run_vigil"
  | "run_full_triad"
  | "run_selected_lane"
  | "skip"
  | "skip_reviewer_rerun"
  | "bug_found"
  | "push_to_pr"
  | "hold";
export type ReviewLane = "full" | "standard" | "sentinel" | "probe" | "hone" | "vigil";
export type ValidationCommandKind = "typecheck" | "test" | "build" | "lint" | "manual";
export type ValidationResult = "pass" | "fail" | "skipped";
export type ValidationFailureCategory = "type-error" | "test-failure" | "timeout" | "missing-command" | "unknown";
export type RerunTrigger =
  | "post_triad_fixes"
  | "user_testing_bug"
  | "major_change_gate"
  | "user_selected_full"
  | "user_selected_lane"
  | "validation_risk"
  | "pr_feedback"
  | "optional_maintenance_review"
  | "skipped_small_fix"
  | "skipped_user_approved";
export type InvocationSource = "standalone" | "dreamers-full" | "dreamers-lite" | "dreamers-pr-resolve";
export type HaltReasonCategory =
  | "blocked_reviewer"
  | "user_halt"
  | "validation_failure"
  | "missing_pr"
  | "missing_artifact"
  | "graphql_failure"
  | "push_held"
  | "other_safe";
export type CycleStatus = "completed" | "halted" | "blocked";
export type DocsStatus = "updated" | "skipped" | "not-needed";
export type PushStatus = "pushed" | "held" | "not-requested";
export type FinalStatus = "completed" | "resolved" | "approved";
export type FindingSeverity = "critical" | "high" | "medium" | "low";
export type FindingLens = "correctness" | "security" | "maintainability" | "test-coverage" | "simplicity";

export interface SessionStartedMetrics {
  session_source?: string;
  initial_input_present?: boolean;
}

export interface SessionCompletedMetrics {
  reason?: string;
}

export interface PromptSubmittedMetrics {
  prompt_count: number;
  input_char_count: number;
  starts_with_slash: boolean;
}

export interface ToolCompletedMetrics {
  tool_name?: string;
  result_type?: string;
}

export interface ToolFailedMetrics {
  tool_name?: string;
  error_present?: boolean;
}

export interface TurnCompletedMetrics {
  stop_reason?: string;
}

export interface SubagentStartedMetrics {
  agent_name?: string;
  agent_display_name?: string;
}

export interface SubagentCompletedMetrics extends SubagentStartedMetrics {
  stop_reason?: string;
}

export interface ErrorOccurredMetrics {
  error_name: string;
  error_context?: string;
  recoverable: boolean;
}

export interface CompactionStartedMetrics {
  trigger?: string;
  instructions_present: boolean;
}

export interface SkillStartedMetrics {
  mode?: SkillMode;
  lane?: ReviewLane;
  invocation_source?: InvocationSource;
  plan_count?: number;
  pr_number?: number;
  unresolved_thread_count?: number;
  strategy?: string;
  plan_path?: string;
  pr_url?: string;
}

export interface SkillCompletedMetrics {
  docs_status?: DocsStatus;
  push_status?: PushStatus;
  final_status?: FinalStatus;
  accepted_count?: number;
  rejected_count?: number;
  resolved_thread_count?: number;
  review_count?: number;
  rereview_count?: number;
  plan_count?: number;
  commit_hash?: string;
  plan_path?: string;
  pr_url?: string;
  docs_updated?: boolean;
}

export interface SkillHaltedMetrics {
  halt_reason_category: HaltReasonCategory;
  gate_type?: GateType;
  lane?: ReviewLane;
  open_question_count?: number;
  unresolved_thread_count?: number;
  plan_path?: string;
  reviewer?: string;
  artifact_path?: string;
  user_selected?: boolean;
}

export interface PhaseStartedMetrics {
  phase_name: string;
  plan_path?: string;
  step_name?: string;
  strategy?: string;
  phase_index?: number;
  plan_position?: number;
}

export interface GatePresentedMetrics {
  gate_type: GateType;
  plan_path?: string;
  reviewer?: string;
  severity?: FindingSeverity;
  lens?: FindingLens;
  location?: string;
  breadth_estimate?: string;
  trigger_category?: string;
  requested_lane?: string;
  option_categories?: string[];
}

export interface GateDecidedMetrics {
  gate_type: GateType;
  decision: GateDecision;
  plan_path?: string;
  follow_up_plan_path?: string;
  trigger_category?: string;
  requested_lane?: string;
  bug_count?: number;
  follow_up_plan_count?: number;
  user_selected?: boolean;
}

export interface ValidationAttemptMetrics {
  command_kind: ValidationCommandKind;
  command_label: string;
  attempt_number: number;
  result: ValidationResult;
  failure_category?: ValidationFailureCategory;
  scope?: string;
  plan_path?: string;
  duration_ms?: number;
}

export interface ReviewPassMetrics {
  lane: ReviewLane;
  reviewers: string[];
  trigger?: RerunTrigger;
  review_pass_id?: string;
  plan_path?: string;
  invocation_source?: InvocationSource;
  is_rereview?: boolean;
}

export interface ReviewPassCompletedMetrics extends ReviewPassMetrics {
  artifact_paths: string[];
  blocked: boolean;
  open_question_count: number;
  findings_by_severity?: Partial<Record<FindingSeverity, number>>;
  findings_by_lens?: Partial<Record<FindingLens, number>>;
}

export interface ReviewFindingsAppliedMetrics {
  review_pass_id?: string;
  follow_up_plan_path?: string;
  plan_path?: string;
  applied_count?: number;
  deferred_count?: number;
  continued_count?: number;
  open_question_count?: number;
  accepted_count?: number;
  rejected_count?: number;
  rereview_needed?: boolean;
  follow_up_plan_paths?: string[];
}

export interface RerunDecisionMetrics {
  trigger: RerunTrigger;
  decision: "run_vigil" | "run_full_triad" | "run_selected_lane" | "skip" | "not_needed";
  reason_category?: string;
  requested_lane?: string;
  plan_path?: string;
  user_selected?: boolean;
}

export interface CycleCompletedMetrics {
  plan_path: string;
  cycle_status?: CycleStatus;
  validation_status?: ValidationResult;
  review_count?: number;
  rereview_count?: number;
  bug_count?: number;
}

export interface PrCreatedMetrics {
  pr_url?: string;
  target_branch?: string;
  commit_hash?: string;
  pr_number?: number;
  draft?: boolean;
}

export interface RetroWrittenMetrics {
  retro_path: string;
  cycle_count?: number;
}

export interface StatsEventMetricMap {
  session_started: SessionStartedMetrics;
  session_completed: SessionCompletedMetrics;
  prompt_submitted: PromptSubmittedMetrics;
  turn_completed: TurnCompletedMetrics;
  tool_requested: ToolCompletedMetrics;
  tool_completed: ToolCompletedMetrics;
  tool_failed: ToolFailedMetrics;
  subagent_started: SubagentStartedMetrics;
  subagent_completed: SubagentCompletedMetrics;
  error_occurred: ErrorOccurredMetrics;
  compaction_started: CompactionStartedMetrics;
  skill_started: SkillStartedMetrics;
  skill_completed: SkillCompletedMetrics;
  skill_halted: SkillHaltedMetrics;
  phase_started: PhaseStartedMetrics;
  gate_presented: GatePresentedMetrics;
  gate_decided: GateDecidedMetrics;
  validation_attempt: ValidationAttemptMetrics;
  review_pass_started: ReviewPassMetrics;
  review_pass_completed: ReviewPassCompletedMetrics;
  review_findings_applied: ReviewFindingsAppliedMetrics;
  rerun_decision: RerunDecisionMetrics;
  cycle_completed: CycleCompletedMetrics;
  pr_created: PrCreatedMetrics;
  retro_written: RetroWrittenMetrics;
  token_usage_recorded: TokenMetrics;
}

export type StatsEventType = keyof StatsEventMetricMap;
export type StatsMetrics = StatsEventMetricMap[StatsEventType];
export type StatsEventSourceFor<EventType extends StatsEventType> =
  EventType extends
    | "session_started"
    | "session_completed"
    | "prompt_submitted"
    | "turn_completed"
    | "tool_requested"
    | "tool_completed"
    | "tool_failed"
    | "subagent_started"
    | "subagent_completed"
    | "error_occurred"
    | "compaction_started"
    ? "hook"
    : EventType extends "token_usage_recorded"
      ? "summary" | "skill"
      : "skill";

export interface BaseStatsEvent<EventType extends StatsEventType, Metrics extends StatsMetrics> {
  schema_version?: number;
  event_id?: string;
  timestamp?: string;
  event_type?: EventType;
  repo_path?: string;
  source?: StatsEventSourceFor<EventType>;
  session_id?: string | null;
  run_id?: string | null;
  repo_name?: string | null;
  branch?: string | null;
  skill?: string | null;
  status?: string | null;
  metrics?: Metrics;
}

export type StatsEventInput = {
  [EventType in StatsEventType]: BaseStatsEvent<EventType, StatsEventMetricMap[EventType]>;
}[StatsEventType];

export interface BaseNormalizedStatsEvent<EventType extends StatsEventType, Metrics extends StatsMetrics> {
  schema_version: number;
  event_id: string;
  timestamp: string;
  event_type: EventType;
  repo_path: string;
  source: StatsEventSourceFor<EventType>;
  session_id: string | null;
  run_id: string | null;
  repo_name: string | null;
  branch: string | null;
  skill: string | null;
  status: string | null;
  metrics: Metrics;
}

export type NormalizedStatsEvent = {
  [EventType in StatsEventType]: BaseNormalizedStatsEvent<EventType, StatsEventMetricMap[EventType]>;
}[StatsEventType];

export interface HookToolResult {
  resultType?: string;
  result_type?: string;
  status?: string;
  [key: string]: unknown;
}

export interface HookErrorPayload {
  name?: string;
  [key: string]: unknown;
}

export interface HookPayload {
  cwd?: string;
  repoPath?: string;
  repo_path?: string;
  timestamp?: string | number;
  sessionId?: string;
  session_id?: string;
  turn_id?: string;
  turnId?: string;
  source?: string;
  initialPrompt?: string;
  prompt?: string;
  toolName?: string;
  tool_name?: string;
  toolResult?: HookToolResult;
  tool_result?: HookToolResult;
  toolResponse?: HookToolResult;
  tool_response?: HookToolResult;
  error?: HookErrorPayload | string;
  errorContext?: string;
  error_context?: string;
  recoverable?: boolean;
  stopReason?: string;
  stop_reason?: string;
  agentName?: string;
  agent_name?: string;
  agentDisplayName?: string;
  agent_display_name?: string;
  agent_type?: string;
  trigger?: string;
  customInstructions?: string;
  custom_instructions?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface TokenMetrics {
  token_source: TokenSourceQuality;
  attribution_scope: TokenAttributionScope;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  ai_credits?: number | null;
  model?: string;
}

export interface ReportFilters {
  repo: "current" | "all";
  skill: string | null;
  since: string | null;
  until: string | null;
  current_repo: string | null;
}

export interface ReportRange {
  first_timestamp: string | null;
  last_timestamp: string | null;
}

export interface CountMap {
  [key: string]: number;
}

export interface ValidationKindSummary {
  attempt_count: number;
  failure_count: number;
  retry_count: number;
  final_pass_count: number;
  final_fail_count: number;
}

export interface ValidationReportPayload extends BaseReportPayload {
  report_type: "validation";
  attempt_count: number;
  command_kinds: Record<string, ValidationKindSummary>;
  range: ReportRange;
}

export interface GateReportPayload extends BaseReportPayload {
  report_type: "gates";
  gate_type_counts: CountMap;
  decision_counts: Record<string, CountMap>;
  range: ReportRange;
}

export interface ReviewArtifactSummary {
  parsed_count: number;
  missing_count: number;
  mismatch_count: number;
  artifact_only_count: number;
  missing_paths: string[];
  mismatches: JsonObject[];
}

export interface ReviewReportPayload extends BaseReportPayload {
  report_type: "reviews";
  review_count: number;
  initial_review_count: number;
  rereview_count: number;
  blocked_count: number;
  open_question_count: number;
  lane_counts: CountMap;
  reviewer_counts: CountMap;
  findings_by_severity: CountMap;
  findings_by_lens: CountMap;
  rereview_trigger_counts: CountMap;
  artifact_summary: ReviewArtifactSummary;
  range: ReportRange;
}

export interface TokenTotals {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  ai_credits: number | null;
}

export interface TokenSourceSummary {
  source_quality: TokenSourceQuality;
  row_count: number;
  session_count: number;
  totals: TokenTotals;
  sessions: JsonObject[];
  skills: Record<string, TokenTotals>;
  models: Record<string, TokenTotals>;
}

export interface TokensReportPayload extends BaseReportPayload {
  report_type: "tokens";
  exact: TokenSourceSummary;
  estimated: TokenSourceSummary;
  unavailable: TokenSourceSummary;
  range: ReportRange;
}

export interface RunGroup {
  skill: string | null;
  status: string;
  run_count: number;
  total_duration_seconds: number;
  average_duration_seconds: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
}

export interface RunReportItem {
  run_id: string;
  repo_path: string;
  skill: string | null;
  status: string;
  data_quality: "confirmed_closed";
  duration_seconds: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  start_timestamp: string | null;
  end_timestamp: string | null;
  validation: ValidationReportPayload;
  gates: GateReportPayload;
  reviews: ReviewReportPayload;
  tokens: TokensReportPayload;
}

export interface IncompleteRunItem {
  run_id: string;
  repo_path: string;
  skill: string | null;
  status: "excluded";
  reason: string;
  data_quality: "incomplete_or_ambiguous";
  event_count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  start_timestamp: string | null;
  end_timestamp: string | null;
}

export interface RunsReportPayload extends BaseReportPayload {
  report_type: "runs";
  run_count: number;
  incomplete_count: number;
  range: ReportRange;
  groups: RunGroup[];
  items: RunReportItem[];
  incomplete_items: IncompleteRunItem[];
}

export interface SummaryReportPayload extends BaseReportPayload {
  report_type: "summarize";
  runs: RunsReportPayload;
  reviews: ReviewReportPayload;
  validation: ValidationReportPayload;
  gates: GateReportPayload;
  tokens: TokensReportPayload;
}

export interface ReportPayloadByCommand {
  runs: RunsReportPayload;
  reviews: ReviewReportPayload;
  validation: ValidationReportPayload;
  gates: GateReportPayload;
  tokens: TokensReportPayload;
  summarize: SummaryReportPayload;
}

export type ReportPayloadFor<Command extends ReportCommand> = ReportPayloadByCommand[Command];
export type ReportPayload = ReportPayloadByCommand[ReportCommand];

export interface BaseReportPayload {
  report_type: ReportCommand;
  warning_count: number;
  filters: ReportFilters;
}
