import { join } from "node:path";
import { readFile } from "node:fs/promises";

import {
  codexSessionActiveTime,
  codexSessionCandidatePaths,
  latestCodexSessionTokenMetrics,
  readCodexSessionSummaryAt,
} from "./codex-session.js";
import { SCHEMA_VERSION, defaultStatusForEvent, digest16, isPlainObject, parseIsoTimestamp, stableStringify } from "./events.js";
import type { Client, ClientSessionActiveTimeMetrics, RuntimeOptions, TokenMetrics } from "./types.js";
import { expandHomePath, isFile } from "./utils.js";

type JsonRecord = Record<string, any>;

const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;

export async function buildHookTokenEvent(primaryEvent: JsonRecord, options: RuntimeOptions): Promise<JsonRecord> {
  if (options.client && options.home) {
    const exactMetrics = await loadClientSessionTokenMetrics(options.client, options.home, primaryEvent.session_id, primaryEvent.timestamp);
    if (exactMetrics) {
      return buildTokenEvent(primaryEvent, exactMetrics);
    }
  }
  return buildUnavailableTokenEvent(primaryEvent);
}

export async function loadClientSessionTokenMetrics(
  client: Client,
  home: string,
  sessionId: any,
  timestamp?: string,
): Promise<TokenMetrics | null> {
  const expandedHome = expandHomePath(home);
  if (client === "codex") {
    return loadCodexSessionTokenMetrics(expandedHome, sessionId, timestamp);
  }
  return loadCopilotSessionTokenMetrics(expandedHome, sessionId, timestamp);
}

export async function loadClientSessionActiveTime(
  client: Client,
  home: string,
  sessionId: any,
  startTimestamp: string | null,
  endTimestamp: string | null,
): Promise<ClientSessionActiveTimeMetrics | null> {
  const expandedHome = expandHomePath(home);
  if (client !== "codex") {
    return null;
  }
  return loadCodexSessionActiveTime(expandedHome, sessionId, startTimestamp, endTimestamp);
}

async function loadCodexSessionTokenMetrics(home: string, sessionId: any, timestamp?: string): Promise<TokenMetrics | null> {
  const targetTimestamp = parseOptionalTargetTimestamp(timestamp);
  const candidates = codexSessionCandidatePaths(home, sessionId, timestamp);
  for (const candidate of candidates) {
    const summary = await readCodexSessionSummaryAt(candidate);
    const metrics = summary ? latestCodexSessionTokenMetrics(summary, targetTimestamp) : null;
    if (metrics) {
      return metrics;
    }
  }
  return null;
}

async function loadCodexSessionActiveTime(
  home: string,
  sessionId: any,
  startTimestamp: string | null,
  endTimestamp: string | null,
): Promise<ClientSessionActiveTimeMetrics | null> {
  const start = parseOptionalTargetTimestamp(startTimestamp ?? undefined);
  const end = parseOptionalTargetTimestamp(endTimestamp ?? undefined);
  if (!start || !end || end < start) {
    return null;
  }
  const candidates = codexSessionCandidatePaths(home, sessionId, startTimestamp ?? undefined);
  for (const candidate of candidates) {
    const summary = await readCodexSessionSummaryAt(candidate);
    const metrics = summary ? codexSessionActiveTime(summary, start, end) : null;
    if (metrics) {
      return metrics;
    }
  }
  return null;
}

async function loadCopilotSessionTokenMetrics(home: string, sessionId: any, timestamp?: string): Promise<TokenMetrics | null> {
  if (typeof sessionId !== "string" || !sessionId.trim()) {
    return null;
  }
  const normalizedSessionId = sessionId.trim();
  if (!SESSION_ID_PATTERN.test(normalizedSessionId)) {
    return null;
  }
  const sessionPath = join(home, "session-state", normalizedSessionId, "events.jsonl");
  if (!isFile(sessionPath)) {
    return null;
  }
  return readCopilotSessionTokenMetricsAt(sessionPath, parseOptionalTargetTimestamp(timestamp));
}

function parseOptionalTargetTimestamp(timestamp?: string): Date | null {
  if (!timestamp) {
    return null;
  }
  try {
    return parseIsoTimestamp(timestamp);
  } catch {
    return null;
  }
}

