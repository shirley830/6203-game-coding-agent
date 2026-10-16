"""Regression tests for the cross-member integration checklist."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.config import AgentConfig
from agent.planning import LLMPlanner
from agent.state import (
    AcceptanceSuite, AgentView, CallLimits, Criterion, ExecutionContext, Phase,
    Plan, RequestVerification, Task, ToolAction, ToolKind, ToolResult, ToolStatus,
    TraceEvent, RunSummary,
)
from agent.tools import LocalRuntime, RuntimeSettings
from agent.tracing.recorder import FileTraceRecorder
from agent.verification import GameVerifier

REPO = Path(__file__).resolve().parents[1]
LIMITS = CallLimits(30, 100, 100)
SUITE = AcceptanceSuite("s1", (
    Criterion("C-LAUNCH", "launch", "launch"),
    Criterion("C-FUNCTIONAL", "rules", "functional"),
    Criterion("C-INTERACTION", "input", "interaction"),
))
PLAN = Plan("p1", (Task("t1", "everything", ("C-LAUNCH", "C-FUNCTIONAL", "C-INTERACTION")),))


def view(*, last_result=None, phase=Phase.DECIDING, launch=()):
    return AgentView("run", phase, "spec", "digest", "rev", SUITE, PLAN, 1, launch,
                     last_result, None, 0, 0, 0, 0.0)


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        self.prompts.append(user_prompt)
        return self.responses.pop(0)


def write(path, content="x"):
    return json.dumps({"decision": "tool_action", "action_id": "same", "kind": "write_file",
                       "arguments": {"path": path, "content": content}})


VERIFY = json.dumps({"decision": "request_verification",
                     "launch_command": ["python3", "-m", "http.server", "8000"]})


class FactoryTests(unittest.TestCase):
    def test_factory_injects_production_game_verifier(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AGENT_MODEL": "anthropic/test-model"}):
            from agent.integrations import create_dependencies
            deps = create_dependencies(AgentConfig(run_root=Path(directory)))
        self.assertIsInstance(deps.verifier, GameVerifier)
        self.assertIsInstance(deps.runtime, LocalRuntime)
        self.assertIsInstance(deps.planner, LLMPlanner)
        self.assertNotIn("Mock", type(deps.verifier).__name__)

    def test_factory_refuses_to_start_without_model(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AGENT_MODEL": ""}):
            from agent.integrations import create_dependencies
            with self.assertRaises(RuntimeError):
                create_dependencies(AgentConfig(run_root=Path(directory)))

    def test_cli_reports_startup_failure_without_mocks(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = Path(directory) / "game_design_prompt.md"
            spec.write_text("# game", encoding="utf-8")
            env = {**os.environ, "AGENT_MODEL": "", "PYTHONPATH": str(REPO)}
            done = subprocess.run([sys.executable, "-m", "agent", str(spec), "--run-root", directory],
                                  capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 2)
            self.assertIn("AGENT_MODEL", done.stderr)
            self.assertFalse((Path(directory) / "game").exists())


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_ids_are_unique_and_paths_normalized(self):
        planner = LLMPlanner(Client(write("game/index.html"), "```json\n" + write("./a.js") + "\n```"))
        first = await planner.next_decision(view(), LIMITS)
        second = await planner.next_decision(view(), LIMITS)
        self.assertNotEqual(first.action_id, second.action_id)
        self.assertEqual(first.arguments["path"], "index.html")
        self.assertEqual(second.arguments["path"], "a.js")

    async def test_history_and_files_reach_the_next_prompt(self):
        client = Client(write("index.html", "<canvas>"), VERIFY)
        planner = LLMPlanner(client)
        action = await planner.next_decision(view(), LIMITS)
        result = ToolResult(action.action_id, ToolStatus.OK, "Wrote 8 bytes to index.html", "r2")
        await planner.next_decision(view(last_result=result), LIMITS)
        self.assertIn("index.html (8 bytes)", client.prompts[1])
        self.assertIn(f"{action.action_id} write_file index.html -> ok", client.prompts[1])

    async def test_verification_requires_entry_file_then_readme(self):
        planner = LLMPlanner(Client(VERIFY, write("index.html", "<canvas>"), VERIFY, VERIFY))
        guard = await planner.next_decision(view(), LIMITS)
        self.assertEqual(guard.kind, ToolKind.INSPECT_WORKSPACE)
        page = await planner.next_decision(view(), LIMITS)
        ok = ToolResult(page.action_id, ToolStatus.OK, "Wrote", "r3")
        readme = await planner.next_decision(view(last_result=ok), LIMITS)
        self.assertEqual(readme.kind, ToolKind.WRITE_FILE)
        self.assertEqual(readme.arguments["path"], "README.md")
        self.assertIn("python3 -m http.server 8000", readme.arguments["content"])
        done = ToolResult(readme.action_id, ToolStatus.OK, "Wrote", "r4")
        decision = await planner.next_decision(view(last_result=done), LIMITS)
        self.assertIsInstance(decision, RequestVerification)
        self.assertTrue(decision.launch_command)


class RuntimeArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_artifacts_are_output_refs_and_do_not_touch_game(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spec.md").write_text("# s", encoding="utf-8")
            context = ExecutionContext("r", root, root / "spec.md", root / "game", root / "traces" / "r")
            runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=sys.platform == "darwin"))
            prepared = await runtime.execute(ToolAction("p", ToolKind.PREPARE_WORKSPACE), context, LIMITS)
            script = ("import os,pathlib; pathlib.Path(os.environ['AGENT_ARTIFACT_DIR'],"
                      "'shot.png').write_bytes(b'png')")
            result = await runtime.execute(ToolAction("c", ToolKind.RUN_COMMAND, {
                "argv": [sys.executable, "-c", script]}), context, LIMITS)
            self.assertEqual(result.status, ToolStatus.OK, result.observation)
            self.assertTrue(any(ref.endswith("shot.png") for ref in result.output_refs))
            self.assertEqual(result.workspace_revision, prepared.workspace_revision)
            self.assertFalse((root / "traces").exists(), "runtime must not write traces/")

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox")
    async def test_commands_cannot_write_agent_spec_or_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "spec.md"
            spec.write_text("# s", encoding="utf-8")
            (root / "traces").mkdir()
            context = ExecutionContext("r", root, spec, root / "game", root / "traces" / "r")
            runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=True))
            await runtime.execute(ToolAction("p", ToolKind.PREPARE_WORKSPACE), context, LIMITS)
            for index, target in enumerate((spec, root / "traces" / "x.txt", REPO / "agent" / "x.py")):
                with self.subTest(target=target.name):
                    code = f"open({str(target)!r}, 'w').write('pwned')"
                    result = await runtime.execute(ToolAction(f"w{index}", ToolKind.RUN_COMMAND, {
                        "argv": [sys.executable, "-c", code]}), context, LIMITS)
                    self.assertEqual(result.status, ToolStatus.ERROR)
            self.assertEqual(spec.read_text(encoding="utf-8"), "# s")
            self.assertFalse((REPO / "agent" / "x.py").exists())


class TracerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.context = ExecutionContext("run", self.root, self.root / "s.md", self.root / "game",
                                        self.root / "traces" / "run")
        self.tracer = FileTraceRecorder()
        await self.tracer.open_run(self.context)
        self.seq = 0

    async def emit(self, kind, payload):
        self.seq += 1
        await self.tracer.append_event(TraceEvent("run", self.seq, "t", kind, Phase.VERIFYING, payload))

    async def test_output_refs_copied_and_outside_paths_refused(self):
        storage = self.root / ".agent-runtime" / "run" / "outputs"
        storage.mkdir(parents=True)
        (storage / "shot.png").write_bytes(b"png")
        outside = self.root / "secret.txt"
        outside.write_text("nope", encoding="utf-8")
        await self.emit("action_result", {"result": {"action_id": "v1", "output_refs": [
            str(storage / "shot.png"), str(outside)]}})
        index = [json.loads(l) for l in (self.context.trace_dir / "outputs" / "index.jsonl")
                 .read_text().splitlines()]
        self.assertIn("copy", index[0])
        self.assertTrue((self.context.trace_dir / index[0]["copy"]).is_file())
        self.assertIn("skipped", index[1])

    async def test_verification_reports_and_checkpoint_history_persist(self):
        await self.emit("module_result", {"operation": "verifier.next_check", "result": {
            "spec_digest": "d", "workspace_revision": "r", "suite_version": "s", "results": []}})
        await self.tracer.save_checkpoint({"phase": "planning"})
        await self.tracer.save_checkpoint({"phase": "verifying"})
        self.assertTrue(list((self.context.trace_dir / "verification").glob("report-*.json")))
        history = (self.context.trace_dir / "checkpoints.jsonl").read_text().splitlines()
        self.assertEqual(len(history), 2)

    def summary(self):
        return RunSummary("run", Phase.FAILED, "x", self.context.game_dir, self.context.trace_dir,
                          (), None, 1, 0, 0, 0.1)

    async def test_finalize_writes_report_and_only_touches_traces(self):
        await self.emit("run_start", {})
        report = await self.tracer.finalize(self.summary())
        self.assertTrue(report.preserved)
        self.assertTrue((self.context.trace_dir / "run_report.md").is_file())
        self.assertFalse(self.context.game_dir.exists())

    async def test_preserved_is_not_claimed_when_events_were_lost(self):
        await self.emit("run_start", {})
        (self.context.trace_dir / "events.jsonl").write_text("", encoding="utf-8")
        with self.assertRaises(Exception):
            await self.tracer.finalize(self.summary())


class VerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_only_listed_files_from_real_runtime_mapping(self):
        verifier = GameVerifier()
        current = view(phase=Phase.VERIFYING, launch=("python3", "-m", "http.server", "8000"))
        inspect = await verifier.next_check(current, LIMITS)
        listing = ToolResult(inspect.action_id, ToolStatus.OK, "Inspected 2 entries", "rev", data={
            "entries": [{"path": "index.html", "type": "file", "size": 3},
                        {"path": "js", "type": "directory"}]})
        read = await verifier.next_check(view(phase=Phase.VERIFYING, last_result=listing,
                                              launch=current.launch_command), LIMITS)
        self.assertEqual(read.arguments, {"path": "index.html"})
        result = ToolResult(read.action_id, ToolStatus.OK, "Read", "rev",
                            data={"path": "index.html", "text": "<canvas> keydown score"})
        harness = await verifier.next_check(view(phase=Phase.VERIFYING, last_result=result,
                                                 launch=current.launch_command), LIMITS)
        self.assertEqual(harness.kind, ToolKind.RUN_COMMAND)
        self.assertTrue(Path(harness.arguments["argv"][1]).is_file())
        self.assertNotIn("--evidence-dir", harness.arguments["argv"])


def _browsers_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
            return True
    except Exception:
        return False


@unittest.skipUnless(_browsers_ready(), "Playwright Chromium not installed")
class EndToEndRehearsal(unittest.TestCase):
    def test_scripted_run_repairs_and_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game_design_prompt.md").write_text("# Collector\nCanvas game.", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(REPO)}
            done = subprocess.run([sys.executable, "-m", "agent", "game_design_prompt.md",
                                   "--factory", "tests.rehearsal_factory:create_dependencies"],
                                  cwd=root, capture_output=True, text=True, env=env, timeout=240)
            self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-2000:])
            summary = json.loads(done.stdout)
            self.assertEqual(summary["outcome"], "succeeded")
            self.assertEqual(summary["recovery_attempts"], 1)
            trace = Path(summary["trace_dir"])
            self.assertTrue(list((trace / "outputs").glob("*after_input.png")))
            self.assertEqual(sorted(p.name for p in (root / "game").iterdir()),
                             ["README.md", "game.js", "index.html"])
            from agent.delivery import audit_delivery
            self.assertTrue(audit_delivery(trace)["complete"])


if __name__ == "__main__":
    unittest.main()
