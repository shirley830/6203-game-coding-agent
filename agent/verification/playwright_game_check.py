#!/usr/bin/env python3
"""Bounded browser-game verification harness (Member 4).

Invoked by the controller via run_command. Lifecycle is always:
  start launch command (optional) -> open page -> functional/interaction checks -> shut down.

Important: navigating to a URL alone is NOT a pass for interaction/functional criteria.

Stdout includes a single line:
  HARNESS_JSON={...}

Exit codes:
  0 = harness completed (individual checks may still fail inside JSON)
  1 = harness infrastructure failure (Playwright missing, timeout, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _print_json(payload: dict[str, Any]) -> None:
    print("HARNESS_JSON=" + json.dumps(payload, ensure_ascii=False), flush=True)


def _check(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    body = {"status": status, "detail": detail, "evidence_refs": []}
    body.update(extra)
    return body


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _terminate(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _guess_url_from_argv(argv: list[str], default_port: int) -> str:
    joined = " ".join(argv)
    match = re.search(r"https?://[^\s\"']+", joined)
    if match:
        return match.group(0)
    port_match = re.search(r"--port[= ](\d+)", joined)
    if port_match:
        return f"http://127.0.0.1:{port_match.group(1)}/"
    if any(part == "http.server" for part in argv):
        # python -m http.server [port]
        for idx, part in enumerate(argv):
            if part == "http.server" and idx + 1 < len(argv) and argv[idx + 1].isdigit():
                return f"http://127.0.0.1:{argv[idx + 1]}/"
        return f"http://127.0.0.1:{default_port}/"
    return f"http://127.0.0.1:{default_port}/"


def _start_launch(launch: list[str], cwd: Path, timeout: float) -> tuple[subprocess.Popen[Any] | None, str, str]:
    """Start the game process; return (proc, url, note)."""
    if not launch:
        # Static fallback: serve the current game directory.
        port = _free_port()
        argv = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
        url = f"http://127.0.0.1:{port}/"
        note = "no launch_command; started python -m http.server fallback"
    else:
        port = _free_port()
        argv = list(launch)
        # If launch looks like http.server without a port, inject a free port.
        if "http.server" in argv and not any(p.isdigit() for p in argv):
            argv.append(str(port))
        url = _guess_url_from_argv(argv, port)
        note = "started provided launch_command"

    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(argv, **popen_kwargs)
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port_num = parsed.port or (443 if parsed.scheme == "https" else 80)
    ready = _wait_port(host, port_num, min(timeout * 0.4, 15.0))
    if not ready and proc.poll() is not None:
        return None, url, note + "; process exited before port was ready"
    if not ready:
        return proc, url, note + "; port not confirmed open (continuing to page check)"
    return proc, url, note + "; port ready"


def _run_playwright(url: str, evidence_dir: Path, budget: float) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "checks": {
                "launch": _check("fail", "playwright package not installed"),
                "functional": _check("fail", "skipped: playwright missing"),
                "interaction": _check("fail", "skipped: playwright missing", state_changed=False),
                "technical": _check("fail", "skipped: playwright missing"),
            },
        }

    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot = evidence_dir / "after_load.png"
    after_input = evidence_dir / "after_input.png"
    nav_timeout_ms = int(max(3.0, min(budget * 0.35, 20.0)) * 1000)
    action_timeout_ms = int(max(2.0, min(budget * 0.25, 10.0)) * 1000)

    launch_block = _check("fail", "not started")
    functional_block = _check("fail", "not started")
    interaction_block = _check("fail", "not started", state_changed=False)
    technical_block = _check("fail", "not started")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(action_timeout_ms)
            response = page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            status = response.status if response is not None else 0
            page.wait_for_timeout(400)
            page.screenshot(path=str(screenshot), full_page=True)

            canvas_count = page.locator("canvas").count()
            body_text = page.locator("body").inner_text(timeout=action_timeout_ms)[:2000]
            has_controls = any(
                token in body_text.lower()
                for token in ("score", "restart", "pause", "arrow", "wasd", "controls", "points")
            )
            play_surface = canvas_count > 0 or bool(page.locator("#game, .game, [data-game]").count())

            if status and status >= 400:
                launch_block = _check(
                    "fail",
                    f"HTTP {status} loading {url}",
                    evidence_refs=[f"screenshot:{screenshot.name}", f"http:{status}"],
                )
            elif not play_surface and not body_text.strip():
                launch_block = _check(
                    "fail",
                    "Page loaded but no play surface or visible UI text",
                    evidence_refs=[f"screenshot:{screenshot.name}"],
                )
            else:
                launch_block = _check(
                    "pass",
                    f"Loaded {url} (http={status}, canvas={canvas_count})",
                    evidence_refs=[f"screenshot:{screenshot.name}", f"url:{url}"],
                )

            if canvas_count > 0:
                technical_block = _check(
                    "pass",
                    f"Found {canvas_count} canvas element(s)",
                    evidence_refs=[f"dom:canvas:{canvas_count}"],
                )
            elif play_surface:
                technical_block = _check(
                    "pass",
                    "Found non-canvas game container",
                    evidence_refs=["dom:game-container"],
                )
            else:
                technical_block = _check(
                    "fail",
                    "No canvas/game container detected",
                    evidence_refs=[f"screenshot:{screenshot.name}"],
                )

            # Functional: require gameplay-facing UI signals beyond a blank page.
            if has_controls or ("score" in body_text.lower()) or canvas_count > 0:
                functional_block = _check(
                    "pass" if launch_block["status"] == "pass" else "fail",
                    "Observed gameplay UI/canvas signals from the specification family",
                    evidence_refs=[
                        f"screenshot:{screenshot.name}",
                        "ui:controls_or_score_or_canvas",
                    ],
                )
            else:
                functional_block = _check(
                    "fail",
                    "No score/controls/canvas gameplay signals; page open is not enough",
                    evidence_refs=[f"screenshot:{screenshot.name}"],
                )

            # Interaction: attempt input and require an observable state change.
            before_text = body_text
            before_score = _extract_score(before_text)
            before_pixels = None
            if canvas_count > 0:
                before_pixels = page.evaluate(
                    """() => {
                        const c = document.querySelector('canvas');
                        if (!c) return null;
                        const ctx = c.getContext('2d');
                        if (!ctx) return null;
                        const {width, height} = c;
                        const data = ctx.getImageData(0, 0, Math.min(width, 64), Math.min(height, 64)).data;
                        let sum = 0;
                        for (let i = 0; i < data.length; i++) sum += data[i];
                        return sum;
                    }"""
                )

            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowUp")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Space")
            page.keyboard.press("KeyP")
            # Click restart-like controls if present.
            for selector in (
                "button:has-text('Restart')",
                "button:has-text('Start')",
                "text=Restart",
                "text=Start",
            ):
                locator = page.locator(selector)
                if locator.count():
                    try:
                        locator.first.click(timeout=1000)
                    except Exception:
                        pass
                    break
            page.mouse.click(120, 120)
            page.wait_for_timeout(500)
            page.screenshot(path=str(after_input), full_page=True)

            after_text = page.locator("body").inner_text(timeout=action_timeout_ms)[:2000]
            after_score = _extract_score(after_text)
            after_pixels = None
            if canvas_count > 0:
                after_pixels = page.evaluate(
                    """() => {
                        const c = document.querySelector('canvas');
                        if (!c) return null;
                        const ctx = c.getContext('2d');
                        if (!ctx) return null;
                        const {width, height} = c;
                        const data = ctx.getImageData(0, 0, Math.min(width, 64), Math.min(height, 64)).data;
                        let sum = 0;
                        for (let i = 0; i < data.length; i++) sum += data[i];
                        return sum;
                    }"""
                )

            state_changed = False
            reasons: list[str] = []
            if before_text != after_text:
                state_changed = True
                reasons.append("dom_text_changed")
            if before_score is not None and after_score is not None and before_score != after_score:
                state_changed = True
                reasons.append(f"score {before_score}->{after_score}")
            if before_pixels is not None and after_pixels is not None and before_pixels != after_pixels:
                state_changed = True
                reasons.append("canvas_pixels_changed")

            if state_changed:
                interaction_block = _check(
                    "pass",
                    "Input produced observable state change: " + ", ".join(reasons),
                    evidence_refs=[
                        f"screenshot:{after_input.name}",
                        "input:keyboard_pointer",
                        *[f"change:{r}" for r in reasons],
                    ],
                    state_changed=True,
                )
            else:
                interaction_block = _check(
                    "fail",
                    "No observable state change after input; page open ≠ playability",
                    evidence_refs=[f"screenshot:{after_input.name}", "input:attempted"],
                    state_changed=False,
                )
        finally:
            browser.close()

    return {
        "ok": all(
            block["status"] == "pass"
            for block in (launch_block, functional_block, interaction_block)
        ),
        "checks": {
            "launch": launch_block,
            "functional": functional_block,
            "interaction": interaction_block,
            "technical": technical_block,
        },
    }


def _extract_score(text: str) -> int | None:
    match = re.search(r"score\s*[:=]?\s*(\d+)", text, flags=re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*points?", text, flags=re.I)
    if match:
        return int(match.group(1))
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Playwright game verification harness")
    parser.add_argument("--timeout-seconds", type=float, default=40.0)
    parser.add_argument("--evidence-dir", type=str, default=".verification-evidence")
    parser.add_argument("--url", type=str, default="")
    parser.add_argument(
        "--launch",
        nargs=argparse.REMAINDER,
        default=[],
        help="Launch argv after this flag (example: --launch python -m http.server 8765)",
    )
    args = parser.parse_args(argv)

    deadline = time.monotonic() + max(5.0, args.timeout_seconds)
    cwd = Path.cwd()
    evidence_dir = cwd / args.evidence_dir
    launch = [part for part in (args.launch or []) if part != "--"]

    proc: subprocess.Popen[Any] | None = None
    url = args.url
    note = ""
    try:
        remaining = deadline - time.monotonic()
        if not url:
            proc, url, note = _start_launch(launch, cwd, remaining)
        else:
            note = "using provided --url"

        remaining = deadline - time.monotonic()
        if remaining <= 2:
            payload = {
                "ok": False,
                "note": note,
                "url": url,
                "checks": {
                    "launch": _check("fail", "timeout before browser checks"),
                    "functional": _check("fail", "timeout"),
                    "interaction": _check("fail", "timeout", state_changed=False),
                    "technical": _check("fail", "timeout"),
                },
            }
            _print_json(payload)
            return 1

        result = _run_playwright(url, evidence_dir, remaining)
        result["note"] = note
        result["url"] = url
        result["launch_argv"] = launch
        _print_json(result)
        return 0 if result.get("ok") else 0  # checks encoded in JSON; controller reads evidence
    except Exception as exc:  # noqa: BLE001 - harness must always emit JSON
        _print_json(
            {
                "ok": False,
                "note": note,
                "url": url,
                "checks": {
                    "launch": _check("fail", f"harness error: {type(exc).__name__}: {exc}"),
                    "functional": _check("fail", "harness error"),
                    "interaction": _check("fail", "harness error", state_changed=False),
                    "technical": _check("fail", "harness error"),
                },
            }
        )
        return 1
    finally:
        _terminate(proc)


if __name__ == "__main__":
    raise SystemExit(main())
