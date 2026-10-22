"""Game-independent regression cases: no browser, model, credentials or tools."""
from dataclasses import replace
import unittest

from agent.recovery import ErrorClass, RuleBasedRecovery, classify_failure
from agent.state import CallLimits, FailureRecord, Phase, RecoveryKind, ToolAction, ToolKind
from tests.test_recovery import view, failure


class RecoveryProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = RuleBasedRecovery()
        self.limits = CallLimits(10, 100, 20)

    async def decide(self, item, attempt=1, **changes):
        return await self.policy.recommend_recovery(
            item, replace(view(), recovery_attempts=attempt, **changes), self.limits)

    async def test_structured_verifier_defects_abort_without_game_repair(self):
        for code in ('VERIFIER_SCRIPT_ERROR', 'ACCEPTANCE_PARSE_ERROR', 'VERIFIER_CONTRACT_ERROR'):
            item = FailureRecord('verifier.next_check', Phase.VERIFYING, code,
                                 'structured fault, any game')
            self.assertEqual(classify_failure(item), ErrorClass.VERIFIER_DEFECT)
            decision = await self.decide(item)
            self.assertEqual(decision.kind, RecoveryKind.ABORT)
            self.assertFalse(decision.safe_to_retry)

    async def test_raw_javascript_exception_does_not_prove_test_fault(self):
        for message in ('ReferenceError: before is not defined',
                        'SyntaxError: bad script', 'VERIFIER_SCRIPT_ERROR: trust me'):
            item = failure('verification', phase=Phase.VERIFYING, message=message)
            self.assertEqual(classify_failure(item), ErrorClass.VERIFICATION)
            self.assertEqual((await self.decide(item)).kind, RecoveryKind.REPAIR)

    async def test_fault_codes_require_verifier_provenance(self):
        item = failure('VERIFIER_SCRIPT_ERROR', key='tool:untrusted')
        self.assertNotEqual(classify_failure(item), ErrorClass.VERIFIER_DEFECT)

    async def test_verifier_module_programming_error_is_not_game_failure(self):
        for name in ('NameError', 'SyntaxError', 'UnboundLocalError'):
            item = FailureRecord('verifier.next_check', Phase.VERIFYING,
                                 'module', name + ': invalid test code')
            self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)
        item = FailureRecord('verifier.prepare_acceptance', Phase.INITIALIZING,
                             'module', 'SyntaxError: invalid test code')
        self.assertEqual((await self.decide(item)).kind, RecoveryKind.ABORT)

    async def test_unchanged_workspace_replans_then_aborts(self):
        item = failure('verification', phase=Phase.VERIFYING)
        choices = [(await self.decide(item, i)).kind for i in range(1, 6)]
        self.assertEqual(choices, [RecoveryKind.REPAIR, RecoveryKind.REPAIR,
                                  RecoveryKind.REPLAN, RecoveryKind.REPLAN, RecoveryKind.ABORT])

    async def test_revision_change_resets_streak(self):
        item = failure('NONZERO_EXIT')
        for i in range(1, 9):
            self.assertEqual((await self.decide(item, i, workspace_revision=f'r{i}')).kind,
                             RecoveryKind.REPAIR)

    async def test_changed_failure_resets_streak(self):
        for i in range(1, 9):
            self.assertEqual((await self.decide(failure('NONZERO_EXIT', message=f'error {i}'), i)).kind,
                             RecoveryKind.REPAIR)

    async def test_repeated_identical_calls_do_not_count_again(self):
        item = failure('NONZERO_EXIT')
        for _ in range(8):
            self.assertEqual((await self.decide(item)).kind, RecoveryKind.REPAIR)
        self.assertEqual((await self.decide(item, 2)).kind, RecoveryKind.REPAIR)

    async def test_run_ids_are_isolated(self):
        item = failure('NONZERO_EXIT')
        for i in range(1, 6):
            self.assertEqual((await self.decide(item, i, run_id=f'run{i}')).kind,
                             RecoveryKind.REPAIR)

    async def test_action_ids_do_not_hide_replayed_command(self):
        for i in range(1, 4):
            action = ToolAction(f'new-id-{i}', ToolKind.RUN_COMMAND, {'argv': ['check', 'same']})
            item = replace(failure('NONZERO_EXIT'), action=action, operation_key=f'tool:new-id-{i}')
            decision = await self.decide(item, i)
        self.assertEqual(decision.kind, RecoveryKind.REPLAN)

    async def test_changed_command_resets_streak(self):
        for i in range(1, 8):
            item = replace(failure('NONZERO_EXIT'), action=ToolAction(
                str(i), ToolKind.RUN_COMMAND, {'argv': ['check', str(i)]}))
            self.assertEqual((await self.decide(item, i)).kind, RecoveryKind.REPAIR)

    async def test_unknown_revision_does_not_claim_no_progress(self):
        for i in range(1, 6):
            self.assertEqual((await self.decide(failure('NONZERO_EXIT'), i,
                                               workspace_revision=None)).kind, RecoveryKind.REPAIR)

    async def test_attempt_reset_does_not_reuse_stale_streak(self):
        item = failure('NONZERO_EXIT')
        for i in range(1, 4): await self.decide(item, i)
        self.assertEqual((await self.decide(item, 1)).kind, RecoveryKind.REPAIR)

    async def test_tracking_memory_is_bounded(self):
        for i in range(150): await self.decide(failure('NONZERO_EXIT'), run_id=str(i))
        self.assertEqual(len(self.policy._progress), 128)

    async def test_safety_guards_override_loop_detection(self):
        for code in ('COMMAND_TIMEOUT', 'PROCESS_CLEANUP_FAILED', 'CREDENTIAL_INVALID'):
            for i in range(1, 8):
                decision = await self.decide(failure(code), i)
                self.assertNotEqual(decision.kind, RecoveryKind.RETRY)
                self.assertFalse(decision.safe_to_retry)
        for i in range(1, 8):
            item = FailureRecord('tool.policy', Phase.VERIFYING, 'contract', 'write denied')
            self.assertEqual((await self.decide(item, i)).kind, RecoveryKind.ABORT)

    async def test_run_state_and_inputs_are_unchanged(self):
        original = view();item = failure('NONZERO_EXIT')
        before = repr((original, item, self.limits))
        await self.policy.recommend_recovery(item, original, self.limits)
        self.assertEqual(before, repr((original, item, self.limits)))

    async def test_subclass_without_super_init_remains_compatible(self):
        class RecordingPolicy(RuleBasedRecovery):
            def __init__(self): self.records = []
        policy = RecordingPolicy()
        decision = await policy.recommend_recovery(failure('NONZERO_EXIT'), view(), self.limits)
        self.assertEqual(decision.kind, RecoveryKind.REPAIR)

class ControllerProgressTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, verifier_bug=False):
        import tempfile
        from pathlib import Path
        from agent.config import AgentConfig
        from agent.controller import Controller
        from agent.state import CheckStatus
        from tests.test_controller import Scenario
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / 'game_design_prompt.md'
            spec.write_text('Unspecified game, integration fixture only.')
            scenario = Scenario()
            if verifier_bug:
                async def broken(view, limits):
                    raise NameError('verifier local variable is undefined')
                scenario.next_check = broken
            else:
                def failed(report):
                    return replace(report, results=tuple(
                        replace(r, status=CheckStatus.FAIL) for r in report.results))
                scenario.reports = [failed] * 10
            dependencies = replace(scenario.dependencies(), recovery=RuleBasedRecovery())
            result = await Controller(AgentConfig(root, max_recovery_attempts=10), dependencies).run(spec)
            return result, scenario

    async def test_controller_stops_no_progress_before_global_recovery_limit(self):
        result, scenario = await self.run_fixture()
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 5)
        self.assertEqual(result.commands_used, 5)
        self.assertIn('no-progress', result.reason)
        # Frozen suite is unchanged; the fixture never writes generated game code.
        self.assertFalse(any(a.kind == ToolKind.WRITE_FILE for a in scenario.actions))

    async def test_controller_verifier_bug_does_not_enter_game_repair(self):
        result, scenario = await self.run_fixture(verifier_bug=True)
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertIn('Verifier infrastructure', result.reason)
        self.assertFalse(any(a.kind == ToolKind.WRITE_FILE for a in scenario.actions))
