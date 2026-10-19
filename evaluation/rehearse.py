"""Sequential real-model rehearsals with durable, credential-free evidence ZIPs.

The spend threshold is checked BETWEEN runs, not a provider-enforced hard cap.
Account/key usage may include concurrent calls from elsewhere. No account settings
or API key limits are changed by this tool.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED

from agent.delivery import audit_delivery
from agent.envfile import parse_env_file


def key_usage(key: str) -> dict:
    request = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)["data"]
    return {name: data.get(name) for name in ("usage", "limit", "limit_remaining")}


def archive_run(root: Path, destination: Path, secrets: list[str]) -> dict:
    """Whitelist outputs; reject credential leakage and symbolic links."""
    paths = []
    for directory in ("game", "traces"):
        source = root / directory
        if source.is_symlink():
            raise ValueError("Refusing artifact-directory symlink")
        if source.exists():
            for path in source.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Refusing artifact symlink")
                if path.is_file() and "__pycache__" not in path.parts:
                    paths.append(path)
    for name in ("game_design_prompt.md", "rehearsal_metadata.json", "run.stdout.json", "run.stderr.log"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    needles = [s.encode() for s in secrets if s]
    for path in paths:
        if any(needle in path.read_bytes() for needle in needles):
            raise ValueError("Credential found in selected output; archive withheld")
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, path.relative_to(root))
    return {"file_count": len(paths), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path,
                        help="NEW persistent directory, never an existing run")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).parent / "specs/manifest.json")
    parser.add_argument("--max-runtime", type=float, default=1200)
    parser.add_argument("--max-spend", type=float, default=1.0,
                        help="OpenRouter usage increase allowed between runs; not a hard per-call cap")
    args = parser.parse_args(argv)
    if not 30 <= args.max_runtime <= 7200 or not 0 < args.max_spend <= 20:
        parser.error("Use a finite runtime in [30,7200] and spend threshold in (0,20]")
    config_text = args.env_file.read_text(encoding="utf-8")
    config = parse_env_file(config_text)
    # Shell credentials/config override agent.env exactly as in the production CLI.
    effective = {**config, **os.environ}
    if not effective.get("AGENT_MODEL", "").startswith("openrouter/"):
        parser.error("This billing-aware batch command currently supports OpenRouter only")
    key = effective.get("OPENROUTER_API_KEY", "")
    if not key:
        parser.error("Configure OPENROUTER_API_KEY locally")
    repo = Path(__file__).resolve().parents[1]
    specs = json.loads(args.manifest.read_text(encoding="utf-8"))
    for item in specs:
        if not (repo / item["path"]).is_file():
            parser.error("Manifest references a missing spec")
    baseline = key_usage(key)
    if not isinstance(baseline.get("usage"), (int, float)):
        parser.error("Cannot measure OpenRouter key usage; no runs started")
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip():
        parser.error("Commit source changes before a comparable batch rehearsal")
    summary = {"source_commit": source, "model": effective["AGENT_MODEL"],
               "baseline_key_usage": baseline, "spend_threshold_usd": args.max_spend,
               "spend_policy": "between-run soft threshold; includes concurrent key activity",
               "runs": [], "pending_specs": [s["spec_id"] for s in specs]}
    summary_path = output / "batch_summary.json"
    def save():
        temporary = summary_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(summary_path)
    save()
    secrets = [value for name, value in effective.items()
               if name.endswith(("_KEY", "_TOKEN", "_SECRET")) and value]
    for item in specs:
        before = key_usage(key)
        used = before["usage"] - baseline["usage"]
        remaining = before.get("limit_remaining")
        if used >= args.max_spend or (isinstance(remaining, (int, float)) and remaining < 0.50):
            summary["stop_reason"] = "Between-run spend threshold or key allowance reached"
            save()
            return 3
        root = output / item["spec_id"]
        root.mkdir()
        (root / "game_design_prompt.md").write_bytes((repo / item["path"]).read_bytes())
        private = root / "agent.env"
        fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(config_text)
        metadata = {"spec_id": item["spec_id"], "source_commit": source,
                    "model": effective["AGENT_MODEL"], "max_runtime_seconds": args.max_runtime,
                    "started_utc": datetime.now(timezone.utc).isoformat(),
                    "manual_development_intervention": False, "key_usage_before": before}
        (root / "rehearsal_metadata.json").write_text(json.dumps(metadata, indent=2))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        print(json.dumps({"started": item["spec_id"]}), flush=True)
        with (root / "run.stdout.json").open("w") as stdout, (root / "run.stderr.log").open("w") as stderr:
            child = subprocess.Popen([sys.executable, "-m", "agent", "game_design_prompt.md",
                                      "--max-runtime", str(args.max_runtime)],
                                     cwd=root, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = child.wait(timeout=args.max_runtime + 120)
            except subprocess.TimeoutExpired:
                # Stop the batch if cooperative cleanup cannot be established.
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                summary["stop_reason"] = "Agent exceeded wall-clock grace; inspect process cleanup before continuing"
                save()
                return 1
        after = key_usage(key)
        record = dict(metadata, exit_code=code, ended_utc=datetime.now(timezone.utc).isoformat(),
                      key_usage_after=after, key_usage_delta_usd=after["usage"] - before["usage"])
        try:
            run = json.loads((root / "run.stdout.json").read_text())
            record.update({name: run.get(name) for name in (
                "outcome", "elapsed_seconds", "steps_used", "commands_used", "recovery_attempts")})
            record["delivery_audit"] = audit_delivery(Path(run["trace_dir"]))
        except (ValueError, KeyError, OSError) as exc:
            record["record_error_type"] = type(exc).__name__
        (root / "rehearsal_metadata.json").write_text(json.dumps(record, indent=2))
        record["archive"] = archive_run(root, output / (item["spec_id"] + ".zip"), secrets)
        summary["runs"].append(record)
        summary["pending_specs"].remove(item["spec_id"])
        summary["latest_key_usage"] = after
        summary["batch_key_usage_delta_usd"] = after["usage"] - baseline["usage"]
        save()
        print(json.dumps({"finished": item["spec_id"], "outcome": record.get("outcome"),
                          "delivery_complete": record.get("delivery_audit", {}).get("complete"),
                          "seconds": record.get("elapsed_seconds")}), flush=True)
    summary["stop_reason"] = "All requested specs attempted; see individual outcomes"
    save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
