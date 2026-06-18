import { existsSync, statSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";

import {
  SCHEMA_VERSION,
  defaultStatusForEvent,
  digest16,
  eventsPath,
  isPlainObject,
  isoNoMillis,
  parseIsoTimestamp,
  statsDir,
} from "./events.js";
import { StatsValidationError } from "./errors.js";
import { loadClientSessionActiveTime, loadClientSessionTokenMetrics } from "./tokens.js";
import type { ReportCommand, ReportOptions, ReportPayloadFor, RuntimeOptions } from "./types.js";
import { compareDesc, isDirectory, isFile, maxDate, minDate, requireReadFile, safeReaddir, sortObject } from "./utils.js";

type JsonRecord = Record<string, any>;

const RELATIVE_RANGE_PATTERN = /^(?<amount>\d+)(?<unit>[dhm])$/;
const FINDING_LINE_PATTERN = /^- \[(?<severity>critical|high|medium|low)\] \[(?<lens>correctness|security|maintainability|test-coverage|simplicity)\] /;
const FINDING_SEVERITY_ORDER = ["critical", "high", "medium", "low"];
const FINDING_LENS_ORDER = ["correctness", "security", "maintainability", "test-coverage", "simplicity"];
const REVIEW_LANES = new Set(["full", "standard", "sentinel", "probe", "hone", "vigil"]);
const TERMINAL_SKILL_EVENTS = new Set(["skill_completed", "skill_halted"]);
const ARTIFACT_SECTION_HEADINGS = new Set(["findings", "plan alignment", "ac coverage", "full refactor findings", "observations", "open questions"]);
const TOKEN_FIELDS = ["input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens", "ai_credits"];

export async function doctor(options: RuntimeOptions = {}): Promise<JsonRecord> {
  const directory = statsDir(options);
  const eventLog = eventsPath(options);
  const report: JsonRecord = {
    writable: false,
    stats_dir: directory,
    events_file: eventLog,
    event_count: 0,
    malformed_line_count: 0,
    error: null,
  };
  try {
    await mkdir(directory, { recursive: true });
    report.writable = true;
    if (isFile(eventLog)) {
      const text = await readFile(eventLog, "utf8");
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim()) {
          continue;
        }
        try {
          const payload = JSON.parse(line);
          if (isPlainObject(payload)) {
            report.event_count += 1;
          } else {
            report.malformed_line_count += 1;
          }
        } catch {
          report.malformed_line_count += 1;
        }
      }
    }
  } catch (error) {
    report.error = String(error instanceof Error ? error.message : error);
  }
  return report;
}

async function loadReportEvents(options: RuntimeOptions = {}): Promise<{ events: JsonRecord[]; warningCount: number }> {
  const eventLog = eventsPath(options);
  if (!isFile(eventLog)) {
    return { events: [], warningCount: 0 };
  }
  const events: JsonRecord[] = [];
  let warningCount = 0;
  const text = await readFile(eventLog, "utf8");
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) {
      continue;
    }
    let payload: any;
    try {
      payload = JSON.parse(line);
    } catch {
      warningCount += 1;
      continue;
    }
    const normalized = normalizeReportEvent(payload);
    if (!normalized) {
      warningCount += 1;
      continue;
    }
    events.push(normalized);
  }
  return { events, warningCount };
}

async function resolveReportTokenEvents(events: JsonRecord[], options: RuntimeOptions): Promise<JsonRecord[]> {
  if (!options.client || !options.home) {
    return events;
  }
  const resolvedEvents: JsonRecord[] = [];
  const tokenCache = new Map<string, JsonRecord | null>();
  for (const event of events) {
    if (!isUnavailableTokenEvent(event)) {
      resolvedEvents.push(event);
      continue;
    }
    const sessionId = event.session_id;
    const timestamp = event.timestamp;
    if (typeof sessionId !== "string" || typeof timestamp !== "string") {
      resolvedEvents.push(event);
      continue;
    }
    const cacheKey = `${sessionId}\u0000${timestamp}`;
    if (!tokenCache.has(cacheKey)) {
      tokenCache.set(cacheKey, await loadClientSessionTokenMetrics(options.client, options.home, sessionId, timestamp));
    }
    const exactMetrics = tokenCache.get(cacheKey);
    if (!exactMetrics) {
      resolvedEvents.push(event);
      continue;
    }
    resolvedEvents.push({ ...event, metrics: { ...exactMetrics } });
  }
  return resolvedEvents;
}

function isUnavailableTokenEvent(event: JsonRecord): boolean {
  return event.event_type === "token_usage_recorded" && isPlainObject(event.metrics) && event.metrics.token_source === "unavailable";
}

function normalizeReportEvent(payload: any): JsonRecord | null {
  if (!isPlainObject(payload) || !isPlainObject(payload.metrics)) {
    return null;
  }
  if (typeof payload.repo_path !== "string" || !payload.repo_path.trim()) {
    return null;
  }
  if (typeof payload.event_type !== "string" || !payload.event_type.trim()) {
    return null;
  }
  if (typeof payload.timestamp !== "string" || !payload.timestamp.trim()) {
    return null;
  }
  let parsedTimestamp: Date;
  try {
    parsedTimestamp = parseIsoTimestamp(payload.timestamp);
  } catch {
    return null;
  }
  return {
    ...payload,
    metrics: { ...payload.metrics },
    _parsed_timestamp: parsedTimestamp,
  };
}

