#!/usr/bin/env node
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { buildCheckpointEvent, loadMetricsJson } from "./checkpoints.js";
import { defaultStatusForEvent, recordEvent, resolveClientContext, stableStringify } from "./events.js";
import { StatsValidationError } from "./errors.js";
import { buildHookEvents } from "./hooks.js";
import { doctor, runReport } from "./reports.js";
import { renderDashboardHtml } from "./dashboard.js";
import type { Client, ReportCommand, ReportPayload, RuntimeOptions, StatsEventInput } from "./types.js";
import { expandHomePath } from "./utils.js";

type FlagValue = string | true;
type FlagMap = Map<string, FlagValue>;
type JsonRecord = Record<string, any>;

const REPORT_COMMANDS = new Set<ReportCommand>(["summarize", "runs", "reviews", "validation", "gates", "tokens"]);
const BOOLEAN_FLAGS = new Set(["json", "print-event-id"]);

interface ParsedArgs {
  command: string;
  flags: FlagMap;
}

interface CliStreams {
  stdin?: NodeJS.ReadableStream;
  stdout?: NodeJS.WritableStream;
  stderr?: NodeJS.WritableStream;
}

function parseArgs(argv: string[]): ParsedArgs {
  const [command, ...tail] = argv;
  if (!command) {
    throw new StatsValidationError("missing_command", "missing command");
  }
  const flags = new Map<string, FlagValue>();
  for (let index = 0; index < tail.length; index += 1) {
    const token = tail[index];
    if (!token?.startsWith("--")) {
      throw new StatsValidationError("invalid_argument", `unexpected argument: ${token}`);
    }
    const withoutPrefix = token.slice(2);
    const equalsIndex = withoutPrefix.indexOf("=");
    if (equalsIndex >= 0) {
      flags.set(withoutPrefix.slice(0, equalsIndex), withoutPrefix.slice(equalsIndex + 1));
      continue;
    }
    if (BOOLEAN_FLAGS.has(withoutPrefix)) {
      flags.set(withoutPrefix, true);
      continue;
    }
    const next = tail[index + 1];
    if (next === undefined || next.startsWith("--")) {
      throw new StatsValidationError("missing_argument", `missing value for --${withoutPrefix}`);
    }
    flags.set(withoutPrefix, next);
    index += 1;
  }
  return { command, flags };
}

function flagString(flags: FlagMap, name: string): string | undefined {
  const value = flags.get(name);
  if (value === undefined || value === true) {
    return undefined;
  }
  return value;
}

function flagBool(flags: FlagMap, name: string): boolean {
  return flags.get(name) === true;
}

function requireFlag(flags: FlagMap, name: string): string {
  const value = flagString(flags, name);
  if (!value) {
    throw new StatsValidationError("missing_argument", `missing value for --${name}`);
  }
  return value;
}

function resolveCliContext(flags: FlagMap, payload?: JsonRecord): { client: Client; home: string } {
  let client = flagString(flags, "client");
  let home = flagString(flags, "home");
  const copilotHome = flagString(flags, "copilot-home");
  const codexHome = flagString(flags, "codex-home");
  if (copilotHome && codexHome) {
    throw new StatsValidationError("conflicting_home", "choose only one client-specific home flag");
  }
  if (copilotHome) {
    if (client && client !== "copilot") {
      throw new StatsValidationError("conflicting_client", "home flag conflicts with --client");
    }
    client = "copilot";
    home = requireCompatibleHome(home, copilotHome);
  }
  if (codexHome) {
    if (client && client !== "codex") {
      throw new StatsValidationError("conflicting_client", "home flag conflicts with --client");
    }
    client = "codex";
    home = requireCompatibleHome(home, codexHome);
  }
  const options: RuntimeOptions = {};
  if (client !== undefined) {
    options.client = client as Client;
  }
  if (home !== undefined) {
    options.home = home;
  }
  return resolveClientContext(options, payload);
}

