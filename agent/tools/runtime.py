"""Production implementation of ``RuntimePort`` for local execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Callable, Mapping

from agent.state import (
    CallLimits,
    ExecutionContext,
    ToolAction,
    ToolKind,
    ToolResult,
    ToolStatus,
)

from .commands import CommandExecutor, process_data, process_observation
from .dependencies import DependencyInstaller
from .errors import RuntimeToolError
from .files import WorkspaceFiles
from .processes import OSSandbox, ProcessRunner, RunStorage


@dataclass(frozen=True)
class RuntimeSettings:
    """Local runtime policy that does not alter controller-owned budgets."""

    strict_os_sandbox: bool = True
    max_file_read_bytes: int = 256 * 1024
    max_workspace_entries: int = 2_000
    output_preview_bytes: int = 8 * 1024
    max_spec_bytes: int = 1024 * 1024
    injected_environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "max_file_read_bytes", "max_workspace_entries", "output_preview_bytes",
            "max_spec_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            for key, value in self.injected_environment.items()
        ):
            raise ValueError("injected_environment must map non-empty strings to strings")


class LocalRuntime:
    """Execute the eight controller-authorized tools without mutating controller state."""

    _REQUIRED = {
        ToolKind.PREPARE_WORKSPACE: frozenset(),
        ToolKind.READ_SPEC: frozenset(),
        ToolKind.INSPECT_WORKSPACE: frozenset(),
        ToolKind.READ_FILE: frozenset({"path"}),
        ToolKind.WRITE_FILE: frozenset({"path", "content"}),
        ToolKind.PATCH_FILE: frozenset({"path", "patch"}),
        ToolKind.RUN_COMMAND: frozenset({"argv"}),
        ToolKind.INSTALL_DEPENDENCIES: frozenset({"packages"}),
    }

    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        output_directory_factory: Callable[[ExecutionContext], Path] | None = None,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self.output_directory_factory = output_directory_factory
        self._run_id: str | None = None
        self._storage: RunStorage | None = None
        self._files: WorkspaceFiles | None = None
        self._commands: CommandExecutor | None = None
        self._dependencies: DependencyInstaller | None = None

    @staticmethod
    def _validate_context(context: ExecutionContext) -> None:
        root = Path(context.run_root).resolve()
        game = Path(context.game_dir).resolve(strict=False)
        if game != root / "game":
            raise RuntimeToolError("CONTEXT_INVALID", "context.game_dir must be run_root/game")
        trace = Path(context.trace_dir).resolve(strict=False)
        traces_root = root / "traces"
        if not trace.is_relative_to(traces_root):
            raise RuntimeToolError("CONTEXT_INVALID", "context.trace_dir must be inside run_root/traces")

    @classmethod
    def _validate_action(cls, action: ToolAction) -> None:
        if not isinstance(action, ToolAction) or not isinstance(action.kind, ToolKind):
            raise RuntimeToolError("ACTION_INVALID", "Runtime requires a typed ToolAction")
        if not isinstance(action.action_id, str) or not action.action_id.strip():
            raise RuntimeToolError("ACTION_INVALID", "action_id must not be empty")
        required = cls._REQUIRED[action.kind]
        allowed = required | ({"cwd"} if action.kind == ToolKind.RUN_COMMAND else set())
        keys = set(action.arguments)
        if not required <= keys or not keys <= allowed:
            raise RuntimeToolError(
                "ACTION_INVALID", "Tool arguments are missing or contain undeclared fields"
            )

    def _bind(self, context: ExecutionContext) -> None:
        self._validate_context(context)
        if self._run_id is not None and self._run_id != context.run_id:
            raise RuntimeToolError(
                "RUNTIME_REUSE_NOT_ALLOWED", "Create a new LocalRuntime for each controller run"
            )
        if self._run_id is not None:
            return
        self._run_id = context.run_id
        output_directory = (
            self.output_directory_factory(context)
            if self.output_directory_factory is not None else None
        )
        self._storage = RunStorage(
            context.run_root, context.run_id, output_directory=output_directory
        )
        self._files = WorkspaceFiles(
            context.game_dir,
            self._storage.environment_record,
            max_read_bytes=self.settings.max_file_read_bytes,
        )
        runner = ProcessRunner(
            context.game_dir,
            self._storage,
            sandbox=OSSandbox(enabled=self.settings.strict_os_sandbox),
            preview_bytes=self.settings.output_preview_bytes,
            extra_environment=self.settings.injected_environment,
        )
        self._commands = CommandExecutor(self._files, runner)
        self._dependencies = DependencyInstaller(context.game_dir, self._storage, runner)

    @property
    def _workspace(self) -> WorkspaceFiles:
        assert self._files is not None
        return self._files

    def _revision_or_none(self) -> str | None:
        try:
            return self._workspace.revision()
        except Exception:
            return None

    @staticmethod
    def _result(
        action: ToolAction,
        status: ToolStatus,
        observation: str,
        revision: str | None,
        *,
        data: Mapping[str, object] | None = None,
        error_code: str | None = None,
        side_effects_unknown: bool = False,
        output_refs: tuple[str, ...] = (),
    ) -> ToolResult:
        return ToolResult(
            action_id=action.action_id,
            status=status,
            observation=observation,
            workspace_revision=revision,
            data=data or {},
            error_code=error_code,
            side_effects_unknown=side_effects_unknown,
            output_refs=output_refs,
        )

    async def execute(
        self,
        action: ToolAction,
        context: ExecutionContext,
        limits: CallLimits,
    ) -> ToolResult:
        """Execute exactly one controller-authorized tool action."""

        try:
            self._validate_action(action)
            self._bind(context)
            if limits.timeout_seconds <= 0:
                raise RuntimeToolError("TIMEOUT_INVALID", "Tool timeout must be positive")
            args = action.arguments
            if action.kind == ToolKind.PREPARE_WORKSPACE:
                self._workspace.prepare()
                return self._result(
                    action, ToolStatus.OK, "Created an empty game workspace",
                    self._workspace.revision(), data={"game_dir": str(context.game_dir)},
                )
            if action.kind == ToolKind.READ_SPEC:
                spec_path = Path(context.spec_path)
                if spec_path.is_symlink() or not spec_path.is_file():
                    raise RuntimeToolError("SPEC_NOT_FOUND", "Specification must be a regular file")
                if spec_path.stat().st_size > self.settings.max_spec_bytes:
                    raise RuntimeToolError("SPEC_TOO_LARGE", "Specification exceeds the read limit")
                try:
                    text = spec_path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeToolError("INVALID_ENCODING", "Specification is not valid UTF-8") from exc
                return self._result(
                    action, ToolStatus.OK, f"Read {len(text.encode('utf-8'))} specification bytes",
                    self._workspace.revision(), data={"text": text},
                )
            if action.kind == ToolKind.INSPECT_WORKSPACE:
                data = self._workspace.inspect(max_entries=self.settings.max_workspace_entries)
                revision = self._workspace.revision()
                data["workspace_revision"] = revision
                return self._result(
                    action, ToolStatus.OK,
                    f"Inspected {data['entry_count']} workspace entries",
                    revision, data=data,
                )
            if action.kind == ToolKind.READ_FILE:
                text = self._workspace.read_text(args["path"])
                return self._result(
                    action, ToolStatus.OK,
                    f"Read {len(text.encode('utf-8'))} bytes from {args['path']}",
                    self._workspace.revision(), data={"path": args["path"], "text": text},
                )
            if action.kind == ToolKind.WRITE_FILE:
                written = self._workspace.write_text(args["path"], args["content"])
                return self._result(
                    action, ToolStatus.OK, f"Wrote {written} bytes to {args['path']}",
                    self._workspace.revision(), data={"path": args["path"], "bytes": written},
                )
            if action.kind == ToolKind.PATCH_FILE:
                written = self._workspace.patch_text(args["path"], args["patch"])
                return self._result(
                    action, ToolStatus.OK, f"Patched {args['path']} ({written} bytes)",
                    self._workspace.revision(), data={"path": args["path"], "bytes": written},
                )
            if action.kind == ToolKind.RUN_COMMAND:
                assert self._commands is not None
                result = await self._commands.execute(
                    args["argv"], args.get("cwd"), timeout_seconds=limits.timeout_seconds,
                    action_id=action.action_id,
                )
                revision = self._workspace.revision() if result.cleanup_confirmed else None
                observation = process_observation(result)
                data = process_data(result)
                if result.timed_out or not result.cleanup_confirmed:
                    code = "COMMAND_TIMEOUT" if result.cleanup_confirmed else "PROCESS_CLEANUP_FAILED"
                    return self._result(
                        action, ToolStatus.UNKNOWN, observation, revision, data=data,
                        error_code=code, side_effects_unknown=True,
                        output_refs=result.output_refs,
                    )
                status = ToolStatus.OK if result.exit_code == 0 else ToolStatus.ERROR
                return self._result(
                    action, status, observation, revision, data=data,
                    error_code=None if status == ToolStatus.OK else "NONZERO_EXIT",
                    output_refs=result.output_refs,
                )
            if action.kind == ToolKind.INSTALL_DEPENDENCIES:
                assert self._dependencies is not None
                started = time.monotonic()
                installation = await self._dependencies.install(
                    args["packages"], timeout_seconds=limits.timeout_seconds,
                    action_id=action.action_id,
                )
                final = installation.final
                cleanup_confirmed = all(step.cleanup_confirmed for step in installation.steps)
                revision = self._workspace.revision() if cleanup_confirmed else None
                data = {
                    "manager": installation.manager,
                    "packages": list(args["packages"]),
                    "duration_seconds": round(time.monotonic() - started, 6),
                    "steps": [process_data(step) for step in installation.steps],
                }
                observation = f"Dependency manager: {installation.manager}\n" + process_observation(final)
                if final.timed_out or not cleanup_confirmed:
                    return self._result(
                        action, ToolStatus.UNKNOWN, observation, revision, data=data,
                        error_code="DEPENDENCY_TIMEOUT" if cleanup_confirmed else "PROCESS_CLEANUP_FAILED",
                        side_effects_unknown=True,
                        output_refs=installation.output_refs,
                    )
                status = ToolStatus.OK if final.exit_code == 0 else ToolStatus.ERROR
                return self._result(
                    action, status, observation, revision, data=data,
                    error_code=None if status == ToolStatus.OK else "DEPENDENCY_INSTALL_FAILED",
                    output_refs=installation.output_refs,
                )
            raise RuntimeToolError("ACTION_INVALID", f"Unsupported tool kind: {action.kind}")
        except RuntimeToolError as exc:
            revision = self._revision_or_none() if self._files is not None else None
            status = ToolStatus.UNKNOWN if exc.side_effects_unknown else ToolStatus.ERROR
            return self._result(
                action, status, exc.message, revision, data=exc.data,
                error_code=exc.code, side_effects_unknown=exc.side_effects_unknown,
            )
    async def _version_operation(self, context, limits, version=None):
        """Controller-only infrastructure; cooperative cancellation awaits cleanup."""
        import asyncio
        from .versions import GameVersions
        self._bind(context)
        store = GameVersions(context.game_dir, context.trace_dir)
        def perform():
            if version is None:
                before = self._workspace.revision()
                snapshot = store.save(timeout_seconds=limits.timeout_seconds)
                after = self._workspace.revision()
                if before != after:
                    raise RuntimeToolError("VERSION_UNSAFE", "Workspace changed while saving version")
                return {"snapshot_id": snapshot, "workspace_revision": after}
            previous = store.restore(version, timeout_seconds=limits.timeout_seconds)
            return {"snapshot_id": version, "previous_snapshot_id": previous,
                    "workspace_revision": self._workspace.revision()}
        task = asyncio.create_task(asyncio.to_thread(perform))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            # Retrieve exceptions before propagating cancellation; no hidden worker
            # may keep changing game/ after the controller believes it stopped.
            try:
                task.result()
            finally:
                raise asyncio.CancelledError
        return task.result()

    async def create_game_snapshot(self, context, limits):
        return await self._version_operation(context, limits)

    async def restore_game_snapshot(self, snapshot_id, context, limits):
        return await self._version_operation(context, limits, snapshot_id)
