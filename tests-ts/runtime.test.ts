import { execFile as execFileCallback } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import type { StatsEventInput, StatsEventMetricMap } from "../src/index.js";
import {
  StatsValidationError,
  buildHookEvent,
  buildHookEvents,
  normalizeEvent,
  recordEvent,
  renderDashboardHtml,
  runReport,
} from "../src/runtime.js";

const execFile = promisify(execFileCallback);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const baseFixtureEvent = {
  schema_version: 1,
  event_id: "evt_01",
  timestamp: "2026-06-13T10:00:00-07:00",
  event_type: "skill_started",
  repo_path: "/tmp/example",
  source: "skill",
  status: "started",
  skill: "dreamers-full",
  run_id: "run_01",
  metrics: { mode: "plan-path" },
} satisfies StatsEventInput;

const typeCheckedSkillEvent = baseFixtureEvent;

const typeCheckedValidationMetrics = {
  command_kind: "test",
  command_label: "npm test",
  attempt_number: 1,
  result: "pass",
} satisfies StatsEventMetricMap["validation_attempt"];

const typeCheckedValidationEvent = {
  schema_version: 1,
  event_id: "evt_type_validation",
  timestamp: "2026-06-13T10:01:00Z",
  event_type: "validation_attempt",
  repo_path: "/tmp/example",
  source: "skill",
  metrics: typeCheckedValidationMetrics,
} satisfies StatsEventInput;

const typeCheckedTokenEvent = {
  schema_version: 1,
  event_id: "evt_type_token",
  timestamp: "2026-06-13T10:02:00Z",
  event_type: "token_usage_recorded",
  repo_path: "/tmp/example",
  source: "summary",
  metrics: { token_source: "unavailable", attribution_scope: "turn" },
} satisfies StatsEventInput;

const typeCheckedEvents: StatsEventInput[] = [
  typeCheckedSkillEvent,
  typeCheckedValidationEvent,
  typeCheckedTokenEvent,
];

void typeCheckedEvents;

const invalidSkillStartedMetrics = {
  mode: "plan-path",
  // @ts-expect-error skill_started metrics are closed to unknown keys.
  unexpected: true,
} satisfies StatsEventMetricMap["skill_started"];

void invalidSkillStartedMetrics;

const invalidValidationMetrics = {
  command_kind: "test",
  command_label: "npm test",
  attempt_number: 1,
  result: "pass",
  // @ts-expect-error validation_attempt metrics are closed to unknown keys.
  unexpected: true,
} satisfies StatsEventMetricMap["validation_attempt"];

void invalidValidationMetrics;

// @ts-expect-error hook-derived event types cannot use skill source.
const invalidHookSourceEvent: StatsEventInput = {
  schema_version: 1,
  event_id: "evt_type_bad_source",
  timestamp: "2026-06-13T10:03:00Z",
  event_type: "tool_completed",
  repo_path: "/tmp/example",
  source: "skill",
  metrics: { tool_name: "exec_command", result_type: "success" },
};

void invalidHookSourceEvent;

function fixtureEvent(overrides: Record<string, any> = {}): any {
  return {
    ...baseFixtureEvent,
    ...overrides,
  };
}

function expectValidationCategory(action: () => unknown, category: string) {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(StatsValidationError);
    expect((error as StatsValidationError).category).toBe(category);
    return;
  }
  throw new Error(`expected StatsValidationError category ${category}`);
}

async function makeTempRepo(home: string, name: string): Promise<string> {
  const repoPath = join(home, name);
  await mkdir(join(repoPath, ".git"), { recursive: true });
  await mkdir(join(repoPath, "subdir"), { recursive: true });
  return repoPath;
}

async function appendMalformedStatsLine(home: string) {
  await writeFile(join(home, "dreamers", "stats", "events.jsonl"), "not-json\n", { flag: "a" });
}

async function recordFixtureEvent(home: string, overrides: Partial<StatsEventInput>) {
  return recordEvent(fixtureEvent(overrides), { client: "codex", home });
}

async function writeCodexSessionRows(home: string, sessionId: string, rolloutTimestamp: string, rows: string[]) {
  const [datePart] = rolloutTimestamp.split("T");
  const [year, month, day] = datePart!.split("-");
  const sessionPath = join(home, "sessions", year!, month!, day!, `rollout-${rolloutTimestamp}-${sessionId}.jsonl`);
  await mkdir(dirname(sessionPath), { recursive: true });
  await writeFile(sessionPath, `${rows.join("\n")}\n`, "utf8");
}

describe("published TypeScript package", () => {
  it("builds loadable dist entrypoints without importing Python runtime code", async () => {
    await rm(join(repoRoot, "dist"), { recursive: true, force: true });
    await execFile("npm", ["run", "build"], { cwd: repoRoot });

    const distEntry = join(repoRoot, "dist", "index.js");
    const distTypes = join(repoRoot, "dist", "index.d.ts");
    const staleNestedEntry = join(repoRoot, "dist", "src", "index.js");
    const staleTestEntry = join(repoRoot, "dist", "tests-ts", "runtime.test.js");
    await access(distEntry);
    await access(distTypes);
    await expect(access(staleNestedEntry)).rejects.toThrow();
    await expect(access(staleTestEntry)).rejects.toThrow();

    const builtModule = await import(`${pathToFileURL(distEntry).href}?t=${Date.now()}`);
    expect(builtModule.normalizeEvent).toBeTypeOf("function");
    expect(builtModule.runReport).toBeTypeOf("function");

    const builtSource = await readFile(distEntry, "utf8");
    expect(builtSource).not.toContain("dreamers_stats");
    expect(builtSource).not.toContain("python");
  });
});

