"""GameVerifier: Member 4 VerifierPort implementation.

Hard constraints honored here:
- Propose only read_file / inspect_workspace / run_command
- Verification commands are expected to start-check-stop inside one harness invocation
- evidence_refs are non-empty on every CriterionResult
- Mandatory launch / functional / interaction coverage via prepare_acceptance
- Never mutates controller AgentState (private cycle state only)
"""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
import sys
from typing import Any

from agent.state import (
    AcceptanceSuite,
    AgentView,
    CallLimits,
    ToolAction,
    ToolKind,
    ToolResult,
    ToolStatus,
    VerificationReport,
)

from .criteria import build_acceptance_suite
from .evidence import EvidenceLedger, evaluate_criteria
from .spec_criteria import (
    SYSTEM_PROMPT, SpecAcceptance, build_prompt, criteria_from, harness_payload,
    parse_spec_acceptance,
)

# Preferred entry files to read after inspect (relative to game/).
_CANDIDATE_FILES = (
    "README.md",
    "readme.md",
    "LAUNCH.md",
    "launch.md",
    "index.html",
    "game.html",
    "main.js",
    "game.js",
    "app.js",
    "src/main.js",
    "src/index.js",
)


class _Step(str, Enum):
    INSPECT = "inspect"
    READ = "read"
    HARNESS = "harness"
    REPORT = "report"


