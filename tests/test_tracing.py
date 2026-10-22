"""Member 6 tests: durable traces, delivery, and missing-terminal detection."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agent.delivery import audit_delivery
from agent.state import (
    CheckStatus, CriterionResult, ExecutionContext, Phase, RunSummary,
    TraceEvent, VerificationReport,
)
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

    async def test_verification_screenshots_are_archived_per_action(self):
        screenshots = self.context.game_dir / ".verification-evidence"
        screenshots.mkdir(parents=True)
        (screenshots / "after_load.png").write_bytes(b"png-first")
        await self.tracer.append_event(self.event(1, "action_result"))
        (screenshots / "after_load.png").write_bytes(b"png-second")
        (screenshots / "after_input.png").write_bytes(b"interaction-proof")
        await self.tracer.append_event(self.event(2, "action_result"))
        archive = self.context.trace_dir / "evidence"
        self.assertEqual((archive / "seq-000001/after_load.png").read_bytes(), b"png-first")
        self.assertEqual((archive / "seq-000002/after_load.png").read_bytes(), b"png-second")
        self.assertEqual((archive / "seq-000002/after_input.png").read_bytes(), b"interaction-proof")
        manifest = [json.loads(line) for line in (archive / "index.jsonl").read_text().splitlines()]
        self.assertEqual(len(manifest), 3)
        self.assertEqual(manifest[0]["source_ref"], "screenshot:after_load.png")
        self.assertEqual(len(manifest[0]["sha256"]), 64)

    async def test_audit_detects_tampered_archived_screenshot(self):
        screenshots = self.context.game_dir / ".verification-evidence"
        screenshots.mkdir(parents=True)
        (screenshots / "after_load.png").write_bytes(b"original")
        await self.tracer.append_event(self.event(1, "action_result"))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        archive = self.context.trace_dir / "evidence" / "seq-000001" / "after_load.png"
        archive.write_bytes(b"tampered")
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("hash mismatch" in x for x in report["problems"]))

    async def test_audit_detects_missing_evidence_manifest(self):
        screenshots = self.context.game_dir / ".verification-evidence"
        screenshots.mkdir(parents=True)
        (screenshots / "after_load.png").write_bytes(b"picture")
        await self.tracer.append_event(self.event(1, "action_result"))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        (self.context.trace_dir / "evidence" / "index.jsonl").unlink()
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("evidence/index.jsonl" in p for p in report["problems"]))

    async def test_absent_screenshots_do_not_prevent_events(self):
        await self.tracer.append_event(self.event(1, "action_result"))
        self.assertFalse((self.context.trace_dir / "evidence").exists())

    async def test_refuses_verification_symlink(self):
        destination = self.root / "outside"
        destination.mkdir()
        self.context.game_dir.mkdir()
        (self.context.game_dir / ".verification-evidence").symlink_to(destination, target_is_directory=True)
        with self.assertRaises(ValueError):
            await self.tracer.append_event(self.event(1, "action_result"))
        self.assertEqual((self.context.trace_dir / "events.jsonl").read_text(), "")

    async def test_missing_referenced_screenshot_is_not_marked_complete(self):
        # Harness claims it captured a screenshot but produced no actual file.
        await self.tracer.append_event(self.event(1, "action_result", {
            "result": {"observation": 'HARNESS_JSON={"checks":{"launch":{"evidence_refs":["screenshot:after_load.png"]}}}'},
        }))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Missing archived screenshot" in p for p in report["problems"]))

    async def test_referenced_screenshot_archived_in_same_event_passes(self):
        proof = self.context.game_dir / ".verification-evidence"
        proof.mkdir(parents=True)
        (proof / "after_load.png").write_bytes(b"actual screenshot bytes")
        await self.tracer.append_event(self.event(1, "action_result", {
            "result": {"data": {"stdout": 'HARNESS_JSON={"checks":{"launch":{"evidence_refs":["screenshot:after_load.png"]}}}'}},
        }))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        report = audit_delivery(self.context.trace_dir)
        self.assertTrue(report["complete"], report["problems"])

    async def test_second_verification_cannot_use_first_screenshot_archive(self):
        proof = self.context.game_dir / ".verification-evidence"
        proof.mkdir(parents=True)
        screenshot = proof / "after_load.png"
        screenshot.write_bytes(b"old evidence")
        result = {"result": {"observation": "screenshot:after_load.png"}}
        await self.tracer.append_event(self.event(1, "action_result", result))
        screenshot.unlink()
        await self.tracer.append_event(self.event(2, "action_result", result))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(3, "transition", {"to": "succeeded"}))
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("event 2" in p for p in report["problems"]))

    async def test_missing_screenshot_in_final_verification_report_is_detected(self):
        report = VerificationReport("digest", "rev", "v1", (
            CriterionResult("launch", CheckStatus.PASS, ("screenshot:after_load.png",)),
        ))
        await self.tracer.append_event(self.event(1, "run_start"))
        await self.tracer.finalize(replace(self.summary(), verification=report))
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        audit = audit_delivery(self.context.trace_dir)
        self.assertFalse(audit["complete"])
        self.assertTrue(any("Missing final verification screenshot" in p for p in audit["problems"]))

    async def test_missing_screenshot_in_verifier_result_is_detected(self):
        await self.tracer.append_event(self.event(1, "module_result", {
            "operation": "verifier.next_check",
            "result": {"results": [{"evidence_refs": ["screenshot:after_input.png"]}]},
        }))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Missing verifier screenshot" in p for p in report["problems"]))

    async def test_planner_text_does_not_invent_screenshot_requirements(self):
        await self.tracer.append_event(self.event(1, "module_result", {
            "operation": "planner.next_decision",
            "result": {"rationale": "We might eventually need screenshot:after_load.png"},
        }))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        report = audit_delivery(self.context.trace_dir)
        self.assertTrue(report["complete"], report["problems"])

    async def _finished_run_with_outputs(self, output_paths):
        await self.tracer.append_event(self.event(1, "action_result", {
            "result": {
                "action_id": "runtime-action-1",
                "output_refs": [str(path) for path in output_paths],
            },
        }))
        await self.tracer.finalize(self.summary())
        await self.tracer.append_event(self.event(2, "transition", {"to": "succeeded"}))
        return audit_delivery(self.context.trace_dir)

    def _make_runtime_output(self, name, data):
        directory = self.root / ".agent-runtime" / self.context.run_id
        directory.mkdir(parents=True, exist_ok=True)
        result = directory / name
        result.write_bytes(data)
        return result

    async def test_delivery_audit_requires_run_report(self):
        report = await self._finished_run_with_outputs([])
        self.assertTrue(report["complete"], report["problems"])
        (self.context.trace_dir / "run_report.md").unlink()
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("run_report.md" in p for p in report["problems"]))

    async def test_delivery_audit_rejects_empty_run_report(self):
        await self._finished_run_with_outputs([])
        (self.context.trace_dir / "run_report.md").write_text("")
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertIn("Empty run_report.md", report["problems"])

    async def test_delivery_audit_accepts_copied_stdout_and_stderr(self):
        stdout = self._make_runtime_output("stdout.txt", b"Game launched")
        stderr = self._make_runtime_output("stderr.txt", b"No error")
        report = await self._finished_run_with_outputs([stdout, stderr])
        self.assertTrue(report["complete"], report["problems"])
        rows = [
            json.loads(line)
            for line in (self.context.trace_dir / "outputs" / "index.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue((self.context.trace_dir / row["copy"]).is_file())
            self.assertEqual(len(row["sha256"]), 64)

    async def test_delivery_audit_detects_missing_copied_stderr(self):
        stderr = self._make_runtime_output("stderr.txt", b"runtime error details")
        report = await self._finished_run_with_outputs([stderr])
        self.assertTrue(report["complete"], report["problems"])
        index = json.loads((self.context.trace_dir / "outputs" / "index.jsonl").read_text().strip())
        (self.context.trace_dir / index["copy"]).unlink()
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Missing or unsafe archived output" in p for p in report["problems"]))

    async def test_delivery_audit_detects_tampered_copied_stdout(self):
        stdout = self._make_runtime_output("stdout.txt", b"original")
        await self._finished_run_with_outputs([stdout])
        index = json.loads((self.context.trace_dir / "outputs" / "index.jsonl").read_text().strip())
        (self.context.trace_dir / index["copy"]).write_bytes(b"changed")
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Archived output hash mismatch" in p for p in report["problems"]))

    async def test_delivery_audit_detects_missing_runtime_output_index(self):
        stdout = self._make_runtime_output("stdout.txt", b"some output")
        await self._finished_run_with_outputs([stdout])
        (self.context.trace_dir / "outputs" / "index.jsonl").unlink()
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Missing outputs/index.jsonl" in p for p in report["problems"]))

    async def test_delivery_audit_rejects_unarchived_nonimage_output(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"not in runtime storage")
        report = await self._finished_run_with_outputs([outside])
        self.assertFalse(report["complete"])
        self.assertTrue(any("Unarchived runtime output" in p for p in report["problems"]))

    async def test_delivery_audit_rejects_mismatched_output_index(self):
        stdout = self._make_runtime_output("stdout.txt", b"output data")
        await self._finished_run_with_outputs([stdout])
        index_path = self.context.trace_dir / "outputs" / "index.jsonl"
        row = json.loads(index_path.read_text().strip())
        row["seq"] = 999
        index_path.write_text(json.dumps(row) + "\n")
        report = audit_delivery(self.context.trace_dir)
        self.assertFalse(report["complete"])
        self.assertTrue(any("Missing indexed runtime output" in p for p in report["problems"]))
        self.assertTrue(any("Unexpected indexed runtime output" in p for p in report["problems"]))

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