describe("event normalization and JSONL recording", () => {
  it("validates, redacts only sensitive content, fills metadata, and writes compact JSONL", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-runtime-"));
    const event = fixtureEvent({
      event_id: "evt_redact",
      event_type: "tool_completed",
      source: "hook",
      status: "completed",
      metrics: {
        prompt_count: 1,
        prompt: "copy the whole request body",
        token_source: "exact",
        tool_result: "full output",
        authorization: "Bearer secret-token-123456",
        nested: {
          prompt_ids: ["prompt_01"],
          transcript_text: "full transcript",
        },
      },
    });

    const normalized = normalizeEvent(event);
    const metrics = normalized.metrics as Record<string, any>;
    expect(normalized.repo_name).toBe("example");
    expect(metrics.prompt_count).toBe(1);
    expect(metrics.prompt).toBe("[REDACTED]");
    expect(metrics.token_source).toBe("exact");
    expect(metrics.tool_result).toBe("[REDACTED]");
    expect(metrics.authorization).toBe("[REDACTED]");
    const nested = metrics.nested as { prompt_ids: string[]; transcript_text: string };
    expect(nested.prompt_ids).toEqual(["prompt_01"]);
    expect(nested.transcript_text).toBe("[REDACTED]");

    await recordEvent(event, { client: "copilot", home });
    const raw = await readFile(join(home, "dreamers", "stats", "events.jsonl"), "utf8");
    expect(raw).not.toContain("\n  ");
    const rows = raw.trim().split("\n").map((line) => JSON.parse(line));
    expect(rows).toHaveLength(1);
    expect(rows[0].event_id).toBe("evt_redact");
    expect(rows[0].metrics.tool_result).toBe("[REDACTED]");
  });

  it("preserves validation categories for malformed fields and checkpoint metrics", () => {
    expectValidationCategory(
      () => normalizeEvent(fixtureEvent({ event_id: "bad id with spaces" })),
      "invalid_event_id",
    );
    expectValidationCategory(
      () => normalizeEvent(fixtureEvent({ timestamp: "2026-06-13T10:00:00" })),
      "invalid_timestamp",
    );
    expectValidationCategory(
      () =>
        normalizeEvent(
          fixtureEvent({
            event_id: "evt_bad_metric",
            metrics: { mode: "plan-path", unexpected: true },
          }),
        ),
      "invalid_metric_key",
    );
  });
});

