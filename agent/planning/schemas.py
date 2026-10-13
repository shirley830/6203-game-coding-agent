from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskOutput(StrictModel):
    task_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class PlanOutput(StrictModel):
    plan_id: str = Field(min_length=1)
    tasks: list[TaskOutput] = Field(min_length=1)


class ToolActionOutput(StrictModel):
    decision: Literal["tool_action"]
    action_id: str = Field(min_length=1)
    kind: Literal[
        "inspect_workspace",
        "read_file",
        "write_file",
        "patch_file",
        "run_command",
        "install_dependencies",
    ]
    arguments: dict[str, Any]
    rationale: str = ""


class RequestVerificationOutput(StrictModel):
    decision: Literal["request_verification"]
    launch_command: list[str] = Field(min_length=1)


class RevisePlanOutput(StrictModel):
    decision: Literal["revise_plan"]
    reason: str = Field(min_length=1)


DecisionOutput = Annotated[
    ToolActionOutput
    | RequestVerificationOutput
    | RevisePlanOutput,
    Field(discriminator="decision"),
]
