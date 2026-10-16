from pydantic import TypeAdapter

from agent.state import (
    AcceptanceSuite,
    Plan,
    RequestVerification,
    RevisePlan,
    Task,
    ToolAction,
    ToolKind,
)

from .schemas import (
    DecisionOutput,
    PlanOutput,
    RequestVerificationOutput,
    RevisePlanOutput,
    ToolActionOutput,
)


def extract_json_object(model_response: str) -> str:
    """Return the JSON object in a model reply.

    Models often wrap JSON in Markdown fences or add a sentence around it; the
    contract is still one JSON object, so only that object is validated.
    """
    text = model_response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("Model reply contains no JSON object")
    return text[start:end + 1]


def parse_plan(
    model_response: str,
    suite: AcceptanceSuite,
) -> Plan:
    payload = PlanOutput.model_validate_json(extract_json_object(model_response))

    criterion_ids = {
        criterion.criterion_id
        for criterion in suite.criteria
    }
    mandatory_ids = {
        criterion.criterion_id
        for criterion in suite.criteria
        if criterion.mandatory
    }

    task_ids = [task.task_id for task in payload.tasks]

    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Plan contains duplicate task IDs")

    referenced_ids: set[str] = set()

    for task in payload.tasks:
        unknown_requirements = (
            set(task.requirement_ids) - criterion_ids
        )
        if unknown_requirements:
            raise ValueError(
                "Task references unknown criterion IDs: "
                f"{sorted(unknown_requirements)}"
            )

        unknown_dependencies = (
            set(task.depends_on) - set(task_ids)
        )
        if unknown_dependencies:
            raise ValueError(
                "Task references unknown dependencies: "
                f"{sorted(unknown_dependencies)}"
            )

        if task.task_id in task.depends_on:
            raise ValueError(
                f"Task {task.task_id} depends on itself"
            )

        referenced_ids.update(task.requirement_ids)

    missing_mandatory = mandatory_ids - referenced_ids
    if missing_mandatory:
        raise ValueError(
            "Plan does not cover mandatory criteria: "
            f"{sorted(missing_mandatory)}"
        )

    _validate_acyclic_dependencies(payload)

    tasks = tuple(
        Task(
            task_id=task.task_id,
            description=task.description,
            requirement_ids=tuple(task.requirement_ids),
            depends_on=tuple(task.depends_on),
        )
        for task in payload.tasks
    )

    return Plan(
        plan_id=payload.plan_id,
        tasks=tasks,
    )


def parse_decision(
    model_response: str,
) -> ToolAction | RequestVerification | RevisePlan:
    adapter = TypeAdapter(DecisionOutput)
    payload = adapter.validate_json(extract_json_object(model_response))

    if isinstance(payload, ToolActionOutput):
        return ToolAction(
            action_id=payload.action_id,
            kind=ToolKind(payload.kind),
            arguments=payload.arguments,
            rationale=payload.rationale,
        )

    if isinstance(payload, RequestVerificationOutput):
        return RequestVerification(
            launch_command=tuple(payload.launch_command),
        )

    if isinstance(payload, RevisePlanOutput):
        return RevisePlan(reason=payload.reason)

    raise TypeError("Unsupported planner decision")


def _validate_acyclic_dependencies(
    plan: PlanOutput,
) -> None:
    dependencies = {
        task.task_id: tuple(task.depends_on)
        for task in plan.tasks
    }

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(
                "Plan contains a dependency cycle"
            )

        if task_id in visited:
            return

        visiting.add(task_id)

        for dependency in dependencies[task_id]:
            visit(dependency)

        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in dependencies:
        visit(task_id)
