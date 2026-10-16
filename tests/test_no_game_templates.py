"""Guard: the submitted agent contains no game code and never reads test fixtures.

The assignment forbids a pre-written game engine, template or fill-in project.
Hand-written pages exist only under tests/fixtures/ for testing the harness.
"""

from pathlib import Path
import re
import unittest

AGENT = Path(__file__).resolve().parents[1] / "agent"


class NoGameTemplateTests(unittest.TestCase):
    def test_agent_package_ships_no_game_files(self):
        game_like = [p for p in AGENT.rglob("*") if p.suffix.lower() in
                     {".html", ".htm", ".js", ".mjs", ".css", ".png", ".jpg", ".svg"}]
        self.assertEqual(game_like, [])

    def test_agent_code_never_references_tests(self):
        offenders = []
        for path in AGENT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import)\s+tests\b", text, re.M) or "fixtures/games" in text:
                offenders.append(str(path.relative_to(AGENT)))
        self.assertEqual(offenders, [])

    def test_prompts_contain_no_game_source(self):
        from agent.planning import prompts
        from agent.verification import spec_criteria
        text = prompts.TECHNICAL_GUIDANCE + prompts.build_plan_prompt.__doc__.__str__()
        text += spec_criteria.build_prompt("spec")
        for marker in ("getContext(", "requestAnimationFrame(", "<canvas id=", "addEventListener("):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
