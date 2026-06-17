import json
import os
import shutil

from dreamers_stats import codex_bundle
from tests.bundle_test_support import (
    CODEX_AGENTS_CONFIG_RELATIVE,
    CODEX_BASH_INSTALLER_PATH,
    CODEX_BASH_REMOVER_PATH,
    CODEX_BASH_WRAPPER_RELATIVE,
    CODEX_HOOKS_CONFIG_RELATIVE,
    CODEX_INSTALL_STATE_RELATIVE,
    CODEX_MCP_CONFIG_RELATIVE,
    CODEX_MCP_SERVER_SHIM_RELATIVE,
    CODEX_POWERSHELL_INSTALLER_PATH,
    CODEX_POWERSHELL_REMOVER_PATH,
    CODEX_POWERSHELL_WRAPPER_RELATIVE,
    CODEX_SHIM_RELATIVE,
    CODEX_STATS_REF_RELATIVE,
    INSTALLED_NODE_DIST_RELATIVE,
    INSTALLED_NODE_RUNTIME_RELATIVE,
    INSTALLED_RUNTIME_PACKAGE_RELATIVE,
    BundleTestCase,
    REPO_ROOT,
)


class CodexBundleTests(BundleTestCase):
    def test_install_updates_previously_managed_runtime_file_when_source_changes(self):
        checkout_root = self.fixture_repo / "checkout"
        runtime_dir = checkout_root / "dreamers_stats"
        shared_scripts_dir = checkout_root / "bundles" / "shared" / "scripts"
        scripts_dir = checkout_root / "bundles" / "codex" / "scripts"
        refs_dir = checkout_root / "bundles" / "codex" / "refs"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shared_scripts_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        refs_dir.mkdir(parents=True, exist_ok=True)

        for name in codex_bundle.RUNTIME_FILES:
            shutil.copy2(REPO_ROOT / "dreamers_stats" / name, runtime_dir / name)
        shutil.copy2(REPO_ROOT / "package.json", checkout_root / "package.json")
        shutil.copytree(REPO_ROOT / "dist", checkout_root / "dist")
        for source_dir, target_dir in (
            (REPO_ROOT / "bundles" / "shared" / "scripts", shared_scripts_dir),
            (REPO_ROOT / "bundles" / "codex" / "scripts", scripts_dir),
            (REPO_ROOT / "bundles" / "codex" / "refs", refs_dir),
        ):
            for source_path in source_dir.iterdir():
                if source_path.is_file():
                    shutil.copy2(source_path, target_dir / source_path.name)

        first = codex_bundle.install_bundle(
            self.codex_home,
            checkout_root,
            force=False,
            launcher_command="python3",
            launcher_args=[],
        )
        self.assertGreater(first.installed_count, 0)

        source_runtime = runtime_dir / "runtime.py"
        source_runtime.write_text(source_runtime.read_text(encoding="utf-8") + "\n# updated source\n", encoding="utf-8")

        second = codex_bundle.install_bundle(
            self.codex_home,
            checkout_root,
            force=False,
            launcher_command="python3",
            launcher_args=[],
        )

        installed_runtime = self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE / "runtime.py"
        self.assertEqual(1, second.installed_count)
        self.assertEqual(source_runtime.read_text(encoding="utf-8"), installed_runtime.read_text(encoding="utf-8"))
        self.assertTrue((self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js").exists())
        self.assertTrue((self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "mcp-server.js").exists())

    def test_bash_install_and_remove_preserve_history_and_user_config(self):
        historic_events = self.events_file(self.codex_home)
        historic_events.parent.mkdir(parents=True, exist_ok=True)
        historic_events.write_text('{"event_id":"historic"}\n', encoding="utf-8")
        self.codex_home.mkdir(parents=True, exist_ok=True)
        agents_path = self.codex_home / CODEX_AGENTS_CONFIG_RELATIVE
        original_agents = "# Personal Instructions\n\nKeep this line.\n"
        agents_path.write_text(original_agents, encoding="utf-8")

        hooks_config = self.codex_home / CODEX_HOOKS_CONFIG_RELATIVE
        hooks_config.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo keep-user-hook",
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        config_toml = self.codex_home / CODEX_MCP_CONFIG_RELATIVE
        config_toml.write_text('[mcp_servers.keep]\ncommand = "keep"\n', encoding="utf-8")

        completed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue((self.codex_home / CODEX_HOOKS_CONFIG_RELATIVE).exists())
        self.assertTrue((self.codex_home / CODEX_BASH_WRAPPER_RELATIVE).exists())
        self.assertTrue((self.codex_home / CODEX_POWERSHELL_WRAPPER_RELATIVE).exists())
        self.assertTrue((self.codex_home / CODEX_SHIM_RELATIVE).exists())
        self.assertTrue((self.codex_home / CODEX_MCP_SERVER_SHIM_RELATIVE).exists())
        self.assertTrue((self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE / "runtime.py").exists())
        self.assertTrue((self.codex_home / INSTALLED_NODE_RUNTIME_RELATIVE / "package.json").exists())
        self.assertTrue((self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js").exists())
        self.assertTrue((self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "mcp-server.js").exists())
        self.assertTrue((self.codex_home / CODEX_INSTALL_STATE_RELATIVE).exists())
        manifest = json.loads((self.codex_home / CODEX_INSTALL_STATE_RELATIVE).read_text(encoding="utf-8"))
        self.assertIn("dreamers/runtime/dreamers_mcp_node/package.json", manifest["files"])
        self.assertIn("dreamers/runtime/dreamers_mcp_node/dist/cli.js", manifest["files"])
        self.assertIn("dreamers/runtime/dreamers_mcp_node/dist/mcp-server.js", manifest["files"])
        self.assertIn("dreamers/scripts/dreamers_node_launcher.py", manifest["files"])
        self.assertTrue((self.codex_home / CODEX_STATS_REF_RELATIVE).exists())
        self.assertEqual('{"event_id":"historic"}\n', historic_events.read_text(encoding="utf-8"))

        hooks_payload = json.loads(hooks_config.read_text(encoding="utf-8"))
        self.assertEqual("echo keep-user-hook", hooks_payload["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])
        session_commands = [
            handler["command"]
            for group in hooks_payload["hooks"]["SessionStart"]
            for handler in group["hooks"]
        ]
        self.assertTrue(any("dreamers_hook.sh SessionStart" in command for command in session_commands))

        config_text = config_toml.read_text(encoding="utf-8")
        self.assertIn('[mcp_servers.keep]', config_text)
        self.assertIn('[mcp_servers.dreamers_stats]', config_text)
        agents_text = agents_path.read_text(encoding="utf-8")
        self.assertIn("Keep this line.", agents_text)
        self.assertIn("BEGIN DREAMERS MCP CODEX STATS", agents_text)
        self.assertIn("dreamers-mcp-stats.md", agents_text)
        self.assertIn("starts with `dreamers-`", agents_text)
        ref_text = (self.codex_home / CODEX_STATS_REF_RELATIVE).read_text(encoding="utf-8")
        self.assertIn("<dreamers-mcp-skill-bookends>", ref_text)
        self.assertIn("skill_started", ref_text)
        self.assertIn("validation_attempt", ref_text)
        self.assertIn("gate_decided", ref_text)
        self.assertIn("skill_completed", ref_text)
        self.assertIn("skill_halted", ref_text)
        self.assertIn("approved_start_implementation", ref_text)
        self.assertIn("mcp__dreamers_stats.record_checkpoint", ref_text)
        self.assertIn("record_checkpoint", ref_text)
        self.assertNotIn("dreamers_stats.py", ref_text)
        self.assertNotIn("checkpoint --client", ref_text)
        self.assertNotIn("py -3", ref_text)

        removed = self.run_shell_script(
            CODEX_BASH_REMOVER_PATH,
            "--codex-home",
            str(self.codex_home),
        )

        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertFalse((self.codex_home / CODEX_BASH_WRAPPER_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_POWERSHELL_WRAPPER_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_SHIM_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_MCP_SERVER_SHIM_RELATIVE).exists())
        self.assertFalse((self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE).exists())
        self.assertFalse((self.codex_home / INSTALLED_NODE_RUNTIME_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_INSTALL_STATE_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_STATS_REF_RELATIVE).exists())
        self.assertEqual('{"event_id":"historic"}\n', historic_events.read_text(encoding="utf-8"))

        hooks_after = json.loads(hooks_config.read_text(encoding="utf-8"))
        self.assertEqual("echo keep-user-hook", hooks_after["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])
        self.assertNotIn("SessionStart", hooks_after["hooks"])

        config_after = config_toml.read_text(encoding="utf-8")
        self.assertIn('[mcp_servers.keep]', config_after)
        self.assertNotIn('[mcp_servers.dreamers_stats]', config_after)
        self.assertEqual(original_agents, agents_path.read_text(encoding="utf-8"))

    def test_bash_install_leaves_unsafe_config_unchanged_and_prints_manual_steps(self):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        hooks_config = self.codex_home / CODEX_HOOKS_CONFIG_RELATIVE
        hooks_config.write_text('{"hooks": [}\n', encoding="utf-8")
        config_toml = self.codex_home / CODEX_MCP_CONFIG_RELATIVE
        config_toml.write_text(
            '[mcp_servers.dreamers_stats]\ncommand = "custom-dreamers"\n',
            encoding="utf-8",
        )

        completed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual('{"hooks": [}\n', hooks_config.read_text(encoding="utf-8"))
        self.assertEqual(
            '[mcp_servers.dreamers_stats]\ncommand = "custom-dreamers"\n',
            config_toml.read_text(encoding="utf-8"),
        )
        combined_output = f"{completed.stdout}\n{completed.stderr}"
        self.assertIn("Manual hook registration", combined_output)
        self.assertIn("Manual MCP registration", combined_output)
        self.assertTrue((self.codex_home / CODEX_BASH_WRAPPER_RELATIVE).exists())

    def test_remove_preserves_user_modified_stats_ref(self):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        installed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        stats_ref = self.codex_home / CODEX_STATS_REF_RELATIVE
        stats_ref.write_text("# user-modified\n", encoding="utf-8")
        node_cli = self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js"
        node_cli.write_text("// user-modified\n", encoding="utf-8")

        removed = self.run_shell_script(
            CODEX_BASH_REMOVER_PATH,
            "--codex-home",
            str(self.codex_home),
        )

        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertTrue(stats_ref.exists())
        self.assertEqual("# user-modified\n", stats_ref.read_text(encoding="utf-8"))
        self.assertTrue(node_cli.exists())
        self.assertEqual("// user-modified\n", node_cli.read_text(encoding="utf-8"))
        self.assertFalse((self.codex_home / CODEX_AGENTS_CONFIG_RELATIVE).exists())

    def test_installed_wrapper_records_safe_codex_events_and_mcp_server_reports_exact_tokens(self):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        installed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)
        self.assertTrue((self.codex_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js").exists())
        shutil.rmtree(self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)

        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)

        prompt_event = self.run_shell_script(
            self.codex_home / CODEX_BASH_WRAPPER_RELATIVE,
            "UserPromptSubmit",
            input_text=json.dumps(
                {
                    "cwd": str(self.fixture_repo),
                    "prompt": "secret prompt text",
                    "timestamp": 1_718_302_420_000,
                    "turn_id": "turn_01",
                }
            ),
            env=env,
        )
        self.assertEqual(0, prompt_event.returncode, prompt_event.stderr)

        session_path = (
            self.codex_home
            / "sessions"
            / "2026"
            / "06"
            / "15"
            / "rollout-2026-06-15T00-00-00-session_bundle.jsonl"
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "event_msg", "payload": {"type": "user_prompt", "text": "secret prompt text"}}),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 300,
                                        "cached_input_tokens": 250,
                                        "output_tokens": 40,
                                        "total_tokens": 340,
                                    }
                                },
                            },
                        }
                    ),
                    json.dumps({"type": "event_msg", "payload": {"type": "assistant_message", "text": "secret assistant text"}}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        stop_event = self.run_shell_script(
            self.codex_home / CODEX_BASH_WRAPPER_RELATIVE,
            "Stop",
            input_text=json.dumps(
                {
                    "cwd": str(self.fixture_repo),
                    "timestamp": 1_718_302_520_000,
                    "turn_id": "turn_01",
                    "session_id": "session_bundle",
                    "last_assistant_message": "secret assistant text",
                    "stop_hook_active": False,
                }
            ),
            env=env,
        )
        self.assertEqual(0, stop_event.returncode, stop_event.stderr)

        raw_line = self.events_file(self.codex_home).read_text(encoding="utf-8")
        self.assertNotIn("secret prompt text", raw_line)
        self.assertNotIn("secret assistant text", raw_line)

        self.record_fixture_run(client="codex", home=self.codex_home)
        shim = self.run_python_script(
            self.codex_home / CODEX_SHIM_RELATIVE,
            "runs",
            "--repo",
            "current",
            "--json",
            cwd=self.fixture_repo,
            env=env,
        )
        self.assertEqual(0, shim.returncode, shim.stderr)
        self.assertEqual("runs", json.loads(shim.stdout)["report_type"])

        server_input = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "tokens",
                            "arguments": {
                                "client": "codex",
                                "home": str(self.codex_home),
                                "repo": "all",
                            },
                        },
                    }
                ),
            ]
        )
        server = self.run_subprocess(
            [shutil.which("python3") or shutil.which("python") or "python3", str(self.codex_home / CODEX_MCP_SERVER_SHIM_RELATIVE)],
            input_text=f"{server_input}\n",
            env=env,
        )
        self.assertEqual(0, server.returncode, server.stderr)
        responses = [json.loads(line) for line in server.stdout.splitlines() if line.strip()]
        tokens_response = responses[-1]["result"]["structuredContent"]
        self.assertEqual("tokens", tokens_response["report_type"])
        self.assertEqual(1, tokens_response["exact"]["row_count"])
        self.assertEqual(0, tokens_response["unavailable"]["row_count"])
        self.assertEqual(340, tokens_response["exact"]["totals"]["total_tokens"])
        self.assertEqual(250, tokens_response["exact"]["totals"]["cache_read_tokens"])

    def test_installed_codex_shims_honor_dreamers_mcp_home_override(self):
        installed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        alt_runtime = self.make_alt_node_runtime()
        shutil.rmtree(self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)
        shutil.rmtree(self.codex_home / INSTALLED_NODE_RUNTIME_RELATIVE)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        env["DREAMERS_MCP_HOME"] = str(alt_runtime)

        shim = self.run_python_script(
            self.codex_home / CODEX_SHIM_RELATIVE,
            "doctor",
            "--json",
            env=env,
        )
        self.assertEqual(0, shim.returncode, shim.stderr)
        self.assertEqual(str(self.events_file(self.codex_home)), json.loads(shim.stdout)["events_file"])

        server_input = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "doctor",
                            "arguments": {"client": "codex", "home": str(self.codex_home)},
                        },
                    }
                ),
            ]
        )
        server = self.run_subprocess(
            [shutil.which("python3") or shutil.which("python") or "python3", str(self.codex_home / CODEX_MCP_SERVER_SHIM_RELATIVE)],
            input_text=f"{server_input}\n",
            env=env,
        )
        self.assertEqual(0, server.returncode, server.stderr)
        responses = [json.loads(line) for line in server.stdout.splitlines() if line.strip()]
        self.assertEqual(str(self.events_file(self.codex_home)), responses[-1]["result"]["structuredContent"]["events_file"])

    def test_powershell_hook_honors_dreamers_mcp_home_override(self):
        installed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        alt_runtime = self.make_alt_node_runtime()
        shutil.rmtree(self.codex_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)
        shutil.rmtree(self.codex_home / INSTALLED_NODE_RUNTIME_RELATIVE)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        env["DREAMERS_MCP_HOME"] = str(alt_runtime)

        completed = self.run_powershell_script(
            self.codex_home / CODEX_POWERSHELL_WRAPPER_RELATIVE,
            "UserPromptSubmit",
            input_text=json.dumps(
                {
                    "cwd": str(self.fixture_repo),
                    "prompt": "/dreamers-full",
                    "timestamp": 1_718_302_420_000,
                    "turn_id": "turn_ps",
                }
            ),
            env=env,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        stored = self.read_events(self.codex_home)[0]
        self.assertEqual("prompt_submitted", stored["event_type"])

    def test_install_skips_config_when_required_same_path_asset_is_user_owned(self):
        scripts_dir = self.codex_home / "dreamers" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "dreamers_hook.sh").write_text("#!/usr/bin/env bash\necho user-owned\n", encoding="utf-8")

        completed = self.run_shell_script(
            CODEX_BASH_INSTALLER_PATH,
            "--codex-home",
            str(self.codex_home),
            "--dreamers-mcp-path",
            str(REPO_ROOT),
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertFalse((self.codex_home / CODEX_HOOKS_CONFIG_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_MCP_CONFIG_RELATIVE).exists())
        self.assertFalse((self.codex_home / CODEX_AGENTS_CONFIG_RELATIVE).exists())
        self.assertIn("required bundle assets were not installed cleanly", completed.stdout)

    def test_powershell_install_and_remove_preserve_history_and_user_config(self):
        historic_events = self.events_file(self.codex_home)
        historic_events.parent.mkdir(parents=True, exist_ok=True)
        historic_events.write_text('{"event_id":"historic"}\n', encoding="utf-8")
        self.codex_home.mkdir(parents=True, exist_ok=True)

        hooks_config = self.codex_home / CODEX_HOOKS_CONFIG_RELATIVE
        hooks_config.write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}),
            encoding="utf-8",
        )

        installed = self.run_powershell_script(
            CODEX_POWERSHELL_INSTALLER_PATH,
            "-CodexHome",
            str(self.codex_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)
        self.assertTrue((self.codex_home / CODEX_INSTALL_STATE_RELATIVE).exists())

        removed = self.run_powershell_script(
            CODEX_POWERSHELL_REMOVER_PATH,
            "-CodexHome",
            str(self.codex_home),
        )
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertEqual('{"event_id":"historic"}\n', historic_events.read_text(encoding="utf-8"))
