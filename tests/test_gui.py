"""本地可视化界面的服务、鉴权和真实流式链路测试。"""

from __future__ import annotations

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

import httpx
import pytest
import yaml

from gui.server import GuiServer, run_gui
from gui.service import ApplicationService


class _LocalModelHandler(BaseHTTPRequestHandler):
    received_authorization: ClassVar[str | None] = None

    def do_POST(self) -> None:
        type(self).received_authorization = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", "0"))
        _ = json.loads(self.rfile.read(length))
        body = (
            'data: {"choices":[{"delta":{"content":"可视化"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"界面正常"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _write_gui_config(project_dir: Path) -> None:
    config_dir = project_dir / "config"
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
                        "params": {"temperature": 0.2},
                    }
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_gui_http_security_and_real_chat(temp_dir: Path) -> None:
    _write_gui_config(temp_dir)
    model_server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalModelHandler)
    model_thread = Thread(target=model_server.serve_forever, daemon=True)
    model_thread.start()

    service = ApplicationService(temp_dir)
    gui_server = GuiServer(("127.0.0.1", 0), service, "visual-test-token")
    gui_thread = Thread(target=gui_server.serve_forever, daemon=True)
    gui_thread.start()
    base_url = f"http://127.0.0.1:{gui_server.server_port}"
    headers = {"X-LocalAI-Token": "visual-test-token"}
    try:
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            assert client.get("/").status_code == 403
            page = client.get("/?token=visual-test-token")
            assert page.status_code == 200
            assert "模型管理" in page.text
            assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
            assert client.get("/api/bootstrap").status_code == 403
            assert client.get(
                "/api/bootstrap",
                headers={**headers, "Origin": "https://evil.example"},
            ).status_code == 403

            bootstrap = client.get("/api/bootstrap", headers=headers).json()
            assert bootstrap["current_mode"] == "coder"
            assert bootstrap["vault_status"] == "uninitialized"

            save = client.post(
                "/api/model/save",
                headers=headers,
                json={
                    "name": "local",
                    "provider": "openai",
                    "base_url": f"http://127.0.0.1:{model_server.server_port}/v1",
                    "model": "local-test",
                    "api_key": "visual-test-key",
                    "capabilities": ["coding"],
                    "priority": 1,
                    "enabled": True,
                },
            )
            assert save.status_code == 200
            assert "api_key" not in save.text

            with client.stream(
                "POST",
                "/api/chat",
                headers=headers,
                json={"message": "测试 GUI", "attachments": []},
            ) as response:
                events = [json.loads(line) for line in response.iter_lines() if line]
            assert response.status_code == 200
            assert "".join(
                event.get("content", "") for event in events if event["type"] == "chunk"
            ) == "可视化界面正常"
            assert events[-1]["type"] == "done"
            assert events[-1]["stats"]["total_input"] == 7
            assert _LocalModelHandler.received_authorization == "Bearer visual-test-key"

            session_id = events[-1]["session_id"]
            export = client.get(
                f"/api/sessions/{session_id}/export?format=md", headers=headers
            )
            assert export.status_code == 200
            assert "测试 GUI" in export.text
    finally:
        gui_server.shutdown()
        gui_server.server_close()
        gui_thread.join(timeout=5)
        service.close()
        model_server.shutdown()
        model_server.server_close()
        model_thread.join(timeout=5)


def test_gui_model_payload_never_returns_ciphertext(temp_dir: Path) -> None:
    _write_gui_config(temp_dir)
    service = ApplicationService(temp_dir)
    try:
        model = service.save_model(
            {
                "name": "safe",
                "provider": "openai",
                "base_url": "https://example.com/v1",
                "model": "safe-model",
                "api_key": "test-secret-key",
                "capabilities": [],
            }
        )
        assert model["configured"] is True
        assert "api_key_encrypted" not in model
        assert "test-secret-key" not in json.dumps(service.bootstrap())
    finally:
        service.close()


def test_gui_attachment_limits_and_safe_filenames(temp_dir: Path) -> None:
    _write_gui_config(temp_dir)
    service = ApplicationService(temp_dir)
    service.MAX_TOTAL_UPLOAD_BYTES = 3
    upload_dir = temp_dir / "uploads"
    upload_dir.mkdir()
    encoded = base64.b64encode(b"ab").decode("ascii")
    try:
        with pytest.raises(ValueError, match="40 MB"):
            service._decode_attachments(
                [
                    {"name": "../first.txt", "content": encoded},
                    {"name": "second.txt", "content": encoded},
                ],
                upload_dir,
            )
        assert (upload_dir / "0-first.txt").is_file()
        assert not (temp_dir / "first.txt").exists()
    finally:
        service.close()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::1"])
def test_gui_rejects_non_supported_bind_addresses(temp_dir: Path, host: str) -> None:
    with pytest.raises(ValueError, match="回环地址"):
        run_gui(temp_dir, host=host, open_browser=False)
