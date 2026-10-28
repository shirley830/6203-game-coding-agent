"""Disputed checks (test_conflict): one bounded re-derivation, never a weaker rule.

Background: in the 2026-10-27 Kimi K3 retest the minesweeper check REQ-R1
seeded mines so that the first click flood-revealed the whole board and won,
yet expected status "playing". The correct game failed it twice and the run
spent its budget repairing a game that was right.
"""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.planning.llm_client import _provider_cost
from agent.planning.parser import parse_decision
from agent.planning.prompts import build_decision_prompt
from agent.recovery import RuleBasedRecovery
from agent.result_levels import render_levels
from agent.state import (
    AcceptanceSuite, AgentView, CallLimits, CheckStatus, Criterion, DisputeCheck,
    FailureRecord, Phase, RecoveryKind, RequestVerification, ToolAction, ToolKind,
)
from agent.verification.spec_criteria import parse_revision, parse_spec_acceptance
from agent.verification.verifier import GameVerifier
from tests.test_controller import Scenario

FAIL_DETAIL = ('step 4 {"expect": "s.status === \'playing\'"}: expectation false: '
               "s.status === 'playing'; state after: {\"status\": \"won\"}")

ACCEPTANCE = {
    "state_contract": {"state_fields": {"status": "'playing' | 'won' | 'lost'"},
                       "test_hooks": {"seedMines": "seedMines(list): place mines"}},
    "requirements": [
        {"id": "R1", "category": "interaction", "description": "Clicking a safe cell keeps playing",
         "steps": [{"call": "seedMines", "args": [[[0, 0]]]}, {"click_cell": [4, 4]},
                   {"expect": "s.status === 'playing'"}]},
        {"id": "R2", "category": "functional", "description": "Clicking a mine loses the game",
         "steps": [{"call": "seedMines", "args": [[[0, 0]]]}, {"click_cell": [0, 0]},
                   {"expect": "s.status === 'lost'"}]},
        {"id": "R3", "category": "functional", "description": "Revealing all safe cells wins",
         "steps": [{"call": "seedMines", "args": [[[0, 0]]]}, {"click_cell": [8, 8]},
                   {"expect": "s.status === 'won'"}]},
    ],
}
SPEC = "Minesweeper. Click a cell to reveal it; empty cells flood-reveal their neighbours."
NEW_R1 = {"id": "R1", "category": "interaction", "description": "Clicking a safe numbered cell keeps playing",
          "steps": [{"call": "seedMines", "args": [[[0, 0], [2, 0]]]}, {"click_cell": [1, 0]},
                    {"expect": "s.status === 'playing'"}]}

VERIFY = RequestVerification(("fake-launch",))


def limits():
    return CallLimits(timeout_seconds=30, steps_remaining=50, commands_remaining=10)


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        self.prompts.append(user_prompt)
        return self.replies.pop(0)


class ParsingTests(unittest.TestCase):
    def test_planner_dispute_decision(self):
        decision = parse_decision(json.dumps({"decision": "dispute_check", "criterion_id": "REQ-R1",
                                              "reason": "flood reveal wins on the first click"}))
        self.assertEqual(decision, DisputeCheck("REQ-R1", "flood reveal wins on the first click"))
        implied = parse_decision(json.dumps({"criterion_id": "REQ-R1",
                                             "reason": "the setup already wins the game"}))
        self.assertIsInstance(implied, DisputeCheck)
        long = parse_decision(json.dumps({"decision": "dispute_check", "criterion_id": "REQ-R3",
                                          "reason": "flood " * 400}))
        self.assertEqual(len(long.reason), 1500)

    def test_out_of_credit_stops_with_a_clear_reason(self):
        import asyncio
        message = ('APIError: litellm.APIError: OpenrouterException - {"error":{"message":"This request '
                   'requires more credits, or fewer max_tokens.","code":402}}')
        failure = FailureRecord("planner.next_decision", Phase.DECIDING, "module", message)
        view = AgentView("run", Phase.RECOVERING, "", "", "r1", None, None, 1, (), None, None, 1, 1, 0, 1.0)
        decision = asyncio.run(RuleBasedRecovery().recommend_recovery(failure, view, limits()))
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertIn("no credit left", decision.reason)

    def test_revision_keep_replace_and_rejections(self):
        acceptance = parse_spec_acceptance(json.dumps(ACCEPTANCE), SPEC)
        self.assertEqual(parse_revision('{"verdict": "keep", "reason": "consistent"}',
                                        acceptance, "R1", SPEC)[0], "keep")
        verdict, _, new = parse_revision(json.dumps({"verdict": "replace", "reason": "flood wins",
                                                     "requirement": NEW_R1}), acceptance, "R1", SPEC)
        self.assertEqual((verdict, new.id), ("replace", "R1"))
        renamed = dict(NEW_R1, id="R9")
        with self.assertRaises(ValueError):
            parse_revision(json.dumps({"verdict": "replace", "requirement": renamed}), acceptance, "R1", SPEC)
        no_expect = dict(NEW_R1, steps=[{"click_cell": [1, 0]}])
        with self.assertRaises(ValueError):
            parse_revision(json.dumps({"verdict": "replace", "requirement": no_expect}), acceptance, "R1", SPEC)
        with self.assertRaises(ValueError):
            parse_revision('{"verdict": "drop"}', acceptance, "R1", SPEC)

    def test_provider_cost(self):
        self.assertEqual(_provider_cost({"cost": 0.0021}), 0.0021)
        self.assertIsNone(_provider_cost({"prompt_tokens": 3}))
        self.assertIsNone(_provider_cost(None))

    def test_policy_routes_conflict_to_revision_only_without_action(self):
        policy = RuleBasedRecovery()
        view = AgentView("run", Phase.RECOVERING, "", "", "r1", None, None, 1, (), None, None, 1, 1, 0, 1.0)
        conflict = FailureRecord("verifier.next_check", Phase.VERIFYING, "test_conflict", "REQ-R1")
        import asyncio
        decision = asyncio.run(policy.recommend_recovery(conflict, view, limits()))
        self.assertEqual(decision.kind, RecoveryKind.REVISE_ACCEPTANCE)
        claimed = replace(conflict, operation_key="tool.run_command",
                          action=ToolAction("a", ToolKind.RUN_COMMAND, {"argv": ["x"]}))
        self.assertNotEqual(asyncio.run(policy.recommend_recovery(claimed, view, limits())).kind,
                            RecoveryKind.REVISE_ACCEPTANCE)


class VerifierRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient(json.dumps(ACCEPTANCE))
        self.verifier = GameVerifier(client=self.client)
        view = AgentView("run", Phase.INITIALIZING, SPEC, "d", None, None, None, 0, (), None, None, 0, 0, 0, 0.0)
        self.suite = await self.verifier.prepare_acceptance(view, limits())
        self.view = replace(view, suite=self.suite)

    async def test_replace_builds_new_suite_and_keeps_old_version(self):
        self.client.replies.append(json.dumps({"verdict": "replace", "reason": "flood wins",
                                               "requirement": NEW_R1}))
        result = await self.verifier.revise_requirement(self.view, "REQ-R1", FAIL_DETAIL,
                                                        "first click wins", limits())
        self.assertEqual(result["verdict"], "replace")
        suite = result["suite"]
        self.assertNotEqual(suite.version, self.suite.version)
        self.assertEqual([c.criterion_id for c in suite.criteria], [c.criterion_id for c in self.suite.criteria])
        self.assertIn("numbered cell", next(c.description for c in suite.criteria if c.criterion_id == "REQ-R1"))
        self.assertIn("Apply the specification's rules in full", self.client.prompts[-1])
        record = self.verifier.revisions[-1]
        self.assertEqual(record["old"]["description"], "Clicking a safe cell keeps playing")
        self.assertEqual(record["new"]["description"], NEW_R1["description"])

    async def test_unusable_reply_is_an_error_not_a_change(self):
        self.client.replies.append("not json")
        result = await self.verifier.revise_requirement(self.view, "REQ-R1", FAIL_DETAIL, "x", limits())
        self.assertEqual((result["verdict"], result["suite"]), ("error", None))

    async def test_generic_check_is_not_revisable(self):
        result = await self.verifier.revise_requirement(self.view, "C-LAUNCH", FAIL_DETAIL, "x", limits())
        self.assertEqual(result["verdict"], "error")


