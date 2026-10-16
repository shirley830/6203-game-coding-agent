"""Spec-derived acceptance: parsing, verifier wiring, harness scenarios, mouse input."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from agent.planning.prompts import build_decision_prompt
from agent.state import CheckStatus, Criterion, Phase, ToolKind
from agent.verification import GameVerifier
from agent.verification.evidence import EvidenceLedger, evaluate_criteria
from agent.verification.spec_criteria import (
    criteria_from, harness_payload, parse_spec_acceptance,
)
from tests.test_integration_tasks import LIMITS, view

HARNESS = Path(__file__).resolve().parents[1] / "agent" / "verification" / "playwright_game_check.py"

COLLECT_SPEC = {
    "state_contract": {
        "state_fields": {"score": "number", "status": "'playing' | 'won'",
                         "player": "{x, y} pixels"},
        "test_hooks": {"setScore": "setScore(n)", "coinRightOfPlayer": "put the coin 20px right"},
    },
    "requirements": [
        {"id": "R1", "category": "interaction", "description": "Arrow keys move the player",
         "steps": [{"save": "x0", "expr": "s.player.x"}, {"key": "ArrowRight"},
                   {"expect": "s.player.x > saved.x0"}]},
        {"id": "R2", "category": "functional", "description": "Pickup adds one point",
         "steps": [{"call": "coinRightOfPlayer"}, {"key": "ArrowRight"},
                   {"expect": "s.score === 1"}]},
        {"id": "R3", "category": "functional", "description": "Win at ten points freezes input",
         "steps": [{"call": "setScore", "args": [9]}, {"call": "coinRightOfPlayer"},
                   {"key": "ArrowRight"}, {"expect": "s.status === 'won'"},
                   {"save": "x1", "expr": "s.player.x"}, {"key": "ArrowRight"},
                   {"expect": "s.player.x === saved.x1"}]},
        {"id": "R4", "category": "functional", "description": "#restart resets score",
         "steps": [{"call": "setScore", "args": [5]}, {"click": "#restart"},
                   {"expect": "s.score === 0 && s.status === 'playing'"}]},
    ],
}

COLLECT_GAME = """<!doctype html><html><body>
<p id="score">Score: 0</p><button id="restart">Restart</button>
<canvas id="c" width="300" height="200"></canvas>
<script>
const st = {score: 0, status: 'playing', player: {x: 20, y: 100}, coin: {x: 200, y: 100}};
window.__gameState = st;
const ctx = document.getElementById('c').getContext('2d');
function draw() { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 300, 200);
  ctx.fillStyle = '#ff0'; ctx.fillRect(st.coin.x, st.coin.y, 10, 10);
  ctx.fillStyle = '#0f0'; ctx.fillRect(st.player.x, st.player.y, 10, 10);
  document.getElementById('score').textContent = 'Score: ' + st.score; }
window.__gameTest = {
  setScore(n) { st.score = n; draw(); },
  coinRightOfPlayer() { st.coin = {x: st.player.x + 20, y: st.player.y}; draw(); },
};
addEventListener('keydown', (e) => {
  if (FREEZE && st.status !== 'playing') return;
  if (e.key === 'ArrowRight') st.player.x += 20;
  if (e.key === 'ArrowLeft') st.player.x -= 20;
  if (Math.abs(st.player.x - st.coin.x) < 10 && st.player.y === st.coin.y) {
    st.score += 1; st.coin = {x: 250, y: 30};
    if (st.score >= 10) st.status = 'won';
  }
  draw();
});
document.getElementById('restart').onclick = () => {
  st.score = 0; st.status = 'playing'; st.player = {x: 20, y: 100}; draw(); };
