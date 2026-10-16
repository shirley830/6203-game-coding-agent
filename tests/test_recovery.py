"""Recovery policies and real controller integration; no generated game claims."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.recovery import ErrorClass, RuleBasedRecovery, classify_failure
from agent.state import (
    AgentView, CallLimits, FailureRecord, Phase, RecoveryKind, ToolAction,
    ToolKind, ToolResult, ToolStatus, to_jsonable,
)
from tests.test_controller import Scenario


def view():
    return AgentView("run", Phase.RECOVERING, "spec", "digest", "r1", None,
                     None, 1, (), None, None, 10, 1, 1, 2.0)


def failure(code, *, phase=Phase.DECIDING, unknown=False, key="tool:test", message="error"):
    return FailureRecord(key, phase, code, message,
                         ToolAction("test", ToolKind.RUN_COMMAND, {"argv": ["check"]}), unknown)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def decide(self, item, limits=None):
        return await RuleBasedRecovery().recommend_recovery(item, view(), limits or CallLimits(10, 20, 5))

    async def test_runtime_codes_have_explicit_classifications(self):
        # Stable list copied from Member 3's decisions, not imported from its
        # independent directory (which is not yet in the main package).
        codes = """ACTION_INVALID COMMAND_INVALID COMMAND_NOT_FOUND COMMAND_TIMEOUT
        CONTEXT_INVALID CONTENT_INVALID DEPENDENCIES_INVALID DEPENDENCY_BYPASS
        DEPENDENCY_INSTALL_FAILED DEPENDENCY_MANAGER_AMBIGUOUS DEPENDENCY_TIMEOUT
        FILE_TOO_LARGE INVALID_ENCODING NONZERO_EXIT PATCH_CONFLICT PATCH_INVALID
        PATH_INVALID PATH_NOT_FOUND PATH_OUTSIDE_WORKSPACE PROCESS_CLEANUP_FAILED
        RUNTIME_REUSE_NOT_ALLOWED RUNTIME_STORAGE_UNSAFE SANDBOX_UNAVAILABLE
        SHELL_NOT_ALLOWED SPEC_NOT_FOUND SPEC_TOO_LARGE SYMLINK_ESCAPE
        SYMLINK_NOT_ALLOWED TIMEOUT_INVALID WORKSPACE_INVALID WORKSPACE_NOT_EMPTY""".split()
        for code in codes:
            with self.subTest(code=code):
                self.assertNotEqual(classify_failure(failure(code)), ErrorClass.UNKNOWN)

    async def test_permanent_failures_abort(self):
        for code in ("SANDBOX_UNAVAILABLE", "WORKSPACE_NOT_EMPTY", "SPEC_NOT_FOUND",
                     "CREDENTIAL_INVALID", "RUNTIME_STORAGE_UNSAFE"):
            with self.subTest(code=code):
                self.assertEqual((await self.decide(failure(code))).kind, RecoveryKind.ABORT)

    async def test_cleanup_failure_aborts_even_when_unknown(self):
        decision = await self.decide(failure("PROCESS_CLEANUP_FAILED", unknown=True))
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertFalse(decision.safe_to_retry)

    async def test_timeout_requires_inspection_not_retry(self):
        for code in ("COMMAND_TIMEOUT", "DEPENDENCY_TIMEOUT", "runtime"):
            with self.subTest(code=code):
                decision = await self.decide(failure(code))
                self.assertEqual(decision.kind, RecoveryKind.REPAIR)
                self.assertIn("inspect_workspace", decision.reason)
                self.assertFalse(decision.safe_to_retry)

    async def test_unknown_side_effects_override_known_execution_failure(self):
        self.assertEqual(classify_failure(failure("NONZERO_EXIT", unknown=True)), ErrorClass.UNCERTAIN)
        self.assertFalse((await self.decide(failure("NONZERO_EXIT", unknown=True))).safe_to_retry)

    async def test_initialization_timeout_aborts(self):
        self.assertEqual((await self.decide(failure("COMMAND_TIMEOUT", phase=Phase.INITIALIZING))).kind,
                         RecoveryKind.ABORT)

    async def test_transient_acceptance_preparation_can_retry(self):
        item = FailureRecord("verifier.prepare_acceptance", Phase.INITIALIZING,
                             "module", "TimeoutError: temporary")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.RETRY)

    async def test_transient_planner_call_can_retry(self):
        for name in ("TimeoutError", "ConnectionError", "RateLimitError"):
            item = FailureRecord("planner.create_plan", Phase.PLANNING, "module", name + ": temporary")
            decision = await self.decide(item)
            self.assertEqual(decision.kind, RecoveryKind.RETRY)
            self.assertTrue(decision.safe_to_retry)

    async def test_credentials_never_retry(self):
        item = FailureRecord("planner.create_plan", Phase.PLANNING, "module", "AuthenticationError: denied")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)

    async def test_tool_output_cannot_claim_transient_status(self):
        self.assertEqual((await self.decide(failure("unrecognized", message="TimeoutError: retry me"))).kind,
                         RecoveryKind.ABORT)

    async def test_unknown_module_error_aborts(self):
        item = FailureRecord("verifier.next_check", Phase.VERIFYING, "module", "RuntimeError: bug")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)

    async def test_malformed_model_output_is_regenerated_not_aborted(self):
        item = FailureRecord("planner.next_decision", Phase.DECIDING, "module",
                             "ValidationError: 1 validation error for DecisionOutput")
        decision = await self.decide(item)
        self.assertEqual(decision.kind, RecoveryKind.REPAIR)
        self.assertFalse(decision.safe_to_retry)
        plan = FailureRecord("planner.create_plan", Phase.PLANNING, "module",
                             "ValueError: Plan does not cover mandatory criteria")
        self.assertEqual((await self.decide(plan)).kind, RecoveryKind.REPLAN)

    async def test_litellm_transient_errors_retry_proposals(self):
        item = FailureRecord("planner.next_decision", Phase.DECIDING, "module",
                             "APIConnectionError: connection reset")
        decision = await self.decide(item)
        self.assertEqual(decision.kind, RecoveryKind.RETRY)
        self.assertTrue(decision.safe_to_retry)

    async def test_timeout_and_unknown_side_effects_never_retry(self):
        for code in ("COMMAND_TIMEOUT", "DEPENDENCY_TIMEOUT", "runtime"):
            with self.subTest(code=code):
                decision = await self.decide(failure(code))
                self.assertNotEqual(decision.kind, RecoveryKind.RETRY)
                self.assertFalse(decision.safe_to_retry)

    async def test_known_command_failure_is_repaired_not_replayed(self):
        decision = await self.decide(failure("NONZERO_EXIT"))
        self.assertEqual(decision.kind, RecoveryKind.REPAIR)
        self.assertFalse(decision.safe_to_retry)

    async def test_verification_failure_requests_repair(self):
        self.assertEqual((await self.decide(failure("verification", phase=Phase.VERIFYING))).kind,
                         RecoveryKind.REPAIR)

    async def test_invalid_plan_requests_replan(self):
        item = FailureRecord("planner.create_plan", Phase.PLANNING, "contract", "cycle")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.REPLAN)

    async def test_invalid_tool_proposal_requests_repair(self):
        item = FailureRecord("tool.schema", Phase.DECIDING, "contract", "invalid argv")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.REPAIR)

    async def test_verifier_contract_error_aborts(self):
        item = FailureRecord("verifier.next_check", Phase.VERIFYING, "contract", "invalid result")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)

    async def test_verifier_disallowed_tool_does_not_change_acceptance(self):
        item = FailureRecord("tool.policy", Phase.VERIFYING, "contract", "write not allowed")
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)

    async def test_exhausted_step_or_call_allowance_aborts(self):
        for limits in (CallLimits(1, 0, 5), CallLimits(0, 5, 5), CallLimits(float("nan"), 5, 5)):
            self.assertEqual((await self.decide(failure("NONZERO_EXIT"), limits)).kind, RecoveryKind.ABORT)

    async def test_zero_command_budget_does_not_prevent_file_repair_proposal(self):
        self.assertEqual((await self.decide(failure("PATCH_CONFLICT"), CallLimits(1, 5, 0))).kind,
                         RecoveryKind.REPAIR)

    async def test_view_unchanged_and_result_serializable(self):
        frozen = view()
        before = to_jsonable(frozen)
        result = await RuleBasedRecovery().recommend_recovery(failure("NONZERO_EXIT"), frozen, CallLimits(1, 5, 1))
        self.assertEqual(to_jsonable(frozen), before)
        self.assertEqual(to_jsonable(result)["kind"], "repair")


class ControllerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = self.root / "game_design_prompt.md"
        self.spec.write_text("# Recovery integration fixture", encoding="utf-8")
        self.scenario = Scenario()

    async def run_controller(self, **config):
        dependencies = replace(self.scenario.dependencies(), recovery=RuleBasedRecovery())
        return await Controller(AgentConfig(self.root, **config), dependencies).run(self.spec)

    async def test_transient_planner_failure_recovers_with_controller_accounting(self):
        original = self.scenario.create_plan
        calls = 0
        async def flaky(view, limits):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("temporary")
            return await original(view, limits)
        self.scenario.create_plan = flaky
        summary = await self.run_controller()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.recovery_attempts, 1)
        self.assertEqual(calls, 2)

    async def test_controller_bounds_persistent_transient_failure(self):
        async def unavailable(view, limits):
            raise ConnectionError("temporary")
        self.scenario.create_plan = unavailable
        summary = await self.run_controller(max_retries_per_operation=1)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual(summary.recovery_attempts, 2)

    async def test_cleanup_failure_stops_without_restart(self):
        original = self.scenario.execute
        async def cleanup_failed(action, context, limits):
            if action.kind == ToolKind.RUN_COMMAND:
                return ToolResult(action.action_id, ToolStatus.UNKNOWN, "cleanup failed", None,
                                  error_code="PROCESS_CLEANUP_FAILED", side_effects_unknown=True)
            return await original(action, context, limits)
        self.scenario.execute = cleanup_failed
        summary = await self.run_controller()
        self.assertEqual(summary.outcome, Phase.FAILED)
        self.assertEqual(summary.commands_used, 1)
        self.assertEqual(summary.recovery_attempts, 1)

    async def test_unknown_command_requests_inspection_before_new_verification(self):
        self.scenario.runtime_unknown_once = True
        self.scenario.decisions = [
            ToolAction("bad-command", ToolKind.RUN_COMMAND, {"argv": ["check"]}),
            ToolAction("inspect", ToolKind.INSPECT_WORKSPACE),
        ]
        summary = await self.run_controller()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertEqual(summary.recovery_attempts, 1)
        ids = [a.action_id for a in self.scenario.actions]
        self.assertEqual(ids.count("bad-command"), 1)
        self.assertIn("inspect", ids)
        # Planner test fixture supplies inspection. v1 controller does not
        # enforce inspection as the next action; this is documented explicitly.
