import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent.__main__ import main
from agent.config import AgentConfig
from agent.state import ToolAction, ToolKind, to_jsonable


class ConfigCliTests(unittest.TestCase):
    def test_invalid_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            for kwargs in ({"max_steps": 0}, {"max_commands": True},
                {"max_retries_per_operation": -1}, {"max_runtime_seconds": float("nan")},
                {"model_call_timeout_seconds": float("inf")},
                {"finalization_reserve_seconds": 900}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    AgentConfig(Path(directory), **kwargs)

    def test_serialization_and_recursive_freeze(self):
        source = {"argv": ["fake"]}
        action = ToolAction("a", ToolKind.RUN_COMMAND, source)
        source["argv"].append("changed")
        self.assertEqual(action.arguments["argv"], ("fake",))
        self.assertEqual(json.loads(json.dumps(to_jsonable(action)))["kind"], "run_command")

    def test_missing_factory_does_not_use_mock(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = main(["missing.md", "--factory", "absent_team_module:create_dependencies"])
        self.assertEqual(code, 2)
        self.assertIn("No mocks are enabled", output.getvalue())

    def test_required_module_entrypoint_help(self):
        result = subprocess.run([sys.executable, "-m", "agent", "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("spec", result.stdout)

    def test_invalid_factory_syntax(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["spec.md", "--factory", "invalid"]), 2)

    def test_invalid_cli_budget(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["spec.md", "--max-steps", "0"]), 2)

    def test_required_entrypoint_with_explicit_test_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game_design_prompt.md").write_text("# Fixture specification", encoding="utf-8")
            environment = dict(os.environ)
            environment["AGENT_MODULE_FACTORY"] = "tests.test_controller:make_test_dependencies"
            result = subprocess.run([sys.executable, "-m", "agent", "game_design_prompt.md",
                "--run-root", str(root)], capture_output=True, text=True,
                env=environment, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["outcome"], "succeeded")
            self.assertTrue(Path(summary["trace_dir"]).is_dir())
            self.assertTrue(Path(summary["game_dir"]).is_dir())


if __name__ == "__main__":
    unittest.main()
