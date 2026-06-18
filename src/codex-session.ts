import { readFile } from "node:fs/promises";
import { basename, join } from "node:path";

import { isPlainObject, parseIsoTimestamp } from "./events.js";
import type { ClientSessionActiveTimeMetrics, TokenMetrics } from "./types.js";
import { isDirectory, isFile, safePathMtime, safeReaddir } from "./utils.js";

type JsonRecord = Record<string, any>;

const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const CODEX_SESSION_MATCH_LIMIT = 8;
const CODEX_SESSION_DAY_DIR_LIMIT = 8;

export interface CodexSessionTokenRecord {
  timestamp: Date | null;
  metrics: TokenMetrics;
}

export interface CodexSessionTaskEvent {
  kind: "start" | "complete";
  timestamp: Date;
}

export interface CodexSessionSummary {
  tokenRecords: CodexSessionTokenRecord[];
  taskEvents: CodexSessionTaskEvent[];
}

export function codexSessionCandidatePaths(home: string, sessionId: any, timestamp?: string): string[] {
  if (typeof sessionId !== "string" || !sessionId.trim()) {
    return [];
  }
  const normalizedSessionId = sessionId.trim();
  if (!SESSION_ID_PATTERN.test(normalizedSessionId)) {
    return [];
  }
  const sessionsRoot = join(home, "sessions");
  if (!isDirectory(sessionsRoot)) {
    return [];
  }
  const seen = new Set<string>();
  const candidates: string[] = [];
  const addCandidate = (candidate: string) => {
    if (seen.has(candidate) || !isFile(candidate)) {
      return;
    }
    seen.add(candidate);
    candidates.push(candidate);
  };
  addCandidate(join(sessionsRoot, `${normalizedSessionId}.jsonl`));
  for (const searchDir of codexSessionSearchDirs(sessionsRoot, timestamp)) {
    for (const entry of safeReaddir(searchDir)) {
      const path = join(searchDir, entry);
      if (entry.endsWith(".jsonl") && codexSessionFileMatches(path, normalizedSessionId)) {
        addCandidate(path);
      }
    }
  }
  return candidates.sort((left, right) => safePathMtime(right) - safePathMtime(left)).slice(0, CODEX_SESSION_MATCH_LIMIT);
}

export async function readCodexSessionSummaryAt(path: string): Promise<CodexSessionSummary | null> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch {
    return null;
  }
  const summary: CodexSessionSummary = {
    tokenRecords: [],
    taskEvents: [],
  };
  for (const line of text.split(/\r?\n/)) {
    let row: any;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if (!isPlainObject(row) || row.type !== "event_msg") {
      continue;
    }
    const tokenRecord = codexSessionRowTokenRecord(row);
    if (tokenRecord) {
      summary.tokenRecords.push(tokenRecord);
    }
    const taskEvent = codexSessionRowTaskEvent(row);
    if (taskEvent) {
      summary.taskEvents.push(taskEvent);
    }
  }
  return summary;
}

export function latestCodexSessionTokenMetrics(
  summary: CodexSessionSummary,
  targetTimestamp: Date | null,
): TokenMetrics | null {
  let latestMetrics: TokenMetrics | null = null;
  let latestMatchingMetrics: TokenMetrics | null = null;
  for (const record of summary.tokenRecords) {
    latestMetrics = record.metrics;
    if (!targetTimestamp || !record.timestamp || record.timestamp <= targetTimestamp) {
      latestMatchingMetrics = record.metrics;
    }
  }
  return latestMatchingMetrics ?? latestMetrics;
}

export function codexSessionActiveTime(
  summary: CodexSessionSummary,
  runStart: Date,
  runEnd: Date,
): ClientSessionActiveTimeMetrics | null {
  let previousTimestamp: Date | null = null;
  let pendingStart: Date | null = null;
  const segments: Array<{ start: Date; end: Date }> = [];
  for (const event of summary.taskEvents) {
    if (previousTimestamp && event.timestamp < previousTimestamp) {
      return null;
    }
    previousTimestamp = event.timestamp;
    if (event.kind === "start") {
      if (pendingStart && intervalOverlaps(pendingStart, event.timestamp, runStart, runEnd)) {
        return null;
      }
      pendingStart = event.timestamp;
      continue;
    }
    if (!pendingStart) {
      if (event.timestamp >= runStart && event.timestamp <= runEnd) {
        return null;
      }
      continue;
    }
    if (event.timestamp < pendingStart) {
      return null;
    }
    segments.push({ start: pendingStart, end: event.timestamp });
    pendingStart = null;
  }
  if (pendingStart && intervalOverlaps(pendingStart, runEnd, runStart, runEnd)) {
    return null;
  }

  let activeMillis = 0;
  let activeTurnCount = 0;
  for (const segment of segments) {
    if (!intervalOverlaps(segment.start, segment.end, runStart, runEnd)) {
      continue;
    }
    if (segment.start < runStart || segment.end > runEnd) {
      return null;
    }
    activeMillis += segment.end.getTime() - segment.start.getTime();
    activeTurnCount += 1;
  }
  if (!activeTurnCount || activeMillis <= 0) {
    return null;
  }
  return {
    active_duration_seconds: Math.floor(activeMillis / 1000),
    active_turn_count: activeTurnCount,
    active_duration_quality: "observed",
    active_duration_source: "codex_session_tasks",
  };
}

