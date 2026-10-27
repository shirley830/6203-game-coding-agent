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
from agent.verification.playwright_game_check import (
    _EVAL_JS, _run_spec_scenarios, unbound_assertion_names,
)
from agent.verification.spec_criteria import parse_spec_acceptance

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
    async def test_fewer_than_three_requirements_is_flagged_but_not_blocking(self):
        verifier = GameVerifier(client=_Reply(_two_requirement_reply()))
        suite = await verifier.prepare_acceptance(_view(), LIMITS)
        ids = [c.criterion_id for c in suite.criteria]
        for generic_id in ("C-LAUNCH", "C-FUNCTIONAL", "C-INTERACTION", "C-TECHNICAL"):
            self.assertIn(generic_id, ids)
        info = next(c for c in suite.criteria if c.criterion_id == "C-ACCEPTANCE-INFO")
        self.assertFalse(info.mandatory)
        incomplete = next(c for c in suite.criteria if c.criterion_id == "C-ACCEPTANCE-INCOMPLETE")
        # Game edits cannot fix missing requirements, so the gap must not block the gate.
        self.assertFalse(incomplete.mandatory)
        self.assertIn("incomplete acceptance", incomplete.description.lower())

        ledger = EvidenceLedger()
        ledger.harness = {"checks": {
            key: {"status": "pass", "detail": "generic ok", "evidence_refs": [f"e:{key}"]}
            for key in ("launch", "functional", "interaction", "technical", "contract")
        }, "requirements": {}, "page_errors": []}
        ledger.tool_ops.append("tool:run_command:x")
        results = {r.criterion_id: r for r in evaluate_criteria(tuple(suite.criteria), ledger)}
        # Still reported as a failed (informational) result, visible in the trace...
        self.assertEqual(results["C-ACCEPTANCE-INCOMPLETE"].status, CheckStatus.FAIL)
        self.assertIn("incomplete acceptance", results["C-ACCEPTANCE-INCOMPLETE"].detail.lower())
        # ...while the mandatory generic checks alone decide the outcome.
        mandatory = [c for c in suite.criteria if c.mandatory]
        self.assertTrue(all(results[c.criterion_id].status == CheckStatus.PASS for c in mandatory),
                        [(c.criterion_id, results[c.criterion_id].status) for c in mandatory])

    def test_broken_assertion_is_dropped_before_freezing(self):
        from agent.verification.spec_criteria import parse_spec_acceptance
        reply = json.dumps({
            "state_contract": {"state_fields": {"score": "number"}, "test_hooks": {}},
            "requirements": [
                {"id": f"R{i}", "category": "functional", "description": f"rule {i}",
                 "steps": [{"key": "ArrowLeft"}, {"expect": "s.score >= 0"}]} for i in range(1, 4)
            ] + [
                {"id": "R9", "category": "functional", "description": "uses an undefined name",
                 "steps": [{"key": "ArrowLeft"}, {"expect": "before === s.score"}]},
                {"id": "R10", "category": "functional", "description": "valid Set and const",
                 "steps": [{"key": "ArrowLeft"},
                           {"expect": "new Set([1, 2]).size === 2 && [1].every(x => { const y = x; return y > 0; })"}]},
            ],
        })
        acceptance = parse_spec_acceptance(reply, "arrow keys move")
        ids = [r.id for r in acceptance.requirements]
        self.assertNotIn("R9", ids)
        self.assertIn("R10", ids)
        self.assertTrue(any("R9" in note and "before" in note for note in acceptance.notes), acceptance.notes)


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

    def test_page_globals_and_arrow_params_are_legal(self):
        self.assertEqual(unbound_assertion_names("document.body.innerText"), [])
        self.assertEqual(
            unbound_assertion_names("s.bricks.every(x => x === false)"), [])
        self.assertEqual(unbound_assertion_names("s.lives < saved.before"), [])
        self.assertEqual(unbound_assertion_names("s.lives < before"), ["before"])


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


