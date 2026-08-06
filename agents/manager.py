"""Agent 配置、可用性检测、场景路由与运行时创建。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

import yaml
from loguru import logger

from agents.base import AgentConfig, AgentRuntime
from agents.claude import ClaudeRuntime
from agents.codex import CodexRuntime


class AgentManager:
    RUNTIMES: ClassVar[dict[str, type[AgentRuntime]]] = {
        "claude": ClaudeRuntime,
        "codex": CodexRuntime,
    }

    def __init__(self, config_path: Path, workspace: Path) -> None:
        self.config_path = config_path
        self.workspace = workspace
        self.settings: dict[str, Any] = {}
        self.configs: dict[str, AgentConfig] = {}
        self._runtime_failures: dict[str, tuple[float, str]] = {}
        self.reload()

    def reload(self) -> None:
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            self.settings = data.get("settings", {}) if isinstance(data, dict) else {}
            raw_agents = data.get("agents", {}) if isinstance(data, dict) else {}
            self.configs = {
                str(name): AgentConfig.from_mapping(str(name), payload)
                for name, payload in raw_agents.items()
                if isinstance(payload, dict) and name in self.RUNTIMES
            }
        except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
            logger.error(f"Agent 配置加载失败: {exc}")
            self.settings = {}
            self.configs = {}

    def runtime(self, name: str) -> AgentRuntime | None:
        config = self.configs.get(name)
        runtime_type = self.RUNTIMES.get(name)
        if config is None or runtime_type is None or not config.enabled:
            return None
        return runtime_type(config, self.workspace)

    def statuses(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for config in sorted(self.configs.values(), key=lambda item: item.priority):
            runtime = self.runtime(config.name)
            status = self.status_for(runtime) if runtime else {
                "name": config.name,
                "display_name": config.display_name,
                "runtime": config.name,
                "enabled": config.enabled,
                "available": False,
                "ready": False,
                "detail": "Agent 已停用",
                "model": config.model,
                "capabilities": list(config.capabilities),
                "permission_mode": config.permission_mode,
            }
            rows.append(status)
        return rows

    def status_for(self, runtime: AgentRuntime) -> dict[str, Any]:
        """Return CLI status combined with a short-lived runtime failure state."""
        status = runtime.status()
        failure = self._runtime_failures.get(runtime.runtime_name)
        if failure is None:
            return status
        retry_at, detail = failure
        if time.monotonic() >= retry_at:
            self._runtime_failures.pop(runtime.runtime_name, None)
            return status
        status["ready"] = False
        status["detail"] = detail
        return status

    def record_failure(self, runtime: AgentRuntime, error: Exception) -> None:
        """Avoid repeatedly routing users to an Agent that just failed to run."""
        message = str(error).lower()
        if "403" in message or "authenticate" in message:
            detail = (
                f"{runtime.config.display_name} 上游认证失败（403），"
                "请更新供应商凭据或重新登录"
            )
        elif "not logged in" in message or "未登录" in message:
            detail = f"{runtime.config.display_name} 未登录，请先完成登录"
        else:
            detail = f"{runtime.config.display_name} 上次运行失败，将在 60 秒后自动重试"
        self._runtime_failures[runtime.runtime_name] = (time.monotonic() + 60, detail)

    def record_success(self, runtime: AgentRuntime) -> None:
        self._runtime_failures.pop(runtime.runtime_name, None)

    def select(self, tags: set[str], forced: str | None = None) -> tuple[AgentRuntime | None, str]:
        if forced == "model":
            return None, "已选择普通模型路由"
        if forced and forced != "auto":
            runtime = self.runtime(forced)
            if runtime is None or not runtime.executable():
                raise ValueError(f"Agent {forced} 未启用或命令不可用")
            status = self.status_for(runtime)
            if not status.get("ready", False):
                raise ValueError(str(status.get("detail") or f"Agent {forced} 当前不可用"))
            return runtime, "用户手动指定"

        primary = min(tags) if tags else "general"
        routing = self.settings.get("routing", {})
        preferred = str(routing.get(primary) or "") if isinstance(routing, dict) else ""
        runtime = self.runtime(preferred) if preferred else None
        if runtime and self.status_for(runtime).get("ready", False):
            return runtime, f"场景 {primary} 自动匹配"

        candidates: list[tuple[int, AgentRuntime]] = []
        for config in self.configs.values():
            candidate = self.runtime(config.name)
            if (
                candidate
                and self.status_for(candidate).get("ready", False)
                and (tags & set(config.capabilities))
            ):
                candidates.append((config.priority, candidate))
        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1], "能力标签自动匹配"
        ready_agents = [
            candidate
            for config in sorted(self.configs.values(), key=lambda item: item.priority)
            if (candidate := self.runtime(config.name))
            and self.status_for(candidate).get("ready", False)
        ]
        if ready_agents:
            return ready_agents[0], f"场景 {primary} 的首选 Agent 不可用，自动交接"
        return None, "没有可用的 Agent，转入普通模型路由"

    def fallback(self, failed: str) -> AgentRuntime | None:
        candidates = [
            self.runtime(config.name)
            for config in sorted(self.configs.values(), key=lambda item: item.priority)
            if config.name != failed
        ]
        return next(
            (
                runtime
                for runtime in candidates
                if runtime and self.status_for(runtime).get("ready", False)
            ),
            None,
        )

    @property
    def fallback_to_models(self) -> bool:
        return bool(self.settings.get("fallback_to_models", True))
