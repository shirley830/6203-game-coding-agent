"""Member 3 runtime tests using real filesystem and subprocess operations."""

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from agent.config import AgentConfig
from agent.controller import Controller
from agent.state import (
    CallLimits, CheckStatus, CriterionResult, ExecutionContext, Phase,
    RequestVerification, ToolAction, ToolKind, ToolStatus, VerificationReport,
)
from agent.tools import LocalRuntime, RuntimeSettings
from agent.tools.files import apply_unified_diff
from agent.tools.processes import OSSandbox, RunStorage
from agent.tools.revision import workspace_revision
from tests.test_controller import Scenario


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = self.root / "game_design_prompt.md"
        self.spec.write_text("# Runtime fixture\nBuild a tiny game.", encoding="utf-8")
        self.context = ExecutionContext(
            "runtime-test", self.root, self.spec, self.root / "game",
            self.root / "traces" / "runtime-test",
        )
        self.runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=False))
        self.limits = CallLimits(5.0, 100, 100)

    async def call(self, action_id, kind, arguments=None):
        return await self.runtime.execute(
            ToolAction(action_id, kind, arguments or {}), self.context, self.limits
        )

    async def prepare(self):
        result = await self.call("prepare", ToolKind.PREPARE_WORKSPACE)
        self.assertEqual(result.status, ToolStatus.OK)
        return result

    async def test_prepare_and_read_spec(self):
        prepared = await self.prepare()
        self.assertTrue(prepared.workspace_revision)
        result = await self.call("spec", ToolKind.READ_SPEC)
        self.assertEqual(result.status, ToolStatus.OK)
        self.assertEqual(result.data["text"], self.spec.read_text(encoding="utf-8"))
        self.assertEqual(result.workspace_revision, prepared.workspace_revision)

    async def test_prepare_refuses_nonempty_workspace(self):
        self.context.game_dir.mkdir()
        (self.context.game_dir / "keep.txt").write_text("keep", encoding="utf-8")
        result = await self.call("prepare", ToolKind.PREPARE_WORKSPACE)
        self.assertEqual(result.status, ToolStatus.ERROR)
        self.assertEqual(result.error_code, "WORKSPACE_NOT_EMPTY")
        self.assertEqual((self.context.game_dir / "keep.txt").read_text(), "keep")

    async def test_write_read_patch_and_inspect(self):
        initial = await self.prepare()
        written = await self.call("write", ToolKind.WRITE_FILE, {
            "path": "src/main.py", "content": "print('old')\n",
        })
        self.assertEqual(written.status, ToolStatus.OK)
        self.assertNotEqual(written.workspace_revision, initial.workspace_revision)
        read = await self.call("read", ToolKind.READ_FILE, {"path": "src/main.py"})
        self.assertEqual(read.data["text"], "print('old')\n")
        patch = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('old')\n+print('new')\n"
        patched = await self.call("patch", ToolKind.PATCH_FILE, {
            "path": "src/main.py", "patch": patch,
        })
        self.assertEqual(patched.status, ToolStatus.OK)
        self.assertEqual((self.context.game_dir / "src/main.py").read_text(), "print('new')\n")
        inspected = await self.call("inspect", ToolKind.INSPECT_WORKSPACE)
        paths = {entry["path"] for entry in inspected.data["entries"]}
        self.assertIn("src/main.py", paths)

    async def test_patch_conflict_is_atomic(self):
        await self.prepare()
        target = self.context.game_dir / "main.py"
        target.write_text("original\n", encoding="utf-8")
        result = await self.call("patch", ToolKind.PATCH_FILE, {
            "path": "main.py",
            "patch": "@@ -1 +1 @@\n-not-original\n+changed\n",
        })
        self.assertEqual(result.status, ToolStatus.ERROR)
        self.assertEqual(result.error_code, "PATCH_CONFLICT")
        self.assertEqual(target.read_text(), "original\n")

    def test_unified_diff_rejects_partial_hunk_counts(self):
        with self.assertRaisesRegex(Exception, "counts"):
            apply_unified_diff("a\n", "@@ -1,2 +1 @@\n-a\n+b\n")

    async def test_runtime_independently_rejects_traversal_and_symlink(self):
        await self.prepare()
        traversal = await self.call("escape", ToolKind.WRITE_FILE, {
            "path": "../outside.txt", "content": "no",
        })
        self.assertEqual(traversal.error_code, "PATH_OUTSIDE_WORKSPACE")
        outside = self.root / "outside"
        outside.mkdir()
        (self.context.game_dir / "link").symlink_to(outside, target_is_directory=True)
        symlink = await self.call("symlink", ToolKind.WRITE_FILE, {
            "path": "link/file.txt", "content": "no",
        })
        self.assertIn(symlink.error_code, {"SYMLINK_NOT_ALLOWED", "SYMLINK_ESCAPE"})
        self.assertFalse((outside / "file.txt").exists())

    async def test_command_success_returns_full_output_references(self):
        await self.prepare()
        result = await self.call("command", ToolKind.RUN_COMMAND, {
            "argv": [sys.executable, "-c", "print('runtime-ok')"],
        })
        self.assertEqual(result.status, ToolStatus.OK)
        self.assertEqual(result.data["exit_code"], 0)
        self.assertIn("runtime-ok", result.data["stdout"])
        self.assertEqual(len(result.output_refs), 2)
        self.assertTrue(all(Path(reference).is_file() for reference in result.output_refs))

    async def test_command_nonzero_and_timeout_are_distinct(self):
        await self.prepare()
        failed = await self.call("failed", ToolKind.RUN_COMMAND, {
            "argv": [sys.executable, "-c", "raise SystemExit(7)"],
        })
        self.assertEqual(failed.status, ToolStatus.ERROR)
        self.assertEqual(failed.error_code, "NONZERO_EXIT")
        self.assertEqual(failed.data["exit_code"], 7)
        short_limits = CallLimits(0.1, 100, 100)
        timed_out = await self.runtime.execute(ToolAction(
            "timeout", ToolKind.RUN_COMMAND,
            {"argv": [sys.executable, "-c", "import time; time.sleep(10)"]},
        ), self.context, short_limits)
        self.assertEqual(timed_out.status, ToolStatus.UNKNOWN)
        self.assertEqual(timed_out.error_code, "COMMAND_TIMEOUT")
        self.assertTrue(timed_out.side_effects_unknown)
        self.assertTrue(timed_out.data["cleanup_confirmed"])

    async def test_shell_and_dependency_bypass_are_rejected(self):
        await self.prepare()
        shell = await self.call("shell", ToolKind.RUN_COMMAND, {
            "argv": ["sh", "-c", "echo unsafe"],
        })
        self.assertEqual(shell.error_code, "SHELL_NOT_ALLOWED")
        pip = await self.call("pip", ToolKind.RUN_COMMAND, {
            "argv": [sys.executable, "-m", "pip", "install", "example"],
        })
        self.assertEqual(pip.error_code, "DEPENDENCY_BYPASS")

    async def test_python_dependency_install_uses_isolated_environment(self):
        await self.prepare()
        wheel = self.context.game_dir / "fixture_pkg-0.0.1-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("fixture_pkg/__init__.py", "VALUE = 'installed'\n")
            archive.writestr(
                "fixture_pkg-0.0.1.dist-info/METADATA",
                "Metadata-Version: 2.1\nName: fixture-pkg\nVersion: 0.0.1\n",
            )
            archive.writestr(
                "fixture_pkg-0.0.1.dist-info/WHEEL",
                "Wheel-Version: 1.0\nGenerator: member3-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr("fixture_pkg-0.0.1.dist-info/RECORD", "")
        before_install = workspace_revision(
            self.context.game_dir,
            self.root / ".agent-runtime" / "runtime-test" / "environment.json",
        )
        installed = await self.call("install", ToolKind.INSTALL_DEPENDENCIES, {
            "packages": ["./fixture_pkg-0.0.1-py3-none-any.whl"],
        })
        self.assertEqual(installed.status, ToolStatus.OK, installed.observation)
        self.assertEqual(installed.data["manager"], "pip")
        self.assertNotEqual(installed.workspace_revision, before_install)
        imported = await self.call("import", ToolKind.RUN_COMMAND, {
            "argv": ["python", "-c", "import fixture_pkg; print(fixture_pkg.VALUE)"],
        })
        self.assertEqual(imported.status, ToolStatus.OK, imported.observation)
        self.assertIn("installed", imported.data["stdout"])

    async def test_member6_can_inject_trace_owned_output_directory(self):
        runtime = LocalRuntime(
            RuntimeSettings(strict_os_sandbox=False),
            output_directory_factory=lambda context: context.trace_dir / "raw",
        )
        prepared = await runtime.execute(
            ToolAction("prepare", ToolKind.PREPARE_WORKSPACE), self.context, self.limits
        )
        self.assertEqual(prepared.status, ToolStatus.OK)
        result = await runtime.execute(ToolAction(
            "command", ToolKind.RUN_COMMAND,
            {"argv": [sys.executable, "-c", "print('trace-output')"]},
        ), self.context, self.limits)
        self.assertEqual(result.status, ToolStatus.OK)
        trace_root = self.context.trace_dir.resolve()
        self.assertTrue(all(Path(ref).resolve().is_relative_to(trace_root) for ref in result.output_refs))

    async def test_runtime_cannot_be_reused_for_another_run(self):
        await self.prepare()
        other = replace(self.context, run_id="other")
        result = await self.runtime.execute(
            ToolAction("inspect", ToolKind.INSPECT_WORKSPACE), other, self.limits
        )
        self.assertEqual(result.error_code, "RUNTIME_REUSE_NOT_ALLOWED")

    def test_revision_ignores_python_cache_but_tracks_executable_bit(self):
        game = self.context.game_dir
        game.mkdir()
        storage = RunStorage(self.root, "revision-test")
        script = game / "run.py"
        script.write_text("print('x')\n", encoding="utf-8")
        first = workspace_revision(game, storage.environment_record)
        cache = game / "__pycache__"
        cache.mkdir()
        (cache / "run.pyc").write_bytes(b"cache")
        self.assertEqual(first, workspace_revision(game, storage.environment_record))
        script.chmod(0o755)
        self.assertNotEqual(first, workspace_revision(game, storage.environment_record))

    def test_macos_sandbox_profile_is_write_scoped(self):
        storage = RunStorage(self.root, "sandbox-test")
        game = self.root / "game"
        game.mkdir()
        sandbox = OSSandbox(enabled=True)
        if sandbox.system != "Darwin" or not sandbox.available:
            self.skipTest("macOS sandbox profile test")
        prefix = sandbox.prefix(game, storage, game)
        profile = prefix[2]
        self.assertIn("(deny file-write*)", profile)
        self.assertIn(str(game.resolve()), profile)
        self.assertIn(str(storage.root.resolve()), profile)


class RuntimeControllerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_controller_dispatches_real_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "game_design_prompt.md"
            spec.write_text("# Integration fixture", encoding="utf-8")
            scenario = Scenario()
            scenario.decisions = [ToolAction(
                "write-real", ToolKind.WRITE_FILE,
                {"path": "main.py", "content": "print('playable fixture')\n"},
            ), RequestVerification((sys.executable, "main.py"))]
            check_requested = False

            async def real_check(view, limits):
                nonlocal check_requested
                if not check_requested:
                    check_requested = True
                    return ToolAction("check-real", ToolKind.RUN_COMMAND, {
                        "argv": [sys.executable, "main.py"],
                    })
                return VerificationReport(
                    view.spec_digest,
                    view.workspace_revision,
                    view.suite.version,
                    tuple(CriterionResult(
                        criterion.criterion_id, CheckStatus.PASS, ("check-real",)
                    ) for criterion in view.suite.criteria),
                )

            scenario.next_check = real_check
            dependencies = replace(
                scenario.dependencies(),
                runtime=LocalRuntime(RuntimeSettings(strict_os_sandbox=False)),
            )
            summary = await Controller(AgentConfig(root), dependencies).run(spec)
            self.assertEqual(summary.outcome, Phase.SUCCEEDED)
            self.assertTrue((root / "game" / "main.py").is_file())
            self.assertEqual(summary.commands_used, 1)


if __name__ == "__main__":
    unittest.main()
