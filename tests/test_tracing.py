"""Member 6 tests: durable traces, delivery, and missing-terminal detection."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from agent.delivery import audit_delivery
from agent.state import ExecutionContext, Phase, RunSummary, TraceEvent
from agent.tracing.recorder import FileTraceRecorder


class TestFileTraceRecorder(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.context = ExecutionContext(
            run_id="test-run-001",
            run_root=self.root,
            spec_path=self.root / "game_design_prompt.md",
            game_dir=self.root / "game",
            trace_dir=self.root / "traces" / "test-run-001",
        )
        self.tracer = FileTraceRecorder()
        await self.tracer.open_run(self.context)

    def event(self, seq, kind="action_start", payload=None):
        return TraceEvent(
            run_id=self.context.run_id, seq=seq,
            timestamp="2026-10-12T00:00:00+00:00", kind=kind,
            phase=Phase.EXECUTING, payload=payload or {"step": seq},
        )

    def summary(self):
        return RunSummary(
            run_id=self.context.run_id, outcome=Phase.SUCCEEDED, reason="verified",
            game_dir=self.context.game_dir, trace_dir=self.context.trace_dir,
            launch_command=("python", "main.py"), verification=None,
            steps_used=2, commands_used=1, recovery_attempts=0,
            elapsed_seconds=1.5,
        )

    async def test_creates_durable_sequenced_events(self):
        await self.tracer.append_event(self.event(1, "run_start"))
        await self.tracer.append_event(self.event(2))
        path = self.context.trace_dir / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([x["seq"] for x in events], [1, 2])
        self.assertEqual(events[1]["kind"], "action_start")

    async def test_rejects_duplicate_and_out_of_order_events(self):
        await self.tracer.append_event(self.event(1))
        with self.assertRaises(ValueError):
            await self.tracer.append_event(self.event(1))
        with self.assertRaises(ValueError):
            await self.tracer.append_event(self.event(3))

    async def test_checkpoint_replaces_old_checkpoint(self):
        await self.tracer.save_checkpoint({"phase": "planning", "steps_used": 1})
        await self.tracer.save_checkpoint({"phase": "verifying", "steps_used": 2})
        saved = json.loads((self.context.trace_dir / "checkpoint.json").read_text())
        self.assertEqual(saved["phase"], "verifying")
        self.assertEqual(saved["steps_used"], 2)

    async def test_finalize_preserves_summary_and_allows_terminal_event(self):
        await self.tracer.append_event(self.event(1, "run_start"))
        report = await self.tracer.finalize(self.summary())
        self.assertTrue(report.preserved)
        self.assertEqual(report.trace_dir, self.context.trace_dir)
        await self.tracer.append_event(self.event(2, "transition", {"from": "finalizing", "to": "succeeded"}))
        audit = audit_delivery(self.context.trace_dir)
        self.assertTrue(audit["complete"], audit["problems"])
        self.assertEqual(audit["event_count"], 2)

    async def test_audit_catches_missing_terminal_event(self):
        await self.tracer.append_event(self.event(1, "run_start"))
        await self.tracer.finalize(self.summary())
        audit = audit_delivery(self.context.trace_dir)
        self.assertFalse(audit["complete"])
        self.assertIn("Missing final terminal transition event", audit["problems"])

    async def test_audit_catches_mismatched_outcome(self):
        await self.tracer.append_event(self.event(1, "run_start"))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "failed"}))
        audit = audit_delivery(self.context.trace_dir)
        self.assertFalse(audit["complete"])
        self.assertIn("Summary outcome differs from final event", audit["problems"])

    async def test_existing_run_is_not_overwritten(self):
        another = FileTraceRecorder()
        with self.assertRaises(FileExistsError):
            await another.open_run(self.context)

    async def test_wrong_run_id_is_rejected(self):
        event = TraceEvent("wrong-run", 1, "2026-10-12T00:00:00Z", "run_start", Phase.INITIALIZING, {})
        with self.assertRaises(ValueError):
            await self.tracer.append_event(event)


if __name__ == "__main__":
    unittest.main()
