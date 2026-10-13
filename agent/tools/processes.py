"""Bounded subprocess execution with OS sandboxing and process-group cleanup."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import shutil
import signal
import time
from typing import Mapping, Sequence

from .errors import RuntimeToolError


SHELL_EXECUTABLES = frozenset({
    "bash", "cmd", "cmd.exe", "dash", "fish", "ksh", "powershell",
    "powershell.exe", "pwsh", "sh", "zsh",
})
PYTHON_PACKAGE_EXECUTABLES = frozenset({"pip", "pip3", "pipx", "poetry", "uv"})
NODE_PACKAGE_EXECUTABLES = frozenset({"npm", "npm.cmd", "pnpm", "yarn"})


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    duration_seconds: float
    stdout_preview: str
    stderr_preview: str
    output_refs: tuple[str, ...]
    timed_out: bool = False
    cleanup_confirmed: bool = True


class RunStorage:
    """Runtime-owned local storage, replaceable by Member 6 during integration."""

    def __init__(
        self, run_root: Path, run_id: str, *, output_directory: Path | None = None
    ) -> None:
        if (
            not isinstance(run_id, str) or not run_id
            or "/" in run_id or "\\" in run_id or "\x00" in run_id
            or Path(run_id).name != run_id or run_id in {".", ".."}
        ):
            raise RuntimeToolError("RUNTIME_STORAGE_UNSAFE", "run_id is not path-safe")
        lexical_run_root = Path(os.path.abspath(run_root))
        self.run_root = lexical_run_root.resolve()
        support_parent = self.run_root / ".agent-runtime"
        if support_parent.is_symlink():
            raise RuntimeToolError("RUNTIME_STORAGE_UNSAFE", ".agent-runtime must not be a symlink")
        support_parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(support_parent, 0o700)
        self.root = support_parent / run_id
        if self.root.is_symlink():
            raise RuntimeToolError("RUNTIME_STORAGE_UNSAFE", "Run storage must not be a symlink")
        if output_directory is not None:
            lexical_output = Path(os.path.abspath(output_directory))
            lexical_trace_root = lexical_run_root / "traces" / run_id
            if not lexical_output.is_relative_to(lexical_trace_root):
                raise RuntimeToolError(
                    "RUNTIME_STORAGE_UNSAFE",
                    "Injected output directory must be inside traces/<run-id>/",
                )
            current = lexical_run_root
            for part in lexical_output.relative_to(lexical_run_root).parts:
                current = current / part
                if current.is_symlink():
                    raise RuntimeToolError(
                        "RUNTIME_STORAGE_UNSAFE",
                        "Injected output directory contains a symbolic link",
                    )
            requested_output = lexical_output.resolve(strict=False)
            allowed_trace_root = lexical_trace_root.resolve(strict=False)
            if not requested_output.is_relative_to(allowed_trace_root):
                raise RuntimeToolError(
                    "RUNTIME_STORAGE_UNSAFE",
                    "Injected output directory resolves outside traces/<run-id>/",
                )
            self.outputs_dir = requested_output
        else:
            self.outputs_dir = self.root / "outputs"
        if self.outputs_dir.is_symlink():
            raise RuntimeToolError("RUNTIME_STORAGE_UNSAFE", "Output directory must not be a symlink")
        self.environment_dir = self.root / "environment"
        self.home_dir = self.root / "home"
        self.temp_dir = self.root / "tmp"
        for directory in (
            self.outputs_dir, self.environment_dir, self.home_dir, self.temp_dir
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        self.environment_record = self.root / "environment.json"
        self._output_counts: dict[tuple[str, str], int] = {}

    @staticmethod
    def _safe_action_id(action_id: str) -> str:
        normalized = "".join(c if c.isalnum() or c in "-_" else "_" for c in action_id)
        fingerprint = hashlib.sha256(action_id.encode("utf-8")).hexdigest()[:12]
        return f"{normalized[:60] or 'action'}-{fingerprint}"

    def output_paths(self, action_id: str, label: str = "command") -> tuple[Path, Path]:
        key = (action_id, label)
        attempt = self._output_counts.get(key, 0) + 1
        self._output_counts[key] = attempt
        stem = f"{self._safe_action_id(action_id)}.{label}.attempt-{attempt}"
        return self.outputs_dir / f"{stem}.stdout.log", self.outputs_dir / f"{stem}.stderr.log"


class OSSandbox:
    """Build an OS-level sandbox prefix or fail closed."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.system = platform.system()

    @property
    def available(self) -> bool:
        if not self.enabled:
            return True
        if self.system == "Darwin":
            return Path("/usr/bin/sandbox-exec").is_file()
        if self.system == "Linux":
            return shutil.which("bwrap") is not None
        return False

    @staticmethod
    def _profile_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')

    def prefix(self, game_dir: Path, storage: RunStorage, cwd: Path) -> list[str]:
        if not self.enabled:
            return []
        if not self.available:
            raise RuntimeToolError(
                "SANDBOX_UNAVAILABLE",
                f"No supported strict command sandbox is available on {self.system}",
            )
        game = game_dir.resolve()
        runtime = storage.root.resolve()
        outputs = storage.outputs_dir.resolve()
        if self.system == "Darwin":
            output_rule = (
                "" if outputs.is_relative_to(runtime)
                else f'(allow file-write* (subpath "{self._profile_path(outputs)}"))\n'
            )
            profile = (
                '(version 1)\n'
                '(allow default)\n'
                '(deny file-write*)\n'
                f'(allow file-write* (subpath "{self._profile_path(game)}"))\n'
                f'(allow file-write* (subpath "{self._profile_path(runtime)}"))\n'
                f'{output_rule}'
            )
            return ["/usr/bin/sandbox-exec", "-p", profile]
        bwrap = shutil.which("bwrap")
        assert bwrap is not None
        prefix = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--ro-bind", "/", "/",
            "--bind", str(game), str(game),
            "--bind", str(runtime), str(runtime),
            "--proc", "/proc",
            "--dev-bind", "/dev", "/dev",
            "--tmpfs", "/tmp",
            "--share-net",
            "--chdir", str(cwd.resolve()),
        ]
        if not outputs.is_relative_to(runtime):
            prefix.extend(["--bind", str(outputs), str(outputs)])
        return prefix


