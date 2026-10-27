"""Finite-state harness. Other members implement the injected ports below."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import time
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from .config import AgentConfig
from .result_levels import result_levels
from .state import (
    AcceptanceSuite, AgentState, AgentView, CallLimits, CheckStatus, Criterion,
    CriterionResult, DisputeCheck, DeliveryReport, ExecutionContext, FailureRecord, Phase,
    Plan, RecoveryDecision, RecoveryKind, RequestVerification, RevisePlan,
    RunSummary, Task, TERMINAL_PHASES, ToolAction, ToolKind, ToolResult,
    ToolStatus, TraceEvent, VerificationReport, to_jsonable,
)

# Member 4 harness marker for a broken assertion script (see _verification_category).
_SCRIPT_ERROR = re.compile(r"\btest_script_error\s*:")
# Disputed spec-derived checks: each may be re-derived once, at most this many per run.
MAX_ACCEPTANCE_REVISIONS = 2


class PlannerPort(Protocol):
    async def create_plan(self, view: AgentView, limits: CallLimits) -> Plan: ...
    async def next_decision(
        self, view: AgentView, limits: CallLimits,
    ) -> ToolAction | RequestVerification | RevisePlan: ...


class RuntimePort(Protocol):
    async def execute(
        self, action: ToolAction, context: ExecutionContext, limits: CallLimits,
    ) -> ToolResult: ...


class VerifierPort(Protocol):
    async def prepare_acceptance(self, view: AgentView, limits: CallLimits) -> AcceptanceSuite: ...
    async def next_check(
        self, view: AgentView, limits: CallLimits,
    ) -> ToolAction | VerificationReport: ...


class RecoveryPort(Protocol):
    async def recommend_recovery(
        self, failure: FailureRecord, view: AgentView, limits: CallLimits,
    ) -> RecoveryDecision: ...


class TracePort(Protocol):
    async def open_run(self, context: ExecutionContext) -> None: ...
    async def append_event(self, event: TraceEvent) -> None: ...
    async def save_checkpoint(self, snapshot: Mapping[str, Any]) -> None: ...
    async def finalize(self, summary: RunSummary) -> DeliveryReport: ...


@dataclass(frozen=True)
class Dependencies:
    planner: PlannerPort
    runtime: RuntimePort
    verifier: VerifierPort
    recovery: RecoveryPort
    tracer: TracePort


TRANSITIONS: Mapping[Phase, frozenset[Phase]] = {
    Phase.INITIALIZING: frozenset({Phase.PLANNING, Phase.RECOVERING, Phase.FINALIZING}),
    Phase.PLANNING: frozenset({Phase.DECIDING, Phase.RECOVERING, Phase.FINALIZING}),
    Phase.DECIDING: frozenset({Phase.PLANNING, Phase.EXECUTING, Phase.VERIFYING,
                             Phase.RECOVERING, Phase.FINALIZING}),
    Phase.EXECUTING: frozenset({Phase.DECIDING, Phase.VERIFYING, Phase.RECOVERING,
                              Phase.FINALIZING}),
    Phase.VERIFYING: frozenset({Phase.EXECUTING, Phase.RECOVERING, Phase.FINALIZING}),
    Phase.RECOVERING: frozenset({Phase.INITIALIZING, Phase.PLANNING, Phase.DECIDING,
                               Phase.EXECUTING, Phase.VERIFYING, Phase.FINALIZING}),
    Phase.FINALIZING: TERMINAL_PHASES,
    **{phase: frozenset() for phase in TERMINAL_PHASES},
}


class BudgetExhausted(Exception):
    pass


class TraceFailure(Exception):
    pass


class OperationFailure(Exception):
    def __init__(self, failure: FailureRecord) -> None:
        super().__init__(failure.message)
        self.failure = failure


T = TypeVar("T")


class Controller:
    """Single-use, sequential controller with bounded, controller-owned calls."""

    def __init__(self, config: AgentConfig, dependencies: Dependencies) -> None:
        self.config = config
        self.dependencies = dependencies
        self._state = AgentState(run_id=uuid4().hex)
        self._context: ExecutionContext | None = None
        self._started = 0.0
        self._used = False
        self._trace_open = False
        self._trace_broken = False
        self._init_stage = 0
        self._verification_checks = 0
        self._seen_actions: set[str] = set()
        self._retry_action = False
        self._candidate_version: dict[str, Any] | None = None
        self._best_version: dict[str, Any] | None = None
        # test_conflict bookkeeping (see _revise_acceptance).
        self._failed_report: VerificationReport | None = None
        self._fail_signatures: dict[str, tuple[str, str | None]] = {}
        self._conflict: tuple[str, str, str] | None = None
        self._revised: list[dict[str, Any]] = []

    async def _version_call(self, name: str, *args) -> Mapping[str, Any] | None:
        """Bounded preservation via runtime, not model-accessible development tools.

        These filesystem-only operations use the existing infrastructure/finalization
        time allowance. They do not launch models or commands or refund budgets.
        Optional hooks keep older runtime/test ports compatible.
        """
        method = getattr(self.dependencies.runtime, name, None)
        if not callable(method) or self._context is None:
            return None
        remaining = self._remaining(infrastructure=True)
        if remaining <= 0:
            return None
        timeout = min(self.config.infrastructure_timeout_seconds, remaining)
        await self._event("game_version_start", {"operation": name})
        try:
            result = await asyncio.wait_for(method(*args, self._context,
                CallLimits(timeout, 0, 0)), timeout)
            if (not isinstance(result, Mapping) or not result.get("snapshot_id")
                    or not result.get("workspace_revision")):
                raise ValueError("Invalid game-version result")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._event("game_version_error", {"operation": name,
                                                      "error_type": type(exc).__name__})
            return None
        await self._event("game_version_result", {"operation": name, "result": result})
        return result

    async def _remember_game_version(self, report: VerificationReport) -> None:
        s = self._state
        candidate = self._candidate_version
        if (candidate is None or s.suite is None or self._verification_checks == 0
                or report.spec_digest != s.spec_digest or report.suite_version != s.suite.version
                or report.workspace_revision != s.workspace_revision
                or report.workspace_revision != candidate.get("workspace_revision")):
            return
        results = {r.criterion_id: r for r in report.results if isinstance(r, CriterionResult)}
        if len(results) != len(report.results) or set(results) != {c.criterion_id for c in s.suite.criteria}:
            return
        passed = {key for key, r in results.items() if r.status == CheckStatus.PASS
                  and r.evidence_refs and all(isinstance(e, str) and e.strip() for e in r.evidence_refs)}
        score = (sum(c.mandatory and c.criterion_id in passed for c in s.suite.criteria), len(passed))
        if self._best_version is None or score > self._best_version["score"]:
            self._best_version = dict(candidate, score=score, report=report,
                                      launch_command=s.launch_command, checks=self._verification_checks)
        await self._event("game_version_evaluated", {
            "snapshot_id": candidate["snapshot_id"], "mandatory_passes": score[0],
            "all_passes": score[1], "selected_snapshot_id": self._best_version["snapshot_id"],
        })

    async def _restore_best_game(self) -> str:
        s = self._state
        best = self._best_version
        if best is None or self._trace_broken or not self._trace_open:
            return ""
        if s.workspace_revision is None:
            await self._event("game_version_restore_skipped", {"reason": "Unknown process/workspace state"})
            return "; best version preserved in traces, restore skipped because state is uncertain"
        result = await self._version_call("restore_game_snapshot", best["snapshot_id"])
        if result is None:
            # A failed preservation operation cannot leave stale evidence trusted.
            self._invalidate()
            return "; best-version restore failed; source snapshots retained in traces"
        s.workspace_revision = result["workspace_revision"]
        s.launch_command = best["launch_command"]
        s.verification_report = (best["report"] if s.workspace_revision == best["workspace_revision"] else None)
        self._verification_checks = best["checks"]
        await self._event("game_version_restored", {
            "snapshot_id": best["snapshot_id"], "mandatory_passes": best["score"][0],
            "report_matches_revision": s.verification_report is not None,
        })
        # Preserve FAILED/EXHAUSTED/CANCELLED; a partial best result never grants success.
        return "; restored best assessed source version"

    @property
    def state_snapshot(self) -> dict[str, Any]:
        """Detached serialization for diagnostics; not a mutable state handle."""
        return to_jsonable(self._state)

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    def _view(self) -> AgentView:
        s = self._state
        return AgentView(s.run_id, s.phase, s.spec_text, s.spec_digest,
                         s.workspace_revision, s.suite, s.plan, s.plan_version,
                         s.launch_command, s.last_result, s.last_failure,
                         s.steps_used, s.commands_used, s.recovery_attempts,
                         self._elapsed())

    def _remaining(self, *, infrastructure: bool = False) -> float:
        reserve = 0.0 if infrastructure else self.config.finalization_reserve_seconds
        return self.config.max_runtime_seconds - reserve - self._elapsed()

    def _limits(self, timeout: float) -> CallLimits:
        remaining = self._remaining()
        if remaining <= 0:
            raise BudgetExhausted("Work-time budget exhausted")
        return CallLimits(min(timeout, remaining),
                          self.config.max_steps - self._state.steps_used,
                          self.config.max_commands - self._state.commands_used)

    def _reserve_step(self) -> None:
        if self._remaining() <= 0:
            raise BudgetExhausted("Work-time budget exhausted")
        if self._state.steps_used >= self.config.max_steps:
            raise BudgetExhausted("Step budget exhausted")
        self._state.steps_used += 1

    async def _infra(self, callback: Callable[[], Awaitable[T]]) -> T:
        remaining = self._remaining(infrastructure=True)
        if remaining <= 0:
            raise TraceFailure("Total deadline reached; cannot complete trace persistence")
        try:
            return await asyncio.wait_for(callback(), min(
                remaining, self.config.infrastructure_timeout_seconds))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._trace_broken = True
            raise TraceFailure(f"Trace infrastructure failed: {type(exc).__name__}: {exc}") from exc

    async def _event(self, kind: str, payload: Any) -> None:
        self._state.last_event_seq += 1
        event = TraceEvent(self._state.run_id, self._state.last_event_seq,
                           datetime.now(timezone.utc).isoformat(), kind,
                           self._state.phase, to_jsonable(payload))
        await self._infra(lambda: self.dependencies.tracer.append_event(event))

    async def _transition(self, phase: Phase) -> None:
        previous = self._state.phase
        if phase not in TRANSITIONS[previous]:
            raise RuntimeError(f"Illegal state transition: {previous.value} -> {phase.value}")
        self._state.phase = phase
        await self._event("transition", {"from": previous, "to": phase})

    def _failure(self, key: str, message: str, *, category: str = "contract",
                 action: ToolAction | None = None, unknown: bool = False,
                 phase: Phase | None = None) -> OperationFailure:
        return OperationFailure(FailureRecord(key, phase or self._state.phase,
                                category, message, action, unknown))

    async def _invoke(self, key: str, callback: Callable[[CallLimits], Awaitable[T]]) -> T:
        self._reserve_step()
        limits = self._limits(self.config.model_call_timeout_seconds)
        await self._event("module_start", {"operation": key, "limits": limits})
        try:
            result = await asyncio.wait_for(callback(limits), limits.timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._failure(key, f"{type(exc).__name__}: {exc}", category="module") from exc
        try:
            to_jsonable(result)
        except (TypeError, ValueError) as exc:
            raise self._failure(key, "Module returned non-serializable data", category="contract") from exc
        await self._event("module_result", {"operation": key, "result": result})
        return result

    def _path_allowed(self, value: Any) -> bool:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            return False
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or ":" in value:
            return False
        target = (self.config.game_dir / value).resolve()
        root = self.config.game_dir.resolve()
        return target.is_relative_to(root)

    @staticmethod
    def _strings(value: Any) -> bool:
        return isinstance(value, (list, tuple)) and bool(value) and all(
            isinstance(v, str) and bool(v.strip()) and "\x00" not in v for v in value)

    def _validate_action(self, action: ToolAction, origin: Phase, *, retry: bool) -> None:
        if not isinstance(action, ToolAction) or not isinstance(action.kind, ToolKind):
            raise self._failure("tool.schema", "Tool proposal must be a valid ToolAction", phase=origin)
        if not isinstance(action.action_id, str) or not action.action_id.strip():
            raise self._failure("tool.schema", "action_id must not be empty", phase=origin)
        if action.action_id in self._seen_actions and not retry:
            raise self._failure("tool.schema", "Duplicate action_id; only the controller can authorize retries", phase=origin)
        required: dict[ToolKind, set[str]] = {
            ToolKind.PREPARE_WORKSPACE: set(), ToolKind.READ_SPEC: set(),
            ToolKind.INSPECT_WORKSPACE: set(), ToolKind.READ_FILE: {"path"},
            ToolKind.WRITE_FILE: {"path", "content"}, ToolKind.PATCH_FILE: {"path", "patch"},
            ToolKind.RUN_COMMAND: {"argv"}, ToolKind.INSTALL_DEPENDENCIES: {"packages"},
        }
        allowed = required[action.kind] | ({"cwd"} if action.kind == ToolKind.RUN_COMMAND else set())
        keys = set(action.arguments)
        if not required[action.kind] <= keys or not keys <= allowed:
            raise self._failure("tool.schema", "Tool arguments are missing or contain undeclared fields", phase=origin)
        if origin != Phase.INITIALIZING and action.kind in {
            ToolKind.PREPARE_WORKSPACE, ToolKind.READ_SPEC,
        }:
            raise self._failure("tool.policy", "Modules cannot reinitialize the workspace or modify the input", phase=origin)
        if origin == Phase.VERIFYING and action.kind not in {
            ToolKind.READ_FILE, ToolKind.INSPECT_WORKSPACE, ToolKind.RUN_COMMAND,
        }:
            raise self._failure("tool.policy", "Verification cannot write files or install dependencies", phase=origin)
        for name in ("path", "cwd"):
            if name in keys and not self._path_allowed(action.arguments[name]):
                raise self._failure("tool.policy", f"{name} is outside the game/ scope", phase=origin)
        for name in ("content", "patch"):
            if name in keys and not isinstance(action.arguments[name], str):
                raise self._failure("tool.schema", f"{name} must be a string", phase=origin)
        for name in ("argv", "packages"):
            if name in keys and not self._strings(action.arguments[name]):
                raise self._failure("tool.schema", f"{name} must be a non-empty sequence of strings", phase=origin)

    async def _dispatch(self, action: ToolAction, origin: Phase, *, retry: bool = False) -> ToolResult:
        self._reserve_step()  # Rejected proposals count too.
        self._validate_action(action, origin, retry=retry)
        command = action.kind in {ToolKind.RUN_COMMAND, ToolKind.INSTALL_DEPENDENCIES}
        if command:
            if self._state.commands_used >= self.config.max_commands:
                raise BudgetExhausted("Command budget exhausted")
            self._state.commands_used += 1
        self._seen_actions.add(action.action_id)
        limits = self._limits(self.config.command_timeout_seconds)
        await self._event("action_start", {"action": action, "limits": limits, "retry": retry})
        started = time.monotonic()
        try:
            assert self._context is not None
            result = await asyncio.wait_for(
                self.dependencies.runtime.execute(action, self._context, limits),
                limits.timeout_seconds)
        except asyncio.CancelledError:
            self._invalidate()
            await self._event("action_cancelled", {"action_id": action.action_id,
                                                   "side_effects_unknown": True})
            raise
        except Exception as exc:
            self._invalidate()
            await self._event("action_unknown", {"action_id": action.action_id,
                              "error": f"{type(exc).__name__}: {exc}"})
            raise self._failure(f"tool:{action.action_id}", str(exc), category="runtime",
                                action=action, unknown=True, phase=origin) from exc
        if (not isinstance(result, ToolResult) or result.action_id != action.action_id
                or not isinstance(result.status, ToolStatus)):
            self._invalidate()
            raise self._failure(f"tool:{action.action_id}", "Invalid runtime result contract",
                                action=action, unknown=True, phase=origin)
        if (not isinstance(result.workspace_revision, str) or not result.workspace_revision.strip()
                or result.side_effects_unknown or result.status == ToolStatus.UNKNOWN):
            self._invalidate()
        else:
            if result.workspace_revision != self._state.workspace_revision:
                self._state.verification_report = None
            self._state.workspace_revision = result.workspace_revision
        self._state.last_result = result
        await self._event("action_result", {"result": result,
                          "elapsed_seconds": time.monotonic() - started})
        if result.status != ToolStatus.OK or result.side_effects_unknown or self._state.workspace_revision is None:
            raise self._failure(f"tool:{action.action_id}", result.observation,
                                category=result.error_code or "tool", action=action,
                                unknown=result.side_effects_unknown or self._state.workspace_revision is None,
                                phase=origin)
        if origin == Phase.VERIFYING:
            self._verification_checks += 1
        self._progress({"kind": action.kind, "args": action.arguments,
                        "revision": result.workspace_revision, "data": result.data,
                        "observation": result.observation})
        await self._infra(lambda: self.dependencies.tracer.save_checkpoint(self.state_snapshot))
        return result

    def _invalidate(self) -> None:
        self._state.workspace_revision = None
        self._state.verification_report = None

    def _progress(self, payload: Any) -> None:
        fingerprint = hashlib.sha256(json.dumps(to_jsonable(payload), sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()
        if fingerprint == self._state.last_progress_fingerprint:
            self._state.no_progress_count += 1
        else:
            self._state.no_progress_count = 0
        self._state.last_progress_fingerprint = fingerprint
        if self._state.no_progress_count >= self.config.max_no_progress_cycles:
            raise BudgetExhausted("Repeated actions made no progress")

    def _validate_suite(self, suite: AcceptanceSuite) -> None:
        if not isinstance(suite, AcceptanceSuite) or not isinstance(suite.version, str) or not suite.version.strip():
            raise self._failure("verifier.prepare_acceptance", "Acceptance suite is missing its version")
        criteria = suite.criteria
        if not criteria or any(not isinstance(c, Criterion) for c in criteria):
            raise self._failure("verifier.prepare_acceptance", "Acceptance suite must contain Criterion objects")
        ids = [c.criterion_id for c in criteria]
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise self._failure("verifier.prepare_acceptance", "Acceptance IDs are empty or duplicated")
        if any(type(c.mandatory) is not bool or not isinstance(c.description, str)
               or not c.description.strip() or c.category not in {
                   "launch", "functional", "interaction", "technical"} for c in criteria):
            raise self._failure("verifier.prepare_acceptance", "Invalid acceptance criterion")
        mandatory = {c.category for c in criteria if c.mandatory}
        if not {"launch", "functional", "interaction"} <= mandatory:
            raise self._failure("verifier.prepare_acceptance", "Mandatory coverage must include launch, functionality, and interaction")

    def _validate_plan(self, plan: Plan) -> None:
        key = "planner.create_plan"
        if not isinstance(plan, Plan) or not isinstance(plan.plan_id, str) or not plan.plan_id.strip():
            raise self._failure(key, "Plan is missing its ID")
        if not plan.tasks or any(not isinstance(t, Task) for t in plan.tasks):
            raise self._failure(key, "Plan must contain Task objects")
        ids = [t.task_id for t in plan.tasks]
        if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise self._failure(key, "Task IDs are empty or duplicated")
        assert self._state.suite is not None
        requirements = {c.criterion_id for c in self._state.suite.criteria}
        covered: set[str] = set()
        graph = {t.task_id: t.depends_on for t in plan.tasks}
        for task in plan.tasks:
            if not isinstance(task.description, str) or not task.description.strip():
                raise self._failure(key, "Task description is empty")
            if not task.requirement_ids or not set(task.requirement_ids) <= requirements:
                raise self._failure(key, "Task references unknown acceptance criteria")
            if not set(task.depends_on) <= set(ids) or task.task_id in task.depends_on:
                raise self._failure(key, "Invalid task dependencies")
            covered.update(task.requirement_ids)
        if not {c.criterion_id for c in self._state.suite.criteria if c.mandatory} <= covered:
            raise self._failure(key, "Plan does not cover all mandatory acceptance criteria")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise self._failure(key, "Task dependencies contain a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)

    def _report_passes(self, report: VerificationReport | None) -> bool:
        s = self._state
        if (not isinstance(report, VerificationReport) or s.suite is None
                or report.spec_digest != s.spec_digest
                or report.workspace_revision != s.workspace_revision
                or not s.workspace_revision or report.suite_version != s.suite.version
                or not s.launch_command or self._verification_checks == 0):
            return False
        if any(not isinstance(r, CriterionResult) or not isinstance(r.status, CheckStatus)
               for r in report.results):
            return False
        results = {r.criterion_id: r for r in report.results}
        if len(results) != len(report.results) or set(results) != {c.criterion_id for c in s.suite.criteria}:
            return False
        return all(not c.mandatory or (results[c.criterion_id].status == CheckStatus.PASS
                   and bool(results[c.criterion_id].evidence_refs)
                   and all(isinstance(ref, str) and bool(ref.strip())
                           for ref in results[c.criterion_id].evidence_refs))
                   for c in s.suite.criteria)

    async def _initialize(self) -> None:
        if self._init_stage == 0:
            await self._dispatch(ToolAction("bootstrap.workspace", ToolKind.PREPARE_WORKSPACE), Phase.INITIALIZING,
                                 retry="bootstrap.workspace" in self._seen_actions)
            self._init_stage = 1
        if self._init_stage == 1:
            result = await self._dispatch(ToolAction("bootstrap.spec", ToolKind.READ_SPEC), Phase.INITIALIZING,
                                         retry="bootstrap.spec" in self._seen_actions)
            text = result.data.get("text")
            if not isinstance(text, str) or not text.strip():
                raise self._failure("input", "Specification is empty or read_spec did not return text", category="input")
            self._state.spec_text = text
            self._state.spec_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            self._init_stage = 2
        if self._init_stage == 2:
            suite = await self._invoke("verifier.prepare_acceptance", lambda limits:
                self.dependencies.verifier.prepare_acceptance(self._view(), limits))
            self._validate_suite(suite)
            self._state.suite = suite
            self._init_stage = 3
        await self._transition(Phase.PLANNING)

    async def _planning(self) -> None:
        plan = await self._invoke("planner.create_plan", lambda limits:
            self.dependencies.planner.create_plan(self._view(), limits))
        self._validate_plan(plan)
        self._state.plan = plan
        self._state.plan_version += 1
        self._progress({"tasks": plan.tasks})
        await self._transition(Phase.DECIDING)

    async def _schedule(self, action: ToolAction, return_phase: Phase, *, retry: bool = False) -> None:
        self._state.pending_action = action
        self._state.execution_return_phase = return_phase
        self._retry_action = retry
        await self._transition(Phase.EXECUTING)

    async def _deciding(self) -> None:
        decision = await self._invoke("planner.next_decision", lambda limits:
            self.dependencies.planner.next_decision(self._view(), limits))
        if isinstance(decision, ToolAction):
            await self._schedule(decision, Phase.DECIDING)
        elif isinstance(decision, RequestVerification):
            if not self._strings(decision.launch_command):
                raise self._failure("planner.next_decision", "Invalid launch command")
            self._state.launch_command = decision.launch_command
            self._state.verification_report = None
            self._verification_checks = 0
            self._candidate_version = await self._version_call("create_game_snapshot")
            await self._transition(Phase.VERIFYING)
        elif isinstance(decision, RevisePlan) and isinstance(decision.reason, str) and decision.reason.strip():
            await self._transition(Phase.PLANNING)
        elif isinstance(decision, DisputeCheck):
            self._dispute(decision)
        else:
            raise self._failure("planner.next_decision", "Unrecognized decision")

    async def _executing(self) -> None:
        action = self._state.pending_action
        if not isinstance(action, ToolAction):
            raise self._failure("tool.schema", "No valid pending action",
                                phase=self._state.execution_return_phase)
        await self._dispatch(action, self._state.execution_return_phase, retry=self._retry_action)
        self._state.pending_action = None
        await self._transition(self._state.execution_return_phase)

    async def _verifying(self) -> None:
        result = await self._invoke("verifier.next_check", lambda limits:
            self.dependencies.verifier.next_check(self._view(), limits))
        if isinstance(result, ToolAction):
            await self._schedule(result, Phase.VERIFYING)
        elif isinstance(result, VerificationReport):
            self._state.verification_report = result
            await self._remember_game_version(result)
            if not self._report_passes(result):
                # Tell the planner which criteria failed and why, so REPAIR is targeted.
                failed = "; ".join(
                    f"{r.criterion_id}={getattr(r.status, 'value', r.status)}: {str(r.detail)[:400]}"
                    for r in result.results
                    if isinstance(r, CriterionResult) and r.status != CheckStatus.PASS)
                category = self._verification_category(result)
                if category == "verification":
                    category = self._repeated_failure_conflict(result)
                self._failed_report = result
                raise self._failure("verifier.next_check", "Verification failed, evidence is missing, or the report is stale"
                                    + (f". Failed criteria: {failed}" if failed else ""),
                                    category=category)
            await self._finish_request(Phase.SUCCEEDED, "Current game passed mandatory acceptance checks")
        else:
            raise self._failure("verifier.next_check", "Verifier returned an invalid result")

    def _verification_category(self, report: VerificationReport) -> str:
        """"test_script_error" only when the verifier itself marked a frozen mandatory
        criterion UNKNOWN with a test_script_error detail (Member 4 harness signal).

        Recovery then stops without editing the game (Member 5). Every other
        failed report, including a FAIL whose text mentions the marker, stays
        "verification" and remains repairable.
        """
        suite = self._state.suite
        mandatory = {c.criterion_id for c in suite.criteria if c.mandatory} if suite else set()
        confirmed = any(
            isinstance(r, CriterionResult) and r.criterion_id in mandatory
            and r.status == CheckStatus.UNKNOWN
            and _SCRIPT_ERROR.search(str(r.detail)) is not None
            for r in report.results)
        return "test_script_error" if confirmed else "verification"

    def _revisable(self, criterion_id: str) -> bool:
        suite = self._state.suite
        return (criterion_id.startswith("REQ-") and len(self._revised) < MAX_ACCEPTANCE_REVISIONS
                and all(r["criterion_id"] != criterion_id for r in self._revised)
                and suite is not None
                and any(c.criterion_id == criterion_id and c.mandatory for c in suite.criteria))

    @staticmethod
    def _failure_signature(detail: str) -> str:
        """The failing step and expectation, without the state dump that follows it."""
        return " ".join(str(detail).split("state after")[0].split())[:400]

    def _dispute(self, decision: DisputeCheck) -> None:
        """Planner says a failed check contradicts the specification: re-derive it once."""
        report = self._failed_report
        failed = {r.criterion_id: r for r in (report.results if report else ())
                  if isinstance(r, CriterionResult) and r.status in {CheckStatus.FAIL, CheckStatus.UNKNOWN}}
        cid = decision.criterion_id
        if cid not in failed or not self._revisable(cid) or not str(decision.reason).strip():
            # Not a crash: the planner gets the reason and decides again (repair).
            raise self._failure("planner.next_decision",
                                f"Dispute of {cid} rejected: only a failed REQ-* check of the last "
                                "verification can be disputed, once per check and at most "
                                f"{MAX_ACCEPTANCE_REVISIONS} per run. Fix the game instead.")
        self._conflict = (cid, str(decision.reason), str(failed[cid].detail))
        raise self._failure("planner.next_decision", f"Check {cid} disputed by the planner: "
                            f"{' '.join(str(decision.reason).split())[:600]}", category="test_conflict")

    def _repeated_failure_conflict(self, report: VerificationReport) -> str:
        """Same check failing the same way after the game changed: question the check once."""
        revision = self._state.workspace_revision
        conflict = None
        current: dict[str, tuple[str, str | None]] = {}
        for r in report.results:
            if not isinstance(r, CriterionResult) or r.status != CheckStatus.FAIL:
                continue
            signature = self._failure_signature(r.detail)
            current[r.criterion_id] = (signature, revision)
            previous = self._fail_signatures.get(r.criterion_id)
            if (conflict is None and previous is not None and previous[0] == signature
                    and previous[1] != revision and self._revisable(r.criterion_id)):
                conflict = (r.criterion_id,
                            "It failed in exactly the same way before and after the game was changed, "
                            "so the check itself may contradict the specification.", str(r.detail))
        self._fail_signatures = current
        if conflict is None:
            return "verification"
        self._conflict = conflict
        return "test_conflict"

    async def _revise_acceptance(self) -> None:
        """Re-derive one disputed check, then verify the unchanged game again.

        The verifier keeps the old version; the event log records both. "keep"
        or an unusable reply sends the planner back to fixing the game.
        """
        s = self._state
        conflict, self._conflict = self._conflict, None
        method = getattr(self.dependencies.verifier, "revise_requirement", None)
        if conflict is None or not callable(method):
            await self._finish_request(Phase.FAILED, "A check was disputed but cannot be re-derived; "
                                       "game and evidence preserved")
            return
        cid, reason, evidence = conflict
        result = await self._invoke("verifier.revise_requirement", lambda limits:
            method(self._view(), cid, evidence, reason, limits))
        verdict = result.get("verdict") if isinstance(result, Mapping) else None
        why = str(result.get("reason", "")) if isinstance(result, Mapping) else ""
        self._revised.append({"criterion_id": cid, "verdict": verdict or "error", "reason": why[:600],
                              "dispute": reason[:600]})
        suite = result.get("suite") if verdict == "replace" else None
        old = s.suite
        if (isinstance(suite, AcceptanceSuite) and old is not None and suite.version != old.version
                and [c.criterion_id for c in suite.criteria] == [c.criterion_id for c in old.criteria]
                and [c.mandatory for c in suite.criteria] == [c.mandatory for c in old.criteria]):
            self._validate_suite(suite)
            before = next(c.description for c in old.criteria if c.criterion_id == cid)
            after = next(c.description for c in suite.criteria if c.criterion_id == cid)
            await self._event("acceptance_revised", {"criterion_id": cid, "reason": why[:600],
                                                     "dispute": reason[:600], "old_suite": old.version,
                                                     "new_suite": suite.version, "old": before,
                                                     "new": after})
            s.suite = suite
            s.verification_report = None
            s.pending_action = None
            self._verification_checks = 0
            self._fail_signatures.pop(cid, None)
            await self._transition(Phase.VERIFYING)
            return
        await self._event("acceptance_kept", {"criterion_id": cid, "verdict": verdict or "error",
                                              "reason": why[:600]})
        # The planner sees this as the latest failure: the check stands, fix the game.
        s.last_failure = FailureRecord("verifier.revise_requirement", Phase.RECOVERING, "verification",
                                       f"Check {cid} was re-examined against the specification and kept "
                                       f"({verdict or 'error'}): {why[:400]}. Fix the game so it passes; "
                                       "do not dispute this check again.")
        s.verification_report = None
        s.pending_action = None
        await self._transition(Phase.DECIDING)

    async def _record_failure(self, failure: FailureRecord) -> None:
        self._state.last_failure = failure
        self._state.failure_count += 1
        await self._event("failure", failure)
        if failure.phase == Phase.RECOVERING:
            await self._finish_request(Phase.FAILED, "Recovery module failed: " + failure.message)
        elif failure.category == "input":
            await self._finish_request(Phase.FAILED, failure.message)
        else:
            await self._transition(Phase.RECOVERING)

    async def _recovering(self) -> None:
        s = self._state
        failure = s.last_failure
        assert failure is not None
        if s.recovery_attempts >= self.config.max_recovery_attempts:
            raise BudgetExhausted("Recovery budget exhausted")
        s.recovery_attempts += 1
        decision = await self._invoke("recovery.recommend_recovery", lambda limits:
            self.dependencies.recovery.recommend_recovery(failure, self._view(), limits))
        if (not isinstance(decision, RecoveryDecision) or not isinstance(decision.kind, RecoveryKind)
                or not isinstance(decision.reason, str) or not decision.reason.strip()):
            raise self._failure("recovery.recommend_recovery", "Invalid recovery decision")
        await self._event("recovery", decision)
        if decision.kind == RecoveryKind.ABORT:
            await self._finish_request(Phase.FAILED, decision.reason)
        elif decision.kind == RecoveryKind.RETRY:
            if decision.safe_to_retry is not True or failure.side_effects_unknown:
                await self._finish_request(Phase.FAILED, "Refusing an unsafe retry or a retry with unknown side effects")
                return
            used = s.retry_counts.get(failure.operation_key, 0)
            if used >= self.config.max_retries_per_operation:
                raise BudgetExhausted("Per-operation retry budget exhausted")
            s.retry_counts[failure.operation_key] = used + 1
            await self._retry_backoff(used + 1, failure.operation_key)
            if failure.action and failure.phase != Phase.INITIALIZING:
                await self._schedule(failure.action, failure.phase, retry=True)
            else:
                await self._transition(failure.phase)
        elif self._init_stage < 3:
            await self._finish_request(Phase.FAILED, "Initialization is incomplete; only safe retry or abort is allowed")
        elif decision.kind == RecoveryKind.REPAIR:
            s.verification_report = None
            s.pending_action = None
            await self._transition(Phase.DECIDING)
        elif decision.kind == RecoveryKind.REPLAN:
            s.verification_report = None
            s.pending_action = None
            await self._transition(Phase.PLANNING)
        elif decision.kind == RecoveryKind.REVISE_ACCEPTANCE:
            await self._revise_acceptance()

    async def _retry_backoff(self, attempt: int, operation_key: str) -> None:
        """Wait before a retry so a brief API outage or rate limit can clear.

        Bounded by the remaining work time; the wait itself is traced.
        """
        base = self.config.retry_backoff_seconds
        if base <= 0:
            return
        delay = min(base * 3 ** (attempt - 1), 60.0, max(self._remaining() - 1.0, 0.0))
        if delay <= 0:
            return
        await self._event("retry_backoff", {"operation": operation_key, "attempt": attempt,
                                            "delay_seconds": round(delay, 3)})
        await asyncio.sleep(delay)

    async def _finish_request(self, outcome: Phase, reason: str) -> None:
        self._state.pending_outcome = outcome
        self._state.stop_reason = reason
        await self._transition(Phase.FINALIZING)

    def _summary(self, outcome: Phase, reason: str) -> RunSummary:
        assert self._context is not None
        s = self._state
        levels = result_levels(outcome, s.suite, s.verification_report,
                               self._context.game_dir, s.launch_command, self._revised)
        return RunSummary(s.run_id, outcome, reason, self._context.game_dir,
                          self._context.trace_dir, s.launch_command, s.verification_report,
                          s.steps_used, s.commands_used, s.recovery_attempts, self._elapsed(),
                          levels)

    async def _finalize(self) -> RunSummary:
        s = self._state
        outcome, reason = s.pending_outcome, s.stop_reason
        if outcome != Phase.SUCCEEDED:
            try:
                reason += await self._restore_best_game()
            except (Exception, asyncio.CancelledError):
                self._invalidate()
                reason += "; best-version preservation could not finish"
        if outcome == Phase.SUCCEEDED and (not self._report_passes(s.verification_report)
                                          or not self.config.game_dir.is_dir()):
            outcome, reason = Phase.FAILED, "Final success conditions are not satisfied"
        if self._trace_broken or not self._trace_open:
            outcome, reason = Phase.FAILED, reason + "; traces were not fully preserved"
        try:
            if not self._trace_broken and self._trace_open:
                await self._event("finalizing", {"outcome": outcome, "reason": reason})
                await self._infra(lambda: self.dependencies.tracer.save_checkpoint(self.state_snapshot))
                report = await self._infra(lambda: self.dependencies.tracer.finalize(self._summary(outcome, reason)))
                assert self._context is not None
                if (not isinstance(report, DeliveryReport) or report.preserved is not True
                        or Path(report.trace_dir).resolve() != self._context.trace_dir.resolve()
                        or not self._context.trace_dir.is_dir()):
                    raise TraceFailure("Delivery module did not confirm preservation in the correct directory")
                if outcome == Phase.SUCCEEDED and self._remaining(infrastructure=True) <= 0:
                    raise TraceFailure("Delivery exceeded the total deadline")
                await self._transition(outcome)
        except (asyncio.CancelledError, Exception) as exc:
            # No further development or success after a delivery failure.
            outcome, reason = Phase.FAILED, reason + f"; delivery failed: {type(exc).__name__}: {exc}"
            s.phase = outcome
        else:
            s.phase = outcome
        s.pending_outcome, s.stop_reason = outcome, reason
        return self._summary(outcome, reason)

    async def run(self, spec_path: Path) -> RunSummary:
        if self._used:
            raise RuntimeError("Controller is single-use; create a new instance for a new run")
        self._used = True
        self._started = time.monotonic()
        path = Path(spec_path).expanduser()
        if not path.is_absolute():
            path = self.config.run_root / path
        self._context = ExecutionContext(self._state.run_id, self.config.run_root,
            path.resolve(), self.config.game_dir, self.config.traces_dir / self._state.run_id)
        try:
            if self.config.traces_dir.is_symlink():
                # Do not open a trace sink through an external symlink.
                raise TraceFailure("traces/ must not be a symbolic link")
            await self._infra(lambda: self.dependencies.tracer.open_run(self._context))
            self._trace_open = True
            await self._event("run_start", {"context": self._context, "config": self.config})
            # Read-only preflight; actual spec read and workspace creation belong to runtime.
            if not path.is_file() or path.suffix.lower() != ".md":
                await self._finish_request(Phase.FAILED, "Input must be an existing Markdown file")
            elif self.config.game_dir.is_symlink() or self.config.traces_dir.is_symlink():
                await self._finish_request(Phase.FAILED, "game/ and traces/ must not be symbolic links")
            elif self.config.game_dir.exists() and (
                not self.config.game_dir.is_dir() or any(self.config.game_dir.iterdir())):
                await self._finish_request(Phase.FAILED, "Refusing to overwrite an existing non-empty game/")
            handlers = {Phase.INITIALIZING: self._initialize, Phase.PLANNING: self._planning,
                        Phase.DECIDING: self._deciding, Phase.EXECUTING: self._executing,
                        Phase.VERIFYING: self._verifying, Phase.RECOVERING: self._recovering}
            while self._state.phase != Phase.FINALIZING:
                try:
                    if self._remaining() <= 0:
                        raise BudgetExhausted("Work-time budget exhausted")
                    await handlers[self._state.phase]()
                except OperationFailure as exc:
                    await self._record_failure(exc.failure)
                except BudgetExhausted as exc:
                    await self._finish_request(Phase.EXHAUSTED, str(exc))
        except asyncio.CancelledError:
            self._state.pending_outcome = Phase.CANCELLED
            self._state.stop_reason = "Run cancelled; existing artifacts are preserved"
            self._state.phase = Phase.FINALIZING
        except TraceFailure as exc:
            self._trace_broken = True
            self._state.pending_outcome = Phase.FAILED
            self._state.stop_reason = str(exc)
            self._state.phase = Phase.FINALIZING
        except Exception as exc:
            self._state.pending_outcome = Phase.FAILED
            self._state.stop_reason = f"Controller error: {type(exc).__name__}: {exc}"
            self._state.phase = Phase.FINALIZING
        return await self._finalize()
