"""Production dependency factory: wires the five team modules into the controller.

Default factory for ``python -m agent game_design_prompt.md``. Every port is a
real implementation; test doubles live only under tests/.

Environment variables (read here, never stored in state, prompts or traces):

- ``AGENT_MODEL``       required, a LiteLLM model name, e.g. ``anthropic/claude-sonnet-5-5``
                        or ``openai/gpt-5``. Provider credentials use the provider's own
                        variable (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ...).
- ``AGENT_API_BASE``    optional custom endpoint for the course-provided API.
- ``AGENT_MAX_TOKENS``  optional output limit per model call (default 16000).
- ``AGENT_STRICT_SANDBOX``  ``0`` disables the OS command sandbox (not recommended).
- ``AGENT_SKIP_PREFLIGHT``  ``1`` skips the startup self-check (not recommended).

Values may also come from ``agent.env`` in the run directory (see ``agent/envfile.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import sys

from .config import AgentConfig
from .controller import Dependencies
from .planning import LiteLLMClient, LLMPlanner
from .preflight import run_preflight
from .recovery import RuleBasedRecovery
from .tools import LocalRuntime, RuntimeSettings
from .tracing.recorder import FileTraceRecorder
from .verification import GameVerifier


def _playwright_environment() -> dict[str, str]:
    """Commands run with an isolated HOME, so point Playwright at the real browsers."""
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return {"PLAYWRIGHT_BROWSERS_PATH": configured}
    home = Path.home()
    if platform.system() == "Darwin":
        default = home / "Library" / "Caches" / "ms-playwright"
    elif platform.system() == "Windows":
        default = Path(os.environ.get("LOCALAPPDATA", home)) / "ms-playwright"
    else:
        default = home / ".cache" / "ms-playwright"
    return {"PLAYWRIGHT_BROWSERS_PATH": str(default)}


def create_dependencies(config: AgentConfig) -> Dependencies:
    model = os.environ.get("AGENT_MODEL", "").strip()
    if not model:
        raise RuntimeError(
            "AGENT_MODEL is not set. Example: export AGENT_MODEL=anthropic/claude-sonnet-5-5 "
            "and the provider key (e.g. ANTHROPIC_API_KEY) before starting the agent."
        )
    strict = os.environ.get("AGENT_STRICT_SANDBOX", "1") != "0"
    browser_environment = _playwright_environment()
    notes: list[str] = []
    if os.environ.get("AGENT_SKIP_PREFLIGHT") != "1":
        report = run_preflight(strict_sandbox=strict, browser_environment=browser_environment)
        print(report.render(), file=sys.stderr)
        if not report.ok:
            # Stop before game/ exists: this run could never pass verification.
            raise RuntimeError("Startup self-check failed: " + "; ".join(report.blocking))
        notes = report.notes
    client = LiteLLMClient(
        model=model,
        api_base=os.environ.get("AGENT_API_BASE") or None,
        max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", "16000")),
    )
    runtime = LocalRuntime(RuntimeSettings(
        strict_os_sandbox=strict,
        injected_environment=browser_environment,
        # Room for the harness JSON with per-scenario results on one stdout line.
        output_preview_bytes=64 * 1024,
    ))
    return Dependencies(
        planner=LLMPlanner(client, environment_notes=notes),
        runtime=runtime,
        # Same model, separate role: derives spec scenarios before planning.
        verifier=GameVerifier(client=client),
        recovery=RuleBasedRecovery(),
        tracer=FileTraceRecorder(),
    )
