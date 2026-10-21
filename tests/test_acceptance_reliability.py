"""Reproduces three acceptance-verifier failures before the fix.

1. Fewer than three valid requirements must not let a run fully pass.
2. A bare ``before`` (or any other name that was not injected) is a test-script
   error, not a game FAIL and not a game PASS.
3. A ball placed below the paddle must be observed only after its centre has
   crossed the bottom edge, because that is when the game deducts a life.
"""

import json
from pathlib import Path
import tempfile
import unittest

from agent.state import AgentView, CallLimits, CheckStatus, Criterion, Phase
from agent.verification import GameVerifier
from agent.verification.evidence import EvidenceLedger, evaluate_criteria
from agent.verification.playwright_game_check import _EVAL_JS, _run_spec_scenarios

LIMITS = CallLimits(30, 100, 100)
CANVAS_SPEC = (
    "Build a browser canvas breakout game. Move the paddle to keep the ball in play. "
    "The ball costs one life only after its centre crosses the bottom edge."
)


def _view(spec_text: str = CANVAS_SPEC) -> AgentView:
    return AgentView(
        "run", Phase.INITIALIZING, spec_text, "digest", "rev", None, None, 0, (),
        None, None, 0, 0, 0, 0.0,
    )


class _Reply:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        return self.reply


def _two_requirement_reply() -> str:
    """Two valid scenarios and one invalid one: below the minimum of three."""
    return json.dumps({
        "state_contract": {
            "state_fields": {"lives": "number", "score": "number"},
            "test_hooks": {"aimBallBelowPaddle": "place the ball just below the paddle, still on screen"},
        },
        "requirements": [
            {"id": "R1", "category": "interaction", "description": "Mouse moves the paddle",
             "steps": [{"save": "x0", "expr": "s.paddle.x"}, {"hold": "ArrowRight", "ms": 200},
                       {"expect": "s.paddle.x !== saved.x0"}]},
            {"id": "R2", "category": "functional", "description": "Missing the ball costs a life",
             "steps": [{"call": "aimBallBelowPaddle"}, {"expect": "s.lives === 2"}]},
            {"id": "R3", "category": "functional", "description": "no assertion here",
             "steps": [{"key": "Space"}]},
        ],
    })


class InsufficientRulesTests(unittest.IsolatedAsyncioTestCase):
    async def test_fewer_than_three_requirements_cannot_fully_pass(self):
        verifier = GameVerifier(client=_Reply(_two_requirement_reply()))
        suite = await verifier.prepare_acceptance(_view(), LIMITS)
        ids = [c.criterion_id for c in suite.criteria]
        # Generic checks stay as auxiliary evidence.
        for generic_id in ("C-LAUNCH", "C-FUNCTIONAL", "C-INTERACTION", "C-TECHNICAL"):
            self.assertIn(generic_id, ids)
        info = next(c for c in suite.criteria if c.criterion_id == "C-ACCEPTANCE-INFO")
        self.assertFalse(info.mandatory)

        mandatory = [c for c in suite.criteria if c.mandatory]
        self.assertTrue(any(
            "未完整验收" in c.description or "incomplete acceptance" in c.description.lower()
            for c in mandatory
        ), [c.criterion_id for c in suite.criteria])

        ledger = EvidenceLedger()
        ledger.harness = {"checks": {
            key: {"status": "pass", "detail": "generic ok", "evidence_refs": [f"e:{key}"]}
            for key in ("launch", "functional", "interaction", "technical", "contract")
        }, "requirements": {}, "page_errors": []}
        ledger.tool_ops.append("tool:run_command:x")
        results = {r.criterion_id: r for r in evaluate_criteria(tuple(suite.criteria), ledger)}
        mandatory_results = [results[c.criterion_id] for c in mandatory]
        self.assertFalse(
            all(r.status == CheckStatus.PASS for r in mandatory_results),
            [(r.criterion_id, r.status, r.detail) for r in mandatory_results],
        )
        incomplete = [r for r in mandatory_results if r.status != CheckStatus.PASS]
        self.assertTrue(incomplete)
        self.assertTrue(all(
            "incomplete acceptance" in r.detail.lower() or "未完整验收" in r.detail
            for r in incomplete
        ), [r.detail for r in incomplete])


class _ScriptPage:
    """Page double: a bare ``before`` raises, the way ``new Function`` does."""

    def __init__(self) -> None:
        self.state = {"lives": 3}

    def reload(self, **kwargs):
        return None

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def screenshot(self, path: str) -> None:
        return None

    def evaluate(self, script, arg=None):
        text = script if isinstance(script, str) else ""
        if text == _EVAL_JS or "new Function" in text:
            expr = arg[0]
            if "before" in expr and "saved.before" not in expr.replace(" ", ""):
                raise ReferenceError("before is not defined")
            return True
        if "JSON.parse" in text:
            return dict(self.state)
        if "Object.keys(window.__gameTest" in text:
            return []
        if text.startswith("(fields)"):
            return [name for name in arg if name not in self.state]
        raise AssertionError("unexpected script " + text[:120])