class ProcessRunner:
    """Execute one argument vector with bounded output and reliable cleanup."""

    def __init__(
        self,
        game_dir: Path,
        storage: RunStorage,
        *,
        sandbox: OSSandbox,
        preview_bytes: int,
        extra_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.game_dir = Path(game_dir)
        self.storage = storage
        self.sandbox = sandbox
        self.preview_bytes = preview_bytes
        self.extra_environment = dict(extra_environment or {})

    def environment(self) -> dict[str, str]:
        allowed_names = (
            "DISPLAY", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMROOT",
            "TERM", "WAYLAND_DISPLAY", "WINDIR", "XDG_RUNTIME_DIR",
        )
        environment = {name: os.environ[name] for name in allowed_names if name in os.environ}
        environment.update(self.extra_environment)
        environment.update({
            "HOME": str(self.storage.home_dir),
            "TMPDIR": str(self.storage.temp_dir),
            "PIP_CACHE_DIR": str(self.storage.root / "pip-cache"),
            "npm_config_cache": str(self.storage.root / "npm-cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(self.storage.root / "pycache"),
        })
        virtual_environment = self.storage.environment_dir / "python"
        bin_directory = virtual_environment / ("Scripts" if os.name == "nt" else "bin")
        if bin_directory.is_dir():
            environment["VIRTUAL_ENV"] = str(virtual_environment)
            environment["PATH"] = str(bin_directory) + os.pathsep + environment.get("PATH", "")
        node_bin = self.game_dir / "node_modules" / ".bin"
        if node_bin.is_dir():
            environment["PATH"] = str(node_bin) + os.pathsep + environment.get("PATH", "")
        return environment

    @staticmethod
    def _validate_command(argv: Sequence[str], *, dependency_command: bool) -> None:
        if not argv or any(not isinstance(item, str) or not item.strip() or "\x00" in item for item in argv):
            raise RuntimeToolError("COMMAND_INVALID", "argv must contain non-empty strings")
        executable = Path(argv[0]).name.lower()
        if executable in SHELL_EXECUTABLES:
            raise RuntimeToolError("SHELL_NOT_ALLOWED", "Shell interpreters are not allowed")
        if dependency_command:
            return
        if executable in PYTHON_PACKAGE_EXECUTABLES:
            raise RuntimeToolError(
                "DEPENDENCY_BYPASS", "Install packages through install_dependencies"
            )
        if executable.startswith("python") and len(argv) >= 3 and tuple(argv[1:3]) == ("-m", "pip"):
            raise RuntimeToolError(
                "DEPENDENCY_BYPASS", "Install packages through install_dependencies"
            )
        if executable in NODE_PACKAGE_EXECUTABLES and len(argv) >= 2 and argv[1] in {
            "add", "ci", "install", "i"
        }:
            raise RuntimeToolError(
                "DEPENDENCY_BYPASS", "Install packages through install_dependencies"
            )

    @staticmethod
    def _preview(path: Path, limit: int) -> str:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size <= limit:
                payload = stream.read()
                suffix = ""
            else:
                half = max(limit // 2, 1)
                beginning = stream.read(half)
                stream.seek(max(size - half, 0))
                ending = stream.read(half)
                payload = beginning + b"\n... output truncated ...\n" + ending
                suffix = f"\n[full output: {size} bytes]"
        return payload.decode("utf-8", errors="replace") + suffix

    @staticmethod
    def _group_exists(group_id: int) -> bool:
        """Check the owned group, even after its original leader has exited."""
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return False
        # Permission failures propagate; they cannot establish successful cleanup.
        return True

    @classmethod
    async def _terminate_group(cls, process: asyncio.subprocess.Process) -> bool:
        """Bound TERM/KILL cleanup and confirm the entire POSIX group is gone.

        start_new_session makes the launched process PID its initial group ID.
        Detached processes that create another session require stronger isolation.
        """
        if os.name != "posix":
            # No Windows process-tree adapter exists. Do not claim tree cleanup.
            if process.returncode is None:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=0.75)
                except (OSError, asyncio.TimeoutError):
                    pass
            return False
        group_id = process.pid
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(group_id, sig)
                except ProcessLookupError:
                    # The group can disappear before asyncio delivers the exit
                    # callback. Reap the leader without treating that race as a
                    # cleanup failure.
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.75)
                    except asyncio.TimeoutError:
                        return False
                    return True
                deadline = time.monotonic() + 0.75
                while time.monotonic() < deadline:
                    if not cls._group_exists(group_id) and process.returncode is not None:
                        return True
                    await asyncio.sleep(0.02)
            return not cls._group_exists(group_id) and process.returncode is not None
        except OSError:
            return False

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        action_id: str,
        label: str = "command",
        dependency_command: bool = False,
    ) -> ProcessResult:
        self._validate_command(argv, dependency_command=dependency_command)
        environment = self.environment()
        if "/" in argv[0]:
            requested = Path(argv[0])
            resolved_executable = (
                requested.resolve(strict=False)
                if requested.is_absolute() else (cwd / requested).resolve(strict=False)
            )
            if resolved_executable.name.lower() in SHELL_EXECUTABLES:
                raise RuntimeToolError("SHELL_NOT_ALLOWED", "Shell interpreters are not allowed")
        elif shutil.which(argv[0], path=environment.get("PATH")) is None:
            raise RuntimeToolError("COMMAND_NOT_FOUND", f"Executable not found: {argv[0]}")
        prefix = self.sandbox.prefix(self.game_dir, self.storage, cwd)
        stdout_path, stderr_path = self.storage.output_paths(action_id, label)
        started = time.monotonic()
        process: asyncio.subprocess.Process | None = None
        timed_out = False
        cleanup_confirmed = True
        cancelled = False
        with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
            os.chmod(stdout_path, 0o600)
            os.chmod(stderr_path, 0o600)
            try:
                process = await asyncio.create_subprocess_exec(
                    *prefix,
                    *argv,
                    cwd=cwd,
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    start_new_session=True,
                )
                try:
                    await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    timed_out = True
                except asyncio.CancelledError:
                    cancelled = True
            except FileNotFoundError as exc:
                raise RuntimeToolError("COMMAND_NOT_FOUND", f"Executable not found: {argv[0]}") from exc
            finally:
                if process is not None:
                    # Cleanup also runs for success/nonzero exit. Retain and await
                    # the task so repeated cancellation cannot orphan cleanup.
                    cleanup_task = asyncio.create_task(self._terminate_group(process))
                    while not cleanup_task.done():
                        try:
                            await asyncio.shield(cleanup_task)
                        except asyncio.CancelledError:
                            cancelled = True
                    cleanup_confirmed = cleanup_task.result()
            if cancelled:
                raise asyncio.CancelledError
        duration = time.monotonic() - started
        result = ProcessResult(
            exit_code=None if timed_out else process.returncode if process is not None else None,
            duration_seconds=duration,
            stdout_preview=self._preview(stdout_path, self.preview_bytes),
            stderr_preview=self._preview(stderr_path, self.preview_bytes),
            output_refs=(str(stdout_path), str(stderr_path)),
            timed_out=timed_out,
            cleanup_confirmed=cleanup_confirmed,
        )
        sandbox_error = result.stderr_preview.lower()
        if prefix and result.exit_code not in (None, 0) and (
            "sandbox_apply" in sandbox_error
            or "bwrap:" in sandbox_error and (
                "operation not permitted" in sandbox_error
                or "permission denied" in sandbox_error
                or "user namespaces" in sandbox_error
            )
        ):
            raise RuntimeToolError(
                "SANDBOX_UNAVAILABLE",
                "The OS sandbox exists but cannot be activated in this environment",
                data={"stderr": result.stderr_preview},
            )
        return result
