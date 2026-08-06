"""通过真实本机 HTTP socket 验证完整问答链路。"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from threading import Thread
from typing import Any, ClassVar

import pytest
import yaml
from rich.console import Console

from cli.terminal import TerminalApp


class _ChatHandler(BaseHTTPRequestHandler):
    received: ClassVar[dict[str, Any]] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "payload": json.loads(self.rfile.read(length)),
        }
        body = (
            'data: {"choices":[{"delta":{"content":"本机"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"验收通过"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.mark.asyncio
async def test_terminal_real_http_end_to_end(temp_dir: Path) -> None:
    config_dir = temp_dir / "config"
    config_dir.mkdir()
    (config_dir / "models.yaml").write_text(
        yaml.safe_dump({"settings": {}, "models": []}), encoding="utf-8"
    )
    (config_dir / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    (config_dir / "modes.yaml").write_text(
        yaml.safe_dump(
            {
                "modes": {
                    "coder": {
                        "display_name": "编程模式",
                        "default_model": "local",
                        "system_prompt": "helpful",
                    }
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = TerminalApp(temp_dir)
    app.console = Console(file=StringIO(), force_terminal=False, width=120)
    try:
        app.model_manager.add_model(
            "local",
            "openai",
            f"http://127.0.0.1:{server.server_port}/v1",
            "local-test",
            "integration-secret",
            capabilities=["coding"],
        )
        app.model_manager.update_settings(
            {"default_model": "local", "fallback_chain": ["local"]}
        )

        await app._ask("真实 HTTP 验收")

        session_id = app.current_session_id
        assert session_id is not None
        messages = app.history.get_messages(session_id)
        assert messages[-1].content == "本机验收通过"
        assert messages[-1].model == "local"
        report = app.history.daily_report()
        assert report["total_input"] == 9
        assert report["total_output"] == 4
        assert _ChatHandler.received["path"] == "/v1/chat/completions"
        assert _ChatHandler.received["authorization"] == "Bearer integration-secret"
        assert _ChatHandler.received["payload"]["model"] == "local-test"
    finally:
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
