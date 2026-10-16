"""Durable JSONL tracing for the PE6203 coding agent (Member 6).

Implements the TracePort protocol defined in agent/controller.py.
The Controller owns event sequencing, state transitions, and completion.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from agent.state import (
    DeliveryReport,
    ExecutionContext,
    RunSummary,
    TraceEvent,
    to_jsonable,
)


MAX_COPIED_OUTPUT_BYTES = 20 * 1024 * 1024


class FileTraceRecorder:
    """Persist events, checkpoints, tool outputs and a run summary in traces/<run_id>/.

    Layout::

        events.jsonl          every controller event, by seq (durable on return)
        checkpoint.json       latest controller snapshot
        checkpoints.jsonl     every checkpoint, appended (development boundaries)
        outputs/              copies of runtime output_refs (logs, screenshots)
        outputs/index.jsonl   original ref -> copied file, with sha256
        verification/         one JSON file per VerificationReport
        summary.json          RunSummary written by finalize()
        run_report.md         human-readable timeline written by finalize()
    """

    def __init__(self) -> None:
        self._context: ExecutionContext | None = None
        self._next_seq = 1
        self._lock = asyncio.Lock()
        self._copied: list[Path] = []

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
            record = to_jsonable(event)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            await asyncio.to_thread(self._append_line, self._trace_dir() / "events.jsonl", line)
            self._next_seq += 1
            await asyncio.to_thread(self._persist_side_files, record)

    def _persist_side_files(self, record: dict[str, Any]) -> None:
        """Copy referenced outputs and verification reports into traces/<run_id>/."""
        payload = record.get("payload") or {}
        if record.get("kind") == "action_result":
            result = payload.get("result") or {}
            refs = result.get("output_refs") or []
            if refs:
                self._copy_output_refs(record["seq"], result.get("action_id", ""), refs)
        elif record.get("kind") == "module_result" and payload.get("operation") == "verifier.next_check":
            result = payload.get("result")
            if isinstance(result, dict) and "results" in result:
                directory = self._trace_dir() / "verification"
                directory.mkdir(exist_ok=True)
                self._write_json_atomically(
                    directory / f"report-{record['seq']:05d}.json", result)

    def _copy_output_refs(self, seq: int, action_id: str, refs: list[Any]) -> None:
        context = self._context
        assert context is not None
        # Only runtime-owned storage is copied; arbitrary paths are never read.
        allowed = (context.run_root / ".agent-runtime" / context.run_id).resolve()
        outputs = self._trace_dir() / "outputs"
        outputs.mkdir(exist_ok=True)
        index_lines: list[str] = []
        for ref in refs:
            entry: dict[str, Any] = {"seq": seq, "action_id": action_id, "ref": ref}
            source = Path(str(ref))
            try:
                resolved = source.resolve(strict=True)
                if not resolved.is_relative_to(allowed) or source.is_symlink() or not resolved.is_file():
                    entry["skipped"] = "outside runtime storage or not a regular file"
                elif resolved.stat().st_size > MAX_COPIED_OUTPUT_BYTES:
                    entry["skipped"] = "too large"
                else:
                    target = outputs / f"{seq:05d}-{resolved.parent.name}-{resolved.name}"[-200:]
                    shutil.copyfile(resolved, target)
                    entry["copy"] = target.relative_to(self._trace_dir()).as_posix()
                    entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
                    self._copied.append(target)
            except OSError as exc:
                entry["skipped"] = f"{type(exc).__name__}: {exc}"
            index_lines.append(json.dumps(entry, ensure_ascii=False))
        for line in index_lines:
            self._append_line_create(outputs / "index.jsonl", line)

    @classmethod
    def _append_line_create(cls, path: Path, line: str) -> None:
        if not path.exists():
            path.touch(mode=0o644)
        cls._append_line(path, line)

    async def save_checkpoint(self, snapshot: Mapping[str, Any]) -> None:
        """Keep the latest serializable controller snapshot (not automatic resume)."""
        async with self._lock:
            await asyncio.to_thread(
                self._write_json_atomically,
                self._trace_dir() / "checkpoint.json",
                snapshot,
            )
            line = json.dumps(to_jsonable(snapshot), ensure_ascii=False, separators=(",", ":"))
            await asyncio.to_thread(
                self._append_line_create, self._trace_dir() / "checkpoints.jsonl", line)

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
            await asyncio.to_thread(self._write_run_report, summary)
            problems = await asyncio.to_thread(self._verify_preserved, summary)
            if problems:
                # preserved=True must be real: report the failure instead.
                raise RuntimeError("Trace preservation check failed: " + "; ".join(problems))
            # Do NOT close the recorder: Controller emits the terminal event next.
            return DeliveryReport(
                preserved=True, trace_dir=context.trace_dir,
                detail=f"{self._next_seq - 1} events, {len(self._copied)} copied outputs",
            )

    def _verify_preserved(self, summary: RunSummary) -> list[str]:
        """Re-read what finalize claims to have preserved."""
        problems: list[str] = []
        root = self._trace_dir()
        try:
            saved = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            if saved != to_jsonable(summary):
                problems.append("summary.json does not match the run summary")
        except (OSError, ValueError) as exc:
            problems.append(f"summary.json unreadable: {exc}")
        try:
            lines = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            seqs = [json.loads(line)["seq"] for line in lines]
            if seqs != list(range(1, self._next_seq)):
                problems.append("events.jsonl is not the contiguous sequence that was acknowledged")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"events.jsonl unreadable: {exc}")
        missing = [p.name for p in self._copied if not p.is_file()]
        if missing:
            problems.append(f"copied outputs missing: {missing[:5]}")
        return problems

    def _write_run_report(self, summary: RunSummary) -> None:
        """Human-readable timeline so someone who missed the live run can follow it."""
        from agent.tracing.report import render_run_report

        events_path = self._trace_dir() / "events.jsonl"
        events = [json.loads(line) for line in
                  events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        text = render_run_report(events, to_jsonable(summary))
        temporary = self._trace_dir() / ".run_report.md.tmp"
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self._trace_dir() / "run_report.md")
