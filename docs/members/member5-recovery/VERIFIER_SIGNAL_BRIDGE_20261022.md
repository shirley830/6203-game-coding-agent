# Member 5 与 Member 4 的现有验收信号对接（2026-10-22）

## 问题与解决

此前 Member 4 返回 UNKNOWN + detail 内 test_script_error，而恢复模块只识别新增错误代码，主流程没有产生这些代码。当前 Controller 已把验收结果写入 FailureRecord.message，因此增加兼容层，直接使用已有链路，不要求先改 Controller 或 AgentState。

仅修改 Member 5 的 agent/recovery/，新增专属测试与本文；不修改其他成员实现或游戏。本补充通过 member5-generic-recovery 分支和 PR #9 交付，待 Member 1 集成。

## 已实现行为

- 来源必须是 category=verification、operation_key=verifier.next_check、phase=verifying；未知副作用不采用本兼容信号。
- 只解析 Controller 当前固定格式的 Failed criteria 摘要，且 criterion_id 必须属于冻结验收套件。
- UNKNOWN + test_script_error: 标记：立即 ABORT、保留证据，不向 Planner 发送游戏 REPAIR，也不宣称游戏通过。
- 冻结套件中的 mandatory C-ACCEPTANCE-INCOMPLETE 被报告为 FAIL：停止本轮。修改游戏不能补全冻结的验收定义，需要修复验收准备并新开运行。
- 普通 FAIL、普通 UNKNOWN、无关报错文字或运行工具输出仍按原有策略处理，不一概视为验收故障。
- 普通游戏失败仍可 REPAIR、复验并成功。

ABORT 会把本轮结束为 FAILED；这是诚实保存结果，不是自动修复验收脚本。仍需 Member 4 修复测试表达式、验收生成规则，随后重跑。

## 测试范围

新增 tests/test_recovery_signals.py：10 项，包括真实 Controller 生成摘要、真实 GameVerifier 消费 harness-shaped UNKNOWN 结果并经 Controller 传到 Recovery、冻结验收不完整时停止、普通游戏失败继续恢复、无关来源和字段误判保护。

真实 GameVerifier 联动使用脚本化 Runtime 测试夹具，不代表真实模型生成游戏联调。另在隔离临时副本叠加最新 Member 4 分支运行完整回归，结果记录见后文。

## 边界与后续

- 此处沿用 Member 4 已决定的 UNKNOWN + detail 标记语义，不仅凭 ReferenceError 等任意字符串猜测测试来源。
- 兼容层依赖 Controller 摘要格式，摘要截断或格式变化可能导致未识别；长期建议改成结构化错误类型与完整报告传递。不能把它宣传为所有验收错误都能准确分类。
- 套件少于 3 条规则的标记只避免假成功及徒劳修游戏，不提高验收生成的成功率；Member 4 仍需改进解析/生成及初始化阶段的有界恢复。
- 仅使用当前阶段与冻结报告信息，不修改套件、不删除强制标准、不把失败改成成功。
- 原防循环逻辑和全部超时/清理安全规则保留。
- 未修改桌面入口、实际本机测试环境，也未执行新的付费模型游戏测试。

## 验证结果

- 新增对接测试 10 项通过。
- 临时副本以本分支 df3c4ac 为基础，叠加本次未提交的 Member 5 修改，以及 Member 4 分支 a30355baac631a924947ae720b3d83ef45c279f0 的验收模块及新增测试。
- 260 项完整回归全部通过，包括浏览器测试与 macOS 沙箱测试，耗时 72.334 秒。
- git diff --check 通过。
- 未改其他成员工作目录，未运行付费真模型联调，本补充通过 PR #9 交付，尚未合入 main。
