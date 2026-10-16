"""Member 4 GameVerifier tests via root agent.verification (no live browser)."""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.state import (
    AgentView,
    CallLimits,
    CheckStatus,
    Phase,
    ToolAction,
    ToolKind,
    ToolResult,
    ToolStatus,
    VerificationReport,
)
from agent.verification import GameVerifier
from agent.verification.criteria import build_acceptance_suite

_REPO_ROOT = Path(__file__).resolve().parents[1]

SPEC = """# Collection game

Create a browser game using HTML, JavaScript and Canvas, playable without remote assets.
Move the player with arrow keys. Collect items to score points. Show a restart button.
"""


def _limits(timeout: float = 30.0) -> CallLimits:
    return CallLimits(timeout_seconds=timeout, steps_remaining=50, commands_remaining=20)


def _view(**overrides) -> AgentView:
    suite = build_acceptance_suite(SPEC)
    base = dict(
        run_id="test-run",
        phase=Phase.VERIFYING,
        spec_text=SPEC,
        spec_digest="abc123",
        workspace_revision="rev1",
        suite=suite,
        plan=None,
        plan_version=1,
        launch_command=("python", "-m", "http.server", "8765"),
        last_result=None,
        last_failure=None,
        steps_used=3,
        commands_used=0,
        recovery_attempts=0,
        elapsed_seconds=1.0,
    )
    base.update(overrides)
    return AgentView(**base)


class CriteriaTests(unittest.TestCase):
    def test_mandatory_categories_present(self):
        suite = build_acceptance_suite(SPEC)
        cats = {c.category for c in suite.criteria if c.mandatory}
        self.assertTrue({"launch", "functional", "interaction"} <= cats)
        self.assertTrue(any(c.category == "technical" for c in suite.criteria))

    def test_empty_spec_rejected(self):
        with self.assertRaises(ValueError):
            build_acceptance_suite("   ")


class VerifierFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.verifier = GameVerifier()

    async def test_prepare_acceptance_freezes_suite(self):
        suite = await self.verifier.prepare_acceptance(_view(phase=Phase.INITIALIZING), _limits())
        self.assertTrue(suite.version.startswith("member4-"))
        self.assertGreaterEqual(len(suite.criteria), 3)

    async def test_first_check_is_inspect(self):
        action = await self.verifier.next_check(_view(), _limits())
        self.assertIsInstance(action, ToolAction)
        assert isinstance(action, ToolAction)
        self.assertEqual(action.kind, ToolKind.INSPECT_WORKSPACE)

    async def test_only_allowed_tool_kinds(self):
        verifier = GameVerifier()
        view = _view()
        action = await verifier.next_check(view, _limits())
        assert isinstance(action, ToolAction)
        view = _view(
            last_result=ToolResult(
                action.action_id,
                ToolStatus.OK,
                "files: index.html README.md main.js",
                "rev1",
                data={"files": ["index.html", "README.md", "main.js"]},
            )
        )
        kinds: list[ToolKind] = []
        for _ in range(6):
            result = await verifier.next_check(view, _limits())
            if isinstance(result, VerificationReport):
                break
            assert isinstance(result, ToolAction)
            self.assertIn(
                result.kind,
                {ToolKind.READ_FILE, ToolKind.INSPECT_WORKSPACE, ToolKind.RUN_COMMAND},
            )
            kinds.append(result.kind)
            observation = "ok"
            data: dict = {}
            if result.kind == ToolKind.RUN_COMMAND:
                observation = (
                    'HARNESS_JSON={"ok":true,"checks":{'
                    '"launch":{"status":"pass","detail":"up","evidence_refs":["url:x"]},'
                    '"functional":{"status":"pass","detail":"ui","evidence_refs":["ui:x"]},'
                    '"interaction":{"status":"pass","detail":"moved","state_changed":true,'
                    '"evidence_refs":["change:canvas"]},'
                    '"technical":{"status":"pass","detail":"canvas","evidence_refs":["dom:canvas"]}'
                    "}}"
                )
            elif result.kind == ToolKind.READ_FILE:
                data = {
                    "text": "<canvas></canvas>\nwindow.addEventListener('keydown', () => score++)\n"
                }
                observation = "read"
            view = _view(
                last_result=ToolResult(
                    result.action_id, ToolStatus.OK, observation, "rev1", data=data,
                )
            )
        self.assertIn(ToolKind.RUN_COMMAND, kinds)

    async def test_report_has_non_empty_evidence_and_all_criteria(self):
        verifier = GameVerifier()
        view = _view()
        action = await verifier.next_check(view, _limits())
        assert isinstance(action, ToolAction)
        view = _view(
            last_result=ToolResult(
                action.action_id, ToolStatus.OK, "index.html", "rev1",
                data={"files": ["index.html"]},
            )
        )
        report = None
        for _ in range(8):
            result = await verifier.next_check(view, _limits())
            if isinstance(result, VerificationReport):
                report = result
                break
            assert isinstance(result, ToolAction)
            obs = "read file"
            data: dict = {"text": "canvas keydown score restart"}
            if result.kind == ToolKind.RUN_COMMAND:
                obs = (
                    'HARNESS_JSON={"ok":true,"checks":{'
                    '"launch":{"status":"pass","detail":"loaded","evidence_refs":["url:http://x"]},'
                    '"functional":{"status":"pass","detail":"score ui","evidence_refs":["ui:score"]},'
                    '"interaction":{"status":"pass","detail":"pixels","state_changed":true,'
                    '"evidence_refs":["change:canvas_pixels_changed"]},'
                    '"technical":{"status":"pass","detail":"canvas","evidence_refs":["dom:canvas:1"]}'
                    "}}"
                )
                data = {}
            view = _view(
                last_result=ToolResult(result.action_id, ToolStatus.OK, obs, "rev1", data=data)
            )

        self.assertIsInstance(report, VerificationReport)
        assert report is not None
        self.assertEqual(report.spec_digest, "abc123")
        self.assertEqual(report.workspace_revision, "rev1")
        assert view.suite is not None
        self.assertEqual(len(report.results), len(view.suite.criteria))
        for item in report.results:
            self.assertTrue(item.evidence_refs)
            self.assertTrue(all(isinstance(r, str) and r.strip() for r in item.evidence_refs))
        passed = {r.criterion_id: r for r in report.results}
        self.assertEqual(passed["C-LAUNCH"].status, CheckStatus.PASS)
        self.assertEqual(passed["C-INTERACTION"].status, CheckStatus.PASS)

    async def test_interaction_page_open_alone_fails(self):
        verifier = GameVerifier()
        view = _view()
        action = await verifier.next_check(view, _limits())
        assert isinstance(action, ToolAction)
        view = _view(
            last_result=ToolResult(
                action.action_id, ToolStatus.OK, "index.html", "rev1",
                data={"files": ["index.html"]},
            )
        )
        report = None
        for _ in range(8):
            result = await verifier.next_check(view, _limits())
            if isinstance(result, VerificationReport):
                report = result
                break
            assert isinstance(result, ToolAction)
            obs = "ok"
            data: dict = {"text": "<html></html>"}
            if result.kind == ToolKind.RUN_COMMAND:
                obs = (
                    'HARNESS_JSON={"ok":false,"checks":{'
                    '"launch":{"status":"pass","detail":"loaded","evidence_refs":["url:x"]},'
                    '"functional":{"status":"fail","detail":"no ui","evidence_refs":["ui:none"]},'
                    '"interaction":{"status":"fail","detail":"no change","state_changed":false,'
                    '"evidence_refs":["input:attempted"]},'
                    '"technical":{"status":"fail","detail":"no canvas","evidence_refs":["dom:none"]}'
                    "}}"
                )
                data = {}
            view = _view(
                last_result=ToolResult(result.action_id, ToolStatus.OK, obs, "rev1", data=data)
            )
        self.assertIsNotNone(report)
        assert report is not None
        by_id = {r.criterion_id: r for r in report.results}
        self.assertEqual(by_id["C-INTERACTION"].status, CheckStatus.FAIL)


class HarnessScriptSmokeTests(unittest.TestCase):
    def test_integrated_harness_file_exists(self):
        path = _REPO_ROOT / "agent" / "verification" / "playwright_game_check.py"
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