function buildReportFilters(options: ReportOptions): JsonRecord {
  const repo = options.repo ?? "current";
  if (repo !== "current" && repo !== "all") {
    throw new StatsValidationError("invalid_report_filter", "repo must be current or all");
  }
  const parsedSince = options.since ? parseReportBoundary(options.since, false) : null;
  const parsedUntil = options.until ? parseReportBoundary(options.until, true) : null;
  if (parsedSince && parsedUntil && parsedSince > parsedUntil) {
    throw new StatsValidationError("invalid_date_range", "--since must be earlier than --until");
  }
  const currentRepo = repo === "current" ? detectRepoRoot(options.cwd ?? process.cwd()) : null;
  return {
    repo,
    skill: options.skill ?? null,
    since: datetimeToIso(parsedSince),
    until: datetimeToIso(parsedUntil),
    current_repo: currentRepo,
    _since: parsedSince,
    _until: parsedUntil,
    _current_repo: currentRepo,
    _client: options.client ?? null,
  };
}

function parseReportBoundary(value: string, isEnd: boolean): Date {
  const match = RELATIVE_RANGE_PATTERN.exec(value);
  if (match?.groups) {
    const amount = Number(match.groups.amount);
    const unit = match.groups.unit;
    const millis = unit === "d" ? amount * 86400000 : unit === "h" ? amount * 3600000 : amount * 60000;
    return new Date(Date.now() - millis);
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const parsed = new Date(`${value}T00:00:00Z`);
    if (isEnd) {
      return new Date(parsed.getTime() + 86400000 - 1);
    }
    return parsed;
  }
  return parseIsoTimestamp(value);
}

function detectRepoRoot(start: string): string {
  let current = resolve(start);
  if (isFile(current)) {
    current = dirname(current);
  }
  while (true) {
    if (existsSync(join(current, ".git"))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) {
      return current;
    }
    current = parent;
  }
}

function filterReportEvents(events: JsonRecord[], filters: JsonRecord): JsonRecord[] {
  return events.filter((event) => {
    if (filters.repo === "current" && filters._current_repo && !eventMatchesRepo(event, filters._current_repo)) {
      return false;
    }
    if (filters.skill !== null && event.skill !== filters.skill) {
      return false;
    }
    const timestamp = event._parsed_timestamp as Date;
    if (filters._since && timestamp < filters._since) {
      return false;
    }
    if (filters._until && timestamp > filters._until) {
      return false;
    }
    return true;
  });
}

function eventMatchesRepo(event: JsonRecord, currentRepo: string): boolean {
  const eventPath = resolve(event.repo_path);
  const repoPath = resolve(currentRepo);
  return eventPath === repoPath || isPathInside(eventPath, repoPath) || isPathInside(repoPath, eventPath);
}

function isPathInside(candidate: string, parent: string): boolean {
  const normalizedParent = parent.endsWith(sep) ? parent : `${parent}${sep}`;
  return candidate.startsWith(normalizedParent);
}

function datetimeToIso(value: Date | null): string | null {
  return value ? isoNoMillis(value) : null;
}

function eventRange(events: JsonRecord[]): JsonRecord {
  if (!events.length) {
    return { first_timestamp: null, last_timestamp: null };
  }
  const timestamps = events.map((event) => event._parsed_timestamp as Date);
  return {
    first_timestamp: datetimeToIso(new Date(Math.min(...timestamps.map((item) => item.getTime())))),
    last_timestamp: datetimeToIso(new Date(Math.max(...timestamps.map((item) => item.getTime())))),
  };
}

function emptyCountDict(keys: string[]): JsonRecord {
  return Object.fromEntries(keys.map((key) => [key, 0]));
}

function mergeCountDicts(target: JsonRecord, source: JsonRecord | undefined, keys: string[]): void {
  for (const key of keys) {
    target[key] += Number(source?.[key] ?? 0);
  }
}

function runKeyForEvent(event: JsonRecord): string | null {
  if (typeof event.run_id !== "string" || !event.run_id) {
    return null;
  }
  if (typeof event.skill !== "string" || !event.skill) {
    return null;
  }
  return `${event.run_id}\u0000${event.repo_path}\u0000${event.skill}`;
}

function splitRunKey(key: string): [string, string, string] {
  return key.split("\u0000") as [string, string, string];
}

function summarizeValidationEvents(validationEvents: JsonRecord[]): JsonRecord {
  const commandKinds: JsonRecord = {};
  const finalAttempts = new Map<string, JsonRecord>();
  for (const event of validationEvents) {
    const metrics = event.metrics;
    const kind = metrics.command_kind;
    const label = metrics.command_label;
    if (typeof kind !== "string" || typeof label !== "string") {
      continue;
    }
    commandKinds[kind] ??= {
      attempt_count: 0,
      failure_count: 0,
      retry_count: 0,
      final_pass_count: 0,
      final_fail_count: 0,
    };
    commandKinds[kind].attempt_count += 1;
    if (metrics.result === "fail") {
      commandKinds[kind].failure_count += 1;
    }
    if (Number(metrics.attempt_number ?? 0) > 1) {
      commandKinds[kind].retry_count += 1;
    }
    const key = [
      event.run_id,
      event.repo_path,
      kind,
      label,
      metrics.scope,
      metrics.plan_path,
    ].join("\u0000");
    const previous = finalAttempts.get(key);
    if (!previous || shouldReplaceValidationAttempt(previous, event)) {
      finalAttempts.set(key, event);
    }
  }
  for (const event of finalAttempts.values()) {
    const kind = event.metrics.command_kind;
    if (event.metrics.result === "pass") {
      commandKinds[kind].final_pass_count += 1;
    } else if (event.metrics.result === "fail") {
      commandKinds[kind].final_fail_count += 1;
    }
  }
  return {
    attempt_count: validationEvents.length,
    command_kinds: sortObject(commandKinds),
  };
}

