# LocalAI Agent Router

本地运行的统一 Agent 与多模型调度平台 —— 让 Claude Code、Codex 和普通模型按场景自动接力。

> 完整部署说明见 [部署指南](DEPLOYMENT.md)。

## 项目简介

LocalAI Agent Router 是一个本地运行的统一 Agent 网关，提供浏览器可视化界面和终端界面。系统可以进入本机已登录的 Claude Code 或 Codex Agent，根据当前对话场景自动选择、恢复各自线程并同步跨 Agent 上下文；Agent 不可用时可降级到普通模型路由。

核心能力：

- 多模型统一接入（DeepSeek / OpenAI / Kimi / 通义千问）
- Claude Code / Codex 原生 CLI Agent 接入与会话恢复
- 场景自动路由（编程 → Codex，写作/文档/研究 → Claude）
- 跨 Agent 上下文交接、失败切换、超时与主动停止
- 智能 Router 调度（强制模型 → 用户规则 → 自动能力匹配 → 默认模型）
- 本地 API Key 加密存储（AES-256-GCM）
- SQLite 对话历史与 Token 统计
- 自动 Fallback 与文件分析
- 工作模式系统（coder / writer / translator / researcher）
- 本地浏览器可视化界面（聊天、模型、历史、统计、附件）

## 目录结构

```
LocalAI-Agent-Router/
├── main.py
├── agent/           # 调度器、分析器、规划器、记忆
├── agents/          # Claude Code / Codex Agent 运行时与统一网关
├── providers/       # Provider 接口与实现
├── models/          # 模型配置管理
├── storage/         # 数据库、加密、历史记录
├── cli/             # 终端交互
├── config/          # 配置文件示例
├── tests/           # 测试
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 安装

### 环境要求

- Python 3.12+
- Windows 10/11 或 Linux

### 1. 克隆或解压项目

```bash
cd LocalAI-Agent-Router
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv .venv
```

### 3. 激活虚拟环境

**Windows：**

```powershell
.venv\Scripts\activate
```

**Linux / macOS：**

```bash
source .venv/bin/activate
```

### 4. 安装依赖

仅运行程序：

```bash
pip install -e .
```

开发与测试（包含 pytest、respx 和覆盖率工具）：

```bash
pip install -e ".[dev]"
```

### 从 GitHub 直接安装

```bash
pip install "git+https://github.com/2025101234/LocalAI-Agent-Router.git"
localai
```

希望与其他 Python 项目隔离时，推荐使用 `pipx`：

```bash
pipx install "git+https://github.com/2025101234/LocalAI-Agent-Router.git"
localai
```

## 配置

首次运行前，需要在 `config/models.yaml` 中配置模型，但**不要直接写入明文 API Key**。

推荐方式：启动终端后使用命令添加：

```
/model add
```

按提示输入模型别名、Provider、API 地址、模型名、API Key 等信息，系统会自动加密保存。

API 地址必须使用 HTTPS；只有 `localhost`、`127.0.0.0/8` 和 `::1` 等本机回环地址允许使用 HTTP，以兼容 Ollama 等本地服务。

主密钥默认保存在系统凭据管理器。如果当前系统没有可用的凭据管理器，首次录入 API Key 时程序会要求两次输入至少 12 个字符的本地主密码；后续启动解锁时只需输入一次。非交互部署也可设置环境变量 `LOCALAI_MASTER_PASSWORD`。请妥善保管该密码，遗失后无法恢复已经加密的 API Key。

如需手动编辑配置文件，请将 `api_key_encrypted` 留空，通过 CLI 录入密钥。

## Claude / Codex Agent 网关

Agent 网关复用本机 Claude Code 和 Codex CLI 的登录状态，不会读取或复制它们的认证令牌。请先安装并登录至少一个 Agent：

```bash
codex --version
codex login status
claude --version
claude auth status --json
```

安装与登录方法以官方文档为准：

- [Codex CLI 与非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode.md)
- [Claude Code 安装](https://docs.anthropic.com/en/docs/claude-code/getting-started)
- [Claude Code CLI 参数](https://docs.anthropic.com/en/docs/claude-code/cli-usage)

启动 GUI 后，“Agent 网关”选择器提供：

- `自动 Agent`：按场景自动选择 Claude 或 Codex。
- `Claude Agent` / `Codex Agent`：强制进入指定 Agent，并恢复该 LocalAI 会话对应的原生线程。
- `普通模型路由`：不运行 Agent，只使用原有 Chat Completions 模型。

默认路由位于 `config/agents.yaml`：

| 场景 | 默认 Agent |
| --- | --- |
| coding / math | Codex |
| writing / document / translation / research / general | Claude |

Claude 默认使用 `safe_mode: true` 与 `acceptEdits`，禁用项目自定义 hooks/plugins 但保留内置工具；Codex 默认使用 `workspace-write` sandbox，并通过 `safe_mode: true` 忽略用户 `config.toml`（认证仍复用 `CODEX_HOME`）。这样可以减少不受网关控制的 hooks、MCP 和插件副作用。不要在不可信项目中改成 `bypassPermissions` 或 `danger-full-access`。运行中的 Agent 可以点击“停止”终止，超时进程也会自动回收。

当对话从 Claude 切回 Codex（或反向切换）时，网关会恢复目标 Agent 自己的原生 session，并只同步它上次运行后新增的对话，避免重复灌入完整历史。

如果 Claude 状态显示“本地代理未启动”，说明 Claude 用户设置中的 `ANTHROPIC_BASE_URL` 指向本机端口，但对应代理没有监听。请启动该代理，或在 `CLAUDE_CONFIG_DIR/settings.json` 中恢复可用地址；网关只诊断状态，不会擅自修改 Claude 的认证和代理配置。首选 Agent 不健康时，“自动 Agent”会立即交接给另一个可用 Agent，不等待网络重试耗尽。

## 运行方式

### 可视化界面（推荐）

```bash
localai gui
```

程序只监听 `127.0.0.1`，启动后会自动打开浏览器。也可以使用独立入口：

```bash
localai-gui
```

指定端口或禁止自动打开浏览器：

```bash
localai gui --port 9000 --no-browser
```

可视化界面提供 Claude/Codex Agent 自动切换、切换原因与工具活动展示、流式聊天、工作模式与模型切换、模型增删改/连接测试、文件上传、历史会话查看与导出、Token/费用统计。每次启动会生成随机访问令牌，API 只接受本次本机页面的请求。

### Windows

```powershell
.venv\Scripts\activate
python main.py
```

安装后也可在任意终端使用：

```bash
localai
python -m localai_agent_router
```

源码运行时配置和数据保存在项目目录。通过 wheel/pip 安装后，程序会在用户数据目录创建配置副本并保存数据库与日志：

- Windows：`%LOCALAPPDATA%\localai-agent-router`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/localai-agent-router`

