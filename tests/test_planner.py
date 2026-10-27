import json
from dataclasses import replace
import unittest

from agent.planning import LLMPlanner
from agent.planning.prompts import (
    build_decision_prompt,
    build_plan_prompt,
)
from agent.state import (
    ToolResult,
    ToolStatus,
    AcceptanceSuite,
    AgentView,
    CallLimits,
    Criterion,
    Phase,
    Plan,
    RequestVerification,
    Task,
    ToolAction,
    ToolKind,
)


class FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> str:
        if not self.responses:
            raise RuntimeError("No fake response available")

        return self.responses.pop(0)


def make_suite() -> AcceptanceSuite:
    return AcceptanceSuite(
        version="test-suite-v1",
        criteria=(
            Criterion(
                criterion_id="launch-001",
                description="The game launches successfully",
                category="launch",
                mandatory=True,
            ),
            Criterion(
                criterion_id="functional-001",
                description="The main game functionality works",
                category="functional",
                mandatory=True,
            ),
            Criterion(
                criterion_id="interaction-001",
                description="The player can move",
                category="interaction",
                mandatory=True,
            ),
        ),
    )


def make_view(
    *,
    plan: Plan | None = None,
    phase: Phase = Phase.PLANNING,
) -> AgentView:
    return AgentView(
        run_id="test-run",
        phase=phase,
        spec_text="Create a game with player movement.",
        spec_digest="test-digest",
        workspace_revision="revision-001",
        suite=make_suite(),
        plan=plan,
        plan_version=1 if plan is not None else 0,
        launch_command=(),
        last_result=None,
        last_failure=None,
        steps_used=0,
        commands_used=0,
        recovery_attempts=0,
        elapsed_seconds=0.0,
    )


