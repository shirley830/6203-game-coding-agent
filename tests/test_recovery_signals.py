"""Real controller report-to-recovery bridge, independent of game rules."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from agent.config import AgentConfig
from agent.controller import Controller
from agent.recovery import RuleBasedRecovery
from agent.recovery.signals import verifier_fault_reason
from agent.state import (AcceptanceSuite, CallLimits, CheckStatus, Criterion, FailureRecord, Phase,
                         RecoveryKind)
from tests.test_controller import Scenario
from tests.test_recovery import view

PREFIX = 'Verification failed, evidence is missing, or the report is stale. Failed criteria: '


def signal(detail, **changes):
    return replace(FailureRecord('verifier.next_check', Phase.VERIFYING,
                                 'verification', PREFIX + detail), **changes)


def frozen(*criteria):
    return replace(view(), suite=AcceptanceSuite('v1', criteria or (
        Criterion('rule', 'Any game requirement', 'functional'),)))


class SignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_unknown_marker_aborts_without_new_error_codes(self):
        item = signal('rule=unknown: step 2: test_script_error: unbound name: before')
        decision = await RuleBasedRecovery().recommend_recovery(
            item, frozen(), CallLimits(10, 20, 5))
        self.assertEqual(decision.kind, RecoveryKind.ABORT)
        self.assertIn('test_script_error', decision.reason)
        self.assertFalse(decision.safe_to_retry)

    def test_plain_runtime_output_cannot_claim_verifier_signal(self):
        for changes in ({'operation_key': 'tool:command'}, {'category': 'NONZERO_EXIT'},
                        {'phase': Phase.EXECUTING}, {'side_effects_unknown': True}):
            self.assertIsNone(verifier_fault_reason(
                signal('rule=unknown: test_script_error: broken', **changes), frozen()))

    def test_marker_requires_status_provenance_and_known_criterion(self):
        for detail in ('rule=fail: test_script_error: not classified by verifier',
                       'other=unknown: test_script_error: unknown criterion',
                       'rule=unknown: no evidence available',
                       'rule=unknown: not_test_script_error: substring only'):
            self.assertIsNone(verifier_fault_reason(signal(detail), frozen()))
        self.assertIsNone(verifier_fault_reason(signal('rule=unknown: test_script_error: x'), view()))

    def test_arbitrary_exception_text_is_not_the_controller_summary(self):
        item = signal('rule=unknown: test_script_error: x',
                      message='ReferenceError: test_script_error: arbitrary output')
        self.assertIsNone(verifier_fault_reason(item, frozen()))

    def test_marker_in_later_criterion_with_semicolons_is_supported(self):
        item = signal('other=fail: game bug; rule=unknown: step 2; test_script_error: bad assertion')
        self.assertIn('test_script_error', verifier_fault_reason(item, frozen()))

    def test_incomplete_frozen_acceptance_stops_but_optional_note_does_not(self):
        c = Criterion('C-ACCEPTANCE-INCOMPLETE', 'incomplete', 'technical')
        item = signal('C-ACCEPTANCE-INCOMPLETE=fail: incomplete acceptance')
        self.assertIn('Frozen acceptance is incomplete', verifier_fault_reason(item, frozen(c)))
        self.assertIsNone(verifier_fault_reason(item, frozen(replace(c, mandatory=False))))


class ControllerSignalTests(unittest.IsolatedAsyncioTestCase):
    async def run_report(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = root / 'game_design_prompt.md'
            spec.write_text('Generic unseen game specification fixture.')
            scenario = Scenario()
            if mode == 'incomplete':
                scenario.suite = replace(scenario.suite, criteria=scenario.suite.criteria + (
                    Criterion('C-ACCEPTANCE-INCOMPLETE', 'Incomplete acceptance', 'technical'),))
            def report(original):
                results = list(original.results)
                if mode == 'incomplete':
                    results[-1] = replace(results[-1], status=CheckStatus.FAIL,
                                          detail='incomplete acceptance')
                else:
                    results[1] = replace(results[1], status=CheckStatus.UNKNOWN if mode == 'script' else CheckStatus.FAIL,
                                         detail='test_script_error: before is undefined' if mode == 'script' else 'gameplay mismatch')
                return replace(original, results=tuple(results))
            scenario.reports = [report]
            deps = replace(scenario.dependencies(), recovery=RuleBasedRecovery())
            result = await Controller(AgentConfig(root), deps).run(spec)
            return result, scenario

    async def test_actual_controller_summary_connects_unknown_marker(self):
        result, scenario = await self.run_report('script')
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertEqual(result.commands_used, 1)
        self.assertIn('test_script_error', result.reason)
        self.assertFalse(any(a.kind.value == 'write_file' for a in scenario.actions))

    async def test_incomplete_suite_never_enters_useless_game_repair(self):
        result, scenario = await self.run_report('incomplete')
        self.assertEqual(result.outcome, Phase.FAILED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertIn('Frozen acceptance is incomplete', result.reason)
        self.assertFalse(any(a.kind.value == 'write_file' for a in scenario.actions))

    async def test_normal_game_failure_still_repairs_and_reverifies(self):
        result, scenario = await self.run_report('game')
        self.assertEqual(result.outcome, Phase.SUCCEEDED)
        self.assertEqual(result.recovery_attempts, 1)
        self.assertEqual(result.commands_used, 2)

class RealVerifierSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_verifier_unknown_result_reaches_recovery(self):
        import json
        from tests.test_recovery_verifier import HarnessObservations, load_verifier
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); spec = root / 'game_design_prompt.md'
            spec.write_text('Browser canvas game with score, arrow keys and restart.')
            runtime = HarnessObservations(first='pass')
            original = runtime.execute
            async def execute(action, context, limits):
                result = await original(action, context, limits)
                if action.kind.value == 'run_command':
                    payload = json.loads(result.observation.split('HARNESS_JSON=', 1)[1])
                    payload['checks']['interaction'].update(
                        status='unknown', detail='test_script_error: unbound name: before')
                    result = replace(result, observation='HARNESS_JSON=' + json.dumps(payload))
                return result
            runtime.execute = execute
            deps = replace(runtime.dependencies(), verifier=load_verifier()(),
                           recovery=RuleBasedRecovery())
            result = await Controller(AgentConfig(root), deps).run(spec)
            self.assertEqual(result.outcome, Phase.FAILED)
            self.assertEqual(result.recovery_attempts, 1)
            self.assertEqual(runtime.harness_calls, 1)
            self.assertIn('test_script_error', result.reason)
            self.assertFalse(any(a.kind.value == 'write_file' for a in runtime.actions))