async function readCopilotSessionTokenMetricsAt(path: string, targetTimestamp: Date | null): Promise<TokenMetrics | null> {
  let latestMetrics: TokenMetrics | null = null;
  let latestMatchingMetrics: TokenMetrics | null = null;
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch {
    return null;
  }
  for (const line of text.split(/\r?\n/)) {
    const record = copilotSessionLineTokenRecord(line);
    if (!record) {
      continue;
    }
    latestMetrics = record.metrics;
    if (!targetTimestamp || !record.timestamp || record.timestamp <= targetTimestamp) {
      latestMatchingMetrics = record.metrics;
    }
  }
  return latestMatchingMetrics ?? latestMetrics;
}

function copilotSessionLineTokenRecord(line: string): { timestamp: Date | null; metrics: TokenMetrics } | null {
  let row: any;
  try {
    row = JSON.parse(line);
  } catch {
    return null;
  }
  if (!isPlainObject(row) || row.type !== "session.shutdown" || !isPlainObject(row.data)) {
    return null;
  }
  const metrics = copilotModelMetricsTokenMetrics(row.data.modelMetrics);
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

function copilotModelMetricsTokenMetrics(modelMetrics: any): TokenMetrics | null {
  if (!isPlainObject(modelMetrics)) {
    return null;
  }
  const totals = emptyTokenTotals();
  const models: string[] = [];
  let foundUsage = false;
  for (const [model, modelData] of Object.entries(modelMetrics)) {
    if (typeof model !== "string" || !model.trim() || !isPlainObject(modelData)) {
      continue;
    }
    const usage = modelData.usage;
    if (!isPlainObject(usage)) {
      continue;
    }
    const inputTokens = firstTokenInt(usage, "inputTokens", "input_tokens");
    const outputTokens = firstTokenInt(usage, "outputTokens", "output_tokens");
    let totalTokens = firstTokenInt(usage, "totalTokens", "total_tokens");
    const cacheReadTokens = firstTokenInt(usage, "cacheReadTokens", "cachedInputTokens", "cache_read_tokens", "cached_input_tokens");
    const cacheWriteTokens = firstTokenInt(usage, "cacheWriteTokens", "cacheCreationInputTokens", "cache_write_tokens", "cache_creation_input_tokens");
    if (inputTokens === null && outputTokens === null && totalTokens === null) {
      continue;
    }
    foundUsage = true;
    models.push(model.trim());
    if (totalTokens === null) {
      totalTokens = (inputTokens ?? 0) + (outputTokens ?? 0);
    }
    totals.input_tokens += inputTokens ?? 0;
    totals.output_tokens += outputTokens ?? 0;
    totals.total_tokens += totalTokens;
    totals.cache_read_tokens += cacheReadTokens ?? 0;
    totals.cache_write_tokens += cacheWriteTokens ?? 0;
  }
  if (!foundUsage) {
    return null;
  }
  const metrics: TokenMetrics = {
    token_source: "exact",
    attribution_scope: "session",
    input_tokens: totals.input_tokens,
    output_tokens: totals.output_tokens,
    total_tokens: totals.total_tokens,
    cache_read_tokens: totals.cache_read_tokens,
    cache_write_tokens: totals.cache_write_tokens,
  };
  const singleModel = models.length === 1 ? models[0] : null;
  if (singleModel) {
    metrics.model = singleModel;
  }
  return metrics;
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

function buildUnavailableTokenEvent(primaryEvent: JsonRecord): JsonRecord {
  return buildTokenEvent(primaryEvent, {
    token_source: "unavailable",
    attribution_scope: "turn",
  });
}

export function buildTokenEvent(primaryEvent: JsonRecord, metrics: TokenMetrics): JsonRecord {
  const event: JsonRecord = {
    schema_version: SCHEMA_VERSION,
    event_id: "",
    timestamp: primaryEvent.timestamp,
    event_type: "token_usage_recorded",
    repo_path: primaryEvent.repo_path,
    source: "summary",
    status: defaultStatusForEvent("token_usage_recorded"),
    session_id: primaryEvent.session_id,
    metrics,
  };
  const raw = [primaryEvent.event_id, event.timestamp, stableStringify(event.metrics)].join("|");
  event.event_id = `summary_token_usage_${digest16(raw)}`;
  return event;
}