DECK = [0, 1, 0, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def _memory_spec(clicks: list[list[int]], expect: str) -> dict:
    return {
        "contract": {
            "state_fields": {"cards": "face values"},
            "test_hooks": {"setDeck": "replace the deck"},
        },
        "scenarios": [{
            "criterion_id": "REQ-MATCH",
            "description": "Two flipped cards stay matched",
            "steps": [
                {"call": "setDeck", "args": [DECK]},
                *[{"click_cell": cell} for cell in clicks],
                {"expect": expect},
            ],
        }],
    }


class _MemoryPage:
    """Page double. Step actions are recorded and are not required to succeed."""

    def __init__(self, *, cols: int | None = None, expect_holds: bool = False) -> None:
        self.expect_holds = expect_holds
        self.actions: list[str] = []
        self.mouse = self._Mouse(self.actions)
        self.state: dict = {"cards": list(DECK)}
        if cols is not None:
            self.state["grid"] = {"cols": cols, "rows": 2}

    class _Mouse:
        def __init__(self, actions: list[str]) -> None:
            self.actions = actions

        def click(self, x: float, y: float) -> None:
            self.actions.append("mouse")

    def reload(self, **kwargs):
        return None

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def screenshot(self, path: str) -> None:
        return None

    def evaluate(self, script, arg=None):
        text = script if isinstance(script, str) else ""
        if "JSON.parse" in text:
            return json.loads(json.dumps(self.state))
        if "Object.keys(window.__gameTest" in text:
            return ["setDeck"]
        if text.startswith("(fields)"):
            return [name for name in arg if name not in self.state]
        if "typeof window.__gameTest[fn]" in text:
            self.actions.append("call-check")
            return True
        if "window.__gameTest[fn]" in text:
            self.actions.append("call")
            return None
        if text.startswith("([col, row])"):
            self.actions.append("click_cell")
            return [1, 1]
        if text == _EVAL_JS or "new Function" in text:
            self.actions.append("expect")
            return self.expect_holds
        raise AssertionError("unexpected script " + text[:160])


class MatchedCardScriptTests(unittest.TestCase):
    def test_different_patterns_are_a_test_script_error(self):
        page = _MemoryPage()
        spec = _memory_spec(
            [[0, 0], [0, 1]],
            "s.cards[0].matched === true && s.cards[3].matched === true",
        )
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(page, spec, Path(directory), 30.0)
        block = blocks["REQ-MATCH"]
        self.assertIn("test_script_error:", block["detail"])
        self.assertEqual(block["status"], "unknown", block)
        self.assertNotEqual(block["status"], "fail")
        self.assertNotEqual(block["status"], "pass")
        self.assertEqual(page.actions, [])

        ledger = EvidenceLedger()
        ledger.harness = {"checks": {}, "requirements": {"REQ-MATCH": block}, "page_errors": []}
        ledger.tool_ops.append("tool:run_command:x")
        result = evaluate_criteria((Criterion("REQ-MATCH", "match", "functional"),), ledger)[0]
        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("test_script_error:", result.detail)
        self.assertNotEqual(result.status, CheckStatus.FAIL)
        self.assertNotEqual(result.status, CheckStatus.PASS)

    def test_same_pattern_is_not_a_test_script_error(self):
        # [0,0] and [2,0] are both pattern 0. grid.cols is known, so the check
        # compares those patterns and then lets a real miss fail as the game.
        page = _MemoryPage(cols=4, expect_holds=False)
        spec = _memory_spec(
            [[0, 0], [2, 0]],
            "s.cards[0].matched === true && s.cards[2].matched === true",
        )
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(page, spec, Path(directory), 30.0)
        block = blocks["REQ-MATCH"]
        self.assertNotIn("test_script_error", block["detail"])
        self.assertEqual(block["status"], "fail", block)
        self.assertIn("expectation false", block["detail"])
        self.assertIn("expect", page.actions)

    def test_negated_match_assertions_do_not_count(self):
        page = _MemoryPage(expect_holds=False)
        spec = _memory_spec(
            [[0, 0], [0, 1]],
            "s.cards[0].matched !== true && s.cards[3].matched === false",
        )
        with tempfile.TemporaryDirectory() as directory:
            _contract, blocks = _run_spec_scenarios(page, spec, Path(directory), 30.0)
        block = blocks["REQ-MATCH"]
        self.assertNotIn("test_script_error", block["detail"])
        self.assertEqual(block["status"], "fail", block)


def _score_requirement(req_id: str, description: str) -> dict:
    return {
        "id": req_id, "category": "functional", "description": description,
        "steps": [{"key": "ArrowRight"}, {"expect": "s.score >= 0"}],
    }


def _acceptance_document(*extra: dict) -> dict:
    requirements = [
        _score_requirement("R1", "score stays non-negative"),
        _score_requirement("R2", "score stays numeric"),
        _score_requirement("R3", "score is still present"),
    ]
    requirements.extend(extra)
    return {
        "state_contract": {"state_fields": {"score": "number"}, "test_hooks": {}},
        "requirements": requirements,
    }


class CanvasClickFilterTests(unittest.TestCase):
    def test_flip_spec_without_pointer_words_keeps_click_cell(self):
        document = _acceptance_document({
            "id": "FLIP", "category": "interaction",
            "description": "Two cards can be turned face up",
            "steps": [{"click_cell": [0, 0]}, {"click_cell": [1, 0]},
                      {"expect": "s.cards[0].faceUp === true"}],
        })
        spec = "记忆翻牌。玩家可以翻开两张牌，点击牌面即可。图案相同就保持匹配。"
        acceptance = parse_spec_acceptance(json.dumps(document), spec)
        self.assertIn("FLIP", [item.id for item in acceptance.requirements])
        self.assertFalse(any("FLIP" in note and "canvas clicks" in note for note in acceptance.notes))

    def test_keyboard_only_without_pointer_drops_canvas_clicks(self):
        document = _acceptance_document({
            "id": "FLIP", "category": "interaction",
            "description": "Clicking the canvas moves the piece",
            "steps": [{"click_cell": [0, 0]}, {"expect": "s.score === 0"}],
        })
        acceptance = parse_spec_acceptance(
            json.dumps(document), "Keyboard-only. No mouse. Move with arrow keys.")
        self.assertNotIn("FLIP", [item.id for item in acceptance.requirements])
        self.assertTrue(any("FLIP" in note and "canvas clicks" in note for note in acceptance.notes))


class _ScriptedReply:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        self.calls += 1
        self.prompts.append(user_prompt)
        if not self.replies:
            raise AssertionError("generate called too many times")
        return self.replies.pop(0)


class IncompleteAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_validation_note_names_the_field(self):
        document = _acceptance_document({
            "id": "Rbad", "category": "functional", "description": "broken cell click",
            "steps": [{"click_cell": [1]}, {"expect": "s.score === 1"}],
        })
        acceptance = parse_spec_acceptance(json.dumps(document), "flip two cards")
        note = next(item for item in acceptance.notes if "Rbad" in item)
        self.assertIn("click_cell", note)
        self.assertNotIn("1 validation error", note)

    async def test_parse_failure_retries_once_and_stays_incomplete(self):
        bad = json.dumps({"state_contract": {"test_hooks": {}}, "requirements": []})
        client = _ScriptedReply([bad, bad])
        verifier = GameVerifier(client=client)
        suite = await verifier.prepare_acceptance(_view(), LIMITS)
        self.assertEqual(client.calls, 2)
        self.assertIn("state_fields", verifier.acceptance_note)
        self.assertIn("state_fields", client.prompts[1])
        self.assertNotIn("1 validation error", verifier.acceptance_note)
        incomplete = next(c for c in suite.criteria if c.criterion_id == "C-ACCEPTANCE-INCOMPLETE")
        self.assertFalse(incomplete.mandatory)
        self.assertIn("incomplete acceptance", incomplete.description.lower())
        self.assertFalse(any(c.criterion_id.startswith("REQ-") for c in suite.criteria))

    async def test_one_retry_can_recover_a_valid_suite(self):
        bad = json.dumps({"state_contract": {"test_hooks": {}}, "requirements": []})
        client = _ScriptedReply([bad, json.dumps(_acceptance_document())])
        verifier = GameVerifier(client=client)
        suite = await verifier.prepare_acceptance(_view(), LIMITS)
        self.assertEqual(client.calls, 2)
        self.assertIn("REQ-R1", [c.criterion_id for c in suite.criteria])
        self.assertFalse(any(c.criterion_id == "C-ACCEPTANCE-INCOMPLETE" for c in suite.criteria))

    async def test_valid_reply_is_not_generated_twice(self):
        client = _ScriptedReply([json.dumps(_acceptance_document())])
        verifier = GameVerifier(client=client)
        suite = await verifier.prepare_acceptance(_view(), LIMITS)
        self.assertEqual(client.calls, 1)
        self.assertIn("REQ-R1", [c.criterion_id for c in suite.criteria])


if __name__ == "__main__":
    unittest.main()
