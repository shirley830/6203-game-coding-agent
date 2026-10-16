"""Read-only audit of a completed Agent tracing directory (Member 6)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


TERMINAL_OUTCOMES = {"succeeded", "failed", "exhausted", "cancelled"}


def audit_delivery(trace_dir: str | Path) -> dict[str, Any]:
    """Find incomplete logs or a missing Controller terminal event."""
    root = Path(trace_dir)
    problems: list[str] = []
    events: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None

    event_file = root / "events.jsonl"
    if not event_file.is_file():
        problems.append("Missing events.jsonl")
    else:
        try:
            for number, line in enumerate(event_file.read_text(encoding="utf-8").splitlines(), 1):
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    problems.append(f"Event at line {number} is not an object")
                    continue
                events.append(parsed)
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"Cannot read events.jsonl: {exc}")

    summary_file = root / "summary.json"
    if not summary_file.is_file():
        problems.append("Missing summary.json")
    else:
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                problems.append("Summary is not an object")
                summary = None
        except (OSError, ValueError) as exc:
            problems.append(f"Cannot read summary.json: {exc}")

    if not events:
        problems.append("No saved trace events")
    else:
        for expected, event in enumerate(events, 1):
            if event.get("seq") != expected:
                problems.append(f"Event sequence is broken at position {expected}")
                break
            if summary is not None and event.get("run_id") != summary.get("run_id"):
                problems.append(f"Unexpected run ID at event {expected}")
                break
        terminal = events[-1]
        last_outcome = terminal.get("payload", {}).get("to") if isinstance(terminal.get("payload"), dict) else None
        if terminal.get("kind") != "transition" or last_outcome not in TERMINAL_OUTCOMES:
            problems.append("Missing final terminal transition event")
        if summary is not None:
            if summary.get("run_id") != events[0].get("run_id"):
                problems.append("Run ID differs between summary and events")
            if last_outcome in TERMINAL_OUTCOMES and summary.get("outcome") != last_outcome:
                problems.append("Summary outcome differs from final event")

    # Member 4 screenshots are copied into traces/evidence by Member 6.
    # Check the manifest and the actual files; symbolic screenshot refs alone
    # do not prove that the screenshot bytes survived delivery.
    manifest = root / "evidence" / "index.jsonl"
    if not manifest.is_file() and list((root / "evidence").glob("seq-*")):
        problems.append("Missing evidence/index.jsonl for archived screenshots")
    if manifest.is_file():
        try:
            for line_num, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                if not isinstance(record, dict):
                    problems.append(f"Invalid evidence record at line {line_num}")
                    continue
                relative = record.get("archived_path")
                expected_digest = record.get("sha256")
                if not isinstance(relative, str) or not isinstance(expected_digest, str):
                    problems.append(f"Invalid evidence reference at line {line_num}")
                    continue
                candidate = (root / relative).resolve()
                evidence_root = (root / "evidence").resolve()
                if not candidate.is_relative_to(evidence_root) or not candidate.is_file():
                    problems.append(f"Missing or unsafe evidence file at line {line_num}")
                    continue
                if candidate.stat().st_size > 15 * 1024 * 1024:
                    problems.append(f"Oversized evidence file at line {line_num}")
                    continue
                actual_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if actual_digest != expected_digest:
                    problems.append(f"Evidence file hash mismatch at line {line_num}")
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"Cannot check evidence index: {exc}")

    return {
        "complete": not problems,
        "run_id": summary.get("run_id") if summary else None,
        "outcome": summary.get("outcome") if summary else None,
        "event_count": len(events),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a PE6203 trace delivery")
    parser.add_argument("trace_dir", type=Path, help="A traces/<run_id>/ directory")
    args = parser.parse_args()
    report = audit_delivery(args.trace_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
