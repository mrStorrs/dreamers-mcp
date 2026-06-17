import { resolve } from "node:path";

import {
  SCHEMA_VERSION,
  defaultStatusForEvent,
  digest16,
  parseIsoTimestamp,
  stableStringify,
  utcNowIso,
} from "./events.js";
import { StatsValidationError } from "./errors.js";
import type { StatsEventInput, StatsEventType } from "./types.js";

export interface CheckpointInput {
  eventType: string;
  skill: string;
  runId: string;
  status?: string;
  sessionId?: string;
  branch?: string;
  repoPath?: string;
  timestamp?: string;
  metrics?: Record<string, unknown>;
}

export function buildCheckpointEvent(input: CheckpointInput): StatsEventInput {
  const timestamp = resolveCheckpointTimestamp(input.timestamp);
  const event = {
    schema_version: SCHEMA_VERSION,
    event_id: "",
    timestamp,
    event_type: input.eventType as StatsEventType,
    repo_path: input.repoPath ? resolve(input.repoPath) : resolve(process.cwd()),
    source: "skill",
    status: input.status ?? defaultStatusForEvent(input.eventType),
    skill: input.skill,
    run_id: input.runId,
    session_id: input.sessionId ?? null,
    branch: input.branch ?? null,
    metrics: input.metrics ?? {},
  } as StatsEventInput;
  event.event_id = checkpointEventId(event);
  return event;
}

export function loadMetricsJson(raw?: string): Record<string, unknown> {
  if (raw === undefined || raw === "") {
    return {};
  }
  const payload: unknown = JSON.parse(raw);
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new StatsValidationError("invalid_metrics", "metrics must be a JSON object");
  }
  return payload as Record<string, unknown>;
}

function resolveCheckpointTimestamp(value?: string): string {
  if (value === undefined) {
    return utcNowIso();
  }
  parseIsoTimestamp(value);
  return value;
}

function checkpointEventId(event: StatsEventInput): string {
  const raw = [
    event.event_type,
    event.skill ?? "",
    event.run_id ?? "",
    event.timestamp,
    stableStringify(event.metrics ?? {}),
  ].join("|");
  return `skill_${event.event_type}_${digest16(raw)}`;
}
