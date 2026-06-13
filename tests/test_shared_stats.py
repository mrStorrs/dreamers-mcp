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
