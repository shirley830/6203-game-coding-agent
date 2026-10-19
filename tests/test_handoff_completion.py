"""Model smoke test and best-version preservation without real API calls."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.config import AgentConfig
from agent.controller import Controller
from agent.preflight import probe_model, main as preflight_main, PreflightReport
from agent.state import (
    CheckStatus, DeliveryReport, Phase, RequestVerification, ToolAction, ToolKind,
    ToolResult, ToolStatus,
)
from agent.tools import LocalRuntime, RuntimeSettings
from agent.tools.errors import RuntimeToolError
from agent.tools.versions import GameVersions
from evaluation.rehearse import archive_run
from tests.test_controller import Scenario


class ModelProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_usable_json_one_call(self):
        client = type("Client", (), {"generate": AsyncMock(return_value='{"ready":true}')})()
        report = await probe_model(client)
        self.assertTrue(report.ok)
        client.generate.assert_awaited_once()

    async def test_empty_invalid_or_wrong_json_is_blocking(self):
        for text in ("", "not JSON", "{}", "[]", '{"ready":false}'):
            with self.subTest(text=text):
                client = type("Client", (), {"generate": AsyncMock(return_value=text)})()
                self.assertFalse((await probe_model(client)).ok)

    async def test_errors_never_print_credential_or_provider_payload(self):
        client = type("Client", (), {"generate": AsyncMock(side_effect=ValueError("secret-provider-key"))})()
        report = await probe_model(client)
        self.assertFalse(report.ok)
        self.assertNotIn("secret-provider-key", report.render())

    async def test_timeout_has_no_hidden_retry(self):
        async def slow(**kwargs):
            await asyncio.sleep(1)
        client = type("Client", (), {"generate": AsyncMock(side_effect=slow)})()
        report = await probe_model(client, timeout_seconds=0.01)
        self.assertFalse(report.ok)
        client.generate.assert_awaited_once()

    def test_default_preflight_makes_no_billed_call(self):
        with patch("agent.preflight.run_preflight", return_value=PreflightReport()), \
                patch("agent.envfile.load_env_file"), patch("builtins.print"), \
                patch("agent.preflight.probe_model", new=AsyncMock()) as probe:
            self.assertEqual(preflight_main([]), 0)
            probe.assert_not_awaited()


class SourceVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.game = self.root / "game"
        self.game.mkdir()
        self.trace = self.root / "traces/run"
        self.store = GameVersions(self.game, self.trace)

    def test_restore_files_deletions_empty_directories_modes_and_losing_source(self):
        (self.game / "main.py").write_text("old")
        (self.game / "main.py").chmod(0o755)
        (self.game / "empty").mkdir()
        version = self.store.save(timeout_seconds=5)
        (self.game / "main.py").write_text("broken")
        (self.game / "extra.txt").write_text("new")
        previous = self.store.restore(version, timeout_seconds=5)
        self.assertEqual((self.game / "main.py").read_text(), "old")
        self.assertTrue((self.game / "main.py").stat().st_mode & 0o100)
        self.assertTrue((self.game / "empty").is_dir())
        self.assertFalse((self.game / "extra.txt").exists())
        backup = self.trace / "game_versions" / previous / "source"
        self.assertEqual((backup / "main.py").read_text(), "broken")
        self.assertEqual((backup / "extra.txt").read_text(), "new")

    def test_empty_game_can_be_saved_and_restored(self):
        version = self.store.save(timeout_seconds=5)
        (self.game / "extra.txt").write_text("new")
        self.store.restore(version, timeout_seconds=5)
        self.assertEqual(list(self.game.iterdir()), [])

    def test_file_directory_shape_changes(self):
        (self.game / "a").mkdir()
        (self.game / "a/b").write_text("old")
        version = self.store.save(timeout_seconds=5)
        (self.game / "a/b").unlink()
        (self.game / "a").rmdir()
        (self.game / "a").write_text("changed")
        self.store.restore(version, timeout_seconds=5)
        self.assertEqual((self.game / "a/b").read_text(), "old")

    def test_symlink_source_is_rejected(self):
        (self.game / "escape").symlink_to(self.root / "elsewhere")
        with self.assertRaises(RuntimeToolError):
            self.store.save(timeout_seconds=5)

    def test_tampered_snapshot_cannot_modify_live_game(self):
        (self.game / "main.py").write_text("old")
        version = self.store.save(timeout_seconds=5)
        (self.game / "main.py").write_text("current")
        (self.trace / "game_versions" / version / "source/main.py").write_text("tampered")
        with self.assertRaises(RuntimeToolError):
            self.store.restore(version, timeout_seconds=5)
        self.assertEqual((self.game / "main.py").read_text(), "current")

    def test_limits_and_bad_id_do_not_modify_source(self):
        (self.game / "main.py").write_text("current")
        with self.assertRaises(RuntimeToolError):
            GameVersions(self.game, self.trace, max_bytes=1).save(timeout_seconds=5)
        with self.assertRaises(RuntimeToolError):
            self.store.restore("../outside", timeout_seconds=5)
        with self.assertRaises(RuntimeToolError):
            self.store.save(timeout_seconds=0)
        self.assertEqual((self.game / "main.py").read_text(), "current")

    def test_dependency_directory_is_retained_at_current_version(self):
        (self.game / "main.py").write_text("old")
        version = self.store.save(timeout_seconds=5)
        (self.game / "node_modules/pkg").mkdir(parents=True)
        (self.game / "node_modules/pkg/index.js").write_text("current-dependency")
        self.store.restore(version, timeout_seconds=5)
        self.assertEqual((self.game / "node_modules/pkg/index.js").read_text(), "current-dependency")


class EvidenceArchiveTests(unittest.TestCase):
    def test_private_settings_are_not_exported_and_game_is_unchanged(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game").mkdir()
            (root / "game/main.py").write_text("fixture")
            (root / "agent.env").write_text("private-token")
            result = archive_run(root, root / "evidence.zip", ["private-token"])
            self.assertEqual(result["file_count"], 1)
            with ZipFile(root / "evidence.zip") as archive:
                self.assertEqual(archive.namelist(), ["game/main.py"])
            self.assertEqual((root / "game/main.py").read_text(), "fixture")

    def test_leaked_key_or_symlink_prevents_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game").mkdir()
            (root / "game/main.py").write_text("private-token")
            with self.assertRaises(ValueError):
                archive_run(root, root / "evidence.zip", ["private-token"])
            self.assertFalse((root / "evidence.zip").exists())
            (root / "game/main.py").unlink()
            (root / "game/escape").symlink_to(root / "agent.env")
            with self.assertRaises(ValueError):
                archive_run(root, root / "evidence.zip", [])


class BestVersionControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.spec = self.root / "spec.md"
        self.spec.write_text("Fixture specification")
        self.scenario = Scenario()
        self.runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=False))
        # Reuse real runtime for source changes/snapshots; command observations
        # are fixtures. No generated game code is executed.
        original = self.runtime.execute
        async def execute(action, context, limits):
            if action.kind == ToolKind.RUN_COMMAND:
                return ToolResult(action.action_id, ToolStatus.OK, "fixture verification", self.runtime._workspace.revision())
            return await original(action, context, limits)
        self.runtime.execute = execute
        from agent.state import RecoveryDecision, RecoveryKind
        self.scenario.recoveries = [RecoveryDecision(RecoveryKind.REPAIR, "fixture repair")] * 3

    async def run_case(self, **config):
        deps = replace(self.scenario.dependencies(), runtime=self.runtime)
        return await Controller(AgentConfig(self.root, **config), deps).run(self.spec)

    async def test_regression_restores_better_partial_version_and_keeps_exhausted(self):
        self.scenario.decisions = [
            ToolAction("good", ToolKind.WRITE_FILE, {"path": "main.py", "content": "better"}),
            RequestVerification(("fixture",)),
            ToolAction("bad", ToolKind.WRITE_FILE, {"path": "main.py", "content": "worse"}),
            RequestVerification(("fixture",)),
        ]
        self.scenario.reports = [
            lambda report: replace(report, results=tuple(
                replace(r, status=CheckStatus.FAIL) if r.criterion_id == "interaction" else r
                for r in report.results)),
            lambda report: replace(report, results=tuple(replace(r, status=CheckStatus.FAIL)
                                                        for r in report.results)),
        ]
        summary = await self.run_case(max_recovery_attempts=1)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual((self.root / "game/main.py").read_text(), "better")
        self.assertEqual(sum(r.status == CheckStatus.PASS for r in summary.verification.results), 2)
        self.assertIn("restored best", summary.reason)
        self.assertTrue(any(e.kind == "game_version_restored" for e in self.scenario.events))
        self.assertTrue(any((self.root / "traces").glob("*/game_versions/*/source/main.py")))

    async def test_success_keeps_current_version_without_restore(self):
        self.scenario.decisions = [
            ToolAction("good", ToolKind.WRITE_FILE, {"path": "main.py", "content": "good"}),
            RequestVerification(("fixture",)),
        ]
        summary = await self.run_case()
        self.assertEqual(summary.outcome, Phase.SUCCEEDED)
        self.assertFalse(any(e.kind == "game_version_restored" for e in self.scenario.events))

    async def test_invalid_report_cannot_become_best(self):
        self.scenario.decisions = [RequestVerification(("fixture",))]
        self.scenario.reports = [lambda report: replace(report, workspace_revision="stale")]
        summary = await self.run_case(max_recovery_attempts=0)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertFalse(any(e.kind == "game_version_evaluated" for e in self.scenario.events))

    async def test_unknown_process_state_skips_restore(self):
        original = self.runtime.execute
        async def execute(action, context, limits):
            if action.action_id == "unknown":
                return ToolResult(action.action_id, ToolStatus.UNKNOWN, "cleanup unconfirmed", None,
                                  error_code="PROCESS_CLEANUP_FAILED", side_effects_unknown=True)
            return await original(action, context, limits)
        self.runtime.execute = execute
        self.scenario.decisions = [
            ToolAction("good", ToolKind.WRITE_FILE, {"path": "main.py", "content": "better"}),
            RequestVerification(("fixture",)),
            ToolAction("bad", ToolKind.WRITE_FILE, {"path": "main.py", "content": "worse"}),
            ToolAction("unknown", ToolKind.RUN_COMMAND, {"argv": ["fixture"]}),
        ]
        self.scenario.reports = [lambda report: replace(report, results=tuple(
            replace(r, status=CheckStatus.FAIL) if r.criterion_id == "interaction" else r
            for r in report.results))]
        summary = await self.run_case(max_recovery_attempts=1)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
        self.assertEqual((self.root / "game/main.py").read_text(), "worse")
        self.assertTrue(any(e.kind == "game_version_restore_skipped" for e in self.scenario.events))

    async def test_dependency_change_invalidates_restored_evidence(self):
        original = self.runtime.execute
        async def execute(action, context, limits):
            result = await original(action, context, limits)
            if action.action_id == "bad":
                self.runtime._storage.environment_record.write_text('{"changed":true}')
                result = replace(result, workspace_revision=self.runtime._workspace.revision())
            return result
        self.runtime.execute = execute
        self.scenario.decisions = [
            ToolAction("good", ToolKind.WRITE_FILE, {"path": "main.py", "content": "better"}),
            RequestVerification(("fixture",)),
            ToolAction("bad", ToolKind.WRITE_FILE, {"path": "main.py", "content": "worse"}),
            RequestVerification(("fixture",)),
        ]
        self.scenario.reports = [
            lambda report: replace(report, results=tuple(
                replace(r, status=CheckStatus.FAIL) if r.criterion_id == "interaction" else r
                for r in report.results)),
            lambda report: replace(report, results=tuple(replace(r, status=CheckStatus.FAIL)
                                                        for r in report.results)),
        ]
        summary = await self.run_case(max_recovery_attempts=1)
        self.assertEqual((self.root / "game/main.py").read_text(), "better")
        self.assertIsNone(summary.verification)
        self.assertEqual(summary.outcome, Phase.EXHAUSTED)
