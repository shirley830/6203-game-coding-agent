"""Result levels: launch, specific rules and delivery are shown apart (Member 1).

Cases follow the Kimi K3 four-spec retest of 2026-10-26: a full pass, a
budget-exhausted 7/8, and a generic-only "success" with 0 specific rules.
"""

import json
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.result_levels import render_levels, result_levels
from agent.state import (
    AcceptanceSuite, CheckStatus, Criterion, CriterionResult, Phase, VerificationReport,
)
from agent.tracing.report import render_run_report
from tests.test_controller import Scenario

GENERIC = ("C-LAUNCH", "C-FUNCTIONAL", "C-INTERACTION", "C-TECHNICAL")


def suite(rules=0, incomplete=False):
    ids = list(GENERIC) + [f"REQ-R{n}" for n in range(1, rules + 1)]
    category = {"C-LAUNCH": "launch", "C-INTERACTION": "interaction", "C-TECHNICAL": "technical"}
    criteria = [Criterion(cid, cid, category.get(cid, "functional")) for cid in ids]
    if incomplete:
        criteria.append(Criterion("C-ACCEPTANCE-INCOMPLETE", "note", "technical", mandatory=False))
    criteria.append(Criterion("C-ACCEPTANCE-INFO", "note", "technical", mandatory=False))
    return AcceptanceSuite("v1", tuple(criteria))


def report(acceptance, failing=()):
    results = []
    for c in acceptance.criteria:
        status = CheckStatus.FAIL if (c.criterion_id in failing
                                      or c.criterion_id == "C-ACCEPTANCE-INCOMPLETE") else CheckStatus.PASS
        if c.criterion_id == "C-ACCEPTANCE-INFO":
            status = CheckStatus.SKIP
        results.append(CriterionResult(c.criterion_id, status, ("e",)))
    return VerificationReport("d", "r", acceptance.version, tuple(results))


class ResultLevelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = Path(temp.name) / "game"
        self.game.mkdir()
        (self.game / "README.md").write_text("python3 -m http.server 8000", encoding="utf-8")

    def levels(self, outcome, acceptance, verification, launch=("python3",)):
        return result_levels(outcome, acceptance, verification, self.game, launch)

    def test_all_rules_passed_is_fully_accepted(self):
        acceptance = suite(rules=8)
        levels = self.levels(Phase.SUCCEEDED, acceptance, report(acceptance))
        self.assertEqual(levels["verdict"], "fully_accepted")
        self.assertEqual(levels["rules"], {"state": "passed", "passed": 8, "total": 8, "failed": []})
        self.assertTrue(levels["delivery"]["complete"])

    def test_generic_only_success_is_not_shown_as_full_success(self):
        acceptance = suite(rules=0, incomplete=True)
        levels = self.levels(Phase.SUCCEEDED, acceptance, report(acceptance))
        self.assertEqual(levels["verdict"], "acceptance_incomplete")
        self.assertEqual(levels["launch"], "pass")
        self.assertEqual(levels["rules"]["state"], "incomplete")
        text = "\n".join(render_levels(levels))
        self.assertIn("未完整验收", text)
        self.assertNotIn("完整成功", text)

    def test_fallback_suite_without_rules_is_also_incomplete(self):
        acceptance = suite(rules=0)
        levels = self.levels(Phase.SUCCEEDED, acceptance, report(acceptance))
        self.assertEqual(levels["verdict"], "acceptance_incomplete")

    def test_exhausted_partial_run_lists_the_failed_rule(self):
        acceptance = suite(rules=8)
        levels = self.levels(Phase.EXHAUSTED, acceptance, report(acceptance, failing={"REQ-R4"}))
        self.assertEqual(levels["verdict"], "not_accepted")
        self.assertEqual(levels["rules"]["passed"], 7)
        self.assertEqual(levels["rules"]["failed"], ["REQ-R4"])
        self.assertIn("7/8 passed; failed: REQ-R4", "\n".join(render_levels(levels)))

    def test_missing_readme_means_delivery_incomplete(self):
        (self.game / "README.md").unlink()
        acceptance = suite(rules=3)
        levels = self.levels(Phase.SUCCEEDED, acceptance, report(acceptance))
        self.assertEqual(levels["verdict"], "not_accepted")
        self.assertEqual(levels["delivery"], {"complete": False, "missing": ["README.md"]})

    def test_no_final_report_is_not_verified(self):
        acceptance = suite(rules=3)
        levels = self.levels(Phase.FAILED, acceptance, None, launch=())
        self.assertEqual(levels["launch"], "not_verified")
        self.assertEqual(levels["rules"]["state"], "not_verified")
        self.assertIn("launch command", levels["delivery"]["missing"])

    def test_run_report_shows_the_levels(self):
        acceptance = suite(rules=0, incomplete=True)
        levels = self.levels(Phase.SUCCEEDED, acceptance, report(acceptance))
        text = render_run_report([], {"run_id": "x", "outcome": "succeeded", "levels": levels})
        self.assertIn("## Result levels", text)
        self.assertIn("未完整验收", text)


class ControllerLevelTests(unittest.IsolatedAsyncioTestCase):
    """The real controller attaches levels to summary.json; outcome is unchanged."""

    async def test_summary_carries_levels(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        spec = root / "game_design_prompt.md"
        spec.write_text("# Test specification", encoding="utf-8")
        scenario = Scenario()
        scenario.suite = suite(rules=0, incomplete=True)
        summary = await Controller(AgentConfig(root), scenario.dependencies()).run(spec)
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.levels["verdict"], "acceptance_incomplete")
        saved = json.loads((summary.trace_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["levels"]["rules"]["state"], "incomplete")


if __name__ == "__main__":
    unittest.main()
