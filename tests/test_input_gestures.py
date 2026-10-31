"""Real pointer gestures in the harness, and pre-run checks of generated scenarios."""

import asyncio
import json
import unittest

from agent.verification.spec_criteria import (
    AcceptanceGaps, Requirement, harness_payload, lint_requirement, parse_revision,
    parse_spec_acceptance,
)
from tests.test_integration_tasks import LIMITS, view
from tests.test_spec_acceptance import _browsers_ready, run_harness
from agent.verification import GameVerifier

# Canvas box: press on it, move, release. A bare click at the target must not move it.
DRAG_GAME = """<!DOCTYPE html><html><body><canvas width="200" height="100"></canvas>
<div id="a" style="width:40px;height:40px">a</div><div id="b" style="width:40px;height:40px">b</div>
<p>Score: 0</p><script>
const st = {box: {x: 20}, held: false, drops: 0, dbl: 0, hover: false, longPress: 0, onB: false};
window.__gameState = st;
const c = document.querySelector('canvas');
const px = (e) => e.clientX - c.getBoundingClientRect().left;
let down = 0;
c.addEventListener('mousedown', (e) => { down = Date.now(); if (Math.abs(px(e) - st.box.x) < 25) st.held = true; });
c.addEventListener('mousemove', (e) => { st.hover = true; if (st.held) st.box.x = px(e); });
c.addEventListener('mouseup', () => { if (st.held) st.drops += 1; st.held = false;
  if (Date.now() - down > 500) st.longPress += 1; });
c.addEventListener('dblclick', () => { st.dbl += 1; });
const a = document.getElementById('a');
let dragging = false;
a.addEventListener('mousedown', () => { dragging = true; });
document.addEventListener('mouseup', (e) => {
  if (dragging && document.elementFromPoint(e.clientX, e.clientY).id === 'b') st.onB = true;
  dragging = false; });
</script></body></html>"""

FIELDS = {"box": "{x}", "drops": "n", "dbl": "n", "hover": "bool", "longPress": "n", "onB": "bool"}


def spec(*requirements, hooks=None, fields=None, **extra):
    return json.dumps({"state_contract": {"state_fields": fields or FIELDS, "test_hooks": hooks or {}},
                       "requirements": list(requirements), **extra})


def req(rid, steps, description="A rule of the game"):
    return {"id": rid, "category": "interaction", "description": description, "steps": steps}


OK = [req("A", [{"key": "Space"}, {"expect": "s.drops === 0"}]),
      req("B", [{"key": "KeyP"}, {"expect": "s.dbl === 0"}]),
      req("C", [{"key": "KeyQ"}, {"expect": "s.hover === false"}])]


@unittest.skipUnless(_browsers_ready(), "Chromium is not installed")
class GestureHarnessTests(unittest.TestCase):
    def statuses(self, *requirements):
        document = json.loads(spec(*requirements))
        return run_harness(DRAG_GAME, document)["requirements"]

    def test_gestures_reach_the_game(self):
        result = self.statuses(
            req("D1", [{"drag_at": [0.1, 0.5, 0.8, 0.5]}, {"expect": "s.box.x > 150 && s.drops === 1"}]),
            req("D2", [{"drag": "#a", "to": "#b"}, {"expect": "s.onB === true"}]),
            req("D3", [{"click_at": [0.5, 0.5], "clicks": 2}, {"expect": "s.dbl === 1"}]),
            req("D4", [{"hover_at": [0.5, 0.5]}, {"expect": "s.hover === true"}]),
            req("D5", [{"press_at": [0.5, 0.5], "ms": 700}, {"expect": "s.longPress === 1"}]))
        self.assertEqual({k: v["status"] for k, v in result.items()},
                         {f"REQ-D{i}": "pass" for i in range(1, 6)}, result)

    def test_two_clicks_do_not_count_as_a_drag(self):
        result = self.statuses(
            req("D1", [{"click_at": [0.1, 0.5]}, {"click_at": [0.8, 0.5]}, {"expect": "s.box.x > 150"}]),
            *OK[:2])
        self.assertEqual(result["REQ-D1"]["status"], "fail", result)