function requireCompatibleHome(home: string | undefined, aliasHome: string): string {
  if (home !== undefined && resolve(expandHomePath(home)) !== resolve(expandHomePath(aliasHome))) {
    throw new StatsValidationError("conflicting_home", "home flag conflicts with --home");
  }
  return aliasHome;
}

async function readStdin(stdin: NodeJS.ReadableStream): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function loadEvent(flags: FlagMap, stdin: NodeJS.ReadableStream): Promise<JsonRecord> {
  const raw = flagString(flags, "event-json") ?? await readStdin(stdin);
  const payload: unknown = JSON.parse(raw);
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new StatsValidationError("invalid_event", "event must be a JSON object");
  }
  return payload as JsonRecord;
}

function writeLine(stream: NodeJS.WritableStream, text: string): void {
  stream.write(`${text}\n`);
}

function reportOptions(flags: FlagMap, context: { client: Client; home: string }) {
  const options: {
    client: Client;
    home: string;
    repo: "current" | "all";
    skill?: string;
    since?: string;
    until?: string;
    cwd: string;
  } = {
    client: context.client,
    home: context.home,
    repo: (flagString(flags, "repo") ?? "current") as "current" | "all",
    cwd: process.cwd(),
  };
  for (const key of ["skill", "since", "until"] as const) {
    const value = flagString(flags, key);
    if (value !== undefined) {
      options[key] = value;
    }
  }
  return options;
}

function checkpointInputFromFlags(flags: FlagMap) {
  const input = {
    eventType: requireFlag(flags, "event-type"),
    skill: requireFlag(flags, "skill"),
    runId: requireFlag(flags, "run-id"),
    metrics: loadMetricsJson(flagString(flags, "metrics-json")),
  };
  for (const [target, flag] of [
    ["status", "status"],
    ["sessionId", "session-id"],
    ["branch", "branch"],
    ["repoPath", "repo-path"],
    ["timestamp", "timestamp"],
  ] as const) {
    const value = flagString(flags, flag);
    if (value !== undefined) {
      (input as Record<string, unknown>)[target] = value;
    }
  }
  return input;
}

export async function runCli(argv: string[] = process.argv.slice(2), streams: CliStreams = {}): Promise<number> {
  const stdin = streams.stdin ?? process.stdin;
  const stdout = streams.stdout ?? process.stdout;
  const stderr = streams.stderr ?? process.stderr;
  let parsed: ParsedArgs;
  try {
    parsed = parseArgs(argv);
  } catch (error) {
    return handleUsageError(error, stderr);
  }
  const { command, flags } = parsed;

  if (command === "record") {
    try {
      const event = await loadEvent(flags, stdin);
      const context = resolveCliContext(flags, event);
      const eventId = await recordEvent(event as StatsEventInput, context);
      if (flagBool(flags, "print-event-id")) {
        writeLine(stdout, eventId);
      }
      return 0;
    } catch (error) {
      return handleWriteCommandError(error, stderr);
    }
  }

  if (command === "checkpoint") {
    try {
      const context = resolveCliContext(flags);
      const event = buildCheckpointEvent(checkpointInputFromFlags(flags));
      const eventId = await recordEvent(event, context);
      if (flagBool(flags, "print-event-id")) {
        writeLine(stdout, eventId);
      }
      return 0;
    } catch (error) {
      return handleWriteCommandError(error, stderr);
    }
  }

  if (command === "doctor") {
    try {
      const context = resolveCliContext(flags);
      const report = await doctor(context);
      if (flagBool(flags, "json")) {
        writeLine(stdout, stableStringify(report));
      } else {
        const status = report.writable ? "ok" : "error";
        writeLine(
          stdout,
          `${status} writable=${String(report.writable).toLowerCase()} events=${report.event_count} malformed=${report.malformed_line_count} path=${report.events_file}`,
        );
      }
      return report.writable ? 0 : 1;
    } catch (error) {
      return handleReadCommandError(error, stderr);
    }
  }

  if (command === "hook") {
    try {
      const payload = await loadEvent(flags, stdin);
      const context = resolveCliContext(flags, payload);
      const events = await buildHookEvents(requireFlag(flags, "event-name"), payload, context);
      for (const event of events) {
        await recordEvent(event, context);
      }
      return 0;
    } catch (error) {
      return handleWriteCommandError(error, stderr);
    }
  }

  if (REPORT_COMMANDS.has(command as ReportCommand)) {
    try {
      const context = resolveCliContext(flags);
      const report = await runReport(command as ReportCommand, reportOptions(flags, context));
      writeLine(stdout, flagBool(flags, "json") ? stableStringify(report) : formatReport(command as ReportCommand, report));
      return 0;
    } catch (error) {
      return handleReadCommandError(error, stderr);
    }
  }

  if (command === "dashboard") {
    let output: string;
    let outputPath: string | undefined;
    try {
      const context = resolveCliContext(flags);
      const report = await runReport("summarize", reportOptions(flags, context));
      output = renderDashboardHtml(report, {
        client: context.client,
        generatedAt: flagString(flags, "generated-at") ?? null,
      });
      outputPath = flagString(flags, "output");
    } catch (error) {
      return handleReadCommandError(error, stderr);
    }
    if (outputPath) {
      try {
        const expandedOutputPath = expandHomePath(outputPath);
        await mkdir(dirname(expandedOutputPath), { recursive: true });
        await writeFile(expandedOutputPath, output, "utf8");
      } catch {
        writeLine(stderr, "write_failed");
        return 1;
      }
    } else {
      stdout.write(output);
    }
    return 0;
  }

  writeLine(stderr, "unknown_command");
  return 2;
}

