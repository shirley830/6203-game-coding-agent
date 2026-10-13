"""Read recorded runs and report counters without executing or modifying games."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path


TERMINAL = {"succeeded", "failed", "exhausted", "cancelled"}


def evaluate_run(trace_dir: Path) -> dict:
    """Check record consistency, not actual game playability or evidence contents."""
    root = Path(trace_dir)
    record = {"trace_dir": str(root), "record_complete": False, "issues": []}
    issues = record["issues"]
    try:
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in
                  (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        if not isinstance(summary, dict) or not all(isinstance(e, dict) for e in events):
            raise ValueError("Summary and events must be objects")
    except (OSError, ValueError) as exc:
        issues.append(f"Unreadable run: {exc}")
        return record

    run_id = summary.get("run_id")
    outcome = summary.get("outcome")
    record.update(run_id=run_id, reported_outcome=outcome)
    if not isinstance(run_id, str) or not run_id:
        issues.append("Missing run ID")
    if not isinstance(outcome, str) or outcome not in TERMINAL:
        issues.append("Invalid summary outcome")
    for number, event in enumerate(events, 1):
        if type(event.get("seq")) is not int or event["seq"] != number:
            issues.append("Non-contiguous event sequence")
            break
    if any(event.get("run_id") != run_id for event in events):
        issues.append("Event run ID differs from summary")
    final = events[-1] if events else {}
    payload = final.get("payload")
    terminal_outcome = payload.get("to") if isinstance(payload, dict) else None
    if (final.get("kind") != "transition" or not isinstance(terminal_outcome, str)
            or terminal_outcome not in TERMINAL):
        issues.append("Missing final terminal transition")
    elif terminal_outcome != outcome or final.get("phase") != terminal_outcome:
        issues.append("Terminal transition differs from summary/phase")

    for name in ("steps_used", "commands_used", "recovery_attempts", "elapsed_seconds"):
        value = summary.get(name)
        valid = (type(value) is int and value >= 0) if name != "elapsed_seconds" else (
            type(value) in {int, float} and math.isfinite(value) and value >= 0)
        if valid:
            record[name] = value
        else:
            issues.append(f"Invalid counter: {name}")

    def count_payload(kind: str, key: str) -> dict:
        counts = Counter()
        for event in events:
            data = event.get("payload")
            if event.get("kind") == kind and isinstance(data, dict):
                value = data.get(key)
                counts[value if isinstance(value, str) else "unknown"] += 1
        return dict(counts)

    record["failure_categories"] = count_payload("failure", "category")
    record["recovery_decisions"] = count_payload("recovery", "kind")
    record["record_complete"] = not issues
    record["recorded_terminal_success"] = not issues and outcome == "succeeded"
    return record


def aggregate(records: list[dict]) -> dict:
    """All requested runs remain in the denominator, including unreadable ones."""
    count = len(records)
    complete = sum(bool(r.get("record_complete")) for r in records)
    succeeded = sum(bool(r.get("recorded_terminal_success")) for r in records)
    return {
        "run_count": count,
        "complete_record_count": complete,
        "recorded_terminal_success_count": succeeded,
        "recorded_terminal_success_rate": succeeded / count if count else None,
        "note": "Recorded controller outcomes only. No independent game, evidence-file, "
                "or recovery-effectiveness verification. Mock runs are not live game results.",
        "runs": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Member 5 run-record metrics")
    parser.add_argument("trace_dirs", nargs="+", type=Path,
                        help="Explicit traces/<run_id> directories from a comparable cohort")
    args = parser.parse_args()
    records = [evaluate_run(path) for path in args.trace_dirs]
    print(json.dumps(aggregate(records), ensure_ascii=False, indent=2))
    return 0 if all(r["record_complete"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
