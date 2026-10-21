"""Pure recovery recommendations; controller owns actions, retries and budgets."""

import hashlib
import json
import math
from collections import OrderedDict

from agent.state import (
    AgentView, CallLimits, FailureRecord, Phase, RecoveryDecision, RecoveryKind, to_jsonable,
)
from .classify import ErrorClass, classify_failure


# Every Nth recovery during repeated verification failure escalates to REPLAN.
REPLAN_AFTER_RECOVERIES = 6


class RuleBasedRecovery:
    """Asynchronous RecoveryPort with bounded, per-run no-progress memory.

    No subprocesses, disk writes, network calls, sleeps, or internal retry loops.
    It intentionally does not implement rollback: Team Contract v1 has no
    rollback decision/tool, and trace checkpoints are not filesystem snapshots.
    """

    def __init__(self):
        # No AgentState mutation; never mix evidence between independent runs.
        self._progress = OrderedDict()

    def _no_progress(self, failure: FailureRecord, view: AgentView) -> int:
        if not hasattr(self, "_progress"):
            self._progress = OrderedDict()
        if view.workspace_revision is None:
            self._progress.pop(view.run_id, None)
            return 0
        action = failure.action
        operation = (failure.operation_key if action is None else
                     (action.kind.value, to_jsonable(action.arguments)))
        fingerprint = json.dumps((failure.category, failure.phase.value,
                                  operation, failure.message,
                                  view.workspace_revision), sort_keys=True)
        fingerprint = hashlib.sha256(fingerprint.encode()).hexdigest()
        previous = self._progress.pop(view.run_id, None)
        attempt = view.recovery_attempts
        streak = 1
        if previous is not None:
            old_attempt, old_fingerprint, old_streak = previous
            if attempt == old_attempt and fingerprint == old_fingerprint:
                streak = old_streak  # duplicate recommendation, not another attempt
            elif attempt == old_attempt + 1 and fingerprint == old_fingerprint:
                streak = old_streak + 1
        self._progress[view.run_id] = (attempt, fingerprint, streak)
        if len(self._progress) > 128:
            self._progress.popitem(last=False)
        return streak

    async def recommend_recovery(
        self, failure: FailureRecord, view: AgentView, limits: CallLimits,
    ) -> RecoveryDecision:
        def choose(kind: RecoveryKind, reason: str) -> RecoveryDecision:
            return RecoveryDecision(kind, reason, safe_to_retry=kind == RecoveryKind.RETRY)

        abort = RecoveryKind.ABORT
        if (not math.isfinite(limits.timeout_seconds) or limits.timeout_seconds <= 0
                or limits.steps_remaining <= 0):
            return choose(abort, "No remaining call/step allowance; preserve artifacts and finalize.")

        error = classify_failure(failure)
        if error == ErrorClass.CLEANUP_FAILED:
            return choose(abort, "Process cleanup failed; Contract v1 has no cleanup tool. "
                          "Do not restart, inspect via another command, or modify files while processes may survive.")
        if error == ErrorClass.VERIFIER_DEFECT:
            return choose(abort, "Verifier infrastructure/acceptance preparation is defective; "
                          "preserve evidence and game source. Do not repair the game to satisfy "
                          "a broken test or weaken frozen criteria; verifier integration needs correction.")
        if error == ErrorClass.PERMANENT:
            return choose(abort, f"Permanent environment/input/contract failure ({failure.category}); "
                          "do not retry, weaken isolation, or request live human intervention.")
        if failure.phase == Phase.RECOVERING:
            return choose(abort, "Recovery itself failed; avoid recursive recovery.")

        # The controller permits only safe retry or abort before acceptance is frozen.
        if failure.phase == Phase.INITIALIZING:
            if (error == ErrorClass.TRANSIENT_MODULE and not failure.side_effects_unknown
                    and failure.operation_key == "verifier.prepare_acceptance"):
                return choose(RecoveryKind.RETRY, "Transient acceptance preparation failure; "
                              "retry once per controller invocation within its retry budget.")
            return choose(abort, "Initialization is incomplete; no repair/replan is legal. Preserve existing artifacts.")

        if error == ErrorClass.UNCERTAIN:
            # Reason is visible to planner in view.last_failure/recovery traces,
            # but v1 does not expose the recommendation itself in AgentView.
            return choose(RecoveryKind.REPAIR, "Unknown side effects: do not repeat the failed action. "
                          "Planner must first request inspect_workspace and use a fresh revision; "
                          "runtime must have completed process cleanup before further development.")

        if error == ErrorClass.TRANSIENT_MODULE:
            if (failure.action is None and failure.operation_key in {
                "planner.create_plan", "planner.next_decision", "verifier.next_check",
            } and not failure.side_effects_unknown):
                return choose(RecoveryKind.RETRY, "Transient proposal-only module failure; "
                              "controller accounts for each attempt and enforces retry limits.")
            return choose(abort, "Cannot establish safe retry semantics for this module operation.")

        if error == ErrorClass.PROPOSAL and failure.phase == Phase.VERIFYING:
            return choose(abort, "Verifier proposed an invalid/disallowed operation; "
                          "fix verifier integration rather than alter frozen acceptance criteria.")

        if error in {ErrorClass.VERIFICATION, ErrorClass.EXECUTION,
                     ErrorClass.MALFORMED_PROPOSAL, ErrorClass.PROPOSAL}:
            streak = self._no_progress(failure, view)
            if streak >= 5:
                return choose(abort, "Same failure on an unchanged workspace persists after replanning; "
                              "stop the no-progress loop and preserve evidence. No success is claimed.")
            if streak >= 3:
                return choose(RecoveryKind.REPLAN, "Same failure recurred without a workspace change; "
                              "replan instead of repeating the ineffective repair. Keep frozen criteria.")
        else:
            if hasattr(self, "_progress"):
                self._progress.pop(view.run_id, None)

        if error == ErrorClass.MALFORMED_PROPOSAL:
            if failure.operation_key == "planner.create_plan":
                return choose(RecoveryKind.REPLAN, "Model returned an invalid plan document; "
                              "regenerate the plan (each attempt is budgeted by the controller).")
            return choose(RecoveryKind.REPAIR, "Model returned an invalid decision document; "
                          "request a fresh decision with the parse error visible in last_failure.")

        if error == ErrorClass.VERIFICATION:
            if view.recovery_attempts >= REPLAN_AFTER_RECOVERIES and view.recovery_attempts % REPLAN_AFTER_RECOVERIES == 0:
                return choose(RecoveryKind.REPLAN, "Acceptance keeps failing after several repairs; "
                              "rebuild the task plan against the frozen criteria before repairing again.")
            return choose(RecoveryKind.REPAIR, "Acceptance failed or evidence is incomplete/stale; "
                          "inspect the failed criteria, repair as necessary and obtain fresh verification.")

        if error == ErrorClass.PROPOSAL:
            if failure.operation_key == "planner.create_plan":
                return choose(RecoveryKind.REPLAN, "Plan violates the frozen acceptance/task contract; regenerate the plan.")
            return choose(RecoveryKind.REPAIR, "Regenerate the invalid development proposal using declared tools, "
                          "game-relative paths, argv arrays and INSTALL_DEPENDENCIES for installation.")

        if error == ErrorClass.EXECUTION:
            return choose(RecoveryKind.REPAIR, "Inspect the known execution failure and output_refs; "
                          "generate a targeted correction and reverify. Do not replay the unchanged command.")

        # Unknown module/port failures need diagnosis, not speculative retries.
        return choose(abort, "Unclassified or non-transient module failure; record evidence and finalize. "
                      "Add a structured error category before enabling automatic recovery.")