function shouldReplaceValidationAttempt(previous: JsonRecord, candidate: JsonRecord): boolean {
  const previousAttempt = Number(previous.metrics.attempt_number ?? 0);
  const candidateAttempt = Number(candidate.metrics.attempt_number ?? 0);
  if (candidateAttempt !== previousAttempt) {
    return candidateAttempt > previousAttempt;
  }
  return (candidate._parsed_timestamp as Date) > (previous._parsed_timestamp as Date);
}

function summarizeGateEvents(gateEvents: JsonRecord[]): JsonRecord {
  const gateTypeCounts: JsonRecord = {};
  const decisionCounts: JsonRecord = {};
  for (const event of gateEvents) {
    const gateType = event.metrics.gate_type;
    const decision = event.metrics.decision;
    gateTypeCounts[gateType] = (gateTypeCounts[gateType] ?? 0) + 1;
    decisionCounts[gateType] ??= {};
    decisionCounts[gateType][decision] = (decisionCounts[gateType][decision] ?? 0) + 1;
  }
  return {
    gate_type_counts: sortObject(gateTypeCounts),
    decision_counts: sortObject(Object.fromEntries(Object.entries(decisionCounts).map(([key, value]) => [key, sortObject(value as JsonRecord)]))),
  };
}

function summarizeReviewEvents(reviewEvents: JsonRecord[]): JsonRecord {
  const laneCounts: JsonRecord = {};
  const reviewerCounts: JsonRecord = {};
  const triggerCounts: JsonRecord = {};
  const findingsBySeverity = emptyCountDict(FINDING_SEVERITY_ORDER);
  const findingsByLens = emptyCountDict(FINDING_LENS_ORDER);
  const parsedArtifactPaths = new Set<string>();
  const missingArtifactPaths = new Set<string>();
  const artifactCache = new Map<string, JsonRecord>();
  const mismatches: JsonRecord[] = [];
  let initialReviewCount = 0;
  let rereviewCount = 0;
  let blockedCount = 0;
  let openQuestionCount = 0;

  for (const event of reviewEvents) {
    const metrics = event.metrics;
    if (typeof metrics.lane === "string" && metrics.lane) {
      laneCounts[metrics.lane] = (laneCounts[metrics.lane] ?? 0) + 1;
    }
    if (Array.isArray(metrics.reviewers)) {
      for (const reviewer of metrics.reviewers) {
        if (typeof reviewer === "string") {
          reviewerCounts[reviewer] = (reviewerCounts[reviewer] ?? 0) + 1;
        }
      }
    }
    if (metrics.is_rereview) {
      rereviewCount += 1;
      if (typeof metrics.trigger === "string" && metrics.trigger) {
        triggerCounts[metrics.trigger] = (triggerCounts[metrics.trigger] ?? 0) + 1;
      }
    } else {
      initialReviewCount += 1;
    }

    const eventArtifacts = resolveReviewArtifacts(event);
    let eventMissing = false;
    const artifactAggregate: JsonRecord = {
      blocked: false,
      open_question_count: 0,
      findings_by_severity: emptyCountDict(FINDING_SEVERITY_ORDER),
      findings_by_lens: emptyCountDict(FINDING_LENS_ORDER),
    };
    for (const artifactPath of eventArtifacts) {
      let parsed = artifactCache.get(artifactPath);
      if (!parsed) {
        parsed = parseReviewArtifact(artifactPath);
        artifactCache.set(artifactPath, parsed);
      }
      if (!parsed.found) {
        eventMissing = true;
        missingArtifactPaths.add(artifactPath);
        continue;
      }
      parsedArtifactPaths.add(artifactPath);
      if (parsed.blocked) {
        artifactAggregate.blocked = true;
      }
      mergeCountDicts(artifactAggregate.findings_by_severity, parsed.findings_by_severity, FINDING_SEVERITY_ORDER);
      mergeCountDicts(artifactAggregate.findings_by_lens, parsed.findings_by_lens, FINDING_LENS_ORDER);
      artifactAggregate.open_question_count += parsed.open_question_count;
    }
    const countSource = eventArtifacts.length && !eventMissing ? artifactAggregate : metrics;
    if (countSource.blocked) {
      blockedCount += 1;
    }
    openQuestionCount += Number(countSource.open_question_count ?? 0);
    mergeCountDicts(findingsBySeverity, countSource.findings_by_severity, FINDING_SEVERITY_ORDER);
    mergeCountDicts(findingsByLens, countSource.findings_by_lens, FINDING_LENS_ORDER);
    if (eventArtifacts.length && !eventMissing && !metrics.artifact_only && reviewEventHasArtifactMismatch(metrics, artifactAggregate)) {
      mismatches.push({
        review_pass_id: metrics.review_pass_id,
        artifact_paths: eventArtifacts,
      });
    }
  }
  return {
    review_count: reviewEvents.length,
    initial_review_count: initialReviewCount,
    rereview_count: rereviewCount,
    lane_counts: laneCounts,
    reviewer_counts: reviewerCounts,
    blocked_count: blockedCount,
    open_question_count: openQuestionCount,
    findings_by_severity: findingsBySeverity,
    findings_by_lens: findingsByLens,
    rereview_trigger_counts: triggerCounts,
    artifact_summary: {
      parsed_count: parsedArtifactPaths.size,
      missing_count: missingArtifactPaths.size,
      mismatch_count: mismatches.length,
      artifact_only_count: 0,
      mismatches,
    },
  };
}