function handleUsageError(error: unknown, stderr: NodeJS.WritableStream): number {
  if (error instanceof StatsValidationError) {
    writeLine(stderr, error.category);
    return 2;
  }
  throw error;
}

function handleWriteCommandError(error: unknown, stderr: NodeJS.WritableStream): number {
  if (error instanceof SyntaxError) {
    writeLine(stderr, "invalid_json");
    return 2;
  }
  if (error instanceof StatsValidationError) {
    writeLine(stderr, error.category);
    return 2;
  }
  writeLine(stderr, "write_failed");
  return 1;
}

function handleReadCommandError(error: unknown, stderr: NodeJS.WritableStream): number {
  if (error instanceof StatsValidationError) {
    writeLine(stderr, error.category);
    return 2;
  }
  writeLine(stderr, "read_failed");
  return 1;
}

function formatReport(command: ReportCommand, report: ReportPayload): string {
  if (command === "runs") {
    return formatRunsReport(report as JsonRecord);
  }
  if (command === "reviews") {
    return formatReviewsReport(report as JsonRecord);
  }
  if (command === "validation") {
    return formatValidationReport(report as JsonRecord);
  }
  if (command === "gates") {
    return formatGatesReport(report as JsonRecord);
  }
  if (command === "tokens") {
    return formatTokensReport(report as JsonRecord);
  }
  return formatSummaryReport(report as JsonRecord);
}

function formatRunsReport(report: JsonRecord): string {
  const lines = ["Dreamers runs report", formatFilterHeader(report.filters), ...formatWarningLines(report.warning_count), `Runs: ${report.run_count}`];
  for (const group of (report.groups ?? []).slice(0, 8)) {
    lines.push(`- ${group.skill} [${group.status}] runs=${group.run_count} avg=${formatDuration(group.average_duration_seconds)}`);
  }
  return lines.join("\n");
}

function formatReviewsReport(report: JsonRecord): string {
  return [
    "Dreamers reviews report",
    formatFilterHeader(report.filters),
    ...formatWarningLines(report.warning_count),
    `Reviews: ${report.review_count} rereviews=${report.rereview_count}`,
    `Findings: ${formatCounterMap(report.findings_by_severity)}`,
    `Artifact mismatches: ${report.artifact_summary.mismatch_count}`,
  ].join("\n");
}

