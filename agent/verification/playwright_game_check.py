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

Member 5 (recovery): a requirement with status "unknown" and detail containing
``test_script_error`` is a broken assertion script (a bare name such as
``before`` was used instead of ``s`` / ``saved.*``). Do not REPAIR the game for
that result. It is not a game FAIL, and it is not a game PASS.
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
    # The game server shares the harness process group, so the runtime's
    # process-group cleanup still reaps it if this harness is killed.
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
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


def _start_launch(launch: list[str], cwd: Path, timeout: float, log_path: Path | None = None) -> tuple[subprocess.Popen[Any] | None, str, str]:
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

    log_stream: Any = subprocess.DEVNULL
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("wb")
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": log_stream,
        "stderr": subprocess.STDOUT if log_path is not None else subprocess.DEVNULL,
    }
    # Deliberately no start_new_session: a detached session would escape the
    # runtime's process-group cleanup after a harness timeout.

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port_num = parsed.port or (443 if parsed.scheme == "https" else 80)
    if _wait_port(host, port_num, 0.3):
        # Another process already serves this port: checking it would verify
        # the wrong program, so fail the launch criterion explicitly.
        raise RuntimeError(f"port {port_num} is already in use before launch; "
                           "stop the other server or choose another port")
    proc = subprocess.Popen(argv, **popen_kwargs)
    ready = _wait_port(host, port_num, min(timeout * 0.4, 15.0))
    if not ready and proc.poll() is not None:
        return None, url, note + "; process exited before port was ready"
    if not ready:
        return proc, url, note + "; port not confirmed open (continuing to page check)"
    return proc, url, note + "; port ready"


def _run_playwright(url: str, evidence_dir: Path, budget: float,
                    spec: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.monotonic()
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

    page_errors: list[str] = []
    contract_block: dict[str, Any] | None = None
    requirement_blocks: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.add_init_script(_KEY_HOOK_JS)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)[:300]))
            page.on("console", lambda msg: page_errors.append(
                "console.error: " + msg.text[:300])
                if msg.type == "error" and not _is_favicon(msg) else None)
            page.set_default_timeout(action_timeout_ms)
            response = page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            status = response.status if response is not None else 0
            page.wait_for_timeout(400)
            page.screenshot(path=str(screenshot), full_page=True)

            canvas_count = page.locator("canvas").count()
            body_text = page.locator("body").inner_text(timeout=action_timeout_ms)[:2000]
            has_controls = any(
                token in body_text.lower()
                for token in ("score", "restart", "pause", "arrow", "wasd", "controls", "points",
                              "moves", "turn", "lives", "level", "status", "click", "win", "reset")
            )
            # A canvas, an explicit game container, or a DOM board (a grid of
            # cells: data-* cells or a container of 4+ equally sized children).
            dom_board = page.evaluate(_DOM_BOARD_JS)
            play_surface = (canvas_count > 0 or dom_board
                            or bool(page.locator("#game, .game, [data-game]").count()))

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
                    "Found a DOM game board" if dom_board else "Found non-canvas game container",
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

            # Interaction: snapshot after EACH input. Comparing only before/after a
            # whole sequence misses games where opposite moves cancel out.
            def snapshot() -> dict[str, Any]:
                return page.evaluate(_SNAPSHOT_JS)

            idle_a = snapshot()
            page.wait_for_timeout(300)
            idle_b = snapshot()
            canvas_animated = idle_a["canvas"] != idle_b["canvas"]
            text_animated = idle_a["text"] != idle_b["text"]
            reasons: list[str] = []
            previous = idle_b
            for key in ("ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp",
                        "KeyD", "KeyS", "Space", "Enter"):
                page.keyboard.down(key)
                page.wait_for_timeout(120)
                page.keyboard.up(key)
                page.wait_for_timeout(150)
                current = snapshot()
                if not canvas_animated and current["canvas"] != previous["canvas"]:
                    reasons.append(f"canvas_changed_after_{key}")
                if not text_animated and current["text"] != previous["text"]:
                    reasons.append(f"dom_text_changed_after_{key}")
                before_score = _extract_score(previous["text"])
                after_score = _extract_score(current["text"])
                if before_score is not None and after_score is not None and before_score != after_score:
                    reasons.append(f"score {before_score}->{after_score} after {key}")
                previous = current
            handled = page.evaluate("() => window.__agentKeyEvents || 0")
            if canvas_animated and handled:
                # Animated scenes change anyway; require the game's own key
                # handlers to have run while the frame kept updating.
                reasons.append(f"animated_canvas_and_{handled}_key_handler_calls")
            page.wait_for_timeout(200)
            page.screenshot(path=str(after_input), full_page=True)
            # Mouse games: click canvas points and clickable board elements.
            reasons.extend(_try_pointer_input(page, snapshot))
            # Pointer and restart-like controls are exercised after the key checks.
            for selector in ("button:has-text('Restart')", "button:has-text('Start')"):
                locator = page.locator(selector)
                if locator.count():
                    try:
                        before_click = snapshot()
                        locator.first.click(timeout=1000)
                        page.wait_for_timeout(200)
                        if snapshot() != before_click:
                            reasons.append("state_changed_after_button_click")
                    except Exception:
                        pass
                    break
            state_changed = bool(reasons)
            reasons = list(dict.fromkeys(reasons))[:12]

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
            if spec:
                remaining = budget - (time.monotonic() - started) - 2.0
                contract_block, requirement_blocks = _run_spec_scenarios(
                    page, spec, evidence_dir, remaining)
        finally:
            browser.close()

    if page_errors and functional_block["status"] == "pass":
        functional_block = _check(
            "fail", "JavaScript errors at runtime: " + " | ".join(page_errors[:5]),
            evidence_refs=list(functional_block.get("evidence_refs", [])),
        )
    checks = {
        "launch": launch_block,
        "functional": functional_block,
        "interaction": interaction_block,
        "technical": technical_block,
    }
    if contract_block is not None:
        checks["contract"] = contract_block
    return {
        "page_errors": page_errors[:20],
        "ok": all(
            block["status"] == "pass"
            for block in (launch_block, functional_block, interaction_block,
                          *requirement_blocks.values())
        ),
        "checks": checks,
        "requirements": requirement_blocks,
    }


