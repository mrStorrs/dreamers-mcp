import hashlib
import json
import os
import shutil
from pathlib import Path

from tests.bundle_test_support import (
    COPILOT_BASH_WRAPPER_RELATIVE,
    COPILOT_HOOK_CONFIG_RELATIVE,
    COPILOT_INSTALLER_PATH,
    COPILOT_POWERSHELL_WRAPPER_RELATIVE,
    COPILOT_REMOVER_PATH,
    COPILOT_SHIM_RELATIVE,
    INSTALLED_NODE_DIST_RELATIVE,
    INSTALLED_NODE_RUNTIME_RELATIVE,
    INSTALLED_RUNTIME_PACKAGE_RELATIVE,
    RUNTIME_INSTALL_STATE_RELATIVE,
    BundleTestCase,
    REPO_ROOT,
)


def sha256_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CopilotBundleTests(BundleTestCase):
    def test_install_and_remove_preserve_history_and_user_owned_assets(self):
        historic_events = self.events_file(self.copilot_home)
        historic_events.parent.mkdir(parents=True, exist_ok=True)
        historic_events.write_text('{"event_id":"historic"}\n', encoding="utf-8")

        user_hook = self.copilot_home / "hooks" / "user-hook.json"
        user_hook.parent.mkdir(parents=True, exist_ok=True)
        user_hook.write_text('{"version":1,"hooks":{"sessionStart":[]}}\n', encoding="utf-8")

        completed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue((self.copilot_home / COPILOT_HOOK_CONFIG_RELATIVE).exists())
        self.assertTrue((self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE).exists())
        self.assertTrue((self.copilot_home / COPILOT_POWERSHELL_WRAPPER_RELATIVE).exists())
        self.assertTrue((self.copilot_home / COPILOT_SHIM_RELATIVE).exists())
        self.assertTrue((self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE / "runtime.py").exists())
        self.assertTrue((self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE / "package.json").exists())
        self.assertTrue((self.copilot_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js").exists())
        self.assertTrue((self.copilot_home / RUNTIME_INSTALL_STATE_RELATIVE).exists())
        manifest = self.read_manifest(self.copilot_home)
        self.assertIn("dreamers/runtime/dreamers_mcp_node/package.json", manifest)
        self.assertIn("dreamers/runtime/dreamers_mcp_node/dist/cli.js", manifest)
        self.assertIn("dreamers/scripts/dreamers_node_launcher.py", manifest)
        self.assertEqual('{"event_id":"historic"}\n', historic_events.read_text(encoding="utf-8"))
        self.assertTrue(user_hook.exists())

        removed = self.run_powershell_script(
            COPILOT_REMOVER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
        )

        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertFalse((self.copilot_home / COPILOT_HOOK_CONFIG_RELATIVE).exists())
        self.assertFalse((self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE).exists())
        self.assertFalse((self.copilot_home / COPILOT_POWERSHELL_WRAPPER_RELATIVE).exists())
        self.assertFalse((self.copilot_home / COPILOT_SHIM_RELATIVE).exists())
        self.assertFalse((self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE).exists())
        self.assertFalse((self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE).exists())
        self.assertFalse((self.copilot_home / RUNTIME_INSTALL_STATE_RELATIVE).exists())
        self.assertEqual('{"event_id":"historic"}\n', historic_events.read_text(encoding="utf-8"))
        self.assertTrue(user_hook.exists())

    def test_remove_preserves_user_modified_managed_asset(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        bash_wrapper = self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE
        hook_config = self.copilot_home / COPILOT_HOOK_CONFIG_RELATIVE
        node_cli = self.copilot_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js"
        bash_wrapper.write_text("#!/usr/bin/env bash\necho user-modified\n", encoding="utf-8")
        node_cli.write_text("// user-modified\n", encoding="utf-8")

        removed = self.run_powershell_script(
            COPILOT_REMOVER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
        )

        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertTrue(bash_wrapper.exists())
        self.assertTrue(node_cli.exists())
        self.assertEqual("// user-modified\n", node_cli.read_text(encoding="utf-8"))
        self.assertFalse(hook_config.exists())
        self.assertIn("modified or user-owned", removed.stdout)

    def test_reinstall_without_force_refreshes_manifest_to_current_ownership(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        bash_wrapper = self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE
        bash_wrapper.write_text("#!/usr/bin/env bash\necho user-modified\n", encoding="utf-8")

        reinstalled = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, reinstalled.returncode, reinstalled.stderr)

        manifest = self.read_manifest(self.copilot_home)
        self.assertNotIn("dreamers/scripts/dreamers_hook.sh", manifest)
        self.assertIn("dreamers/scripts/dreamers_stats.py", manifest)
        self.assertIn("dreamers/scripts/dreamers_node_launcher.py", manifest)
        self.assertIn("dreamers/runtime/dreamers_mcp_node/dist/cli.js", manifest)
        self.assertIn("hooks/dreamers-stats.json", manifest)

        removed = self.run_powershell_script(
            COPILOT_REMOVER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
        )
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertTrue(bash_wrapper.exists())
        self.assertFalse((self.copilot_home / COPILOT_SHIM_RELATIVE).exists())

    def test_reinstall_without_force_refreshes_managed_node_assets_when_source_changes(self):
        checkout_root = self.fixture_repo / "checkout"
        shutil.copytree(REPO_ROOT / "dreamers_stats", checkout_root / "dreamers_stats")
        shutil.copytree(REPO_ROOT / "bundles", checkout_root / "bundles")
        shutil.copytree(REPO_ROOT / "dist", checkout_root / "dist")
        shutil.copy2(REPO_ROOT / "package.json", checkout_root / "package.json")

        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(checkout_root),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        source_cli = checkout_root / "dist" / "cli.js"
        source_cli.write_text(source_cli.read_text(encoding="utf-8") + "\n// updated source\n", encoding="utf-8")
        source_package = checkout_root / "package.json"
        package_payload = json.loads(source_package.read_text(encoding="utf-8"))
        package_payload["description"] = "updated source package"
        source_package.write_text(json.dumps(package_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        reinstalled = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(checkout_root),
        )
        self.assertEqual(0, reinstalled.returncode, reinstalled.stderr)

        installed_cli = self.copilot_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js"
        installed_package = self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE / "package.json"
        self.assertEqual(source_cli.read_text(encoding="utf-8"), installed_cli.read_text(encoding="utf-8"))
        self.assertEqual(source_package.read_text(encoding="utf-8"), installed_package.read_text(encoding="utf-8"))
        manifest = self.read_manifest(self.copilot_home)
        self.assertEqual(
            sha256_file(source_cli),
            manifest["dreamers/runtime/dreamers_mcp_node/dist/cli.js"],
        )
        self.assertEqual(
            sha256_file(source_package),
            manifest["dreamers/runtime/dreamers_mcp_node/package.json"],
        )

        removed = self.run_powershell_script(
            COPILOT_REMOVER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
        )
        self.assertEqual(0, removed.returncode, removed.stderr)
        self.assertFalse(installed_cli.exists())
        self.assertFalse(installed_package.exists())
        self.assertFalse((self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE).exists())

    def test_reinstall_whatif_does_not_refresh_managed_node_assets(self):
        checkout_root = self.fixture_repo / "checkout"
        shutil.copytree(REPO_ROOT / "dreamers_stats", checkout_root / "dreamers_stats")
        shutil.copytree(REPO_ROOT / "bundles", checkout_root / "bundles")
        shutil.copytree(REPO_ROOT / "dist", checkout_root / "dist")
        shutil.copy2(REPO_ROOT / "package.json", checkout_root / "package.json")

        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(checkout_root),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        installed_cli = self.copilot_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js"
        installed_package = self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE / "package.json"
        original_cli = installed_cli.read_text(encoding="utf-8")
        original_package = installed_package.read_text(encoding="utf-8")

        (checkout_root / "dist" / "cli.js").write_text(original_cli + "\n// updated source\n", encoding="utf-8")
        package_payload = json.loads((checkout_root / "package.json").read_text(encoding="utf-8"))
        package_payload["description"] = "whatif updated source package"
        (checkout_root / "package.json").write_text(json.dumps(package_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        dry_run = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(checkout_root),
            "-WhatIf",
        )
        self.assertEqual(0, dry_run.returncode, dry_run.stderr)
        self.assertEqual(original_cli, installed_cli.read_text(encoding="utf-8"))
        self.assertEqual(original_package, installed_package.read_text(encoding="utf-8"))

    def test_installed_wrapper_records_safe_event_and_shim_runs_reports(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)
        self.assertTrue((self.copilot_home / INSTALLED_NODE_DIST_RELATIVE / "cli.js").exists())
        shutil.rmtree(self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)

        payload = {
            "sessionId": "sess_01",
            "timestamp": 1_718_302_420_000,
            "cwd": str(self.fixture_repo),
            "prompt": "/dreamers-full investigate hooks",
            "authorization": "Bearer abc1234567890",
        }
        env = os.environ.copy()
        env["COPILOT_HOME"] = str(self.copilot_home)
        completed = self.run_shell_script(
            self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE,
            "userPromptSubmitted",
            input_text=json.dumps(payload),
            env=env,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout)

        stored = self.read_events(self.copilot_home)[0]
        self.assertEqual("prompt_submitted", stored["event_type"])
        self.assertEqual("hook", stored["source"])
        self.assertEqual(1, stored["metrics"]["prompt_count"])
        raw_line = self.events_file(self.copilot_home).read_text(encoding="utf-8")
        self.assertNotIn("/dreamers-full investigate hooks", raw_line)
        self.assertNotIn("abc1234567890", raw_line)

        self.record_fixture_run(home=self.copilot_home)
        shim = self.run_python_script(
            self.copilot_home / COPILOT_SHIM_RELATIVE,
            "runs",
            "--repo",
            "current",
            "--json",
            cwd=self.fixture_repo,
            env=env,
        )
        self.assertEqual(0, shim.returncode, shim.stderr)
        self.assertEqual("runs", json.loads(shim.stdout)["report_type"])

        summary = self.run_python_script(
            self.copilot_home / COPILOT_SHIM_RELATIVE,
            "summarize",
            "--repo",
            "current",
            "--json",
            cwd=self.fixture_repo,
            env=env,
        )
        self.assertEqual(0, summary.returncode, summary.stderr)
        self.assertIn("workflow_outputs", json.loads(summary.stdout))

        summary_text = self.run_python_script(
            self.copilot_home / COPILOT_SHIM_RELATIVE,
            "summarize",
            "--repo",
            "current",
            cwd=self.fixture_repo,
            env=env,
        )
        self.assertEqual(0, summary_text.returncode, summary_text.stderr)
        self.assertIn("Workflow outputs", summary_text.stdout)

    def test_installed_copilot_shim_honors_dreamers_mcp_home_override(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        alt_runtime = self.make_alt_node_runtime()
        shutil.rmtree(self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)
        shutil.rmtree(self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE)
        env = os.environ.copy()
        env["COPILOT_HOME"] = str(self.copilot_home)
        env["DREAMERS_MCP_HOME"] = str(alt_runtime)

        shim = self.run_python_script(
            self.copilot_home / COPILOT_SHIM_RELATIVE,
            "doctor",
            "--json",
            env=env,
        )
        self.assertEqual(0, shim.returncode, shim.stderr)
        self.assertEqual(str(self.events_file(self.copilot_home)), json.loads(shim.stdout)["events_file"])

    def test_powershell_hook_honors_dreamers_mcp_home_override(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        alt_runtime = self.make_alt_node_runtime()
        shutil.rmtree(self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)
        shutil.rmtree(self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE)
        env = os.environ.copy()
        env["COPILOT_HOME"] = str(self.copilot_home)
        env["DREAMERS_MCP_HOME"] = str(alt_runtime)

        completed = self.run_powershell_script(
            self.copilot_home / COPILOT_POWERSHELL_WRAPPER_RELATIVE,
            "userPromptSubmitted",
            input_text=json.dumps(
                {
                    "sessionId": "sess_ps",
                    "timestamp": 1_718_302_420_000,
                    "cwd": str(self.fixture_repo),
                    "prompt": "/dreamers-full",
                }
            ),
            env=env,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        stored = self.read_events(self.copilot_home)[0]
        self.assertEqual("prompt_submitted", stored["event_type"])

    def test_installed_wrapper_warns_without_blocking_when_runtime_missing(self):
        installed = self.run_powershell_script(
            COPILOT_INSTALLER_PATH,
            "-CopilotHome",
            str(self.copilot_home),
            "-DreamersMcpPath",
            str(REPO_ROOT),
        )
        self.assertEqual(0, installed.returncode, installed.stderr)

        shutil.rmtree(self.copilot_home / INSTALLED_RUNTIME_PACKAGE_RELATIVE)
        shutil.rmtree(self.copilot_home / INSTALLED_NODE_RUNTIME_RELATIVE)
        env = os.environ.copy()
        env["COPILOT_HOME"] = str(self.copilot_home)

        completed = self.run_shell_script(
            self.copilot_home / COPILOT_BASH_WRAPPER_RELATIVE,
            "sessionStart",
            input_text=json.dumps(
                {
                    "sessionId": "sess_01",
                    "timestamp": 1_718_302_400_000,
                    "cwd": str(self.fixture_repo),
                    "source": "new",
                }
            ),
            env=env,
        )

        self.assertEqual(0, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("dreamers hook warning", completed.stderr)
        self.assertFalse(self.events_file(self.copilot_home).exists())
