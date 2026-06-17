import { execFile as execFileCallback, spawn } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

const execFile = promisify(execFileCallback);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const cliPath = join(repoRoot, "dist", "cli.js");
const mcpServerPath = join(repoRoot, "dist", "mcp-server.js");
const posixIt = process.platform === "win32" ? it.skip : it;

async function nodeCli(args: string[], options: { input?: string; cwd?: string; env?: NodeJS.ProcessEnv } = {}) {
  return runProcess(process.execPath, [cliPath, ...args], options);
}

async function runProcess(command: string, args: string[], options: { input?: string; cwd?: string; env?: NodeJS.ProcessEnv } = {}) {
  return new Promise<{ stdout: string; stderr: string }>((resolveProcess, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? repoRoot,
      env: options.env ? { ...process.env, ...options.env } : process.env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolveProcess({ stdout, stderr });
        return;
      }
      reject(Object.assign(new Error(`Command failed: ${command} ${args.join(" ")}`), { code, stdout, stderr }));
    });
    child.stdin.end(options.input ?? "");
  });
}

async function runFirstAvailable(
  candidates: Array<[string, string[]]>,
  args: string[],
  options: { input?: string; cwd?: string; env?: NodeJS.ProcessEnv } = {},
) {
  for (const [command, prefixArgs] of candidates) {
    try {
      return await runProcess(command, [...prefixArgs, ...args], options);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        continue;
      }
      throw error;
    }
  }
  throw new Error(`none of these commands were available: ${candidates.map(([command]) => command).join(", ")}`);
}

function pythonCandidates(): Array<[string, string[]]> {
  return process.platform === "win32"
    ? [
        ["py", ["-3"]],
        ["python", []],
        ["python3", []],
      ]
    : [
        ["python3", []],
        ["python", []],
      ];
}