class ControllerConflictTests(unittest.IsolatedAsyncioTestCase):
    """Real Controller + RuleBasedRecovery with the fake ports from test_controller."""

    async def run_flow(self, *, decisions, fails, revision, others=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        spec = root / "game_design_prompt.md"
        spec.write_text(SPEC, encoding="utf-8")
        scenario = Scenario()
        details = {"REQ-R1": FAIL_DETAIL, **(others or {})}
        scenario.suite = AcceptanceSuite("v1", scenario.suite.criteria + tuple(
            Criterion(cid, f"{cid}: clicking a safe cell keeps playing", "interaction") for cid in details))
        scenario.decisions = list(decisions)
        calls = []

        def failing(report):
            results = tuple(replace(r, status=CheckStatus.FAIL, detail=details[r.criterion_id])
                            if r.criterion_id in details else r for r in report.results)
            return replace(report, results=results)

        scenario.reports = [failing] * fails

        async def revise_requirement(view, criterion_id, evidence, reason, limits):
            calls.append((criterion_id, reason, view.suite.version))
            scenario.check_requested = False  # the real verifier starts a new cycle for a new suite
            if revision == "replace":
                new = AcceptanceSuite(view.suite.version + "-rev", tuple(
                    replace(c, description="Clicking a numbered cell keeps playing")
                    if c.criterion_id == criterion_id else c for c in view.suite.criteria))
                return {"verdict": "replace", "reason": "flood reveal wins at once", "suite": new}
            return {"verdict": "keep", "reason": "a correct game passes", "suite": None}

        scenario.revise_requirement = revise_requirement
        controller = Controller(AgentConfig(root), replace(scenario.dependencies(),
                                                           recovery=RuleBasedRecovery()))
        summary = await controller.run(spec)
        events = [json.loads(line) for line in (summary.trace_dir / "events.jsonl").read_text().splitlines()]
        return summary, scenario, calls, events

    def write(self, name):
        return ToolAction(name, ToolKind.WRITE_FILE, {"path": f"{name}.js", "content": name})

    async def test_same_failure_after_a_change_rederives_the_check_once(self):
        summary, scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, self.write("b")], fails=2, revision="replace")
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual([c[0] for c in calls], ["REQ-R1"])
        failures = [e["payload"]["category"] for e in events if e["kind"] == "failure"]
        self.assertEqual(failures, ["verification", "test_conflict"])
        revised = [e for e in events if e["kind"] == "acceptance_revised"]
        self.assertEqual(revised[0]["payload"]["old"], "REQ-R1: clicking a safe cell keeps playing")
        writes = [a for a in scenario.actions if a.kind == ToolKind.WRITE_FILE]
        self.assertEqual(len(writes), 2)  # no game edit after the conflict
        self.assertEqual(summary.levels["revised_checks"][0]["verdict"], "replace")
        self.assertIn("re-derived (重做): REQ-R1", "\n".join(render_levels(summary.levels)))

    async def test_planner_dispute_kept_check_goes_back_to_repair(self):
        dispute = DisputeCheck("REQ-R1", "The flood reveal wins the game on the first click.")
        summary, _scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, dispute, self.write("b")], fails=1, revision="keep")
        self.assertEqual(len(calls), 1)
        self.assertIn("first click", calls[0][1])
        self.assertTrue(any(e["kind"] == "acceptance_kept" for e in events))
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.levels["revised_checks"][0]["verdict"], "keep")

    async def test_second_dispute_of_the_same_check_is_rejected(self):
        dispute = DisputeCheck("REQ-R1", "The flood reveal wins the game on the first click.")
        _summary, _scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, dispute, dispute, self.write("b")], fails=2, revision="keep")
        self.assertEqual(len(calls), 1)
        messages = [e["payload"]["message"] for e in events if e["kind"] == "failure"]
        self.assertTrue(any("Dispute of REQ-R1 rejected" in m for m in messages))

    async def test_dispute_of_a_check_that_did_not_fail_is_rejected(self):
        dispute = DisputeCheck("C-LAUNCH", "The launch check is wrong for this game.")
        _summary, _scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, dispute, self.write("b")], fails=1, revision="replace")
        self.assertEqual(calls, [])
        self.assertFalse(any(e["kind"] == "acceptance_revised" for e in events))

    async def test_dispute_rederives_failed_checks_with_the_same_end_state(self):
        dispute = DisputeCheck("REQ-R1", "The flood reveal wins the game on the first click.")
        other = FAIL_DETAIL.replace("step 4", "step 6")
        summary, _scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, dispute], fails=1, revision="replace",
            others={"REQ-R2": other, "REQ-R3": "step 2: expectation false; state after: {\"status\": \"lost\"}"})
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual([c[0] for c in calls], ["REQ-R1", "REQ-R2"])  # REQ-R3 ended differently
        self.assertIn("same state as REQ-R1", calls[1][1])
        self.assertEqual(calls[1][2], "v1-rev")  # built on the suite with REQ-R1 already replaced
        self.assertEqual(len([e for e in events if e["kind"] == "acceptance_revised"]), 2)

    async def test_no_dispute_is_offered_after_the_budget_is_used(self):
        dispute1 = DisputeCheck("REQ-R1", "The flood reveal wins the game on the first click.")
        dispute2 = DisputeCheck("REQ-R2", "The flood reveal wins the game on the first click.")
        _summary, _scenario, calls, events = await self.run_flow(
            decisions=[self.write("a"), VERIFY, dispute1, dispute2, dispute2], fails=3, revision="keep",
            others={"REQ-R2": "step 1: expectation false; state after: {\"status\": \"lost\"}"})
        self.assertEqual([c[0] for c in calls], ["REQ-R1", "REQ-R2"])
        messages = [e["payload"]["message"] for e in events if e["kind"] == "failure"]
        self.assertTrue(any("Dispute of REQ-R2 rejected" in m for m in messages))

    def test_prompt_says_when_no_dispute_is_left(self):
        view = AgentView("run", Phase.DECIDING, SPEC, "d", None, None, None, 0, (), None, None, 0, 0, 0, 0.0)
        self.assertIn("Disputes left in this run: 2.", build_decision_prompt(replace(view, disputes_left=2)))
        exhausted = build_decision_prompt(replace(view, disputes_left=0))
        self.assertIn("No check disputes are left in this run: never return dispute_check", exhausted)
        self.assertNotIn("Disputes left in this run", exhausted)


if __name__ == "__main__":
    unittest.main()