def make_limits() -> CallLimits:
    return CallLimits(
        timeout_seconds=10.0,
        steps_remaining=20,
        commands_remaining=10,
    )


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_plan(self) -> None:
        client = FakeLLMClient(
            responses=[
                """
                {
                  "plan_id": "plan-001",
                  "tasks": [
                    {
                      "task_id": "task-001",
                      "description": "Create the launch entry point",
                      "requirement_ids": ["launch-001"],
                      "depends_on": []
                    },
                    {
                      "task_id": "task-002",
                      "description": "Implement player movement",
                      "requirement_ids": [
                        "functional-001",
                        "interaction-001"
                      ],
                      "depends_on": ["task-001"]
                    }
                  ]
                }
                """
            ]
        )

        planner = LLMPlanner(client)
        plan = await planner.create_plan(
            make_view(),
            make_limits(),
        )

        self.assertEqual(plan.plan_id, "plan-001")
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(
            plan.tasks[1].depends_on,
            ("task-001",),
        )

    async def test_next_decision_returns_tool_action(
        self,
    ) -> None:
        existing_plan = Plan(
            plan_id="plan-001",
            tasks=(
                Task(
                    task_id="task-001",
                    description="Create the game",
                    requirement_ids=(
                        "launch-001",
                        "interaction-001",
                    ),
                ),
            ),
        )

        client = FakeLLMClient(
            responses=[
                """
                {
                  "decision": "tool_action",
                  "action_id": "write-main-001",
                  "kind": "write_file",
                  "arguments": {
                    "path": "main.py",
                    "content": "print('game')"
                  },
                  "rationale": "Create the game entry point"
                }
                """
            ]
        )

        planner = LLMPlanner(client)
        decision = await planner.next_decision(
            make_view(
                plan=existing_plan,
                phase=Phase.DECIDING,
            ),
            make_limits(),
        )

        self.assertIsInstance(decision, ToolAction)
        self.assertEqual(
            decision.kind,
            ToolKind.WRITE_FILE,
        )
        self.assertEqual(
            decision.arguments["path"],
            "main.py",
        )

    async def test_missing_plan_requests_revision(
        self,
    ) -> None:
        planner = LLMPlanner(FakeLLMClient([]))

        decision = await planner.next_decision(
            make_view(
                plan=None,
                phase=Phase.DECIDING,
            ),
            make_limits(),
        )

        self.assertEqual(
            decision.reason,
            "No development plan exists",
        )
        
    async def test_plan_must_cover_every_mandatory_criterion(
        self,
    ) -> None:
        client = FakeLLMClient(
            responses=[
                """
                {
                  "plan_id": "incomplete-plan",
                  "tasks": [
                    {
                      "task_id": "task-001",
                      "description": "Create the game",
                      "requirement_ids": [
                        "launch-001",
                        "interaction-001"
                      ],
                      "depends_on": []
                    }
                  ]
                }
                """
            ]
        )

        planner = LLMPlanner(client)

        with self.assertRaises(ValueError):
            await planner.create_plan(
                make_view(),
                make_limits(),
            )

    async def test_request_verification_with_launch_command(
        self,
    ) -> None:
        existing_plan = Plan(
            plan_id="plan-001",
            tasks=(
                Task(
                    task_id="task-001",
                    description="Create the game",
                    requirement_ids=(
                        "launch-001",
                        "functional-001",
                        "interaction-001",
                    ),
                ),
            ),
        )

        # The planner only forwards a launch command whose files exist in
        # game/ and whose README.md documents that exact command.
        client = FakeLLMClient(
            responses=[
                json.dumps({"decision": "tool_action", "action_id": "a", "kind": "write_file",
                            "arguments": {"path": "main.py", "content": "print('game')"}}),
                json.dumps({"decision": "tool_action", "action_id": "b", "kind": "write_file",
                            "arguments": {"path": "README.md",
                                          "content": "Install: none\nRun: python main.py\n"}}),
                """
                {
                  "decision": "request_verification",
                  "launch_command": ["python", "main.py"]
                }
                """,
            ]
        )

        planner = LLMPlanner(client)
        view = make_view(plan=existing_plan, phase=Phase.DECIDING)
        last_result = None
        for _ in range(2):
            action = await planner.next_decision(
                replace(view, last_result=last_result), make_limits(),
            )
            last_result = ToolResult(action.action_id, ToolStatus.OK, "Wrote", "rev")
        decision = await planner.next_decision(
            replace(view, last_result=last_result), make_limits(),
        )

        self.assertIsInstance(
            decision,
            RequestVerification,
        )
        self.assertEqual(
            decision.launch_command,
            ("python", "main.py"),
        )

    async def test_empty_launch_argument_is_rejected(
        self,
    ) -> None:
        existing_plan = Plan(
            plan_id="plan-001",
            tasks=(
                Task(
                    task_id="task-001",
                    description="Create the game",
                    requirement_ids=(
                        "launch-001",
                        "functional-001",
                        "interaction-001",
                    ),
                ),
            ),
        )

        client = FakeLLMClient(
            responses=[
                """
                {
                  "decision": "request_verification",
                  "launch_command": ["python", ""]
                }
                """
            ]
        )

        planner = LLMPlanner(client)

        with self.assertRaises(ValueError):
            await planner.next_decision(
                make_view(
                    plan=existing_plan,
                    phase=Phase.DECIDING,
                ),
                make_limits(),
            )

    def test_prompts_include_default_quality_rules(
        self,
    ) -> None:
        plan_prompt = build_plan_prompt(make_view())
        decision_prompt = build_decision_prompt(make_view())

        self.assertIn(
            "presentation-ready browser game",
            plan_prompt,
        )
        self.assertIn(
            "presentation-ready",
            plan_prompt,
        )
        self.assertIn(
            "never a separate .css file",
            plan_prompt,
        )
        self.assertIn(
            "presentation-ready course demo",
            decision_prompt,
        )
        self.assertIn(
            "Never delay a verification request for further visual polishing",
            decision_prompt,
        )
        self.assertNotIn(
            "instead of\n  requesting verification",
            decision_prompt,
        )
        self.assertIn(
            "independently testable implementation tasks",
            plan_prompt,
        )
        self.assertIn(
            "minimal dependencies",
            plan_prompt,
        )
        self.assertIn(
            "clearly distinguishable",
            plan_prompt,
        )
        self.assertIn(
            "complete core gameplay loop",
            decision_prompt,
        )
        self.assertIn(
            "difficult to see",
            decision_prompt,
        )
        self.assertIn(
            "prefer\n  patch_file",
            decision_prompt,
        )
        
        self.assertIn(
            "original specification as the source of truth",
            plan_prompt,
        )
        self.assertIn(
            "override, weaken, remove, or invent requirements",
            plan_prompt,
        )
        self.assertIn(
            "test-only behaviour",
            plan_prompt,
        )
        self.assertIn(
            "Verification results are diagnostic evidence",
            decision_prompt,
        )
        self.assertIn(
            "merely to make the conflicting test pass",
            decision_prompt,
        )
        self.assertIn(
            "Never remove, weaken, or reinterpret",
            decision_prompt,
        )
        
