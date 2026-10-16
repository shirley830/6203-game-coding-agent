"""Non-browser games (process mode) and runtime configuration from agent.env."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.__main__ import main
from agent.planning.planner import LLMPlanner
from agent.state import Phase
from agent.verification import GameVerifier
from tests.test_integration_tasks import LIMITS, view

HARNESS = Path(__file__).resolve().parents[1] / "agent" / "verification" / "playwright_game_check.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "games"


def run_process_harness(name: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        game = Path(directory) / "game"
        shutil.copytree(FIXTURES / name, game)
        done = subprocess.run(
            [sys.executable, str(HARNESS), "--timeout-seconds", "30",
             "--evidence-dir", str(Path(directory) / "evidence"),
             "--launch", sys.executable, "main.py"],
            cwd=game, capture_output=True, text=True, timeout=90)
        line = next(l for l in done.stdout.splitlines() if l.startswith("HARNESS_JSON="))
        return json.loads(line[len("HARNESS_JSON="):])


class ProcessModeHarnessTests(unittest.TestCase):
    def statuses(self, result):
        return {k: v["status"] for k, v in result["checks"].items()}

    def test_terminal_game_is_played_through_stdin(self):
        result = run_process_harness("process_term")
        self.assertEqual(result["mode"], "process")
        self.assertEqual(self.statuses(result), {"launch": "pass", "functional": "pass",
                                                 "interaction": "pass", "technical": "pass"})

    def test_crash_fails_with_the_exception_first(self):
        result = run_process_harness("process_crash")
        self.assertEqual(result["checks"]["launch"]["status"], "fail")
        self.assertIn("RuntimeError: missing level file", result["checks"]["launch"]["detail"][:200])
        self.assertEqual(result["checks"]["functional"]["status"], "fail")

    def test_graphical_game_is_labelled_static_evidence(self):
        result = run_process_harness("process_gfx")
        interaction = result["checks"]["interaction"]
        self.assertEqual(interaction["status"], "pass")
        self.assertIn("static evidence only", interaction["detail"])


class Client:
    async def generate(self, **_):
        raise AssertionError("no spec scenarios should be requested for non-browser games")


class NonBrowserVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_spec_uses_generic_suite_and_process_launch(self):
        spec_view = view(phase=Phase.INITIALIZING)
        spec_view = spec_view.__class__(**{**spec_view.__dict__,
                                           "spec_text": "Create a terminal hangman game in Python."})
        verifier = GameVerifier(client=Client())
        suite = await verifier.prepare_acceptance(spec_view, LIMITS)
        self.assertFalse(any(c.criterion_id.startswith("REQ-") for c in suite.criteria))
        self.assertIn("process mode", verifier.acceptance_note)
        argv = verifier._emit_harness(view(launch=("python3", "main.py")), LIMITS).arguments["argv"]
        self.assertEqual(argv[-3:], ("--launch", "python3", "main.py"))

    def test_python_readme_documents_dependencies(self):
        text = LLMPlanner._readme(("python3", "main.py"), None, ["pygame"], False)
        self.assertIn("python3 -m pip install pygame", text)
        self.assertIn("python3 main.py", text)
        self.assertNotIn("http://127.0.0.1", text)
        with_requirements = LLMPlanner._readme(("python3", "main.py"), None, ["pygame"], True)
        self.assertIn("pip install -r requirements.txt", with_requirements)


def capture_runtime(config):
    raise RuntimeError(f"captured max_runtime={config.max_runtime_seconds}")


class RuntimeConfigTests(unittest.TestCase):
    def run_main(self, env_text, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game_design_prompt.md").write_text("# g", encoding="utf-8")
            (root / "agent.env").write_text(env_text, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "AGENT_MAX_RUNTIME"}
            with patch.dict(os.environ, env, clear=True), patch("sys.stderr") as stderr:
                main([str(root / "game_design_prompt.md"), "--run-root", directory,
                      "--factory", "tests.test_non_browser:capture_runtime", *extra])
            return "".join(str(c.args[0]) for c in stderr.write.call_args_list)

    def test_runtime_from_agent_env(self):
        self.assertIn("captured max_runtime=1200.0", self.run_main("AGENT_MAX_RUNTIME=1200\n"))

    def test_cli_flag_wins_and_default_is_1800(self):
        self.assertIn("captured max_runtime=600.0",
                      self.run_main("AGENT_MAX_RUNTIME=1200\n", ("--max-runtime", "600")))
        self.assertIn("captured max_runtime=1800.0", self.run_main("# nothing\n"))


if __name__ == "__main__":
    unittest.main()
