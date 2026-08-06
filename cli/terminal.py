"""终端交互主逻辑。"""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path
from typing import Any, ClassVar

import yaml
from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from agent.analyzer import TaskAnalyzer
from agent.memory import MemoryManager
from agent.planner import ExecutionPlanner
from agent.router import Router
from models.manager import ModelManager
from models.registry import ProviderRegistry
from providers.base import ProviderError, RateLimitError
from storage.database import Database
from storage.encryption import SecureVault
from storage.exceptions import StorageError
from storage.history import ConversationHistory
from storage.permissions import secure_directory, secure_file


class TerminalApp:
    """LocalAI Agent Router 终端程序。"""

    COMMANDS: ClassVar[dict[str, str]] = {
        "/help": "显示帮助信息",
        "/quit": "退出程序",
        "/exit": "退出程序",
        "/model list": "列出所有模型",
        "/model <name>": "强制使用指定模型",
        "/model auto": "恢复自动调度",
        "/model test <name>": "测试模型连接",
        "/model add": "交互式添加模型",
        "/model update <name>": "交互式修改模型",
        "/model remove <name>": "删除模型（需要确认）",
        "/model enable|disable <name>": "启用或禁用模型",
        "/mode <name>": "切换工作模式",
        "/history": "查看最近会话",
        "/history search <keyword>": "搜索历史",
        "/history show <id>": "查看会话详情",
        "/history export <id> <json|md> [path]": "导出会话",
        "/stats": "查看今日统计",
        "/stats monthly": "查看本月统计",
        "/clear": "清空当前会话上下文",
    }

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.data_dir = project_dir / "data"
        self.config_dir = project_dir / "config"
        self.console = Console()
        self._closed = False
        self._log_sink_id = self._configure_logging()

        # 初始化底层组件
        self.vault = SecureVault(
            self.data_dir, password_provider=self._prompt_master_password
        )
        self.db = Database(self.data_dir / "localai.db")
        self.db.create_tables()
        self.history = ConversationHistory(self.db.get_session())

        self.model_manager = ModelManager(
            self.config_dir / "models.yaml", self.vault
        )
        self.registry = ProviderRegistry()
        self.analyzer = TaskAnalyzer(self.config_dir / "rules.yaml")
        self.router = Router(self.model_manager, self.registry, self.analyzer)
        self.planner = ExecutionPlanner(self._load_modes())
        self.memory = MemoryManager()

        self.current_mode = "coder"
        self.current_session_id: str | None = None
        self._ensure_session()

    def _configure_logging(self) -> int:
        log_path = self.data_dir / "logs" / "localai.log"
        secure_directory(log_path.parent)
        sink_id = logger.add(
            log_path,
            rotation="5 MB",
            retention="14 days",
            encoding="utf-8",
            level="INFO",
            enqueue=False,
        )
        logger.debug(f"文件日志 sink 已启用: {sink_id}")
        secure_file(log_path)
        return sink_id

    def _prompt_master_password(self, creating: bool = False) -> str:
        """在系统钥匙串不可用时请求本地加密主密码。"""
        self.console.print(
            "[yellow]系统凭据管理器不可用，需要本地主密码保护 API Key。[/yellow]"
        )
        password = Prompt.ask("本地主密码（至少 12 个字符）", password=True)
        if not creating:
            return password
        confirmation = Prompt.ask("再次输入本地主密码", password=True)
        if password != confirmation:
            raise ValueError("两次输入的本地主密码不一致，未保存 API Key")
        return password

    def close(self) -> None:
        """释放数据库与日志资源。"""
        if self._closed:
            return
        self.history.db.close()
        self.db.engine.dispose()
        logger.remove(self._log_sink_id)
        self._closed = True

    def _ensure_session(self) -> None:
        if self.current_session_id is None:
            self.current_session_id = self.history.create_session(
                title="新会话", mode=self.current_mode
            )

    def _load_modes(self) -> dict[str, Any]:
        modes_path = self.config_dir / "modes.yaml"
        if not modes_path.exists():
            return {}
        try:
            with modes_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw_modes = data.get("modes", {})
            if isinstance(raw_modes, dict):
                return {
                    str(name): {"name": str(name), **(config or {})}
                    for name, config in raw_modes.items()
                    if isinstance(config, dict)
                }
            if isinstance(raw_modes, list):
                return {
                    str(mode["name"]): mode
                    for mode in raw_modes
                    if isinstance(mode, dict) and mode.get("name")
                }
            raise ValueError("modes 必须是映射或列表")
        except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
            logger.error(f"加载模式配置失败: {exc}")
            return {}

    def _set_mode(self, mode_name: str) -> None:
        if mode_name not in self.planner.modes:
            available = ", ".join(self.planner.modes.keys())
            self.console.print(f"[red]未知模式: {mode_name}。可用: {available}[/red]")
            return
        self.current_mode = mode_name
        mode = self.planner.modes[mode_name]
        self.memory.set_system_prompt(mode.get("system_prompt", ""))
        if self.current_session_id:
            self.history.update_session_mode(self.current_session_id, mode_name)
        self.console.print(f"[green]已切换到 {mode.get('display_name', mode_name)}[/green]")

    def _show_help(self) -> None:
        table = Table(title="命令列表", show_header=True, header_style="bold cyan")
        table.add_column("命令", style="bold")
        table.add_column("说明")
        for cmd, desc in self.COMMANDS.items():
            table.add_row(cmd, desc)
        self.console.print(table)

    def _list_models(self) -> None:
        table = Table(title="模型列表", show_header=True, header_style="bold cyan")
        table.add_column("名称")
        table.add_column("Provider")
        table.add_column("Model")
        table.add_column("状态")
        table.add_column("能力")
        table.add_column("优先级")
        for cfg in self.model_manager.list_models():
            if not cfg.enabled:
                status = "[red]禁用[/red]"
            elif not cfg.api_key_encrypted:
                status = "[yellow]未配置密钥[/yellow]"
            else:
                status = "[green]启用[/green]"
            table.add_row(
                cfg.name,
                cfg.provider,
                cfg.model,
                status,
                ", ".join(cfg.capabilities),
                str(cfg.priority),
            )
        self.console.print(table)

    def _handle_model_command(self, args: list[str]) -> None:
        if not args:
            self._list_models()
            return
        sub = args[0].lower()
        if sub == "list":
            self._list_models()
        elif sub == "auto":
            self.router.force_model(None)
            self.console.print("[green]已恢复自动调度[/green]")
        elif sub == "test" and len(args) >= 2:
            asyncio.run(self._test_model(args[1]))
        elif sub == "add":
            self._add_model_interactive()
        elif sub == "update" and len(args) >= 2:
            self._update_model_interactive(args[1])
        elif sub == "remove" and len(args) >= 2:
            self._remove_model(args[1])
        elif sub in ("enable", "disable") and len(args) >= 2:
            self._set_model_enabled(args[1], sub == "enable")
        else:
            cfg = self.model_manager.get_model(sub)
            if cfg is None:
                self.console.print(f"[red]未知模型: {sub}[/red]")
                return
            if not cfg.enabled:
                self.console.print(f"[red]模型已禁用: {sub}[/red]")
                return
            if not cfg.api_key_encrypted:
                self.console.print(f"[red]模型未配置 API Key: {sub}[/red]")
                return
            self.router.force_model(sub)
            self.console.print(f"[green]已强制使用模型: {sub}[/green]")

    async def _test_model(self, name: str) -> None:
        cfg = self.model_manager.get_model(name)
        if cfg is None:
            self.console.print(f"[red]未知模型: {name}[/red]")
            return
        provider = self.router._create_provider(cfg)
        if provider is None:
            self.console.print("[red]无法创建 provider，请检查 API Key[/red]")
            return
        self.console.print(f"[yellow]正在测试 {name} ...[/yellow]")
        try:
            ok = await provider.validate()
            if ok:
                self.console.print(f"[green]{name} 连接正常[/green]")
        except ProviderError as exc:
            self.console.print(f"[red]{name} 连接失败: {exc.message}[/red]")
        finally:
            await provider.close()

    def _add_model_interactive(self) -> None:
        name = Prompt.ask("模型别名")
        provider = Prompt.ask("Provider", choices=self.registry.list_providers())
        base_url = Prompt.ask("API 地址")
        model = Prompt.ask("模型名称")
        api_key = Prompt.ask("API Key", password=True)
        caps = Prompt.ask("能力标签（逗号分隔，如 coding,writing）")
        try:
            priority = int(Prompt.ask("优先级（数字越小越高）", default="100"))
            self.model_manager.add_model(
                name=name,
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
                capabilities=[c.strip() for c in caps.split(",") if c.strip()],
                priority=priority,
            )
            self.console.print(f"[green]模型 {name} 添加成功[/green]")
        except (ValueError, OSError, StorageError) as exc:
            self.console.print(f"[red]添加失败: {exc}[/red]")

    def _update_model_interactive(self, name: str) -> None:
        cfg = self.model_manager.get_model(name)
        if cfg is None:
            self.console.print(f"[red]未知模型: {name}[/red]")
            return
        base_url = Prompt.ask("API 地址", default=cfg.base_url)
        model = Prompt.ask("模型名称", default=cfg.model)
        caps = Prompt.ask("能力标签（逗号分隔）", default=",".join(cfg.capabilities))
        priority_text = Prompt.ask("优先级", default=str(cfg.priority))
        api_key = Prompt.ask("新 API Key（留空则保持不变）", password=True, default="")
        try:
            updates: dict[str, Any] = {
                "base_url": base_url,
                "model": model,
                "capabilities": [c.strip() for c in caps.split(",") if c.strip()],
                "priority": int(priority_text),
            }
            if api_key:
                updates["api_key"] = api_key
            self.model_manager.update_model(name, **updates)
            self.console.print(f"[green]模型 {name} 已更新[/green]")
        except (ValueError, OSError) as exc:
            self.console.print(f"[red]更新失败: {exc}[/red]")

    def _remove_model(self, name: str) -> None:
        if self.model_manager.get_model(name) is None:
            self.console.print(f"[red]未知模型: {name}[/red]")
            return
        if not Confirm.ask(f"确认删除模型 {name}？", default=False):
            self.console.print("[dim]已取消[/dim]")
            return
        try:
            self.model_manager.remove_model(name)
            if self.router.get_forced_model() == name:
                self.router.force_model(None)
            self.console.print(f"[green]模型 {name} 已删除[/green]")
        except (ValueError, OSError) as exc:
            self.console.print(f"[red]删除失败: {exc}[/red]")

    def _set_model_enabled(self, name: str, enabled: bool) -> None:
        try:
            self.model_manager.set_enabled(name, enabled)
            state = "启用" if enabled else "禁用"
            self.console.print(f"[green]模型 {name} 已{state}[/green]")
        except (ValueError, OSError) as exc:
            self.console.print(f"[red]操作失败: {exc}[/red]")

    def _handle_history_command(self, args: list[str]) -> None:
        if len(args) >= 2 and args[0] == "show":
            self._show_history(args[1])
            return
        if len(args) >= 3 and args[0] == "export":
            self._export_history(args[1], args[2], args[3:])
            return
        if len(args) >= 2 and args[0] == "search":
            keyword = " ".join(args[1:])
            sessions = self.history.search_sessions(keyword)
        else:
            sessions = self.history.list_sessions(limit=20)

        if not sessions:
            self.console.print("[dim]暂无历史会话[/dim]")
            return
        table = Table(title="历史会话", show_header=True)
        table.add_column("ID", style="dim")
        table.add_column("标题")
        table.add_column("模式")
        table.add_column("更新时间")
        for s in sessions:
            table.add_row(
                s.id[:8],
                s.title,
                s.mode or "-",
                s.updated_at.replace(tzinfo=UTC)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M"),
            )
        self.console.print(table)

    def _resolve_session_id(self, value: str) -> str | None:
        exact = self.history.get_session(value)
        if exact is not None:
            return exact.id
        matches = [s.id for s in self.history.list_sessions(limit=1000) if s.id.startswith(value)]
        return matches[0] if len(matches) == 1 else None

    def _show_history(self, value: str) -> None:
        session_id = self._resolve_session_id(value)
        if session_id is None:
            self.console.print(f"[red]未找到唯一会话: {value}[/red]")
            return
        session = self.history.get_session(session_id)
        if session is None:
            return
        self.console.print(
            Panel.fit(
                f"[bold]{session.title}[/bold]\nID: {session.id}\n模式: {session.mode or '-'}",
                title="会话详情",
            )
        )
        for message in self.history.get_messages(session_id):
            title = message.role if not message.model else f"{message.role} · {message.model}"
            self.console.print(Panel(Markdown(message.content), title=title))

    def _export_history(self, value: str, export_format: str, path_args: list[str]) -> None:
        session_id = self._resolve_session_id(value)
        if session_id is None:
            self.console.print(f"[red]未找到唯一会话: {value}[/red]")
            return
        normalized = export_format.lower()
        if normalized not in ("json", "md", "markdown"):
            self.console.print("[red]导出格式必须是 json 或 md[/red]")
            return
        suffix = "json" if normalized == "json" else "md"
        output_path = (
            Path(" ".join(path_args)).expanduser()
            if path_args
            else self.data_dir / "exports" / f"{session_id}.{suffix}"
        )
        try:
            if suffix == "json":
                self.history.export_session_to_json(session_id, output_path)
            else:
                self.history.export_session_to_markdown(session_id, output_path)
            self.console.print(f"[green]已导出到 {output_path.resolve()}[/green]")
        except (OSError, ValueError) as exc:
            self.console.print(f"[red]导出失败: {exc}[/red]")

    def _handle_stats_command(self, args: list[str]) -> None:
        if args and args[0] == "monthly":
            report = self.history.monthly_report()
            title = f"{report['year']}-{report['month']:02d} 月度统计"
        else:
            report = self.history.daily_report()
            title = f"{report['date']} 今日统计"

        table = Table(title=title, show_header=True)
        table.add_column("模型")
        table.add_column("输入 Token")
        table.add_column("输出 Token")
        table.add_column("调用次数")
        table.add_column("预估花费")
        for row in report["models"]:
            table.add_row(
                row["model"],
                str(row["input_tokens"]),
                str(row["output_tokens"]),
                str(row["calls"]),
                f"${row['cost']:.6f}",
            )
        table.add_row(
            "[bold]合计[/bold]",
            str(report["total_input"]),
            str(report["total_output"]),
            str(report["total_calls"]),
            f"[bold]${report['total_cost']:.6f}[/bold]",
        )
        self.console.print(table)

    async def _ask(self, raw_input: str) -> None:
        if not raw_input.strip():
            self.console.print("[dim]请输入问题或命令[/dim]")
            return
        self._ensure_session()
        plan = self.planner.parse(raw_input, current_mode=self.current_mode)

        # 刷新规则热加载
        self.analyzer.reload()

        mode_cfg = self.planner.modes.get(self.current_mode, {})
        provider = self.router.select_model(
            raw_input,
            mode_model=mode_cfg.get("default_model"),
        )
        if provider is None:
            detail = self.router.last_error or "没有可用的模型，请先配置 API Key"
            self.console.print(f"[red]{detail}[/red]")
            return

        # 构建 system prompt
        system_prompt = mode_cfg.get("system_prompt", "")
        messages = self.planner.build_messages(
            plan,
            system_prompt=system_prompt,
            history=self.memory.get_messages(),
        )

        self.console.print(f"[cyan][检测任务] {self.analyzer.analyze(raw_input)['primary_tag']}[/cyan]")
        self.console.print(f"[cyan][选择模型] {provider.name}[/cyan]")

        # 流式输出
        full_answer = ""
        used_model = provider.name
        usage: dict[str, int] = {}
        md = Markdown("")
        try:
            with Live(md, console=self.console, refresh_per_second=10) as live:
                async for chunk in provider.chat(messages, stream=True, **plan.params):
                    full_answer += chunk
                    live.update(Markdown(full_answer))
            usage = provider.get_usage()
        except RateLimitError as exc:
            self.console.print(f"[yellow]模型 {provider.name} 限流，尝试 fallback: {exc.message}[/yellow]")
            full_answer, used_model, usage = await self._fallback(
                messages, provider.name, plan.params, full_answer
            )
        except ProviderError as exc:
            self.console.print(f"[yellow]模型 {provider.name} 失败，尝试 fallback: {exc.message}[/yellow]")
            full_answer, used_model, usage = await self._fallback(
                messages, provider.name, plan.params, full_answer
            )
        finally:
            await provider.close()

        # 保存到记忆与历史
        self.memory.add("user", raw_input)
        self.memory.add("assistant", full_answer, model=used_model)
        session_id = self.current_session_id
        if session_id is None:
            raise RuntimeError("会话初始化失败")
        self.history.add_message(session_id, "user", raw_input)
        self.history.add_message(session_id, "assistant", full_answer, model=used_model)

        # 优先使用 Provider 返回的真实 usage，服务未返回时才本地估算。
        input_tokens = usage.get(
            "input_tokens", sum(self._estimate_tokens(m["content"]) for m in messages)
        )
        output_tokens = usage.get("output_tokens", self._estimate_tokens(full_answer))
        cfg = self.model_manager.get_model(used_model)
        cost = 0.0
        if cfg:
            cost = (
                input_tokens / 1000 * cfg.cost_per_1k_input
                + output_tokens / 1000 * cfg.cost_per_1k_output
            )
        self.history.record_usage(
            session_id, used_model, input_tokens, output_tokens, cost
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return (len(text) + 3) // 4 if text else 0

    async def _fallback(
        self,
        messages: list[dict[str, str]],
        failed_name: str,
        params: dict[str, Any],
        partial_answer: str = "",
    ) -> tuple[str, str, dict[str, int]]:
        chain = self.router.build_fallback_chain(failed_name)
        try:
            for fallback_provider in chain:
                if fallback_provider.name == failed_name:
                    continue
                try:
                    self.console.print(
                        f"[yellow]切换到 fallback 模型: {fallback_provider.name}[/yellow]"
                    )
                    full_answer = ""
                    with Live(Markdown(""), console=self.console, refresh_per_second=10) as live:
                        async for chunk in fallback_provider.chat(
                            messages, stream=True, **params
                        ):
                            full_answer += chunk
                            live.update(Markdown(full_answer))
                    return (
                        full_answer,
                        fallback_provider.name,
                        fallback_provider.get_usage(),
                    )
                except ProviderError as exc:
                    self.console.print(
                        f"[red]{fallback_provider.name} 也失败了: {exc.message}[/red]"
                    )
            self.console.print("[red]所有 fallback 模型均不可用[/red]")
            return partial_answer, failed_name, {}
        finally:
            for fallback_provider in chain:
                await fallback_provider.close()

    def _handle_command(self, text: str) -> bool:
        """处理命令，返回是否继续运行。"""
        parts = text.strip().split()
        if not parts:
            return True
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/quit", "/exit"):
            return False
        if cmd == "/help":
            self._show_help()
        elif cmd == "/model":
            self._handle_model_command(args)
        elif cmd == "/mode":
            if args:
                self._set_mode(args[0])
            else:
                self.console.print(f"[dim]当前模式: {self.current_mode}[/dim]")
        elif cmd == "/history":
            self._handle_history_command(args)
        elif cmd == "/stats":
            self._handle_stats_command(args)
        elif cmd == "/clear":
            self.memory.clear()
            self.current_session_id = None
            self._ensure_session()
            self.console.print("[green]已清空当前会话上下文[/green]")
        else:
            self.console.print(f"[red]未知命令: {cmd}，输入 /help 查看帮助[/red]")
        return True

    def run(self) -> None:
        """启动终端交互循环。"""
        self.console.print(
            Panel.fit(
                "[bold cyan]LocalAI Agent Router[/bold cyan]\n"
                "输入 /help 查看命令，输入问题即可开始对话。",
                title="欢迎",
            )
        )
        self._set_mode(self.current_mode)

        try:
            while True:
                try:
                    user_input = Prompt.ask("\n[bold green]你[/bold green]")
                except (EOFError, KeyboardInterrupt):
                    self.console.print("\n[dim]再见[/dim]")
                    break

                if user_input.startswith("/"):
                    if not self._handle_command(user_input):
                        break
                else:
                    try:
                        asyncio.run(self._ask(user_input))
                    except Exception as exc:  # noqa: BLE001 - 交互循环不能因单次请求退出
                        logger.exception("对话处理失败")
                        self.console.print(f"[red]处理失败: {exc}[/red]")
        finally:
            self.close()
