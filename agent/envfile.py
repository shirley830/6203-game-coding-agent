"""Load ``agent.env`` so the live run starts with exactly one command.

The file sits in the run directory next to game_design_prompt.md and holds the
model settings and provider key, one ``KEY=value`` per line::

    # agent.env
    AGENT_MODEL=openai/course-model
    AGENT_API_BASE=https://example.edu/v1
    OPENAI_API_KEY=...

Variables already set in the shell win over the file. Values are never
printed; only the names of loaded keys are reported. Commands launched by the
runtime receive a filtered environment, so these values do not reach them.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not _KEY.fullmatch(key):
            raise ValueError(f"agent.env line {number}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(path: Path) -> list[str]:
    """Apply the file to os.environ without overriding existing variables.

    Returns the names that were loaded (never the values).
    """
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
