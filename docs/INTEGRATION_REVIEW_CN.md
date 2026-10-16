# 集成检查与整改建议（2026-10-16）

本文件对应 `integration` 分支：合并了 PR #1（成员3）、#2（成员2）、#4（成员4）以及成员5、成员6的分支，并补齐了检查清单中未完成的项。测试：`python -m pytest -q` → 137 passed（含一次真实 runtime + 沙箱 + Playwright + recovery + tracing 的端到端彩排）。

## 一、检查清单结论

| 成员 | 任务 | 检查前 | 现在 | 涉及文件 |
|---|---|---|---|---|
| 1 | 合并 PR #4，可 import GameVerifier | PR #4 未合并 | ✅ 已合并，`from agent.verification import GameVerifier` | — |
| 1 | `create_dependencies` 注入 `GameVerifier()` | ❌ `agent/integrations.py` 不存在，CLI 必然启动失败 | ✅ 新建，注入五个真实模块；无 `AGENT_MODEL` 时明确退出，不使用 mock | `agent/integrations.py` |
| 1 | 跑 `python -m agent <spec>` 彩排 | ❌ 无法运行 | ✅ 离线彩排通过（脚本化模型，其他全部真实）；真实模型彩排待做 | `tests/rehearsal_factory.py` |
| 2 | 规划覆盖全部 mandatory criteria | ✅ parser 已校验 | ✅ | — |
| 2 | `launch_command` 非空且可启动 | ⚠️ 只校验非空 | ✅ 引用的文件必须已存在；提示词统一为浏览器游戏 + `python3 -m http.server` | `agent/planning/planner.py`、`prompts.py` |
| 2 | 在 game/ 生成启动文档 | ⚠️ 仅提示词要求 | ✅ 验证前强制 `game/README.md` 含确切启动命令（必要时自动写入） | `planner.py` |
| 3 | 执行 verifier 的 run_command（含 harness 绝对路径） | ✅ | ✅ 已在 macOS 严格沙箱内实测 Playwright | — |
| 3 | 超时/取消清理进程组 | ⚠️ macOS 上偶发误报 “cleanup failed” | ✅ 修复两处时序/EPERM 问题，压力测试通过 | `agent/tools/processes.py` |
| 3 | 回传 revision；命令不能改 agent/、spec、traces/ | ✅ | ✅ 新增沙箱写入测试 | `tests/test_integration_tasks.py` |
| 5 | 验证失败给 REPAIR/REPLAN | ✅ REPAIR | ✅ 连续失败周期性升级 REPLAN | `agent/recovery/policy.py` |
| 5 | timeout/未知副作用禁止盲 RETRY | ✅ | ✅ 新增测试；另修复“模型 JSON 格式错误直接 ABORT” | `agent/recovery/classify.py` |
| 6 | 持久化验证事件与 checkpoint | ⚠️ 只有最新 checkpoint | ✅ `verification/report-*.json`、`checkpoints.jsonl` | `agent/tracing/recorder.py` |
| 6 | harness output_refs（截图）写入 traces/<run_id>/ | ❌ 截图写进了 game/ | ✅ 截图 → runtime 产物目录 → tracer 复制到 `traces/<run_id>/outputs/` | runtime + harness + recorder |
| 6 | finalize 只碰 traces/，preserved=True 真实 | ⚠️ 无条件返回 True | ✅ 回读校验后才返回 True；另生成 `run_report.md` | `recorder.py`、`report.py` |

## 二、检查中发现并已修复的阻断性问题

1. **依赖名错误**：`pyproject.toml` 写的是 `litellm-core`（PyPI 上不存在），`pip install -e .` 直接失败 → 改为 `litellm`，并加入 `playwright`。
2. **Verifier 与真实 runtime 不兼容**：runtime 返回的 `data` 是只读 Mapping，verifier 只认 `dict`，导致文件列表、源码内容、harness JSON 全部读不到；还会读取不存在的 `README.md/main.js` 触发失败。
3. **Harness 交互检测误判正确的游戏**：按 →←↑↓ 后只比较首尾状态，正常移动的角色回到原位即判 FAIL（彩排中实际出现）。改为每次按键后对比，并对动画画面要求游戏自身键盘处理函数被调用。
4. **Harness 用 `start_new_session` 启动游戏服务器**：harness 超时被杀时服务器脱离进程组残留。
5. **Planner 无记忆**：每次决策只看到最后一个结果、没有文件列表和历史，也不保证 action_id 唯一 → 真实运行会原地打转或被控制器拒绝。
6. **Recovery**：模型偶发返回非法 JSON → 分类为 MODULE → ABORT，整场演示直接失败。

## 三、距离“完美符合作业要求”仍存在的问题（按优先级）

### P0 —— 演示前必须完成

1. **用课程指定模型做真实彩排（至少 5 次）**。模型/API 公布后立即：`export AGENT_MODEL=... AGENT_API_BASE=...`，用 `evaluation/specs/` 中的 6 个规格各跑一次，再用 `python -m evaluation.report traces/*` 统计成功率、耗时、恢复次数。重点观察：单文件过大导致输出截断（调 `AGENT_MAX_TOKENS`）、调用超时（`--model-timeout`）、重复修复不收敛。
2. ✅ **合并到 main**（2026-10-16 已完成）：各成员确认后，`integration` 分支已整体合并进 main。
3. **演示机预检**：Python 3.11、`pip install -e .`、`python -m playwright install chromium-headless-shell`、8000 端口空闲、能访问模型 API、`sandbox-exec`（macOS）或 `bwrap`（Linux）可用。建议新增 `python -m agent.preflight` 一键检查（约半天）。
4. **确认演示时长并设置预算**：默认 `--max-runtime 1800`（30 分钟）。若展示时段更短，提前设定，并保证最终化预留足够。

