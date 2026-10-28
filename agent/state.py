"""Shared contracts. Ports receive immutable views; the controller owns state."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


def freeze(value: Any) -> Any:
    """Accept only JSON-compatible payloads and recursively remove mutability."""
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("Payload keys must be strings")
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        import math
        if math.isfinite(value):
            return value
    raise TypeError(f"Not a JSON payload: {type(value).__name__}")


def to_jsonable(value: Any) -> Any:
    """Convert snapshots and events to plain JSON values, without live objects."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        freeze(value)
        return value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class Phase(str, Enum):
    INITIALIZING = "initializing"
    PLANNING = "planning"
    DECIDING = "deciding"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


TERMINAL_PHASES = frozenset({Phase.SUCCEEDED, Phase.FAILED, Phase.EXHAUSTED, Phase.CANCELLED})


class ToolKind(str, Enum):
    PREPARE_WORKSPACE = "prepare_workspace"
    READ_SPEC = "read_spec"
    INSPECT_WORKSPACE = "inspect_workspace"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    PATCH_FILE = "patch_file"
    RUN_COMMAND = "run_command"
    INSTALL_DEPENDENCIES = "install_dependencies"


class ToolStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    UNKNOWN = "unknown"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    UNKNOWN = "unknown"


class RecoveryKind(str, Enum):
    RETRY = "retry"
    REPAIR = "repair"
    REPLAN = "replan"
    ABORT = "abort"
    # Re-derive one disputed spec-derived check, then re-verify the unchanged game.
    REVISE_ACCEPTANCE = "revise_acceptance"


@dataclass(frozen=True)
class ToolAction:
    action_id: str
    kind: ToolKind
    arguments: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", freeze(self.arguments))


@dataclass(frozen=True)
class ToolResult:
    action_id: str
    status: ToolStatus
    observation: str
    workspace_revision: str | None
    data: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    side_effects_unknown: bool = False
    output_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze(self.data))
        object.__setattr__(self, "output_refs", tuple(self.output_refs))


@dataclass(frozen=True)
class Task:
    task_id: str
    description: str
    requirement_ids: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_ids", tuple(self.requirement_ids))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))


@dataclass(frozen=True)
class Plan:
    plan_id: str
    tasks: tuple[Task, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(self.tasks))


@dataclass(frozen=True)
class RequestVerification:
    launch_command: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "launch_command", tuple(self.launch_command))


@dataclass(frozen=True)
class RevisePlan:
    reason: str


@dataclass(frozen=True)
class DisputeCheck:
    """The planner argues that a failed spec-derived check contradicts the specification.

    The controller turns it into a ``test_conflict`` failure; the check is then
    re-derived once with this reason as evidence. The game is not edited.
    """
    criterion_id: str
    reason: str


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    description: str
    category: str  # launch / functional / interaction / technical
    mandatory: bool = True


@dataclass(frozen=True)
class AcceptanceSuite:
    version: str
    criteria: tuple[Criterion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", tuple(self.criteria))


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    status: CheckStatus
    evidence_refs: tuple[str, ...]
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True)
class VerificationReport:
    spec_digest: str
    workspace_revision: str
    suite_version: str
    results: tuple[CriterionResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))


@dataclass(frozen=True)
class FailureRecord:
    operation_key: str
    phase: Phase
    category: str
    message: str
    action: ToolAction | None = None
    side_effects_unknown: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    kind: RecoveryKind
    reason: str
    safe_to_retry: bool = False


@dataclass(frozen=True)
class CallLimits:
    timeout_seconds: float
    steps_remaining: int
    commands_remaining: int


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    run_root: Path
    spec_path: Path
    game_dir: Path
    trace_dir: Path


@dataclass(frozen=True)
class AgentView:
    run_id: str
    phase: Phase
    spec_text: str
    spec_digest: str
    workspace_revision: str | None
    suite: AcceptanceSuite | None
    plan: Plan | None
    plan_version: int
    launch_command: tuple[str, ...]
    last_result: ToolResult | None
    last_failure: FailureRecord | None
    steps_used: int
    commands_used: int
    recovery_attempts: int
    elapsed_seconds: float
    # Work time left before the deadline (None when the caller does not know).
    remaining_seconds: float | None = None
    # Check disputes the planner may still raise (None when the caller does not know).
    disputes_left: int | None = None


@dataclass(frozen=True)
class TraceEvent:
    run_id: str
    seq: int
    timestamp: str
    kind: str
    phase: Phase
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze(self.payload))


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    outcome: Phase
    reason: str
    game_dir: Path
    trace_dir: Path
    launch_command: tuple[str, ...]
    verification: VerificationReport | None
    steps_used: int
    commands_used: int
    recovery_attempts: int
    elapsed_seconds: float
    # Launch / specific rules / delivery, see agent/result_levels.py.
    levels: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DeliveryReport:
    preserved: bool
    trace_dir: Path
    detail: str = ""


@dataclass
class AgentState:
    """Private mutable execution state; never passed directly to a module."""

    run_id: str
    phase: Phase = Phase.INITIALIZING
    schema_version: int = 1
    spec_text: str = ""
    spec_digest: str = ""
    workspace_revision: str | None = None
    suite: AcceptanceSuite | None = None
    plan: Plan | None = None
    plan_version: int = 0
    launch_command: tuple[str, ...] = ()
    last_result: ToolResult | None = None
    last_failure: FailureRecord | None = None
    pending_action: ToolAction | None = None
    execution_return_phase: Phase = Phase.DECIDING
    verification_report: VerificationReport | None = None
    pending_outcome: Phase = Phase.FAILED
    stop_reason: str = ""
    steps_used: int = 0
    commands_used: int = 0
    recovery_attempts: int = 0
    retry_counts: dict[str, int] = field(default_factory=dict)
    no_progress_count: int = 0
    last_progress_fingerprint: str = ""
    last_event_seq: int = 0
    failure_count: int = 0
