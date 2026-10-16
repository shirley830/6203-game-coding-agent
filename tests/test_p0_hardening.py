"""P0 hardening: retry backoff, startup self-check, agent.env configuration."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.__main__ import build_parser, main
from agent.config import AgentConfig
from agent.envfile import load_env_file, parse_env_file
from agent.planning.prompts import build_decision_prompt
from agent.preflight import PreflightReport, run_preflight
from agent.state import (
    CallLimits, ExecutionContext, Phase, RecoveryDecision, RecoveryKind, ToolAction,
    ToolKind, ToolStatus,
)
from agent.tools import LocalRuntime, RuntimeSettings
from tests.test_controller import Scenario
from tests.test_integration_tasks import view


class RetryBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = self.root / "game_design_prompt.md"
        self.spec.write_text("# Test specification", encoding="utf-8")

    async def test_retry_waits_and_records_the_wait(self):
        scenario = Scenario()
        scenario.runtime_error_once = True
        scenario.recoveries = [RecoveryDecision(RecoveryKind.RETRY, "transient", True)]
        from agent.controller import Controller
        controller = Controller(AgentConfig(self.root, retry_backoff_seconds=0.05),
                                scenario.dependencies())
        summary = await controller.run(self.spec)
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        waits = [e.payload for e in scenario.events if e.kind == "retry_backoff"]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["delay_seconds"], 0.05)

    async def test_backoff_grows_and_is_capped(self):
        from agent.controller import Controller
        controller = Controller(AgentConfig(self.root, retry_backoff_seconds=5.0,
                                            max_runtime_seconds=900),
                                Scenario().dependencies())
        controller._started = __import__("time").monotonic()
        controller._event = AsyncMock()
        with patch("agent.controller.asyncio.sleep", new=AsyncMock()) as sleep:
            for attempt in range(1, 6):
                await controller._retry_backoff(attempt, "planner.next_decision")
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [5, 15, 45, 60, 60])

    async def test_backoff_never_exceeds_remaining_time(self):
        from agent.controller import Controller
        controller = Controller(AgentConfig(self.root, retry_backoff_seconds=50.0,
                                            max_runtime_seconds=30,
                                            finalization_reserve_seconds=10),
                                Scenario().dependencies())
        controller._started = __import__("time").monotonic()
        controller._event = AsyncMock()
        with patch("agent.controller.asyncio.sleep", new=AsyncMock()) as sleep:
            await controller._retry_backoff(1, "planner.create_plan")
        self.assertLess(sleep.await_args.args[0], 20)

    def test_config_rejects_negative_backoff_and_cli_defaults(self):
        with self.assertRaises(ValueError):
            AgentConfig(self.root, retry_backoff_seconds=-1)
        args = build_parser().parse_args(["spec.md"])
        self.assertEqual(args.retry_backoff, 5.0)
        self.assertEqual(args.max_retries, 5)


def capture_factory(config):
    raise RuntimeError("captured AGENT_MODEL=" + os.environ.get("AGENT_MODEL", ""))


class EnvFileTests(unittest.TestCase):
    def test_parse_formats(self):
        values = parse_env_file(
            "# comment\n\nexport AGENT_MODEL=openai/x\nAGENT_API_BASE='http://h/v1'\n"
            'OPENAI_API_KEY="k=1"\nAGENT_MAX_TOKENS=8000 # trailing\n')
        self.assertEqual(values, {"AGENT_MODEL": "openai/x", "AGENT_API_BASE": "http://h/v1",
                                  "OPENAI_API_KEY": "k=1", "AGENT_MAX_TOKENS": "8000"})
        with self.assertRaises(ValueError):
            parse_env_file("not a setting\n")

    def test_shell_variables_win_and_values_are_not_reported(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AGENT_MODEL": "from-shell"}, clear=False):
            path = Path(directory) / "agent.env"
            path.write_text("AGENT_MODEL=from-file\nPE6203_TEST_KEY=secret\n", encoding="utf-8")
            loaded = load_env_file(path)
            self.assertEqual(loaded, ["PE6203_TEST_KEY"])
            self.assertEqual(os.environ["AGENT_MODEL"], "from-shell")
            os.environ.pop("PE6203_TEST_KEY")

    def test_cli_reads_agent_env_from_run_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game_design_prompt.md").write_text("# g", encoding="utf-8")
            (root / "agent.env").write_text("AGENT_MODEL=openai/from-env-file\n", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "AGENT_MODEL"}
            with patch.dict(os.environ, env, clear=True), \
                    patch("sys.stderr") as stderr:
                code = main([str(root / "game_design_prompt.md"), "--run-root", directory,
                             "--factory", "tests.test_p0_hardening:capture_factory"])
            printed = "".join(str(c.args[0]) for c in stderr.write.call_args_list)
            self.assertEqual(code, 2)
            self.assertIn("captured AGENT_MODEL=openai/from-env-file", printed)
            self.assertIn("Loaded AGENT_MODEL", printed)
            self.assertNotIn("openai/from-env-file from", printed)
            self.assertFalse((root / "game").exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox")
    def test_sandboxed_commands_cannot_read_agent_env(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spec.md").write_text("# s", encoding="utf-8")
            (root / "agent.env").write_text("OPENAI_API_KEY=top-secret\n", encoding="utf-8")
            context = ExecutionContext("r", root, root / "spec.md", root / "game", root / "traces" / "r")
            runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=True))
            limits = CallLimits(30, 10, 10)

            async def scenario():
                await runtime.execute(ToolAction("p", ToolKind.PREPARE_WORKSPACE), context, limits)
                return await runtime.execute(ToolAction("c", ToolKind.RUN_COMMAND, {"argv": [
                    sys.executable, "-c", f"print(open({str(root / 'agent.env')!r}).read())"]}),
                    context, limits)

            result = asyncio.run(scenario())
            self.assertEqual(result.status, ToolStatus.ERROR)
            self.assertNotIn("top-secret", result.observation)


class PreflightTests(unittest.TestCase):
    def test_busy_port_blocks_and_missing_node_is_a_note(self):
        with patch("agent.preflight._port_free", return_value=False), \
                patch("agent.preflight._check_sandbox"), patch("agent.preflight._check_browser"), \
                patch("agent.preflight.shutil.which", side_effect=lambda name: None
                      if name in {"node", "npm"} else "/usr/bin/" + name):
            report = run_preflight()
        self.assertFalse(report.ok)
        self.assertTrue(any("8000" in item for item in report.blocking))
        self.assertTrue(any("node is NOT installed" in item for item in report.notes))

    def test_factory_stops_before_development_when_blocked(self):
        from agent import integrations
        blocked = PreflightReport(blocking=["Port 8000 is already in use"])
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AGENT_MODEL": "openai/x"}), \
                patch.object(integrations, "run_preflight", return_value=blocked), \
                patch("sys.stderr"):
            os.environ.pop("AGENT_SKIP_PREFLIGHT", None)
            with self.assertRaisesRegex(RuntimeError, "self-check failed"):
                integrations.create_dependencies(AgentConfig(Path(directory)))

    def test_notes_reach_the_planner_prompt(self):
        from agent import integrations
        ready = PreflightReport(notes=["node is NOT installed: do not plan commands that use node"])
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AGENT_MODEL": "openai/x"}), \
                patch.object(integrations, "run_preflight", return_value=ready), \
                patch("sys.stderr"):
            os.environ.pop("AGENT_SKIP_PREFLIGHT", None)
            deps = integrations.create_dependencies(AgentConfig(Path(directory)))
        self.assertEqual(deps.planner.environment_notes, ready.notes)
        prompt = build_decision_prompt(view(), environment=deps.planner.environment_notes)
        self.assertIn("MACHINE ENVIRONMENT", prompt)
        self.assertIn("node is NOT installed", prompt)
        self.assertNotIn('"node"', prompt)



class LiteLLMOutputTests(unittest.TestCase):
    def test_litellm_banner_cannot_corrupt_cli_json(self):
        import litellm
        import agent.planning.llm_client  # noqa: F401
        self.assertTrue(litellm.suppress_debug_info)


if __name__ == "__main__":
    unittest.main()
