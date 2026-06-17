#!/usr/bin/env node
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

import { buildCheckpointEvent } from "./checkpoints.js";
import { recordEvent } from "./events.js";
import { StatsValidationError } from "./errors.js";
import { buildHookEvents } from "./hooks.js";
import { doctor, runReport } from "./reports.js";
import { stableStringify } from "./events.js";
import type { Client, ReportCommand, RuntimeOptions, StatsEventInput } from "./types.js";

type JsonRecord = Record<string, any>;

export const PROTOCOL_VERSION = "2025-11-25";
const REPORT_COMMANDS = new Set<ReportCommand>(["summarize", "runs", "reviews", "validation", "gates", "tokens"]);

export function toolDefinitions(): JsonRecord[] {
  const reportSchema = {
    type: "object",
    properties: {
      client: { type: "string", enum: ["copilot", "codex"] },
      home: { type: "string" },
      repo: { type: "string", enum: ["current", "all"] },
      skill: { type: "string" },
      since: { type: "string" },
      until: { type: "string" },
      output: { type: "string", enum: ["json"] },
    },
    required: ["client", "home"],
  };
  return [
    {
      name: "doctor",
      description: "Return stats path health and malformed-line counts for a client home.",
      inputSchema: {
        type: "object",
        properties: {
          client: { type: "string", enum: ["copilot", "codex"] },
          home: { type: "string" },
        },
        required: ["client", "home"],
      },
    },
    ...[...REPORT_COMMANDS].map((name) => ({
      name,
      description: `Return the bounded ${name} report for a client stats log.`,
      inputSchema: reportSchema,
    })),
    {
      name: "record_event",
      description: "Validate, redact, and append a prebuilt stats event to the selected client home.",
      inputSchema: {
        type: "object",
        properties: {
          client: { type: "string", enum: ["copilot", "codex"] },
          home: { type: "string" },
          event: { type: "object" },
        },
        required: ["client", "home", "event"],
      },
    },
    {
      name: "record_checkpoint",
      description: "Build and record a Dreamers checkpoint event for the selected client home.",
      inputSchema: {
        type: "object",
        properties: {
          client: { type: "string", enum: ["copilot", "codex"] },
          home: { type: "string" },
          event_type: { type: "string" },
          skill: { type: "string" },
          run_id: { type: "string" },
          status: { type: "string" },
          session_id: { type: "string" },
          branch: { type: "string" },
          repo_path: { type: "string" },
          timestamp: { type: "string" },
          metrics: { type: "object" },
        },
        required: ["client", "home", "event_type", "skill", "run_id"],
      },
    },
    {
      name: "record_hook",
      description: "Build and record a hook-derived runtime event for the selected client home.",
      inputSchema: {
        type: "object",
        properties: {
          client: { type: "string", enum: ["copilot", "codex"] },
          home: { type: "string" },
          event_name: { type: "string" },
          payload: { type: "object" },
        },
        required: ["client", "home", "event_name", "payload"],
      },
    },
  ];
}

function jsonrpcResult(messageId: unknown, result: JsonRecord): JsonRecord {
  return { jsonrpc: "2.0", id: messageId, result };
}

function jsonrpcError(messageId: unknown, code: number, message: string): JsonRecord {
  return { jsonrpc: "2.0", id: messageId, error: { code, message } };
}

function toolSuccess(payload: JsonRecord): JsonRecord {
  return {
    resultType: "complete",
    content: [{ type: "text", text: stableStringify(payload) }],
    structuredContent: payload,
    isError: false,
  };
}

function toolError(message: string): JsonRecord {
  return {
    resultType: "complete",
    content: [{ type: "text", text: message }],
    isError: true,
  };
}

function toolOptions(argumentsPayload: JsonRecord): RuntimeOptions {
  const options: RuntimeOptions = {};
  if (argumentsPayload.client !== undefined) {
    options.client = argumentsPayload.client as Client;
  }
  if (argumentsPayload.home !== undefined) {
    options.home = argumentsPayload.home;
  }
  return options;
}

