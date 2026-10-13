# 成员 5 交接说明

集成分支：member5-recovery-evaluation
基于 main 1658dc3；共享接口与之前的 b2aa330 一致。

我这边 Member 5 的 Recovery & Evaluation 第一版已完成，独立分支供大家集成。

已完成：
- agent/recovery/：按错误代码分类，返回 RETRY、REPAIR、REPLAN、ABORT。
- evaluation/report.py：只读统计运行日志中的终态、耗时、执行计数、失败类别和恢复决策。
- evaluation/specs/：4 个开发需求、2 个保留测试需求，没有预写游戏代码。
- 新增 36 项测试，连同原有 43 项，共 79 项通过。

测试范围是恢复模块、日志统计及使用模拟依赖的控制器集成，不代表真实游戏、严格沙箱或进程清理已通过。没有修改 controller.py、state.py、共享接口、预算和其他成员代码。

## Member 1 主流程与集成负责人

请在依赖工厂中导入：

```python
from agent.recovery import RuleBasedRecovery
recovery = RuleBasedRecovery()
```

将实例传给 Dependencies.recovery，并统一更新打包配置，包含 agent.recovery 和 evaluation。

总时间、调用/命令/步骤预算、重试次数、最终验证预留时间仍由你的控制器管理。我的模块不修改或重置计数。

目前控制器未强制“未知副作用后必须先 inspect_workspace”。请与 Member 2 一起落实该顺序，最好由控制器强制执行；仅记录恢复理由并不能保证下一步安全。

## Member 2 模型与规划负责人

发生超时或未知副作用时，请根据 view.last_failure 和 workspace_revision=None，先提出 inspect_workspace，再决定修改或重新执行。

当前 AgentView 不包含 RecoveryDecision，因此不能假定 Planner 能看到恢复理由。REPAIR 后由你生成针对性修复，工具执行仍经过控制器。

请对齐模型 API 异常。当前识别 TimeoutError、ConnectionError、RateLimitError 为临时模块故障；未识别的异常保守终止，认证和权限错误不重试。

## Member 3 敏菲 工具与环境负责人

已按 MEMBER3_DECISIONS.md 的错误代码设计恢复策略。

COMMAND_TIMEOUT、DEPENDENCY_TIMEOUT 不直接重试；PROCESS_CLEANUP_FAILED 会终止，避免存活进程影响重启或文件修改。请继续修复进程清理问题，并在演示电脑验证严格沙箱和实际依赖安装。

目前未实现回退：工作区版本哈希不是文件备份，runtime 也没有恢复快照工具。如需扩展，由你和 Member 1 先约定工具与契约，我负责提出恢复决定。

## Member 4 验证负责人

你负责发现问题和提供证据，我负责选择处理策略。验证失败或报告过期会请求 REPAIR，修改之后仍由你重新验证相关需求及可能受影响的功能。

请保证验证命令有界并关闭进程；不能仅凭启动成功确认玩法通过，也不能把未验证需求当作通过。

## Member 6 Li Jiakun Tracing & Delivery 负责人

我的恢复决定由控制器通过 recovery 事件记录；无需 recovery 模块直接写日志。

离线统计读取 events.jsonl 和 summary.json，要求运行编号一致、事件序号连续、末尾终态与摘要一致。缺失或损坏的记录计入总运行数，避免成功率被高估。

统计工具不能替代你的交付审计，也不能证明玩法或某次修复有效。首个可玩时间、模型 token 成本和逐次修复效果需要以后一起约定额外日志字段。

## 我 Member 5 后续工作

配合合并和接口联调，完成真实的“失败 → 恢复决定 → 执行 → 复验 → 留痕”试跑；随后组织干净目录下的盲测，记录模型、预算、机器和 Agent 版本，区分模拟测试与真实游戏结果。

测试命令：

```bash
python -m unittest tests.test_recovery tests.test_evaluation -v
python -m unittest discover -v
```

离线统计：

```bash
python -m evaluation.report traces/RUN_A traces/RUN_B
```

自动回退未实现：当前契约仅支持 RETRY、REPAIR、REPLAN、ABORT。详细说明见 MEMBER5_HANDOFF.md 和 MEMBER5_TEST_REPORT.md。
