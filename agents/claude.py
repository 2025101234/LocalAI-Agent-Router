"""Claude Code stream-json Agent 适配器。"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from agents.base import AgentEmitter, AgentError, AgentResult, AgentRuntime


class ClaudeRuntime(AgentRuntime):
    runtime_name = "claude"
    PERMISSION_MODES: ClassVar[set[str]] = {
        "acceptEdits", "auto", "default", "dontAsk", "plan"
    }

    def status(self) -> dict[str, Any]:
        status = super().status()
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        config_dir = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        )
        settings_path = config_dir / "settings.json"
        if not base_url and settings_path.is_file():
            try:
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
                env = payload.get("env", {}) if isinstance(payload, dict) else {}
                base_url = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        try:
            parsed = urlsplit(base_url) if base_url else None
            port = (
                parsed.port or (443 if parsed.scheme == "https" else 80)
                if parsed
                else None
            )
        except ValueError:
            status["ready"] = False
            status["detail"] = "Claude ANTHROPIC_BASE_URL 配置无效"
            return status
        if parsed and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            try:
                with socket.create_connection((parsed.hostname, port), timeout=0.2):
                    pass
            except OSError:
                status["ready"] = False
                status["detail"] = f"Claude 本地代理未启动（{parsed.hostname}:{port}）"
            else:
                status["detail"] = "Claude 本地代理可连接"
        return status

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        permission = self.config.permission_mode or "acceptEdits"
        if permission not in self.PERMISSION_MODES:
            raise AgentError(f"Claude permission mode 无效: {permission}")
        args = [
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            permission,
            "--max-turns",
            str(self.config.max_turns),
        ]
        if self.config.safe_mode:
            args.append("--safe-mode")
        if self.config.model:
            args.extend(("--model", self.config.model))
        if session_id:
            args.extend(("--resume", session_id))

        state: dict[str, Any] = {
            "session_id": session_id,
            "answer": "",
            "usage": {},
            "cost": 0.0,
            "error": "",
        }

        def consume(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "system" and event.get("subtype") == "init":
                state["session_id"] = str(event.get("session_id") or "") or session_id
            elif event_type == "assistant":
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                for block in message.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = str(block.get("text") or "")
                        if text:
                            state["answer"] += text
                            emit({"type": "chunk", "content": text})
                    elif block.get("type") == "tool_use":
                        emit(
                            {
                                "type": "activity",
                                "agent": "claude",
                                "activity": str(block.get("name") or "tool_use"),
                                "status": "started",
                            }
                        )
            elif event_type == "result":
                state["session_id"] = str(event.get("session_id") or "") or state["session_id"]
                usage = event.get("usage")
                if isinstance(usage, dict):
                    state["usage"] = {
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                    }
                state["cost"] = float(event.get("total_cost_usd") or 0.0)
                if event.get("is_error"):
                    state["error"] = str(event.get("result") or "Claude Agent 运行失败")
                elif not state["answer"] and event.get("result"):
                    text = str(event["result"])
                    state["answer"] = text
                    emit({"type": "chunk", "content": text})

        full_prompt = prompt + self.attachment_prompt(attachments)
        code, stderr = await self._run_jsonl(args, full_prompt, consume)
        if code or state["error"]:
            raise AgentError(state["error"] or stderr or f"Claude 退出码 {code}")
        if not state["answer"]:
            raise AgentError(stderr or "Claude 未返回 Agent 消息")
        return AgentResult(
            runtime="claude",
            answer=state["answer"],
            session_id=state["session_id"],
            model=self.config.model or "claude-default",
            usage=state["usage"],
            cost=state["cost"],
        )