_BROWSER_HINT = re.compile(r"http\.server|https?://|--port|\.html?\b")
_GRAPHICAL_LIBS = re.compile(r"^\s*(import|from)\s+(pygame|tkinter|pyglet|arcade)\b", re.M)
_EVENT_HANDLING = re.compile(r"KEYDOWN|key\.get_pressed|MOUSEBUTTONDOWN|\.bind\(|on_key_press|on_mouse_press")
_TERMINAL_INPUTS = ("", "w", "a", "s", "d", "1", "2", "3", "h", "y", "n", "left", "right", "q")


def _is_browser_launch(launch: list[str]) -> bool:
    return not launch or bool(_BROWSER_HINT.search(" ".join(launch)))


def _game_sources(cwd: Path) -> str:
    texts = []
    for path in sorted(cwd.rglob("*.py"))[:30]:
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace")[:200_000])
        except OSError:
            continue
    return "\n".join(texts)


def _run_process_checks(launch: list[str], cwd: Path, evidence_dir: Path, budget: float,
                        spec: dict[str, Any] | None) -> dict[str, Any]:
    """Basic verification for non-browser games (terminal or pygame-style).

    Checks that the program starts and keeps running without a traceback;
    terminal games are fed typed moves and must print more output in response.
    Graphical games run headless (SDL dummy drivers); their input cannot be
    injected, so interaction is judged from event-handling code and labelled as
    static evidence. Spec scenarios need a browser page and are not run here.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = evidence_dir / "game_stdout.log"
    stderr_path = evidence_dir / "game_stderr.log"
    env = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
           "PYGAME_HIDE_SUPPORT_PROMPT": "1", "PYTHONUNBUFFERED": "1"}
    sources = _game_sources(cwd)
    graphical = bool(_GRAPHICAL_LIBS.search(sources))
    read = lambda path: path.read_text(encoding="utf-8", errors="replace")  # noqa: E731
    refs = [f"log:{stdout_path.name}", f"log:{stderr_path.name}"]
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.Popen(launch, cwd=str(cwd), stdin=subprocess.PIPE, stdout=out,
                                stderr=err, env=env)
        try:
            time.sleep(min(3.0, max(budget * 0.2, 1.0)))
            first_exit = proc.poll()
            initial = read(stdout_path)
            interaction = _check("fail", "not attempted", state_changed=False)
            if graphical:
                if first_exit is None and _EVENT_HANDLING.search(sources):
                    interaction = _check(
                        "pass", "graphical game runs headless and handles input events "
                        "(static evidence only: input cannot be injected without a display)",
                        evidence_refs=["static:event-handling", *refs], state_changed=True)
                else:
                    interaction = _check("fail", "no keyboard/mouse event handling found or the "
                                         "program stopped", state_changed=False)
            elif first_exit is None:
                time.sleep(1.0)
                idle = read(stdout_path)
                for line in _TERMINAL_INPUTS:
                    if proc.poll() is not None or proc.stdin is None:
                        break
                    try:
                        proc.stdin.write((line + "\n").encode())
                        proc.stdin.flush()
                    except OSError:
                        break
                    time.sleep(0.25)
                time.sleep(0.5)
                after = read(stdout_path)
                idle_growth = len(idle) - len(initial)
                input_growth = len(after) - len(idle)
                if input_growth > max(20, 2 * idle_growth):
                    interaction = _check("pass", f"typed moves produced {input_growth} new output "
                                         f"characters (idle growth {idle_growth})",
                                         evidence_refs=refs, state_changed=True)
                else:
                    interaction = _check("fail", "typed moves on standard input did not change the "
                                         "output", evidence_refs=refs, state_changed=False)
            else:
                interaction = _check("fail", "program exited before any input could be given",
                                     evidence_refs=refs, state_changed=False)
        finally:
            _terminate(proc)
    stdout_text, stderr_text = read(stdout_path), read(stderr_path)
    traceback = "Traceback (most recent call last)" in stderr_text + stdout_text
    err_lines = stderr_text.strip().splitlines()
    # Final exception line first: it is what the planner needs to fix the bug.
    tail = " | ".join(err_lines[-1:] + err_lines[-3:-1])[:300]
    if first_exit not in (None, 0):
        launch_block = _check("fail", f"exited with code {first_exit}: {tail} "
                              f"(command: {' '.join(launch)[-120:]})", evidence_refs=refs)
    elif first_exit == 0 and not initial.strip():
        launch_block = _check("fail", "program exited immediately without output", evidence_refs=refs)
    else:
        launch_block = _check("pass", f"`{' '.join(launch)}` started and kept running"
                              + (" headless (SDL dummy)" if graphical else ""), evidence_refs=refs)
    if traceback:
        functional = _check("fail", f"Python traceback while running: {tail}", evidence_refs=refs)
    elif launch_block["status"] == "pass":
        functional = _check("pass", "ran without errors" + ("" if graphical else
                            f"; printed {len(stdout_text)} characters"), evidence_refs=refs)
    else:
        functional = _check("fail", "program did not run", evidence_refs=refs)
    requirements = {
        item["criterion_id"]: _check("fail", "spec scenarios need a browser page, but the launch "
                                     "command starts a non-browser program")
        for item in (spec or {}).get("scenarios", [])
    }
    return {
        "mode": "process",
        "page_errors": [tail] if traceback else [],
        "ok": all(b["status"] == "pass" for b in (launch_block, functional, interaction)),
        "checks": {"launch": launch_block, "functional": functional, "interaction": interaction,
                   "technical": _check("pass" if launch_block["status"] == "pass" else "fail",
                                       "non-browser program verified in process mode",
                                       evidence_refs=refs)},
        "requirements": requirements,
    }


def _is_favicon(msg: Any) -> bool:
    """Browsers request /favicon.ico on their own; its 404 is not a game error."""
    try:
        return "favicon" in str((msg.location or {}).get("url", "")).lower()
    except Exception:
        return False


_CLICKABLE = ("[data-cell]", "[data-index]", ".cell", ".card", ".tile", "td",
              "[role=button]", "button:not(:has-text('Restart')):not(:has-text('Reset'))"
              ":not(:has-text('New'))")


def _try_pointer_input(page: Any, snapshot: Any) -> list[str]:
    """Click canvas points and the first board elements; report state changes."""
    reasons: list[str] = []
    targets: list[tuple[str, Any]] = []
    box = page.locator("canvas").first.bounding_box() if page.locator("canvas").count() else None
    if box:
        for fx, fy in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
            targets.append((f"canvas@{fx},{fy}",
                            (box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)))
    for selector in _CLICKABLE:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 2)):
                if locator.nth(index).is_visible():
                    targets.append((f"{selector}#{index}", locator.nth(index)))
        except Exception:
            continue
        if len(targets) >= 9:
            break
    for label, target in targets[:9]:
        try:
            before = snapshot()
            if isinstance(target, tuple):
                page.mouse.click(*target)
            else:
                target.click(timeout=800)
            page.wait_for_timeout(180)
            if snapshot() != before:
                reasons.append(f"state_changed_after_click_{label}")
        except Exception:
            continue
    return reasons


_STATE_JS = """() => { const g = window.__gameState;
  const s = typeof g === 'function' ? g() : g;
  if (s === undefined || s === null) return null;
  try { return JSON.parse(JSON.stringify(s)); } catch (e) { return {unserializable: String(e)}; } }"""

_EVAL_JS = """([expr, saved]) => { const g = window.__gameState;
  const s = typeof g === 'function' ? g() : g;
  return (new Function('s', 'saved', 'return (' + expr + ');'))(s, saved); }"""

# Free names may be the injected `s` / `saved`, language values, page globals
# (`document`, `window`, `globalThis`), or arrow/function parameters.
# A bare `before` is still a test-script error; `saved.before` is a property.
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_ALLOWED_FREE = frozenset({
    "s", "saved",
    "true", "false", "null", "undefined", "NaN", "Infinity",
    "Math", "Number", "String", "Boolean", "Object", "Array", "JSON", "Date",
    "isNaN", "isFinite", "parseInt", "parseFloat",
})
_PAGE_GLOBALS = frozenset({"document", "window", "globalThis"})
_KEYWORDS = frozenset({
    "function", "return", "typeof", "instanceof", "new", "void",
    "in", "of", "if", "else",
})
_BALL_DROP_STEP_MS = 50
_BALL_DROP_MAX_MS = 2000
_BALL_DROP_SETTLE_MS = 50
# Life is deducted only after the ball centre crosses the bottom (b.y - BALL_R > H).
_BALL_PAST_BOTTOM_JS = """() => {
  const g = window.__gameState;
  const s = typeof g === 'function' ? g() : g;
  if (!s) return false;
  const b = s.ball || s.b;
  if (!b || typeof b.y !== 'number') return false;
  const r = typeof b.r === 'number' ? b.r
    : typeof b.radius === 'number' ? b.radius
    : typeof s.BALL_R === 'number' ? s.BALL_R
    : typeof s.ballRadius === 'number' ? s.ballRadius
    : null;
  const height = typeof s.H === 'number' ? s.H
    : typeof s.height === 'number' ? s.height
    : typeof s.canvasHeight === 'number' ? s.canvasHeight
    : null;
  if (r === null || height === null) return false;
  return (b.y - r) > height;
}"""


def _bound_names(masked: str) -> set[str]:
    """Parameter names of arrows and function expressions in one assertion."""
    names: set[str] = set()
    for match in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>", masked):
        names.add(match.group(1))
    for match in re.finditer(r"\(([^()]*)\)\s*=>", masked):
        names.update(_IDENT.findall(match.group(1)))
    for match in re.finditer(
            r"\bfunction\b\s*(?:[A-Za-z_$][\w$]*)?\s*\(([^()]*)\)", masked):
        names.update(_IDENT.findall(match.group(1)))
    return names


def unbound_assertion_names(expr: str) -> list[str]:
    """Free identifiers that are not injected, page globals, or parameters.

    Property names (the identifier after ``.``) are ignored, so ``saved.before``
    is legal. A bare ``before`` is not.
    """
    masked = _mask_js_literals(expr)
    bound = _bound_names(masked)
    found: list[str] = []
    for match in _IDENT.finditer(masked):
        name = match.group(0)
        previous = match.start() - 1
        while previous >= 0 and masked[previous] in " \t\n\r":
            previous -= 1
        if previous >= 0 and masked[previous] == ".":
            continue
        if (name in _ALLOWED_FREE or name in _PAGE_GLOBALS or name in _KEYWORDS
                or name in bound or name in found):
            continue
        found.append(name)
    return found


def _mask_js_literals(expr: str) -> str:
    out: list[str] = []
    index = 0
    length = len(expr)
    while index < length:
        char = expr[index]
        if char in "\"'`":
            quote = char
            out.append(" ")
            index += 1
            while index < length:
                if expr[index] == "\\":
                    out.append("  ")
                    index += 2
                    continue
                if expr[index] == quote:
                    out.append(" ")
                    index += 1
                    break
                out.append(" ")
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _assertion_script_error(expr: str) -> str | None:
    names = unbound_assertion_names(expr)
    if not names:
        return None
    return ("test_script_error: unbound name(s): "
            f"{', '.join(names)}")


def _is_reference_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return "ReferenceError" in text or "is not defined" in text


def _is_ball_drop_setup(hook_name: str) -> bool:
    compact = re.sub(r"[^A-Za-z0-9]", "", hook_name).lower()
    return "aimballbelowpaddle" in compact or ("ball" in compact and "below" in compact)


def _wait_until_ball_past_bottom(page: Any) -> None:
    """Poll until ``b.y - BALL_R > H``, then one short settle. Capped."""
    waited = 0
    while waited < _BALL_DROP_MAX_MS:
        if page.evaluate(_BALL_PAST_BOTTOM_JS):
            page.wait_for_timeout(_BALL_DROP_SETTLE_MS)
            return
        page.wait_for_timeout(_BALL_DROP_STEP_MS)
        waited += _BALL_DROP_STEP_MS


def _evaluate_assertion(page: Any, expr: str, saved: dict[str, Any]) -> Any:
    problem = _assertion_script_error(expr)
    if problem:
        raise _TestScriptError(problem)
    try:
        return page.evaluate(_EVAL_JS, [expr, saved])
    except _TestScriptError:
        raise
    except Exception as exc:  # noqa: BLE001 - unbound names arrive as ReferenceError
        if _is_reference_error(exc):
            raise _TestScriptError(f"test_script_error: {type(exc).__name__}: {exc}") from exc
        raise


class _TestScriptError(Exception):
    """The assertion script is invalid. This is not a game failure."""

_DOM_BOARD_JS = """() => {
  if (document.querySelector('[data-cell],[data-col],[data-x],[data-index]')) return true;
  return [...document.querySelectorAll('body *')].some(e => {
    const kids = [...e.children];
    if (kids.length < 4) return false;
    const r0 = kids[0].getBoundingClientRect();
    return r0.width > 8 && kids.every(k => { const r = k.getBoundingClientRect();
      return Math.abs(r.width - r0.width) < 2 && Math.abs(r.height - r0.height) < 2; });
  });
}"""

_MISSING_FIELDS_JS = """(fields) => { const g = window.__gameState;
  const s = typeof g === 'function' ? g() : g;
  if (s === undefined || s === null) return fields;
  return fields.filter(f => !(f in Object(s))); }"""

# Grid cell centre for click_cell: a canvas board (state.grid gives the size),
# or a DOM board (data-* attributes, or a container whose children are cells).
_CELL_JS = """([col, row]) => { const g = window.__gameState;
  const s = typeof g === 'function' ? g() : g;
  const grid = s && s.grid;
  const centre = (r) => [r.left + r.width / 2, r.top + r.height / 2];
  const tagged = document.querySelector(`[data-cell="${col},${row}"]`)
    || document.querySelector(`[data-col="${col}"][data-row="${row}"]`)
    || document.querySelector(`[data-x="${col}"][data-y="${row}"]`)
    || document.querySelector(`[data-c="${col}"][data-r="${row}"]`);
  if (tagged) return centre(tagged.getBoundingClientRect());
  const c = document.querySelector('canvas');
  if (c && grid) { const r = c.getBoundingClientRect();
    return [r.left + (col + 0.5) * r.width / grid.cols, r.top + (row + 0.5) * r.height / grid.rows]; }
  if (grid) { const n = grid.cols * grid.rows;
    const box = [...document.querySelectorAll('body *')].find(e => e.children.length === n
      && [...e.children].every(k => k.getBoundingClientRect().width > 0));
    if (box) return centre(box.children[row * grid.cols + col].getBoundingClientRect()); }
  throw new Error('click_cell needs state.grid {cols, rows} and a canvas board or a DOM grid of cells'); }"""


def _describe_step(step: dict[str, Any]) -> str:
    return json.dumps(step, ensure_ascii=False)[:160]


def _run_step(page: Any, step: dict[str, Any], saved: dict[str, Any]) -> str | None:
    """Run one scenario step. Returns an error message, or None on success."""
    if "key" in step:
        for _ in range(int(step.get("repeat", 1))):
            page.keyboard.down(step["key"])
            page.wait_for_timeout(50)
            page.keyboard.up(step["key"])
            page.wait_for_timeout(40)
    elif "hold" in step:
        page.keyboard.down(step["hold"])
        page.wait_for_timeout(int(step.get("ms", 300)))
        page.keyboard.up(step["hold"])
    elif "click" in step:
        page.locator(step["click"]).first.click(timeout=1500)
    elif "click_at" in step:
        box = page.locator("canvas").first.bounding_box()
        if not box:
            return "click_at needs a visible canvas"
        fx, fy = step["click_at"]
        page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
    elif "click_cell" in step:
        x, y = page.evaluate(_CELL_JS, step["click_cell"])
        page.mouse.click(x, y)
    elif "call" in step:
        exists = page.evaluate("(fn) => !!(window.__gameTest && typeof window.__gameTest[fn] === 'function')",
                               step["call"])
        if not exists:
            return f"window.__gameTest.{step['call']} is not defined (state contract)"
        page.evaluate("([fn, args]) => window.__gameTest[fn](...args)",
                      [step["call"], step.get("args", [])])
        # aimBallBelowPaddle() only parks the ball on screen. A life is lost
        # once the centre crosses the bottom, which the fixed 120ms pause may miss.
        if _is_ball_drop_setup(str(step["call"])):
            _wait_until_ball_past_bottom(page)
    elif "wait" in step:
        page.wait_for_timeout(int(step["wait"]))
    elif "save" in step:
        try:
            saved[step["save"]] = _evaluate_assertion(page, step["expr"], saved)
        except _TestScriptError as exc:
            return str(exc)
    elif "expect" in step:
        try:
            held = _evaluate_assertion(page, step["expect"], saved)
        except _TestScriptError as exc:
            return str(exc)
        if not held:
            return f"expectation false: {step['expect']}"
    if any(name in step for name in ("key", "hold", "click", "click_at", "click_cell", "call")):
        page.wait_for_timeout(120)  # let animation frames apply the input
    return None


def _run_spec_scenarios(page: Any, spec: dict[str, Any], evidence_dir: Path,
                        budget: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check the state contract, then run each requirement scenario on a fresh load."""
    deadline = time.monotonic() + max(budget, 0.0)
    contract = spec.get("contract") or {}
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        state = page.evaluate(_STATE_JS)
        hooks = page.evaluate("() => Object.keys(window.__gameTest || {}).filter("
                              "k => typeof window.__gameTest[k] === 'function')")
        # Checked on the live object: JSON copies drop function-valued fields
        # such as isWall(x, y), which a contract may legitimately require.
        live_missing = page.evaluate(_MISSING_FIELDS_JS, list(contract.get("state_fields", {})))
    except Exception as exc:  # noqa: BLE001
        state, hooks, live_missing = None, [], None
        contract_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    else:
        contract_error = ""
    missing = list(contract.get("state_fields", {})) if state is None else list(live_missing or [])
    missing_hooks = [h for h in contract.get("test_hooks", {}) if h not in hooks]
    if state is None:
        contract_block = _check("fail", "window.__gameState is not defined; the state contract "
                                "requires it to expose the real game state " + contract_error)
    elif missing or missing_hooks:
        contract_block = _check("fail", f"state contract incomplete: missing fields {missing}, "
                                f"missing hooks {missing_hooks}")
    else:
        contract_block = _check("pass", "window.__gameState and __gameTest match the contract",
                                evidence_refs=["contract:state_fields", "contract:test_hooks"])

    blocks: dict[str, Any] = {}
    for scenario in spec.get("scenarios", []):
        cid = scenario["criterion_id"]
        if time.monotonic() > deadline:
            blocks[cid] = _check("fail", "not run: harness time budget exhausted")
            continue
        saved: dict[str, Any] = {}
        failure = None
        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(250)
            if page.evaluate(_STATE_JS) is None:
                failure = "window.__gameState is not defined"
            for number, step in enumerate(scenario.get("steps", []), 1):
                if failure or time.monotonic() > deadline:
                    failure = failure or "harness time budget exhausted mid-scenario"
                    break
                error = _run_step(page, step, saved)
                if error:
                    failure = f"step {number} {_describe_step(step)}: {error}"
                    break
        except _TestScriptError as exc:
            failure = str(exc)
        except Exception as exc:  # noqa: BLE001 - a broken scenario is a failed criterion
            failure = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        shot = evidence_dir / f"req-{re.sub('[^A-Za-z0-9_-]', '_', cid)}.png"
        try:
            page.screenshot(path=str(shot))
        except Exception:
            pass
        if failure is None:
            blocks[cid] = _check("pass", f"scenario passed: {scenario.get('description', '')[:120]}",
                                 evidence_refs=[f"screenshot:{shot.name}", f"scenario:{cid}"])
        else:
            snapshot_text = ""
            try:
                snapshot_text = json.dumps(page.evaluate(_STATE_JS))[:300]
            except Exception:
                pass
            # test_script_error is not a game FAIL. Member 5 must not REPAIR the game.
            status = "unknown" if "test_script_error" in failure else "fail"
            blocks[cid] = _check(status, f"{failure}; state after: {snapshot_text}",
                                 evidence_refs=[f"screenshot:{shot.name}"])
    return contract_block, blocks


