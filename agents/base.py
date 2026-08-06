"""统一 Agent 运行时抽象与安全子进程工具。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AgentEvent = dict[str, Any]
AgentEmitter = Callable[[AgentEvent], None]


class AgentError(RuntimeError):
    """Agent CLI 不可用、超时或返回失败。"""


class AgentCancelled(AgentError):
    """用户主动停止 Agent。"""


@dataclass(slots=True)
class AgentResult:
    runtime: str
    answer: str
    session_id: str | None = None
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0


@dataclass(slots=True)
class AgentConfig:
    name: str
    display_name: str
    command: str
    enabled: bool
    model: str
    capabilities: list[str]
    priority: int
    permission_mode: str
    timeout_seconds: int
    max_turns: int = 25
    safe_mode: bool = False

    @classmethod
    def from_mapping(cls, name: str, payload: dict[str, Any]) -> AgentConfig:
        command = str(payload.get("command") or name).strip()
        model = str(payload.get("model") or "").strip()
        if model and not re.fullmatch(r"[A-Za-z0-9._:/+-]{1,128}", model):
            raise ValueError(f"Agent {name} 的模型标识无效")
        timeout = int(payload.get("timeout_seconds", 600))
        max_turns = int(payload.get("max_turns", 25))
        if not 10 <= timeout <= 3600:
            raise ValueError(f"Agent {name} 超时必须在 10 到 3600 秒之间")
        if not 1 <= max_turns <= 100:
            raise ValueError(f"Agent {name} max_turns 必须在 1 到 100 之间")
        return cls(
            name=name,
            display_name=str(payload.get("display_name") or name),
            command=command,
            enabled=bool(payload.get("enabled", True)),
            model=model,
            capabilities=[str(item) for item in payload.get("capabilities", [])],
            priority=max(0, int(payload.get("priority", 100))),
            permission_mode=str(payload.get("permission_mode") or ""),
            timeout_seconds=timeout,
            max_turns=max_turns,
            safe_mode=bool(payload.get("safe_mode", False)),
        )


class AgentRuntime(ABC):
    """把本地 Agent CLI 映射为统一的流式运行接口。"""

    runtime_name = ""

    def __init__(self, config: AgentConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace.resolve()
        self._process: asyncio.subprocess.Process | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._process_lock = threading.Lock()
        self._cancel_requested = False

    def cancel(self) -> bool:
        with self._process_lock:
            process = self._process
            loop = self._loop
            if process is None or loop is None or process.returncode is not None:
                return False
            self._cancel_requested = True
            loop.call_soon_threadsafe(process.terminate)
            return True

    def executable(self) -> str | None:
        command_path = Path(self.config.command)
        if command_path.is_absolute():
            return str(command_path) if command_path.is_file() else None
        return shutil.which(self.config.command)

    def status(self) -> dict[str, Any]:
        executable = self.executable()
        return {
            "name": self.config.name,
            "display_name": self.config.display_name,
            "runtime": self.runtime_name,
            "enabled": self.config.enabled,
            "available": bool(executable),
            "ready": bool(executable),
            "detail": "CLI 已安装" if executable else "未找到 CLI 命令",
            "model": self.config.model,
            "capabilities": list(self.config.capabilities),
            "permission_mode": self.config.permission_mode,
        }

    @abstractmethod
    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        """运行或恢复 Agent 会话。"""

    async def _run_jsonl(
        self,
        args: list[str],
        prompt: str,
        consume: Callable[[dict[str, Any]], None],
    ) -> tuple[int, str]:
        executable = self.executable()
        if not executable:
            raise AgentError(f"未找到 {self.config.display_name} 命令: {self.config.command}")
        creation_flags = 0x08000000 if os.name == "nt" else 0
        child_env = os.environ.copy()
        for internal_name in (
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
            "CODEX_PERMISSION_PROFILE",
            "CODEX_SHELL",
            "CODEX_THREAD_ID",
        ):
            child_env.pop(internal_name, None)
        child_env["NO_COLOR"] = "1"
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            cwd=str(self.workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
            creationflags=creation_flags,
        )
        with self._process_lock:
            self._process = process
            self._loop = asyncio.get_running_loop()
            self._cancel_requested = False
        stdin = process.stdin
        stdout = process.stdout
        stderr_pipe = process.stderr
        if stdin is None or stdout is None or stderr_pipe is None:
            process.kill()
            await process.wait()
            raise AgentError("无法创建 Agent 标准输入输出管道")
        stdin.write(prompt.encode("utf-8"))
        await stdin.drain()
        stdin.close()

        async def read_events() -> None:
            while line := await stdout.readline():
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(payload, dict):
                    consume(payload)
            await process.wait()

        stderr_task = asyncio.create_task(stderr_pipe.read())

        async def stop_process() -> None:
            if process.returncode is not None:
                return
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()

        try:
            await asyncio.wait_for(read_events(), timeout=self.config.timeout_seconds)
        except TimeoutError as exc:
            await stop_process()
            raise AgentError(
                f"{self.config.display_name} 运行超过 {self.config.timeout_seconds} 秒，已停止"
            ) from exc
        except asyncio.CancelledError:
            await stop_process()
            raise
        except Exception:
            await stop_process()
            raise
        finally:
            stderr = (await stderr_task).decode("utf-8", errors="replace").strip()
            with self._process_lock:
                cancelled = self._cancel_requested
                self._process = None
                self._loop = None
                self._cancel_requested = False
        if cancelled:
            raise AgentCancelled(f"{self.config.display_name} 任务已停止")
        return process.returncode or 0, stderr[-2000:]

    @staticmethod
    def attachment_prompt(attachments: list[Path]) -> str:
        if not attachments:
            return ""
        paths = "\n".join(f"- {path}" for path in attachments)
        return f"\n\n以下附件位于当前工作区内，请按需读取：\n{paths}"
