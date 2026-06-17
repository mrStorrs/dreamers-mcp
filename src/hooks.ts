import { StatsValidationError } from "./errors.js";
import { buildHookTokenEvent } from "./tokens.js";
import {
  SCHEMA_VERSION,
  defaultStatusForEvent,
  digest16,
  isPlainObject,
  isoNoMillis,
  parseIsoTimestamp,
  stableStringify,
  utcNowIso,
} from "./events.js";
import type { Client, HookPayload, NormalizedStatsEvent, RuntimeOptions, StatsEventType } from "./types.js";

type JsonRecord = Record<string, any>;
type HookEventType = Extract<
  StatsEventType,
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
>;

function hookValue(payload: HookPayload, ...keys: string[]): any {
  for (const key of keys) {
    if (key in payload) {
      return payload[key];
    }
  }
  return undefined;
}

function hookNestedValue(payload: HookPayload, parentKeys: string[], childKeys: string[], defaultValue?: any): any {
  const parent = hookValue(payload, ...parentKeys);
  if (!isPlainObject(parent)) {
    return defaultValue;
  }
  const value = hookValue(parent, ...childKeys);
  return value === undefined ? defaultValue : value;
}

function codexResultType(payload: HookPayload): string {
  for (const parentKey of ["toolResult", "tool_result", "toolResponse", "tool_response"]) {
    const parent = hookValue(payload, parentKey);
    if (!isPlainObject(parent)) {
      continue;
    }
    for (const childKey of ["resultType", "result_type", "status"]) {
      const value = parent[childKey];
      if (typeof value === "string" && value.trim()) {
        return value;
      }
    }
  }
  return "success";
}

function hookTimestamp(payload: HookPayload): string {
  const value = hookValue(payload, "timestamp");
  if (value === null || value === undefined || value === "") {
    return utcNowIso();
  }
  if (typeof value === "number") {
    return isoNoMillis(new Date(value));
  }
  if (typeof value === "string") {
    parseIsoTimestamp(value);
    return value;
  }
  throw new StatsValidationError("invalid_timestamp", "hook timestamp must be epoch milliseconds or ISO text");
}

function hookEventId(eventName: string, event: JsonRecord): string {
  const raw = [
    eventName,
    event.event_type,
    event.session_id ?? "",
    event.timestamp,
    stableStringify(event.metrics),
  ].join("|");
  return `hook_${event.event_type}_${digest16(raw)}`;
}

export function buildHookEvent(eventName: string, payload: HookPayload): NormalizedStatsEvent {
  if (!isPlainObject(payload)) {
    throw new StatsValidationError("invalid_event", "event must be a JSON object");
  }
  const spec = hookSpec(eventName);
  const repoPath = hookValue(payload, "cwd", "repoPath", "repo_path");
  if (typeof repoPath !== "string" || !repoPath.trim()) {
    throw new StatsValidationError("invalid_field_type", "repo_path must be a non-empty string");
  }
  const event = {
    schema_version: SCHEMA_VERSION,
    event_id: "",
    timestamp: hookTimestamp(payload),
    event_type: spec.event_type,
    repo_path: repoPath,
    source: "hook",
    status: spec.status ? spec.status(payload) : defaultStatusForEvent(spec.event_type),
    session_id: hookValue(payload, "sessionId", "session_id", "turn_id", "turnId") ?? null,
    run_id: null,
    repo_name: null,
    branch: null,
    skill: null,
    metrics: spec.metrics(payload),
  } as NormalizedStatsEvent;
  event.event_id = hookEventId(eventName, event);
  return event;
}