async function readJsonl(path: string): Promise<any[]> {
  const text = await readFile(path, "utf8");
  return text.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

const baseEvent = {
  schema_version: 1,
  event_id: "evt_cli_start",
  timestamp: "2026-06-15T00:00:00Z",
  event_type: "skill_started",
  repo_path: "/tmp/example-cli",
  source: "skill",
  status: "started",
  skill: "dreamers-full",
  run_id: "run_cli",
  metrics: { mode: "plan-path" },
};

describe("Node CLI entrypoint", () => {
  beforeAll(async () => {
    await rm(join(repoRoot, "dist"), { recursive: true, force: true });
    await execFile("npm", ["run", "build"], { cwd: repoRoot });
  });

  it("publishes executable dist entrypoints through package bin metadata", async () => {
    const packageJson = JSON.parse(await readFile(join(repoRoot, "package.json"), "utf8"));
    expect(packageJson.bin).toMatchObject({
      "dreamers-stats": "./dist/cli.js",
      "dreamers-stats-mcp": "./dist/mcp-server.js",
    });
    await access(cliPath);
    await access(mcpServerPath);
  });

  it("keeps package validation scripts aligned with the Node-first README gate", async () => {
    const packageJson = JSON.parse(await readFile(join(repoRoot, "package.json"), "utf8"));
    expect(packageJson.scripts).toMatchObject({
      typecheck: "tsc --noEmit -p tsconfig.json",
      test: "vitest run --no-file-parallelism",
      build: "tsc -p tsconfig.build.json",
      "test:compat": "node tests-ts/run-compat-tests.mjs",
      validate: "npm run typecheck && npm test && npm run build && npm run test:compat",
    });

    const readme = await readFile(join(repoRoot, "README.md"), "utf8");
    expect(readme).toContain("npm run validate");
    expect(readme).toContain(["npm run typecheck", "npm test", "npm run build", "npm run test:compat"].join("\n"));
    expect(readme).toContain("node tests-ts/run-compat-tests.mjs");
    expect(readme).toContain("PowerShell:");
  });

  it("preserves record, doctor, checkpoint, hook, report, and dashboard command behavior", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-cli-"));
    const repo = join(home, "repo");
    await mkdir(join(repo, ".git"), { recursive: true });

    const record = await nodeCli([
      "record",
      "--client",
      "codex",
      "--home",
      home,
      "--event-json",
      JSON.stringify({ ...baseEvent, repo_path: repo }),
      "--print-event-id",
    ]);
    expect(record.stderr).toBe("");
    expect(record.stdout.trim()).toBe("evt_cli_start");

    await nodeCli([
      "record",
      "--client",
      "codex",
      "--home",
      home,
      "--event-json",
      JSON.stringify({
        ...baseEvent,
        event_id: "evt_cli_complete",
        timestamp: "2026-06-15T00:05:00Z",
        event_type: "skill_completed",
        repo_path: repo,
        status: "completed",
        metrics: { final_status: "completed" },
      }),
    ]);

    const checkpoint = await nodeCli([
      "checkpoint",
      "--client",
      "codex",
      "--home",
      home,
      "--event-type",
      "validation_attempt",
      "--skill",
      "dreamers-full",
      "--run-id",
      "run_cli",
      "--repo-path",
      repo,
      "--timestamp",
      "2026-06-15T00:01:00Z",
      "--metrics-json",
      JSON.stringify({
        command_kind: "test",
        command_label: "npm test",
        attempt_number: 1,
        result: "pass",
      }),
      "--print-event-id",
    ]);
    expect(checkpoint.stdout.trim()).toMatch(/^skill_validation_attempt_/);

    await nodeCli([
      "checkpoint",
      "--client",
      "codex",
      "--home",
      home,
      "--event-type",
      "gate_decided",
      "--skill",
      "dreamers-full",
      "--run-id",
      "run_cli",
      "--repo-path",
      repo,
      "--timestamp",
      "2026-06-15T00:02:00Z",
      "--metrics-json",
      JSON.stringify({ gate_type: "plan-approval", decision: "approved" }),
    ]);
    for (const event of [
      {
        ...baseEvent,
        event_id: "evt_cli_cycle",
        timestamp: "2026-06-15T00:06:00Z",
        event_type: "cycle_completed",
        repo_path: repo,
        metrics: { plan_path: "plan.md", cycle_status: "completed" },
      },
      {
        ...baseEvent,
        event_id: "evt_cli_pr",
        timestamp: "2026-06-15T00:07:00Z",
        event_type: "pr_created",
        repo_path: repo,
        metrics: { pr_number: 12 },
      },
      {
        ...baseEvent,
        event_id: "evt_cli_retro",
        timestamp: "2026-06-15T00:08:00Z",
        event_type: "retro_written",
        repo_path: repo,
        metrics: { retro_path: ".dreamers/retro.md" },
      },
    ]) {
      await nodeCli([
        "record",
        "--client",
        "codex",
        "--home",
        home,
        "--event-json",
        JSON.stringify(event),
      ]);
    }

    const hook = await nodeCli(
      [
        "hook",
        "--client",
        "codex",
        "--home",
        home,
        "--event-name",
        "UserPromptSubmit",
      ],
      {
        input: JSON.stringify({
          cwd: repo,
          timestamp: 1_718_302_420_000,
          turn_id: "turn_cli",
          prompt: "secret prompt text",
        }),
      },
    );
    expect(hook.stderr).toBe("");
    expect(hook.stdout).toBe("");

    const events = await readJsonl(join(home, "dreamers", "stats", "events.jsonl"));
    expect(events.map((event) => event.event_type)).toContain("prompt_submitted");

    const doctor = await nodeCli(["doctor", "--client", "codex", "--home", home, "--json"]);
    const doctorPayload = JSON.parse(doctor.stdout);
    expect(doctor.stdout.startsWith('{"error":')).toBe(true);
    expect(doctorPayload.writable).toBe(true);
    expect(doctorPayload.event_count).toBe(8);

    for (const command of ["summarize", "runs", "reviews", "validation", "gates", "tokens"]) {
      const report = await nodeCli([command, "--client", "codex", "--home", home, "--repo", "all", "--json"]);
      expect(JSON.parse(report.stdout).report_type).toBe(command);
    }
    const copilotSummary = await nodeCli(["summarize", "--client", "copilot", "--home", home, "--repo", "all", "--json"]);
    expect(JSON.parse(copilotSummary.stdout).workflow_outputs).toMatchObject({
      cycle_status_counts: { completed: 1 },
      pr_count: 1,
      retro_count: 1,
    });
    const copilotSummaryText = await nodeCli(["summarize", "--client", "copilot", "--home", home, "--repo", "all"]);
    expect(copilotSummaryText.stdout).toContain("Workflow outputs");
    expect(copilotSummaryText.stdout).toContain("prs=1 retros=1");

    const dashboard = await nodeCli([
      "dashboard",
      "--client",
      "codex",
      "--home",
      home,
      "--repo",
      "all",
      "--generated-at",
      "2026-06-15T00:10:00Z",
    ]);
    expect(dashboard.stdout).toContain("<html");
    expect(dashboard.stdout).toContain("Generated: Jun 15, 2026 00:10 UTC");

    const outputPath = join(home, "dashboard.html");
    await nodeCli(["dashboard", "--client", "codex", "--home", home, "--repo", "all", "--output", outputPath]);
    expect(await readFile(outputPath, "utf8")).toContain("<html");
  });

  it("returns Python-compatible validation categories and exit codes", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-cli-invalid-"));
    try {
      await nodeCli([
        "record",
        "--client",
        "codex",
        "--home",
        home,
        "--event-json",
        "{bad",
      ]);
      throw new Error("expected CLI failure");
    } catch (error) {
      const failed = error as { code?: number; stdout?: string; stderr?: string };
      expect(failed.code).toBe(2);
      expect(failed.stdout).toBe("");
      expect(failed.stderr?.trim()).toBe("invalid_json");
    }
  });

  it("preserves CLI alias, argument, and write-error parity", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-cli-parity-"));
    const aliasDoctor = await nodeCli(["doctor", "--codex-home", home, "--json"]);
    expect(JSON.parse(aliasDoctor.stdout).events_file).toBe(join(home, "dreamers", "stats", "events.jsonl"));

    const cases = [
      {
        args: ["doctor", "--client", "codex", "--copilot-home", home],
        code: 2,
        stderr: "conflicting_client",
      },
      {
        args: ["doctor", "--home", join(home, "one"), "--codex-home", join(home, "two")],
        code: 2,
        stderr: "conflicting_home",
      },
      {
        args: ["doctor", "--home"],
        code: 2,
        stderr: "missing_argument",
      },
      {
        args: ["unknown-command"],
        code: 2,
        stderr: "unknown_command",
      },
      {
        args: ["runs", "--client", "codex", "--home", home, "--repo", "invalid"],
        code: 2,
        stderr: "invalid_report_filter",
      },
    ];

    for (const entry of cases) {
      try {
        await nodeCli(entry.args);
        throw new Error(`expected failure for ${entry.args.join(" ")}`);
      } catch (error) {
        const failed = error as { code?: number; stdout?: string; stderr?: string };
        expect(failed.code).toBe(entry.code);
        expect(failed.stdout).toBe("");
        expect(failed.stderr?.trim()).toBe(entry.stderr);
      }
    }

    const outputDirectory = join(home, "existing-directory");
    await mkdir(outputDirectory);
    try {
      await nodeCli(["dashboard", "--client", "codex", "--home", home, "--repo", "all", "--output", outputDirectory]);
      throw new Error("expected dashboard write failure");
    } catch (error) {
      const failed = error as { code?: number; stdout?: string; stderr?: string };
      expect(failed.code).toBe(1);
      expect(failed.stdout).toBe("");
      expect(failed.stderr?.trim()).toBe("write_failed");
    }
  });

  it("infers client from env and hook payload while rejecting ambiguous homes", async () => {
    const copilotHome = await mkdtemp(join(tmpdir(), "dreamers-cli-copilot-env-"));
    const codexHome = await mkdtemp(join(tmpdir(), "dreamers-cli-codex-payload-"));
    const envDoctor = await nodeCli(["doctor", "--json"], {
      env: {
        DREAMERS_STATS_CLIENT: "copilot",
        COPILOT_HOME: copilotHome,
        CODEX_HOME: "",
      },
    });
    expect(JSON.parse(envDoctor.stdout).events_file).toBe(join(copilotHome, "dreamers", "stats", "events.jsonl"));

    await nodeCli(
      ["hook", "--home", codexHome, "--event-name", "UserPromptSubmit"],
      {
        env: {
          DREAMERS_STATS_CLIENT: "",
          DREAMERS_CLIENT: "",
          COPILOT_HOME: "",
          CODEX_HOME: "",
        },
        input: JSON.stringify({
          client: "codex",
          cwd: "/tmp/example-cli",
          timestamp: "2026-06-15T00:03:00Z",
          turn_id: "turn_payload_client",
          prompt: "/test",
        }),
      },
    );
    const events = await readJsonl(join(codexHome, "dreamers", "stats", "events.jsonl"));
    expect(events[0].event_type).toBe("prompt_submitted");

    try {
      await nodeCli(["doctor", "--json"], {
        env: {
          DREAMERS_STATS_CLIENT: "",
          DREAMERS_CLIENT: "",
          COPILOT_HOME: copilotHome,
          CODEX_HOME: codexHome,
        },
      });
      throw new Error("expected ambiguous client failure");
    } catch (error) {
      const failed = error as { code?: number; stdout?: string; stderr?: string };
      expect(failed.code).toBe(2);
      expect(failed.stdout).toBe("");
      expect(failed.stderr?.trim()).toBe("ambiguous_client");
    }
  });

  posixIt("executes the documented clean-home Codex install, CLI, MCP, and remove flow", async () => {
    const smokeRoot = await mkdtemp(join(tmpdir(), "dreamers-readme-smoke-"));
    const codexHome = join(smokeRoot, "codex-home");
    const statsPath = join(codexHome, "dreamers", "stats", "events.jsonl");
    await mkdir(dirname(statsPath), { recursive: true });
    await writeFile(statsPath, '{"event_id":"historic"}\n', "utf8");

    await runProcess("bash", [
      join(repoRoot, "Install-DreamersMcpCodex.sh"),
      "--codex-home",
      codexHome,
      "--dreamers-mcp-path",
      repoRoot,
    ]);
    await rm(join(codexHome, "dreamers", "runtime", "dreamers_stats"), { recursive: true, force: true });

    await runProcess(
      "bash",
      [join(codexHome, "dreamers", "scripts", "dreamers_hook.sh"), "UserPromptSubmit"],
      {
        env: { CODEX_HOME: codexHome },
        input: JSON.stringify({
          cwd: repoRoot,
          timestamp: "2026-06-17T00:00:00Z",
          turn_id: "turn_readme_smoke",
          prompt: "secret smoke prompt",
        }),
      },
    );

    const summary = await nodeCli(["summarize", "--client", "codex", "--home", codexHome, "--repo", "all", "--json"]);
    expect(JSON.parse(summary.stdout).report_type).toBe("summarize");

    const mcp = await runFirstAvailable(
      pythonCandidates(),
      [join(codexHome, "dreamers", "scripts", "dreamers_mcp_server.py")],
      {
        env: { CODEX_HOME: codexHome },
        input: [
          JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
          JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }),
          "",
        ].join("\n"),
      },
    );
    const responses = mcp.stdout.trim().split("\n").map((line) => JSON.parse(line));
    expect(responses[1].result.tools.some((tool: any) => tool.name === "summarize")).toBe(true);

    const rawEvents = await readFile(statsPath, "utf8");
    expect(rawEvents).toContain('"event_id":"historic"');
    expect(rawEvents).not.toContain("secret smoke prompt");

    await runProcess("bash", [join(repoRoot, "Remove-DreamersMcpCodex.sh"), "--codex-home", codexHome]);
    expect(await readFile(statsPath, "utf8")).toContain('"event_id":"historic"');
    await expect(access(join(codexHome, "dreamers", "runtime", "dreamers_mcp_node"))).rejects.toThrow();
  });
});