draw();
</script></body></html>"""

LIGHTS_SPEC = {
    "state_contract": {"state_fields": {"grid": "{cols, rows}", "lights": "16 booleans",
                                        "moves": "number"}, "test_hooks": {}},
    "requirements": [
        {"id": "L1", "category": "interaction", "description": "Clicking a cell toggles it and its neighbours",
         "steps": [{"save": "m0", "expr": "s.moves"}, {"click_cell": [1, 1]},
                   {"expect": "s.lights[5] && s.lights[1] && s.lights[4] && s.lights[6] && s.lights[9]"},
                   {"expect": "!s.lights[0] && s.moves === saved.m0 + 1"}]},
        {"id": "L2", "category": "functional", "description": "Corner click stays within bounds",
         "steps": [{"click_cell": [0, 0]}, {"expect": "s.lights.filter(Boolean).length === 3"}]},
        {"id": "L3", "category": "functional", "description": "Moves count once per click",
         "steps": [{"click_cell": [3, 3]}, {"click_cell": [3, 3]}, {"expect": "s.moves === 2"}]},
    ],
}

LIGHTS_GAME = """<!doctype html><html><body><p id="moves">Moves: 0</p>
<canvas id="c" width="200" height="200"></canvas>
<script>
const st = {grid: {cols: 4, rows: 4}, lights: Array(16).fill(false), moves: 0};
window.__gameState = st;
const c = document.getElementById('c'), ctx = c.getContext('2d');
function draw() { for (let i = 0; i < 16; i++) { ctx.fillStyle = st.lights[i] ? '#ff0' : '#333';
  ctx.fillRect((i % 4) * 50 + 2, Math.floor(i / 4) * 50 + 2, 46, 46); }
  document.getElementById('moves').textContent = 'Moves: ' + st.moves; }
