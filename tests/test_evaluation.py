"""Offline metrics must not turn incomplete or failed records into success."""

import json
from pathlib import Path
import tempfile
import unittest

from evaluation.report import aggregate, evaluate_run


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.summary = dict(run_id="run1", outcome="succeeded", steps_used=10,
                            commands_used=2, recovery_attempts=1, elapsed_seconds=3.5)
        self.events = [
            dict(run_id="run1", seq=1, kind="failure", phase="executing",
                 payload={"category": "NONZERO_EXIT"}),
            dict(run_id="run1", seq=2, kind="recovery", phase="recovering",
                 payload={"kind": "repair"}),
            dict(run_id="run1", seq=3, kind="transition", phase="succeeded",
                 payload={"from": "finalizing", "to": "succeeded"}),
        ]

    def write(self):
        (self.root / "summary.json").write_text(json.dumps(self.summary), encoding="utf-8")
        (self.root / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")

    def test_complete_record_reports_counters_and_decisions(self):
        self.write()
        record = evaluate_run(self.root)
        self.assertTrue(record["recorded_terminal_success"])
        self.assertEqual(record["failure_categories"], {"NONZERO_EXIT": 1})
        self.assertEqual(record["recovery_decisions"], {"repair": 1})
        self.assertEqual(record["elapsed_seconds"], 3.5)

    def test_missing_terminal_is_not_success(self):
        self.events.pop()
        self.write()
        self.assertFalse(evaluate_run(self.root)["recorded_terminal_success"])

    def test_mismatched_summary_is_not_success(self):
        self.events[-1]["payload"]["to"] = "failed"
        self.write()
        self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_broken_sequence_is_not_complete(self):
        self.events[1]["seq"] = 9
        self.write()
        self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_cross_run_events_are_rejected(self):
        self.events[0]["run_id"] = "other"
        self.write()
        self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_invalid_counters_are_rejected(self):
        for value in (-1, True, "10"):
            self.summary["steps_used"] = value
            self.write()
            self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_malformed_outcome_does_not_crash(self):
        self.summary["outcome"] = ["succeeded"]
        self.events[-1]["payload"]["to"] = {"invalid": True}
        self.write()
        self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_failed_run_can_have_complete_records(self):
        self.summary["outcome"] = "failed"
        self.events[-1]["phase"] = "failed"
        self.events[-1]["payload"]["to"] = "failed"
        self.write()
        record = evaluate_run(self.root)
        self.assertTrue(record["record_complete"])
        self.assertFalse(record["recorded_terminal_success"])

    def test_missing_or_corrupt_files_are_retained_as_incomplete_runs(self):
        self.assertFalse(evaluate_run(self.root)["record_complete"])
        self.write()
        (self.root / "events.jsonl").write_text("not JSON", encoding="utf-8")
        self.assertFalse(evaluate_run(self.root)["record_complete"])

    def test_metrics_include_failed_and_unreadable_runs_in_denominator(self):
        records = [dict(record_complete=True, recorded_terminal_success=True),
                   dict(record_complete=True, recorded_terminal_success=False),
                   dict(record_complete=False)]
        result = aggregate(records)
        self.assertEqual(result["recorded_terminal_success_rate"], 1 / 3)
        self.assertEqual(result["complete_record_count"], 2)

    def test_empty_cohort_has_no_success_rate(self):
        self.assertIsNone(aggregate([])["recorded_terminal_success_rate"])

    def test_analysis_does_not_modify_trace_files(self):
        self.write()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        evaluate_run(self.root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