describe("Node MCP server entrypoint", () => {
  beforeAll(async () => {
    await execFile("npm", ["run", "build"], { cwd: repoRoot });
  });

  it("serves JSON-RPC initialize, ping, tools/list, tools/call, and tool errors over stdio", async () => {
    const home = await mkdtemp(join(tmpdir(), "dreamers-mcp-"));
    const messages = [
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      { jsonrpc: "2.0", id: 2, method: "ping", params: {} },
      { jsonrpc: "2.0", id: 3, method: "tools/list", params: {} },
      {
        jsonrpc: "2.0",
        id: 4,
        method: "tools/call",
        params: {
          name: "record_event",
          arguments: {
            client: "codex",
            home,
            event: { ...baseEvent, event_id: "evt_mcp_start" },
          },
        },
      },
      {
        jsonrpc: "2.0",
        id: 5,
        method: "tools/call",
        params: {
          name: "record_checkpoint",
          arguments: {
            client: "codex",
            home,
            event_type: "validation_attempt",
            skill: "dreamers-full",
            run_id: "run_mcp",
            timestamp: "2026-06-15T00:01:00Z",
            metrics: {
              command_kind: "test",
              command_label: "npm test",
              attempt_number: 1,
              result: "pass",
            },
          },
        },
      },
      {
        jsonrpc: "2.0",
        id: 6,
        method: "tools/call",
        params: {
          name: "record_hook",
          arguments: {
            client: "codex",
            home,
            event_name: "UserPromptSubmit",
            payload: {
              cwd: "/tmp/example-cli",
              timestamp: "2026-06-15T00:02:00Z",
              turn_id: "turn_mcp",
              prompt: "/test",
            },
          },
        },
      },
      {
        jsonrpc: "2.0",
        id: 7,
        method: "tools/call",
        params: {
          name: "runs",
          arguments: { client: "codex", home, repo: "all", output: "json" },
        },
      },
      {
        jsonrpc: "2.0",
        id: 8,
        method: "tools/call",
        params: {
          name: "not_a_tool",
          arguments: { client: "codex", home },
        },
      },
    ];

    const server = await runProcess(process.execPath, [mcpServerPath], {
      input: `${messages.map((message) => JSON.stringify(message)).join("\n")}\n`,
    });
    const responses = server.stdout.trim().split("\n").map((line) => JSON.parse(line));

    expect(responses[0].result.protocolVersion).toBe("2025-11-25");
    expect(responses[0].result.serverInfo).toMatchObject({ name: "dreamers-mcp", version: "0.1.0" });
    expect(responses[1].result).toEqual({});
    expect(responses[2].result.resultType).toBe("complete");
    const tools = responses[2].result.tools;
    expect(tools.map((tool: any) => tool.name)).toEqual([
      "doctor",
      "summarize",
      "runs",
      "reviews",
      "validation",
      "gates",
      "tokens",
      "record_event",
      "record_checkpoint",
      "record_hook",
    ]);
    const recordCheckpointTool = tools.find((tool: any) => tool.name === "record_checkpoint");
    expect(recordCheckpointTool.inputSchema.required).toEqual(["client", "home", "event_type", "skill", "run_id"]);
    expect(recordCheckpointTool.inputSchema.properties.metrics).toEqual({ type: "object" });
    const reportTool = tools.find((tool: any) => tool.name === "tokens");
    expect(reportTool.inputSchema.properties.output).toEqual({ type: "string", enum: ["json"] });
    expect(responses[3].result).toMatchObject({
      resultType: "complete",
      structuredContent: { event_id: "evt_mcp_start" },
      isError: false,
    });
    expect(responses[4].result.structuredContent.event_id).toMatch(/^skill_validation_attempt_/);
    expect(responses[5].result.structuredContent.event_ids).toHaveLength(1);
    expect(responses[6].result.structuredContent.report_type).toBe("runs");
    expect(responses[6].result.content[0].type).toBe("text");
    expect(JSON.parse(responses[6].result.content[0].text).report_type).toBe("runs");
    expect(responses[7].result).toMatchObject({ resultType: "complete", isError: true });
    expect(responses[7].result.content[0].text).toContain("tool is not supported");
  });
});