### P1 —— 直接影响“满足规格主要需求”和“开发过程”评分

5. **验收标准过于通用**（最大短板）。`criteria.py` 只生成 4 条泛化标准，`C-FUNCTIONAL` 只要有 canvas/score 字样就通过，无法证明“10 分获胜”“重启清零”等规格要求。建议（成员4 + 成员2，2–3 天）：
   - `prepare_acceptance` 调用 LLM 从规格中抽取 5–10 条可检验需求（仍在规划前冻结）；
   - 提示词要求生成的游戏暴露只读测试钩子 `window.__gameState = {score, player, status}`；
   - harness 按需求执行脚本化场景（按键 N 次 → 断言状态），每条需求单独 PASS/FAIL 并附截图。
6. **traces 缺少模型推理记录**。当前记录了动作、结果、失败、恢复，但没有每次 LLM 调用的提示摘要、原始回复和 token 用量。建议 planner 把 rationale/回复摘要/usage 放进返回对象或由 tracer 单独记录（成员2 + 成员6，约 1 天），演示时更容易讲清“Agent 如何分解规格、遇到什么问题、如何恢复”。
7. **非浏览器游戏无法验证**。若规格明确要求 Python/pygame 桌面游戏，harness 无法验证，会反复修复直到预算耗尽。至少要能：识别非网页项目 → 启动进程存活 N 秒且无 traceback 作为 launch 证据，并在报告中如实说明局限。
8. **没有回滚**。修复导致回退时无法恢复到上一个通过验证的版本。可在每次验证前把 game/ 快照到 `.agent-runtime/snapshots/<revision>`，由 recovery 决定是否回滚（需在 INTERFACES 中增加一个工具或决策类型）。

### P2 —— 提交物与仓库整洁度

9. **提交结构**：要求 `submission/agent/`（含 README）、`game/`、`traces/`、`report.pdf`。建议把本仓库整体放入 `submission/agent/`，README 中写清 `pip install -e .` 后在 submission/ 目录运行。注意 `traces/` 中 `outputs/index.jsonl` 引用的原始路径在 `.agent-runtime/`，复制品已在 traces 内，无需提交 `.agent-runtime/`。
10. ✅ **仓库冗余**（已完成：旧代码副本已删除，成员文档移至 `docs/members/`，测试记录移至 `docs/testing/`）：`DAI MINFEI_Member3/`、`member4_ZHOU SIHAN/` 是旧的代码副本，根目录还有多份 `MEMBER*_*.md` 和 `INTEGRATION_TEST_OUTPUT.txt`。建议移到 `docs/members/`，删除重复代码，避免评分者误以为是两套实现；`TEST_REPORT.md` 仍写“65 tests”，需更新。
11. **report.pdf 尚未开始**。必须包含：架构与取舍理由（FSM 控制器 vs 多 Agent）、规划与验证方式、失败恢复、当前局限、下一步改进、AI 使用声明、每位成员贡献。建议以本文件第三部分作为“局限与改进”章节素材。
12. **演示材料**：选择“一个技术实现深入讲解”（推荐：harness 交互验证或沙箱进程组清理），并用 `traces/<run_id>/run_report.md` 的时间线讲解开发过程。

## 四、各成员需要 review 的改动

| 成员 | commit |
|---|---|
| 1 | `Member 1 integration: production factory, targeted repair, rehearsal` |
| 2 | `Member 2 planner: working memory, browser-game guidance, launch guards` |
| 3 | `Runtime: allow bounded 3s SIGKILL grace…`、`Member 3 runtime: artifact dir per command…` |
| 4 | `Member 4 verifier: integrate with real runtime and fix harness checks` |
| 5 | `Member 5 recovery: regenerate malformed model output instead of aborting` |
| 6 | `Member 6 tracing: copy output_refs, persist verification and checkpoints` |

## 五、P0 加固（已完成）

| 事项 | 实现 | 验证 |
|---|---|---|
| 模型重试退避 | 重试前依次等待 5s/15s/45s/60s（不超过剩余时间），重试上限 5 次；`--retry-backoff` 可调；trace 记录 `retry_backoff` 事件 | 模拟 API 连续返回两次 503，Agent 等待 5s、15s 后继续并最终成功 |
| 启动自检 | `agent/preflight.py`：Python、python3、8000 端口、沙箱、Playwright Chromium 不满足时在创建 game/ 前停止；node/npm 是否存在等写入 planner 提示词 | `python -m agent.preflight` 可在演示机上单独运行 |
| `agent.env` 配置 | 运行目录下的 `agent.env` 自动加载（shell 变量优先，只打印变量名）；沙箱禁止生成的代码读取该文件 | 只用 `python -m agent game_design_prompt.md` 一条命令完成运行；traces 中无 key |
| 附带修复 | LiteLLM 出错时向 stdout 打印横幅，破坏 CLI 输出的 JSON；已关闭 | 回归测试 |
