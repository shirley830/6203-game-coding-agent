"""Generate the animated SVG artwork used by README.md.

Run from the repository root:  python3 assets/src/build_readme_art.py

Everything is plain SVG + CSS animation (GitHub renders animated SVG images
but no scripts). Every animation is disabled under prefers-reduced-motion, and
the un-animated state of every element is a sensible final frame. Numbers shown
in the artwork come from real runs documented in docs/testing/LIVE_MODEL_TESTS.md.
"""

from __future__ import annotations

import base64
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets"
SHOTS = Path(__file__).resolve().parent / "shots"

FONT = "Segoe UI, -apple-system, BlinkMacSystemFont, Helvetica Neue, Helvetica, Arial, sans-serif"
MONO = "SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace"

C = {
    "bg0": "#0f2a33", "bg1": "#0a1d24", "bg2": "#06141a", "line": "#2b4a54", "card": "#10262e",
    "card2": "#133039", "stroke": "#33545e", "text": "#f2f8f9", "muted": "#a9c2c9", "dim": "#7fa3ad",
    "faint": "#5f7f88", "teal": "#6ff0cf", "blue": "#38b6e8", "ok": "#4fd18b", "warn": "#f5c451",
    "err": "#ff8a7a", "okbg": "#123229", "warnbg": "#33290f", "errbg": "#3a1d1d",
}


# ---------------------------------------------------------------- helpers

def kf(name: str, windows: list[tuple[float, float]], prop: str = "opacity",
       on: str = "1", off: str = "0", edge: float = 0.8) -> str:
    """Keyframes that switch `prop` on inside each (start, end) percentage window."""
    stops = [f"0%{{{prop}:{off}}}"]
    for start, end in windows:
        stops += [f"{start:.2f}%{{{prop}:{off}}}", f"{start + edge:.2f}%{{{prop}:{on}}}",
                  f"{end:.2f}%{{{prop}:{on}}}", f"{end + edge:.2f}%{{{prop}:{off}}}"]
    stops.append(f"100%{{{prop}:{off}}}")
    return f"@keyframes {name}{{{''.join(stops)}}}"


def page(width: int, height: int, title: str, desc: str, body: str, css: str = "",
         radius: int = 20, glow: tuple[float, float] = (0.15, 0.2)) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">
<title id="t">{escape(title)}</title>
<desc id="d">{escape(desc)}</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{C['bg0']}"/><stop offset="0.55" stop-color="{C['bg1']}"/><stop offset="1" stop-color="{C['bg2']}"/>
  </linearGradient>
  <radialGradient id="glow" cx="{glow[0]}" cy="{glow[1]}" r="0.7">
    <stop offset="0" stop-color="#2ad1a8" stop-opacity="0.17"/><stop offset="1" stop-color="#2ad1a8" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{C['teal']}"/><stop offset="1" stop-color="{C['blue']}"/>
  </linearGradient>
  <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{C['teal']}" stop-opacity="0"/><stop offset="0.5" stop-color="{C['teal']}" stop-opacity="0.9"/><stop offset="1" stop-color="{C['blue']}" stop-opacity="0"/>
  </linearGradient>
  <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="#fff" stroke-opacity="0.035"/></pattern>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="140%"><feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#000" flood-opacity="0.45"/></filter>
  <filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter>
  <clipPath id="frame"><rect width="{width}" height="{height}" rx="{radius}"/></clipPath>
</defs>
<style>
  text {{ font-family: {FONT}; }}
  .mono {{ font-family: {MONO}; }}
  .flow {{ stroke-dasharray: 6 10; animation: dash 1.1s linear infinite; }}
  @keyframes dash {{ to {{ stroke-dashoffset: -32; }} }}
  .blink {{ animation: blink 1.1s steps(1) infinite; }}
  @keyframes blink {{ 50% {{ opacity: 0; }} }}
  .pulse {{ transform-box: fill-box; transform-origin: center; animation: pulse 2.4s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity: .25; transform: scale(1); }} 50% {{ opacity: .9; transform: scale(1.05); }} }}
  .sweep {{ animation: sweep 5s ease-in-out infinite; }}
  @keyframes sweep {{ 0% {{ transform: translateX(-420px); }} 100% {{ transform: translateX({width + 40}px); }} }}
  {css}
  @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
