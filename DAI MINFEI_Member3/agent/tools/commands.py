"""Command policy and presentation layer over the process runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .errors import RuntimeToolError
from .files import WorkspaceFiles
from .processes import ProcessResult, ProcessRunner


class CommandExecutor:
    def __init__(self, files: WorkspaceFiles, runner: ProcessRunner) -> None:
        self.files = files
        self.runner = runner

    async def execute(
        self,
        argv: Sequence[str],
        cwd_value: Any,
        *,
        timeout_seconds: float,
        action_id: str,
    ) -> ProcessResult:
        cwd = self.files.resolve(cwd_value or ".", allow_root=True)
        if not cwd.is_dir():
            raise RuntimeToolError("PATH_NOT_FOUND", f"Command cwd does not exist: {cwd_value or '.'}")
        return await self.runner.run(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            action_id=action_id,
        )


def process_data(result: ProcessResult) -> dict[str, object]:
    return {
        "exit_code": result.exit_code,
        "duration_seconds": round(result.duration_seconds, 6),
        "timed_out": result.timed_out,
        "cleanup_confirmed": result.cleanup_confirmed,
        "stdout": result.stdout_preview,
        "stderr": result.stderr_preview,
    }


def process_observation(result: ProcessResult) -> str:
    if result.timed_out:
        headline = f"Command timed out after {result.duration_seconds:.2f}s"
    else:
        headline = f"Command exited with code {result.exit_code} after {result.duration_seconds:.2f}s"
    sections = [headline]
    if result.stdout_preview:
        sections.append("stdout:\n" + result.stdout_preview)
    if result.stderr_preview:
        sections.append("stderr:\n" + result.stderr_preview)
    return "\n".join(sections)
