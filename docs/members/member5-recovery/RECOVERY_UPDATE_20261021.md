# Member 5 通用恢复改进（2026-10-21，待集成）

## 范围

仅修改 agent/recovery/classify.py、agent/recovery/policy.py，新增 tests/test_recovery_progress.py 和本文。未修改 controller.py、state.py、Planner、Verifier、Runtime 或游戏产物。独立分支 member5-generic-recovery 基于 main 提交，供 Member 1 审核集成；未改正在用于试玩的本机测试副本。

本次改进不依赖游戏名称、按键、得分、胜负条件或文件内容；不包含贪吃蛇/打砖块特例。它改进恢复决策，不保证未知题目一定生成成功。

## 1. 区分验收基础设施故障

- 新增内部分类 verifier_defect。
- 仅在 verifier.* 来源且处于 initializing/verifying 时，识别明确分类 VERIFIER_SCRIPT_ERROR、ACCEPTANCE_PARSE_ERROR、VERIFIER_CONTRACT_ERROR。
- verifier.* 模块直接抛出 NameError、SyntaxError、UnboundLocalError 时，也按验收模块编程故障处理。
- 返回 ABORT，保持游戏及证据，不把坏测试交给 Planner 通过修改游戏绕过，不允许更改冻结标准。
- 不扫描任意命令输出中的报错词。ReferenceError/断言失败的文字本身不能证明是游戏还是测试错误；普通 verification 仍保持待诊断的 REPAIR。

### Member 1 / Member 4 对接限制

当前控制器将失败报告统一转换成 category=verification，恢复视图不含完整报告。上述新增结构化类别是可接收的契约建议，尚未由现有主流程自动产生。Member 4 需提供可信的故障来源，Member 1 再将其映射到 FailureRecord.category 和 verifier.* operation_key。不能让游戏输出自行声明来源。

因此，本次不会自动修复上轮 before 未定义错误，也不会单凭报错文本将其改判为测试故障。具体验收解析失败后静默退回通用验收的情况，仍需 Member 4 修复；恢复模块在没有收到失败事件时无法介入。

现有接口无“修复验收器”动作。确定验收基础设施故障时选择停止保留证据，需要先修复集成再重跑，不假装通过。

## 2. 检测连续无进展

按 run_id 隔离，记录失败类别、阶段、操作/参数、失败消息、工作区版本的指纹。工具 action_id 变化不代表新方案，相同命令会归为同一指纹。

- 同一失败签名、相同工作区版本连续出现：第 1/2 次保留原决策，第 3/4 次 REPLAN，第 5 次 ABORT。
- 工作区版本、失败或操作参数改变，或恢复序号不连续时，重新计数。
- 同一恢复序号的重复请求不会多计一次。
- 无版本信息时不声称检测出无进展。
- 最多保存 128 个 run 的哈希状态；实例无磁盘读写、网络、系统命令或内部重试；不修改 AgentState。
- 清理失败、未知副作用、初始化限制等原有安全处理优先，不会因循环检测转为盲 RETRY。
- 原有全局恢复/步骤/时间限制仍由控制器负责；每第 6 次普通验收恢复重新规划的原策略保持兼容。

### 适用边界

当前接口没有每次验收失败条目及通过数，因此只能识别“报告签名重复且工作区未变”，不能判断改代码后通过率是否提高，也不保证检测所有语义重复。模块实例必须在同一运行中复用；重新创建实例或进程重启会清空记录。全局剩余时间也未传入，不能将单次调用 timeout 当成总剩余时间。

RecoveryDecision.reason 当前写入日志，但并未直接传递给 Planner。REPLAN/ABORT 由控制器实际执行；提示 Planner 采用新策略仍需 Member 1 后续传递诊断信息。

## 3. 通用回归测试

新增 19 项测试，不调用真实模型或浏览器、不消耗 API：

- 可信验收故障及伪装报错的区分；普通游戏报错文字不会冒充验收故障。
- 连续失败时重新规划和停止；工作区变化、错误变化、操作参数变化均重新计数。
- 重复调用幂等、不同 run 隔离、记录数量有界、缺少版本时保守处理。
- 超时、未知副作用和清理失败不会盲重试；输入视图不变。
- 与真实 Controller 的两项联动：第 5 次无进展停止，以及验收模块编程错误不会写游戏文件。

运行恢复相关测试：

```sh
python -m unittest tests.test_recovery tests.test_recovery_progress tests.test_recovery_verifier -q
```

验证结果：

- 恢复单测与 Controller/Verifier 联动：53 项通过。
- 完整回归：245 项，239 通过、6 项因未指定浏览器缓存而跳过，无失败。
- 指定本机已安装浏览器缓存后，补跑 tests.test_integration_tasks 与 tests.test_spec_acceptance：35 项全部通过，包含之前跳过的 6 项浏览器测试。因此完整测试集合已分批覆盖，未发现失败。
- 最初在受限环境运行完整测试，2 项 macOS 沙箱测试因无法启用沙箱报错；在支持系统沙箱的环境重跑后通过。未通过修改成员 3 测试来绕过。
- git diff --check 通过。
- 尚未以此次修改重新运行付费真游戏测试；上轮结果属于修改前版本。
