# Member delivery notes

Design decisions, test reports and handoff notes written by each member while
their module was developed on its own branch. They are kept as a record for
the report; file paths inside them refer to the pre-integration layout
(for example `DAI MINFEI_Member3/` or `member4_ZHOU SIHAN/`). The integrated
code lives only under `agent/`.

| Member | Module (in `agent/`) | Notes |
|---|---|---|
| 1 | `__main__.py`, `controller.py`, `state.py`, `config.py`, `integrations.py` | [testing/MEMBER1_TEST_REPORT.md](../testing/MEMBER1_TEST_REPORT.md), [INTERFACES.md](../INTERFACES.md) |
| 2 | `planning/` | see `tests/test_planner.py` |
| 3 | `tools/` | [member3-runtime/](member3-runtime/) |
| 4 | `verification/` | [member4-verification/](member4-verification/) |
| 5 | `recovery/`, `evaluation/` (repo root) | [member5-recovery/](member5-recovery/) |
| 6 | `tracing/`, `delivery.py` | see `tests/test_tracing.py` |
