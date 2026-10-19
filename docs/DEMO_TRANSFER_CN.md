# Member 1：演示电脑接收与运行指南

此包用于转移 Agent 和验证记录，不包含 API Key。当前测试电脑不是最终演示机；安装成功也不等于正式彩排完成。

## 1. 环境与安装

优先使用 macOS，Python 3.11 或以上。当前已验证的是 Apple silicon macOS / Python 3.12。Linux 还需要可用的 bwrap；Windows 严格沙箱尚未适配。

解压源码包，在源码目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m playwright install chromium-headless-shell
python -m pytest -q
```

也提供 wheel 安装包；只运行 Agent 时可以安装 wheel，但整套测试和团队修改建议使用源码包。macOS 依赖锁定文件来自当前测试机，供排查版本差异；不能直接当作所有系统的通用锁文件。

## 2. 创建全新运行目录

保持虚拟环境已激活，在源码目录外新建一个运行目录。该目录开始时只放老师给的规格和本机私有配置。不要提前把样例游戏复制进 game/。

```bash
mkdir ../demo-run
cp agent.env.example ../demo-run/agent.env
cd ../demo-run
```

将老师给的规格保存为 game_design_prompt.md。使用本机编辑器打开 agent.env，填写老师提供的 Key，并设置：

```text
AGENT_MODEL=openrouter/z-ai/glm-5.3
OPENROUTER_API_KEY=在本机填写老师提供的Key
AGENT_MAX_TOKENS=32000
AGENT_REASONING_MAX_TOKENS=8000
AGENT_MAX_RUNTIME=填写老师允许的秒数
```

最后一项必须填有效数字；正式时长尚未确认。历史测试的 1200 秒是测试预算，不是老师的正式安排。不要把 Key 发群、放进截图、提交 GitHub 或打入交付包。

## 3. 自检与真实彩排

```bash
python -m agent.preflight --probe-model
python -m agent game_design_prompt.md
```

自检应显示严格沙箱、浏览器和模型试调用通过。--probe-model 会产生一次实际模型调用费用；无此参数时只检查本机环境。

Agent 会在 game/ 生成游戏，在 traces/ 留下证据。游戏可能是浏览器游戏，也可能是终端游戏；老师允许两者，Agent 按规格选择。终端游戏仍使用同一个 Agent 入口，不需要另外编写一套 Agent。具体试玩命令以生成的 game/README.md 为准。

## 4. 彩排签收

- 记录最终源码提交号、演示机系统与 Python 版本、模型标识和运行预算。
- 检查结果是否 succeeded；failed 或 exhausted 必须如实保留。
- 根据 game/README.md 启动并人工试玩游戏，核对规则；自动验收通过不能替代人工审阅。
- 检查 traces 中报告、事件和截图/进程证据完整，确认游戏服务器、浏览器与子进程已退出。
- 用归档工具导出证据，排除 agent.env 与实际 Key。保存成功和失败两类记录。

## 5. 尚待团队补充

| 事项 | 接收人 | 完成标准 |
|---|---|---|
| 正式允许 Agent 运行时长 | Member 1 向老师确认 | 写入最终配置，并按该预算彩排 |
| 最终演示电脑 | Member 1 组织全员 | 在该电脑完成自检、真实游戏生成与人工试玩 |
| 旧 32 次原始记录 | Member 1 | 提供原始 game/、traces/、截图及提交号，去除凭据后归档 |

旧 32 次的统计报告在仓库中，但完整原始目录未在 GitHub 主分支、可见历史、Releases、Actions artifacts 中找到；请从当时运行的电脑或备份获取。无法获取的记录应标为缺失，不得由新测试代替旧原始数据。

## 6. 查看归档证据

七项证据总包内每个规格有一个独立 ZIP，包含当次 game/、traces/、规格与元数据。解压单次 ZIP 后，从 game/README.md 查看试玩命令，从 traces/<run_id>/ 查看报告、事件和截图。

原始 JSON 中可能保留测试电脑的绝对路径，这是当次记录的一部分；换电脑后请按归档中的同一相对目录定位文件，不要把旧绝对路径当作新电脑的有效路径。原始事件保持不改写；重新运行应创建新目录和新 run_id，不要覆盖旧证据。
