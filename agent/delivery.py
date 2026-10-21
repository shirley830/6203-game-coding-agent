"""Read-only audit of a completed Agent tracing directory (Member 6)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TERMINAL_OUTCOMES = {"succeeded", "failed", "exhausted", "cancelled"}

# Member 4's harness emits screenshot:after_load.png / screenshot:after_input.png
# inside HARNESS_JSON observations; VerifierPort may also return these refs
# in its structured verification report. Only actual screenshot file references
# count (not other evidence such as DOM changes or HTTP statuses).
_SCREENSHOT_REF = re.compile(
    r"screenshot:([A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp))\b", re.IGNORECASE
)


def _screenshot_refs(value: Any) -> set[str]:
    """Find screenshot references in structured results and stdout previews."""
    if isinstance(value, str):
        return {"screenshot:" + match.group(1) for match in _SCREENSHOT_REF.finditer(value)}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for nested in value.values():
            result.update(_screenshot_refs(nested))
        return result
    if isinstance(value, (list, tuple)):
        result: set[str] = set()
        for nested in value:
            result.update(_screenshot_refs(nested))
        return result
    return set()



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

    # A finalized run should always include its human-readable timeline.
    # It is not necessary for the report to claim success: failed runs
    # require an explanation just as much as successful ones.
    run_report = root / "run_report.md"
    if not run_report.is_file() or run_report.is_symlink():
        problems.append("Missing or unsafe run_report.md")
    else:
        try:
            if not run_report.read_text(encoding="utf-8").strip():
                problems.append("Empty run_report.md")
        except (OSError, UnicodeError) as exc:
            problems.append(f"Cannot read run_report.md: {exc}")

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

    # A valid checksum proves that a *saved* file is intact, but does not
    # establish that *every referenced* screenshot was saved in the first place.
    # Compare the refs found in real verifier output with the archive manifest.
    # Action-result references must be archived for that action's own sequence
    # (an older screenshot cannot satisfy a later verification attempt).
    archived_by_seq: dict[int, set[str]] = {}
    archived_any: set[str] = set()
    if manifest.is_file():
        try:
            for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                reference = record.get("source_ref")
                sequence = record.get("event_seq")
                relative = record.get("archived_path")
                if not isinstance(reference, str) or not isinstance(sequence, int):
                    continue
                if reference not in _screenshot_refs(reference):
                    continue
                if not isinstance(relative, str):
                    continue
                destination = (root / relative).resolve()
                if not destination.is_relative_to((root / "evidence").resolve()) or not destination.is_file():
                    continue
                archived_by_seq.setdefault(sequence, set()).add(reference)
                archived_any.add(reference)
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"Cannot match screenshot references: {exc}")

    for event in events:
        if event.get("kind") == "action_result":
            payload = event.get("payload")
            refs = _screenshot_refs(payload.get("result", {})) if isinstance(payload, dict) else set()
            seq = event.get("seq")
            for reference in sorted(refs - archived_by_seq.get(seq, set())):
                problems.append(f"Missing archived screenshot for event {seq}: {reference}")
        elif event.get("kind") == "module_result":
            payload = event.get("payload")
            if isinstance(payload, dict) and "verif" in str(payload.get("operation", "")).lower():
                for reference in sorted(_screenshot_refs(payload.get("result", {})) - archived_any):
                    problems.append(f"Missing verifier screenshot: {reference}")
    if summary is not None:
        for reference in sorted(_screenshot_refs(summary.get("verification", {})) - archived_any):
            problems.append(f"Missing final verification screenshot: {reference}")

    # Check Member 3's runtime output references against Member 6's archive.
    # Some runs have no stdout/stderr: only require files that are actually
    # referenced by action_result events. A deliberate skip for a non-image
    # output is incomplete delivery, not proof that it was preserved.
    expected_outputs: set[tuple[int, str]] = set()
    for event in events:
        if event.get("kind") != "action_result":
            continue
        payload = event.get("payload")
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            continue
        refs = result.get("output_refs") or []
        if not isinstance(refs, list):
            problems.append(f"Malformed output_refs at event {event.get('seq')}")
            continue
        for ref in refs:
            if isinstance(ref, str):
                expected_outputs.add((event["seq"], ref))
            else:
                problems.append(f"Invalid output reference at event {event.get('seq')}")

    output_manifest = root / "outputs" / "index.jsonl"
    indexed_outputs: set[tuple[int, str]] = set()
    if not output_manifest.is_file():
        if expected_outputs:
            problems.append("Missing outputs/index.jsonl for referenced runtime outputs")
    else:
        output_root = (root / "outputs").resolve()
        try:
            for line_number, line in enumerate(output_manifest.read_text(encoding="utf-8").splitlines(), 1):
                record = json.loads(line)
                if not isinstance(record, dict):
                    problems.append(f"Invalid output index record at line {line_number}")
                    continue
                seq = record.get("seq")
                ref = record.get("ref")
                if not isinstance(seq, int) or not isinstance(ref, str):
                    problems.append(f"Invalid output reference in index at line {line_number}")
                    continue
                key = (seq, ref)
                if key in indexed_outputs:
                    problems.append(f"Duplicate output index reference at line {line_number}")
                indexed_outputs.add(key)

                if "copy" in record:
                    relative = record.get("copy")
                    expected_hash = record.get("sha256")
                    if not isinstance(relative, str) or not isinstance(expected_hash, str):
                        problems.append(f"Invalid copied output record at line {line_number}")
                        continue
                    source = root / relative
                    destination = source.resolve()
                    if (not destination.is_relative_to(output_root) or not source.is_file()
                            or source.is_symlink()):
                        problems.append(f"Missing or unsafe archived output at line {line_number}")
                        continue
                    if destination.stat().st_size > 20 * 1024 * 1024:
                        problems.append(f"Oversized archived output at line {line_number}")
                        continue
                    actual_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
                    if actual_hash != expected_hash:
                        problems.append(f"Archived output hash mismatch at line {line_number}")
                elif "skipped" in record:
                    # Screenshot bytes belong in evidence/ rather than outputs/.
                    if record.get("skipped") == "screenshot archived under evidence/":
                        screenshot = f"screenshot:{Path(ref).name}"
                        if screenshot not in archived_by_seq.get(seq, set()):
                            problems.append(f"Missing archived runtime screenshot at event {seq}: {screenshot}")
                    else:
                        problems.append(f"Unarchived runtime output at event {seq}: {ref}")
                else:
                    problems.append(f"Output index lacks copy/skipped at line {line_number}")
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"Cannot check outputs/index.jsonl: {exc}")

    for seq, ref in sorted(expected_outputs - indexed_outputs):
        problems.append(f"Missing indexed runtime output at event {seq}: {ref}")
    for seq, ref in sorted(indexed_outputs - expected_outputs):
        problems.append(f"Unexpected indexed runtime output at event {seq}: {ref}")

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