</style>
<g clip-path="url(#frame)">
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect width="{width}" height="{height}" fill="url(#grid)"/>
  <rect width="{width}" height="{height}" fill="url(#glow)"/>
{body}
</g>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="{radius - 0.5}" fill="none" stroke="{C['line']}"/>
</svg>
"""


def write(name: str, content: str) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"{name:34s} {len(content.encode()) / 1024:7.1f} KiB")


def card(x, y, w, h, fill=None, stroke=None, rx=12, extra=""):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill or C["card"]}" stroke="{stroke or C["stroke"]}" {extra}/>')


def t(x, y, s, size=15, fill=None, weight=400, anchor="start", cls="", extra=""):
    c = f' class="{cls}"' if cls else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill or C["text"]}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{c} {extra}>{s}</text>')


# ---------------------------------------------------------------- section headers

SECTIONS = [
    ("01", "quick-start", "Quick start", "Install once, drop in a spec, run one command."),
    ("02", "games", "Games it built, live", "Real output of autonomous runs. Nothing here was written by hand."),
    ("03", "how-it-works", "How it works", "One controller decides. Six modules propose, act, check and record."),
    ("04", "verification", "How a game is verified", "Tests are written from the spec before the code exists, then played in a real browser."),
    ("05", "inside-a-run", "Inside a run", "A bounded state machine: plan, build, verify, recover, deliver."),
    ("06", "run-output", "What a run leaves behind", "A playable game, and a trace a stranger can read afterwards."),
    ("07", "budgets", "Budgets and outcomes", "Every run ends, on time, with an honest verdict."),
    ("08", "team", "Team", "Six members, six modules, one contract."),
    ("09", "testing", "Testing", "Unit, integration, browser and live-model evidence."),
]


def section_header(number: str, slug: str, title: str, subtitle: str) -> None:
    body = f"""
  <rect x="0" y="0" width="420" height="140" fill="url(#sweep)" opacity="0.07" class="sweep"/>
  <text x="54" y="96" font-size="74" font-weight="800" fill="url(#accent)" letter-spacing="-2">{number}</text>
  <line x1="168" y1="38" x2="168" y2="104" stroke="{C['line']}" stroke-width="2"/>
  {t(196, 70, escape(title), 34, weight=700)}
  {t(197, 104, escape(subtitle), 17, C['muted'])}
  <g transform="translate(1176 52)">
    <circle r="5" fill="{C['teal']}" class="pulse"/>
    <circle cx="22" r="5" fill="{C['teal']}" opacity=".5" class="pulse" style="animation-delay:.4s"/>
    <circle cx="44" r="5" fill="{C['blue']}" opacity=".35" class="pulse" style="animation-delay:.8s"/>
  </g>
  <rect x="0" y="136" width="380" height="4" fill="url(#sweep)" class="sweep"/>"""
    write(f"sections/{number}-{slug}.svg",
          page(1280, 140, f"{number} {title}", subtitle, body, radius=16, glow=(0.05, 0.3)))


# ---------------------------------------------------------------- hero banner

def banner() -> None:
    cycle = 16
    # Terminal lines: (icon, colour, label, value, value colour, reveal %)
    lines = [
        ("✓", C["ok"], "preflight", "sandbox, Chromium, port 8000 ready", C["text"], 12),
        ("✓", C["ok"], "acceptance", "8 spec scenarios frozen", C["text"], 21),
        ("✓", C["ok"], "plan", "7 tasks · write index.html, game.js", C["text"], 30),
        ("✗", C["err"], "verify", "13/14 · style.css missing (404)", C["text"], 41),
        ("↻", C["warn"], "repair", "inline the CSS into index.html", C["text"], 52),
        ("✓", C["ok"], "verify", "14/14 criteria pass", C["teal"], 63),
    ]
    css = [f".tl{{animation-duration:{cycle}s;animation-iteration-count:infinite;}}",
           kf("type", [(1, 95)], "width", "560px", "0px", edge=9)]
    term = []
    for i, (icon, colour, label, value, vcol, at) in enumerate(lines):
        y = 140 + i * 27
        css.append(kf(f"l{i}", [(at, 95)]))
        bold = ' font-weight="700"' if label == "verify" and vcol == C["teal"] else ""
        term.append(f'<text x="712" y="{y}" font-size="14.5" class="mono tl" style="animation-name:l{i}">'
                    f'<tspan fill="{colour}">{icon}</tspan><tspan x="732" fill="{C["dim"]}">{label}</tspan>'
                    f'<tspan x="834" fill="{vcol}"{bold}>{escape(value)}</tspan></text>')
    css.append(kf("l6", [(74, 95)]))
    term.append(f'<text x="712" y="{140 + 6 * 27 + 8}" font-size="14.5" class="mono tl" style="animation-name:l6" fill="{C["dim"]}">'
                f'→ <tspan fill="{C["text"]}">game/</tspan><tspan x="790" fill="{C["text"]}">traces/&lt;run_id&gt;/run_report.md</tspan></text>')

    stages = [("01 · RUNTIME", "Read the spec", [(12, 21)]), ("02 · VERIFIER", "Freeze acceptance", [(21, 30)]),
              ("03 · PLANNER", "Plan and build", [(30, 41)]), ("04 · HARNESS", "Test in a browser", [(41, 52), (63, 74)]),
              ("05 · RECOVERY", "Repair or replan", [(52, 63)]), ("06 · TRACING", "Deliver + trace", [(74, 95)])]
    pipe = []
    for i, (tag, label, windows) in enumerate(stages):
        x = 56 + i * 198
        w = 180 if i == 5 else 178
        css.append(kf(f"s{i}", windows))
        pipe.append(card(x, 384, w, 58, C["card"], C["stroke"], 10))
        lit = card(x, 384, w, 58, C["okbg"], C["teal"], 10, 'stroke-width="1.5"')
        pipe.append(f'<g class="tl" style="animation-name:s{i};opacity:0">{lit}'
                    f'<rect x="{x + 10}" y="384" width="{w - 20}" height="2" fill="url(#accent)"/></g>')
        pipe.append(t(x + 16, 405, tag, 10.5, C["dim"], extra='letter-spacing="1.2"'))
        pipe.append(t(x + 16, 428, label, 15, weight=600))

    body = f"""
  <g>
    <rect x="56" y="44" width="10" height="10" rx="2" fill="{C['teal']}" class="pulse"/>
    {t(76, 54, "PE6203 · GENERATIVE AND AGENTIC AI · GROUP PROJECT", 13, C['muted'], 600, extra='letter-spacing="2.4"')}
    {t(54, 124, "From an unseen spec", 52, weight=800, extra='letter-spacing="-1"')}
    {t(54, 186, "to a verified game.", 52, "url(#accent)", 800, extra='letter-spacing="-1"')}
    {t(56, 226, "An autonomous coding agent that plans, builds, tests in a real", 18, C['muted'])}
    {t(56, 252, "browser, repairs what fails, and keeps a complete trace.", 18, C['muted'])}
    {t(56, 296, "MODULES", 12, C['dim'], extra='letter-spacing="1.2"')}{t(196, 296, "TESTS", 12, C['dim'], extra='letter-spacing="1.2"')}{t(316, 296, "LIVE RUNS", 12, C['dim'], extra='letter-spacing="1.2"')}
    <text x="56" y="328" font-size="28" font-weight="700" fill="{C['text']}">6<tspan fill="{C['faint']}" font-size="18"> / 6</tspan></text>
    <text x="196" y="328" font-size="28" font-weight="700" fill="{C['text']}">314</text>
    <text x="316" y="328" font-size="28" font-weight="700" fill="{C['text']}">62<tspan fill="{C['faint']}" font-size="16" font-weight="600"> · 5 models</tspan></text>
  </g>
  <rect x="660" y="20" width="600" height="340" rx="40" fill="{C['teal']}" opacity="0.05" filter="url(#soft)"/>
  <g filter="url(#shadow)">{card(690, 40, 536, 316, '#08161c', '#2f505a', 14)}</g>
  <path d="M690 54a14 14 0 0 1 14-14h508a14 14 0 0 1 14 14v22H690z" fill="#10242c"/>
  <line x1="690" y1="76" x2="1226" y2="76" stroke="#2f505a"/>
  <circle cx="712" cy="58" r="5.5" fill="#ff6b6b" fill-opacity=".85"/><circle cx="731" cy="58" r="5.5" fill="#f5c451" fill-opacity=".85"/><circle cx="750" cy="58" r="5.5" fill="#4fd18b" fill-opacity=".85"/>
  {t(958, 62, "live run · claude-sonnet-5.5 · collection game", 12, C['dim'], anchor='middle')}
  <clipPath id="typing"><rect x="700" y="90" height="30" width="560" class="tl" style="animation-name:type"/></clipPath>
  <text x="712" y="108" font-size="14.5" class="mono" fill="{C['teal']}" clip-path="url(#typing)">$ <tspan fill="{C['text']}">python -m agent game_design_prompt.md</tspan></text>
  {''.join(term)}
  <rect x="712" y="324" width="9" height="16" fill="{C['teal']}" opacity=".8" class="blink"/>
  {t(56, 368, "ONE CONTROLLER · SIX MODULES · BOUNDED BY TIME, STEPS AND RETRIES", 12, C['faint'], extra='letter-spacing="1.2"')}
  <line x1="56" y1="413" x2="1226" y2="413" stroke="{C['line']}" stroke-dasharray="3 5"/>
  {''.join(pipe)}"""
    write("readme-banner.svg", page(
        1280, 470, "PE6203 Game Coding Agent",
        "An autonomous coding agent that reads an unseen game specification, freezes executable "
        "acceptance tests, plans and builds the game, verifies it in a real browser, repairs failures, "
        "and preserves the game and a complete trace. The terminal replays a real run: 8 spec "
        "scenarios, a failed check, a repair, and 14 of 14 criteria passing.",
        body, "\n".join(css)))


# ---------------------------------------------------------------- at a glance

def glance() -> None:
    panels = [
        ("IN", "One unseen spec", ["game_design_prompt.md", "nothing else, no human help"], C["blue"]),
        ("PROCESS", "Autonomous build loop", ["freeze tests → plan → write code", "play in a browser → repair"], C["teal"]),
        ("OUT", "Game + trace", ["game/ with a launch command", "traces/ with every decision"], C["ok"]),
    ]
    body = []
    for i, (tag, title, lines, colour) in enumerate(panels):
        x = 40 + i * 410
        body.append(card(x, 34, 380, 172, C["card"], C["stroke"], 14))
        body.append(f'<rect x="{x}" y="34" width="380" height="3" rx="1.5" fill="{colour}" opacity=".9"/>')
        body.append(t(x + 26, 72, tag, 12, colour, 700, extra='letter-spacing="2"'))
        body.append(t(x + 26, 108, title, 24, weight=700))
        for j, line in enumerate(lines):
            body.append(t(x + 26, 146 + j * 28, escape(line), 15.5, C["muted"], cls="mono" if j == 0 and i != 1 else ""))
        if i < 2:
            ax = x + 380
            body.append(f'<path d="M{ax + 4} 120 H{ax + 26}" stroke="{C["teal"]}" stroke-width="2.5" class="flow"/>')
            body.append(f'<path d="M{ax + 20} 113 l8 7 -8 7" fill="none" stroke="{C["teal"]}" stroke-width="2.5"/>')
    write("glance.svg", page(1280, 240, "At a glance",
                             "In: one unseen spec. Process: freeze tests, plan, write code, play it in a browser, "
                             "repair. Out: a playable game and a complete trace.", "\n".join(body), radius=18))


# ---------------------------------------------------------------- architecture

def architecture() -> None:
    def node(x, y, w, h, tag, title, sub, colour):
        return (card(x, y, w, h, C["card"], C["stroke"], 12)
                + f'<rect x="{x}" y="{y + 10}" width="3" height="{h - 20}" rx="1.5" fill="{colour}"/>'
                + t(x + 20, y + 26, tag, 11, colour, 700, extra='letter-spacing="1.6"')
                + t(x + 20, y + 52, title, 18, weight=700) + t(x + 20, y + 74, sub, 13, C["muted"]))

    def link(d, colour, width=2, animated=True, marker=True):
        cls = ' class="flow"' if animated else ""
        end = ' marker-end="url(#arrow)"' if marker else ""
        return f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}"{cls}{end}/>'

    body = f"""
  <defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="7" refY="5" orient="auto"><path d="M0 0 L8 5 L0 10" fill="none" stroke="{C['teal']}" stroke-width="1.6"/></marker></defs>
  {t(64, 50, "ADVISORS · PROPOSE AND CHECK", 12, C['dim'], 600, extra='letter-spacing="1.6"')}
  {t(640, 50, "DECIDES", 12, C['dim'], 600, 'middle', extra='letter-spacing="1.6"')}
  {t(1216, 50, "EXECUTORS · ACT AND RECORD", 12, C['dim'], 600, 'end', extra='letter-spacing="1.6"')}

  <!-- spec input -->
  <rect x="540" y="72" width="200" height="40" rx="20" fill="{C['card2']}" stroke="{C['blue']}"/>
  {t(640, 97, "game_design_prompt.md", 14, C['text'], 600, 'middle', cls='mono')}
  {link("M640 114 V168", C['blue'])}

  <!-- controller -->
  <rect x="486" y="164" width="308" height="192" rx="24" fill="none" stroke="{C['teal']}" stroke-width="2" opacity=".35" class="pulse"/>
  <g filter="url(#shadow)"><rect x="500" y="176" width="280" height="168" rx="18" fill="#0b1f26" stroke="{C['teal']}" stroke-width="2"/></g>
  {t(640, 216, "CONTROLLER · MEMBER 1", 11.5, C['teal'], 700, 'middle', extra='letter-spacing="1.8"')}
  {t(640, 254, "Finite-state controller", 22, weight=800, anchor='middle')}
  {t(640, 284, "legal transitions · budgets", 14, C['muted'], anchor='middle')}
  {t(640, 306, "one tool gateway · success gate", 14, C['muted'], anchor='middle')}
  {t(640, 330, "the only thing that can say SUCCEEDED", 12, C['dim'], anchor='middle')}

  <!-- advisors (left) -->
  {node(64, 92, 300, 92, "PLANNER · M2", "Plan and decide", "LLM proposes the next action", C['teal'])}
  {node(64, 214, 300, 92, "VERIFIER · M4", "Acceptance + browser", "writes tests, plays the game", C['blue'])}
  {node(64, 336, 300, 92, "RECOVERY · M5", "Repair or replan", "classifies every failure", C['warn'])}
  {link("M498 216 C430 216 420 138 366 138", C['teal'])}
  {link("M366 150 C420 150 430 228 496 228", C['teal'], 1.5, True, True)}
  {link("M498 262 H366", C['blue'])}
  {link("M498 306 C430 306 420 382 366 382", C['warn'])}

  <!-- executors (right) -->
  {node(916, 120, 300, 92, "RUNTIME · M3", "Sandboxed tools", "files · commands · installs", C['ok'])}
  {node(916, 300, 300, 92, "TRACING · M6", "Durable record", "every event, screenshot, call", C['teal'])}
  {link("M782 226 C850 226 860 166 912 166", C['ok'], 2.5)}
  {link("M782 296 C850 296 860 346 912 346", C['teal'], 2.5)}

  <!-- outputs -->
  <rect x="980" y="232" width="110" height="40" rx="20" fill="{C['okbg']}" stroke="{C['ok']}"/>{t(1035, 257, "game/", 15, C['ok'], 700, 'middle', cls='mono')}
  <rect x="1100" y="232" width="116" height="40" rx="20" fill="{C['card2']}" stroke="{C['teal']}"/>{t(1158, 257, "traces/", 15, C['teal'], 700, 'middle', cls='mono')}
  {link("M1035 214 V228", C['ok'], 2, False)}
  {link("M1158 298 V276", C['teal'], 2, False)}

  <line x1="64" y1="452" x2="1216" y2="452" stroke="{C['line']}" stroke-dasharray="3 5"/>
  {t(640, 482, "Modules never touch controller state · every action is validated, budgeted and traced · no mocks in production", 13.5, C['dim'], anchor='middle')}