function formatValidationReport(report: JsonRecord): string {
  const lines = ["Dreamers validation report", formatFilterHeader(report.filters), ...formatWarningLines(report.warning_count), `Attempts: ${report.attempt_count}`];
  for (const [kind, summary] of Object.entries(report.command_kinds ?? {}) as [string, JsonRecord][]) {
    if (summary.attempt_count) {
      lines.push(`- ${kind} attempts=${summary.attempt_count} fails=${summary.failure_count} retries=${summary.retry_count}`);
    }
  }
  return lines.join("\n");
}

function formatGatesReport(report: JsonRecord): string {
  return [
    "Dreamers gates report",
    formatFilterHeader(report.filters),
    ...formatWarningLines(report.warning_count),
    `Gates: ${formatCounterMap(report.gate_type_counts)}`,
  ].join("\n");
}

function formatTokensReport(report: JsonRecord): string {
  const lines = ["Dreamers tokens report", formatFilterHeader(report.filters), ...formatWarningLines(report.warning_count)];
  for (const sourceQuality of ["exact", "estimated", "unavailable"]) {
    const summary = report[sourceQuality];
    const totalTokens = sourceQuality === "unavailable" ? "n/a" : summary.totals.total_tokens;
    lines.push(`- ${sourceQuality}: rows=${summary.row_count} sessions=${summary.session_count} total_tokens=${totalTokens}`);
  }
  return lines.join("\n");
}

function formatSummaryReport(report: JsonRecord): string {
  if (report.workflow_outputs) {
    return formatCopilotSummaryReport(report);
  }
  const lines = [
    "Dreamers stats summary",
    formatFilterHeader(report.filters),
    ...formatWarningLines(report.warning_count),
    "Skill runs",
    ...formatSummaryBlockFromRuns(report.runs),
    "Reviews",
    `- ${report.reviews.review_count} reviews`,
    `- findings ${formatCounterMap(report.reviews.findings_by_severity)}`,
    "Validation",
    `- ${report.validation.attempt_count} attempts`,
    "Gates",
    `- ${formatCounterMap(report.gates.gate_type_counts) || "none"}`,
    "Tokens",
    `- exact total ${report.tokens.exact.totals.total_tokens}`,
  ];
  return lines.slice(0, 30).join("\n");
}

function formatCopilotSummaryReport(report: JsonRecord): string {
  const lines = [`Dreamers stats summary (${formatCompatFilterHeader(report.filters)})`, ...formatCompatWarningLines(report.warning_count), ""];
  lines.push("Skill runs", ...formatCopilotSummaryBlockFromRuns(report.runs), "");
  lines.push("Reviews", ...formatCopilotSummaryBlockFromReviews(report.reviews), "");
  lines.push("Validation", ...formatCopilotSummaryBlockFromValidation(report.validation), "");
  lines.push("Gates", ...formatCopilotSummaryBlockFromGates(report.gates), "");
  lines.push("Workflow outputs", ...formatCopilotSummaryBlockFromWorkflow(report.workflow_outputs), "");
  lines.push("Tokens", ...formatCopilotSummaryBlockFromTokens(report.tokens));
  return lines.join("\n");
}

function formatCompatFilterHeader(filters: JsonRecord): string {
  const parts = [`repo=${filters.repo}`];
  for (const key of ["skill", "since", "until"]) {
    if (filters[key] !== null && filters[key] !== undefined) {
      parts.push(`${key}=${filters[key]}`);
    }
  }
  return parts.join(", ");
}

function formatCompatWarningLines(warningCount: number): string[] {
  return warningCount ? [`Warnings: skipped ${warningCount} malformed or unreadable line(s)`] : [];
}

