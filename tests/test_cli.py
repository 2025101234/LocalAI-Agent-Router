"""终端命令、模式和端到端 fallback 测试。"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt

from cli.terminal import TerminalApp
from storage.encryption import SecureVault


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadError("connection interrupted")


@pytest.fixture
def terminal_app(temp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(SecureVault, "_get_keyring", lambda self: None)
    config_dir = temp_dir / "config"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text(
        yaml.safe_dump(
            {"settings": {"default_model": "deepseek", "fallback_chain": ["deepseek", "qwen"]}, "models": []}
        ),
        encoding="utf-8",
    )
    (config_dir / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    (config_dir / "modes.yaml").write_text(
        yaml.safe_dump(
            {
                "modes": [
                    {
                        "name": "coder",
                        "display_name": "编程模式",
                        "default_model": "deepseek",
                        "system_prompt": "coder prompt",
                        "params": {"temperature": 0.2},
                    },
                    {
                        "name": "writer",
                        "display_name": "写作模式",
                        "default_model": "qwen",
                        "system_prompt": "writer prompt",
                        "params": {"temperature": 0.7},
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    app = TerminalApp(temp_dir)
    app.console = Console(file=StringIO(), force_terminal=False, width=120)
    yield app
    app.close()


def test_mode_switch_updates_prompt_and_session(terminal_app: TerminalApp) -> None:
    terminal_app._set_mode("writer")

    assert terminal_app.current_mode == "writer"
    assert terminal_app.memory.get_messages()[0]["content"] == "writer prompt"
    session = terminal_app.history.get_session(terminal_app.current_session_id)
    assert session is not None and session.mode == "writer"


def test_history_show_and_export_commands(terminal_app: TerminalApp, temp_dir: Path) -> None:
    session_id = terminal_app.current_session_id
    assert session_id is not None
    terminal_app.history.add_message(session_id, "user", "history content")
    output_path = temp_dir / "manual-export.json"

    terminal_app._handle_command(f"/history show {session_id[:8]}")
    terminal_app._handle_command(f"/history export {session_id[:8]} json {output_path}")

    assert output_path.exists()
    rendered = terminal_app.console.file.getvalue()
    assert "history content" in rendered


def test_model_crud_commands(
    terminal_app: TerminalApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_answers = iter(
        ["custom", "openai", "https://custom.example/v1", "custom-model", "secret", "writing,coding", "5"]
    )
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: next(add_answers))
    terminal_app._handle_model_command(["add"])
    assert terminal_app.model_manager.get_decrypted_key("custom") == "secret"

    update_answers = iter(
        ["https://new.example/v1", "new-model", "writing", "9", "new-secret"]
    )
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: next(update_answers))
    terminal_app._handle_model_command(["update", "custom"])
    cfg = terminal_app.model_manager.get_model("custom")
    assert cfg is not None and cfg.model == "new-model" and cfg.priority == 9
    assert terminal_app.model_manager.get_decrypted_key("custom") == "new-secret"

    terminal_app._handle_model_command(["disable", "custom"])
    assert terminal_app.model_manager.get_model("custom").enabled is False
    terminal_app._handle_model_command(["enable", "custom"])
    terminal_app._handle_model_command(["custom"])
    assert terminal_app.router.get_forced_model() == "custom"
    terminal_app._handle_model_command(["auto"])
    assert terminal_app.router.get_forced_model() is None

    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: False)
    terminal_app._handle_model_command(["remove", "custom"])
    assert terminal_app.model_manager.get_model("custom") is not None
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: True)
    terminal_app._handle_model_command(["remove", "custom"])
    assert terminal_app.model_manager.get_model("custom") is None


def test_help_stats_clear_and_unknown_commands(terminal_app: TerminalApp) -> None:
    original_session = terminal_app.current_session_id
    terminal_app._handle_command("/help")
    terminal_app._handle_command("/model list")
    terminal_app._handle_command("/mode")
    terminal_app._handle_command("/mode missing")
    terminal_app._handle_command("/stats")
    terminal_app._handle_command("/stats monthly")
    terminal_app._handle_command("/history")
    terminal_app._handle_command("/unknown")
    terminal_app._handle_command("/clear")

    assert terminal_app.current_session_id != original_session
    rendered = terminal_app.console.file.getvalue()
    assert "命令列表" in rendered
    assert "未知命令" in rendered
    assert "今日统计" in rendered


@pytest.mark.asyncio
async def test_model_connection_test_paths(terminal_app: TerminalApp) -> None:
    await terminal_app._test_model("missing")
    terminal_app.model_manager.add_model(
        "openai", "openai", "https://validate.example/v1", "test", "key"
    )
    with respx.mock:
        respx.post("https://validate.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        await terminal_app._test_model("openai")

    assert "连接正常" in terminal_app.console.file.getvalue()


@pytest.mark.asyncio
async def test_empty_and_unconfigured_ask(terminal_app: TerminalApp) -> None:
    await terminal_app._ask("")
    await terminal_app._ask("hello")

    rendered = terminal_app.console.file.getvalue()
    assert "请输入问题" in rendered
    assert "没有可用的模型" in rendered


def test_interactive_run_handles_commands(
    terminal_app: TerminalApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["/help", "/model list", "/mode coder", "/exit"])
    monkeypatch.setattr(Prompt, "ask", lambda *args, **kwargs: next(answers))

    terminal_app.run()

    assert terminal_app._closed is True
    assert "LocalAI Agent Router" in terminal_app.console.file.getvalue()


@pytest.mark.asyncio
async def test_fallback_records_actual_model_and_usage(terminal_app: TerminalApp) -> None:
    terminal_app.model_manager.add_model(
        "deepseek", "deepseek", "https://primary.example/v1", "deepseek-chat", "key-1", priority=1
    )
    terminal_app.model_manager.add_model(
        "qwen", "qwen", "https://fallback.example/v1", "qwen", "key-2", priority=2
    )

    with respx.mock:
        respx.post("https://primary.example/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="rate limit")
        )
        respx.post("https://fallback.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"fallback answer"}}]}\n\n'
                    'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3}}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
        await terminal_app._ask("hello")

    session_id = terminal_app.current_session_id
    assert session_id is not None
    messages = terminal_app.history.get_messages(session_id)
    assert messages[-1].content == "fallback answer"
    assert messages[-1].model == "qwen"
    report = terminal_app.history.daily_report()
    assert report["models"][0]["model"] == "qwen"
    assert report["total_input"] == 12
    assert report["total_output"] == 3


@pytest.mark.asyncio
async def test_all_fallbacks_fail_preserves_partial_answer(terminal_app: TerminalApp) -> None:
    terminal_app.model_manager.add_model(
        "qwen", "qwen", "https://failed.example/v1", "qwen", "key", priority=2
    )
    with respx.mock:
        respx.post("https://failed.example/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="down")
        )
        answer, model, usage = await terminal_app._fallback(
            [{"role": "user", "content": "hi"}],
            "deepseek",
            {},
            "partial",
        )

    assert (answer, model, usage) == ("partial", "deepseek", {})


@pytest.mark.asyncio
async def test_interrupted_stream_saves_partial_history(terminal_app: TerminalApp) -> None:
    terminal_app.model_manager.add_model(
        "deepseek", "deepseek", "https://broken.example/v1", "deepseek", "key"
    )
    with respx.mock:
        respx.post("https://broken.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                stream=BrokenStream(),
                headers={"content-type": "text/event-stream"},
            )
        )
        await terminal_app._ask("hello")

    session_id = terminal_app.current_session_id
    assert session_id is not None
    assert terminal_app.history.get_messages(session_id)[-1].content == "partial"