function codexSessionSearchDirs(sessionsRoot: string, timestamp?: string): string[] {
  const searchDirs: string[] = [];
  const seen = new Set<string>();
  const addDir = (path: string) => {
    if (seen.has(path) || !isDirectory(path)) {
      return;
    }
    seen.add(path);
    searchDirs.push(path);
  };
  if (timestamp) {
    try {
      const parsed = parseIsoTimestamp(timestamp);
      addDir(join(
        sessionsRoot,
        String(parsed.getUTCFullYear()).padStart(4, "0"),
        String(parsed.getUTCMonth() + 1).padStart(2, "0"),
        String(parsed.getUTCDate()).padStart(2, "0"),
      ));
    } catch {
      // Ignore malformed timestamps for fallback session search.
    }
  }
  for (const dayDir of listCodexSessionDayDirs(sessionsRoot).sort((left, right) => safePathMtime(right) - safePathMtime(left))) {
    if (searchDirs.length >= CODEX_SESSION_DAY_DIR_LIMIT) {
      break;
    }
    addDir(dayDir);
  }
  return searchDirs;
}

function listCodexSessionDayDirs(sessionsRoot: string): string[] {
  const dayDirs: string[] = [];
  for (const year of safeReaddir(sessionsRoot)) {
    const yearDir = join(sessionsRoot, year);
    if (!isDirectory(yearDir)) {
      continue;
    }
    for (const month of safeReaddir(yearDir)) {
      const monthDir = join(yearDir, month);
      if (!isDirectory(monthDir)) {
        continue;
      }
      for (const day of safeReaddir(monthDir)) {
        const dayDir = join(monthDir, day);
        if (isDirectory(dayDir)) {
          dayDirs.push(dayDir);
        }
      }
    }
  }
  return dayDirs;
}

function codexSessionFileMatches(path: string, sessionId: string): boolean {
  const stem = basename(path).replace(/\.jsonl$/, "");
  return stem === sessionId || stem.endsWith(`-${sessionId}`);
}

function codexSessionRowTokenRecord(row: JsonRecord): CodexSessionTokenRecord | null {
  const payload = row.payload;
  if (!isPlainObject(payload) || payload.type !== "token_count") {
    return null;
  }
  const info = payload.info;
  if (!isPlainObject(info) || !isPlainObject(info.last_token_usage)) {
    return null;
  }
  const metrics = codexUsageTokenMetrics(info.last_token_usage, info);
  if (!metrics) {
    return null;
  }
  let timestamp: Date | null = null;
  if (typeof row.timestamp === "string") {
    try {
      timestamp = parseIsoTimestamp(row.timestamp);
    } catch {
      timestamp = null;
    }
  }
  return { timestamp, metrics };
}

function codexSessionRowTaskEvent(row: JsonRecord): CodexSessionTaskEvent | null {
  const payload = row.payload;
  if (!isPlainObject(payload)) {
    return null;
  }
  const kind = payload.type === "task_started" ? "start" : payload.type === "task_complete" ? "complete" : null;
  if (!kind || typeof row.timestamp !== "string") {
    return null;
  }
  try {
    return { kind, timestamp: parseIsoTimestamp(row.timestamp) };
  } catch {
    return null;
  }
}

function codexUsageTokenMetrics(usage: JsonRecord, info: JsonRecord): TokenMetrics | null {
  const inputTokens = optionalTokenInt(usage.input_tokens);
  const outputTokens = optionalTokenInt(usage.output_tokens);
  let totalTokens = optionalTokenInt(usage.total_tokens);
  const cacheReadTokens = firstTokenInt(usage, "cache_read_tokens", "cached_input_tokens");
  const cacheWriteTokens = firstTokenInt(usage, "cache_write_tokens", "cache_creation_input_tokens");
  if (inputTokens === null && outputTokens === null && totalTokens === null) {
    return null;
  }
  if (totalTokens === null) {
    totalTokens = (inputTokens ?? 0) + (outputTokens ?? 0);
  }
  const metrics: TokenMetrics = {
    token_source: "exact",
    attribution_scope: "turn",
    input_tokens: inputTokens ?? 0,
    output_tokens: outputTokens ?? 0,
    total_tokens: totalTokens,
    cache_read_tokens: cacheReadTokens ?? 0,
    cache_write_tokens: cacheWriteTokens ?? 0,
  };
  const aiCredits = optionalMetricNumber(usage.ai_credits);
  if (aiCredits !== null) {
    metrics.ai_credits = aiCredits;
  }
  const model = usage.model ?? info.model;
  if (typeof model === "string" && model.trim()) {
    metrics.model = model;
  }
  return metrics;
}

function firstTokenInt(values: JsonRecord, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = optionalTokenInt(values[key]);
    if (value !== null) {
      return value;
    }
  }
  return null;
}

function optionalTokenInt(value: any): number | null {
  if (typeof value === "boolean" || !Number.isInteger(value) || value < 0) {
    return null;
  }
  return value;
}

function optionalMetricNumber(value: any): number | null {
  if (typeof value === "boolean" || typeof value !== "number" || Number.isNaN(value) || value < 0) {
    return null;
  }
  return value;
}

function intervalOverlaps(start: Date, end: Date, windowStart: Date, windowEnd: Date): boolean {
  return end > windowStart && start < windowEnd;
}