class ScriptErrorTests(unittest.TestCase):
    def test_bare_before_is_test_script_error_not_game_failure(self):
        spec = {
            "contract": {"state_fields": {"lives": "number"}, "test_hooks": {}},
            "scenarios": [{
                "criterion_id": "REQ-LIFE",
                "description": "life drops compared with the value saved as before",
                "steps": [{"expect": "s.lives < before"}],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(
                _ScriptPage(), spec, Path(directory), 30.0)
        block = blocks["REQ-LIFE"]
        self.assertIn("test_script_error", block["detail"])
        self.assertNotEqual(block["status"], "fail", block)
        self.assertNotEqual(block["status"], "pass", block)
        self.assertEqual(block["status"], "unknown", block)

        ledger = EvidenceLedger()
        ledger.harness = {"checks": {}, "requirements": {"REQ-LIFE": block}, "page_errors": []}
        ledger.tool_ops.append("tool:run_command:x")
        result = evaluate_criteria((Criterion("REQ-LIFE", "lives", "functional"),), ledger)[0]
        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("test_script_error", result.detail)
        self.assertNotEqual(result.status, CheckStatus.FAIL)
        self.assertNotEqual(result.status, CheckStatus.PASS)


class _DropGame:
    """Life is removed only once the ball centre has crossed the bottom.

    ``aimBallBelowPaddle`` leaves the ball on screen. At 0.1 px/ms the fixed
    120ms harness pause is not enough to cross ``y - r > H``.
    """

    def __init__(self, vy: float = 0.1) -> None:
        self.vy = vy
        self.y = 200.0
        self.r = 8.0
        self.H = 400.0
        self.lives = 3
        self.armed = False
        self.elapsed = 0
        self.armed_at = None
        self.lives_read_at = None
        self.y_at_read = None
        self.condition_checks = 0
        self.saw_inside_check = False
        self.saw_crossed_check = False

    def wait_for_timeout(self, ms: int) -> None:
        self.elapsed += int(ms)
        if not self.armed:
            return
        self.y += self.vy * int(ms)
        if (self.y - self.r) > self.H and self.lives == 3:
            self.lives = 2

    def reload(self, **kwargs):
        return None

    def screenshot(self, path: str) -> None:
        return None

    def _state(self) -> dict:
        return {"lives": self.lives, "ball": {"y": self.y, "r": self.r}, "H": self.H}

    def _is_bottom_check(self, text: str) -> bool:
        if "new Function" in text or "JSON.parse" in text or "Object.keys" in text:
            return False
        lowered = text.lower()
        return ("ball" in lowered or "b.y" in lowered) and ">" in text and "y" in lowered

    def evaluate(self, script, arg=None):
        text = script if isinstance(script, str) else ""
        if self._is_bottom_check(text):
            self.condition_checks += 1
            crossed = (self.y - self.r) > self.H
            self.saw_crossed_check = self.saw_crossed_check or crossed
            self.saw_inside_check = self.saw_inside_check or not crossed
            return crossed
        if text == _EVAL_JS or "new Function" in text:
            expr = arg[0].replace(" ", "")
            if expr == "s.lives===2":
                self.lives_read_at = self.elapsed
                self.y_at_read = self.y
                return self.lives == 2
            return True
        if "JSON.parse" in text:
            return self._state()
        if "Object.keys(window.__gameTest" in text:
            return ["aimBallBelowPaddle"]
        if text.startswith("(fields)"):
            return [name for name in arg if name not in self._state()]
        if "typeof window.__gameTest[fn]" in text:
            return arg == "aimBallBelowPaddle"
        if "window.__gameTest[fn]" in text:
            self.armed = True
            self.y = 390.0  # below the paddle, still inside: 390 - 8 <= 400
            self.armed_at = self.elapsed
            return None
        raise AssertionError("unexpected script " + text[:160])


def _drop_spec() -> dict:
    return {
        "contract": {
            "state_fields": {"lives": "number", "ball": "{y, r}", "H": "canvas height"},
            "test_hooks": {"aimBallBelowPaddle": "place the ball below the paddle"},
        },
        "scenarios": [{
            "criterion_id": "REQ-DROP",
            "description": "A ball falling past the bottom costs one life",
            "steps": [{"call": "aimBallBelowPaddle"}, {"expect": "s.lives === 2"}],
        }],
    }


class BallDropWaitTests(unittest.TestCase):
    def test_lives_are_read_only_after_the_ball_centre_crosses_the_bottom(self):
        game = _DropGame(vy=0.1)
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(game, _drop_spec(), Path(directory), 30.0)
        block = blocks["REQ-DROP"]
        self.assertEqual(block["status"], "pass", block)
        self.assertIsNotNone(game.lives_read_at)
        self.assertIsNotNone(game.y_at_read)
        self.assertGreater(game.y_at_read - game.r, game.H)
        self.assertTrue(game.saw_inside_check)
        self.assertTrue(game.saw_crossed_check)
        waited = game.lives_read_at - game.armed_at
        self.assertGreater(waited, 120)
        self.assertLess(waited, 1500)

    def test_ball_drop_wait_is_capped_when_the_ball_never_crosses(self):
        game = _DropGame(vy=0.0)
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(game, _drop_spec(), Path(directory), 30.0)
        block = blocks["REQ-DROP"]
        self.assertGreaterEqual(game.condition_checks, 2)
        self.assertFalse(game.saw_crossed_check)
        self.assertNotEqual(block["status"], "pass", block)
        self.assertIsNotNone(game.armed_at)
        self.assertLessEqual(game.elapsed - game.armed_at, 2500)


if __name__ == "__main__":
    unittest.main()
