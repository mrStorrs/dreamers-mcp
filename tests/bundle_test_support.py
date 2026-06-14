import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dreamers_stats import runtime
from dreamers_stats.cli import main as cli_main


REPO_ROOT = Path(__file__).resolve().parents[1]
COPILOT_INSTALLER_PATH = REPO_ROOT / "Install-DreamersMcpCopilot.ps1"
COPILOT_REMOVER_PATH = REPO_ROOT / "Remove-DreamersMcpCopilot.ps1"
CODEX_BASH_INSTALLER_PATH = REPO_ROOT / "Install-DreamersMcpCodex.sh"
CODEX_BASH_REMOVER_PATH = REPO_ROOT / "Remove-DreamersMcpCodex.sh"
CODEX_POWERSHELL_INSTALLER_PATH = REPO_ROOT / "Install-DreamersMcpCodex.ps1"
CODEX_POWERSHELL_REMOVER_PATH = REPO_ROOT / "Remove-DreamersMcpCodex.ps1"
COPILOT_BASH_WRAPPER_RELATIVE = Path("dreamers") / "scripts" / "dreamers_hook.sh"
COPILOT_POWERSHELL_WRAPPER_RELATIVE = Path("dreamers") / "scripts" / "dreamers_hook.ps1"
COPILOT_SHIM_RELATIVE = Path("dreamers") / "scripts" / "dreamers_stats.py"
COPILOT_HOOK_CONFIG_RELATIVE = Path("hooks") / "dreamers-stats.json"
INSTALLED_RUNTIME_PACKAGE_RELATIVE = Path("dreamers") / "runtime" / "dreamers_stats"
RUNTIME_INSTALL_STATE_RELATIVE = Path("dreamers") / "install-state" / "runtime-hooks.txt"
CODEX_BASH_WRAPPER_RELATIVE = Path("dreamers") / "scripts" / "dreamers_hook.sh"
CODEX_POWERSHELL_WRAPPER_RELATIVE = Path("dreamers") / "scripts" / "dreamers_hook.ps1"
CODEX_SHIM_RELATIVE = Path("dreamers") / "scripts" / "dreamers_stats.py"
CODEX_MCP_SERVER_SHIM_RELATIVE = Path("dreamers") / "scripts" / "dreamers_mcp_server.py"
CODEX_INSTALL_STATE_RELATIVE = Path("dreamers") / "install-state" / "codex-bundle.json"
CODEX_HOOKS_CONFIG_RELATIVE = Path("hooks.json")
CODEX_MCP_CONFIG_RELATIVE = Path("config.toml")


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


class BundleTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copilot_home = Path(self.tmp.name) / "copilot-home"
        self.codex_home = Path(self.tmp.name) / "codex-home"
        self.fixture_repo = Path(self.tmp.name) / "fixture-repo"
        (self.fixture_repo / ".git").mkdir(parents=True)

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

    def events_file(self, home):
        return Path(home) / "dreamers" / "stats" / "events.jsonl"

    def read_events(self, home):
        return [
            json.loads(line)
            for line in self.events_file(home).read_text(encoding="utf-8").splitlines()
        ]

    def read_manifest(self, home):
        manifest_path = Path(home) / RUNTIME_INSTALL_STATE_RELATIVE
        entries = {}
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            path_key, hash_value = line.split("|", 1)
            entries[path_key] = hash_value
        return entries

    def powershell_command(self):
        if shutil.which("pwsh"):
            return ["pwsh", "-NoLogo", "-NoProfile", "-File"]
        if shutil.which("powershell"):
            return ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        self.skipTest("PowerShell is not available in this environment")

    def run_powershell_script(self, script_path, *args, input_text="", env=None, cwd=None):
        command = [*self.powershell_command(), str(script_path), *args]
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=env,
        )

    def run_python_script(self, script_path, *args, input_text="", env=None, cwd=None):
        command = [sys.executable, str(script_path), *args]
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=env,
        )

    def run_shell_script(self, script_path, *args, input_text="", env=None, cwd=None):
        command = ["bash", str(script_path), *args]
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=env,
        )

    def run_subprocess(self, command, *, input_text="", env=None, cwd=None):
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            cwd=REPO_ROOT if cwd is None else cwd,
            env=env,
        )

    def record_fixture_run(self, *, client="copilot", home=None):
        target_home = self.copilot_home if home is None else Path(home)
        runtime.record_event(
            valid_event(
                event_id="evt_run_start",
                timestamp="2026-06-13T10:00:00Z",
                event_type="skill_started",
                repo_path=str(self.fixture_repo),
                run_id="run_01",
                skill="dreamers-full",
                metrics={"mode": "manifest", "plan_count": 2},
            ),
            client=client,
            home=target_home,
        )
        runtime.record_event(
            valid_event(
                event_id="evt_run_end",
                timestamp="2026-06-13T10:05:00Z",
                event_type="skill_completed",
                repo_path=str(self.fixture_repo),
                run_id="run_01",
                skill="dreamers-full",
                status="completed",
                metrics={"final_status": "completed", "plan_count": 2},
            ),
            client=client,
            home=target_home,
        )