"""
    write("architecture.svg", page(1280, 510, "Architecture",
          "A finite-state controller in the middle decides; the planner, verifier and recovery modules "
          "propose and check; the sandboxed runtime and tracing act and record, producing game/ and traces/.",
          body))


# ---------------------------------------------------------------- verification

def verification() -> None:
    cycle = 12
    css = [f".v{{animation-duration:{cycle}s;animation-iteration-count:infinite;}}",
           ".player{animation:move 12s ease-in-out infinite;} @keyframes move{0%,22%{transform:translateX(0)}32%,100%{transform:translateX(64px)}}",
           ".cursor{animation:cur 12s ease-in-out infinite;} @keyframes cur{0%,34%{transform:translate(0,0)}44%,100%{transform:translate(-118px,-118px)}}",
           kf("key", [(22, 32)]), kf("click", [(44, 48)])]
    checks = ["R1 arrow keys move the player", "R2 a pickup adds one point", "R3 win at 10 freezes input",
              "R4 restart resets score", "C-CONTRACT live game state"]
    items = []
    for i, label in enumerate(checks):
        y = 132 + i * 34
        css.append(kf(f"tick{i}", [(60 + i * 3, 95)]))
        items.append(f'<rect x="{352}" y="{y - 15}" width="20" height="20" rx="5" fill="none" stroke="{C["stroke"]}"/>')
        items.append(f'<g class="v" style="animation-name:tick{i}"><rect x="352" y="{y - 15}" width="20" height="20" rx="5" fill="{C["okbg"]}" stroke="{C["ok"]}"/>'
                     f'<path d="M357 {y - 5} l4 4 8 -9" fill="none" stroke="{C["ok"]}" stroke-width="2.4"/></g>')
        items.append(t(384, y, escape(label), 14, C["muted"]))
    for name, windows in (("fail", [(48, 62)]), ("fix", [(62, 74)]), ("pass", [(74, 96)])):
        css.append(kf(name, windows))

    body = f"""
  <!-- step labels -->
  {t(60, 52, "① SPEC", 12, C['blue'], 700, extra='letter-spacing="1.6"')}
  {t(330, 52, "② FROZEN ACCEPTANCE", 12, C['teal'], 700, extra='letter-spacing="1.6"')}
  {t(660, 52, "③ PLAYED IN CHROMIUM", 12, C['teal'], 700, extra='letter-spacing="1.6"')}
  {t(1010, 52, "④ GATE", 12, C['ok'], 700, extra='letter-spacing="1.6"')}

  <!-- spec document -->
  {card(60, 72, 220, 250, C['card'], C['stroke'], 12)}
  {t(80, 102, "game_design_prompt.md", 13, C['text'], 600, cls='mono')}
  <g fill="{C['faint']}"><rect x="80" y="122" width="170" height="8" rx="4"/><rect x="80" y="142" width="140" height="8" rx="4"/><rect x="80" y="162" width="160" height="8" rx="4"/>
  <rect x="80" y="196" width="150" height="8" rx="4" fill="{C['blue']}" opacity=".7"/><rect x="80" y="216" width="120" height="8" rx="4"/><rect x="80" y="236" width="165" height="8" rx="4" fill="{C['blue']}" opacity=".7"/><rect x="80" y="256" width="100" height="8" rx="4"/></g>
  {t(80, 300, "written before any code exists", 12.5, C['dim'])}
  <path d="M286 196 H322" stroke="{C['teal']}" stroke-width="2.5" class="flow"/>

  <!-- checklist -->
  {card(330, 72, 300, 250, C['card'], C['stroke'], 12)}
  {''.join(items)}
  {t(352, 306, "3-10 scenarios · real input + asserts", 12.5, C['dim'])}
  <path d="M636 196 H652" stroke="{C['teal']}" stroke-width="2.5" class="flow"/>

  <!-- browser -->
  <g filter="url(#shadow)">{card(660, 72, 320, 250, '#0b1b22', '#2f505a', 12)}</g>
  <path d="M660 84a12 12 0 0 1 12-12h296a12 12 0 0 1 12 12v18H660z" fill="#10242c"/>
  <circle cx="676" cy="87" r="4" fill="#ff6b6b" fill-opacity=".8"/><circle cx="690" cy="87" r="4" fill="#f5c451" fill-opacity=".8"/><circle cx="704" cy="87" r="4" fill="#4fd18b" fill-opacity=".8"/>
  {t(830, 91, "127.0.0.1:8000", 11, C['dim'], anchor='middle', cls='mono')}
  <rect x="676" y="116" width="288" height="152" rx="6" fill="#111a2e" stroke="#2a3b5a"/>
  <circle cx="792" cy="160" r="6" fill="#ffd400"/><circle cx="870" cy="214" r="6" fill="#ffd400"/><circle cx="930" cy="140" r="6" fill="#ffd400"/>
  <g class="player"><circle cx="736" cy="214" r="9" fill="#3ec58f"/></g>
  {t(690, 136, "Score: 0 / 10", 11, '#cfe3ff')}
  <rect x="884" y="276" width="80" height="28" rx="6" fill="#2b7bb9"/>{t(924, 295, "Restart", 13, '#fff', 600, 'middle')}
  <g class="v" style="animation-name:key;opacity:0"><rect x="676" y="276" width="70" height="28" rx="6" fill="{C['okbg']}" stroke="{C['teal']}"/>{t(711, 295, "→ key", 13, C['teal'], 700, 'middle', cls='mono')}</g>
  <g class="v" style="animation-name:click;opacity:0"><circle cx="924" cy="290" r="18" fill="none" stroke="{C['teal']}" stroke-width="2"/></g>
  <g class="cursor"><path d="M1042 408 l0 22 6 -6 5 11 4 -2 -5 -11 9 0 z" fill="#fff" stroke="#000" stroke-width="1"/></g>
  <path d="M986 196 H1002" stroke="{C['teal']}" stroke-width="2.5" class="flow"/>

  <!-- gate -->
  {card(1010, 72, 214, 250, C['card'], C['stroke'], 12)}
  <g class="v" style="animation-name:fail;opacity:0">
    <circle cx="1117" cy="160" r="40" fill="{C['errbg']}" stroke="{C['err']}" stroke-width="2"/>
    <path d="M1103 146 l28 28 M1131 146 l-28 28" stroke="{C['err']}" stroke-width="4" stroke-linecap="round"/>
    {t(1117, 238, "13 / 14", 26, C['err'], 800, 'middle')}{t(1117, 266, "failing step + state", 12.5, C['muted'], anchor='middle')}{t(1117, 284, "sent back to the planner", 12.5, C['muted'], anchor='middle')}
  </g>
  <g class="v" style="animation-name:fix;opacity:0">
    <circle cx="1117" cy="160" r="40" fill="{C['warnbg']}" stroke="{C['warn']}" stroke-width="2"/>
    {t(1117, 174, "↻", 40, C['warn'], 700, 'middle')}
    {t(1117, 238, "repair", 26, C['warn'], 800, 'middle')}{t(1117, 266, "fix the real defect,", 12.5, C['muted'], anchor='middle')}{t(1117, 284, "never bend the game", 12.5, C['muted'], anchor='middle')}
  </g>
  <g class="v" style="animation-name:pass">
    <circle cx="1117" cy="160" r="40" fill="{C['okbg']}" stroke="{C['ok']}" stroke-width="2"/>
    <path d="M1099 160 l12 12 24 -26" fill="none" stroke="{C['ok']}" stroke-width="5" stroke-linecap="round"/>
    {t(1117, 238, "14 / 14", 26, C['ok'], 800, 'middle')}{t(1117, 266, "every mandatory criterion", 12.5, C['muted'], anchor='middle')}{t(1117, 284, "passes with evidence", 12.5, C['muted'], anchor='middle')}
  </g>
  {t(640, 362, "Unpassable tests are filtered · mouse games are clicked, keyboard games are keyed · a screenshot for every scenario", 13.5, C['dim'], anchor='middle')}
