from agent.state import (
    AgentView,
    CallLimits,
    Plan,
    RequestVerification,
    RevisePlan,
    ToolAction,
)

from .llm_client import LLMClient


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

        raise NotImplementedError

    async def next_decision(
        self,
        view: AgentView,
        limits: CallLimits,
    ) -> ToolAction | RequestVerification | RevisePlan:
        if view.plan is None:
            return RevisePlan(reason="No development plan exists")

        raise NotImplementedError