describe("hook event conversion and token harvesting", () => {
  it("maps Copilot and Codex hook names into normalized event types and metrics", () => {
    const cases = [
      {
        name: "SessionStart",
        payload: { cwd: "/tmp/example", sessionId: "sess_01", timestamp: "2026-06-15T00:00:00Z", source: "codex" },
        expected: { event_type: "session_started", metrics: { session_source: "codex", initial_input_present: false } },
      },
      {
        name: "UserPromptSubmit",
        payload: { cwd: "/tmp/example", sessionId: "sess_01", timestamp: "2026-06-15T00:00:01Z", prompt: " /test" },
        expected: { event_type: "prompt_submitted", metrics: { prompt_count: 1, input_char_count: 6, starts_with_slash: true } },
      },
      {
        name: "PostToolUse",
        payload: {
          cwd: "/tmp/example",
          sessionId: "sess_01",
          timestamp: "2026-06-15T00:00:02Z",
          toolName: "exec_command",
          toolResult: { status: "success" },
        },
        expected: { event_type: "tool_completed", metrics: { tool_name: "exec_command", result_type: "success" } },
      },
      {
        name: "PreCompact",
        payload: { cwd: "/tmp/example", sessionId: "sess_01", timestamp: "2026-06-15T00:00:03Z", trigger: "manual" },
        expected: { event_type: "compaction_started", metrics: { trigger: "manual", instructions_present: false } },
      },
      {
        name: "SubagentStart",
        payload: { cwd: "/tmp/example", sessionId: "sess_01", timestamp: "2026-06-15T00:00:04Z", agent_type: "Probe" },
        expected: { event_type: "subagent_started", metrics: { agent_name: "Probe", agent_display_name: "Probe" } },
      },
      {
        name: "SubagentStop",
        payload: {
          cwd: "/tmp/example",
          sessionId: "sess_01",
          timestamp: "2026-06-15T00:00:05Z",
          agent_type: "Probe",
          stopReason: "complete",
        },
        expected: { event_type: "subagent_completed", metrics: { agent_name: "Probe", stop_reason: "complete" } },
      },
      {
        name: "sessionEnd",
        payload: { cwd: "/tmp/example", sessionId: "sess_01", timestamp: "2026-06-15T00:00:06Z", reason: "complete" },
        expected: { event_type: "session_completed", metrics: { reason: "complete" } },
      },
    ];

    for (const entry of cases) {
      const event = buildHookEvent(entry.name, entry.payload);
      expect(event.event_type).toBe(entry.expected.event_type);
      expect(event.metrics).toMatchObject(entry.expected.metrics);
      expect(event.source).toBe("hook");
      expect(event.repo_path).toBe("/tmp/example");
    }
  });

  it("builds Codex Stop hook events with exact turn token metrics", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-codex-"));
    const sessionId = "sess_01";
    const sessionPath = join(
      home,
      "sessions",
      "2026",
      "06",
      "15",
      `rollout-2026-06-15T00-00-00-${sessionId}.jsonl`,
    );
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(
      sessionPath,
      `${JSON.stringify({
        timestamp: "2026-06-15T00:00:01Z",
        type: "event_msg",
        payload: {
          type: "token_count",
          info: {
            model: "gpt-test",
            last_token_usage: {
              input_tokens: 10,
              output_tokens: 4,
              total_tokens: 14,
              cached_input_tokens: 2,
            },
          },
        },
      })}\n`,
      "utf8",
    );

    const events = await buildHookEvents(
      "Stop",
      {
        sessionId,
        timestamp: "2026-06-15T00:00:02Z",
        cwd: "/tmp/example",
        stopReason: "complete",
      },
      { client: "codex", home },
    );

    expect(events).toHaveLength(2);
    expect(events[0]!.event_type).toBe("turn_completed");
    expect(events[1]!.event_type).toBe("token_usage_recorded");
    expect(events[1]!.metrics).toMatchObject({
      token_source: "exact",
      attribution_scope: "turn",
      input_tokens: 10,
      output_tokens: 4,
      total_tokens: 14,
      cache_read_tokens: 2,
      model: "gpt-test",
    });
  });

  it("falls back to unavailable Codex tokens when no session log is present", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-codex-missing-"));
    const events = await buildHookEvents(
      "Stop",
      {
        sessionId: "missing_session",
        timestamp: "2026-06-15T00:00:02Z",
        cwd: "/tmp/example",
      },
      { client: "codex", home },
    );

    expect(events).toHaveLength(2);
    expect(events[1]!.metrics).toMatchObject({
      token_source: "unavailable",
      attribution_scope: "turn",
    });
  });

  it("builds Copilot sessionEnd hook events with exact session token metrics", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-copilot-"));
    const sessionId = "sess_01";
    const sessionPath = join(home, "session-state", sessionId, "events.jsonl");
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(
      sessionPath,
      `${JSON.stringify({
        timestamp: "2026-06-15T00:00:01Z",
        type: "session.shutdown",
        data: {
          modelMetrics: {
            "gpt-test": {
              usage: {
                inputTokens: 7,
                outputTokens: 5,
                totalTokens: 12,
                cacheReadTokens: 3,
                cacheWriteTokens: 2,
              },
            },
          },
        },
      })}\n`,
      "utf8",
    );

    const events = await buildHookEvents(
      "sessionEnd",
      {
        sessionId,
        timestamp: "2026-06-15T00:00:02Z",
        cwd: "/tmp/example",
        reason: "complete",
      },
      { client: "copilot", home },
    );

    expect(events).toHaveLength(2);
    expect(events[0]!.event_type).toBe("session_completed");
    expect(events[1]!.metrics).toMatchObject({
      token_source: "exact",
      attribution_scope: "session",
      input_tokens: 7,
      output_tokens: 5,
      total_tokens: 12,
      cache_read_tokens: 3,
      cache_write_tokens: 2,
      model: "gpt-test",
    });
  });

  it("accepts docs-shaped Codex hook payloads without timestamps", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-docs-hooks-"));
    const before = Date.now() - 1_000;

    const sessionStart = buildHookEvent("SessionStart", {
      cwd: "/tmp/example",
      source: "startup",
      session_id: "session_docs",
    });
    const prompt = buildHookEvent("UserPromptSubmit", {
      cwd: "/tmp/example",
      prompt: "do not store prompt text",
      turn_id: "turn_docs",
      session_id: "session_docs",
    });
    const stopEvents = await buildHookEvents(
      "Stop",
      {
        cwd: "/tmp/example",
        stop_hook_active: false,
        last_assistant_message: "do not store assistant text",
        turn_id: "turn_docs",
        session_id: "session_docs",
      },
      { client: "codex", home },
    );
    const after = Date.now() + 1_000;

    const events = [sessionStart, prompt, ...stopEvents];
    expect(events.map((event) => event.event_type)).toEqual([
      "session_started",
      "prompt_submitted",
      "turn_completed",
      "token_usage_recorded",
    ]);
    for (const event of events) {
      const timestamp = Date.parse(event.timestamp);
      expect(timestamp).toBeGreaterThanOrEqual(before);
      expect(timestamp).toBeLessThanOrEqual(after);
    }
    expect(events.map((event) => JSON.stringify(event)).join("\n")).not.toContain("do not store");
  });

  it("matches exact Codex session filenames instead of newer overlapping names", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-codex-overlap-"));
    const sessionDir = join(home, "sessions", "2026", "06", "15");
    await mkdir(sessionDir, { recursive: true });
    const wrongPath = join(sessionDir, "rollout-2026-06-15T00-00-00-session_overlap_extra.jsonl");
    const correctPath = join(sessionDir, "rollout-2026-06-15T00-00-00-session_overlap.jsonl");
    await writeFile(
      wrongPath,
      `${JSON.stringify({
        type: "event_msg",
        payload: {
          type: "token_count",
          info: { last_token_usage: { input_tokens: 900, output_tokens: 99, total_tokens: 999 } },
        },
      })}\n`,
      "utf8",
    );
    await writeFile(
      correctPath,
      `${JSON.stringify({
        type: "event_msg",
        payload: {
          type: "token_count",
          info: { last_token_usage: { input_tokens: 100, output_tokens: 23, total_tokens: 123 } },
        },
      })}\n`,
      "utf8",
    );
    await utimes(correctPath, 1_718_302_400, 1_718_302_400);
    await utimes(wrongPath, 1_718_302_500, 1_718_302_500);

    const events = await buildHookEvents(
      "Stop",
      {
        cwd: "/tmp/example",
        timestamp: "2026-06-15T00:02:00Z",
        session_id: "session_overlap",
        stop_hook_active: false,
      },
      { client: "codex", home },
    );

    expect(events).toHaveLength(2);
    expect(events[1]!.metrics).toMatchObject({
      token_source: "exact",
      total_tokens: 123,
    });
  });
});

