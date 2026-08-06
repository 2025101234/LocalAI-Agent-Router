# 部署指南

LocalAI Agent Router 是本地交互程序，不需要公网 Web 服务器、反向代理或常驻守护进程。安装后可运行 `localai gui` 使用可视化界面，也可运行 `localai` 使用终端界面。

## 1. 环境要求

- Python 3.12 或更高版本
- Windows 10/11、主流 Linux 发行版或 macOS
- 至少满足一项：已登录的 Codex CLI、已登录的 Claude Code，或一个 OpenAI Chat Completions 兼容模型服务

## 2. 推荐部署：pipx

`pipx` 会为程序创建独立虚拟环境，同时把 `localai` 命令加入当前用户的 PATH。

```bash
python -m pip install --user pipx
python -m pipx ensurepath
pipx install "git+https://github.com/2025101234/LocalAI-Agent-Router.git"
localai version
localai gui
```

升级：

```bash
pipx upgrade localai-agent-router
```

卸载程序不会自动删除用户数据库：

```bash
pipx uninstall localai-agent-router
```

## 3. 源码部署

```bash
git clone https://github.com/2025101234/LocalAI-Agent-Router.git
cd LocalAI-Agent-Router
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
localai
```

Linux / macOS：

```bash
source .venv/bin/activate
python -m pip install -e .
localai
```

## 4. Wheel 离线部署

先在联网机器下载仓库 Releases 中的 `.whl` 文件和依赖，再复制到目标机器：

```bash
python -m pip install localai_agent_router-0.3.3-py3-none-any.whl
localai version
```

项目目录中的 Wheel 也可以通过以下命令构建：

```bash
python -m pip wheel . --no-deps --wheel-dir dist
```

## 5. 首次配置

推荐先启动可视化界面：

```bash
localai gui
```

在“模型管理”中编辑内置模型、录入 API Key 并点击“测试连接”。也可在终端界面更新一个内置模型：

使用统一 Agent 网关时，先确认本机 CLI 已安装并登录：

```bash
codex --version
codex login status
claude --version
claude auth status --json
```

启动界面后保持“Agent 网关 → 自动 Agent”。默认编程/数学进入 Codex，写作/文档/翻译/研究进入 Claude。详细路由、模型、超时和权限位于 `config/agents.yaml`。

Agent 状态只在本机读取。若 Claude 显示本地代理未启动，请检查 `CLAUDE_CONFIG_DIR/settings.json` 中的 `ANTHROPIC_BASE_URL` 及对应监听端口；程序不会自动改写 Claude 的认证令牌或代理设置。

若 Claude 在运行后显示“上游认证失败（403）”，说明本地代理已连通但其供应商凭据被上游拒绝。请在代理管理程序中更新该供应商凭据，或关闭代理接管后执行 `claude auth login` 重新登录。网关会暂时跳过该 Agent，自动改用 Codex 或普通模型。

```text
/model update deepseek
/model test deepseek
```

或添加自定义/本地模型：

```text
/model add
```

远程地址必须使用 HTTPS。本机 `localhost`、`127.0.0.0/8`、`::1` 可以使用 HTTP，并会自动绕过 `HTTP_PROXY` / `HTTPS_PROXY`。

## 6. 数据目录与备份

通过 pip 或 Wheel 安装时，默认数据目录为：

- Windows：`%LOCALAPPDATA%\localai-agent-router`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/localai-agent-router`

也可指定独立目录：

```bash
localai --project-dir /path/to/localai-data
```

需要备份时，请在程序退出后复制整个用户数据目录。API Key 密文需要系统凭据管理器中的主密钥，或本地 `.master_key` 与对应主密码共同恢复。

不要把运行后的 `models.yaml`、`.master_key`、数据库、日志或导出会话提交到 GitHub。

## 7. 无桌面凭据管理器的服务器

服务器没有可用 keyring 时，程序会要求本地主密码。自动化环境可在启动进程前设置：

Linux / macOS：

```bash
export LOCALAI_MASTER_PASSWORD='至少十二个字符的强密码'
localai
```

Windows PowerShell：

```powershell
$env:LOCALAI_MASTER_PASSWORD = '至少十二个字符的强密码'
localai
```

请通过系统 Secret Manager、CI/CD Secret 或受限环境变量注入密码，不要写入仓库或部署脚本。

## 8. 部署验证

```bash
localai version
localai gui --no-browser
codex login status
claude auth status --json
```

进入程序后依次执行：

```text
/model list
/model test <模型别名>
/stats
/help
```

开发环境可运行完整质量门：

```bash
python -m pip install -e ".[dev]"
pytest -q
```
