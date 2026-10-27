import json

from pydantic import TypeAdapter

from agent.state import (
    AcceptanceSuite,
    DisputeCheck,
    Plan,
    RequestVerification,
    RevisePlan,
    Task,
    ToolAction,
    ToolKind,
)

from .schemas import (
    DecisionOutput,
    DisputeCheckOutput,
    PlanOutput,
    RequestVerificationOutput,
    RevisePlanOutput,
    ToolActionOutput,
)


def extract_json_object(model_response: str) -> str:
    """Return the first complete JSON object in a model reply.

    Models often wrap JSON in Markdown fences, add a sentence around it, or
    append a second object; the contract is one JSON object, so only the first
    complete object is used.
    """
    text = model_response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    if start == -1:
        raise ValueError("Model reply contains no JSON object")
    try:
        _, end = json.JSONDecoder().raw_decode(text, start)
        return text[start:end]
    except json.JSONDecodeError:
        end = text.rfind("}")
        if end < start:
            raise ValueError("Model reply contains no JSON object")
        return text[start:end + 1]


_TOOL_KINDS = {"inspect_workspace", "read_file", "write_file", "patch_file",
               "run_command", "install_dependencies"}
_ARGUMENT_KEYS = {"path", "content", "patch", "argv", "cwd", "packages"}
_DECISION_ALIASES = {
    "tool": "tool_action", "action": "tool_action", "tool_call": "tool_action",
    "verify": "request_verification", "verification": "request_verification",
    "revise": "revise_plan", "replan": "revise_plan",
    "dispute": "dispute_check", "test_conflict": "dispute_check",
}
_DECISION_FIELDS = {
    "tool_action": {"decision", "action_id", "kind", "arguments", "rationale"},
    "request_verification": {"decision", "launch_command"},
    "revise_plan": {"decision", "reason"},
    "dispute_check": {"decision", "criterion_id", "reason"},
}


def normalize_decision(payload: object) -> object:
    """Repair common, unambiguous format slips before strict validation.

    For example {"decision": "read_file", "path": "x"} becomes a tool_action of
    kind read_file. Anything ambiguous is left for validation to reject.
    """
    if not isinstance(payload, dict):
        return payload
    data = dict(payload)
    decision = data.get("decision")
    if decision in _TOOL_KINDS:
        data.setdefault("kind", decision)
        decision = "tool_action"
    elif isinstance(decision, str) and decision in _DECISION_ALIASES:
        decision = _DECISION_ALIASES[decision]
    elif decision is None:
        if data.get("kind") in _TOOL_KINDS:
            decision = "tool_action"
        elif "launch_command" in data:
            decision = "request_verification"
        elif "criterion_id" in data and "reason" in data:
            decision = "dispute_check"
        elif "reason" in data:
            decision = "revise_plan"
    if decision not in _DECISION_FIELDS:
        return payload
    data["decision"] = decision
    if decision == "tool_action":
        arguments = dict(data.get("arguments") or {})
        for key in _ARGUMENT_KEYS & set(data):
            arguments.setdefault(key, data.pop(key))
        data["arguments"] = arguments
        data.setdefault("action_id", "auto")
    return {key: value for key, value in data.items() if key in _DECISION_FIELDS[decision]}


def _strip_plan(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        allowed = {"task_id", "description", "requirement_ids", "depends_on"}
        tasks = [{k: v for k, v in t.items() if k in allowed} if isinstance(t, dict) else t
                 for t in tasks]
    return {"plan_id": payload.get("plan_id", "plan"), "tasks": tasks}


def parse_plan(
    model_response: str,
    suite: AcceptanceSuite,
) -> Plan:
    payload = PlanOutput.model_validate(_strip_plan(json.loads(extract_json_object(model_response))))

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
) -> ToolAction | RequestVerification | RevisePlan | DisputeCheck:
    adapter = TypeAdapter(DecisionOutput)
    payload = adapter.validate_python(normalize_decision(json.loads(extract_json_object(model_response))))

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

    if isinstance(payload, DisputeCheckOutput):
        return DisputeCheck(criterion_id=payload.criterion_id, reason=payload.reason[:1500])

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
