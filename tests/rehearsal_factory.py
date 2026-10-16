"""Offline rehearsal factory: production modules + a scripted model client.

NOT a production path. It exists so the whole pipeline (controller, real
runtime and OS sandbox, Playwright verifier, recovery, tracing) can be
rehearsed without an API key:

    PYTHONPATH=<repo> python -m agent game_design_prompt.md \
        --factory tests.rehearsal_factory:create_dependencies

The script first writes a game that ignores keyboard input, so verification
fails on interaction, recovery chooses REPAIR, and a fixed game then passes.
"""

from __future__ import annotations

import json
import re

from agent.config import AgentConfig
from agent.controller import Dependencies
from agent.planning import LLMPlanner
from agent.planning.prompts import DEFAULT_LAUNCH_COMMAND
from agent.recovery import RuleBasedRecovery
from agent.tools import LocalRuntime, RuntimeSettings
from agent.tracing.recorder import FileTraceRecorder
from agent.verification import GameVerifier
from agent.integrations import _playwright_environment


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Collector</title></head>
<body>
<p id="score">Score: 0</p>
<p>Controls: arrow keys move. <button id="restart">Restart</button></p>
<canvas id="board" width="320" height="240"></canvas>
<script src="game.js"></script>
</body></html>
"""

_BROKEN_JS = """const ctx = document.getElementById('board').getContext('2d');
let player = {x: 20, y: 20};
function draw() {
  ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 320, 240);
  ctx.fillStyle = '#3c6'; ctx.fillRect(player.x, player.y, 16, 16);
}
draw();
"""

_FIXED_JS = """const ctx = document.getElementById('board').getContext('2d');
const scoreEl = document.getElementById('score');
let player, coin, score;
function reset() { player = {x: 20, y: 20}; coin = {x: 60, y: 20}; score = 0; render(); }
function render() {
  ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 320, 240);
  ctx.fillStyle = '#fc3'; ctx.fillRect(coin.x, coin.y, 12, 12);
  ctx.fillStyle = '#3c6'; ctx.fillRect(player.x, player.y, 16, 16);
  scoreEl.textContent = 'Score: ' + score;
}
const moves = {ArrowRight: [20, 0], ArrowLeft: [-20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20]};
window.addEventListener('keydown', (event) => {
  const step = moves[event.key];
  if (!step) return;
  player.x = Math.max(0, Math.min(304, player.x + step[0]));
  player.y = Math.max(0, Math.min(224, player.y + step[1]));
  if (Math.abs(player.x - coin.x) < 16 && Math.abs(player.y - coin.y) < 16) {
    score += 1; coin = {x: (coin.x + 100) % 300, y: (coin.y + 60) % 220};
  }
  render();
});
document.getElementById('restart').addEventListener('click', reset);
reset();
"""


def _write(path: str, content: str) -> str:
    return json.dumps({"decision": "tool_action", "action_id": "x", "kind": "write_file",
                       "arguments": {"path": path, "content": content}, "rationale": "scripted"})


_VERIFY = json.dumps({"decision": "request_verification",
                      "launch_command": list(DEFAULT_LAUNCH_COMMAND)})


class ScriptedClient:
    def __init__(self) -> None:
        self.decisions = [
            _write("index.html", _PAGE),
            "```json\n" + _write("game.js", _BROKEN_JS) + "\n```",  # fenced on purpose
            _VERIFY,   # planner guard writes README.md first
            _VERIFY,   # verification runs and fails on interaction
            _write("game.js", _FIXED_JS),
            _VERIFY,
        ]

    async def generate(self, *, system_prompt: str, user_prompt: str, timeout_seconds: float) -> str:
        if "Create a development plan" in user_prompt:
            ids = sorted(set(re.findall(r'"criterion_id": "([^"]+)"', user_prompt)))
            return json.dumps({"plan_id": "plan-rehearsal", "tasks": [
                {"task_id": "t1", "description": "Page, canvas and HUD", "requirement_ids": ids[:1] or ids,
                 "depends_on": []},
                {"task_id": "t2", "description": "Movement, scoring and restart",
                 "requirement_ids": ids, "depends_on": ["t1"]},
            ]})
        if not self.decisions:
            raise RuntimeError("Rehearsal script exhausted")
        return self.decisions.pop(0)


def create_dependencies(config: AgentConfig) -> Dependencies:
    return Dependencies(
        planner=LLMPlanner(ScriptedClient()),
        runtime=LocalRuntime(RuntimeSettings(injected_environment=_playwright_environment())),
        verifier=GameVerifier(),
        recovery=RuleBasedRecovery(),
        tracer=FileTraceRecorder(),
    )