function buildRunsReport(events: JsonRecord[], warningCount: number, filters: JsonRecord, activeDurations: Map<string, JsonRecord>): JsonRecord {
  const { runs, eventsByRun, reliableRuns } = collectRunContext(events);
  const summaries = reliableRuns.map(([key, run]) =>
    buildReliableRunSummary(key, run, eventsByRun.get(key) ?? [], activeDurations.get(key) ?? unavailableActiveDuration()));
  const items = summaries
    .map((summary) => buildReliableRunItem(summary))
    .sort((left, right) => compareDesc(left.last_timestamp, right.last_timestamp) || String(left.run_id).localeCompare(String(right.run_id)));
  const groups = runGroupRows(buildReliableRunGroups(summaries));
  const incompleteItems = [...runs.values()]
    .map((run) => ({ run, reason: runReliabilityReason(run) }))
    .filter((item) => item.reason !== null)
    .map((item) => buildIncompleteRunItem(item.run, item.reason as string))
    .sort((left, right) => compareDesc(left.last_timestamp, right.last_timestamp) || String(left.run_id).localeCompare(String(right.run_id)));

  return {
    report_type: "runs",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    run_count: items.length,
    incomplete_count: incompleteItems.length,
    range: runRange(reliableRuns.map(([, run]) => run)),
    groups,
    items,
    incomplete_items: incompleteItems,
  };
}

function collectRunContext(events: JsonRecord[]): {
  runs: Map<string, JsonRecord>;
  eventsByRun: Map<string, JsonRecord[]>;
  reliableKeys: Set<string>;
  reliableRuns: Array<[string, JsonRecord]>;
} {
  const runs = new Map<string, JsonRecord>();
  const eventsByRun = new Map<string, JsonRecord[]>();
  for (const event of events) {
    const runKey = runKeyForEvent(event);
    if (!runKey) {
      continue;
    }
    const [runId, repoPath, skill] = splitRunKey(runKey);
    eventsByRun.set(runKey, [...(eventsByRun.get(runKey) ?? []), event]);
    const run = runs.get(runKey) ?? {
      run_id: runId,
      repo_path: repoPath,
      skill,
      first_timestamp: event._parsed_timestamp,
      last_timestamp: event._parsed_timestamp,
      start_events: [],
      terminal_events: [],
    };
    run.first_timestamp = minDate(run.first_timestamp, event._parsed_timestamp);
    run.last_timestamp = maxDate(run.last_timestamp, event._parsed_timestamp);
    if (event.event_type === "skill_started") {
      run.start_events.push(event);
    } else if (TERMINAL_SKILL_EVENTS.has(event.event_type)) {
      run.terminal_events.push(event);
    }
    runs.set(runKey, run);
  }

  const reliableKeys = new Set([...runs.entries()].filter(([, run]) => runReliabilityReason(run) === null).map(([key]) => key));
  const runKeysBySession = new Map<string, Set<string>>();
  for (const [runKey, runEvents] of eventsByRun.entries()) {
    for (const event of runEvents) {
      if (typeof event.session_id === "string" && event.session_id) {
        const key = `${event.repo_path}\u0000${event.session_id}`;
        runKeysBySession.set(key, (runKeysBySession.get(key) ?? new Set()).add(runKey));
      }
    }
  }
  for (const event of events) {
    if (event.event_type !== "token_usage_recorded" || runKeyForEvent(event)) {
      continue;
    }
    if (typeof event.session_id !== "string" || !event.session_id) {
      continue;
    }
    const candidateKeys = runKeysBySession.get(`${event.repo_path}\u0000${event.session_id}`) ?? new Set();
    if (candidateKeys.size === 1) {
      const candidateKey = [...candidateKeys][0] as string;
      if (reliableKeys.has(candidateKey)) {
        eventsByRun.set(candidateKey, [...(eventsByRun.get(candidateKey) ?? []), event]);
      }
    }
  }

  const reliableRuns = [...runs.entries()].filter(([key]) => reliableKeys.has(key));
  return { runs, eventsByRun, reliableKeys, reliableRuns };
}

async function resolveReportActiveDurations(events: JsonRecord[], options: RuntimeOptions): Promise<Map<string, JsonRecord>> {
  const activeDurations = new Map<string, JsonRecord>();
  const { eventsByRun, reliableRuns } = collectRunContext(events);
  await Promise.all(reliableRuns.map(async ([key, run]) => {
    activeDurations.set(key, await resolveRunActiveDuration(run, eventsByRun.get(key) ?? [], options));
  }));
  return activeDurations;
}

function runReliabilityReason(run: JsonRecord): string | null {
  if (!run.start_events.length) {
    return "missing_start";
  }
  if (run.start_events.length > 1) {
    return "duplicate_starts";
  }
  if (!run.terminal_events.length) {
    return "missing_terminal";
  }
  if (run.terminal_events.length > 1) {
    return "duplicate_terminals";
  }
  if (run.terminal_events[0]._parsed_timestamp < run.start_events[0]._parsed_timestamp) {
    return "terminal_before_start";
  }
  return null;
}

