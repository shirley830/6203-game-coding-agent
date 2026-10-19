# 演示准备与交接清单

本说明区分已完成的技术工作和必须由老师、演示机负责人、Member 1 提供的外部信息。当前不是“正式演示机全部就绪”的证明。

## 1 模型平台与试调用

用户已确认当前 Key 由老师提供，已通过 OpenRouter 调用 GLM-5.3。若现场换发 Key 或更换平台，仍需用最终配置重新试调用。

在最终演示机上，用最终平台、模型标识及 Key 配置本机 agent.env 后运行：

```bash
python -m agent.preflight --probe-model
```

这会先检查机器，再做一次计费模型调用，不隐藏重试。只有返回 JSON 正文且 ready=true 才通过；认证、超时、空正文、无效 JSON 都失败。不会创建 game/。默认不带 --probe-model 时不调用收费 API，输出会明确说明模型未检查。

试调用使用与 Agent 一致的 Base URL、输出上限与推理预算，因此可提前发现换平台后 reasoning 参数不兼容或正文为空的问题。Key 不要发群、提交或打包。

## 2 最终演示电脑

当前测试电脑是 macOS，但用户已确认不是演示电脑。因此这一项仍需在最终电脑执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m playwright install chromium-headless-shell
python -m agent.preflight --probe-model
```

macOS 需要 sandbox-exec。Linux 需要可用的 bwrap。当前没有严格 Windows 适配。不要通过关闭沙箱来把演示机标记为通过。

用正式机器跑一轮真实 GLM-5.3 游戏，保存 game/、traces/、源码提交号、Python/依赖版本、自检输出与启动命令。当前电脑的成功不能替代这轮验收。

## 3 正式运行时间

需要老师给出允许 Agent 运行的时长，而不只是整组展示时长。现在未知。

```text
AGENT_MAX_RUNTIME=老师允许的秒数
```

该值包含程序自己的 finalization reserve；不要再重复扣除这部分。若要给人工试玩另留时间，应根据正式安排另行预留。不要把测试时使用的 1200 秒当成正式获批时间。

## 4 最佳源码版本

控制器在每次 RequestVerification 后，通过 runtime 保存源码快照。只有与当前规格、冻结验收集、源码版本相符且有真实工具检查的报告，才参与排名。优先比较通过的 mandatory 项，再比较全部有证据的通过项；并列保留较早版本。

失败或预算耗尽时，在剩余收尾时间内恢复最佳已评估源码，保留被替换的源码。恢复不会把 FAILED/EXHAUSTED/CANCELLED 改成 SUCCEEDED。

边界：
- 进程或工作区状态未知时跳过恢复，快照仍保留，避免与残留进程竞争写入。
- 只恢复源码，不回退已安装依赖。恢复后的完整 revision 不匹配时清除旧验证结论。
- 快照拒绝符号链接和特殊文件；默认限制 64 MiB、10000 项和单次 infrastructure timeout。
- 快照损坏、复制过大或超时会记录错误，不应误报成功。
- 快照在 traces/<run_id>/game_versions，控制器记录保存、评分、恢复和跳过事件；不是给模型调用的新开发工具。

## 5 原始运行记录与新记录归档

最初检索 GitHub 主分支、可见 Git 历史、Releases 和 Actions artifacts 时，未找到旧 32 次的完整运行目录。docs/testing/LIVE_MODEL_TESTS.md 明确说明原始目录在仓库外。结果表和三张展示截图不能还原每次源代码、日志与证据。

更新：用户已提供 live_runs_32.zip，32 个独立 run_id、667 张截图，32/32 交付审计通过；历史结果为 25 succeeded、7 exhausted。已原样复制并核验 SHA-256，详见 docs/testing/HISTORICAL_RUN_ARCHIVE_20261019.md。此前搜索记录仍保留作追溯。新测试也使用持久目录，成功和失败结果均保留。

运行全部七个既有规格的新一轮测试：

```bash
python -m evaluation.rehearse --env-file /本机私有配置/agent.env \
  --output-root /持久目录/全新批次名 --max-runtime 1200 --max-spend 1
```

工具使用全新运行目录，保留元数据并导出不含凭据的证据 ZIP。当前版本只支持 OpenRouter 的用量检查。--max-spend 是两轮之间检查的软阈值，一次运行中仍可能超过阈值；它不改变平台限额。费用差值也可能包含同一 Key 的其他并发调用。

历史使用过的 holdout 已不是真正未见测试，不能再把它们称作严格盲测；本轮是回归/重复测试。

## 6 Key 与余额

当前 Key 由老师提供，API Key 不是网站登录凭据；用户无需登录老师账户，也无需知道老师的注册方式。已能读取当前 Key 的 usage、limit、limit_remaining，初次确认剩余额度约 9.07 美元，测试后以最新查询为准。limit_remaining 是 Key 剩余额度，不是账户钱包余额。

账户 credits 接口返回 403，未申请额外权限或更改账户设置。账户钱包余额属于 Key 所属账户的信息，不再要求用户登录确认；若后续出现额度不足或账户余额不足，再请老师确认可用额度或补充 Key。不要把旧文档的 1.98 美元当作当前余额。

## 7 重复测试证据

以本批次 batch_summary.json 和每次 traces 为准，明确源码版本、规格、模型、机器、预算、成功/失败、恢复次数和账单差值。单次成功不等于稳定成功率，失败不能从统计分母排除。报告中的旧结果不替代原始记录。

## 8 分支清理

仅清理经确认已包含于 main 的旧分支，保留 main 和当前交接修复分支。清理前保存 SHA 清单和 Git bundle，删除使用预期 SHA 条件和原子推送，避免误删已被成员继续更新的分支。记录见 docs/testing/BRANCH_CLEANUP_20261019.json。

## 最终签收前仍需填写

- 当前老师提供的 Key：OpenRouter 已验证；现场换发后须重新试调用。
- 老师允许的 Agent 运行秒数：未知。
- 最终演示电脑及其自检、完整彩排证据：未提供。
- 旧 32 次原始运行目录：已收到用户提供的 ZIP 并完成原样归档与核验。
- 当前 Key 可用额度：已能查询；老师账户钱包余额不作为用户签收前置条件。

这些外部事项完成前，不能签收为整份交接全部完成。