# Counts calls into the game's own keyboard handlers (registered via
# addEventListener or on* properties) without changing their behaviour.
_KEY_HOOK_JS = """
(() => {
  window.__agentKeyEvents = 0;
  const KEYS = new Set(['keydown', 'keyup', 'keypress']);
  const original = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, listener, options) {
    if (KEYS.has(type) && typeof listener === 'function') {
      const wrapped = function (...args) { window.__agentKeyEvents++; return listener.apply(this, args); };
      return original.call(this, type, wrapped, options);
    }
    return original.call(this, type, listener, options);
  };
  for (const target of [window, document]) {
    original.call(target, 'keydown', () => {
      if (target.onkeydown) window.__agentKeyEvents++;
    }, true);
  }
})();
"""

# Text plus a cheap hash of every canvas (full surface, not a corner).
_SNAPSHOT_JS = """() => {
  const hash = (s) => { let h = 0; for (let i = 0; i < s.length; i += 7) h = (h * 31 + s.charCodeAt(i)) | 0; return h; };
  const canvases = [...document.querySelectorAll('canvas')].map((c) => {
    try { return hash(c.toDataURL()); } catch (e) { return 'unreadable'; }
  });
  return {text: (document.body ? document.body.innerText : '').slice(0, 2000), canvas: canvases.join(',')};
}"""


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
    parser.add_argument(
        "--evidence-dir", type=str, default=os.environ.get("AGENT_ARTIFACT_DIR", ""),
        help="Screenshot directory; defaults to the runtime-provided AGENT_ARTIFACT_DIR",
    )
    parser.add_argument("--url", type=str, default="")
    parser.add_argument("--scenarios-json", type=str, default="",
                        help="State contract and requirement scenarios from the verifier")
    parser.add_argument(
        "--launch",
        nargs=argparse.REMAINDER,
        default=[],
        help="Launch argv after this flag (example: --launch python -m http.server 8765)",
    )
    args = parser.parse_args(argv)

    deadline = time.monotonic() + max(5.0, args.timeout_seconds)
    cwd = Path.cwd()
    if args.evidence_dir:
        evidence_dir = Path(args.evidence_dir)
    else:
        # Never write evidence into game/: it would change the workspace revision.
        import tempfile
        evidence_dir = Path(tempfile.mkdtemp(prefix="harness-evidence-"))
    launch = [part for part in (args.launch or []) if part != "--"]

    proc: subprocess.Popen[Any] | None = None
    url = args.url
    note = ""
    try:
        remaining = deadline - time.monotonic()
        if not url and not _is_browser_launch(launch):
            spec = json.loads(args.scenarios_json) if args.scenarios_json else None
            result = _run_process_checks(launch, cwd, evidence_dir, remaining, spec)
            result["launch_argv"] = launch
            result["artifacts"] = sorted(str(p) for p in evidence_dir.iterdir() if p.is_file())
            _print_json(result)
            return 0
        if not url:
            proc, url, note = _start_launch(launch, cwd, remaining, evidence_dir / "game_server.log")
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

        spec = json.loads(args.scenarios_json) if args.scenarios_json else None
        result = _run_playwright(url, evidence_dir, remaining, spec)
        result["note"] = note
        result["url"] = url
        result["launch_argv"] = launch
        result["artifacts"] = sorted(str(path) for path in evidence_dir.iterdir() if path.is_file())
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
