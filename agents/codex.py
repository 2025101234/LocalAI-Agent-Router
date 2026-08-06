"""Codex CLI JSONL Agent 适配器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from agents.base import AgentEmitter, AgentError, AgentResult, AgentRuntime


class CodexRuntime(AgentRuntime):
    runtime_name = "codex"
    SANDBOXES: ClassVar[set[str]] = {
        "read-only", "workspace-write", "danger-full-access"
    }

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        sandbox = self.config.permission_mode or "workspace-write"
        if sandbox not in self.SANDBOXES:
            raise AgentError(f"Codex sandbox 无效: {sandbox}")
        if session_id:
            args = ["exec", "resume", "--json"]
            if self.config.safe_mode:
                args.append("--ignore-user-config")
            if self.config.model:
                args.extend(("--model", self.config.model))
            args.extend(("--skip-git-repo-check", session_id, "-"))
        else:
            args = [
                "exec",
                "--json",
                "--sandbox",
                sandbox,
                "--cd",
                str(self.workspace),
                "--skip-git-repo-check",
            ]
            if self.config.model:
                args.extend(("--model", self.config.model))
            if self.config.safe_mode:
                args.append("--ignore-user-config")
            args.append("-")

        state: dict[str, Any] = {
            "session_id": session_id,
            "answer": "",
            "usage": {},
            "error": "",
        }

        def consume(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            if event_type == "thread.started":
                state["session_id"] = str(event.get("thread_id") or "") or session_id
            elif event_type == "item.completed":
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                item_type = str(item.get("type") or "")
                if item_type == "agent_message":
                    text = str(item.get("text") or "")
                    if text:
                        state["answer"] += text
                        emit({"type": "chunk", "content": text})
                elif item_type in {
                    "command_execution", "file_change", "mcp_tool_call", "web_search", "plan"
                }:
                    emit(
                        {
                            "type": "activity",
                            "agent": "codex",
                            "activity": item_type,
                            "status": str(item.get("status") or "completed"),
                        }
                    )
            elif event_type == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    state["usage"] = {
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                    }
            elif event_type in {"turn.failed", "error"}:
                state["error"] = str(event.get("message") or event.get("error") or event_type)

        full_prompt = prompt + self.attachment_prompt(attachments)
        code, stderr = await self._run_jsonl(args, full_prompt, consume)
        if code or state["error"]:
            raise AgentError(state["error"] or stderr or f"Codex 退出码 {code}")
        if not state["answer"]:
            raise AgentError(stderr or "Codex 未返回 Agent 消息")
        return AgentResult(
            runtime="codex",
            answer=state["answer"],
            session_id=state["session_id"],
            model=self.config.model or "codex-default",
            usage=state["usage"],
        )
