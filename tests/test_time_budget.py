"""Time spent on obvious steps and the remaining budget (Member 1).

Background: in the 2026-10-27 Kimi K3 retest one run spent 204 s of its 400 s
budget reasoning before simply requesting the first verification, and the
model never saw how much time was left.
"""

from dataclasses import replace
import json
import unittest

from agent.planning import LLMPlanner
from agent.planning.prompts import DEFAULT_LAUNCH_COMMAND, build_decision_prompt
from agent.state import (
    Phase, Plan, RequestVerification, Task, ToolAction, ToolKind, ToolResult, ToolStatus,
)
from tests.test_planner import FakeLLMClient, make_limits, make_view

README = "Install: nothing\nRun: " + " ".join(DEFAULT_LAUNCH_COMMAND) + "\n"
PAGE = '<html><head></head><body><canvas></canvas><script src="game.js"></script></body></html>'


def write(name, path, content):
    return json.dumps({"decision": "tool_action", "action_id": name, "kind": "write_file",
                       "arguments": {"path": path, "content": content}, "rationale": "build"})


class FirstVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def drive(self, replies, *, extra_replies=()):
        plan = Plan("plan-001", (Task("task-001", "Build the game",
                                      ("launch-001", "functional-001", "interaction-001")),))
        client = FakeLLMClient(list(replies) + list(extra_replies))
        planner = LLMPlanner(client)
        view = make_view(plan=plan, phase=Phase.DECIDING)
        decisions = []
        for _ in range(len(replies) + 1):
            decision = await planner.next_decision(view, make_limits())
            decisions.append(decision)
            if isinstance(decision, ToolAction):
                view = replace(view, last_result=ToolResult(decision.action_id, ToolStatus.OK,
                                                             "Wrote", f"r{len(decisions)}", {"bytes": 1}))
        return decisions, client

    async def test_complete_game_is_verified_without_a_model_call(self):
        decisions, client = await self.drive([write("a", "index.html", PAGE),
                                              write("b", "game.js", "run();"),
                                              write("c", "README.md", README)])
        self.assertIsInstance(decisions[-1], RequestVerification)
        self.assertEqual(decisions[-1].launch_command, DEFAULT_LAUNCH_COMMAND)
        self.assertEqual(client.responses, [])

    async def test_missing_script_leaves_the_decision_to_the_model(self):
        model_next = write("d", "game.js", "run();")
        decisions, client = await self.drive([write("a", "index.html", PAGE),
                                              write("c", "README.md", README)],
                                             extra_replies=[model_next])
        self.assertIsInstance(decisions[-1], ToolAction)
        self.assertEqual(decisions[-1].arguments["path"], "game.js")

    async def test_readme_written_first_is_not_a_trigger(self):
        decisions, _client = await self.drive([write("c", "README.md", README),
                                               write("a", "index.html", PAGE)],
                                              extra_replies=[write("b", "game.js", "run();")])
        self.assertFalse(any(isinstance(d, RequestVerification) for d in decisions))

    async def test_only_before_the_first_verification(self):
        plan = Plan("plan-001", (Task("task-001", "Build the game",
                                      ("launch-001", "functional-001", "interaction-001")),))
        planner = LLMPlanner(FakeLLMClient([write("c", "README.md", README),
                                            json.dumps({"decision": "tool_action", "action_id": "e",
                                                        "kind": "inspect_workspace", "arguments": {},
                                                        "rationale": "look"})]))
        planner._texts["index.html"] = "<canvas></canvas>"
        planner._files["index.html"] = 17
        view = replace(make_view(plan=plan, phase=Phase.DECIDING), launch_command=DEFAULT_LAUNCH_COMMAND)
        first = await planner.next_decision(view, make_limits())
        view = replace(view, last_result=ToolResult(first.action_id, ToolStatus.OK, "Wrote", "r2", {}))
        second = await planner.next_decision(view, make_limits())
        self.assertEqual(second.kind, ToolKind.INSPECT_WORKSPACE)


class RemainingTimeTests(unittest.TestCase):
    def test_prompt_shows_remaining_time_and_short_budget_rule(self):
        view = make_view(plan=Plan("p", (Task("t", "x", ("launch-001",)),)), phase=Phase.DECIDING)
        roomy = build_decision_prompt(replace(view, remaining_seconds=300))
        self.assertIn("about 300s of work time left", roomy)
        self.assertNotIn("SHORT:", roomy)
        short = build_decision_prompt(replace(view, remaining_seconds=90))
        self.assertIn("SHORT: make only the smallest patch_file change", short)
        self.assertNotIn("work time left", build_decision_prompt(view))


if __name__ == "__main__":
    unittest.main()
