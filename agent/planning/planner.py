from agent.state import (
    AgentView,
    CallLimits,
    Plan,
    RequestVerification,
    RevisePlan,
    ToolAction,
)

from .llm_client import LLMClient
from .parser import parse_decision, parse_plan
from .prompts import build_decision_prompt, build_plan_prompt


class LLMPlanner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def create_plan(
        self,
        view: AgentView,
        limits: CallLimits,
    ) -> Plan:
        if view.suite is None:
            raise ValueError(
                "Acceptance suite must exist before planning"
            )

        model_response = await self.client.generate(
            system_prompt=(
                "You are the planning component of an autonomous "
                "game coding agent. Follow the supplied contract and "
                "return JSON only. Do not execute tools or hide retries."
            ),
            user_prompt=build_plan_prompt(view),
            timeout_seconds=limits.timeout_seconds,
        )

        return parse_plan(
            model_response=model_response,
            suite=view.suite,
        )

    async def next_decision(
        self,
        view: AgentView,
        limits: CallLimits,
    ) -> ToolAction | RequestVerification | RevisePlan:
        if view.plan is None:
            return RevisePlan(
                reason="No development plan exists"
            )

        model_response = await self.client.generate(
            system_prompt=(
                "You are the decision component of an autonomous "
                "game coding agent. Select exactly one next action "
                "and return JSON only. Do not execute tools, change "
                "controller state, or hide retries."
            ),
            user_prompt=build_decision_prompt(view),
            timeout_seconds=limits.timeout_seconds,
        )

        return parse_decision(
            model_response=model_response,
        )
