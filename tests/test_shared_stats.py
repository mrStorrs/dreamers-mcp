import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from dreamers_stats import runtime
from dreamers_stats.cli import main as cli_main


REPO_ROOT = Path(__file__).resolve().parents[1]


def valid_event(**overrides):
    event = {
        "schema_version": 1,
        "event_id": "evt_01",
        "timestamp": "2026-06-13T10:00:00-07:00",
        "event_type": "skill_started",
        "repo_path": "/tmp/example",
        "source": "skill",
        "status": "started",
        "metrics": {"mode": "plan-path"},
    }
    event.update(overrides)
    return event


class SharedStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copilot_home = Path(self.tmp.name) / "copilot-home"
        self.codex_home = Path(self.tmp.name) / "codex-home"
        self.copilot_events = self.copilot_home / "dreamers" / "stats" / "events.jsonl"
        self.codex_events = self.codex_home / "dreamers" / "stats" / "events.jsonl"
        self.fixture_repo = Path(self.tmp.name) / "fixture-repo"
        (self.fixture_repo / ".git").mkdir(parents=True)
        (self.fixture_repo / ".dreamers" / "reviews").mkdir(parents=True)
        self.other_repo = Path(self.tmp.name) / "other-repo"
        (self.other_repo / ".git").mkdir(parents=True)

    def invoke(self, argv, stdin_text="", cwd=None, env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO(stdin_text)
        base_env = os.environ.copy()
        if env:
            base_env.update(env)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            original = os.environ.copy()
            os.environ.clear()
            os.environ.update(base_env)
            try:
                if cwd is None:
                    code = cli_main(argv, stdin=stdin)
                else:
                    with contextlib.chdir(cwd):
                        code = cli_main(argv, stdin=stdin)
            finally:
                os.environ.clear()
                os.environ.update(original)
        return code, stdout.getvalue(), stderr.getvalue()

    def invoke_hook(self, event_name, payload, *extra_args, env=None):
        return self.invoke(
            ["hook", *extra_args, "--event-name", event_name],
            stdin_text=json.dumps(payload),
            env=env,
        )

    def invoke_checkpoint(
        self,
        event_type,
        *,
        client="copilot",
        home=None,
        skill="dreamers-full",
        run_id="run_01",
        status=None,
        metrics=None,
        extra_args=None,
    ):
        argv = [
            "checkpoint",
            "--client",
            client,
            "--home",
            str(home or self.copilot_home),
            "--event-type",
            event_type,
            "--skill",
            skill,
            "--run-id",
            run_id,
            "--metrics-json",
            json.dumps(metrics or {}),
        ]
        if status is not None:
            argv.extend(["--status", status])
        if extra_args:
            argv.extend(extra_args)
        return self.invoke(argv)

    def read_events(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def fixture_event(
        self,
        event_type,
        *,
        event_id,
        timestamp,
        metrics,
        source="skill",
        status=None,
        repo="current",
        run_id=None,
        session_id=None,
        skill=None,
        branch="feat/dreamers-mcp",
    ):
        repo_path = self.fixture_repo if repo == "current" else self.other_repo
        return {
            "schema_version": 1,
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "repo_path": str(repo_path),
            "repo_name": repo_path.name,
            "branch": branch,
            "run_id": run_id,
            "session_id": session_id,
            "skill": skill,
            "source": source,
            "status": status if status is not None else runtime.default_status_for_event(event_type),
            "metrics": metrics,
        }

    def record_fixture_event(self, event, *, client="copilot", home=None):
        runtime.record_event(event, client=client, home=home or self.copilot_home)

    def write_fixture_lines(self, lines, *, home=None):
        events_path = (home or self.copilot_home) / "dreamers" / "stats" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")

    def write_codex_session_lines(self, session_id, lines, *, home=None):
        session_path = (
            (home or self.codex_home)
            / "sessions"
            / "2026"
            / "06"
            / "15"
            / f"rollout-2026-06-15T00-00-00-{session_id}.jsonl"
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with session_path.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(json.dumps(line))
                handle.write("\n")
        return session_path

    def write_copilot_session_lines(self, session_id, lines, *, home=None):
        session_path = (home or self.copilot_home) / "session-state" / session_id / "events.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with session_path.open("w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(json.dumps(line))
                handle.write("\n")
        return session_path

    def write_review_artifact(self, repo_path, name, body):
        artifact_path = repo_path / ".dreamers" / "reviews" / name
        artifact_path.write_text(body, encoding="utf-8")
        return artifact_path

    def test_record_cli_routes_only_to_requested_client_home(self):
        payload = valid_event(event_id="evt_route_01")

        code, stdout, stderr = self.invoke(
            ["record", "--client", "copilot", "--home", str(self.copilot_home)],
            stdin_text=json.dumps(payload),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        self.assertTrue(self.copilot_events.exists())
        self.assertFalse(self.codex_events.exists())

        payload["event_id"] = "evt_route_02"
        code, stdout, stderr = self.invoke(
            ["record", "--client", "codex", "--home", str(self.codex_home)],
            stdin_text=json.dumps(payload),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        self.assertEqual(["evt_route_01"], [event["event_id"] for event in self.read_events(self.copilot_events)])
        self.assertEqual(["evt_route_02"], [event["event_id"] for event in self.read_events(self.codex_events)])

    def test_client_inference_uses_env_or_payload_and_rejects_ambiguity(self):
        code, stdout, stderr = self.invoke(
            ["record", "--home", str(self.codex_home)],
            stdin_text=json.dumps(valid_event(event_id="evt_infer_payload", source="hook", event_type="session_started", status="started", metrics={"client": "codex"})),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        self.assertTrue(self.codex_events.exists())

        env = {
            "DREAMERS_STATS_CLIENT": "copilot",
            "COPILOT_HOME": str(self.copilot_home),
        }
        code, stdout, stderr = self.invoke(
            ["doctor", "--json"],
            env=env,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertEqual(str(self.copilot_events), json.loads(stdout)["events_file"])

        ambiguous_env = {
            "COPILOT_HOME": str(self.copilot_home),
            "CODEX_HOME": str(self.codex_home),
        }
        code, stdout, stderr = self.invoke(
            ["doctor", "--json"],
            env=ambiguous_env,
        )

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("ambiguous_client", stderr)

    def test_hook_redacts_sensitive_and_prohibited_content(self):
        code, stdout, stderr = self.invoke_hook(
            "postToolUse",
            {
                "sessionId": "sess_01",
                "timestamp": 1_718_302_420_000,
                "cwd": "/tmp/example",
                "client": "copilot",
                "toolName": "functions.exec_command",
                "toolArgs": {"cmd": "cat secret.txt"},
                "toolResult": {
                    "resultType": "success",
                    "textResultForLlm": "do not store this tool output",
                },
                "prompt": "do not store prompt text",
                "authorization": "Bearer abc1234567890",
            },
            "--home",
            str(self.copilot_home),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)

        stored = self.read_events(self.copilot_events)[0]
        self.assertEqual("tool_completed", stored["event_type"])
        self.assertEqual("functions.exec_command", stored["metrics"]["tool_name"])
        raw_line = self.copilot_events.read_text(encoding="utf-8")
        self.assertNotIn("secret.txt", raw_line)
        self.assertNotIn("do not store", raw_line)
        self.assertNotIn("Bearer abc1234567890", raw_line)

    def test_copilot_session_end_records_exact_tokens_from_session_log(self):
        self.write_copilot_session_lines(
            "session_exact",
            [
                {
                    "timestamp": "2026-06-15T00:00:01Z",
                    "type": "assistant.message",
                    "data": {"message": "secret assistant text", "outputTokens": 50},
                },
                {
                    "timestamp": "2026-06-15T00:00:02Z",
                    "type": "session.shutdown",
                    "data": {
                        "modelMetrics": {
                            "gpt-5.4": {
                                "usage": {
                                    "inputTokens": 300,
                                    "outputTokens": 40,
                                    "cacheReadTokens": 250,
                                    "cacheWriteTokens": 0,
                                }
                            }
                        }
                    },
                },
            ],
        )

        code, stdout, stderr = self.invoke_hook(
            "sessionEnd",
            {
                "cwd": str(self.fixture_repo),
                "timestamp": "2026-06-15T00:00:03Z",
                "sessionId": "session_exact",
                "reason": "shutdown",
                "transcript": "do not store transcript",
            },
            "--client",
            "copilot",
            "--home",
            str(self.copilot_home),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        stored = self.read_events(self.copilot_events)
        token_events = [event for event in stored if event["event_type"] == "token_usage_recorded"]
        self.assertEqual(1, len(token_events))
        token_metrics = token_events[0]["metrics"]
        self.assertEqual("exact", token_metrics["token_source"])
        self.assertEqual("session", token_metrics["attribution_scope"])
        self.assertEqual("gpt-5.4", token_metrics["model"])
        self.assertEqual(300, token_metrics["input_tokens"])
        self.assertEqual(40, token_metrics["output_tokens"])
        self.assertEqual(340, token_metrics["total_tokens"])
        self.assertEqual(250, token_metrics["cache_read_tokens"])
        self.assertEqual(0, token_metrics["cache_write_tokens"])

        code, stdout, stderr = self.invoke(
            ["tokens", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(1, report["exact"]["row_count"])
        self.assertEqual(0, report["unavailable"]["row_count"])
        self.assertEqual(340, report["exact"]["totals"]["total_tokens"])
        self.assertEqual(250, report["exact"]["totals"]["cache_read_tokens"])
        self.assertEqual(340, report["exact"]["models"]["gpt-5.4"]["total_tokens"])

        raw_line = self.copilot_events.read_text(encoding="utf-8")
        self.assertNotIn("secret assistant text", raw_line)
        self.assertNotIn("do not store transcript", raw_line)

    def test_copilot_reports_resolve_unavailable_tokens_from_session_log(self):
        self.write_copilot_session_lines(
            "session_report",
            [
                {
                    "timestamp": "2026-06-15T00:00:04Z",
                    "type": "session.shutdown",
                    "data": {
                        "modelMetrics": {
                            "claude-opus-4.6": {
                                "usage": {
                                    "inputTokens": 80,
                                    "outputTokens": 20,
                                    "cacheReadTokens": 40,
                                    "cacheWriteTokens": 0,
                                }
                            }
                        }
                    },
                },
                {
                    "timestamp": "2026-06-15T00:00:10Z",
                    "type": "session.shutdown",
                    "data": {
                        "modelMetrics": {
                            "claude-opus-4.6": {
                                "usage": {
                                    "inputTokens": 900,
                                    "outputTokens": 99,
                                    "cacheReadTokens": 800,
                                    "cacheWriteTokens": 0,
                                }
                            }
                        }
                    },
                },
            ],
        )
        self.record_fixture_event(
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_report_copilot_unavailable_tokens",
                timestamp="2026-06-15T00:00:05Z",
                session_id="session_report",
                source="summary",
                metrics={"token_source": "unavailable", "attribution_scope": "session"},
            ),
            client="copilot",
            home=self.copilot_home,
        )

        code, stdout, stderr = self.invoke(
            ["tokens", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(1, report["exact"]["row_count"])
        self.assertEqual(0, report["unavailable"]["row_count"])
        self.assertEqual(100, report["exact"]["totals"]["total_tokens"])
        self.assertEqual(40, report["exact"]["totals"]["cache_read_tokens"])
        raw_line = self.copilot_events.read_text(encoding="utf-8")
        self.assertIn('"token_source":"unavailable"', raw_line)

    def test_codex_hook_events_map_to_shared_schema_and_report_unavailable_tokens(self):
        cases = [
            (
                "SessionStart",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_400_000,
                    "source": "resume",
                    "turn_id": "turn_01",
                },
                "session_started",
            ),
            (
                "UserPromptSubmit",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_410_000,
                    "prompt": "do not store prompt text",
                    "turn_id": "turn_01",
                },
                "prompt_submitted",
            ),
            (
                "PostToolUse",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_420_000,
                    "tool_name": "Bash",
                    "tool_use_id": "tool_01",
                    "tool_input": {"command": "cat secret.txt"},
                    "tool_response": {"output": "do not store tool output"},
                    "turn_id": "turn_01",
                },
                "tool_completed",
            ),
            (
                "PreCompact",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_430_000,
                    "trigger": "auto",
                    "turn_id": "turn_01",
                },
                "compaction_started",
            ),
            (
                "SubagentStart",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_440_000,
                    "agent_type": "probe",
                    "agent_id": "agent_01",
                    "turn_id": "turn_01",
                },
                "subagent_started",
            ),
            (
                "SubagentStop",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_450_000,
                    "agent_type": "probe",
                    "agent_id": "agent_01",
                    "last_assistant_message": "do not store assistant text",
                    "turn_id": "turn_01",
                },
                "subagent_completed",
            ),
            (
                "Stop",
                {
                    "cwd": "/tmp/example",
                    "timestamp": 1_718_302_460_000,
                    "stop_hook_active": False,
                    "last_assistant_message": "do not store stop text",
                    "turn_id": "turn_01",
                },
                "turn_completed",
            ),
        ]

        for event_name, payload, event_type in cases:
            with self.subTest(event_name=event_name):
                code, stdout, stderr = self.invoke_hook(
                    event_name,
                    payload,
                    "--client",
                    "codex",
                    "--home",
                    str(self.codex_home),
                )
                self.assertEqual(0, code)
                self.assertEqual("", stdout)
                self.assertEqual("", stderr)

        stored = self.read_events(self.codex_events)
        stored_types = [event["event_type"] for event in stored if event["event_type"] != "token_usage_recorded"]
        self.assertEqual(
            [
                "session_started",
                "prompt_submitted",
                "tool_completed",
                "compaction_started",
                "subagent_started",
                "subagent_completed",
                "turn_completed",
            ],
            stored_types,
        )
        raw_line = self.codex_events.read_text(encoding="utf-8")
        self.assertNotIn("secret.txt", raw_line)
        self.assertNotIn("do not store", raw_line)

        code, stdout, stderr = self.invoke(
            ["tokens", "--client", "codex", "--home", str(self.codex_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(0, report["exact"]["row_count"])
        self.assertEqual(1, report["unavailable"]["row_count"])

    def test_codex_stop_hook_records_exact_tokens_from_session_log(self):
        self.write_codex_session_lines(
            "session_exact",
            [
                {
                    "timestamp": "2026-06-15T00:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "user_prompt", "text": "secret prompt text"},
                },
                {
                    "timestamp": "2026-06-15T00:00:01Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 200,
                                "cached_input_tokens": 125,
                                "output_tokens": 22,
                                "total_tokens": 222,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-06-15T00:00:02Z",
                    "type": "event_msg",
                    "payload": {"type": "assistant_message", "message": "secret assistant text"},
                },
            ],
        )

        code, stdout, stderr = self.invoke_hook(
            "Stop",
            {
                "cwd": str(self.fixture_repo),
                "timestamp": 1_781_483_620_000,
                "session_id": "session_exact",
                "last_assistant_message": "do not store stop text",
                "stop_hook_active": False,
            },
            "--client",
            "codex",
            "--home",
            str(self.codex_home),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        stored = self.read_events(self.codex_events)
        token_events = [event for event in stored if event["event_type"] == "token_usage_recorded"]
        self.assertEqual(1, len(token_events))
        token_metrics = token_events[0]["metrics"]
        self.assertEqual("exact", token_metrics["token_source"])
        self.assertEqual("turn", token_metrics["attribution_scope"])
        self.assertEqual(200, token_metrics["input_tokens"])
        self.assertEqual(22, token_metrics["output_tokens"])
        self.assertEqual(222, token_metrics["total_tokens"])
        self.assertEqual(125, token_metrics["cache_read_tokens"])
        self.assertEqual(0, token_metrics["cache_write_tokens"])

        code, stdout, stderr = self.invoke(
            ["tokens", "--client", "codex", "--home", str(self.codex_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(1, report["exact"]["row_count"])
        self.assertEqual(0, report["unavailable"]["row_count"])
        self.assertEqual(222, report["exact"]["totals"]["total_tokens"])

        code, stdout, stderr = self.invoke(
            ["summarize", "--client", "codex", "--home", str(self.codex_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        summary = json.loads(stdout)
        self.assertEqual(222, summary["tokens"]["exact"]["totals"]["total_tokens"])
        self.assertEqual(0, summary["tokens"]["unavailable"]["row_count"])

        raw_line = self.codex_events.read_text(encoding="utf-8")
        self.assertNotIn("secret prompt text", raw_line)
        self.assertNotIn("secret assistant text", raw_line)
        self.assertNotIn("do not store", raw_line)

    def test_codex_stop_hook_ignores_overlapping_session_filename_matches(self):
        session_dir = self.codex_home / "sessions" / "2026" / "06" / "15"
        session_dir.mkdir(parents=True, exist_ok=True)
        wrong_path = session_dir / "rollout-2026-06-15T00-00-00-session_overlap_extra.jsonl"
        correct_path = session_dir / "rollout-2026-06-15T00-00-00-session_overlap.jsonl"
        wrong_path.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": {"input_tokens": 900, "output_tokens": 99, "total_tokens": 999}},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        correct_path.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": {"input_tokens": 100, "output_tokens": 23, "total_tokens": 123}},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(correct_path, (1_718_302_400, 1_718_302_400))
        os.utime(wrong_path, (1_718_302_500, 1_718_302_500))

        code, stdout, stderr = self.invoke_hook(
            "Stop",
            {
                "cwd": str(self.fixture_repo),
                "timestamp": 1_718_302_520_000,
                "session_id": "session_overlap",
                "stop_hook_active": False,
            },
            "--client",
            "codex",
            "--home",
            str(self.codex_home),
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        stored = self.read_events(self.codex_events)
        token_events = [event for event in stored if event["event_type"] == "token_usage_recorded"]
        self.assertEqual(1, len(token_events))
        self.assertEqual(123, token_events[0]["metrics"]["total_tokens"])

    def test_codex_reports_resolve_unavailable_tokens_from_session_log(self):
        self.write_codex_session_lines(
            "session_report",
            [
                {
                    "timestamp": "2026-06-15T00:00:04Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 80,
                                "cached_input_tokens": 40,
                                "output_tokens": 20,
                                "total_tokens": 100,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-06-15T00:00:10Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 900,
                                "cached_input_tokens": 800,
                                "output_tokens": 99,
                                "total_tokens": 999,
                            }
                        },
                    },
                },
            ],
        )
        self.record_fixture_event(
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_report_unavailable_tokens",
                timestamp="2026-06-15T00:00:05Z",
                session_id="session_report",
                source="summary",
                metrics={"token_source": "unavailable", "attribution_scope": "turn"},
            ),
            client="codex",
            home=self.codex_home,
        )

        code, stdout, stderr = self.invoke(
            ["tokens", "--client", "codex", "--home", str(self.codex_home), "--repo", "all", "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(1, report["exact"]["row_count"])
        self.assertEqual(0, report["unavailable"]["row_count"])
        self.assertEqual(100, report["exact"]["totals"]["total_tokens"])
        self.assertEqual(40, report["exact"]["totals"]["cache_read_tokens"])
        raw_line = self.codex_events.read_text(encoding="utf-8")
        self.assertIn('"token_source":"unavailable"', raw_line)

    def test_codex_hook_events_accept_docs_shaped_payloads_without_timestamp(self):
        before = datetime.now(tz=UTC)
        cases = [
            (
                "SessionStart",
                {
                    "cwd": "/tmp/example",
                    "source": "startup",
                    "session_id": "session_docs",
                },
                "session_started",
            ),
            (
                "UserPromptSubmit",
                {
                    "cwd": "/tmp/example",
                    "prompt": "do not store prompt text",
                    "turn_id": "turn_docs",
                    "session_id": "session_docs",
                },
                "prompt_submitted",
            ),
            (
                "Stop",
                {
                    "cwd": "/tmp/example",
                    "stop_hook_active": False,
                    "last_assistant_message": "do not store assistant text",
                    "turn_id": "turn_docs",
                    "session_id": "session_docs",
                },
                "turn_completed",
            ),
        ]

        for event_name, payload, event_type in cases:
            with self.subTest(event_name=event_name):
                code, stdout, stderr = self.invoke_hook(
                    event_name,
                    payload,
                    "--client",
                    "codex",
                    "--home",
                    str(self.codex_home),
                )
                self.assertEqual(0, code)
                self.assertEqual("", stdout)
                self.assertEqual("", stderr)

        after = datetime.now(tz=UTC)
        stored = self.read_events(self.codex_events)
        for event in stored:
            parsed = runtime.parse_iso_timestamp(event["timestamp"]).astimezone(UTC)
            self.assertGreaterEqual(parsed, before.replace(microsecond=0))
            self.assertLessEqual(parsed, after.replace(microsecond=0))

        stored_types = [event["event_type"] for event in stored]
        self.assertEqual(
            [
                "session_started",
                "prompt_submitted",
                "turn_completed",
                "token_usage_recorded",
            ],
            stored_types,
        )
        raw_line = self.codex_events.read_text(encoding="utf-8")
        self.assertNotIn("do not store", raw_line)

    def test_checkpoint_validation_blocks_freeform_metrics(self):
        code, stdout, stderr = self.invoke_checkpoint(
            "gate_decided",
            metrics={
                "gate_type": "user-testing",
                "decision": "approved",
                "user_explanation": "this should never be stored",
            },
        )

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("invalid_metric_key", stderr)
        self.assertFalse(self.copilot_events.exists())

    def test_reports_skip_malformed_lines_and_preserve_parity_counts(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_run_full_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_full_01",
                skill="dreamers-full",
                metrics={"mode": "plan-path", "plan_count": 1},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_run_full_end",
                timestamp="2026-06-13T10:30:00Z",
                run_id="run_full_01",
                skill="dreamers-full",
                metrics={"plan_count": 1, "final_status": "completed"},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_run_lite_start",
                timestamp="2026-06-13T11:00:00Z",
                run_id="run_lite_01",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_halted",
                event_id="evt_run_lite_end",
                timestamp="2026-06-13T11:05:00Z",
                run_id="run_lite_01",
                skill="dreamers-lite",
                metrics={"halt_reason_category": "user_halt"},
            )
        )
        self.write_fixture_lines(['{"not":"valid"', '{"event_id":"missing_fields"}'])

        code, stdout, stderr = self.invoke(
            ["summarize", "--client", "copilot", "--home", str(self.copilot_home), "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual("summarize", report["report_type"])
        self.assertEqual(2, report["warning_count"])
        self.assertEqual(2, report["runs"]["run_count"])
        self.assertEqual("2026-06-13T11:05:00Z", report["runs"]["range"]["last_timestamp"])

    def test_report_commands_match_expected_aggregates(self):
        sentinel_name = "sentinel-plan-01-20260613-100000.md"
        vigil_name = "vigil-plan-01-20260613-103000.md"
        self.write_review_artifact(
            self.fixture_repo,
            sentinel_name,
            "Status: Findings reported - 1 items\n\nFindings\n- [high] [correctness] dreamers_stats/runtime.py:10 - issue -> fix\n\nOpen Questions\nnone\n",
        )
        self.write_review_artifact(
            self.fixture_repo,
            vigil_name,
            "Approved — no findings\n\nOpen Questions\nnone\n",
        )
        events = (
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_review_full",
                timestamp="2026-06-13T10:15:00Z",
                run_id="run_review_01",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_full_01",
                    "lane": "full",
                    "reviewers": ["sentinel"],
                    "artifact_paths": [f".dreamers/reviews/{sentinel_name}"],
                    "findings_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 1, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 0,
                },
            ),
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_review_vigil",
                timestamp="2026-06-13T10:35:00Z",
                run_id="run_review_01",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_vigil_01",
                    "lane": "vigil",
                    "reviewers": ["vigil"],
                    "artifact_paths": [f".dreamers/reviews/{vigil_name}"],
                    "findings_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 0, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 0,
                    "is_rereview": True,
                    "trigger": "user_testing_bug",
                },
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_validation_test_01",
                timestamp="2026-06-13T10:01:00Z",
                run_id="run_validate_01",
                skill="dreamers-full",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 1,
                    "result": "fail",
                    "failure_category": "test-failure",
                },
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_validation_test_02",
                timestamp="2026-06-13T10:02:00Z",
                run_id="run_validate_01",
                skill="dreamers-full",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 2,
                    "result": "pass",
                },
            ),
            self.fixture_event(
                "gate_decided",
                event_id="evt_gate_bug",
                timestamp="2026-06-13T09:05:00Z",
                run_id="run_gate_01",
                skill="dreamers-full",
                metrics={"gate_type": "user-testing", "decision": "bug_found", "bug_count": 1},
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_tokens_exact_01",
                timestamp="2026-06-13T10:20:00Z",
                run_id="run_tokens_01",
                session_id="sess_exact_01",
                skill="dreamers-full",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "model": "gpt-5",
                    "attribution_scope": "session",
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "ai_credits": 1.25,
                },
            ),
        )
        for event in events:
            self.record_fixture_event(event)

        commands = {
            "reviews": lambda report: self.assertEqual(1, report["findings_by_severity"]["high"]),
            "validation": lambda report: self.assertEqual(1, report["command_kinds"]["test"]["retry_count"]),
            "gates": lambda report: self.assertEqual({"bug_found": 1}, report["decision_counts"]["user-testing"]),
            "tokens": lambda report: self.assertEqual(120, report["exact"]["totals"]["total_tokens"]),
        }
        for command, assertion in commands.items():
            with self.subTest(command=command):
                code, stdout, stderr = self.invoke(
                    [command, "--client", "copilot", "--home", str(self.copilot_home), "--json"],
                    cwd=self.fixture_repo,
                )
                self.assertEqual(0, code)
                self.assertEqual("", stderr)
                assertion(json.loads(stdout))

    def test_runs_report_includes_individual_run_stats(self):
        events = [
            self.fixture_event(
                "skill_started",
                event_id="evt_run_detail_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={"mode": "plan-path"},
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_run_detail_validation_fail",
                timestamp="2026-06-13T10:01:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 1,
                    "result": "fail",
                    "failure_category": "test-failure",
                },
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_run_detail_validation_pass",
                timestamp="2026-06-13T10:02:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 2,
                    "result": "pass",
                },
            ),
            self.fixture_event(
                "gate_decided",
                event_id="evt_run_detail_gate",
                timestamp="2026-06-13T10:03:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={"gate_type": "plan-approval", "decision": "approved"},
            ),
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_run_detail_review",
                timestamp="2026-06-13T10:04:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_run_detail_01",
                    "lane": "full",
                    "reviewers": ["sentinel"],
                    "artifact_paths": [],
                    "findings_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 1, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 1,
                },
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_run_detail_tokens",
                timestamp="2026-06-13T10:05:00Z",
                run_id="run_detail_01",
                session_id="sess_run_detail_01",
                skill="dreamers-full",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "model": "gpt-5",
                    "attribution_scope": "session",
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "total_tokens": 42,
                    "ai_credits": 0.5,
                },
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_run_detail_completed",
                timestamp="2026-06-13T10:06:00Z",
                run_id="run_detail_01",
                skill="dreamers-full",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_run_detail_other_start",
                timestamp="2026-06-13T11:00:00Z",
                run_id="run_detail_02",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
        ]
        for event in events:
            self.record_fixture_event(event)

        report = runtime.run_report(
            "runs",
            client="copilot",
            home=self.copilot_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        self.assertEqual(1, report["run_count"])
        self.assertEqual(1, report["incomplete_count"])
        items = {item["run_id"]: item for item in report["items"]}
        self.assertNotIn("run_detail_02", items)
        incomplete = {item["run_id"]: item for item in report["incomplete_items"]}
        self.assertEqual("missing_terminal", incomplete["run_detail_02"]["reason"])
        run = items["run_detail_01"]
        self.assertEqual("completed", run["status"])
        self.assertEqual(360, run["duration_seconds"])
        self.assertEqual("2026-06-13T10:00:00Z", run["first_timestamp"])
        self.assertEqual("2026-06-13T10:06:00Z", run["last_timestamp"])
        self.assertEqual(2, run["validation"]["attempt_count"])
        self.assertEqual(1, run["validation"]["command_kinds"]["test"]["retry_count"])
        self.assertEqual({"approved": 1}, run["gates"]["decision_counts"]["plan-approval"])
        self.assertEqual(1, run["reviews"]["review_count"])
        self.assertEqual(1, run["reviews"]["findings_by_severity"]["high"])
        self.assertEqual(42, run["tokens"]["exact"]["totals"]["total_tokens"])

    def test_runs_report_separates_unreliable_runs_from_default_aggregates(self):
        events = [
            self.fixture_event(
                "skill_started",
                event_id="evt_reliable_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_reliable",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_reliable_done",
                timestamp="2026-06-13T10:03:00Z",
                run_id="run_reliable",
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_missing_start_validation",
                timestamp="2026-06-13T10:04:00Z",
                run_id="run_missing_start",
                skill="dreamers-lite",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 1,
                    "result": "fail",
                    "failure_category": "test-failure",
                },
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_missing_terminal_start",
                timestamp="2026-06-13T10:05:00Z",
                run_id="run_missing_terminal",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_missing_terminal_tokens",
                timestamp="2026-06-13T10:06:00Z",
                run_id="run_missing_terminal",
                session_id="sess_missing_terminal",
                skill="dreamers-lite",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "attribution_scope": "session",
                    "input_tokens": 90,
                    "output_tokens": 10,
                    "total_tokens": 100,
                },
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_duplicate_start_a",
                timestamp="2026-06-13T10:07:00Z",
                run_id="run_duplicate_start",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_duplicate_start_b",
                timestamp="2026-06-13T10:08:00Z",
                run_id="run_duplicate_start",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_duplicate_start_done",
                timestamp="2026-06-13T10:09:00Z",
                run_id="run_duplicate_start",
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_duplicate_terminal_start",
                timestamp="2026-06-13T10:10:00Z",
                run_id="run_duplicate_terminal",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_duplicate_terminal_done_a",
                timestamp="2026-06-13T10:11:00Z",
                run_id="run_duplicate_terminal",
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_halted",
                event_id="evt_duplicate_terminal_done_b",
                timestamp="2026-06-13T10:12:00Z",
                run_id="run_duplicate_terminal",
                skill="dreamers-lite",
                metrics={"halt_reason_category": "other_safe"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_reversed_done",
                timestamp="2026-06-13T10:13:00Z",
                run_id="run_reversed",
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_reversed_start",
                timestamp="2026-06-13T10:14:00Z",
                run_id="run_reversed",
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
        ]
        for event in events:
            self.record_fixture_event(event)

        report = runtime.run_report(
            "runs",
            client="copilot",
            home=self.copilot_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        self.assertEqual(1, report["run_count"])
        self.assertEqual(5, report["incomplete_count"])
        self.assertEqual(["run_reliable"], [item["run_id"] for item in report["items"]])
        reliable = report["items"][0]
        self.assertEqual(0, reliable["validation"]["attempt_count"])
        self.assertEqual(0, reliable["tokens"]["exact"]["totals"]["total_tokens"])
        reasons = {item["run_id"]: item["reason"] for item in report["incomplete_items"]}
        self.assertEqual(
            {
                "run_missing_start": "missing_start",
                "run_missing_terminal": "missing_terminal",
                "run_duplicate_start": "duplicate_starts",
                "run_duplicate_terminal": "duplicate_terminals",
                "run_reversed": "terminal_before_start",
            },
            reasons,
        )

    def test_runs_report_correlates_hook_token_events_by_unique_session(self):
        session_id = "session_run_detail_hook"
        self.write_codex_session_lines(
            session_id,
            [
                {
                    "timestamp": "2026-06-15T10:04:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 80,
                                "output_tokens": 20,
                                "total_tokens": 100,
                            },
                            "model": "gpt-5",
                        },
                    },
                },
            ],
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_run_detail_hook_start",
                timestamp="2026-06-15T10:00:00Z",
                run_id="run_detail_hook_01",
                session_id=session_id,
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            client="codex",
            home=self.codex_home,
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_run_detail_hook_done",
                timestamp="2026-06-15T10:05:00Z",
                run_id="run_detail_hook_01",
                session_id=session_id,
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            client="codex",
            home=self.codex_home,
        )
        code, stdout, stderr = self.invoke_hook(
            "Stop",
            {
                "cwd": str(self.fixture_repo),
                "timestamp": "2026-06-15T10:05:00Z",
                "session_id": session_id,
                "stop_hook_active": False,
            },
            "--client",
            "codex",
            "--home",
            str(self.codex_home),
        )
        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        token_events = [
            event for event in self.read_events(self.codex_events)
            if event["event_type"] == "token_usage_recorded"
        ]
        self.assertEqual(1, len(token_events))
        self.assertIsNone(token_events[0]["run_id"])
        self.assertIsNone(token_events[0]["skill"])

        report = runtime.run_report(
            "runs",
            client="codex",
            home=self.codex_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        items = {item["run_id"]: item for item in report["items"]}
        self.assertEqual(100, items["run_detail_hook_01"]["tokens"]["exact"]["totals"]["total_tokens"])

    def test_runs_report_keeps_shared_session_tokens_unattributed_when_incomplete_run_competes(self):
        session_id = "session_shared_with_incomplete"
        self.write_codex_session_lines(
            session_id,
            [
                {
                    "timestamp": "2026-06-15T10:06:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "total_tokens": 150,
                            },
                            "model": "gpt-5",
                        },
                    },
                },
            ],
        )
        events = [
            self.fixture_event(
                "skill_started",
                event_id="evt_shared_reliable_start",
                timestamp="2026-06-15T10:00:00Z",
                run_id="run_shared_reliable",
                session_id=session_id,
                skill="dreamers-lite",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_shared_reliable_done",
                timestamp="2026-06-15T10:05:00Z",
                run_id="run_shared_reliable",
                session_id=session_id,
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_shared_incomplete_start",
                timestamp="2026-06-15T10:02:00Z",
                run_id="run_shared_incomplete",
                session_id=session_id,
                skill="dreamers-full",
                metrics={"mode": "task-description"},
            ),
        ]
        for event in events:
            self.record_fixture_event(event, client="codex", home=self.codex_home)

        code, stdout, stderr = self.invoke_hook(
            "Stop",
            {
                "cwd": str(self.fixture_repo),
                "timestamp": "2026-06-15T10:06:00Z",
                "session_id": session_id,
                "stop_hook_active": False,
            },
            "--client",
            "codex",
            "--home",
            str(self.codex_home),
        )
        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)

        report = runtime.run_report(
            "runs",
            client="codex",
            home=self.codex_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        self.assertEqual(1, report["run_count"])
        self.assertEqual(1, report["incomplete_count"])
        items = {item["run_id"]: item for item in report["items"]}
        self.assertEqual(0, items["run_shared_reliable"]["tokens"]["exact"]["totals"]["total_tokens"])
        self.assertEqual("run_shared_incomplete", report["incomplete_items"][0]["run_id"])

    def test_dashboard_command_writes_standalone_html_file(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_dashboard_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_01",
                skill="dreamers-full",
                metrics={"mode": "plan-path", "plan_count": 1},
            ),
            client="codex",
            home=self.codex_home,
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_done",
                timestamp="2026-06-13T10:02:00Z",
                run_id="run_dashboard_01",
                skill="dreamers-full",
                metrics={"final_status": "completed", "plan_count": 1},
            ),
            client="codex",
            home=self.codex_home,
        )
        output_path = Path(self.tmp.name) / "dreamers-stats.html"

        code, stdout, stderr = self.invoke(
            [
                "dashboard",
                "--client",
                "codex",
                "--home",
                str(self.codex_home),
                "--repo",
                "current",
                "--generated-at",
                "2026-06-15T00:00:00Z",
                "--output",
                str(output_path),
            ],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stdout)
        self.assertEqual("", stderr)
        self.assertGreater(output_path.stat().st_size, 1000)
        html_text = output_path.read_text(encoding="utf-8")
        self.assertIn("<!doctype html>", html_text)
        self.assertIn("Dreamers Stats", html_text)
        self.assertIn(f"current_repo={self.fixture_repo}", html_text)
        self.assertIn("Runs by skill", html_text)
        self.assertIn("Reviews", html_text)
        self.assertIn("Validation", html_text)
        self.assertIn("Gates", html_text)
        self.assertIn("Tokens", html_text)
        self.assertIn("dreamers-full", html_text)
        self.assertIn("Run details", html_text)

        code, stdout, stderr = self.invoke(
            [
                "dashboard",
                "--client",
                "codex",
                "--home",
                str(self.codex_home),
                "--repo",
                "current",
                "--generated-at",
                "2026-06-15T00:00:00Z",
            ],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertEqual(html_text, stdout)

    def test_dashboard_cli_renders_expandable_individual_run_details(self):
        events = [
            self.fixture_event(
                "skill_started",
                event_id="evt_dashboard_detail_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_detail_01",
                skill="dreamers-full",
                metrics={"mode": "plan-path"},
            ),
            self.fixture_event(
                "validation_attempt",
                event_id="evt_dashboard_detail_validation",
                timestamp="2026-06-13T10:01:00Z",
                run_id="run_dashboard_detail_01",
                skill="dreamers-full",
                metrics={
                    "command_kind": "test",
                    "command_label": "unittest",
                    "attempt_number": 1,
                    "result": "pass",
                },
            ),
            self.fixture_event(
                "gate_decided",
                event_id="evt_dashboard_detail_gate",
                timestamp="2026-06-13T10:02:00Z",
                run_id="run_dashboard_detail_01",
                skill="dreamers-full",
                metrics={"gate_type": "plan-approval", "decision": "approved"},
            ),
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_dashboard_detail_review",
                timestamp="2026-06-13T10:03:00Z",
                run_id="run_dashboard_detail_01",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_dashboard_detail_01",
                    "lane": "full",
                    "reviewers": ["sentinel"],
                    "artifact_paths": [],
                    "findings_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 0, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 0,
                },
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_dashboard_detail_tokens",
                timestamp="2026-06-13T10:04:00Z",
                run_id="run_dashboard_detail_01",
                session_id="sess_dashboard_detail_01",
                skill="dreamers-full",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "model": "gpt-5",
                    "attribution_scope": "session",
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "total_tokens": 42,
                    "ai_credits": 0.5,
                },
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_detail_completed",
                timestamp="2026-06-13T10:05:00Z",
                run_id="run_dashboard_detail_01",
                skill="dreamers-full",
                metrics={"final_status": "completed"},
            ),
        ]
        for event in events:
            self.record_fixture_event(event)

        code, stdout, stderr = self.invoke(
            ["dashboard", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn('<section class="panel run-details"><h2>Run details</h2>', stdout)
        self.assertIn('<details class="run-detail">', stdout)
        self.assertIn("run_dashboard_detail_01", stdout)
        self.assertIn("<dt>validation attempts</dt><dd>1</dd>", stdout)
        self.assertIn("<dt>gate decisions</dt><dd>1</dd>", stdout)
        self.assertIn("<dt>review passes</dt><dd>1</dd>", stdout)
        self.assertIn("<dt>token total</dt><dd>42</dd>", stdout)
        self.assertIn("<dt>first seen</dt><dd>Jun 13, 2026 10:00 UTC</dd>", stdout)
        self.assertIn("<dt>last seen</dt><dd>Jun 13, 2026 10:05 UTC</dd>", stdout)

    def test_dashboard_formats_values_timestamps_and_status_badges(self):
        events = [
            self.fixture_event(
                "skill_started",
                event_id="evt_dashboard_completed_start",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_completed",
                skill="dreamers-full",
                metrics={"mode": "plan-path"},
            ),
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_completed_done",
                timestamp="2026-06-13T10:02:00Z",
                run_id="run_dashboard_completed",
                skill="dreamers-full",
                metrics={"final_status": "completed"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_dashboard_halted_start",
                timestamp="2026-06-13T10:03:00Z",
                run_id="run_dashboard_halted",
                skill="dreamers-update",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "skill_halted",
                event_id="evt_dashboard_halted",
                timestamp="2026-06-13T10:05:00Z",
                run_id="run_dashboard_halted",
                skill="dreamers-update",
                metrics={"halt_reason_category": "user_halt"},
            ),
            self.fixture_event(
                "skill_started",
                event_id="evt_dashboard_progress",
                timestamp="2026-06-13T10:06:00Z",
                run_id="run_dashboard_progress",
                skill="dreamers-note",
                metrics={"mode": "task-description"},
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_dashboard_large_tokens",
                timestamp="2026-06-13T10:20:00Z",
                run_id="run_dashboard_completed",
                session_id="sess_dashboard_tokens",
                skill="dreamers-full",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "model": "gpt-5",
                    "attribution_scope": "session",
                    "input_tokens": 1000000,
                    "output_tokens": 234567,
                    "total_tokens": 1234567,
                    "cache_read_tokens": 12000,
                    "cache_write_tokens": 3000,
                    "ai_credits": 12.5,
                },
            ),
        ]
        for event in events:
            self.record_fixture_event(event)
        report = runtime.run_report(
            "summarize",
            client="copilot",
            home=self.copilot_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        html_text = runtime.render_dashboard_html(report, client="copilot", generated_at="2026-06-15T07:05:37Z")

        self.assertIn("Generated: Jun 15, 2026 07:05 UTC", html_text)
        self.assertIn("Data range: Jun 13, 2026 10:00 UTC to Jun 13, 2026 10:20 UTC", html_text)
        self.assertIn("<strong>1,234,567</strong>", html_text)
        self.assertIn('<span class="status-badge status-completed">completed</span>', html_text)
        self.assertIn('<span class="status-badge status-halted">halted</span>', html_text)
        self.assertIn("Incomplete / ambiguous runs", html_text)
        self.assertIn("missing_terminal", html_text)

    def test_dashboard_cli_uses_structured_gate_and_token_tables(self):
        events = [
            self.fixture_event(
                "gate_decided",
                event_id="evt_dashboard_gate_plan",
                timestamp="2026-06-13T09:00:00Z",
                run_id="run_dashboard_gate",
                skill="dreamers-full",
                metrics={"gate_type": "plan-approval", "decision": "approved"},
            ),
            self.fixture_event(
                "gate_decided",
                event_id="evt_dashboard_gate_push",
                timestamp="2026-06-13T09:05:00Z",
                run_id="run_dashboard_gate",
                skill="dreamers-full",
                metrics={"gate_type": "push-decision", "decision": "approved"},
            ),
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_dashboard_cli_tokens",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_tokens",
                session_id="sess_dashboard_cli_tokens",
                skill="dreamers-full",
                source="summary",
                metrics={
                    "token_source": "exact",
                    "model": "gpt-5",
                    "attribution_scope": "session",
                    "input_tokens": 1000000,
                    "output_tokens": 2000,
                    "total_tokens": 1002000,
                    "cache_read_tokens": 3000,
                    "cache_write_tokens": 4000,
                    "ai_credits": 2.0,
                },
            ),
        ]
        for event in events:
            self.record_fixture_event(event)

        code, stdout, stderr = self.invoke(
            ["dashboard", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("<th scope=\"col\">Gate</th>", stdout)
        self.assertIn("<th scope=\"col\">Decisions</th>", stdout)
        self.assertIn("<td>plan-approval</td><td>1</td><td>approved=1</td>", stdout)
        self.assertIn("<td>push-decision</td><td>1</td><td>approved=1</td>", stdout)
        self.assertIn("<th scope=\"col\">Quality</th>", stdout)
        self.assertIn("<td>exact</td><td>1</td><td>1</td><td>1,000,000</td>", stdout)
        self.assertNotIn("<dt>plan-approval</dt>", stdout)
        self.assertNotIn("rows=1 sessions=1 total_tokens=1002000", stdout)

    def test_dashboard_command_writes_html_to_stdout_without_output_path(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_stdout",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_stdout",
                skill="dreamers-lite",
                metrics={"final_status": "completed"},
            )
        )

        code, stdout, stderr = self.invoke(
            ["dashboard", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("<!doctype html>", stdout)
        self.assertIn("dreamers-lite", stdout)

    def test_dashboard_empty_state_names_active_filter(self):
        code, stdout, stderr = self.invoke(
            ["dashboard", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn(f"current_repo={self.fixture_repo}", stdout)
        self.assertIn("no runs matched these filters", stdout)
        self.assertIn("no validation attempts matched these filters", stdout)

    def test_dashboard_token_card_marks_unavailable_totals_as_na(self):
        self.record_fixture_event(
            self.fixture_event(
                "token_usage_recorded",
                event_id="evt_dashboard_unavailable_tokens",
                timestamp="2026-06-13T10:00:00Z",
                session_id="sess_unavailable",
                source="summary",
                metrics={"token_source": "unavailable", "attribution_scope": "turn"},
            )
        )
        report = runtime.run_report(
            "summarize",
            client="copilot",
            home=self.copilot_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        html_text = runtime.render_dashboard_html(report, client="copilot", generated_at="2026-06-15T00:00:00Z")

        self.assertIn("<span>Tokens</span><strong>n/a</strong><small>unavailable totals</small>", html_text)
        self.assertIn("<td>unavailable</td><td>1</td><td>1</td><td>n/a</td>", html_text)
        self.assertNotIn("<span>Tokens</span><strong>0</strong><small>exact total</small>", html_text)

    def test_dashboard_renderer_escapes_report_values(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_escape",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_escape",
                skill="dreamers-<script>&",
                metrics={"final_status": "completed"},
            )
        )
        report = runtime.run_report(
            "summarize",
            client="copilot",
            home=self.copilot_home,
            repo="current",
            cwd=self.fixture_repo,
        )

        html_text = runtime.render_dashboard_html(report, client="copilot", generated_at="2026-06-15T00:00:00Z")

        self.assertNotIn("dreamers-<script>&", html_text)
        self.assertIn("dreamers-&lt;script&gt;&amp;", html_text)

    def test_dashboard_includes_malformed_line_warning(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_dashboard_warning",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_dashboard_warning",
                skill="dreamers-full",
                metrics={"final_status": "completed"},
            )
        )
        self.write_fixture_lines(['{"not":"valid"', '{"event_id":"missing_fields"}'])

        code, stdout, stderr = self.invoke(
            ["dashboard", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("Warnings", stdout)
        self.assertIn("skipped 2 malformed historical lines", stdout)
        self.assertIn(".warning{display:flex", stdout)

    def test_runs_report_preserves_real_run_identity_and_final_status(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_real_start",
                timestamp="2026-06-13T08:00:00Z",
                run_id="run_real_01",
                skill="dreamers-full",
                metrics={"mode": "plan-path", "plan_count": 1},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_real_end",
                timestamp="2026-06-13T08:20:00Z",
                run_id="run_real_01",
                skill="dreamers-full",
                metrics={"plan_count": 1, "final_status": "resolved"},
            )
        )
        event_missing_run = self.fixture_event(
            "skill_completed",
            event_id="evt_missing_run",
            timestamp="2026-06-13T09:00:00Z",
            run_id=None,
            skill="dreamers-full",
            metrics={"plan_count": 1, "final_status": "completed"},
        )
        self.record_fixture_event(event_missing_run)
        event_missing_skill = self.fixture_event(
            "skill_completed",
            event_id="evt_missing_skill",
            timestamp="2026-06-13T09:05:00Z",
            run_id="run_missing_skill",
            skill=None,
            metrics={"plan_count": 1, "final_status": "completed"},
        )
        self.record_fixture_event(event_missing_skill)

        code, stdout, stderr = self.invoke(
            ["runs", "--client", "copilot", "--home", str(self.copilot_home), "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(1, report["run_count"])
        self.assertEqual("resolved", report["groups"][0]["status"])
        self.assertEqual(1200, report["groups"][0]["total_duration_seconds"])

    def test_reviews_report_uses_artifact_counts_and_missing_artifact_fallbacks(self):
        blocked_name = "probe-plan-01-20260613-101000.md"
        missing_name = "missing-plan-01-20260613-101500.md"
        self.write_review_artifact(
            self.fixture_repo,
            blocked_name,
            "\n".join(
                [
                    "Status: Blocked - missing parity data",
                    "",
                    "Findings",
                    "- [high] [test-coverage] tests/test_shared_stats.py:10 - issue -> fix",
                    "",
                    "Open Questions",
                    "1. should the review rerun after parity fixes?",
                ]
            )
            + "\n",
        )

        self.record_fixture_event(
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_review_blocked",
                timestamp="2026-06-13T10:15:00Z",
                run_id="run_review_01",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_full_01",
                    "lane": "probe",
                    "reviewers": ["probe"],
                    "artifact_paths": [f".dreamers/reviews/{blocked_name}"],
                    "findings_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 0, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 0,
                },
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_review_missing",
                timestamp="2026-06-13T10:20:00Z",
                run_id="run_review_02",
                skill="dreamers-full",
                metrics={
                    "review_pass_id": "review_full_02",
                    "lane": "hone",
                    "reviewers": ["hone"],
                    "artifact_paths": [f".dreamers/reviews/{missing_name}"],
                    "findings_by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 0, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 1},
                    "blocked": True,
                    "open_question_count": 2,
                },
            )
        )

        code, stdout, stderr = self.invoke(
            ["reviews", "--client", "copilot", "--home", str(self.copilot_home), "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(2, report["review_count"])
        self.assertEqual(2, report["blocked_count"])
        self.assertEqual(3, report["open_question_count"])
        self.assertEqual(2, report["findings_by_severity"]["high"])
        self.assertEqual(1, report["artifact_summary"]["parsed_count"])
        self.assertEqual(1, report["artifact_summary"]["missing_count"])

    def test_reviews_report_counts_unreferenced_current_repo_artifacts_once(self):
        referenced_name = "vigil-referenced-20260613-101000.md"
        unreferenced_name = "probe-unreferenced-20260613-101500.md"
        other_repo_name = "hone-other-20260613-102000.md"
        self.write_review_artifact(
            self.fixture_repo,
            referenced_name,
            "Findings reported - 1 items\n\nFindings\n- [high] [simplicity] dreamers_stats/runtime.py:10 - issue -> fix\n\nOpen Questions\n- none\n",
        )
        self.write_review_artifact(
            self.fixture_repo,
            unreferenced_name,
            "Blocked - missing coverage\n\nFindings\n- [medium] [test-coverage] tests/test_shared_stats.py:10 - issue -> fix\n\nOpen Questions\n1. should this rerun?\n",
        )
        (self.other_repo / ".dreamers" / "reviews").mkdir(parents=True)
        self.write_review_artifact(
            self.other_repo,
            other_repo_name,
            "Findings reported - 1 items\n\nFindings\n- [low] [simplicity] other.py:1 - issue -> fix\n\nOpen Questions\nnone\n",
        )
        self.record_fixture_event(
            self.fixture_event(
                "review_pass_completed",
                event_id="evt_review_referenced",
                timestamp="2026-06-13T10:15:00Z",
                run_id="run_review_referenced",
                skill="dreamers-lite",
                metrics={
                    "review_pass_id": "review_referenced",
                    "lane": "vigil",
                    "reviewers": ["vigil"],
                    "artifact_paths": [f".dreamers/reviews/{referenced_name}"],
                    "findings_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "findings_by_lens": {"correctness": 0, "security": 0, "maintainability": 0, "test-coverage": 0, "simplicity": 0},
                    "blocked": False,
                    "open_question_count": 0,
                },
            )
        )

        code, stdout, stderr = self.invoke(
            ["reviews", "--client", "copilot", "--home", str(self.copilot_home), "--repo", "current", "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(2, report["review_count"])
        self.assertEqual(1, report["artifact_summary"]["artifact_only_count"])
        self.assertEqual(2, report["artifact_summary"]["parsed_count"])
        self.assertEqual(1, report["blocked_count"])
        self.assertEqual(1, report["open_question_count"])
        self.assertEqual(1, report["findings_by_severity"]["high"])
        self.assertEqual(1, report["findings_by_severity"]["medium"])

    def test_validation_report_uses_full_attempt_identity_and_timestamp_tiebreaker(self):
        base_metrics = {
            "command_kind": "test",
            "command_label": "unittest",
            "scope": "plan",
            "plan_path": ".dreamers/plans/feature-dreamers-mcp/plan-01-shared-stats-core.md",
        }
        self.record_fixture_event(
            self.fixture_event(
                "validation_attempt",
                event_id="evt_validation_a1",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_validate_01",
                skill="dreamers-full",
                metrics={**base_metrics, "attempt_number": 1, "result": "fail", "failure_category": "test-failure"},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "validation_attempt",
                event_id="evt_validation_a2",
                timestamp="2026-06-13T10:00:01Z",
                run_id="run_validate_01",
                skill="dreamers-full",
                metrics={**base_metrics, "attempt_number": 1, "result": "pass"},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "validation_attempt",
                event_id="evt_validation_other_scope",
                timestamp="2026-06-13T10:01:00Z",
                run_id="run_validate_01",
                skill="dreamers-full",
                metrics={
                    **base_metrics,
                    "scope": "repo",
                    "attempt_number": 1,
                    "result": "fail",
                    "failure_category": "test-failure",
                },
            )
        )

        code, stdout, stderr = self.invoke(
            ["validation", "--client", "copilot", "--home", str(self.copilot_home), "--json"],
            cwd=self.fixture_repo,
        )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        report = json.loads(stdout)
        self.assertEqual(3, report["attempt_count"])
        self.assertEqual(2, report["command_kinds"]["test"]["failure_count"])
        self.assertEqual(1, report["command_kinds"]["test"]["final_pass_count"])
        self.assertEqual(1, report["command_kinds"]["test"]["final_fail_count"])

    def test_mcp_server_lists_tools_and_returns_bounded_summary(self):
        self.record_fixture_event(
            self.fixture_event(
                "skill_started",
                event_id="evt_summary_start",
                timestamp="2026-06-13T09:55:00Z",
                run_id="run_summary_01",
                skill="dreamers-full",
                metrics={"mode": "task-description"},
            )
        )
        self.record_fixture_event(
            self.fixture_event(
                "skill_completed",
                event_id="evt_summary_ok",
                timestamp="2026-06-13T10:00:00Z",
                run_id="run_summary_01",
                skill="dreamers-full",
                metrics={"plan_count": 1, "final_status": "completed"},
            )
        )

        request_lines = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "summarize",
                        "arguments": {
                            "client": "copilot",
                            "home": str(self.copilot_home),
                            "repo": "all",
                            "output": "json",
                        },
                    },
                }
            ),
        ]

        completed = subprocess.run(
            [sys.executable, "-m", "dreamers_stats.mcp_server"],
            input="\n".join(request_lines) + "\n",
            text=True,
            capture_output=True,
            check=False,
            cwd=REPO_ROOT,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(3, len(responses))
        initialize = responses[0]
        tools_list = responses[1]
        tool_call = responses[2]
        self.assertEqual("2025-11-25", initialize["result"]["protocolVersion"])
        tool_names = [tool["name"] for tool in tools_list["result"]["tools"]]
        self.assertIn("summarize", tool_names)
        self.assertIn("record_event", tool_names)
        self.assertFalse(tool_call["result"]["isError"])
        structured = tool_call["result"]["structuredContent"]
        self.assertEqual("summarize", structured["report_type"])
        self.assertEqual(1, structured["runs"]["run_count"])
        self.assertNotIn("evt_summary_ok", json.dumps(tool_call["result"]))


if __name__ == "__main__":
    unittest.main()
