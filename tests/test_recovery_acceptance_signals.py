"""Member 4/5 signal contract: script defects stop; gameplay still repairs.

No API key, LLM call, generated game, or browser is used. The typed Controller
below simulates Member 1's proposed mapping without changing production code.
"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.recovery import ErrorClass, RuleBasedRecovery, classify_failure
from agent.state import (CallLimits, CheckStatus, FailureRecord, Phase,
                         RecoveryKind, ToolAction, ToolKind)
from tests.test_controller import Scenario
from tests.test_recovery import view


SCRIPT_DETAIL = (
    "test_script_error: cards [0, 3] are required matched===true "
    "but the prepared deck contains different patterns"
)


def script_failure(**changes):
    return replace(FailureRecord("verifier.next_check", Phase.VERIFYING,
                                 "test_script_error", SCRIPT_DETAIL), **changes)


class TypedSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_category_stops_without_summary_or_frozen_suite(self):
        item = script_failure(message="Confirmed assertion defect; detail retained in traces")
        self.assertEqual(classify_failure(item), ErrorClass.VERIFIER_DEFECT)
        decision = await RuleBasedRecovery().recommend_recovery(
            item, view(), CallLimits(10, 20, 5))
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertFalse(decision.safe_to_retry)
        self.assertIn("test_script_error", decision.reason)
        self.assertIn("preserve evidence and game source", decision.reason)

    async def test_unknown_side_effects_do_not_turn_script_fault_into_game_repair(self):
        decision = await RuleBasedRecovery().recommend_recovery(
            script_failure(side_effects_unknown=True), view(), CallLimits(10, 20, 5))
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertFalse(decision.safe_to_retry)

    def test_typed_category_requires_verifier_report_provenance(self):
        for changes in (
            {"operation_key": "planner.next_decision"},
            {"operation_key": "tool:run_command"},
            {"operation_key": "verifier.unrecognised"},
            {"operation_key": "verifier.prepare_acceptance", "phase": Phase.INITIALIZING},
            {"phase": Phase.EXECUTING},
            {"action": ToolAction("x", ToolKind.RUN_COMMAND, {"argv": ["check"]})},
        ):
            with self.subTest(changes=changes):
                self.assertNotEqual(classify_failure(script_failure(**changes)),
                                    ErrorClass.VERIFIER_DEFECT)

    async def test_message_alone_cannot_reclassify_gameplay_failure(self):
        item = script_failure(category="verification")
        decision = await RuleBasedRecovery().recommend_recovery(
            item, view(), CallLimits(10, 20, 5))
        self.assertEqual(classify_failure(item), ErrorClass.VERIFICATION)
        self.assertEqual(decision.kind, RecoveryKind.REPAIR)


class _TypedController(Controller):
    """Test-only Member 1 adapter, inspecting the actual report, not all FAILs."""
    def _failure(self, key, message, **kwargs):
        report = self._state.verification_report
        if (key == "verifier.next_check" and kwargs.get("category") == "verification"
                and report is not None and any(
                    r.status == CheckStatus.UNKNOWN and r.detail.startswith("test_script_error:")
                    for r in report.results)):
            kwargs["category"] = "test_script_error"
        return super()._failure(key, message, **kwargs)


class ControllerSignalContractTests(unittest.IsolatedAsyncioTestCase):
    async def run_flow(self, *, typed, script):
        class RecordingRecovery(RuleBasedRecovery):
            def __init__(self):
                super().__init__()
                self.records = []

            async def recommend_recovery(self, failure, view, limits):
                decision = await super().recommend_recovery(failure, view, limits)
                self.records.append((failure, decision))
                return decision

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "game_design_prompt.md"
            spec.write_text("An unfamiliar game; signal integration fixture.")
            scenario = Scenario()

            def report(original):
                results = list(original.results)
                results[1] = replace(results[1],
                                     status=CheckStatus.UNKNOWN if script else CheckStatus.FAIL,
                                     detail=SCRIPT_DETAIL if script else "A real gameplay mismatch")
                return replace(original, results=tuple(results))

            scenario.reports = [report]
            recovery = RecordingRecovery()
            cls = _TypedController if typed else Controller
            result = await cls(AgentConfig(root), replace(
                scenario.dependencies(), recovery=recovery)).run(spec)
            return result, scenario, recovery.records

    async def test_existing_controller_and_member4_marker_stop_game_repair(self):
        result, scenario, records = await self.run_flow(typed=False, script=True)
        self.assertEqual(records[0][0].category, "verification")
        self.assertEqual(records[0][1].kind, RecoveryKind.ABORT)
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertFalse(any(a.kind == ToolKind.WRITE_FILE for a in scenario.actions))

    async def test_member1_typed_mapping_is_accepted_without_game_edit(self):
        result, scenario, records = await self.run_flow(typed=True, script=True)
        self.assertEqual(records[0][0].category, "test_script_error")
        self.assertEqual(records[0][1].kind, RecoveryKind.ABORT)
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertIn("test_script_error", result.reason)
        self.assertFalse(any(a.kind == ToolKind.WRITE_FILE for a in scenario.actions))

    async def test_genuine_game_failure_remains_repairable_after_mapping(self):
        result, _scenario, records = await self.run_flow(typed=True, script=False)
        self.assertEqual(records[0][0].category, "verification")
        self.assertEqual(records[0][1].kind, RecoveryKind.REPAIR)
        self.assertEqual(result.outcome, Phase.SUCCEEDED)
        self.assertEqual(result.commands_used, 2)
