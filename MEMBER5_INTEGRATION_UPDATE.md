# Member 5 第二阶段联调说明

## 结论

可以开始接口联调，不需要等待模型或 API Key。本轮只增加成员 5 的测试和说明，恢复策略本身已经满足“验证失败给 REPAIR、超时与未知副作用禁止盲 RETRY”。不修改其他成员目录、controller.py 或 state.py。

Member 5 分支：member5-recovery-evaluation。

## 本轮实际完成

新增 tests/test_recovery_verifier.py，使用真实 Controller、RuleBasedRecovery 和未经修改的 GameVerifier。Planner、Runtime、Tracer 是测试替身；harness 返回值是模拟数据，没有启动浏览器、游戏或模型调用。

Member 4 来源：origin/member4-ZHOU-SIHAN-verification，提交 ca2561d161569a4108859fcb1bd11565d114d36e。

当前 Member 5 分支尚未合入其他成员生产模块，因此把 Member 4 的四个验证包源文件原样导出到仓库外的临时测试目录，通过 MEMBER5_VERIFIER_DIR 指定。没有复制或修改其他成员的项目目录。根目录 agent.verification 正式集成后，测试默认使用根目录副本，不再需要这个环境变量。

## 七个场景及结果

| 场景 | 本次结果 |
|---|---|
| 页面启动成功但 interaction 失败 | 真实 GameVerifier 生成失败报告，Recovery 返回 REPAIR；模拟 Planner 修改文件形成新版本，再验通过 |
| 首次验收全部通过 | 无恢复调用，正常结束 |
| harness 非零退出 | REPAIR，不直接重放原命令；修改版本后重新验收通过 |
| harness 超时、结果未知 | REPAIR；测试 Planner 先 inspect_workspace，再修改和重新验收；没有重复原 action_id |
| PROCESS_CLEANUP_FAILED | ABORT；失败后不再执行开发工具 |
| 相同验收失败持续出现 | 在控制器恢复预算上限结束，EXHAUSTED，不无限循环 |
| 每次 harness 运行都改变工作区版本 | 复现验收循环重启，最终 EXHAUSTED，没有误报成功；这是待解决的集成问题 |

冻结的验收标准没有被恢复模块修改；成功路径最终报告带有新工作区版本和非空证据引用。这些引用来自模拟数据，未证明真实截图存在。

## 验证记录

环境：Python 3.11+ 的项目要求，使用工作区自带 Python 执行。

```bash
# 成员 4 根目录包已集成时
python -m unittest tests.test_recovery_verifier -v
python -m unittest discover -v

# 尚未集成时，指定未经修改的 verification 包目录
MEMBER5_VERIFIER_DIR=/absolute/path/to/verification python -m unittest tests.test_recovery_verifier -v
MEMBER5_VERIFIER_DIR=/absolute/path/to/verification python -m unittest discover -v
```

本次指定了 Member 4 原样快照：新增 7 项通过；完整主目录测试 86 项通过，无跳过。

未集成且未指定环境变量时，这个测试类明确跳过，不能声称 86 项通过。原 79 项仍可运行。测试快照仅用于复现接口行为，不等同于生产工厂已集成。

未测试：真实 API、实际生成游戏、真实 Playwright、生产 LocalRuntime、生产 tracing、严格沙箱、实际进程清理、npm 安装。

## 需要具体成员配合

### Member 1 Shirley 集成与控制器

- 在生产工厂同时注入真实 planner、runtime、GameVerifier、RuleBasedRecovery 和 tracer。
- 保留预算与重试的统一管理，不用 mocks 代替生产模块。
- 未知副作用之后的检查顺序仍未被控制器强制；需要统一约定并落实。
- 本轮没有替你合并其他人的 PR，也没有修改你的控制器。

### Member 2 Planner

- 超时后根据 last_failure、workspace_revision=None，先提出 inspect_workspace，确认状态后再写入或运行。
- 本轮测试使用遵守上述规则的模拟 Planner，不代表你当前实现已强制该行为。
- 将模型输出解析失败与程序内部异常区分，避免把所有 ValueError 当成可重试错误。
- 当前恢复策略不能可靠识别所有 LiteLLM 异常，请一起约定专用异常或结构化错误。未知异常保守终止。

### Member 3 敏菲 Runtime 与 Member 4 ZHOU SIHAN Verifier

请优先联合确认“截图等证据输出改变 workspace_revision”问题：

1. GameVerifier 的 cycle key 包含 workspace_revision；只要版本变化，下一次 next_check 就清空当前验收步骤和证据。
2. harness 默认将 after_load.png、after_input.png 写入 game/.verification-evidence/。
3. 当前检查到的成员 3 版本计算未忽略该目录；截图文件创建或改变会使版本变化。
4. 测试用每次 harness 返回新版本复现了循环重启，直到步骤预算耗尽。

请协商证据目录、证据归档和代码版本计算的边界，确保真实游戏代码变化仍会使旧验收失效。不要仅为通过测试冻结 workspace_revision。成员 5 只提供复现和恢复测试，不修改你们的实现。

### Member 6 Li Jiakun Tracing

- 持久化 failure、recovery、重新验证和最终终态，保持运行编号与序号可追踪。
- 与成员 3/4 确认证据从 game/.verification-evidence/ 归档到 traces 的实际链路。stdout/stderr output_refs 不自动等同于截图文件已经归档。
- 本轮使用测试 tracer，没有验证真实文件归档。

### 我 Member 5 后续任务

- 维护恢复场景和回归测试；在团队根目录集成后复跑，替换临时快照加载方式。
- 与 Member 2 对齐模型异常和输出解析错误语义，再修改自己负责的分类与策略。
- 在工厂和浏览器环境就绪后参与真实失败、修复与复验测试。
- 随后在干净运行目录执行测试需求，收集真实成功率、耗时、失败类别与恢复结果。模拟结果不计入真实游戏实验。

## 完成标准

本阶段完成标准是接口闭环和失败安全性；真实游戏完整运行仍是后续联合验收。不能把“86 项测试通过”写成“全系统游戏联调通过”。
