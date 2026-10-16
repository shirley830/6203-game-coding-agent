"""Integration regressions for bounded process-group cleanup and result gating."""
import asyncio
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.state import CallLimits, ExecutionContext, ToolAction, ToolKind, ToolStatus
from agent.tools import LocalRuntime, RuntimeSettings
from agent.tools.processes import ProcessResult


@unittest.skipUnless(os.name == "posix", "POSIX descendant cleanup regression")
class ProcessCleanupRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def probe(
        self, timeout: bool = False, exit_code: int = 0,
        cancel: bool = False, strict: bool = False,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "spec.md"
            spec.write_text("# Process cleanup fixture", encoding="utf-8")
            context = ExecutionContext(
                "cleanup", root, spec, root / "game", root / "traces" / "cleanup"
            )
            runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=strict))
            limits = CallLimits(5, 100, 100)
            await runtime.execute(
                ToolAction("prepare", ToolKind.PREPARE_WORKSPACE, {}), context, limits
            )
            child = (
                "import os,signal,time,pathlib; "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "pathlib.Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
            )
            parent = (
                "import subprocess,sys,pathlib,time; "
                f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                "\nwhile not pathlib.Path('child.pid').exists(): time.sleep(.01)\n"
                + ("time.sleep(30)" if timeout or cancel else f"raise SystemExit({exit_code})")
            )
            pid = None
            task = None
            try:
                task = asyncio.create_task(runtime.execute(
                    ToolAction("probe", ToolKind.RUN_COMMAND, {
                        "argv": [sys.executable, "-c", parent],
                    }), context, CallLimits(.5 if timeout else 5, 100, 100)
                ))
                if cancel:
                    async with asyncio.timeout(3):
                        while not (context.game_dir / "child.pid").exists():
                            if task.done():
                                self.fail(f"Command ended before child startup: {task.result()}")
                            await asyncio.sleep(.01)
                    task.cancel()
                    await asyncio.sleep(.05)
                    task.cancel()  # A second cancellation must not abandon cleanup.
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 3)
                    result = None
                else:
                    result = await asyncio.wait_for(task, 4)
                    self.assertTrue(result.data["cleanup_confirmed"], result.observation)
                    expected = ToolStatus.UNKNOWN if timeout else ToolStatus.OK if exit_code == 0 else ToolStatus.ERROR
                    self.assertEqual(result.status, expected)
                    if exit_code:
                        self.assertEqual(result.data["exit_code"], exit_code)
                pid = int((context.game_dir / "child.pid").read_text())
                await asyncio.sleep(.05)
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                self.assertFalse(
                    alive,
                    f"Child survives command completion/cancellation: {result}",
                )
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if pid is None and (context.game_dir / "child.pid").exists():
                    pid = int((context.game_dir / "child.pid").read_text())
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    async def test_normal_exit_cleans_background_child(self):
        await self.probe(timeout=False)

    async def test_timeout_kills_child_ignoring_term(self):
        await self.probe(timeout=True)

    async def test_nonzero_exit_cleans_background_child(self):
        await self.probe(exit_code=7)

    async def test_repeated_cancellation_waits_for_cleanup(self):
        await self.probe(cancel=True)

    @unittest.skipUnless(sys.platform == "darwin", "Strict macOS sandbox rehearsal")
    async def test_strict_macos_sandbox_cleans_descendants(self):
        if not Path("/usr/bin/sandbox-exec").is_file():
            self.skipTest("sandbox-exec unavailable")
        await self.probe(strict=True)
        await self.probe(timeout=True, strict=True)


class CleanupResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_failure_blocks_command_and_installation_success(self):
        for kind in (ToolKind.RUN_COMMAND, ToolKind.INSTALL_DEPENDENCIES):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec = root / "spec.md"
                spec.write_text("# Cleanup result fixture")
                context = ExecutionContext("gate", root, spec, root / "game", root / "traces/gate")
                runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=False))
                limits = CallLimits(5, 100, 100)
                await runtime.execute(ToolAction("prepare", ToolKind.PREPARE_WORKSPACE, {}), context, limits)
                uncertain = ProcessResult(0, .01, "", "", (), cleanup_confirmed=False)
                with patch("agent.tools.processes.ProcessRunner.run", new=AsyncMock(return_value=uncertain)) as run:
                    result = await runtime.execute(ToolAction("uncertain", kind, {
                        "argv": [sys.executable, "-c", "print('ok')"],
                    } if kind == ToolKind.RUN_COMMAND else {"packages": ["fixture"]}), context, limits)
                self.assertEqual(result.status, ToolStatus.UNKNOWN)
                self.assertEqual(result.error_code, "PROCESS_CLEANUP_FAILED")
                self.assertTrue(result.side_effects_unknown)
                self.assertIsNone(result.workspace_revision)
                self.assertIn("cleanup could not be confirmed", result.observation)
                self.assertEqual(run.await_count, 1)  # Uncertain venv setup must not start pip.

    async def test_install_cleanup_failure_is_not_recorded_as_success(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "spec.md"
            spec.write_text("# Dependency cleanup fixture")
            context = ExecutionContext("npm-gate", root, spec, root / "game", root / "traces/npm-gate")
            runtime = LocalRuntime(RuntimeSettings(strict_os_sandbox=False))
            limits = CallLimits(5, 100, 100)
            await runtime.execute(ToolAction("prepare", ToolKind.PREPARE_WORKSPACE, {}), context, limits)
            (context.game_dir / "package.json").write_text('{"name":"fixture","version":"1.0.0"}')
            uncertain = ProcessResult(0, .01, "", "", (), cleanup_confirmed=False)
            with patch("agent.tools.processes.ProcessRunner.run", new=AsyncMock(return_value=uncertain)):
                result = await runtime.execute(ToolAction("install", ToolKind.INSTALL_DEPENDENCIES, {"packages": ["fixture"]}), context, limits)
            record = json.loads((root / ".agent-runtime/npm-gate/environment.json").read_text())
            self.assertEqual(result.error_code, "PROCESS_CLEANUP_FAILED")
            self.assertEqual(record["last_status"], "unknown")
            self.assertEqual(record["installed_packages"], [])
