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
from pathlib import Path
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


# Hand-written fixture games for the offline rehearsal only; the agent never
# reads tests/. See tests/fixtures/README.md.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "games"
_PAGE = (_FIXTURES / "rehearsal_index.html").read_text(encoding="utf-8")

_BROKEN_JS = (_FIXTURES / "rehearsal_broken_game.js").read_text(encoding="utf-8")

_FIXED_JS = (_FIXTURES / "rehearsal_fixed_game.js").read_text(encoding="utf-8")


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
