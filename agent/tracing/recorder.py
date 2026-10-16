"""Durable JSONL tracing for the PE6203 coding agent (Member 6).

Implements the TracePort protocol defined in agent/controller.py.
The Controller owns event sequencing, state transitions, and completion.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import hashlib
import stat
from pathlib import Path
import tempfile
from typing import Any, Mapping

from agent.state import (
    DeliveryReport,
    ExecutionContext,
    RunSummary,
    TraceEvent,
    to_jsonable,
)


class FileTraceRecorder:
    """Persist events, the latest checkpoint, and a run summary in traces/<run_id>/."""

    def __init__(self) -> None:
        self._context: ExecutionContext | None = None
        self._next_seq = 1
        self._lock = asyncio.Lock()

    def _trace_dir(self) -> Path:
        if self._context is None:
            raise RuntimeError("Call open_run() before writing a trace")
        return self._context.trace_dir

    @staticmethod
    def _create_run_directory(context: ExecutionContext) -> None:
        # Only the controller may choose the run directory. Never overwrite a run.
        expected = context.run_root / "traces" / context.run_id
        if context.trace_dir.absolute() != expected.absolute():
            raise ValueError("Unexpected trace directory")
        if (context.run_root / "traces").is_symlink():
            raise ValueError("traces/ may not be a symbolic link")
        context.trace_dir.mkdir(parents=True, exist_ok=False)
        path = context.trace_dir / "events.jsonl"
        with path.open("x", encoding="utf-8") as stream:
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            payload = (line + "\n").encode("utf-8")
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError("Could not persist trace event")
                payload = payload[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_json_atomically(path: Path, content: Any) -> None:
        """Replace a JSON snapshot only after its replacement is safely written."""
        data = (json.dumps(to_jsonable(content), ensure_ascii=False, indent=2)
                + "\n").encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".member6-", suffix=".tmp",
                dir=path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            # Preserve the directory rename on platforms that support fsync(dir).
            if os.name == "posix":
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    try:
                        os.fsync(dir_fd)
                    except OSError as exc:
                        # Some filesystems (notably on macOS) reject directory fsync.
                        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
                            raise
                finally:
                    os.close(dir_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _archive_game_screenshots(context: ExecutionContext, seq: int) -> None:
        """Keep per-action copies of real verifier screenshots in traces/.

        Member 4 writes screenshots to game/.verification-evidence/. Do not
        rename or alter those source files. Copy them after action results so
        a later verification cycle cannot overwrite earlier evidence.
        """
        source = context.game_dir / ".verification-evidence"
        if source.is_symlink():
            raise ValueError("Verification evidence directory cannot be a symlink")
        if not source.exists():
            return
        if not source.is_dir():
            raise ValueError("Verification evidence path is not a directory")

        evidence = []
        # Member 4 uses flat screenshots; refuse symlinks and ignore other
        # generated files. Limit file size so logs cannot fill the disk.
        for path in sorted(source.iterdir()):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            if path.is_symlink():
                raise ValueError("Verification screenshot cannot be a symlink")
            if not path.is_file():
                continue
            source_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                source_flags |= os.O_NOFOLLOW
            fd = os.open(path, source_flags)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Verification screenshot is not a regular file")
                if info.st_size > 15 * 1024 * 1024:
                    raise ValueError("Verification screenshot exceeds 15 MiB")
                with os.fdopen(fd, "rb", closefd=False) as reader:
                    payload = reader.read(15 * 1024 * 1024 + 1)
                if len(payload) > 15 * 1024 * 1024:
                    raise ValueError("Verification screenshot exceeds 15 MiB")
            finally:
                os.close(fd)
            evidence.append((path.name, payload))

        if not evidence:
            return
        relative_folder = Path("evidence") / f"seq-{seq:06d}"
        target = context.trace_dir / relative_folder
        target.mkdir(parents=True, exist_ok=False)
        index_records = []
        for filename, data in evidence:
            destination = target / filename
            with destination.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            index_records.append({
                "event_seq": seq,
                "source_ref": f"screenshot:{filename}",
                "archived_path": (relative_folder / filename).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        index_path = context.trace_dir / "evidence" / "index.jsonl"
        index_path.touch(exist_ok=True)
        for record in index_records:
            FileTraceRecorder._append_line(index_path, json.dumps(record, separators=(",", ":")))

    async def open_run(self, context: ExecutionContext) -> None:
        """Create a fresh run directory; never replace an earlier run."""
        async with self._lock:
            if self._context is not None:
                raise RuntimeError("This recorder is already attached to a run")
            await asyncio.to_thread(self._create_run_directory, context)
            self._context = context
            self._next_seq = 1

    async def append_event(self, event: TraceEvent) -> None:
        """Persist one sequenced event before acknowledging the Controller."""
        async with self._lock:
            context = self._context
            if context is None:
                raise RuntimeError("Run not opened")
            if event.run_id != context.run_id:
                raise ValueError("Event belongs to a different run")
            if event.seq != self._next_seq:
                raise ValueError(f"Expected event seq={self._next_seq}, got {event.seq}")
            # Capture screenshots while they still represent this action.
            if event.kind == "action_result":
                await asyncio.to_thread(self._archive_game_screenshots, context, event.seq)
            line = json.dumps(to_jsonable(event), ensure_ascii=False, separators=(",", ":"))
            await asyncio.to_thread(self._append_line, self._trace_dir() / "events.jsonl", line)
            self._next_seq += 1

    async def save_checkpoint(self, snapshot: Mapping[str, Any]) -> None:
        """Keep the latest serializable controller snapshot (not automatic resume)."""
        async with self._lock:
            await asyncio.to_thread(
                self._write_json_atomically,
                self._trace_dir() / "checkpoint.json",
                snapshot,
            )

    async def finalize(self, summary: RunSummary) -> DeliveryReport:
        """Write summary.json; the Controller will append a final event afterwards."""
        async with self._lock:
            context = self._context
            if context is None:
                raise RuntimeError("Run not opened")
            if summary.run_id != context.run_id:
                raise ValueError("Summary belongs to a different run")
            if summary.trace_dir.absolute() != context.trace_dir.absolute():
                raise ValueError("Summary trace directory does not match the run")
            events = self._trace_dir() / "events.jsonl"
            if not events.is_file() or events.stat().st_size == 0:
                raise RuntimeError("Cannot finalize without persisted events")
            await asyncio.to_thread(
                self._write_json_atomically,
                self._trace_dir() / "summary.json",
                summary,
            )
            # Do NOT close the recorder: Controller emits the terminal event next.
            return DeliveryReport(preserved=True, trace_dir=context.trace_dir)
