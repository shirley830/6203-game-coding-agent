"""Startup self-check, run before development begins.

A missing browser, an unusable sandbox or a busy game port would otherwise
surface only at the first verification, after minutes of model calls, and the
run could never succeed. Those are *blocking* problems: the agent stops before
creating game/. Everything else becomes a *note* for the planner (for example
that Node.js is not installed, so it must not plan `node` commands).

Standalone use on the demo machine:  python -m agent.preflight
"""

from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .planning.prompts import DEFAULT_LAUNCH_COMMAND
from .tools.processes import OSSandbox


@dataclass
class PreflightReport:
    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def render(self) -> str:
        lines = [f"[ok]    {item}" for item in self.passed]
        lines += [f"[note]  {item}" for item in self.notes]
        lines += [f"[FAIL]  {item}" for item in self.blocking]
        return "\n".join(lines)


def _game_port() -> int:
    return int(next((part for part in DEFAULT_LAUNCH_COMMAND if part.isdigit()), "8000"))


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _check_sandbox(report: PreflightReport, strict: bool) -> None:
    if not strict:
        report.notes.append("OS command sandbox disabled (AGENT_STRICT_SANDBOX=0)")
        return
    sandbox = OSSandbox(enabled=True)
    if not sandbox.available:
        report.blocking.append(
            f"No command sandbox on {platform.system()}: macOS needs /usr/bin/sandbox-exec, "
            "Linux needs bubblewrap (bwrap). Windows is not supported.")
        return
    if platform.system() == "Darwin":
        probe = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", sys.executable, "-c", "0"]
    else:
        probe = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
                 sys.executable, "-c", "0"]
    try:
        done = subprocess.run(probe, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.blocking.append(f"Command sandbox cannot start: {type(exc).__name__}: {exc}")
        return
    if done.returncode != 0:
        report.blocking.append("Command sandbox exists but cannot run commands: "
                               + done.stderr.decode(errors="replace")[:200])
    else:
        report.passed.append(f"command sandbox works ({platform.system()})")


def _check_browser(report: PreflightReport, environment: dict[str, str]) -> None:
    code = ("from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    p.chromium.launch(headless=True).close()\n")
    try:
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60,
                              env={**os.environ, **environment})
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.blocking.append(f"Headless Chromium did not start: {type(exc).__name__}")
        return
    if done.returncode != 0:
        error = done.stderr.decode(errors="replace").strip().splitlines()
        report.blocking.append(
            "Playwright/Chromium unavailable, so no game can be verified: "
            + (error[-1][:200] if error else "unknown error")
            + ". Fix: pip install -e . && python -m playwright install chromium-headless-shell")
    else:
        report.passed.append("Playwright headless Chromium launches")


def run_preflight(*, strict_sandbox: bool = True,
                  browser_environment: dict[str, str] | None = None) -> PreflightReport:
    report = PreflightReport()
    if sys.version_info < (3, 11):
        report.blocking.append(f"Python 3.11+ required, found {platform.python_version()}")
    else:
        report.passed.append(f"Python {platform.python_version()}")

    python3 = shutil.which("python3")
    if python3 is None:
        report.blocking.append("python3 is not on PATH; the game launch command needs it")
    else:
        report.passed.append(f"python3 on PATH ({python3})")

    port = _game_port()
    if _port_free(port):
        report.passed.append(f"game port {port} is free")
    else:
        report.blocking.append(f"Port {port} is already in use; stop that server before the run "
                               "(verification would test the wrong page)")

    _check_sandbox(report, strict_sandbox)
    _check_browser(report, browser_environment or {})

    for tool in ("node", "npm"):
        if shutil.which(tool) is None:
            report.notes.append(f"{tool} is NOT installed: do not plan commands that use {tool}")
        else:
            report.notes.append(f"{tool} is installed")
    return report


async def probe_model(client, *, timeout_seconds: float = 90.0) -> PreflightReport:
    """One explicitly requested, billed call; never print raw response/errors/keys."""
    report = PreflightReport()
    try:
        content = await asyncio.wait_for(client.generate(
            system_prompt="Return exactly one JSON object, without commentary.",
            user_prompt='Reply with exactly {"ready":true}.',
            timeout_seconds=timeout_seconds,
        ), timeout_seconds)
        payload = json.loads(content)
        if not isinstance(payload, dict) or payload.get("ready") is not True:
            raise ValueError("Unexpected probe output")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Provider exception messages can include request details. Type only.
        report.blocking.append(
            f"Model probe failed ({type(exc).__name__}); check provider, credentials, "
            "model ID, output budget and reasoning parameters. No development started.")
    else:
        report.passed.append("Model API returns usable JSON text with current provider settings")
    return report


def main(argv=None) -> int:
    from .integrations import _playwright_environment, reasoning_budget
    from .envfile import load_env_file

    parser = argparse.ArgumentParser(description="Check demo machine and optionally make one billed model call")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / "agent.env")
    parser.add_argument("--probe-model", action="store_true", help="Make one billed API call; no retries")
    parser.add_argument("--model-timeout", type=float, default=90.0)
    args = parser.parse_args(argv)
    if not 0 < args.model_timeout <= 600:
        parser.error("--model-timeout must be positive and at most 600 seconds")
    load_env_file(args.env_file)

    report = run_preflight(
        strict_sandbox=os.environ.get("AGENT_STRICT_SANDBOX", "1") != "0",
        browser_environment=_playwright_environment(),
    )
    if args.probe_model and report.ok:
        model = os.environ.get("AGENT_MODEL", "").strip()
        if not model:
            report.blocking.append("AGENT_MODEL is not configured")
        else:
            from .planning.llm_client import LiteLLMClient
            try:
                client = LiteLLMClient(
                    model=model, api_base=os.environ.get("AGENT_API_BASE") or None,
                    max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", "32000")),
                    reasoning_max_tokens=reasoning_budget(model),
                )
                model_report = asyncio.run(probe_model(client, timeout_seconds=args.model_timeout))
                report.passed.extend(model_report.passed)
                report.blocking.extend(model_report.blocking)
            except Exception as exc:
                report.blocking.append(f"Invalid model configuration ({type(exc).__name__})")
    elif not args.probe_model:
        report.notes.append("Model connectivity NOT checked; use --probe-model for one billed test")
    print(report.render())
    print("\nREADY" if report.ok else "\nNOT READY: fix the [FAIL] items above")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
