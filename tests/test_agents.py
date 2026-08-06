"""Claude/Codex Agent 协议、路由、会话恢复和交接测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from agents.base import (
    AgentCancelled,
    AgentConfig,
    AgentEmitter,
    AgentError,
    AgentResult,
    AgentRuntime,
)
from agents.claude import ClaudeRuntime
from agents.codex import CodexRuntime
from agents.manager import AgentManager
from gui.service import ApplicationService


def _config(
    name: str,
    *,
    permission: str,
    model: str = "",
    command: str = "unused",
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name=name.title(),
        command=command,
        enabled=True,
        model=model,
        capabilities=["coding"] if name == "codex" else ["writing", "general"],
        priority=10,
        permission_mode=permission,
        timeout_seconds=30,
        max_turns=5,
    )


@pytest.mark.asyncio
async def test_codex_runtime_parses_jsonl_and_resumes(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CodexRuntime(
        _config("codex", permission="workspace-write", model="test-codex"), temp_dir
    )
    runtime.config.safe_mode = True
    seen: dict[str, Any] = {}

    async def fake_run(args, prompt, consume):
        seen.update(args=args, prompt=prompt)
        consume({"type": "thread.started", "thread_id": "codex-thread"})
        consume(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "status": "completed"},
            }
        )
        consume(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Codex 完成"},
            }
        )
        consume(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 11, "output_tokens": 4},
            }
        )
        return 0, ""

    monkeypatch.setattr(runtime, "_run_jsonl", fake_run)
    events: list[dict[str, Any]] = []
    result = await runtime.run("修复代码", None, [], events.append)

    assert result.answer == "Codex 完成"
    assert result.session_id == "codex-thread"
    assert result.usage == {"input_tokens": 11, "output_tokens": 4}
    assert seen["args"][:4] == ["exec", "--json", "--sandbox", "workspace-write"]
    assert "--ignore-user-config" in seen["args"]
    assert any(event["type"] == "activity" for event in events)

    await runtime.run("继续", "codex-thread", [], events.append)
    assert seen["args"][:3] == ["exec", "resume", "--json"]
    assert "codex-thread" in seen["args"]


@pytest.mark.asyncio
async def test_claude_runtime_parses_stream_json(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = ClaudeRuntime(
        _config("claude", permission="acceptEdits", model="sonnet"), temp_dir
    )
    seen: dict[str, Any] = {}

    async def fake_run(args, prompt, consume):
        seen.update(args=args, prompt=prompt)
        consume({"type": "system", "subtype": "init", "session_id": "claude-session"})
        consume(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "text", "text": "Claude 完成"},
                    ]
                },
            }
        )
        consume(
            {
                "type": "result",
                "subtype": "success",
                "session_id": "claude-session",
                "usage": {"input_tokens": 9, "output_tokens": 3},
                "total_cost_usd": 0.01,
            }
        )
        return 0, ""

    monkeypatch.setattr(runtime, "_run_jsonl", fake_run)
    events: list[dict[str, Any]] = []
    result = await runtime.run("整理文档", None, [], events.append)

    assert result.answer == "Claude 完成"
    assert result.session_id == "claude-session"
    assert result.cost == 0.01
    assert seen["args"][:3] == ["--print", "--output-format", "stream-json"]
    assert any(event.get("activity") == "Read" for event in events)


class _ProcessRuntime(AgentRuntime):
    runtime_name = "process-test"

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        del prompt, session_id, attachments, emit
        raise NotImplementedError


@pytest.mark.asyncio
async def test_agent_subprocess_uses_stdin_and_jsonl(temp_dir: Path) -> None:
    runtime = _ProcessRuntime(
        _config("process", permission="", command=sys.executable), temp_dir
    )
    events: list[dict[str, Any]] = []
    code = (
        "import json,sys; "
        "print(json.dumps({'prompt': sys.stdin.read()}, ensure_ascii=False))"
    )

    return_code, stderr = await runtime._run_jsonl(
        ["-c", code], "机密提示不进入命令行", events.append
    )

    assert return_code == 0
    assert stderr == ""
    assert events == [{"prompt": "机密提示不进入命令行"}]


@pytest.mark.asyncio
async def test_agent_subprocess_can_be_cancelled(temp_dir: Path) -> None:
    runtime = _ProcessRuntime(
        _config("process", permission="", command=sys.executable), temp_dir
    )
    task = asyncio.create_task(
        runtime._run_jsonl(["-c", "import time; time.sleep(30)"], "stop", lambda _: None)
    )
    for _ in range(100):
        if runtime._process is not None:
            break
        await asyncio.sleep(0.01)

    assert runtime.cancel() is True
    with pytest.raises(AgentCancelled, match="已停止"):
        await task


class _FakeAgentRuntime(AgentRuntime):
    calls: ClassVar[list[dict[str, Any]]] = []

    @property
    def runtime_name(self) -> str:  # type: ignore[override]
        return self.config.name

    def executable(self) -> str | None:
        return f"fake-{self.config.name}"

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        type(self).calls.append(
            {
                "runtime": self.config.name,
                "prompt": prompt,
                "session_id": session_id,
                "attachments": attachments,
            }
        )
        answer = f"{self.config.name} 已完成"
        emit({"type": "chunk", "content": answer})
        return AgentResult(
            runtime=self.config.name,
            answer=answer,
            session_id=session_id or f"{self.config.name}-session",
            model=self.config.model or "default",
            usage={"input_tokens": 5, "output_tokens": 2},
        )


class _FailingAgentRuntime(_FakeAgentRuntime):
    async def run(
        self,
        prompt: str,
        session_id: str | None,
        attachments: list[Path],
        emit: AgentEmitter,
    ) -> AgentResult:
        del prompt, session_id, attachments, emit
        raise AgentError("模拟 Agent 故障")


class _NotReadyAgentRuntime(_FakeAgentRuntime):
    def status(self) -> dict[str, Any]:
        status = super().status()
        status.update(ready=False, detail="本地代理未启动")
        return status


def _write_agent_project(project: Path) -> None:
    config = project / "config"
    config.mkdir()
    (config / "models.yaml").write_text(
        yaml.safe_dump({"settings": {}, "models": []}), encoding="utf-8"
    )
    (config / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    (config / "modes.yaml").write_text(
        yaml.safe_dump(
            {
                "modes": {
                    "coder": {"display_name": "编程", "params": {}},
                    "writer": {"display_name": "写作", "params": {}},
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (config / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "settings": {
                    "default_target": "auto",
                    "fallback_to_models": False,
                    "routing": {"coding": "codex", "writing": "claude"},
                },
                "agents": {
                    "codex": {
                        "display_name": "Codex Agent",
                        "command": "codex",
                        "model": "codex-test",
                        "capabilities": ["coding"],
                        "permission_mode": "workspace-write",
                    },
                    "claude": {
                        "display_name": "Claude Agent",
                        "command": "claude",
                        "model": "claude-test",
                        "capabilities": ["writing"],
                        "permission_mode": "acceptEdits",
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_service_switches_agents_and_hands_off_context(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_project(temp_dir)
    _FakeAgentRuntime.calls.clear()
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FakeAgentRuntime, "claude": _FakeAgentRuntime},
    )
    service = ApplicationService(temp_dir)
    events: list[dict[str, Any]] = []
    try:
        await service.chat("请修复这个 Python bug", [], events.append)
        service.set_mode("writer")
        await service.chat("请写一份项目总结", [], events.append)
        service.set_mode("coder")
        await service.chat("继续检查代码", [], events.append)

        assert [call["runtime"] for call in _FakeAgentRuntime.calls] == [
            "codex",
            "claude",
            "codex",
        ]
        assert _FakeAgentRuntime.calls[2]["session_id"] == "codex-session"
        assert "claude 已完成" in _FakeAgentRuntime.calls[2]["prompt"]
        session = service.get_session(service.current_session_id)
        assert [message["model"] for message in session["messages"] if message["model"]] == [
            "agent:codex/codex-test",
            "agent:claude/claude-test",
            "agent:codex/codex-test",
        ]
        assert any(event.get("agent") == "claude" for event in events)
    finally:
        service.close()


@pytest.mark.asyncio
async def test_service_falls_back_between_agents(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_project(temp_dir)
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FailingAgentRuntime, "claude": _FakeAgentRuntime},
    )
    service = ApplicationService(temp_dir)
    events: list[dict[str, Any]] = []
    try:
        await service.chat("请修复代码 bug", [], events.append)

        messages = service.get_session(service.current_session_id)["messages"]
        assert messages[-1]["model"] == "agent:claude/claude-test"
        assert any(event["type"] == "reset" for event in events)
        assert any("正在交接给 Claude Agent" in event.get("content", "") for event in events)
    finally:
        service.close()


def test_agent_manager_scene_routing(temp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_agent_project(temp_dir)
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FakeAgentRuntime, "claude": _FakeAgentRuntime},
    )
    manager = AgentManager(temp_dir / "config" / "agents.yaml", temp_dir)

    coding, reason = manager.select({"coding"})
    writing, _ = manager.select({"writing"})

    assert coding is not None and coding.runtime_name == "codex"
    assert writing is not None and writing.runtime_name == "claude"
    assert "自动匹配" in reason
    assert json.dumps(manager.statuses(), ensure_ascii=False)


def test_claude_status_detects_stopped_local_proxy(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (temp_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(temp_dir))
    runtime = ClaudeRuntime(
        _config("claude", permission="plan", command=sys.executable), temp_dir
    )

    status = runtime.status()

    assert status["available"] is True
    assert status["ready"] is False
    assert "本地代理未启动" in status["detail"]


def test_claude_status_handles_invalid_proxy_url(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (temp_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:not-a-port"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(temp_dir))
    runtime = ClaudeRuntime(
        _config("claude", permission="plan", command=sys.executable), temp_dir
    )

    status = runtime.status()

    assert status["ready"] is False
    assert "配置无效" in status["detail"]


def test_manager_skips_unhealthy_preferred_agent(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_project(temp_dir)
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FakeAgentRuntime, "claude": _NotReadyAgentRuntime},
    )
    manager = AgentManager(temp_dir / "config" / "agents.yaml", temp_dir)

    selected, reason = manager.select({"writing"})

    assert selected is not None and selected.runtime_name == "codex"
    assert "自动交接" in reason
    with pytest.raises(ValueError, match="本地代理未启动"):
        manager.select({"writing"}, forced="claude")


def test_manager_temporarily_skips_agent_after_auth_failure(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_project(temp_dir)
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FakeAgentRuntime, "claude": _FakeAgentRuntime},
    )
    manager = AgentManager(temp_dir / "config" / "agents.yaml", temp_dir)
    claude = manager.runtime("claude")
    assert claude is not None

    manager.record_failure(claude, AgentError("API Error: 403 Forbidden"))
    statuses = {item["name"]: item for item in manager.statuses()}
    selected, reason = manager.select({"writing"})

    assert statuses["claude"]["ready"] is False
    assert "403" in statuses["claude"]["detail"]
    assert selected is not None and selected.runtime_name == "codex"
    assert "自动交接" in reason


def test_manager_surfaces_claude_upstream_timeout(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_project(temp_dir)
    monkeypatch.setattr(
        AgentManager,
        "RUNTIMES",
        {"codex": _FakeAgentRuntime, "claude": _FakeAgentRuntime},
    )
    manager = AgentManager(temp_dir / "config" / "agents.yaml", temp_dir)
    claude = manager.runtime("claude")
    assert claude is not None

    manager.record_failure(claude, AgentError("Claude Agent 运行超过 180 秒，已停止"))

    status = {item["name"]: item for item in manager.statuses()}["claude"]
    assert status["ready"] is False
    assert "连接超时" in status["detail"]