function runStartTimestamp(run: JsonRecord): Date | null {
  return run.start_events.length ? run.start_events[0]._parsed_timestamp : null;
}

function runEndTimestamp(run: JsonRecord): Date | null {
  return run.terminal_events.length ? run.terminal_events[0]._parsed_timestamp : null;
}

function runStatusFromTerminal(run: JsonRecord): string {
  const terminalEvent = run.terminal_events[0];
  if (terminalEvent.event_type === "skill_completed") {
    return terminalEvent.metrics.final_status ?? terminalEvent.status ?? "completed";
  }
  return terminalEvent.status ?? "halted";
}

function runDurationSeconds(startTimestamp: Date | null, endTimestamp: Date | null): number {
  if (!startTimestamp || !endTimestamp) {
    return 0;
  }
  return Math.max(Math.floor((endTimestamp.getTime() - startTimestamp.getTime()) / 1000), 0);
}

async function resolveRunActiveDuration(run: JsonRecord, runEvents: JsonRecord[], options: RuntimeOptions): Promise<JsonRecord> {
  const startTimestamp = runStartTimestamp(run);
  const endTimestamp = runEndTimestamp(run);
  const startIso = datetimeToIso(startTimestamp);
  const endIso = datetimeToIso(endTimestamp);
  if (!startIso || !endIso) {
    return unavailableActiveDuration();
  }
  if (!options.client || !options.home) {
    return unavailableActiveDuration();
  }
  if (options.client !== "codex") {
    return unavailableActiveDuration();
  }
  const sessionIds = [...new Set(
    runEvents
      .map((event) => event.session_id)
      .filter((sessionId): sessionId is string => typeof sessionId === "string" && Boolean(sessionId)),
  )];
  if (!sessionIds.length) {
    return unavailableActiveDuration();
  }
  if (sessionIds.length > 1) {
    return unavailableActiveDuration();
  }
  const activeTime = await loadClientSessionActiveTime(options.client, options.home, sessionIds[0], startIso, endIso);
  if (!activeTime) {
    return unavailableActiveDuration();
  }
  return activeTime;
}

function unavailableActiveDuration(): JsonRecord {
  return {
    active_duration_seconds: null,
    active_turn_count: 0,
    active_duration_quality: "unavailable",
    active_duration_source: null,
  };
}

function buildReliableRunSummary(key: string, run: JsonRecord, runEvents: JsonRecord[], activeDuration: JsonRecord): JsonRecord {
  const startTimestamp = runStartTimestamp(run);
  const endTimestamp = runEndTimestamp(run);
  return {
    key,
    run,
    runEvents,
    run_id: run.run_id,
    repo_path: run.repo_path,
    skill: run.skill,
    status: runStatusFromTerminal(run),
    duration_seconds: runDurationSeconds(startTimestamp, endTimestamp),
    activeDuration,
    first_timestamp: run.first_timestamp,
    last_timestamp: run.last_timestamp,
    start_timestamp: startTimestamp,
    end_timestamp: endTimestamp,
  };
}

function buildReliableRunGroups(summaries: JsonRecord[]): Map<string, JsonRecord> {
  const groups = new Map<string, JsonRecord>();
  for (const summary of summaries) {
    const startTimestamp = summary.start_timestamp;
    const endTimestamp = summary.end_timestamp;
    if (!startTimestamp || !endTimestamp) {
      continue;
    }
    const key = `${summary.skill}\u0000${summary.status}`;
    const group = groups.get(key) ?? {
      skill: summary.skill,
      status: summary.status,
      run_count: 0,
      total_duration_seconds: 0,
      active_run_count: 0,
      total_active_duration_seconds: 0,
      first_timestamp: startTimestamp,
      last_timestamp: endTimestamp,
    };
    group.run_count += 1;
    group.total_duration_seconds += Number(summary.duration_seconds ?? 0);
    if (typeof summary.activeDuration.active_duration_seconds === "number") {
      group.active_run_count += 1;
      group.total_active_duration_seconds += summary.activeDuration.active_duration_seconds;
    }
    group.first_timestamp = minDate(group.first_timestamp, startTimestamp);
    group.last_timestamp = maxDate(group.last_timestamp, endTimestamp);
    groups.set(key, group);
  }
  return groups;
}

function runGroupRows(groups: Map<string, JsonRecord>): JsonRecord[] {
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([, group]) => ({
    skill: group.skill,
    status: group.status,
    run_count: group.run_count,
    total_duration_seconds: group.total_duration_seconds,
    average_duration_seconds: group.run_count ? Math.floor(group.total_duration_seconds / group.run_count) : 0,
    active_run_count: group.active_run_count,
    total_active_duration_seconds: group.active_run_count ? group.total_active_duration_seconds : null,
    average_active_duration_seconds: group.active_run_count ? Math.floor(group.total_active_duration_seconds / group.active_run_count) : null,
    first_timestamp: datetimeToIso(group.first_timestamp),
    last_timestamp: datetimeToIso(group.last_timestamp),
  }));
}

