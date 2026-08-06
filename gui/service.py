"""供本地 Web GUI 使用的应用服务层。"""

from __future__ import annotations

import base64
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC
from pathlib import Path
from threading import RLock
from typing import Any

import yaml
from loguru import logger

from agent.analyzer import TaskAnalyzer
from agent.memory import MemoryManager
from agent.planner import ExecutionPlan, ExecutionPlanner
from agent.router import Router
from models.manager import ModelConfig, ModelManager
from models.registry import ProviderRegistry
from providers.base import ProviderError, RateLimitError
from storage.database import Database
from storage.encryption import SecureVault
from storage.history import ConversationHistory
from storage.permissions import secure_directory, secure_file

EventCallback = Callable[[dict[str, Any]], None]


class ApplicationService:
    """封装聊天、配置、历史和统计，供 GUI API 安全复用。"""

    MAX_UPLOAD_BYTES = 10 * 1024 * 1024
    MAX_TOTAL_UPLOAD_BYTES = 40 * 1024 * 1024
    MAX_UPLOAD_COUNT = 8

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.data_dir = project_dir / "data"
        self.config_dir = project_dir / "config"
        self.lock = RLock()
        self._log_sink_id = self._configure_logging()
        self.vault = SecureVault(self.data_dir)
        self.db = Database(self.data_dir / "localai.db")
        self.db.create_tables()
        self.history = ConversationHistory(self.db.get_session())
        self.model_manager = ModelManager(self.config_dir / "models.yaml", self.vault)
        self.registry = ProviderRegistry()
        self.analyzer = TaskAnalyzer(self.config_dir / "rules.yaml")
        self.planner = ExecutionPlanner(self._load_modes())
        self.router = Router(self.model_manager, self.registry, self.analyzer)
        self.memory = MemoryManager()
        self.current_mode = "coder" if "coder" in self.planner.modes else next(
            iter(self.planner.modes), "default"
        )
        self.current_session_id = self.history.create_session(
            title="新会话", mode=self.current_mode
        )
        self._closed = False

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
        secure_file(log_path)
        return sink_id

    def _load_modes(self) -> dict[str, Any]:
        path = self.config_dir / "modes.yaml"
        if not path.exists():
            return {}
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = loaded.get("modes", {}) if isinstance(loaded, dict) else {}
            if isinstance(raw, dict):
                return {
                    str(name): {"name": str(name), **config}
                    for name, config in raw.items()
                    if isinstance(config, dict)
                }
            if isinstance(raw, list):
                return {
                    str(item["name"]): item
                    for item in raw
                    if isinstance(item, dict) and item.get("name")
                }
        except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
            logger.error(f"GUI 加载模式配置失败: {exc}")
        return {}

    def close(self) -> None:
        if self._closed:
            return
        self.history.db.close()
        self.db.engine.dispose()
        logger.remove(self._log_sink_id)
        self._closed = True

    def vault_status(self) -> str:
        if self.vault._key is not None:
            return "ready"
        if self.vault._load_key_from_keyring() is not None:
            return "ready"
        if (self.data_dir / self.vault.KEY_FILENAME).exists():
            return "locked"
        return "uninitialized"

    def unlock_vault(self, password: str) -> str:
        with self.lock:
            self.vault.unlock(password)
            return self.vault_status()

    @staticmethod
    def _model_payload(config: ModelConfig) -> dict[str, Any]:
        data = asdict(config)
        data.pop("api_key_encrypted", None)
        data["configured"] = bool(config.api_key_encrypted)
        return data

    @staticmethod
    def _local_timestamp(value: Any) -> str:
        if value is None:
            return ""
        return value.replace(tzinfo=UTC).astimezone().isoformat(timespec="minutes")

    def list_models(self) -> list[dict[str, Any]]:
        return [self._model_payload(item) for item in self.model_manager.list_models()]

    def list_modes(self) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "display_name": str(config.get("display_name") or name),
                "description": str(config.get("description") or ""),
            }
            for name, config in self.planner.modes.items()
        ]

    def list_sessions(self, limit: int = 40) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "title": item.title,
                "mode": item.mode,
                "updated_at": self._local_timestamp(item.updated_at),
                "active": item.id == self.current_session_id,
            }
            for item in self.history.list_sessions(limit=limit)
        ]

    def bootstrap(self) -> dict[str, Any]:
        with self.lock:
            return {
                "models": self.list_models(),
                "modes": self.list_modes(),
                "sessions": self.list_sessions(),
                "stats": self.history.daily_report(),
                "monthly_stats": self.history.monthly_report(),
                "current_mode": self.current_mode,
                "current_session_id": self.current_session_id,
                "forced_model": self.router.get_forced_model(),
                "vault_status": self.vault_status(),
            }

    def set_mode(self, mode: str) -> None:
        with self.lock:
            if mode not in self.planner.modes:
                raise ValueError(f"未知模式: {mode}")
            self.current_mode = mode
            self.history.update_session_mode(self.current_session_id, mode)

    def force_model(self, name: str | None) -> None:
        with self.lock:
            if name and self.model_manager.get_model(name) is None:
                raise ValueError(f"未知模型: {name}")
            self.router.force_model(name)

    def new_session(self) -> dict[str, Any]:
        with self.lock:
            self.memory.clear()
            self.current_session_id = self.history.create_session(
                title="新会话", mode=self.current_mode
            )
            return self.get_session(self.current_session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.history.get_session(session_id)
            if session is None:
                raise ValueError("会话不存在")
            return {
                "id": session.id,
                "title": session.title,
                "mode": session.mode,
                "created_at": self._local_timestamp(session.created_at),
                "updated_at": self._local_timestamp(session.updated_at),
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "model": message.model,
                        "created_at": self._local_timestamp(message.created_at),
                    }
                    for message in self.history.get_messages(session_id)
                ],
            }

    def open_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            payload = self.get_session(session_id)
            self.current_session_id = session_id
            if payload["mode"] in self.planner.modes:
                self.current_mode = payload["mode"]
            self.memory.clear()
            for message in payload["messages"][-self.memory.max_messages :]:
                self.memory.add(message["role"], message["content"], message["model"])
            return payload

    def save_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            name = str(payload.get("name") or "").strip()
            existing = self.model_manager.get_model(name)
            values = {
                "provider": str(payload.get("provider") or "openai"),
                "base_url": str(payload.get("base_url") or ""),
                "model": str(payload.get("model") or ""),
                "capabilities": payload.get("capabilities") or [],
                "priority": payload.get("priority", 100),
                "enabled": payload.get("enabled", True),
                "cost_per_1k_input": payload.get("cost_per_1k_input", 0),
                "cost_per_1k_output": payload.get("cost_per_1k_output", 0),
            }
            api_key = str(payload.get("api_key") or "")
            if existing is None:
                config = self.model_manager.add_model(
                    name=name,
                    api_key=api_key,
                    **{key: values[key] for key in (
                        "provider", "base_url", "model", "capabilities", "priority", "enabled"
                    )},
                )
                if values["cost_per_1k_input"] or values["cost_per_1k_output"]:
                    config = self.model_manager.update_model(
                        name,
                        cost_per_1k_input=values["cost_per_1k_input"],
                        cost_per_1k_output=values["cost_per_1k_output"],
                    )
            else:
                updates = dict(values)
                if api_key:
                    updates["api_key"] = api_key
                config = self.model_manager.update_model(name, **updates)
            return self._model_payload(config)

    def delete_model(self, name: str) -> None:
        with self.lock:
            self.model_manager.remove_model(name)
            if self.router.get_forced_model() == name:
                self.router.force_model(None)

    def toggle_model(self, name: str, enabled: bool) -> dict[str, Any]:
        with self.lock:
            return self._model_payload(self.model_manager.set_enabled(name, enabled))

    async def test_model(self, name: str) -> bool:
        with self.lock:
            config = self.model_manager.get_model(name)
            if config is None:
                raise ValueError("模型不存在")
            provider = self.router._create_provider(config)
        if provider is None:
            raise ValueError(self.router.last_error or "模型尚未配置 API Key")
        try:
            return await provider.validate()
        finally:
            await provider.close()

    def _decode_attachments(
        self, attachments: list[dict[str, str]], directory: Path
    ) -> list[Path]:
        if len(attachments) > self.MAX_UPLOAD_COUNT:
            raise ValueError(f"一次最多上传 {self.MAX_UPLOAD_COUNT} 个文件")
        paths: list[Path] = []
        total_bytes = 0
        for index, item in enumerate(attachments):
            name = Path(str(item.get("name") or f"file-{index}")).name
            if not name or name in {".", ".."}:
                raise ValueError("附件名称无效")
            try:
                content = base64.b64decode(item.get("content", ""), validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"附件 {name} 编码无效") from exc
            if len(content) > self.MAX_UPLOAD_BYTES:
                raise ValueError(f"附件 {name} 超过 10 MB")
            total_bytes += len(content)
            if total_bytes > self.MAX_TOTAL_UPLOAD_BYTES:
                raise ValueError("附件总大小超过 40 MB")
            path = directory / f"{index}-{name}"
            path.write_bytes(content)
            paths.append(path)
        return paths

    async def chat(
        self,
        text: str,
        attachments: list[dict[str, str]],
        emit: EventCallback,
    ) -> None:
        if not text.strip() and not attachments:
            raise ValueError("请输入消息或添加附件")
        with self.lock, tempfile.TemporaryDirectory(prefix="localai-gui-") as temp:
            paths = self._decode_attachments(attachments, Path(temp))
            plan = ExecutionPlan(
                user_input=text.strip(),
                file_paths=paths,
                mode=self.current_mode,
                params=dict(self.planner.modes.get(self.current_mode, {}).get("params", {})),
            )
            self.analyzer.reload()
            mode_config = self.planner.modes.get(self.current_mode, {})
            provider = self.router.select_model(
                text + " " + " ".join(path.name for path in paths),
                mode_model=mode_config.get("default_model"),
            )
            if provider is None:
                raise ValueError(self.router.last_error or "没有可用模型，请先配置 API Key")
            messages = self.planner.build_messages(
                plan,
                system_prompt=mode_config.get("system_prompt", ""),
                history=self.memory.get_messages(),
            )
            tag = self.analyzer.analyze(text)["primary_tag"]
            emit({"type": "meta", "model": provider.name, "tag": tag})
            answer = ""
            used_model = provider.name
            usage: dict[str, int] = {}
            try:
                async for chunk in provider.chat(messages, stream=True, **plan.params):
                    answer += chunk
                    emit({"type": "chunk", "content": chunk})
                usage = provider.get_usage()
            except (RateLimitError, ProviderError) as exc:
                emit({"type": "status", "content": f"{provider.name} 失败，正在切换备用模型"})
                answer, used_model, usage = await self._fallback(
                    messages, provider.name, plan.params, answer, emit
                )
                if not answer:
                    raise ValueError(exc.message) from exc
            finally:
                await provider.close()

            self.memory.add("user", text.strip() or "[附件]")
            self.memory.add("assistant", answer, model=used_model)
            self.history.add_message(
                self.current_session_id, "user", text.strip() or "[附件]"
            )
            self.history.add_message(
                self.current_session_id, "assistant", answer, model=used_model
            )
            input_tokens = usage.get(
                "input_tokens",
                sum(self._estimate_tokens(message["content"]) for message in messages),
            )
            output_tokens = usage.get("output_tokens", self._estimate_tokens(answer))
            config = self.model_manager.get_model(used_model)
            cost = 0.0
            if config:
                cost = (
                    input_tokens / 1000 * config.cost_per_1k_input
                    + output_tokens / 1000 * config.cost_per_1k_output
                )
            self.history.record_usage(
                self.current_session_id, used_model, input_tokens, output_tokens, cost
            )
            emit(
                {
                    "type": "done",
                    "model": used_model,
                    "session_id": self.current_session_id,
                    "stats": self.history.daily_report(),
                    "sessions": self.list_sessions(),
                }
            )

    async def _fallback(
        self,
        messages: list[dict[str, str]],
        failed_name: str,
        params: dict[str, Any],
        partial: str,
        emit: EventCallback,
    ) -> tuple[str, str, dict[str, int]]:
        providers = self.router.build_fallback_chain(failed_name)
        try:
            for provider in providers:
                if provider.name == failed_name:
                    continue
                try:
                    emit({"type": "status", "content": f"已切换到 {provider.name}"})
                    answer = ""
                    async for chunk in provider.chat(messages, stream=True, **params):
                        answer += chunk
                        emit({"type": "chunk", "content": chunk})
                    return answer, provider.name, provider.get_usage()
                except ProviderError:
                    continue
            return partial, failed_name, {}
        finally:
            for provider in providers:
                await provider.close()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return (len(text) + 3) // 4 if text else 0

    def export_session(self, session_id: str, export_format: str) -> tuple[bytes, str, str]:
        payload = self.get_session(session_id)
        if export_format == "json":
            import json

            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return content, "application/json; charset=utf-8", f"{session_id}.json"
        if export_format != "md":
            raise ValueError("导出格式必须是 json 或 md")
        lines = [f"# {payload['title']}", ""]
        for message in payload["messages"]:
            lines.extend((f"## {message['role']}", "", message["content"], ""))
        return "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8", f"{session_id}.md"