c.addEventListener('click', (e) => {
  const r = c.getBoundingClientRect();
  const col = Math.floor((e.clientX - r.left) / (r.width / 4));
  const row = Math.floor((e.clientY - r.top) / (r.height / 4));
  for (const [dc, dr] of [[0,0],[1,0],[-1,0],[0,1],[0,-1]]) {
    const cc = col + dc, rr = row + dr;
    if (cc >= 0 && cc < 4 && rr >= 0 && rr < 4) st.lights[rr * 4 + cc] = !st.lights[rr * 4 + cc];
  }
  st.moves += 1; draw();
});
draw();
</script></body></html>"""


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_harness(game_html: str, spec: dict) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        game = Path(directory) / "game"
        game.mkdir()
        (game / "index.html").write_text(game_html, encoding="utf-8")
        payload = harness_payload(parse_spec_acceptance(json.dumps(spec)))
        port = str(_free_port())
        done = subprocess.run(
            [sys.executable, str(HARNESS), "--timeout-seconds", "80",
             "--evidence-dir", str(Path(directory) / "evidence"),
             "--scenarios-json", json.dumps(payload),
             "--launch", sys.executable, "-m", "http.server", port, "--bind", "127.0.0.1"],
            cwd=game, capture_output=True, text=True, timeout=120)
        line = next(l for l in done.stdout.splitlines() if l.startswith("HARNESS_JSON="))
        return json.loads(line[len("HARNESS_JSON="):])


def _browsers_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
            return True
    except Exception:
        return False


class ParsingTests(unittest.TestCase):
    def test_valid_document_becomes_frozen_criteria(self):
        acceptance = parse_spec_acceptance("```json\n" + json.dumps(COLLECT_SPEC) + "\n```")
        criteria = criteria_from(acceptance)
        ids = [c.criterion_id for c in criteria]
        self.assertEqual(ids, ["C-CONTRACT", "REQ-R1", "REQ-R2", "REQ-R3", "REQ-R4"])
        self.assertTrue(all(c.mandatory for c in criteria))
        self.assertIn("window.__gameState", criteria[0].description)
        self.assertIn("[test:", criteria[3].description)

    def test_invalid_requirements_are_dropped_individually(self):
        bad_hook = json.loads(json.dumps(COLLECT_SPEC))
        bad_hook["requirements"][1]["steps"][0]["call"] = "cheat"
        bad_hook["requirements"][0]["steps"][1] = {"key": "ArrowRight", "wait": 10}
        bad_hook["requirements"][2]["steps"].insert(0, {"resetGame": None})
        with self.assertRaises(ValueError):  # only one valid requirement left
            parse_spec_acceptance(json.dumps(bad_hook))
        one_bad = json.loads(json.dumps(COLLECT_SPEC))
        one_bad["requirements"][1]["steps"][0]["call"] = "cheat"
        one_bad["requirements"].append({"id": "R5", "category": "functional",
                                        "description": "no assertion", "steps": [{"key": "Space"}]})
        acceptance = parse_spec_acceptance(json.dumps(one_bad))
        self.assertEqual([r.id for r in acceptance.requirements], ["R1", "R3", "R4"])
        self.assertTrue(any("R2" in n and "undeclared" in n for n in acceptance.notes))
        self.assertTrue(any("R5" in n and "invalid" in n for n in acceptance.notes))

    def test_nested_field_descriptions_are_accepted(self):
        document = json.loads(json.dumps(COLLECT_SPEC))
        document["state_contract"]["state_fields"]["player"] = {"x": "number", "y": "number"}
        acceptance = parse_spec_acceptance(json.dumps(document) + "\nThanks! {\"extra\": 1}")
        self.assertIn('"x"', acceptance.state_contract.state_fields["player"])

    def test_hook_call_shorthand_is_accepted(self):
        document = json.loads(json.dumps(COLLECT_SPEC))
        document["requirements"][2]["steps"][0] = {"setScore": [9]}
        document["requirements"][1]["steps"][0] = {"coinRightOfPlayer": None}
        acceptance = parse_spec_acceptance(json.dumps(document))
        self.assertEqual(len(acceptance.requirements), 4, acceptance.notes)
        self.assertEqual(acceptance.requirements[2].steps[0].call, "setScore")
        self.assertEqual(acceptance.requirements[2].steps[0].args, [9])

    def test_canvas_clicks_dropped_for_keyboard_specs(self):
        document = json.loads(json.dumps(COLLECT_SPEC))
        document["requirements"].append({"id": "R9", "category": "functional",
            "description": "Pickup by clicking", "steps": [{"click_at": [0.3, 0.3]},
                                                            {"expect": "s.score === 1"}]})
        keyboard = parse_spec_acceptance(json.dumps(document), "Move with arrow keys. Restart button.")
        self.assertNotIn("R9", [r.id for r in keyboard.requirements])
        self.assertTrue(any("R9" in n and "mouse" in n for n in keyboard.notes))
        mouse = parse_spec_acceptance(json.dumps(document), "A mouse-controlled memory game.")
        self.assertIn("R9", [r.id for r in mouse.requirements])


class Client:
    def __init__(self, reply):
        self.reply = reply

    async def generate(self, *, system_prompt, user_prompt, timeout_seconds):
        return self.reply


class VerifierWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_spec_suite_and_harness_arguments(self):
        verifier = GameVerifier(client=Client(json.dumps(COLLECT_SPEC)))
        suite = await verifier.prepare_acceptance(view(phase=Phase.INITIALIZING), LIMITS)
        ids = [c.criterion_id for c in suite.criteria]
        self.assertIn("C-LAUNCH", ids)
        self.assertIn("REQ-R3", ids)
        self.assertTrue(suite.version.endswith("-spec"))
        launch = ("python3", "-m", "http.server", "8000")
        argv = verifier._emit_harness(view(launch=launch), LIMITS).arguments["argv"]
        self.assertLess(argv.index("--scenarios-json"), argv.index("--launch"))
        self.assertEqual(tuple(argv[-4:]), launch)

    async def test_bad_model_reply_falls_back_to_generic_suite(self):
        verifier = GameVerifier(client=Client("not json"))
        suite = await verifier.prepare_acceptance(view(phase=Phase.INITIALIZING), LIMITS)
        self.assertFalse(any(c.criterion_id.startswith("REQ-") for c in suite.criteria))
        self.assertIn("failed", verifier.acceptance_note)
        info = next(c for c in suite.criteria if c.criterion_id == "C-ACCEPTANCE-INFO")
        self.assertFalse(info.mandatory)
        self.assertIn("failed", info.description)

    def test_unexecuted_scenario_fails_its_criterion(self):
        ledger = EvidenceLedger()
        ledger.harness = {"checks": {"launch": {"status": "pass"}}, "requirements": {}}
        ledger.tool_ops.append("tool:run_command:x")
        results = evaluate_criteria((Criterion("REQ-R1", "x", "interaction"),), ledger)
        self.assertEqual(results[0].status, CheckStatus.FAIL)
        self.assertIn("not executed", results[0].detail)

    def test_generic_interaction_accepts_spec_scenario_proof(self):
        ledger = EvidenceLedger()
        ledger.harness = {"checks": {"interaction": {"status": "fail", "detail": "probe missed"}},
                          "requirements": {"REQ-L1": {"status": "pass", "evidence_refs": ["s.png"]}}}
        ledger.tool_ops.append("tool:run_command:x")
        results = evaluate_criteria((Criterion("C-INTERACTION", "x", "interaction"),
                                     Criterion("REQ-L1", "click", "interaction")), ledger)
        self.assertEqual([r.status for r in results], [CheckStatus.PASS, CheckStatus.PASS])
        self.assertIn("REQ-L1", results[0].detail)

    def test_decision_format_slips_are_repaired(self):
        from agent.planning.parser import parse_decision
        from agent.state import RequestVerification, ToolAction
        action = parse_decision('{"decision": "read_file", "path": "game.js"}')
        self.assertIsInstance(action, ToolAction)
        self.assertEqual((action.kind, dict(action.arguments)), (ToolKind.READ_FILE, {"path": "game.js"}))
        verify = parse_decision('{"decision": "request_verification", "launch_command": ["a"], '
                                '"rationale": "done"}\n{"second": true}')
        self.assertIsInstance(verify, RequestVerification)
        with self.assertRaises(Exception):
            parse_decision('{"decision": "delete_everything"}')

    def test_planner_prompt_forbids_gaming_the_verifier(self):
        prompt = build_decision_prompt(view())
        self.assertIn("NEVER add controls", prompt)
        self.assertIn("window.__gameState must reflect the REAL", prompt)


@unittest.skipUnless(_browsers_ready(), "Playwright Chromium not installed")
class HarnessScenarioTests(unittest.TestCase):
    def test_correct_game_passes_every_scenario(self):
        result = run_harness(COLLECT_GAME.replace("FREEZE", "true"), COLLECT_SPEC)
        self.assertEqual(result["checks"]["contract"]["status"], "pass", result["checks"]["contract"])
        statuses = {k: v["status"] for k, v in result["requirements"].items()}
        self.assertEqual(statuses, {"REQ-R1": "pass", "REQ-R2": "pass", "REQ-R3": "pass", "REQ-R4": "pass"},
                         result["requirements"])
        # The browser's own /favicon.ico 404 is not a game error.
        self.assertEqual(result["checks"]["functional"]["status"], "pass", result["page_errors"])

    def test_rule_violation_fails_with_the_failing_step(self):
        result = run_harness(COLLECT_GAME.replace("FREEZE", "false"), COLLECT_SPEC)
        r3 = result["requirements"]["REQ-R3"]
        self.assertEqual(r3["status"], "fail")
        self.assertIn("saved.x1", r3["detail"])
        self.assertIn("state after", r3["detail"])
        self.assertEqual(result["requirements"]["REQ-R1"]["status"], "pass")

    def test_missing_contract_fails_contract_and_scenarios(self):
        result = run_harness(COLLECT_GAME.replace("FREEZE", "true")
                             .replace("window.__gameState = st;", ""), COLLECT_SPEC)
        self.assertEqual(result["checks"]["contract"]["status"], "fail")
        self.assertTrue(all(v["status"] == "fail" for v in result["requirements"].values()))

    def test_mouse_only_game_passes_by_clicks(self):
        result = run_harness(LIGHTS_GAME, LIGHTS_SPEC)
        self.assertEqual(result["checks"]["interaction"]["status"], "pass",
                         result["checks"]["interaction"])
        self.assertIn("click", result["checks"]["interaction"]["detail"])
        statuses = {k: v["status"] for k, v in result["requirements"].items()}
        self.assertEqual(statuses, {"REQ-L1": "pass", "REQ-L2": "pass", "REQ-L3": "pass"},
                         result["requirements"])


if __name__ == "__main__":
    unittest.main()