"""
    write("verification.svg", page(1280, 392, "How a game is verified",
          "The specification becomes frozen executable scenarios before any code exists; the harness plays "
          "the game in headless Chromium with real keys and clicks; failures go back to the planner, and "
          "success needs every mandatory criterion to pass with evidence.", body, "\n".join(css)))


# ---------------------------------------------------------------- state machine

def state_machine() -> None:
    cycle = 15
    nodes = {"INIT": (60, 96, "INITIALIZING"), "PLAN": (236, 96, "PLANNING"), "DECIDE": (412, 96, "DECIDING"),
             "EXEC": (588, 96, "EXECUTING"), "VERIFY": (764, 96, "VERIFYING"), "FINAL": (940, 96, "FINALIZING"),
             "DONE": (1100, 96, "SUCCEEDED"), "RECOVER": (676, 236, "RECOVERING")}
    timeline = [("INIT", 0, 7, "Workspace created, spec read, 8 acceptance scenarios frozen"),
                ("PLAN", 7, 14, "Plan: 7 tasks covering every mandatory criterion"),
                ("DECIDE", 14, 20, "Next action: write index.html"), ("EXEC", 20, 27, "Sandboxed write, workspace revision updated"),
                ("DECIDE", 27, 32, "All tasks done: request verification"), ("VERIFY", 32, 41, "Harness plays the game: 13 / 14"),
                ("RECOVER", 41, 50, "Failure classified: REPAIR (style.css missing)"), ("DECIDE", 50, 56, "Fix: inline the CSS"),
                ("EXEC", 56, 62, "Sandboxed write"), ("VERIFY", 62, 72, "Fresh verification: 14 / 14"),
                ("FINAL", 72, 81, "Trace finalized and audited"), ("DONE", 81, 96, "SUCCEEDED · game/ and traces/ preserved")]
    css = [f".s{{animation-duration:{cycle}s;animation-iteration-count:infinite;}}"]
    windows: dict[str, list] = {}
    for key, a, b, _ in timeline:
        windows.setdefault(key, []).append((a, b))
    body = []
    edges = [("M200 126 H232", True), ("M376 126 H408", True), ("M552 118 H584", True), ("M584 136 H552", True),
             ("M552 104 C620 50 700 50 760 104", True), ("M904 126 H936", True), ("M1080 126 H1096", True),
             ("M660 158 C660 200 690 220 700 236", False), ("M836 158 C836 200 810 220 800 236", False),
             ("M676 266 C560 266 480 220 482 160", True), ("M676 280 C400 290 300 230 306 160", True),
             ("M840 266 C920 266 990 220 1004 160", True)]
    for d, solid in edges:
        dash = "" if solid else ' stroke-dasharray="4 5"'
        body.append(f'<path d="{d}" fill="none" stroke="{C["faint"]}" stroke-width="1.8"{dash} marker-end="url(#a2)"/>')
    for label, (x, y) in (("tool / observation", (570, 176)), ("verify", (660, 44)), ("repair", (560, 238)),
                          ("replan", (360, 300)), ("abort", (960, 238)), ("error", (612, 210)), ("failed check", (842, 210))):
        body.append(t(x, y, label, 11.5, C["dim"], anchor="middle"))
    for key, (x, y, label) in nodes.items():
        w = 140 if key != "DONE" else 140
        colour = C["warn"] if key == "RECOVER" else C["ok"] if key == "DONE" else C["teal"]
        fill = C["warnbg"] if key == "RECOVER" else C["okbg"]
        css.append(kf(f"n{key}", windows.get(key, [])))
        body.append(card(x, y, w, 60, C["card"], C["stroke"], 12))
        lit = card(x, y, w, 60, fill, colour, 12, 'stroke-width="2"')
        body.append(f'<g class="s" style="animation-name:n{key};opacity:0"><rect x="{x - 6}" y="{y - 6}" width="{w + 12}" height="72" rx="16" fill="{colour}" opacity=".18" filter="url(#soft)"/>'
                    f'{lit}</g>')
        body.append(t(x + w / 2, y + 36, label, 13.5, C["text"], 700, "middle", extra='letter-spacing="0.6"'))
    for i, (key, a, b, text) in enumerate(timeline):
        css.append(kf(f"c{i}", [(a, b)]))
        last = i == len(timeline) - 1
        body.append(f'<g class="s" style="animation-name:c{i}{"" if last else ";opacity:0"}">'
                    f'{t(640, 356, escape(text), 16, C["teal"] if not last else C["ok"], 600, "middle")}</g>')
    defs = (f'<defs><marker id="a2" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">'
            f'<path d="M0 0 L7 4.5 L0 9" fill="none" stroke="{C["faint"]}" stroke-width="1.5"/></marker></defs>')
    body.append(f'<rect x="300" y="330" width="680" height="40" rx="20" fill="none" stroke="{C["line"]}"/>')
    write("state-machine.svg", page(1280, 392, "Inside a run",
          "The run moves from initializing through planning, deciding, executing and verifying; a failed check "
          "goes to recovering, which repairs or replans; passing verification finalizes the run as succeeded.",
          defs + "\n".join(body), "\n".join(css)))


# ---------------------------------------------------------------- gallery

GAMES = [
    ("collect", "Collection game", "dev_01_collect", "arrow keys · 5 coins · win at 10 · restart",
     ["Claude Sonnet 5.5", "8 spec scenarios", "14 / 14", "215 s"]),
    ("memory", "Memory cards", "dev_03_memory", "mouse only · 4 pairs · mismatch delay",
     ["Claude Sonnet 5.5", "8 spec scenarios", "14 / 14 first try", "125 s"]),
    ("turns", "Turn-based keys", "holdout_02_turns", "3 keys · enemy chases · exit unlocks",
     ["Claude Sonnet 5.5", "9 spec scenarios", "15 / 15", "199 s"]),
]

HANGMAN = ["=== HANGMAN ===", "Word:    _ _ _ _ _ _ _ _   Lives: 6", "> e", "Word:    _ e _ _ _ _ _ _",
           "Yes! 'e' is in the word.", "> a", "Word:    _ e _ _ _ a _ _", "> z",
           "Sorry, 'z' is not in the word. You lose a life.", "> e",
           "You already guessed 'e'. No life lost.", "> r", "Word:    _ e _ _ _ a r _   Lives: 5"]


def frame(title: str, spec: str, blurb: str, chips: list[str], inner: str, url: str) -> str:
    chip_svg, x = [], 24
    for i, chip in enumerate(chips):
        w = 14 + len(chip) * 7.4
        colour = C["ok"] if "/" in chip and i == 2 else C["teal"] if i == 0 else C["muted"]
        chip_svg.append(f'<rect x="{x}" y="452" width="{w:.0f}" height="26" rx="13" fill="{C["card2"]}" stroke="{C["stroke"]}"/>'
                        + t(x + w / 2, 469, escape(chip), 12.5, colour, 600, "middle"))
        x += w + 8
    return f"""
  <path d="M0 0h640v40H0z" fill="#10242c"/>
  <circle cx="20" cy="20" r="5.5" fill="#ff6b6b" fill-opacity=".85"/><circle cx="39" cy="20" r="5.5" fill="#f5c451" fill-opacity=".85"/><circle cx="58" cy="20" r="5.5" fill="#4fd18b" fill-opacity=".85"/>
  <rect x="150" y="9" width="340" height="22" rx="11" fill="#08161c"/>{t(320, 25, escape(url), 12, C['dim'], anchor='middle', cls='mono')}
  <line x1="0" y1="40" x2="640" y2="40" stroke="#2f505a"/>
  {inner}
  <rect x="0" y="360" width="640" height="140" fill="url(#fade)"/>
  {t(24, 412, escape(title), 24, weight=800)}
  {t(24, 436, escape(f"{spec} · {blurb}"), 13, C['muted'])}
  {''.join(chip_svg)}"""


def gallery() -> None:
    fade = (f'<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["bg2"]}" stop-opacity="0"/>'
            f'<stop offset=".45" stop-color="{C["bg2"]}" stop-opacity=".92"/><stop offset="1" stop-color="{C["bg2"]}"/></linearGradient></defs>')
    for key, title, spec, blurb, chips in GAMES:
        data = base64.b64encode((SHOTS / f"{key}.jpg").read_bytes()).decode()
        inner = f'<image href="data:image/jpeg;base64,{data}" x="0" y="40" width="640" height="427" preserveAspectRatio="xMidYMin slice"/>'
        write(f"gallery/{key}.svg", page(640, 500, f"{title}, built live",
              f"Screenshot of the {title.lower()} the agent generated for {spec}: {blurb}. "
              f"{', '.join(chips)}.", fade + frame(title, spec, blurb, chips, inner, "127.0.0.1:8000"),
              radius=16, glow=(0.5, 0.0)))
    css = [".h{animation-duration:14s;animation-iteration-count:infinite;}"]
    lines = []
    for i, line in enumerate(HANGMAN):
        css.append(kf(f"h{i}", [(2 + i * 5.5, 96)]))
        colour = (C["teal"] if line.startswith(">") else C["ok"] if line.startswith("Yes")
                  else C["err"] if line.startswith("Sorry") else C["warn"] if line.startswith("You already")
                  else C["text"])
        lines.append(f'<text x="28" y="{76 + i * 22}" font-size="14" class="mono h" fill="{colour}" '
                     f'style="animation-name:h{i}">{escape(line)}</text>')
    inner = f'<rect x="0" y="40" width="640" height="460" fill="#08161c"/>{"".join(lines)}'
    write("gallery/hangman.svg", page(640, 500, "Terminal hangman, built live",
          "A real transcript of the terminal hangman game the agent generated in Python for dev_05_terminal: "
          "correct guesses reveal letters, a repeat costs no life, a wrong guess costs one.",
          fade + frame("Terminal hangman", "dev_05_terminal", "Python · typed guesses · 6 lives",
                       ["Claude Sonnet 5.5", "process mode", "4 / 4", "77 s"], "".join(lines) and inner, "python3 main.py"),
          "\n".join(css), radius=16, glow=(0.5, 0.0)))


# ---------------------------------------------------------------- team

def team() -> None:
    members = [("M1", "Controller + integration", "agent/controller.py", "state machine, gateway, success gate, wiring", C["teal"]),
               ("M2", "Planner", "agent/planning/", "plans, next actions, prompts, model client", C["teal"]),
               ("M3", "Runtime + sandbox", "agent/tools/", "files, commands, deps, process cleanup", C["ok"]),
               ("M4", "Verifier + harness", "agent/verification/", "spec scenarios, Chromium play-testing", C["blue"]),
               ("M5", "Recovery + evaluation", "agent/recovery/", "failure classes, repair or replan", C["warn"]),
               ("M6", "Tracing + delivery", "agent/tracing/", "events, evidence, run report, audit", C["teal"])]
    css = [".tm{animation:shine 6s ease-in-out infinite;transform-box:fill-box;}",
           "@keyframes shine{0%{transform:translateX(-120px);opacity:0}10%{opacity:1}30%{transform:translateX(380px);opacity:0}100%{opacity:0}}"]
    body = []
    for i, (tag, name, path, role, colour) in enumerate(members):
        x, y = 40 + (i % 3) * 410, 30 + (i // 3) * 150
        body.append(card(x, y, 380, 128, C["card"], C["stroke"], 14))
        body.append(f'<clipPath id="cp{i}"><rect x="{x}" y="{y}" width="380" height="128" rx="14"/></clipPath>')
        body.append(f'<g clip-path="url(#cp{i})"><rect x="{x}" y="{y}" width="120" height="128" fill="url(#sweep)" opacity=".10" class="tm" style="animation-delay:{i * 0.6}s"/></g>')
        body.append(f'<rect x="{x + 22}" y="{y + 24}" width="52" height="52" rx="14" fill="{C["card2"]}" stroke="{colour}" stroke-width="1.6"/>')
        body.append(t(x + 48, y + 57, tag, 18, colour, 800, "middle"))
        body.append(t(x + 92, y + 46, escape(name), 19, weight=700))
        body.append(t(x + 92, y + 70, escape(path), 13, colour, cls="mono"))
        body.append(t(x + 22, y + 106, escape(role), 14, C["muted"]))
    write("team.svg", page(1280, 330, "Team", "Six members and the module each owns: controller, planner, "
                           "runtime, verifier, recovery, tracing.", "\n".join(body), "\n".join(css), radius=18))


# ---------------------------------------------------------------- testing

def testing() -> None:
    stats = [("314", "tests passing", C["teal"]), ("62", "live model runs", C["blue"]),
             ("5", "models compared", C["ok"]), ("9", "rounds of fixes", C["warn"])]
    body = []
    for i, (value, label, colour) in enumerate(stats):
        x, y = 40 + (i % 2) * 250, 34 + (i // 2) * 150
        body.append(card(x, y, 230, 130, C["card"], C["stroke"], 14))
        body.append(f'<rect x="{x}" y="{y}" width="230" height="3" fill="{colour}"/>')
        body.append(t(x + 24, y + 74, value, 52, colour, 800))
        body.append(t(x + 24, y + 106, label, 15, C["muted"]))
    bars = [("Kimi K3 (course)", 18, 23, C["teal"]), ("GLM-5.3 (earlier)", 14, 16, C["blue"]),
            ("Claude Sonnet 5.5", 11, 11, C["ok"]),
            ("GPT-4.1-mini", 5, 8, C["warn"]), ("Qwen3-Coder", 1, 4, C["err"])]
    css = [".bar{transform-box:fill-box;transform-origin:left;animation:grow 2.2s cubic-bezier(.2,.8,.2,1) both;}",
           "@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"]
    body.append(card(560, 34, 680, 280, C["card"], C["stroke"], 14))
    body.append(t(588, 74, "Live runs that succeeded, by model", 19, weight=700))
    body.append(t(588, 98, "all 62 runs · round 1 used generic checks only · details in LIVE_MODEL_TESTS.md", 13, C["dim"]))
    for i, (name, ok, total, colour) in enumerate(bars):
        y = 116 + i * 38
        body.append(t(588, y + 18, name, 15, C["text"], 600))
        body.append(f'<rect x="760" y="{y}" width="380" height="26" rx="13" fill="{C["card2"]}"/>')
        body.append(f'<rect x="760" y="{y}" width="{380 * ok / total:.0f}" height="26" rx="13" fill="{colour}" class="bar" style="animation-delay:{0.3 + i * 0.25}s"/>')
        body.append(t(1156, y + 19, f"{ok}/{total}", 16, colour, 800))
    write("testing.svg", page(1280, 348, "Testing", "314 tests; 62 live runs with 5 models. Succeeded: Kimi K3 (the course model) 18 of 23, GLM-5.3 14 of 16, "
                              "Claude Sonnet 5.5 11 of 11, GPT-4.1-mini 5 of 8, Qwen3-Coder 1 of 4.",
                              "\n".join(body), "\n".join(css), radius=18))


# ---------------------------------------------------------------- run output

def run_output() -> None:
    tree = [("run/", C["text"], ""), ("├─ game_design_prompt.md", C["muted"], "input, never modified"),
            ("├─ agent.env", C["muted"], "settings, never submitted"), ("├─ game/", C["ok"], "playable + README.md"),
            ("├─ traces/<run_id>/", C["teal"], "the record of this run"), ("└─ .agent-runtime/", C["dim"], "deps, raw output")]
    files = [("run_report.md", "readable story: rationale per action", C["teal"]),
             ("events.jsonl", "every event, durable on write", C["blue"]),
             ("evidence/", "screenshots + sha256 index", C["ok"]),
             ("verification/", "every verification report", C["ok"]),
             ("model_usage.jsonl", "tokens · latency · cost per call", C["warn"]),
             ("summary.json", "final outcome and counters", C["teal"])]
    css = [".f{animation-duration:9s;animation-iteration-count:infinite;}"]
    body = [card(40, 32, 520, 296, "#08161c", "#2f505a", 14)]
    for i, (name, colour, note) in enumerate(tree):
        body.append(t(66, 76 + i * 40, escape(name), 15.5, colour, 600 if i in (3, 4) else 400, cls="mono"))
        if note:
            body.append(t(534, 76 + i * 40, escape(note), 13, C["dim"], anchor="end"))
    body.append(f'<path d="M566 196 H600" stroke="{C["teal"]}" stroke-width="2.5" class="flow"/>')
    for i, (name, note, colour) in enumerate(files):
        x, y = 610 + (i % 2) * 320, 32 + (i // 2) * 100
        css.append(kf(f"f{i}", [(5 + i * 9, 92)], "opacity", "1", ".35"))
        body.append(f'<g class="f" style="animation-name:f{i}">{card(x, y, 300, 84, C["card"], C["stroke"], 12)}'
                    f'<rect x="{x}" y="{y + 12}" width="3" height="60" rx="1.5" fill="{colour}"/>'
                    f'{t(x + 20, y + 36, escape(name), 15.5, colour, 700, cls="mono")}{t(x + 20, y + 62, escape(note), 13.5, C["muted"])}</g>')
    write("run-output.svg", page(1280, 360, "What a run leaves behind",
          "The run directory holds the spec, agent.env, game/, traces/<run_id>/ and .agent-runtime/. The trace "
          "holds run_report.md, events.jsonl, evidence/, verification/, model_usage.jsonl and summary.json.",
          "\n".join(body), "\n".join(css), radius=18))


# ---------------------------------------------------------------- budgets

def budgets() -> None:
    limits = [("1800 s", "total runtime", "AGENT_MAX_RUNTIME"), ("200", "work steps", "--max-steps"),
              ("80", "commands", "--max-commands"), ("20", "recoveries", "--max-recovery-attempts"),
              ("5", "retries / op", "--max-retries")]
    body = []
    for i, (value, label, option) in enumerate(limits):
        x = 40 + i * 244
        body.append(card(x, 30, 224, 120, C["card"], C["stroke"], 14))
        body.append(t(x + 22, 82, value, 36, C["teal"], 800))
        body.append(t(x + 22, 108, label, 15, C["text"], 600))
        body.append(t(x + 22, 132, option, 12, C["dim"], cls="mono"))
    body.append(t(40, 192, "RETRY BACKOFF", 12, C["dim"], 700, extra='letter-spacing="1.6"'))
    for i, (sec, w) in enumerate((("5 s", 60), ("15 s", 110), ("45 s", 200), ("60 s", 240))):
        x = 180 + sum(v for _, v in (("", 60), ("", 110), ("", 200), ("", 240))[:i]) + i * 10
        body.append(f'<rect x="{x}" y="176" width="{w}" height="24" rx="12" fill="{C["card2"]}" stroke="{C["stroke"]}"/>')
        body.append(t(x + w / 2, 193, sec, 12.5, C["warn"], 700, "middle"))
    body.append(t(860, 193, "transient model errors wait, then retry", 13, C["muted"]))
    outcomes = [("SUCCEEDED", "0", C["ok"], C["okbg"]), ("FAILED", "1", C["err"], C["errbg"]),
                ("STARTUP ERROR", "2", C["muted"], C["card2"]), ("EXHAUSTED", "3", C["warn"], C["warnbg"]),
                ("CANCELLED", "130", C["muted"], C["card2"])]
    body.append(t(40, 250, "OUTCOME · EXIT CODE", 12, C["dim"], 700, extra='letter-spacing="1.6"'))
    for i, (name, code, colour, fill) in enumerate(outcomes):
        x = 40 + i * 244
        body.append(f'<rect x="{x}" y="266" width="224" height="44" rx="22" fill="{fill}" stroke="{colour}"/>')
        body.append(t(x + 22, 294, name, 14, colour, 800, extra='letter-spacing="1"'))
        body.append(t(x + 200, 294, code, 15, colour, 800, "end", cls="mono"))
    write("budgets.svg", page(1280, 340, "Budgets and outcomes",
          "Defaults: 1800 s runtime, 200 steps, 80 commands, 20 recoveries, 5 retries with 5, 15, 45 and 60 second "
          "backoff. Outcomes: succeeded 0, failed 1, startup error 2, exhausted 3, cancelled 130.",
          "\n".join(body), radius=18))


# ---------------------------------------------------------------- footer

def footer() -> None:
    body = f"""
  <rect x="0" y="0" width="420" height="120" fill="url(#sweep)" opacity=".08" class="sweep"/>
  {t(640, 54, "Built live · verified in a browser · traced end to end", 22, C['text'], 700, 'middle')}
  {t(640, 84, "PE6203 Generative and Agentic AI · group project · games are generated at run time, never pre-written", 13.5, C['dim'], anchor='middle')}"""
    write("footer.svg", page(1280, 120, "Footer", "Built live, verified in a browser, traced end to end.",
                             body, radius=16, glow=(0.5, 0.5)))


def main() -> None:
    banner()
    glance()
    for section in SECTIONS:
        section_header(*section)
    architecture()
    verification()
    state_machine()
    gallery()
    team()
    testing()
    run_output()
    budgets()
    footer()


if __name__ == "__main__":
    main()