class FileContentMemoryTests(unittest.IsolatedAsyncioTestCase):
    """Decisions see current file texts, kept up to date across patches."""

    async def test_written_and_patched_text_is_shown_in_later_prompts(self):
        plan = Plan(plan_id="plan-001", tasks=(Task(
            task_id="task-001", description="Create the game",
            requirement_ids=("launch-001", "functional-001", "interaction-001")),))
        prompts: list[str] = []

        class Recording(FakeLLMClient):
            async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
                prompts.append(user_prompt)
                return await super().generate(system_prompt=system_prompt,
                                              user_prompt=user_prompt,
                                              timeout_seconds=timeout_seconds)

        client = Recording([
            json.dumps({"decision": "tool_action", "action_id": "x", "kind": "write_file",
                        "arguments": {"path": "game.js", "content": "let speed = 1;\nrun();\n"},
                        "rationale": "first version"}),
            json.dumps({"decision": "tool_action", "action_id": "y", "kind": "patch_file",
                        "arguments": {"path": "game.js",
                                      "patch": "@@ -1,2 +1,2 @@\n-let speed = 1;\n+let speed = 2;\n run();\n"},
                        "rationale": "fix speed"}),
            json.dumps({"decision": "tool_action", "action_id": "z", "kind": "inspect_workspace",
                        "arguments": {}, "rationale": "look"}),
        ])
        planner = LLMPlanner(client)
        view = make_view(plan=plan, phase=Phase.DECIDING)
        first = await planner.next_decision(view, make_limits())
        view = replace(view, last_result=ToolResult(first.action_id, ToolStatus.OK, "Wrote", "r2",
                                                    {"bytes": 22}))
        second = await planner.next_decision(view, make_limits())
        self.assertIn("===== game.js =====\nlet speed = 1;", prompts[1])
        view = replace(view, last_result=ToolResult(second.action_id, ToolStatus.OK, "Patched", "r3",
                                                     {"bytes": 22}))
        await planner.next_decision(view, make_limits())
        self.assertIn("===== game.js =====\nlet speed = 2;", prompts[2])
        self.assertNotIn("let speed = 1;", prompts[2].split("CURRENT FILE CONTENTS")[1].split("RECENT ACTIONS")[0])

    async def test_decisions_can_use_a_separate_client(self):
        plan = Plan(plan_id="plan-001", tasks=(Task(
            task_id="task-001", description="Create the game",
            requirement_ids=("launch-001", "functional-001", "interaction-001")),))
        main = FakeLLMClient([])
        decisions = FakeLLMClient([json.dumps({"decision": "tool_action", "action_id": "x",
                                               "kind": "inspect_workspace", "arguments": {},
                                               "rationale": "look"})])
        planner = LLMPlanner(main, decision_client=decisions)
        action = await planner.next_decision(make_view(plan=plan, phase=Phase.DECIDING), make_limits())
        self.assertEqual(action.kind, ToolKind.INSPECT_WORKSPACE)
        self.assertEqual(decisions.responses, [])


if __name__ == "__main__":
    unittest.main()