function hookSpec(eventName: string): { event_type: HookEventType; status?: (payload: HookPayload) => string; metrics: (payload: HookPayload) => JsonRecord } {
  const sharedSessionStart: { event_type: HookEventType; metrics: (payload: HookPayload) => JsonRecord } = {
    event_type: "session_started",
    metrics: (payload: HookPayload) => ({
      session_source: hookValue(payload, "source"),
      initial_input_present: Boolean(hookValue(payload, "initialPrompt")),
    }),
  };
  const specs: Record<string, { event_type: HookEventType; status?: (payload: HookPayload) => string; metrics: (payload: HookPayload) => JsonRecord }> = {
    sessionStart: sharedSessionStart,
    SessionStart: sharedSessionStart,
    sessionEnd: {
      event_type: "session_completed",
      metrics: (payload) => ({ reason: hookValue(payload, "reason") }),
    },
    userPromptSubmitted: {
      event_type: "prompt_submitted",
      metrics: (payload) => {
        const prompt = String(hookValue(payload, "prompt") ?? "");
        return {
          prompt_count: 1,
          input_char_count: prompt.length,
          starts_with_slash: prompt.trimStart().startsWith("/"),
        };
      },
    },
    UserPromptSubmit: {
      event_type: "prompt_submitted",
      metrics: (payload) => {
        const prompt = String(hookValue(payload, "prompt") ?? "");
        return {
          prompt_count: 1,
          input_char_count: prompt.length,
          starts_with_slash: prompt.trimStart().startsWith("/"),
        };
      },
    },
    postToolUse: {
      event_type: "tool_completed",
      metrics: (payload) => ({
        tool_name: hookValue(payload, "toolName", "tool_name"),
        result_type: hookNestedValue(payload, ["toolResult", "tool_result"], ["resultType", "result_type"], "success"),
      }),
    },
    PostToolUse: {
      event_type: "tool_completed",
      metrics: (payload) => ({
        tool_name: hookValue(payload, "toolName", "tool_name"),
        result_type: codexResultType(payload),
      }),
    },
    postToolUseFailure: {
      event_type: "tool_failed",
      metrics: (payload) => ({
        tool_name: hookValue(payload, "toolName", "tool_name"),
        error_present: Boolean(hookValue(payload, "error")),
      }),
    },
    agentStop: {
      event_type: "turn_completed",
      metrics: (payload) => ({ stop_reason: hookValue(payload, "stopReason", "stop_reason") }),
    },
    Stop: {
      event_type: "turn_completed",
      metrics: (payload) => ({ stop_reason: hookValue(payload, "stopReason", "stop_reason") }),
    },
    subagentStart: {
      event_type: "subagent_started",
      metrics: (payload) => ({
        agent_name: hookValue(payload, "agentName", "agent_name"),
        agent_display_name: hookValue(payload, "agentDisplayName", "agent_display_name"),
      }),
    },
    SubagentStart: {
      event_type: "subagent_started",
      metrics: (payload) => ({
        agent_name: hookValue(payload, "agentName", "agent_name", "agent_type"),
        agent_display_name: hookValue(payload, "agentDisplayName", "agent_display_name", "agent_type"),
      }),
    },
    subagentStop: {
      event_type: "subagent_completed",
      metrics: (payload) => ({
        agent_name: hookValue(payload, "agentName", "agent_name"),
        agent_display_name: hookValue(payload, "agentDisplayName", "agent_display_name"),
        stop_reason: hookValue(payload, "stopReason", "stop_reason"),
      }),
    },
    SubagentStop: {
      event_type: "subagent_completed",
      metrics: (payload) => ({
        agent_name: hookValue(payload, "agentName", "agent_name", "agent_type"),
        agent_display_name: hookValue(payload, "agentDisplayName", "agent_display_name", "agent_type"),
        stop_reason: hookValue(payload, "stopReason", "stop_reason"),
      }),
    },
    errorOccurred: {
      event_type: "error_occurred",
      status: (payload) => (Boolean(hookValue(payload, "recoverable")) ? "recoverable" : "terminal"),
      metrics: (payload) => ({
        error_name: hookNestedValue(payload, ["error"], ["name"], "unknown"),
        error_context: hookValue(payload, "errorContext", "error_context"),
        recoverable: Boolean(hookValue(payload, "recoverable")),
      }),
    },
    preCompact: {
      event_type: "compaction_started",
      metrics: (payload) => ({
        trigger: hookValue(payload, "trigger"),
        instructions_present: Boolean(hookValue(payload, "customInstructions", "custom_instructions")),
      }),
    },
    PreCompact: {
      event_type: "compaction_started",
      metrics: (payload) => ({
        trigger: hookValue(payload, "trigger"),
        instructions_present: Boolean(hookValue(payload, "customInstructions", "custom_instructions")),
      }),
    },
  };
  const spec = specs[eventName];
  if (!spec) {
    throw new StatsValidationError("invalid_hook_event", "unsupported hook event");
  }
  return spec;
}

export async function buildHookEvents(
  eventName: string,
  payload: HookPayload,
  options: RuntimeOptions = {},
): Promise<NormalizedStatsEvent[]> {
  const primary = buildHookEvent(eventName, payload);
  const events: NormalizedStatsEvent[] = [primary];
  if (shouldBuildHookTokenEvent(eventName, options.client)) {
    events.push((await buildHookTokenEvent(primary, options)) as NormalizedStatsEvent);
  }
  return events;
}

function shouldBuildHookTokenEvent(eventName: string, client?: Client): boolean {
  return eventName === "Stop" || (client === "copilot" && eventName === "sessionEnd");
}
