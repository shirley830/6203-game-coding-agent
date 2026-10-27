"""Controller maps a confirmed verifier script error to a structured category (Member 1).

Only an UNKNOWN result on a frozen mandatory criterion whose detail carries the
Member 4 marker counts. A FAIL mentioning the marker, or an UNKNOWN on a
non-mandatory criterion, stays "verification" and remains repairable.
"""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.recovery import RuleBasedRecovery
from agent.state import (
    AcceptanceSuite, CheckStatus, Criterion, CriterionResult, Phase, RecoveryKind,
    ToolKind, VerificationReport,
)
from tests.test_controller import Scenario

MARKER = "test_script_error: cards [0, 1] are required matched===true but deck patterns ['A', 'B'] are not all the same"


class ScriptErrorMappingTests(unittest.IsolatedAsyncioTestCase):
    async def run_once(self, status, detail, *, criterion=1):
        records = []

        class Recording(RuleBasedRecovery):
            async def recommend_recovery(self, failure, view, limits):
                decision = await super().recommend_recovery(failure, view, limits)
                records.append((failure, decision))
                return decision

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "game_design_prompt.md"
            spec.write_text("Signal mapping fixture.", encoding="utf-8")
            scenario = Scenario()

            def report(original):
                results = list(original.results)
                results[criterion] = replace(results[criterion], status=status, detail=detail)
                return replace(original, results=tuple(results))

            scenario.reports = [report]
            summary = await Controller(AgentConfig(root), replace(
                scenario.dependencies(), recovery=Recording())).run(spec)
            return summary, scenario, records

    async def test_confirmed_script_error_stops_without_game_edit(self):
        summary, scenario, records = await self.run_once(CheckStatus.UNKNOWN, MARKER)
        failure, decision = records[0]
        self.assertEqual(failure.category, "test_script_error")
        self.assertEqual(failure.operation_key, "verifier.next_check")
        self.assertIsNone(failure.action)
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(summary.levels["verdict"], "not_accepted")
        self.assertFalse(any(a.kind == ToolKind.WRITE_FILE for a in scenario.actions))

    async def test_game_fail_mentioning_marker_stays_repairable(self):
        summary, _scenario, records = await self.run_once(CheckStatus.FAIL, "page printed " + MARKER)
        self.assertEqual(records[0][0].category, "verification")
        self.assertEqual(records[0][1].kind, RecoveryKind.REPAIR)
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)

    async def test_unknown_without_marker_is_not_a_script_error(self):
        _summary, _scenario, records = await self.run_once(CheckStatus.UNKNOWN, "no evidence recorded")
        self.assertEqual(records[0][0].category, "verification")

    async def test_optional_criterion_does_not_trigger_mapping(self):
        controller = Controller.__new__(Controller)
        controller._state = type("S", (), {"suite": AcceptanceSuite("v1", (
            Criterion("REQ-OPT", "Optional rule", "functional", mandatory=False),))})()
        report = VerificationReport("d", "r", "v1", (
            CriterionResult("REQ-OPT", CheckStatus.UNKNOWN, ("e",), MARKER),))
        self.assertEqual(controller._verification_category(report), "verification")


if __name__ == "__main__":
    unittest.main()
