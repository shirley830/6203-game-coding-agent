# Member 5 对接 Member 4 验收信号（2026-10-27）

## 本次改动

基于 main `87fbdd1`，核对 Member 4 的 `member4-acceptance-signals`（`03c33f6`）后，补充恢复模块对结构化类别 `test_script_error` 的识别。

修改范围：`agent/recovery/classify.py`、`agent/recovery/policy.py`、Member 5 专属测试和本文。没有修改 Controller、AgentState、验收模块、游戏代码或其他成员目录。

两条兼容路径：

- 现有主流程：Verifier 返回 UNKNOWN，detail 含 `test_script_error:`；Controller 生成冻结验收条目的失败摘要。Member 5 原有兼容层识别后 ABORT，保留游戏和证据，不进入游戏 REPAIR。
- Member 1 新映射：`FailureRecord.category="test_script_error"`，`operation_key="verifier.next_check"`、`phase=VERIFYING`、`action=None`。本次补充将其认定为验收故障，同样 ABORT，原因中保留类别，方便 tracing。

结构化信号即使带有未知副作用也停止，不发起新的游戏修改或重复执行。此行为不保证替 Runtime 完成了进程清理；清理仍由原流程负责。未分类的普通报错文字不能凭名称冒充此信号。

## Member 1 需要配合的接口

只对经确认的测试脚本错误设置 `test_script_error`。应使用对应验收报告中的真实状态与明确标记判断，不能把所有 FAIL、所有 UNKNOWN、所有 JavaScript 错误或游戏页面错误统一归类成脚本错误。

本次没有修改生产 Controller，所以结构化类别的生产映射仍待 Member 1 接入。测试中的 `_TypedController` 只是接口验证夹具，不是已部署的生产逻辑。在 Member 1 接入前，现有 UNKNOWN+标记的兼容路径仍保留。

## 停止意味着什么

ABORT 是诚实结束本轮为 FAILED、保存证据，不是游戏通过，也不是自动修好了验收。当前 RecoveryKind 没有“修复验收脚本”操作；要由 Member 4/Member 1 受控修复验收并重新运行，不允许恢复模块自行删除或放宽冻结标准。

普通游戏失败继续走原有 REPAIR/REPLAN 和无进展限制。扫雷展开范围被截断、最后一秒漏击被豁免等偏离原题的游戏逻辑，不能仅凭本次更新自动当成测试故障；仍需 Member 2 按原题修复，Member 4 校正测试。

## 验收不完整的边界

Member 4 分支在两次生成均失败后保留 `C-ACCEPTANCE-INCOMPLETE`，但该条目仍是 `mandatory=False`。若其他 mandatory 检查全过，Controller 可能不调用 Recovery，底层 outcome 仍为 succeeded；最新 main 的 result levels 会显示 `acceptance_incomplete`。因此不能宣称该分支已经保证“立即停止本轮”。Member 1 需确认最终政策，Member 5 不跨模块改变其状态逻辑。

## 验证方式

新增七项测试，覆盖：结构化类别、未知副作用时停止、来源约束、普通报错文字不误判、原 Controller 兼容路径、Member 1 映射的模拟链路、真实游戏失败继续修复。

全部测试不需要 API Key；Controller 集成使用测试夹具，不代表真实模型游戏联调。完整回归和叠加分支回归结果见本文后续记录。

## 实际验证结果

- 恢复模块相关测试：70 项通过，其中本次新增 7 项。
- main 87fbdd1 + Member 5 改动：完整回归 289 项通过，无跳过，72.273 秒。
- Member 4 03c33f6 + Member 5 改动：在隔离副本完整回归 298 项通过，无跳过，72.083 秒。
- 单独取 Member 4 实际 harness 检测函数产生的矛盾配对结果，送入真实 Controller + Recovery：UNKNOWN/test_script_error → FAILED，恢复1次，游戏写入动作0次。页面使用测试替身，没有调用真实浏览器或模型。
- 首次两套完整回归并行执行时，main 的脚本化浏览器彩排有1项失败；同项单独复跑通过，再串行跑完整289项通过。存在并行端口/资源干扰的可能，未据此修改其他成员实现。两次完整回归原始日志均保留。
- git diff --check 通过。没有使用 Key 或运行新的付费游戏测试。

本次未更新本机演示入口，也未将其他成员分支合入生产环境。交付分支为 `member5-acceptance-signals`，只包含恢复模块、专属测试和本技术说明；需要 Member 1 集成双方改动后，再做真实模型游戏联调。
