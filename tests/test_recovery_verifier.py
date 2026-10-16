"""Member 5 contract tests with Member 4's unmodified GameVerifier.

No LLM, browser or subprocess is executed. Harness observations are fixtures.
After root integration this imports agent.verification normally. Before that,
MEMBER5_VERIFIER_DIR may point to an unmodified verification package snapshot.
"""

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.recovery import RuleBasedRecovery
from agent.state import (
    CheckStatus, Phase, Plan, RecoveryKind, RequestVerification, Task, ToolAction, ToolKind,
    ToolResult, ToolStatus,
)
from tests.test_controller import Scenario


def load_verifier():
    external = os.environ.get("MEMBER5_VERIFIER_DIR")
    if external:
        root = Path(external).resolve()
        name = "member5_external_verification"
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)])
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load verifier from {root}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module.GameVerifier
    try:
        from agent.verification import GameVerifier
    except ModuleNotFoundError as exc:
        if exc.name == "agent.verification":
            raise unittest.SkipTest(
                "Member 4 not integrated; set MEMBER5_VERIFIER_DIR to an unmodified package snapshot"
            ) from exc
        raise
    return GameVerifier


class RecordingRecovery(RuleBasedRecovery):
    def __init__(self):
        self.records = []

    async def recommend_recovery(self, failure, view, limits):
        decision = await super().recommend_recovery(failure, view, limits)
        self.records.append((failure, decision))
        return decision


class HarnessObservations(Scenario):
    """Fake runtime returns harness-shaped data, never runs a game."""

    def __init__(self, first="interaction_fail"):
        super().__init__()
        self.first = first
        self.harness_calls = 0
        self.always_fail = False
        self.change_revision_on_harness = False
        self.timeout_action_id = None

    async def create_plan(self, view, limits):
        self.frozen_suite = view.suite
        return Plan("fixture-plan", (Task("fixture-task", "Cover actual frozen suite",
                    tuple(c.criterion_id for c in view.suite.criteria)),))

    async def execute(self, action, context, limits):
        if action.kind in {ToolKind.PREPARE_WORKSPACE, ToolKind.READ_SPEC, ToolKind.WRITE_FILE}:
            return await super().execute(action, context, limits)
        self.actions.append(action)
        revision = f"r{self.revision}"
        if action.kind == ToolKind.INSPECT_WORKSPACE:
            return ToolResult(action.action_id, ToolStatus.OK,
                              "README.md index.html main.js", revision,
                              data={"files": ["README.md", "index.html", "main.js"]})
        if action.kind == ToolKind.READ_FILE:
            return ToolResult(action.action_id, ToolStatus.OK,
                              "read fixture: canvas keydown score restart", revision,
                              data={"path": action.arguments["path"], "text": "fixture only"})
        if action.kind != ToolKind.RUN_COMMAND:
            raise AssertionError(f"Unexpected test tool {action.kind}")
        self.harness_calls += 1
        first = self.harness_calls == 1
        if first and self.first in {"timeout", "cleanup_failed"}:
            code = "COMMAND_TIMEOUT" if self.first == "timeout" else "PROCESS_CLEANUP_FAILED"
            self.timeout_action_id = action.action_id
            return ToolResult(action.action_id, ToolStatus.UNKNOWN, code, None,
                              error_code=code, side_effects_unknown=True)
        if first and self.first == "nonzero":
            return ToolResult(action.action_id, ToolStatus.ERROR, "harness exited nonzero", revision,
                              error_code="NONZERO_EXIT")

        failed = self.always_fail or (first and self.first == "interaction_fail")
        checks = {category: {"status": "pass", "evidence_refs": [f"fixture:{category}"]}
                  for category in ("launch", "functional", "interaction", "technical")}
        checks["interaction"].update(
            status="fail" if failed else "pass", state_changed=not failed,
            detail="no state change" if failed else "fixture state changed")
        payload = {"ok": not failed, "checks": checks}
        if self.change_revision_on_harness:
            self.revision += 1
            revision = f"r{self.revision}"
        return ToolResult(action.action_id, ToolStatus.OK,
                          "HARNESS_JSON=" + json.dumps(payload), revision,
                          output_refs=("fixture:harness-output",))


class VerifierRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier_type = load_verifier()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = self.root / "game_design_prompt.md"
        self.spec.write_text("Browser Canvas collection game: arrow keys, score, restart.", encoding="utf-8")
        self.recovery = RecordingRecovery()
        self.runtime = HarnessObservations()
        self.verifier = type(self).verifier_type()

    def repair_decisions(self, *, inspect=False):
        decisions = [RequestVerification(("fixture-launch",))]
        if inspect:
            decisions.append(ToolAction("recovery.inspect", ToolKind.INSPECT_WORKSPACE))
        decisions.extend([
            ToolAction("recovery.fixture-change", ToolKind.WRITE_FILE,
                       {"path": "repair-fixture.txt", "content": "test revision change; not game code"}),
            RequestVerification(("fixture-launch",)),
        ])
        return decisions

    async def run_flow(self, **config):
        deps = replace(self.runtime.dependencies(), verifier=self.verifier, recovery=self.recovery)
        return await Controller(AgentConfig(self.root, **config), deps).run(self.spec)

    async def test_real_verifier_interaction_failure_repairs_and_reverifies(self):
        self.runtime.decisions = self.repair_decisions()
        summary = await self.run_flow()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(self.runtime.harness_calls, 2)
        self.assertEqual(self.recovery.records[0][0].category, "verification")
        self.assertEqual(self.recovery.records[0][1].kind, RecoveryKind.REPAIR)
        self.assertEqual(summary.verification.workspace_revision, "r2")
        self.assertTrue(all(r.status == CheckStatus.PASS and r.evidence_refs
                            for r in summary.verification.results))
        self.assertEqual(summary.verification.suite_version, self.runtime.frozen_suite.version)

    async def test_first_pass_needs_no_recovery(self):
        self.runtime.first = "pass"
        summary = await self.run_flow()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(self.recovery.records, [])

    async def test_nonzero_harness_result_requests_repair_not_retry(self):
        self.runtime.first = "nonzero"
        self.runtime.decisions = self.repair_decisions()
        summary = await self.run_flow()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(self.recovery.records[0][0].category, "NONZERO_EXIT")
        self.assertEqual(self.recovery.records[0][1].kind, RecoveryKind.REPAIR)
        self.assertFalse(self.recovery.records[0][1].safe_to_retry)

    async def test_timeout_never_replays_harness_and_planner_inspects_first(self):
        self.runtime.first = "timeout"
        self.runtime.decisions = self.repair_decisions(inspect=True)
        summary = await self.run_flow()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(self.recovery.records[0][1].kind, RecoveryKind.REPAIR)
        actions = self.runtime.actions
        index = next(i for i, a in enumerate(actions) if a.action_id == self.runtime.timeout_action_id)
        self.assertEqual(actions[index + 1].kind, ToolKind.INSPECT_WORKSPACE)
        self.assertEqual(sum(a.action_id == self.runtime.timeout_action_id for a in actions), 1)
        # The scripted planner obeys inspection ordering; controller v1 does not enforce it.

    async def test_cleanup_failure_aborts_and_no_further_tools_run(self):
        self.runtime.first = "cleanup_failed"
        summary = await self.run_flow()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(self.runtime.harness_calls, 1)
        self.assertEqual(self.recovery.records[0][1].kind, RecoveryKind.ABORT)
        self.assertEqual(self.runtime.actions[-1].action_id, self.runtime.timeout_action_id)

    async def test_repeated_acceptance_failure_is_bounded(self):
        self.runtime.always_fail = True
        summary = await self.run_flow(max_recovery_attempts=2)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual(summary.recovery_attempts, 2)
        self.assertTrue(all(d.kind == RecoveryKind.REPAIR for _, d in self.recovery.records))

    async def test_revision_changing_evidence_never_creates_false_success(self):
        # Current Member 4 resets the whole cycle when revision changes. A harness
        # writing evidence under game/ may therefore restart checks indefinitely.
        # This reproduces the boundary without changing its implementation.
        self.runtime.first = "pass"
        self.runtime.change_revision_on_harness = True
        summary = await self.run_flow(max_steps=65)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertGreater(self.runtime.harness_calls, 1)
        self.assertEqual(summary.recovery_attempts, 0)
