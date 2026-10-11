"""Behavioral integration tests with injected fakes, without a game template."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller, Dependencies, TRANSITIONS
from agent.state import (
    AcceptanceSuite, CheckStatus, Criterion, CriterionResult, DeliveryReport,
    Phase, Plan, RecoveryDecision, RecoveryKind, RequestVerification, RevisePlan,
    Task, TERMINAL_PHASES, ToolAction, ToolKind, ToolResult, ToolStatus,
    VerificationReport, to_jsonable,
)


class Scenario:
    """All fake ports stay inside tests. Runtime never executes generated code."""

    def __init__(self) -> None:
        self.context = None
        self.events = []
        self.actions = []
        self.decisions = []
        self.recoveries = []
        self.reports = []
        self.revision = 0
        self.counter = 0
        self.check_requested = False
        self.fail_event = None
        self.delivery_preserved = True
        self.runtime_error_once = False
        self.runtime_unknown_once = False
        self.block_commands = False
        self.runtime_entered = asyncio.Event()
        self.runtime_cancelled = False
        self.slow_planning = False
        self.plan_override = None
        self.suite = AcceptanceSuite("v1", (
            Criterion("launch", "Launch check", "launch"),
            Criterion("function", "Main requirements", "functional"),
            Criterion("interaction", "Interaction check", "interaction"),
        ))

    def dependencies(self):
        return Dependencies(self, self, self, self, self)

    async def open_run(self, context):
        self.context = context
        context.trace_dir.mkdir(parents=True)

    async def append_event(self, event):
        if event.kind == self.fail_event:
            raise OSError("fake disk failure")
        self.events.append(event)
        with (self.context.trace_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(to_jsonable(event), ensure_ascii=False) + "\n")

    async def save_checkpoint(self, snapshot):
        (self.context.trace_dir / "checkpoint.json").write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    async def finalize(self, summary):
        (self.context.trace_dir / "summary.json").write_text(
            json.dumps(to_jsonable(summary), ensure_ascii=False), encoding="utf-8")
        return DeliveryReport(self.delivery_preserved, self.context.trace_dir)

    async def prepare_acceptance(self, view, limits):
        return self.suite

    async def create_plan(self, view, limits):
        if self.slow_planning:
            await asyncio.sleep(2)
        return self.plan_override or Plan("p1", (Task("t1", "Test task",
                     tuple(c.criterion_id for c in self.suite.criteria)),))

    async def next_decision(self, view, limits):
        if self.decisions:
            result = self.decisions.pop(0)
        else:
            result = RequestVerification(("fake-launch",))
        if isinstance(result, RequestVerification):
            self.check_requested = False
        return result

    async def execute(self, action, context, limits):
        self.actions.append(action)
        if action.kind == ToolKind.PREPARE_WORKSPACE:
            context.game_dir.mkdir()
            self.revision = 1
        elif action.kind == ToolKind.READ_SPEC:
            return ToolResult(action.action_id, ToolStatus.OK, "read", f"r{self.revision}",
                              {"text": context.spec_path.read_text(encoding="utf-8")})
        elif action.kind == ToolKind.WRITE_FILE:
            target = context.game_dir / action.arguments["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.arguments["content"], encoding="utf-8")
            self.revision += 1
        elif action.kind == ToolKind.RUN_COMMAND:
            if self.runtime_error_once:
                self.runtime_error_once = False
                return ToolResult(action.action_id, ToolStatus.ERROR, "temporary",
                                  f"r{self.revision}", error_code="transient")
            if self.runtime_unknown_once:
                self.runtime_unknown_once = False
                raise RuntimeError("unknown command outcome")
            if self.block_commands:
                self.runtime_entered.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    self.runtime_cancelled = True
        return ToolResult(action.action_id, ToolStatus.OK, "observed", f"r{self.revision}")

    async def next_check(self, view, limits):
        if not self.check_requested:
            self.check_requested = True
            self.counter += 1
            return ToolAction(f"check-{self.counter}", ToolKind.RUN_COMMAND, {"argv": ["fake-check"]})
        report = VerificationReport(view.spec_digest, view.workspace_revision or "unknown",
            view.suite.version, tuple(CriterionResult(c.criterion_id, CheckStatus.PASS,
                ("events.jsonl",)) for c in view.suite.criteria))
        if self.reports:
            return self.reports.pop(0)(report)
        return report

    async def recommend_recovery(self, failure, view, limits):
        if self.recoveries:
            return self.recoveries.pop(0)
        return RecoveryDecision(RecoveryKind.ABORT, "fake abort")


def make_test_dependencies(config):
    """Explicit test factory for subprocess entrypoint integration tests only."""
    return Scenario().dependencies()


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = self.root / "game_design_prompt.md"
        self.spec.write_text("# Test specification\nAcceptance fixture, not a game.", encoding="utf-8")
        self.scenario = Scenario()

    def controller(self, **overrides):
        return Controller(AgentConfig(self.root, **overrides), self.scenario.dependencies())

    async def run_scenario(self, **overrides):
        self.current_controller = self.controller(**overrides)
        return await self.current_controller.run(self.spec)

    async def test_success_requires_real_dispatch_and_preserves_trace(self):
        self.scenario.decisions = [ToolAction("write", ToolKind.WRITE_FILE,
            {"path": "artifact.txt", "content": "test fixture"})]
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual((summary.game_dir / "artifact.txt").read_text(), "test fixture")
        events = [json.loads(line) for line in (summary.trace_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        starts = [e["payload"]["action"]["action_id"] for e in events if e["kind"] == "action_start"]
        results = [e["payload"]["result"]["action_id"] for e in events if e["kind"] == "action_result"]
        self.assertEqual(starts, results)
        self.assertEqual(summary.commands_used, 1)
        self.assertEqual(events[-1]["payload"]["to"], "succeeded")

    async def test_no_success_without_verifier_dispatch(self):
        async def ungrounded(view, limits):
            return VerificationReport(view.spec_digest, view.workspace_revision,
                view.suite.version, tuple(CriterionResult(c.criterion_id, CheckStatus.PASS,
                ("claimed",)) for c in view.suite.criteria))
        self.scenario.next_check = ungrounded
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)

    async def test_stale_report_rejected(self):
        self.scenario.reports = [lambda report: replace(report, workspace_revision="old")]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_spec_digest_mismatch_rejected(self):
        self.scenario.reports = [lambda report: replace(report, spec_digest="wrong")]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_suite_version_mismatch_rejected(self):
        self.scenario.reports = [lambda report: replace(report, suite_version="wrong")]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_missing_criterion_rejected(self):
        self.scenario.reports = [lambda report: replace(report, results=report.results[:-1])]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_empty_evidence_rejected(self):
        self.scenario.reports = [lambda report: replace(report, results=tuple(
            replace(r, evidence_refs=()) for r in report.results))]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_skipped_mandatory_criterion_rejected(self):
        self.scenario.reports = [lambda report: replace(report, results=tuple(
            replace(r, status=CheckStatus.SKIP) for r in report.results))]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_failed_verification_repair_then_reverify(self):
        self.scenario.reports = [lambda report: replace(report, results=tuple(
            replace(r, status=CheckStatus.FAIL) for r in report.results))]
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.REPAIR, "repair")]
        self.scenario.decisions = [RequestVerification(("fake-launch",)),
            ToolAction("repair", ToolKind.WRITE_FILE, {"path": "artifact.txt", "content": "repaired"}),
            RequestVerification(("fake-launch",))]
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.verification.workspace_revision, "r2")
        self.assertEqual(summary.recovery_attempts, 1)
        self.assertEqual(summary.commands_used, 2)

    async def test_safe_retry_consumes_budgets(self):
        self.scenario.runtime_error_once = True
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.RETRY, "transient", True)]
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.commands_used, 2)
        self.assertEqual(self.current_controller.state_snapshot["retry_counts"], {"tool:check-1": 1})

    async def test_unknown_side_effect_retry_rejected(self):
        self.scenario.runtime_unknown_once = True
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.RETRY, "unsafe", True)]
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(summary.commands_used, 1)
        self.assertIn("unknown", summary.reason)

    async def test_retry_budget_zero(self):
        self.scenario.runtime_error_once = True
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.RETRY, "retry", True)]
        self.assertEqual((await self.run_scenario(max_retries_per_operation=0)).outcome, Phase.EXHAUSTED)

    async def test_recovery_budget_zero(self):
        self.scenario.runtime_error_once = True
        summary = await self.run_scenario(max_recovery_attempts=0)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual(summary.recovery_attempts, 0)

    async def test_command_budget_and_partial_artifact_preserved(self):
        self.scenario.decisions = [ToolAction("artifact", ToolKind.WRITE_FILE,
            {"path": "partial.txt", "content": "partial"}),
            ToolAction("cmd1", ToolKind.RUN_COMMAND, {"argv": ["fake"]}),
            ToolAction("cmd2", ToolKind.RUN_COMMAND, {"argv": ["fake"]})]
        summary = await self.run_scenario(max_commands=1)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual(summary.commands_used, 1)
        self.assertTrue((summary.game_dir / "partial.txt").is_file())
        self.assertNotIn("cmd2", [a.action_id for a in self.scenario.actions])

    async def test_step_budget_enforced_before_call(self):
        summary = await self.run_scenario(max_steps=3)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual(summary.steps_used, 3)

    async def test_no_progress_limit(self):
        self.scenario.decisions = [ToolAction(f"inspect-{i}", ToolKind.INSPECT_WORKSPACE) for i in range(5)]
        summary = await self.run_scenario(max_no_progress_cycles=2)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertIn("progress", summary.reason)

    async def test_timeout_cancels_runtime_and_blocks_success(self):
        self.scenario.block_commands = True
        summary = await self.run_scenario(command_timeout_seconds=0.02)
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertTrue(self.scenario.runtime_cancelled)
        self.assertTrue(any(e.kind == "action_unknown" for e in self.scenario.events))

    async def test_total_work_deadline(self):
        self.scenario.slow_planning = True
        summary = await self.run_scenario(max_runtime_seconds=0.12,
            finalization_reserve_seconds=0.05)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)

    async def test_external_cancellation_preserves_traces(self):
        self.scenario.block_commands = True
        controller = self.controller()
        task = asyncio.create_task(controller.run(self.spec))
        await asyncio.wait_for(self.scenario.runtime_entered.wait(), 1)
        task.cancel()
        summary = await task
        self.assertEqual(summary.outcome, Phase.CANCELLED)
        self.assertTrue(self.scenario.runtime_cancelled)
        self.assertTrue((summary.trace_dir / "summary.json").is_file())

    async def test_traversal_rejected_before_runtime(self):
        self.scenario.decisions = [ToolAction("escape", ToolKind.WRITE_FILE,
            {"path": "../outside.txt", "content": "bad"})]
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertNotIn("escape", [a.action_id for a in self.scenario.actions])
        self.assertFalse((self.root / "outside.txt").exists())

    async def test_symlink_escape_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        execute = self.scenario.execute
        async def runtime(action, context, limits):
            result = await execute(action, context, limits)
            if action.kind == ToolKind.PREPARE_WORKSPACE:
                (context.game_dir / "escape").symlink_to(outside, target_is_directory=True)
            return result
        self.scenario.execute = runtime
        self.scenario.decisions = [ToolAction("escape", ToolKind.WRITE_FILE,
            {"path": "escape/secret.txt", "content": "bad"})]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)
        self.assertFalse((outside / "secret.txt").exists())

    async def test_verifier_cannot_write_files(self):
        async def bad_verifier(view, limits):
            return ToolAction("bad-check", ToolKind.WRITE_FILE, {"path": "bad.txt", "content": "bad"})
        self.scenario.next_check = bad_verifier
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)
        self.assertNotIn("bad-check", [a.action_id for a in self.scenario.actions])

    async def test_extra_tool_argument_rejected(self):
        self.scenario.decisions = [ToolAction("bad", ToolKind.RUN_COMMAND,
            {"argv": ["fake"], "shell": True})]
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)
        self.assertNotIn("bad", [a.action_id for a in self.scenario.actions])

    async def test_nonempty_game_never_overwritten(self):
        (self.root / "game").mkdir()
        artifact = self.root / "game" / "keep.txt"
        artifact.write_text("keep")
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(artifact.read_text(), "keep")
        self.assertEqual(self.scenario.actions, [])

    async def test_missing_spec_records_failure(self):
        self.spec.unlink()
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertTrue((summary.trace_dir / "summary.json").is_file())

    async def test_empty_spec_fails(self):
        self.spec.write_text(" ")
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_trace_failure_prevents_tool_execution(self):
        self.scenario.fail_event = "action_start"
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(self.scenario.actions, [])

    async def test_delivery_failure_blocks_success(self):
        self.scenario.delivery_preserved = False
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_trace_symlink_not_opened(self):
        target = self.root / "external"
        target.mkdir()
        (self.root / "traces").symlink_to(target, target_is_directory=True)
        summary = await self.run_scenario()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertIsNone(self.scenario.context)
        self.assertEqual(list(target.iterdir()), [])

    async def test_invalid_acceptance_suite_fails(self):
        self.scenario.suite = AcceptanceSuite("v1", (Criterion("only", "only", "launch"),))
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_cyclic_plan_fails(self):
        ids = tuple(c.criterion_id for c in self.scenario.suite.criteria)
        self.scenario.plan_override = Plan("bad", (Task("a", "a", ids, ("b",)), Task("b", "b", ids, ("a",))))
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_module_exception_is_recoverable(self):
        original = self.scenario.create_plan
        async def broken(view, limits):
            self.scenario.create_plan = original
            raise RuntimeError("fake transient")
        self.scenario.create_plan = broken
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.RETRY, "retry", True)]
        self.assertEqual((await self.run_scenario()).outcome, Phase.SUCCEEDED)

    async def test_nonserializable_module_result_is_rejected(self):
        async def broken(view, limits):
            return object()
        self.scenario.next_decision = broken
        self.assertEqual((await self.run_scenario()).outcome, Phase.FAILED)

    async def test_readonly_view_nested_payload(self):
        original = self.scenario.next_decision
        async def observe(view, limits):
            with self.assertRaises(TypeError):
                view.last_result.data["text"] = "overwrite"
            with self.assertRaises(Exception):
                view.steps_used = 0
            return await original(view, limits)
        self.scenario.next_decision = observe
        self.assertEqual((await self.run_scenario()).outcome, Phase.SUCCEEDED)

    async def test_controller_single_use(self):
        controller = self.controller()
        await controller.run(self.spec)
        with self.assertRaises(RuntimeError):
            await controller.run(self.spec)

    def test_terminal_states_have_no_outgoing_edges(self):
        for phase in TERMINAL_PHASES:
            self.assertEqual(TRANSITIONS[phase], frozenset())


if __name__ == "__main__":
    unittest.main()