function buildReliableRunItem(summary: JsonRecord): JsonRecord {
  return {
    run_id: summary.run_id,
    repo_path: summary.repo_path,
    skill: summary.skill,
    status: summary.status,
    data_quality: "confirmed_closed",
    duration_seconds: summary.duration_seconds,
    ...summary.activeDuration,
    first_timestamp: datetimeToIso(summary.first_timestamp),
    last_timestamp: datetimeToIso(summary.last_timestamp),
    start_timestamp: datetimeToIso(summary.start_timestamp),
    end_timestamp: datetimeToIso(summary.end_timestamp),
    validation: summarizeValidationEvents(summary.runEvents.filter((event: JsonRecord) => event.event_type === "validation_attempt")),
    gates: summarizeGateEvents(summary.runEvents.filter((event: JsonRecord) => event.event_type === "gate_decided")),
    reviews: summarizeReviewEvents(summary.runEvents.filter((event: JsonRecord) => event.event_type === "review_pass_completed")),
    tokens: summarizeTokenEvents(summary.runEvents.filter((event: JsonRecord) => event.event_type === "token_usage_recorded")),
  };
}

function buildIncompleteRunItem(run: JsonRecord, reason: string): JsonRecord {
  const startTimestamp = runStartTimestamp(run);
  const endTimestamp = runEndTimestamp(run);
  return {
    run_id: run.run_id,
    repo_path: run.repo_path,
    skill: run.skill,
    status: "excluded",
    reason,
    data_quality: "incomplete_or_ambiguous",
    event_count: run.start_events.length + run.terminal_events.length,
    first_timestamp: datetimeToIso(run.first_timestamp),
    last_timestamp: datetimeToIso(run.last_timestamp),
    start_timestamp: datetimeToIso(startTimestamp),
    end_timestamp: datetimeToIso(endTimestamp),
  };
}

function runRange(runs: JsonRecord[]): JsonRecord {
  if (!runs.length) {
    return { first_timestamp: null, last_timestamp: null };
  }
  return {
    first_timestamp: datetimeToIso(new Date(Math.min(...runs.map((run) => run.first_timestamp.getTime())))),
    last_timestamp: datetimeToIso(new Date(Math.max(...runs.map((run) => run.last_timestamp.getTime())))),
  };
}

function resolveReviewArtifacts(event: JsonRecord): string[] {
  const repoRoot = event.repo_path;
  const resolved: string[] = [];
  const seen = new Set<string>();
  const artifactPaths = event.metrics.artifact_paths;
  if (!Array.isArray(artifactPaths)) {
    return resolved;
  }
  for (const pathText of artifactPaths) {
    if (typeof pathText !== "string" || !pathText.trim()) {
      continue;
    }
    const path = isAbsolute(pathText) ? pathText : join(repoRoot, pathText);
    if (seen.has(path)) {
      continue;
    }
    seen.add(path);
    resolved.push(path);
  }
  return resolved;
}

function parseReviewArtifact(path: string): JsonRecord {
  const summary: JsonRecord = {
    found: false,
    blocked: false,
    open_question_count: 0,
    findings_by_severity: emptyCountDict(FINDING_SEVERITY_ORDER),
    findings_by_lens: emptyCountDict(FINDING_LENS_ORDER),
  };
  if (!isFile(path)) {
    return summary;
  }
  let lines: string[];
  try {
    lines = requireReadFile(path).split(/\r?\n/);
  } catch {
    return summary;
  }
  summary.found = true;
  const statusLine = lines.find((line) => line.trim())?.trim() ?? "";
  const normalizedStatus = statusLine.toLowerCase().startsWith("status:")
    ? statusLine.split(":", 2)[1]?.trim() ?? ""
    : statusLine;
  summary.blocked = normalizedStatus.startsWith("Blocked");
  const sections = splitArtifactSections(lines);
  for (const line of sections.findings ?? []) {
    const match = FINDING_LINE_PATTERN.exec(line.trim());
    if (!match?.groups) {
      continue;
    }
    const severity = match.groups.severity;
    const lens = match.groups.lens;
    if (severity === undefined || lens === undefined) {
      continue;
    }
    summary.findings_by_severity[severity] += 1;
    summary.findings_by_lens[lens] += 1;
  }
  const openQuestionLines = (sections["open questions"] ?? []).map((line) => line.trim()).filter(Boolean);
  const questionLines = openQuestionLines.filter((line) => !isNoneArtifactLine(line));
  if (questionLines.length) {
    summary.open_question_count = questionLines.filter(
      (line) => line.startsWith("- ") || /^\d+\.\s+/.test(line) || normalizeArtifactListItem(line) !== "none",
    ).length;
  }
  return summary;
}

function splitArtifactSections(lines: string[]): Record<string, string[]> {
  const sections: Record<string, string[]> = {};
  let currentSection: string | null = null;
  for (const line of lines) {
    const heading = normalizeHeading(line.trim());
    if (ARTIFACT_SECTION_HEADINGS.has(heading)) {
      currentSection = heading;
      sections[currentSection] ??= [];
      continue;
    }
    if (currentSection) {
      sections[currentSection]!.push(line);
    }
  }
  return sections;
}

function normalizeHeading(value: string): string {
  const trimmed = value.endsWith(":") ? value.slice(0, -1) : value;
  return trimmed.trim().toLowerCase().replace(/\s+/g, " ");
}

function isNoneArtifactLine(line: string): boolean {
  return normalizeArtifactListItem(line) === "none";
}