function formatCopilotSummaryBlockFromRuns(report: JsonRecord): string[] {
  if (!report.groups?.length) {
    return ["- none"];
  }
  return report.groups.map(
    (group: JsonRecord) =>
      `- ${group.skill} ${group.status}: ${group.run_count} runs, avg ${formatCompatDuration(group.average_duration_seconds)}, total ${formatCompatDuration(group.total_duration_seconds)}`,
  );
}

function formatCopilotSummaryBlockFromReviews(report: JsonRecord): string[] {
  return [
    `- reviews=${report.review_count} rereviews=${report.rereview_count} blocked=${report.blocked_count} open_questions=${report.open_question_count}`,
    `- lanes: ${formatCounterMapSorted(report.lane_counts)}`,
    `- artifacts: parsed=${report.artifact_summary.parsed_count} mismatches=${report.artifact_summary.mismatch_count}`,
  ];
}

function formatCopilotSummaryBlockFromValidation(report: JsonRecord): string[] {
  const entries = Object.entries(report.command_kinds ?? {}) as [string, JsonRecord][];
  if (!entries.length) {
    return ["- none"];
  }
  return entries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([kind, stats]) => `- ${kind}: attempts=${stats.attempt_count} failures=${stats.failure_count} retries=${stats.retry_count}`);
}

function formatCopilotSummaryBlockFromGates(report: JsonRecord): string[] {
  const entries = Object.entries(report.decision_counts ?? {}) as [string, JsonRecord][];
  if (!entries.length) {
    return ["- none"];
  }
  return entries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([gateType, decisions]) => `- ${gateType}: ${formatCounterMapSorted(decisions)}`);
}

function formatCopilotSummaryBlockFromWorkflow(report: JsonRecord): string[] {
  return [
    `- cycles: ${formatCounterMapSorted(report.cycle_status_counts)}`,
    `- prs=${report.pr_count} retros=${report.retro_count}`,
  ];
}

function formatCopilotSummaryBlockFromTokens(report: JsonRecord): string[] {
  return ["exact", "estimated", "unavailable"].map((sourceQuality) => {
    const summary = report[sourceQuality];
    const totalTokens = summary.totals.total_tokens === null ? "none" : summary.totals.total_tokens;
    return `- ${sourceQuality}: rows=${summary.row_count} total_tokens=${totalTokens}`;
  });
}

function formatSummaryBlockFromRuns(report: JsonRecord): string[] {
  if (report.run_count === 0) {
    return ["- none"];
  }
  const lines = [`- ${report.run_count} runs`];
  if (report.groups?.length) {
    const first = report.groups[0];
    lines.push(`- ${first.skill} [${first.status}] x${first.run_count}`);
  }
  return lines;
}

function formatFilterHeader(filters: JsonRecord): string {
  const parts = [`repo=${filters.repo}`];
  if (filters.current_repo) {
    parts.push(`current_repo=${filters.current_repo}`);
  }
  for (const key of ["skill", "since", "until"]) {
    if (filters[key]) {
      parts.push(`${key}=${filters[key]}`);
    }
  }
  return `Filters: ${parts.join(" ")}`;
}

function formatWarningLines(warningCount: number): string[] {
  return warningCount ? [`Warnings: skipped ${warningCount} malformed historical lines`] : [];
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  if (hours) {
    return `${hours}h${minuteRemainder}m${remainder}s`;
  }
  if (minutes) {
    return `${minutes}m${remainder}s`;
  }
  return `${remainder}s`;
}

function formatCompatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) {
    return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return minuteRemainder ? `${hours}h ${minuteRemainder}m` : `${hours}h`;
}

function formatCounterMap(values: JsonRecord): string {
  return Object.entries(values ?? {})
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ");
}

function formatCounterMapSorted(values: JsonRecord): string {
  const entries = Object.entries(values ?? {}).sort(([left], [right]) => left.localeCompare(right));
  if (!entries.length) {
    return "none";
  }
  return entries.map(([key, value]) => `${key}=${value}`).join(", ");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runCli().then((code) => {
    process.exitCode = code;
  });
}

export { defaultStatusForEvent };