class LintTests(unittest.TestCase):
    def lint(self, steps, description="A rule of the game", fields=frozenset({"score", "status"})):
        return lint_requirement(Requirement.model_validate(req("R", steps, description)), set(fields))

    def test_valid_scenario_passes(self):
        self.assertEqual(self.lint([{"save": "a", "expr": "s.score"}, {"key": "Space"},
                                    {"expect": "s.score > saved.a"},
                                    {"expect": "[1].every(s => s.other === 1) || true"}]), "")

    def test_saved_value_used_before_it_is_saved(self):
        self.assertIn("saved", self.lint([{"key": "Space"}, {"expect": "s.score > saved.a"}]))

    def test_undeclared_state_field(self):
        self.assertIn("state_fields", self.lint([{"key": "Space"}, {"expect": "s.lives === 3"}]))

    def test_contradictory_expectations_without_input(self):
        problem = self.lint([{"key": "Space"}, {"expect": "s.status === 'won'"},
                             {"expect": "s.status === 'lost'"}])
        self.assertIn("both", problem)
        self.assertEqual(self.lint([{"key": "Space"}, {"expect": "s.status === 'won'"}, {"key": "KeyR"},
                                    {"expect": "s.status === 'playing'"}]), "")

    def test_hook_only_scenario(self):
        self.assertIn("hooks", self.lint([{"call": "win"}, {"expect": "s.status === 'won'"}]))
        self.assertEqual(self.lint([{"expect": "s.score === 0"}]), "")

    def test_gesture_cannot_be_replaced_by_clicks(self):
        self.assertIn("drag", self.lint([{"click_at": [0.1, 0.1]}, {"click_at": [0.9, 0.9]},
                                         {"expect": "s.score === 1"}], "Dragging a crate scores"))
        self.assertEqual(self.lint([{"drag_at": [0.1, 0.1, 0.9, 0.9]}, {"expect": "s.score === 1"}],
                                   "Dragging a crate scores"), "")


class ParserTests(unittest.TestCase):
    def test_drag_spec_without_a_drag_scenario_is_a_gap(self):
        with self.assertRaises(AcceptanceGaps):
            parse_spec_acceptance(spec(*OK), "Drag crates with the mouse.", strict=True)
        acceptance = parse_spec_acceptance(spec(*OK), "Drag crates with the mouse.")
        self.assertTrue(any("drag" in item for item in acceptance.unverified))
        self.assertTrue(any(n.startswith("unverified:") for n in acceptance.notes))

    def test_negated_gesture_is_not_a_gap(self):
        self.assertEqual(parse_spec_acceptance(spec(*OK), "Use keys, no drag.", strict=True).unverified, [])

    def test_random_spec_needs_a_hook(self):
        with self.assertRaises(AcceptanceGaps):
            parse_spec_acceptance(spec(*OK), "Coins spawn at random.", strict=True)
        hooked = spec(*OK, hooks={"placeCoin": "placeCoin(x, y)"})
        self.assertEqual(parse_spec_acceptance(hooked, "Coins spawn at random.", strict=True).unverified, [])

    def test_model_can_declare_unverified_behaviour(self):
        acceptance = parse_spec_acceptance(spec(*OK, unverified=["pinch zoom"]))
        self.assertEqual(acceptance.unverified, ["pinch zoom"])

    def test_grid_is_added_for_cell_steps(self):
        acceptance = parse_spec_acceptance(spec(
            req("G", [{"click_cell": [1, 1]}, {"expect": "s.grid.cols > 0"}]), *OK))
        self.assertIn("grid", acceptance.state_contract.state_fields)

    def test_field_a_scenario_reads_joins_the_contract(self):
        acceptance = parse_spec_acceptance(spec(req("F", [{"key": "Space"}, {"expect": "s.lives === 3"}]), *OK))
        self.assertIn("lives", acceptance.state_contract.state_fields)

    def test_payload_keeps_gesture_fields(self):
        acceptance = parse_spec_acceptance(spec(
            req("P", [{"press_at": [0.5, 0.5]}, {"drag": "#a", "to": "#b"},
                      {"click_at": [0.1, 0.1], "clicks": 2}, {"expect": "s.drops === 0"}]), *OK))
        steps = harness_payload(acceptance)["scenarios"][0]["steps"]
        self.assertEqual(steps[:3], [{"press_at": [0.5, 0.5], "ms": 300}, {"drag": "#a", "to": "#b"},
                                     {"click_at": [0.1, 0.1], "clicks": 2}])

    def test_revision_cannot_swap_a_drag_for_clicks(self):
        document = spec(req("D", [{"drag_at": [0, 0, 1, 1]}, {"expect": "s.drops === 1"}], "Drag a box"), *OK)
        acceptance = parse_spec_acceptance(document)
        reply = json.dumps({"verdict": "replace", "reason": "x", "requirement": req(
            "D", [{"click_at": [0, 0]}, {"click_at": [1, 1]}, {"expect": "s.drops === 1"}], "Drag a box")})
        with self.assertRaises(ValueError):
            parse_revision(reply, acceptance, "D", "Drag a box")


class Replies:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        self.calls += 1
        return self.replies.pop(0)


class RetryTests(unittest.TestCase):
    def derive(self, client):
        verifier = GameVerifier(client=client)
        return asyncio.run(verifier._derive_spec_acceptance("Drag crates with the mouse.", LIMITS))

    def test_a_gap_gets_one_more_attempt(self):
        fixed = spec(req("D", [{"drag_at": [0, 0, 1, 1]}, {"expect": "s.drops === 1"}]), *OK)
        client = Replies(spec(*OK), fixed)
        self.assertEqual(self.derive(client).unverified, [])
        self.assertEqual(client.calls, 2)

    def test_a_remaining_gap_is_reported_not_fatal(self):
        client = Replies(spec(*OK), spec(*OK))
        acceptance = self.derive(client)
        self.assertEqual(len(acceptance.requirements), 3)
        self.assertTrue(acceptance.unverified)


if __name__ == "__main__":
    unittest.main()