function normalizeArtifactListItem(line: string): string {
  return line.trim().toLowerCase().replace(/^[-*]\s+/, "").replace(/^\d+\.\s+/, "").trim();
}

function reviewEventHasArtifactMismatch(metrics: JsonRecord, parsed: JsonRecord): boolean {
  const storedSeverity = emptyCountDict(FINDING_SEVERITY_ORDER);
  const storedLens = emptyCountDict(FINDING_LENS_ORDER);
  mergeCountDicts(storedSeverity, metrics.findings_by_severity, FINDING_SEVERITY_ORDER);
  mergeCountDicts(storedLens, metrics.findings_by_lens, FINDING_LENS_ORDER);
  return (
    JSON.stringify(storedSeverity) !== JSON.stringify(parsed.findings_by_severity)
    || JSON.stringify(storedLens) !== JSON.stringify(parsed.findings_by_lens)
    || Boolean(metrics.blocked) !== Boolean(parsed.blocked)
    || Number(metrics.open_question_count ?? 0) !== Number(parsed.open_question_count ?? 0)
  );
}

function referencedReviewArtifactPaths(reviewEvents: JsonRecord[]): Set<string> {
  const paths = new Set<string>();
  for (const event of reviewEvents) {
    for (const artifactPath of resolveReviewArtifacts(event)) {
      paths.add(resolve(artifactPath));
    }
  }
  return paths;
}

function localReviewArtifactEvents(filters: JsonRecord, referencedPaths: Set<string>): JsonRecord[] {
  if (filters.repo !== "current" || filters.skill !== null || !filters._current_repo) {
    return [];
  }
  const reviewDir = join(filters._current_repo, ".dreamers", "reviews");
  if (!isDirectory(reviewDir)) {
    return [];
  }
  const events: JsonRecord[] = [];
  for (const entry of safeReaddir(reviewDir).filter((item) => item.endsWith(".md")).sort()) {
    const path = join(reviewDir, entry);
    const resolved = resolve(path);
    if (referencedPaths.has(resolved)) {
      continue;
    }
    const timestamp = reviewArtifactTimestamp(path);
    if (!artifactTimestampMatchesFilters(timestamp, filters)) {
      continue;
    }
    events.push(buildArtifactReviewEvent(path, filters._current_repo, timestamp));
  }
  return events;
}

function reviewArtifactTimestamp(path: string): Date {
  try {
    return statSync(path).mtime;
  } catch {
    return new Date();
  }
}

function artifactTimestampMatchesFilters(timestamp: Date, filters: JsonRecord): boolean {
  if (filters._since && timestamp < filters._since) {
    return false;
  }
  if (filters._until && timestamp > filters._until) {
    return false;
  }
  return true;
}

function buildArtifactReviewEvent(path: string, repoRoot: string, timestamp: Date): JsonRecord {
  const reviewer = basename(path).split("-", 1)[0] || "review";
  const lane = REVIEW_LANES.has(reviewer) ? reviewer : "standard";
  return {
    schema_version: SCHEMA_VERSION,
    event_id: reviewArtifactEventId(path, timestamp),
    timestamp: datetimeToIso(timestamp),
    _parsed_timestamp: timestamp,
    event_type: "review_pass_completed",
    repo_path: repoRoot,
    source: "skill",
    status: defaultStatusForEvent("review_pass_completed"),
    skill: null,
    run_id: null,
    session_id: null,
    metrics: {
      review_pass_id: basename(path).replace(/\.md$/, ""),
      lane,
      reviewers: [reviewer],
      artifact_paths: [path],
      blocked: false,
      open_question_count: 0,
      findings_by_severity: emptyCountDict(FINDING_SEVERITY_ORDER),
      findings_by_lens: emptyCountDict(FINDING_LENS_ORDER),
      is_rereview: false,
      artifact_only: true,
    },
  };
}

function reviewArtifactEventId(path: string, timestamp: Date): string {
  return `artifact_review_${digest16(`${resolve(path)}|${datetimeToIso(timestamp)}`)}`;
}

function buildReviewsReport(events: JsonRecord[], warningCount: number, filters: JsonRecord): JsonRecord {
  const reviewEvents = events.filter((event) => event.event_type === "review_pass_completed");
  const artifactEvents = localReviewArtifactEvents(filters, referencedReviewArtifactPaths(reviewEvents));
  const allReviewEvents = [...reviewEvents, ...artifactEvents];
  const summary = summarizeReviewEvents(allReviewEvents);
  summary.artifact_summary.artifact_only_count = artifactEvents.length;
  return {
    report_type: "reviews",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    ...summary,
    range: eventRange(allReviewEvents),
  };
}

function buildValidationReport(events: JsonRecord[], warningCount: number, filters: JsonRecord): JsonRecord {
  const validationEvents = events.filter((event) => event.event_type === "validation_attempt");
  return {
    report_type: "validation",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    ...summarizeValidationEvents(validationEvents),
    range: eventRange(validationEvents),
  };
}

function buildGatesReport(events: JsonRecord[], warningCount: number, filters: JsonRecord): JsonRecord {
  const gateEvents = events.filter((event) => event.event_type === "gate_decided");
  return {
    report_type: "gates",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    ...summarizeGateEvents(gateEvents),
    range: eventRange(gateEvents),
  };
}

