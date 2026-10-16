# Test fixtures (not part of the agent)

`games/` holds tiny hand-written browser pages used **only by the test suite**:

| File | Used by | Purpose |
|---|---|---|
| `rehearsal_index.html`, `rehearsal_broken_game.js`, `rehearsal_fixed_game.js` | `tests/rehearsal_factory.py` | Scripted replies for the offline end-to-end rehearsal (a broken game that fails verification, then a fixed one), so the pipeline can be exercised without an API key |
| `contract_collect.html` | `tests/test_spec_acceptance.py` | Checks that the Playwright harness runs spec scenarios against a page exposing `window.__gameState` (a correct and a deliberately buggy variant) |
| `contract_lights_out.html` | `tests/test_spec_acceptance.py` | Checks mouse-only interaction (grid clicks) |

They are test inputs, not templates: the production agent (`agent/`) never
reads `tests/`, contains no game code, and generates every game from the
specification at run time. `tests/test_no_game_templates.py` enforces this.
