"""Build a frozen AcceptanceSuite from the input specification text."""

from __future__ import annotations

import hashlib
import re

from agent.state import AcceptanceSuite, Criterion

# Categories the controller requires among mandatory criteria.
REQUIRED_MANDATORY = frozenset({"launch", "functional", "interaction"})

_CONTROL_WORDS = (
    "arrow", "wasd", "keyboard", "mouse", "click", "tap", "drag",
    "space", "pause", "restart", "control",
)
_RULE_WORDS = (
    "score", "point", "win", "lose", "collision", "collect", "obstacle",
    "maze", "enemy", "timer", "level", "health", "lives", "goal",
)
_TECH_WORDS = (
    "canvas", "html", "javascript", "webgl", "local asset", "no remote",
    "offline", "browser",
)


def _digest_version(spec_text: str) -> str:
    normalized = re.sub(r"\s+", " ", spec_text.strip().lower())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"member4-{digest}"


def _first_sentence(spec_text: str, limit: int = 160) -> str:
    text = " ".join(spec_text.strip().split())
    if not text:
        return "Unspecified game requirements"
    for separator in (". ", "。", "\n"):
        if separator in text:
            text = text.split(separator, 1)[0]
            break
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _mentions(spec_lower: str, words: tuple[str, ...]) -> bool:
    return any(word in spec_lower for word in words)


def build_acceptance_suite(spec_text: str) -> AcceptanceSuite:
    """Derive acceptance criteria from the specification (not from generator claims).

    Always includes mandatory launch, functional, and interaction criteria.
    Optional technical criteria are added when the specification implies them.
    """
    if not isinstance(spec_text, str) or not spec_text.strip():
        raise ValueError("Specification text is empty; cannot prepare acceptance")

    summary = _first_sentence(spec_text)
    lower = spec_text.lower()
    criteria: list[Criterion] = [
        Criterion(
            criterion_id="C-LAUNCH",
            description=(
                "Documented launch command starts the game, the play surface becomes "
                "reachable, and the process is shut down cleanly after the check."
            ),
            category="launch",
            mandatory=True,
        ),
        Criterion(
            criterion_id="C-FUNCTIONAL",
            description=(
                f"Core gameplay rules from the specification are observable while running: {summary}"
            ),
            category="functional",
            mandatory=True,
        ),
        Criterion(
            criterion_id="C-INTERACTION",
            description=(
                "Player input changes game state (keyboard/pointer). "
                "Opening a page without an observable state change does not pass."
            ),
            category="interaction",
            mandatory=True,
        ),
    ]

    if _mentions(lower, _TECH_WORDS):
        tech_bits: list[str] = []
        if "canvas" in lower:
            tech_bits.append("uses a canvas (or equivalent) play surface")
        if "remote" in lower or "local" in lower or "offline" in lower or "asset" in lower:
            tech_bits.append("runs with local/programmatic assets as required")
        if not tech_bits:
            tech_bits.append("matches the stated technical delivery constraints")
        criteria.append(
            Criterion(
                criterion_id="C-TECHNICAL",
                description="Technical constraints: " + "; ".join(tech_bits) + ".",
                category="technical",
                mandatory=True,
            )
        )

    # Soft reminders encoded into functional/interaction descriptions when keywords appear.
    if _mentions(lower, _CONTROL_WORDS) or _mentions(lower, _RULE_WORDS):
        # Already covered by C-FUNCTIONAL / C-INTERACTION; keep suite lean.
        pass

    suite = AcceptanceSuite(version=_digest_version(spec_text), criteria=tuple(criteria))
    mandatory_categories = {c.category for c in suite.criteria if c.mandatory}
    if not REQUIRED_MANDATORY <= mandatory_categories:
        raise ValueError("Internal error: mandatory launch/functional/interaction coverage missing")
    return suite