可使用全局选项 `--project-dir` / `-d` 指定其他目录。

首次使用的最短流程：

```text
localai
/model update deepseek
/model test deepseek
你好，请介绍一下你自己
```

也可以用 `/model add` 添加任意 OpenAI Chat Completions 兼容服务；本地 Ollama 等服务可填写 `http://127.0.0.1:<端口>/v1`。

### Linux

```bash
source .venv/bin/activate
python main.py
```

启动后将进入交互式终端：

```
┌─────────────────────────────────────────┐
│  LocalAI Agent Router                   │
│  输入 /help 查看命令，输入问题即可开始对话。 │
└─────────────────────────────────────────┘

你：帮我分析这道 C++ 题
[检测任务] coding
[选择模型] deepseek
[流式输出答案...]
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/quit` / `/exit` | 退出 |
| `/model list` | 列出模型 |
| `/model <name>` | 强制使用某模型 |
| `/model auto` | 恢复自动调度 |
| `/model test <name>` | 测试模型连接 |
| `/model add` | 交互式添加模型 |
| `/model update <name>` | 修改模型地址、名称、能力、优先级或密钥 |
| `/model remove <name>` | 确认后删除模型 |
| `/model enable <name>` | 启用模型 |
| `/model disable <name>` | 禁用模型 |
| `/mode coder` | 切换编程模式 |
| `/mode writer` | 切换写作模式 |
| `/mode translator` | 切换翻译模式 |
| `/mode researcher` | 切换研究模式 |
| `/history` | 查看历史会话 |
| `/history search <关键词>` | 搜索历史 |
| `/history show <id>` | 查看单条会话详情，支持 ID 前缀 |
| `/history export <id> <json\|md> [路径]` | 导出会话 |
| `/stats` | 今日统计 |
| `/stats monthly` | 本月统计 |
| `/clear` | 清空当前会话上下文 |

## 自定义路由规则

编辑 `config/rules.yaml`：

```yaml
rules:
  - name: "编程问题"
    keywords: ["C++", "算法", "debug"]
    model: "deepseek"
```

修改后输入任意问题即可即时生效，无需重启。

## 文件分析

在问题中直接附带文件路径即可：

```
你：请分析这段代码 main.cpp
```

或：

```
你：总结这篇论文 paper.pdf
```

也兼容任务书约定的写法和带空格的引号路径：

```
你：ai ask "D:\docs\my paper.pdf" 请总结
```

支持 `.txt`、`.md`、常见代码文件、JSON/YAML 和 PDF；单文件上限为 10 MB，PDF 同时限制为 200 页和最多 2,000,000 个提取字符。不支持的二进制文件会被拒绝，不会当作文本发送。

本机回环地址会自动绕过 `HTTP_PROXY` / `HTTPS_PROXY`，避免 Ollama 等本地服务被系统代理错误转发；远程 HTTPS 模型仍尊重用户配置的环境代理。

## 测试

```bash
pytest tests/
```

覆盖率验证：

```bash
pytest tests/ --cov=agent --cov=cli --cov=models --cov=providers --cov=storage
```

测试覆盖：

- Provider 调用（含 mock HTTP）
- Router 调度逻辑
- 配置读取与加密存储
- 数据库与历史记录
- Fallback 机制

## 安全说明

- 所有 API Key 使用带随机 nonce 和认证标签的 AES-256-GCM 加密后写入配置。
- 主密钥优先仅保存到系统凭据管理器（keyring）；不可用时使用用户主密码加密后回退到权限受限的本地文件。旧版固定口令格式会在首次读取时自动迁移。
- 远程模型地址强制使用 HTTPS，API 错误响应正文不会写入日志或直接显示。
- 附件内容会被标记为不可信数据，以降低文件内容提示注入风险；敏感文件在 POSIX 系统上使用仅当前用户可读写的权限。
- 配置文件与数据库均保存在本地，不会上传云端。
- API Key 不会出现在模型列表、日志或普通终端输出中。

模板中的模型标识会随官方接口更新，但具体可用模型、地域地址和费用取决于你的服务账户。`cost_per_1k_input` / `cost_per_1k_output` 为 `0` 时仅统计 Token、不估算费用；需要费用统计时可在 `config/models.yaml` 中按账户当前价格填写。

运行日志保存在 `data/logs/localai.log`，自动按 5 MB 轮转并保留 14 天。

## 技术栈

- CLI：Typer + Rich
- HTTP：httpx
- 数据库：SQLite + SQLAlchemy
- 配置：YAML
- 加密：cryptography
- 日志：loguru
- 测试：pytest + respx

## 许可证

MIT