class GameVerifier:
    """Asynchronous VerifierPort adapter for browser / local game acceptance."""

    def __init__(self, *, harness_path: str | Path | None = None, client: Any = None) -> None:
        """Create a verifier.

        harness_path:
            Path to playwright_game_check.py. Defaults to the copy shipped
            inside this package so the submitted agent/ is self-contained.
        """
        if harness_path is None:
            harness_path = Path(__file__).resolve().with_name("playwright_game_check.py")
        self._harness_path = Path(harness_path).resolve()
        # Optional LLM client (same LLMClient protocol as the planner). With it,
        # prepare_acceptance derives spec-specific executable scenarios.
        self._client = client
        self._spec_acceptance: SpecAcceptance | None = None
        self.acceptance_note = "generic criteria (no model client)"
        self._action_seq = 0
        self._reset_cycle()

    def _reset_cycle(self) -> None:
        self._step = _Step.INSPECT
        self._cycle_key: tuple[str | None, str | None, tuple[str, ...]] | None = None
        self._pending_reads: list[str] = []
        self._reads_done: set[str] = set()
        self._ledger = EvidenceLedger()
        self._last_action_id: str | None = None
        self._last_kind: ToolKind | None = None
        self._awaiting_result = False

    def _new_action_id(self, prefix: str) -> str:
        self._action_seq += 1
        return f"verify.{prefix}.{self._action_seq}"

    def _cycle_fingerprint(self, view: AgentView) -> tuple[str | None, str | None, tuple[str, ...]]:
        suite_version = view.suite.version if view.suite else None
        return (view.workspace_revision, suite_version, tuple(view.launch_command))

    def _ensure_cycle(self, view: AgentView) -> None:
        key = self._cycle_fingerprint(view)
        if self._cycle_key != key:
            self._reset_cycle()
            self._cycle_key = key

    async def prepare_acceptance(self, view: AgentView, limits: CallLimits) -> AcceptanceSuite:
        """Create and freeze acceptance criteria from the specification text."""
        generic = build_acceptance_suite(view.spec_text)
        if self._client is None:
            return generic
        try:
            reply = await self._client.generate(
                system_prompt=SYSTEM_PROMPT, user_prompt=build_prompt(view.spec_text),
                timeout_seconds=limits.timeout_seconds,
            )
            acceptance = parse_spec_acceptance(reply)
        except Exception as exc:  # noqa: BLE001 - one attempt, then a safe fallback
            # Verification must not depend on this call; keep the generic suite.
            self.acceptance_note = f"generic criteria (spec derivation failed: {type(exc).__name__}: {str(exc)[:200]})"
            return generic
        self._spec_acceptance = acceptance
        self.acceptance_note = f"{len(acceptance.requirements)} spec-derived requirements"
        return AcceptanceSuite(
            version=generic.version + "-spec",
            criteria=generic.criteria + criteria_from(acceptance),
        )

    async def next_check(
        self, view: AgentView, limits: CallLimits,
    ) -> ToolAction | VerificationReport:
        """Emit the next allowed verification tool action, or the final report."""
        self._ensure_cycle(view)
        if view.suite is None:
            raise RuntimeError("next_check called without a frozen AcceptanceSuite")

        # Ingest the observation from the previous controller-dispatched tool.
        if self._awaiting_result:
            self._ingest_last_result(view.last_result)
            self._awaiting_result = False

        if self._step == _Step.INSPECT:
            return self._emit(ToolKind.INSPECT_WORKSPACE, {}, "inspect workspace inventory")

        if self._step == _Step.READ:
            path = self._next_read_path()
            if path is not None:
                return self._emit(
                    ToolKind.READ_FILE,
                    {"path": path},
                    f"read game artifact {path}",
                )
            self._step = _Step.HARNESS

        if self._step == _Step.HARNESS:
            return self._emit_harness(view, limits)

        # REPORT
        return self._build_report(view)

    def _emit(self, kind: ToolKind, arguments: dict[str, Any], rationale: str) -> ToolAction:
        if kind not in {ToolKind.READ_FILE, ToolKind.INSPECT_WORKSPACE, ToolKind.RUN_COMMAND}:
            raise RuntimeError(f"Verification must not propose {kind}")
        action_id = self._new_action_id(kind.value)
        self._last_action_id = action_id
        self._last_kind = kind
        self._awaiting_result = True
        return ToolAction(action_id=action_id, kind=kind, arguments=arguments, rationale=rationale)

    def _ingest_last_result(self, result: ToolResult | None) -> None:
        if result is None or self._last_kind is None or self._last_action_id is None:
            # Missing observation: advance carefully without inventing PASS evidence.
            if self._step == _Step.INSPECT:
                self._step = _Step.HARNESS
            elif self._step == _Step.READ:
                self._step = _Step.HARNESS
            elif self._step == _Step.HARNESS:
                self._step = _Step.REPORT
            return

        if result.action_id != self._last_action_id:
            # The previous cycle was interrupted (failure -> recovery -> repair).
            # Start a fresh cycle instead of mixing old evidence into this one.
            key = self._cycle_key
            self._reset_cycle()
            self._cycle_key = key
            return

        self._ledger.ingest_result(result, self._last_kind, self._last_action_id)

        if self._last_kind == ToolKind.INSPECT_WORKSPACE:
            self._pending_reads = self._select_reads(result)
            self._step = _Step.READ if self._pending_reads else _Step.HARNESS
        elif self._last_kind == ToolKind.READ_FILE:
            if not self._pending_reads:
                self._step = _Step.HARNESS
        elif self._last_kind == ToolKind.RUN_COMMAND:
            self._step = _Step.REPORT

    def _select_reads(self, result: ToolResult) -> list[str]:
        """Read only files the inspection actually listed; a missing file would
        otherwise become a tool failure and abort the verification cycle."""
        listed = [p for p in self._ledger.files_seen if isinstance(p, str)]
        listed_set = set(listed)
        selected = [c for c in _CANDIDATE_FILES if c in listed_set and c not in self._reads_done]
        # Fall back to any top-level HTML/JS sources when names are unconventional.
        for path in listed:
            if len(selected) >= 4:
                break
            if path.endswith((".html", ".js")) and path not in selected and path not in self._reads_done:
                selected.append(path)
        return selected[:4]

    def _next_read_path(self) -> str | None:
        while self._pending_reads:
            path = self._pending_reads.pop(0)
            if path in self._reads_done:
                continue
            self._reads_done.add(path)
            return path
        return None

    def _emit_harness(self, view: AgentView, limits: CallLimits) -> ToolAction:
        if not view.launch_command:
            # Still run harness in "docs-only" mode so we produce a FAIL with evidence.
            launch: list[str] = []
        else:
            launch = list(view.launch_command)

        timeout = max(5, int(min(limits.timeout_seconds, 85)) - 3)
        argv = [
            sys.executable,
            str(self._harness_path),
            "--timeout-seconds",
            str(timeout),
        ]
        if self._spec_acceptance is not None:
            argv += ["--scenarios-json", json.dumps(harness_payload(self._spec_acceptance),
                                                    separators=(",", ":"))]
        if launch:
            argv.append("--launch")
            argv.extend(launch)

        self._step = _Step.HARNESS
        return self._emit(
            ToolKind.RUN_COMMAND,
            {"argv": argv},
            "bounded Playwright harness: start game, check playability, then shut down",
        )

    def _build_report(self, view: AgentView) -> VerificationReport:
        assert view.suite is not None
        results = evaluate_criteria(view.suite.criteria, self._ledger)
        revision = view.workspace_revision or "unknown"
        return VerificationReport(
            spec_digest=view.spec_digest,
            workspace_revision=revision,
            suite_version=view.suite.version,
            results=results,
        )