function summarizeTokenEvents(tokenEvents: JsonRecord[]): JsonRecord {
  return {
    exact: summarizeTokenSource("exact", tokenEvents),
    estimated: summarizeTokenSource("estimated", tokenEvents),
    unavailable: summarizeTokenSource("unavailable", tokenEvents),
  };
}

function summarizeTokenSource(sourceQuality: string, events: JsonRecord[]): JsonRecord {
  const rows = events.filter((event) => event.metrics.token_source === sourceQuality);
  let totals = emptyTokenTotals();
  const skills: JsonRecord = {};
  const models: JsonRecord = {};
  const sessions: JsonRecord = {};
  for (const event of rows) {
    const metrics = event.metrics;
    if (sourceQuality !== "unavailable") {
      mergeTokenTotals(totals, metrics);
    }
    const skill = event.skill ?? "unknown";
    skills[skill] ??= emptyTokenTotals();
    if (sourceQuality !== "unavailable") {
      mergeTokenTotals(skills[skill], metrics);
    }
    const model = metrics.model;
    if (model) {
      models[model] ??= emptyTokenTotals();
      if (sourceQuality !== "unavailable") {
        mergeTokenTotals(models[model], metrics);
      }
    }
    const sessionId = event.session_id ?? `event:${event.event_id}`;
    sessions[sessionId] ??= {
      session_id: sessionId,
      row_count: 0,
      ...emptyTokenTotals(),
    };
    sessions[sessionId].row_count += 1;
    if (sourceQuality !== "unavailable") {
      mergeTokenTotals(sessions[sessionId], metrics);
    }
  }
  if (sourceQuality === "unavailable") {
    totals = Object.fromEntries(TOKEN_FIELDS.map((field) => [field, null]));
  }
  return {
    source_quality: sourceQuality,
    row_count: rows.length,
    session_count: Object.keys(sessions).length,
    totals,
    sessions: Object.values(sessions).sort((left: any, right: any) => String(left.session_id).localeCompare(String(right.session_id))),
    skills,
    models,
  };
}

function emptyTokenTotals(): JsonRecord {
  return {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    ai_credits: 0.0,
  };
}

function mergeTokenTotals(target: JsonRecord, metrics: JsonRecord): void {
  for (const field of TOKEN_FIELDS) {
    const value = metrics[field];
    if (value !== null && value !== undefined) {
      target[field] = (target[field] ?? 0) + value;
    }
  }
}

function buildTokensReport(events: JsonRecord[], warningCount: number, filters: JsonRecord): JsonRecord {
  const tokenEvents = events.filter((event) => event.event_type === "token_usage_recorded");
  return {
    report_type: "tokens",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    ...summarizeTokenEvents(tokenEvents),
    range: eventRange(tokenEvents),
  };
}

function buildWorkflowReport(events: JsonRecord[]): JsonRecord {
  const cycleStatusCounts: JsonRecord = {};
  for (const event of events) {
    if (event.event_type !== "cycle_completed") {
      continue;
    }
    const cycleStatus = event.metrics.cycle_status;
    if (typeof cycleStatus === "string" && cycleStatus) {
      cycleStatusCounts[cycleStatus] = (cycleStatusCounts[cycleStatus] ?? 0) + 1;
    }
  }
  return {
    cycle_status_counts: sortObject(cycleStatusCounts),
    pr_count: events.filter((event) => event.event_type === "pr_created").length,
    retro_count: events.filter((event) => event.event_type === "retro_written").length,
  };
}

function buildSummaryReport(events: JsonRecord[], warningCount: number, filters: JsonRecord, activeDurations: Map<string, JsonRecord>): JsonRecord {
  const report: JsonRecord = {
    report_type: "summarize",
    warning_count: warningCount,
    filters: reportFiltersPublic(filters),
    runs: buildRunsReport(events, warningCount, filters, activeDurations),
    reviews: buildReviewsReport(events, warningCount, filters),
    validation: buildValidationReport(events, warningCount, filters),
    gates: buildGatesReport(events, warningCount, filters),
    tokens: buildTokensReport(events, warningCount, filters),
  };
  if (filters._client === "copilot") {
    report.workflow_outputs = buildWorkflowReport(events);
  }
  return report;
}

function reportFiltersPublic(filters: JsonRecord): JsonRecord {
  return {
    repo: filters.repo,
    skill: filters.skill,
    since: filters.since,
    until: filters.until,
    current_repo: filters.current_repo,
  };
}

const REPORT_BUILDERS: Record<ReportCommand, (events: JsonRecord[], warningCount: number, filters: JsonRecord, activeDurations: Map<string, JsonRecord>) => JsonRecord> = {
  runs: buildRunsReport,
  reviews: buildReviewsReport,
  validation: buildValidationReport,
  gates: buildGatesReport,
  tokens: buildTokensReport,
  summarize: buildSummaryReport,
};

export async function runReport<Command extends ReportCommand>(
  command: Command,
  options: ReportOptions = {},
): Promise<ReportPayloadFor<Command>> {
  const { events, warningCount } = await loadReportEvents(options);
  const resolvedEvents = await resolveReportTokenEvents(events, options);
  const filters = buildReportFilters(options);
  const filtered = filterReportEvents(resolvedEvents, filters);
  const activeDurations = command === "runs" || command === "summarize"
    ? await resolveReportActiveDurations(filtered, options)
    : new Map<string, JsonRecord>();
  return REPORT_BUILDERS[command](filtered, warningCount, filters, activeDurations) as ReportPayloadFor<Command>;
}