export async function handleToolCall(name: string, argumentsPayload: JsonRecord): Promise<JsonRecord> {
  const options = toolOptions(argumentsPayload);
  if (name === "doctor") {
    return toolSuccess(await doctor(options));
  }
  if (REPORT_COMMANDS.has(name as ReportCommand)) {
    const reportOptions: RuntimeOptions & {
      repo: "current" | "all";
      skill?: string;
      since?: string;
      until?: string;
      cwd?: string;
    } = {
      ...options,
      repo: argumentsPayload.repo ?? "current",
    };
    for (const key of ["skill", "since", "until", "cwd"] as const) {
      if (argumentsPayload[key] !== undefined) {
        reportOptions[key] = argumentsPayload[key];
      }
    }
    return toolSuccess(await runReport(name as ReportCommand, reportOptions));
  }
  if (name === "record_event") {
    const eventId = await recordEvent(argumentsPayload.event as StatsEventInput, options);
    return toolSuccess({ event_id: eventId });
  }
  if (name === "record_checkpoint") {
    const checkpointInput = {
      eventType: argumentsPayload.event_type,
      skill: argumentsPayload.skill,
      runId: argumentsPayload.run_id,
      metrics: argumentsPayload.metrics ?? {},
    };
    for (const [target, source] of [
      ["status", "status"],
      ["sessionId", "session_id"],
      ["branch", "branch"],
      ["repoPath", "repo_path"],
      ["timestamp", "timestamp"],
    ] as const) {
      if (argumentsPayload[source] !== undefined) {
        (checkpointInput as Record<string, unknown>)[target] = argumentsPayload[source];
      }
    }
    const event = buildCheckpointEvent(checkpointInput);
    const eventId = await recordEvent(event, options);
    return toolSuccess({ event_id: eventId });
  }
  if (name === "record_hook") {
    const events = await buildHookEvents(argumentsPayload.event_name, argumentsPayload.payload, options);
    const eventIds: string[] = [];
    for (const event of events) {
      eventIds.push(await recordEvent(event, options));
    }
    return toolSuccess({ event_id: eventIds[0], event_ids: eventIds });
  }
  throw new StatsValidationError("invalid_tool", "tool is not supported");
}

export async function handleMessage(message: JsonRecord): Promise<JsonRecord | null> {
  const method = message.method;
  const messageId = message.id;
  const params = message.params ?? {};

  if (method === "initialize") {
    return jsonrpcResult(messageId, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: "dreamers-mcp", version: "0.1.0" },
      instructions: "Use report tools for bounded summaries; raw stats writes require explicit tool calls.",
    });
  }
  if (method === "notifications/initialized") {
    return null;
  }
  if (method === "ping") {
    return jsonrpcResult(messageId, {});
  }
  if (method === "tools/list") {
    return jsonrpcResult(messageId, { resultType: "complete", tools: toolDefinitions() });
  }
  if (method === "tools/call") {
    let result: JsonRecord;
    try {
      result = await handleToolCall(params.name, params.arguments ?? {});
    } catch (error) {
      result = toolError(error instanceof Error ? error.message : String(error));
    }
    return jsonrpcResult(messageId, result);
  }
  if (messageId === null || messageId === undefined) {
    return null;
  }
  return jsonrpcError(messageId, -32601, "Method not found");
}

export async function serve(input: NodeJS.ReadableStream = process.stdin, output: NodeJS.WritableStream = process.stdout): Promise<number> {
  const reader = createInterface({ input });
  for await (const rawLine of reader) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    let message: JsonRecord;
    try {
      message = JSON.parse(line);
    } catch {
      continue;
    }
    const response = await handleMessage(message);
    if (response !== null) {
      output.write(`${JSON.stringify(response)}\n`);
    }
  }
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  serve().then((code) => {
    process.exitCode = code;
  });
}