describe("report builders and dashboard rendering", () => {
  it("derives active AI time from Codex task spans without exposing session content", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-active-time-"));
    const repoPath = await makeTempRepo(home, "repo");
    const sessionId = "session_active_time";
    const sessionPath = join(
      home,
      "sessions",
      "2026",
      "06",
      "15",
      `rollout-2026-06-15T10-00-00-${sessionId}.jsonl`,
    );
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(
      sessionPath,
      [
        JSON.stringify({ timestamp: "2026-06-15T10:00:05Z", type: "event_msg", payload: { type: "task_started" } }),
        JSON.stringify({
          timestamp: "2026-06-15T10:00:06Z",
          type: "event_msg",
          payload: { type: "user_message", message: "SECRET_ACTIVE_PROMPT" },
        }),
        JSON.stringify({
          timestamp: "2026-06-15T10:00:55Z",
          type: "event_msg",
          payload: { type: "agent_message", message: "SECRET_ACTIVE_ASSISTANT" },
        }),
        JSON.stringify({
          timestamp: "2026-06-15T10:01:05Z",
          type: "event_msg",
          payload: {
            type: "token_count",
            info: { last_token_usage: { input_tokens: 12, output_tokens: 8, total_tokens: 20 } },
          },
        }),
        JSON.stringify({ timestamp: "2026-06-15T10:01:05Z", type: "event_msg", payload: { type: "task_complete" } }),
        "malformed SECRET_ACTIVE_TOOL_OUTPUT",
        JSON.stringify({ timestamp: "2026-06-15T10:04:00Z", type: "event_msg", payload: { type: "task_started" } }),
        JSON.stringify({ timestamp: "2026-06-15T10:04:30Z", type: "event_msg", payload: { type: "task_complete" } }),
        "",
      ].join("\n"),
      "utf8",
    );

    await recordFixtureEvent(home, {
      event_id: "evt_active_start",
      timestamp: "2026-06-15T10:00:00Z",
      repo_path: repoPath,
      run_id: "run_active",
      session_id: sessionId,
      skill: "dreamers-full",
      metrics: { mode: "task-description" },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_active_done",
      timestamp: "2026-06-15T10:05:00Z",
      event_type: "skill_completed",
      repo_path: repoPath,
      run_id: "run_active",
      session_id: sessionId,
      skill: "dreamers-full",
      status: "completed",
      metrics: { final_status: "completed" },
    });

    const report = await runReport("runs", {
      client: "codex",
      home,
      repo: "current",
      cwd: repoPath,
    });

    expect(report.run_count).toBe(1);
    const run = report.items[0] as any;
    expect(run.duration_seconds).toBe(300);
    expect(run.active_duration_seconds).toBe(90);
    expect(run.active_turn_count).toBe(2);
    expect(run.active_duration_quality).toBe("observed");
    expect(run.active_duration_source).toBe("codex_session_tasks");
    expect(run.active_duration_unavailable_reason).toBeUndefined();

    const group = report.groups[0] as any;
    expect(group.total_duration_seconds).toBe(300);
    expect(group.average_duration_seconds).toBe(300);
    expect(group.active_run_count).toBe(1);
    expect(group.total_active_duration_seconds).toBe(90);
    expect(group.average_active_duration_seconds).toBe(90);

    const serialized = JSON.stringify(report);
    expect(serialized).not.toContain("SECRET_ACTIVE_PROMPT");
    expect(serialized).not.toContain("SECRET_ACTIVE_ASSISTANT");
    expect(serialized).not.toContain("SECRET_ACTIVE_TOOL_OUTPUT");
  });

  it("marks active AI time unavailable for ambiguous session logs while preserving run summaries", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-active-time-missing-"));
    const repoPath = await makeTempRepo(home, "repo");
    const sessionId = "session_ambiguous_active_time";
    const sessionPath = join(
      home,
      "sessions",
      "2026",
      "06",
      "15",
      `rollout-2026-06-15T11-00-00-${sessionId}.jsonl`,
    );
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(
      sessionPath,
      [
        JSON.stringify({ timestamp: "2026-06-15T11:00:10Z", type: "event_msg", payload: { type: "task_started" } }),
        JSON.stringify({ timestamp: "2026-06-15T11:00:20Z", type: "event_msg", payload: { type: "task_started" } }),
        JSON.stringify({ timestamp: "2026-06-15T11:00:30Z", type: "event_msg", payload: { type: "task_complete" } }),
        "",
      ].join("\n"),
      "utf8",
    );

    for (const event of [
      fixtureEvent({
        event_id: "evt_ambiguous_start",
        timestamp: "2026-06-15T11:00:00Z",
        repo_path: repoPath,
        run_id: "run_ambiguous_active",
        session_id: sessionId,
        skill: "dreamers-full",
        metrics: { mode: "task-description" },
      }),
      fixtureEvent({
        event_id: "evt_ambiguous_validation",
        timestamp: "2026-06-15T11:01:00Z",
        event_type: "validation_attempt",
        repo_path: repoPath,
        run_id: "run_ambiguous_active",
        session_id: sessionId,
        skill: "dreamers-full",
        status: "completed",
        metrics: { command_kind: "test", command_label: "npm test", attempt_number: 1, result: "pass" },
      }),
      fixtureEvent({
        event_id: "evt_ambiguous_gate",
        timestamp: "2026-06-15T11:02:00Z",
        event_type: "gate_decided",
        repo_path: repoPath,
        run_id: "run_ambiguous_active",
        session_id: sessionId,
        skill: "dreamers-full",
        status: "decided",
        metrics: { gate_type: "implementation-start", decision: "approved_start_implementation" },
      }),
      fixtureEvent({
        event_id: "evt_ambiguous_token",
        timestamp: "2026-06-15T11:03:00Z",
        event_type: "token_usage_recorded",
        repo_path: repoPath,
        run_id: "run_ambiguous_active",
        session_id: sessionId,
        skill: "dreamers-full",
        source: "summary",
        status: "completed",
        metrics: { token_source: "unavailable", attribution_scope: "turn" },
      }),
      fixtureEvent({
        event_id: "evt_ambiguous_done",
        timestamp: "2026-06-15T11:05:00Z",
        event_type: "skill_completed",
        repo_path: repoPath,
        run_id: "run_ambiguous_active",
        session_id: sessionId,
        skill: "dreamers-full",
        status: "completed",
        metrics: { final_status: "completed" },
      }),
    ]) {
      await recordEvent(event, { client: "codex", home });
    }

    const report = await runReport("runs", { client: "codex", home, repo: "current", cwd: repoPath });
    const run = report.items[0] as any;
    expect(run.duration_seconds).toBe(300);
    expect(run.active_duration_seconds).toBeNull();
    expect(run.active_turn_count).toBe(0);
    expect(run.active_duration_quality).toBe("unavailable");
    expect(run.active_duration_source).toBeNull();
    expect(run.active_duration_unavailable_reason).toBeUndefined();
    expect(run.validation.attempt_count).toBe(1);
    expect(run.gates.decision_counts["implementation-start"]).toEqual({ approved_start_implementation: 1 });
    expect(run.tokens.unavailable.row_count).toBe(1);

    const group = report.groups[0] as any;
    expect(group.total_duration_seconds).toBe(300);
    expect(group.active_run_count).toBe(0);
    expect(group.total_active_duration_seconds).toBeNull();
    expect(group.average_active_duration_seconds).toBeNull();
  });

  it("marks active AI time unavailable for missing and unpairable session spans", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-active-time-unpairable-"));
    const repoPath = await makeTempRepo(home, "repo");
    const cases = [
      { slug: "missing", hour: 12, rows: null },
      {
        slug: "orphan_complete",
        hour: 13,
        rows: [
          JSON.stringify({ timestamp: "2026-06-15T13:00:10Z", type: "event_msg", payload: { type: "task_complete" } }),
        ],
      },
      {
        slug: "trailing_start",
        hour: 14,
        rows: [
          JSON.stringify({ timestamp: "2026-06-15T14:00:10Z", type: "event_msg", payload: { type: "task_started" } }),
        ],
      },
      {
        slug: "out_of_order",
        hour: 15,
        rows: [
          JSON.stringify({ timestamp: "2026-06-15T15:00:20Z", type: "event_msg", payload: { type: "task_started" } }),
          JSON.stringify({ timestamp: "2026-06-15T15:00:10Z", type: "event_msg", payload: { type: "task_complete" } }),
        ],
      },
      {
        slug: "crosses_start",
        hour: 16,
        rows: [
          JSON.stringify({ timestamp: "2026-06-15T15:59:50Z", type: "event_msg", payload: { type: "task_started" } }),
          JSON.stringify({ timestamp: "2026-06-15T16:00:30Z", type: "event_msg", payload: { type: "task_complete" } }),
        ],
      },
      {
        slug: "crosses_end",
        hour: 17,
        rows: [
          JSON.stringify({ timestamp: "2026-06-15T17:04:50Z", type: "event_msg", payload: { type: "task_started" } }),
          JSON.stringify({ timestamp: "2026-06-15T17:05:10Z", type: "event_msg", payload: { type: "task_complete" } }),
        ],
      },
    ];

    for (const entry of cases) {
      const hour = String(entry.hour).padStart(2, "0");
      const sessionId = `session_${entry.slug}`;
      const runId = `run_${entry.slug}`;
      if (entry.rows) {
        await writeCodexSessionRows(home, sessionId, `2026-06-15T${hour}-00-00`, entry.rows);
      }
      for (const event of [
        fixtureEvent({
          event_id: `evt_${entry.slug}_start`,
          timestamp: `2026-06-15T${hour}:00:00Z`,
          repo_path: repoPath,
          run_id: runId,
          session_id: sessionId,
          skill: "dreamers-full",
          metrics: { mode: "task-description" },
        }),
        fixtureEvent({
          event_id: `evt_${entry.slug}_validation`,
          timestamp: `2026-06-15T${hour}:01:00Z`,
          event_type: "validation_attempt",
          repo_path: repoPath,
          run_id: runId,
          session_id: sessionId,
          skill: "dreamers-full",
          status: "completed",
          metrics: { command_kind: "test", command_label: "npm test", attempt_number: 1, result: "pass" },
        }),
        fixtureEvent({
          event_id: `evt_${entry.slug}_gate`,
          timestamp: `2026-06-15T${hour}:02:00Z`,
          event_type: "gate_decided",
          repo_path: repoPath,
          run_id: runId,
          session_id: sessionId,
          skill: "dreamers-full",
          status: "decided",
          metrics: { gate_type: "implementation-start", decision: "approved_start_implementation" },
        }),
        fixtureEvent({
          event_id: `evt_${entry.slug}_token`,
          timestamp: `2026-06-15T${hour}:03:00Z`,
          event_type: "token_usage_recorded",
          repo_path: repoPath,
          run_id: runId,
          session_id: sessionId,
          skill: "dreamers-full",
          source: "summary",
          status: "completed",
          metrics: { token_source: "unavailable", attribution_scope: "turn" },
        }),
        fixtureEvent({
          event_id: `evt_${entry.slug}_done`,
          timestamp: `2026-06-15T${hour}:05:00Z`,
          event_type: "skill_completed",
          repo_path: repoPath,
          run_id: runId,
          session_id: sessionId,
          skill: "dreamers-full",
          status: "completed",
          metrics: { final_status: "completed" },
        }),
      ]) {
        await recordEvent(event, { client: "codex", home });
      }
    }

    const report = await runReport("runs", { client: "codex", home, repo: "current", cwd: repoPath });
    expect(report.run_count).toBe(cases.length);
    for (const entry of cases) {
      const run = report.items.find((item) => item.run_id === `run_${entry.slug}`) as any;
      expect(run.duration_seconds).toBe(300);
      expect(run.active_duration_seconds).toBeNull();
      expect(run.active_turn_count).toBe(0);
      expect(run.active_duration_quality).toBe("unavailable");
      expect(run.active_duration_source).toBeNull();
      expect(run.validation.attempt_count).toBe(1);
      expect(run.gates.decision_counts["implementation-start"]).toEqual({ approved_start_implementation: 1 });
      expect(run.tokens.unavailable.row_count).toBe(1);
    }
  });

  it("aggregates active AI time across mixed-availability runs in one group", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-active-time-group-"));
    const repoPath = await makeTempRepo(home, "repo");
    const runs = [
      { runId: "run_group_a", sessionId: "session_group_a", minute: "00", activeStart: "00:10", activeEnd: "01:10" },
      { runId: "run_group_b", sessionId: "session_group_b", minute: "10", activeStart: "10:30", activeEnd: "11:00" },
      { runId: "run_group_c", sessionId: "session_group_c", minute: "20", activeStart: null, activeEnd: null },
    ];

    for (const run of runs) {
      if (run.activeStart && run.activeEnd) {
        await writeCodexSessionRows(home, run.sessionId, `2026-06-15T10-${run.minute}-00`, [
          JSON.stringify({ timestamp: `2026-06-15T10:${run.activeStart}Z`, type: "event_msg", payload: { type: "task_started" } }),
          JSON.stringify({ timestamp: `2026-06-15T10:${run.activeEnd}Z`, type: "event_msg", payload: { type: "task_complete" } }),
        ]);
      }
      for (const event of [
        fixtureEvent({
          event_id: `evt_${run.runId}_start`,
          timestamp: `2026-06-15T10:${run.minute}:00Z`,
          repo_path: repoPath,
          run_id: run.runId,
          session_id: run.sessionId,
          skill: "dreamers-full",
          metrics: { mode: "task-description" },
        }),
        fixtureEvent({
          event_id: `evt_${run.runId}_done`,
          timestamp: `2026-06-15T10:${String(Number(run.minute) + 5).padStart(2, "0")}:00Z`,
          event_type: "skill_completed",
          repo_path: repoPath,
          run_id: run.runId,
          session_id: run.sessionId,
          skill: "dreamers-full",
          status: "completed",
          metrics: { final_status: "completed" },
        }),
      ]) {
        await recordEvent(event, { client: "codex", home });
      }
    }

    const report = await runReport("runs", { client: "codex", home, repo: "current", cwd: repoPath });
    const group = report.groups[0] as any;
    expect(group.run_count).toBe(3);
    expect(group.total_duration_seconds).toBe(900);
    expect(group.average_duration_seconds).toBe(300);
    expect(group.active_run_count).toBe(2);
    expect(group.total_active_duration_seconds).toBe(90);
    expect(group.average_active_duration_seconds).toBe(45);
  });

  it("keeps active AI time as a report-only derivation without new bookend events", async () => {
    const tracked = await execFile("git", ["ls-files"], { cwd: repoRoot });
    const files = tracked.stdout
      .split(/\r?\n/)
      .filter((file) => file && !file.startsWith(".dreamers/") && /\.(?:ts|js|mjs|py|sh|md|json)$/.test(file));
    const joined = (await Promise.all(files.map((file) => readFile(join(repoRoot, file), "utf8")))).join("\n");
    expect(joined).not.toMatch(/\bactive_(?:time|ai)_(?:started|completed|recorded)\b/);
    expect(joined).not.toMatch(/\bai_work_(?:started|completed|recorded)\b/);
    expect(joined).not.toMatch(/\bmodel_work_(?:started|completed|recorded)\b/);

    const eventSchema = await readFile(join(repoRoot, "src", "events.ts"), "utf8");
    expect(eventSchema).not.toMatch(/required_fields:[\s\S]{0,160}active_/);
  });

  it("resolves unavailable Codex token rows from session logs when report home uses tilde", async () => {
    const realHome = await mkdtemp(join(homedir(), ".dreamers-ts-codex-home-"));
    const tildeHome = `~/${basename(realHome)}`;
    try {
      const repoPath = await makeTempRepo(realHome, "repo");
      const sessionId = "sess_report_codex";
      const sessionPath = join(
        realHome,
        "sessions",
        "2026",
        "06",
        "13",
        `rollout-2026-06-13T10-05-00-${sessionId}.jsonl`,
      );
      await mkdir(dirname(sessionPath), { recursive: true });
      await writeFile(
        sessionPath,
        `${JSON.stringify({
          timestamp: "2026-06-13T10:04:59Z",
          type: "event_msg",
          payload: {
            type: "token_count",
            info: {
              model: "gpt-report",
              last_token_usage: {
                input_tokens: 11,
                output_tokens: 13,
                total_tokens: 24,
                cached_input_tokens: 5,
              },
            },
          },
        })}\n`,
        "utf8",
      );
      await recordEvent(
        fixtureEvent({
          event_id: "evt_report_codex_token",
          timestamp: "2026-06-13T10:05:00Z",
          event_type: "token_usage_recorded",
          repo_path: repoPath,
          source: "summary",
          status: "completed",
          session_id: sessionId,
          metrics: { token_source: "unavailable", attribution_scope: "turn" },
        }),
        { client: "codex", home: tildeHome },
      );

      const report = await runReport("tokens", { client: "codex", home: tildeHome, repo: "all" });

      expect(report.exact.row_count).toBe(1);
      expect(report.exact.totals.total_tokens).toBe(24);
      expect(report.exact.totals.cache_read_tokens).toBe(5);
      expect(report.exact.models["gpt-report"]?.total_tokens).toBe(24);
      expect(report.unavailable.row_count).toBe(0);
    } finally {
      await rm(realHome, { recursive: true, force: true });
    }
  });

  it("resolves unavailable Copilot token rows from session logs when report home uses tilde", async () => {
    const realHome = await mkdtemp(join(homedir(), ".dreamers-ts-copilot-home-"));
    const tildeHome = `~/${basename(realHome)}`;
    try {
      const repoPath = await makeTempRepo(realHome, "repo");
      const sessionId = "sess_report_copilot";
      const sessionPath = join(realHome, "session-state", sessionId, "events.jsonl");
      await mkdir(dirname(sessionPath), { recursive: true });
      await writeFile(
        sessionPath,
        `${JSON.stringify({
          timestamp: "2026-06-13T10:04:59Z",
          type: "session.shutdown",
          data: {
            modelMetrics: {
              "gpt-report": {
                usage: {
                  inputTokens: 17,
                  outputTokens: 19,
                  totalTokens: 36,
                  cacheReadTokens: 7,
                  cacheWriteTokens: 3,
                },
              },
            },
          },
        })}\n`,
        "utf8",
      );
      await recordEvent(
        fixtureEvent({
          event_id: "evt_report_copilot_token",
          timestamp: "2026-06-13T10:05:00Z",
          event_type: "token_usage_recorded",
          repo_path: repoPath,
          source: "summary",
          status: "completed",
          session_id: sessionId,
          metrics: { token_source: "unavailable", attribution_scope: "turn" },
        }),
        { client: "copilot", home: tildeHome },
      );

      const report = await runReport("tokens", { client: "copilot", home: tildeHome, repo: "all" });

      expect(report.exact.row_count).toBe(1);
      expect(report.exact.totals.total_tokens).toBe(36);
      expect(report.exact.totals.cache_read_tokens).toBe(7);
      expect(report.exact.totals.cache_write_tokens).toBe(3);
      expect(report.unavailable.row_count).toBe(0);
    } finally {
      await rm(realHome, { recursive: true, force: true });
    }
  });

  it("builds every report type with repo/date/skill filters and review artifact parity", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-report-"));
    const repoPath = await makeTempRepo(home, "repo");
    const otherRepoPath = await makeTempRepo(home, "other-repo");
    const reviewArtifactPath = join(repoPath, ".dreamers", "reviews", "sentinel-typescript-port-20260613.md");
    await mkdir(dirname(reviewArtifactPath), { recursive: true });
    await writeFile(
      reviewArtifactPath,
      [
        "Status: Complete",
        "",
        "Findings",
        "- [high] [correctness] Built entrypoint must be loadable.",
        "",
        "Open Questions",
        "- none",
        "",
      ].join("\n"),
      "utf8",
    );

    await recordFixtureEvent(home, {
      event_id: "evt_start",
      timestamp: "2026-06-13T10:00:00Z",
      repo_path: repoPath,
      metrics: { mode: "plan-path" },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_validation",
      timestamp: "2026-06-13T10:01:00Z",
      event_type: "validation_attempt",
      repo_path: repoPath,
      status: "completed",
      metrics: {
        command_kind: "test",
        command_label: "npm test",
        attempt_number: 1,
        result: "pass",
      },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_gate",
      timestamp: "2026-06-13T10:02:00Z",
      event_type: "gate_decided",
      repo_path: repoPath,
      status: "decided",
      metrics: {
        gate_type: "implementation-start",
        decision: "approved_start_atomic",
      },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_review",
      timestamp: "2026-06-13T10:03:00Z",
      event_type: "review_pass_completed",
      repo_path: repoPath,
      status: "completed",
      metrics: {
        lane: "sentinel",
        reviewers: ["sentinel"],
        artifact_paths: [reviewArtifactPath],
        blocked: false,
        open_question_count: 0,
        findings_by_severity: { high: 1 },
        findings_by_lens: { correctness: 1 },
      },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_token_exact",
      timestamp: "2026-06-13T10:04:00Z",
      event_type: "token_usage_recorded",
      repo_path: repoPath,
      source: "summary",
      status: "completed",
      session_id: "sess_01",
      metrics: {
        token_source: "exact",
        attribution_scope: "turn",
        input_tokens: 1,
        output_tokens: 2,
        total_tokens: 3,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
      },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_token_unavailable",
      timestamp: "2026-06-13T10:05:00Z",
      event_type: "token_usage_recorded",
      repo_path: repoPath,
      source: "summary",
      status: "completed",
      session_id: "sess_02",
      metrics: { token_source: "unavailable", attribution_scope: "turn" },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_complete",
      timestamp: "2026-06-13T10:06:00Z",
      event_type: "skill_completed",
      repo_path: repoPath,
      status: "completed",
      metrics: { final_status: "completed" },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_xss",
      timestamp: "2026-06-13T10:07:00Z",
      repo_path: repoPath,
      skill: "<script>alert(1)</script>",
      run_id: "run_xss",
      metrics: { mode: "plan-path" },
    });
    await recordFixtureEvent(home, {
      event_id: "evt_other_repo",
      timestamp: "2026-06-13T10:08:00Z",
      repo_path: otherRepoPath,
      run_id: "run_other",
      metrics: { mode: "plan-path" },
    });
    await appendMalformedStatsLine(home);

    const reportOptions = {
      client: "codex" as const,
      home,
      repo: "current" as const,
      cwd: join(repoPath, "subdir"),
    };
    const [runs, validation, gates, reviews, tokens, summary] = await Promise.all([
      runReport("runs", reportOptions),
      runReport("validation", reportOptions),
      runReport("gates", reportOptions),
      runReport("reviews", reportOptions),
      runReport("tokens", reportOptions),
      runReport("summarize", reportOptions),
    ]);

    expect([runs, validation, gates, reviews, tokens, summary].map((report) => report.report_type)).toEqual([
      "runs",
      "validation",
      "gates",
      "reviews",
      "tokens",
      "summarize",
    ]);
    expect(summary.warning_count).toBe(1);
    expect(summary.filters.current_repo).toBe(repoPath);
    expect(summary.runs.run_count).toBe(1);
    expect(summary.runs.incomplete_count).toBe(1);
    expect(summary.runs.groups.some((group: any) => group.skill === "dreamers-full")).toBe(true);
    expect(summary.runs.incomplete_items.some((item: any) => item.skill === "<script>alert(1)</script>")).toBe(true);
    expect(summary.validation.command_kinds.test?.final_pass_count).toBe(1);
    expect(summary.gates.decision_counts["implementation-start"]?.approved_start_atomic).toBe(1);
    expect(summary.reviews.findings_by_severity.high).toBe(1);
    expect(summary.reviews.findings_by_lens.correctness).toBe(1);
    expect(summary.reviews.artifact_summary.mismatch_count).toBe(0);
    expect(summary.tokens.exact.totals.total_tokens).toBe(3);
    expect(summary.tokens.unavailable.totals.total_tokens).toBeNull();

    const allRepos = await runReport("runs", { client: "codex", home, repo: "all" });
    expect(allRepos.run_count).toBe(1);
    expect(allRepos.incomplete_count).toBe(2);

    const skillFiltered = await runReport("runs", { ...reportOptions, skill: "dreamers-full" });
    expect(skillFiltered.groups).toHaveLength(1);
    expect(skillFiltered.groups[0]?.skill).toBe("dreamers-full");

    const dateFiltered = await runReport("validation", {
      ...reportOptions,
      since: "2026-06-13T10:01:00Z",
      until: "2026-06-13T10:01:30Z",
    });
    expect(dateFiltered.attempt_count).toBe(1);

    const html = renderDashboardHtml(summary, {
      client: "codex",
      generatedAt: "2026-06-13T10:09:00Z",
    });
    expect(html).toContain("Runs by skill");
    expect(html).toContain("Run details");
    expect(html).toContain("Validation");
    expect(html).toContain("Reviews");
    expect(html).toContain("Gates");
    expect(html).toContain("Tokens");
    expect(html).toContain("status-completed");
    expect(html).toContain("<td>unavailable</td>");
    expect(html).toContain("<td>n/a</td>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>alert(1)</script>");
  });

  it("reports individual run details, retries, gates, reviews, and attributed hook tokens", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-ts-run-details-"));
    const repoPath = await makeTempRepo(home, "repo");
    const sessionId = "session_run_detail_hook";
    const sessionPath = join(
      home,
      "sessions",
      "2026",
      "06",
      "15",
      `rollout-2026-06-15T10-04-00-${sessionId}.jsonl`,
    );
    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(
      sessionPath,
      `${JSON.stringify({
        timestamp: "2026-06-15T10:04:00Z",
        type: "event_msg",
        payload: {
          type: "token_count",
          info: {
            model: "gpt-5",
            last_token_usage: {
              input_tokens: 80,
              output_tokens: 20,
              total_tokens: 100,
            },
          },
        },
      })}\n`,
      "utf8",
    );

    for (const event of [
      fixtureEvent({
        event_id: "evt_run_detail_start",
        timestamp: "2026-06-15T10:00:00Z",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        metrics: { mode: "task-description" },
      }),
      fixtureEvent({
        event_id: "evt_run_detail_validation_fail",
        timestamp: "2026-06-15T10:01:00Z",
        event_type: "validation_attempt",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        status: "completed",
        metrics: {
          command_kind: "test",
          command_label: "npm test",
          attempt_number: 1,
          result: "fail",
          failure_category: "test-failure",
        },
      }),
      fixtureEvent({
        event_id: "evt_run_detail_validation_pass",
        timestamp: "2026-06-15T10:02:00Z",
        event_type: "validation_attempt",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        status: "completed",
        metrics: {
          command_kind: "test",
          command_label: "npm test",
          attempt_number: 2,
          result: "pass",
        },
      }),
      fixtureEvent({
        event_id: "evt_run_detail_gate",
        timestamp: "2026-06-15T10:03:00Z",
        event_type: "gate_decided",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        status: "decided",
        metrics: { gate_type: "plan-approval", decision: "approved" },
      }),
      fixtureEvent({
        event_id: "evt_run_detail_review",
        timestamp: "2026-06-15T10:04:00Z",
        event_type: "review_pass_completed",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        status: "completed",
        metrics: {
          review_pass_id: "review_run_detail_01",
          lane: "full",
          reviewers: ["sentinel"],
          artifact_paths: [],
          findings_by_severity: { critical: 0, high: 1, medium: 0, low: 0 },
          findings_by_lens: { correctness: 1, security: 0, maintainability: 0, "test-coverage": 0, simplicity: 0 },
          blocked: false,
          open_question_count: 1,
        },
      }),
      fixtureEvent({
        event_id: "evt_run_detail_done",
        timestamp: "2026-06-15T10:05:00Z",
        event_type: "skill_completed",
        repo_path: repoPath,
        run_id: "run_detail_01",
        session_id: sessionId,
        skill: "dreamers-lite",
        status: "completed",
        metrics: { final_status: "completed" },
      }),
      fixtureEvent({
        event_id: "evt_missing_terminal_start",
        timestamp: "2026-06-15T11:00:00Z",
        repo_path: repoPath,
        run_id: "run_missing_terminal",
        skill: "dreamers-lite",
        metrics: { mode: "task-description" },
      }),
    ]) {
      await recordEvent(event, { client: "codex", home });
    }
    const tokenEvents = await buildHookEvents(
      "Stop",
      {
        cwd: repoPath,
        timestamp: "2026-06-15T10:05:00Z",
        session_id: sessionId,
        stop_hook_active: false,
      },
      { client: "codex", home },
    );
    await recordEvent(tokenEvents[1]!, { client: "codex", home });

    const report = await runReport("runs", {
      client: "codex",
      home,
      repo: "current",
      cwd: repoPath,
    });

    expect(report.run_count).toBe(1);
    expect(report.incomplete_count).toBe(1);
    const incomplete = report.incomplete_items[0];
    expect(incomplete).toBeDefined();
    expect(incomplete!.reason).toBe("missing_terminal");
    const run = report.items[0];
    expect(run).toBeDefined();
    if (!run) {
      throw new Error("expected run_detail_01 in report items");
    }
    expect(run.run_id).toBe("run_detail_01");
    expect(run.status).toBe("completed");
    expect(run.duration_seconds).toBe(300);
    expect(run.validation.attempt_count).toBe(2);
    expect(run.validation.command_kinds.test?.retry_count).toBe(1);
    expect(run.gates.decision_counts["plan-approval"]).toEqual({ approved: 1 });
    expect(run.reviews.review_count).toBe(1);
    expect(run.reviews.findings_by_severity.high).toBe(1);
    expect(run.tokens.exact.totals.total_tokens).toBe(100);
  });
});
