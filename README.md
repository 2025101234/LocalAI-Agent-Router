# LocalAI Agent Router

纯本地运行的多模型 AI Agent 平台 —— 你的个人 AI 调度中心。

> 完整部署说明见 [部署指南](DEPLOYMENT.md)。

## 项目简介

LocalAI Agent Router 是一个纯终端运行的多模型 AI Agent 平台。用户只需通过终端与 Agent 交互，系统会根据任务类型自动选择最合适的大语言模型，同时支持完全自定义模型和路由规则。

核心能力：

- 多模型统一接入（DeepSeek / OpenAI / Kimi / 通义千问）
- 智能 Router 调度（强制模型 → 用户规则 → 自动能力匹配 → 默认模型）
- 本地 API Key 加密存储（AES-256-GCM）
- SQLite 对话历史与 Token 统计
- 自动 Fallback 与文件分析
- 工作模式系统（coder / writer / translator / researcher）

## 目录结构

```
LocalAI-Agent-Router/
├── main.py
├── agent/           # 调度器、分析器、规划器、记忆
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

## 运行方式

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
