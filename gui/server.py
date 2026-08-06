"""仅绑定回环地址的 LocalAI Web GUI 服务。"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import signal
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, unquote, urlsplit

from loguru import logger

from gui.service import ApplicationService
from providers.base import ProviderError
from storage.exceptions import EncryptionError


class GuiServer(ThreadingHTTPServer):
    """携带应用服务和会话令牌的本地 HTTP 服务器。"""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        service: ApplicationService,
        token: str,
    ) -> None:
        super().__init__(address, GuiRequestHandler)
        self.service = service
        self.token = token
        self.static_dir = Path(__file__).with_name("static")


class GuiRequestHandler(BaseHTTPRequestHandler):
    """GUI 静态资源和 JSON/NDJSON API。"""

    server: GuiServer
    # Base64 adds roughly one third; this covers the service's 40 MB aggregate limit.
    MAX_BODY = 56 * 1024 * 1024
    STATIC_TYPES: ClassVar[dict[str, str]] = {
        "index.html": "text/html; charset=utf-8",
        "app.js": "text/javascript; charset=utf-8",
        "styles.css": "text/css; charset=utf-8",
    }

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("GUI HTTP " + (format % args))

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )

    def _host_is_local(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return (urlsplit(origin).hostname or "").lower() in {
            "127.0.0.1",
            "localhost",
            "::1",
        }

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-LocalAI-Token", "")
        return (
            self._host_is_local()
            and self._origin_is_local()
            and secrets.compare_digest(supplied, self.server.token)
        )

    def _send_bytes(
        self,
        content: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        filename: str | None = None,
    ) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/json":
            raise ValueError("请求必须使用 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length <= 0 or length > self.MAX_BODY:
            raise ValueError("请求正文为空或超过限制")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("JSON 格式无效") from exc
        if not isinstance(payload, dict):
            raise TypeError("JSON 根节点必须是对象")
        return payload

    def _serve_static(self, name: str) -> None:
        if name not in self.STATIC_TYPES:
            self._error("资源不存在", HTTPStatus.NOT_FOUND)
            return
        path = self.server.static_dir / name
        if not path.is_file():
            self._error("界面资源缺失", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_bytes(path.read_bytes(), self.STATIC_TYPES[name])

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            query_token = parse_qs(parsed.query).get("token", [""])[0]
            if not self._host_is_local() or not secrets.compare_digest(
                query_token, self.server.token
            ):
                self._error("访问令牌无效", HTTPStatus.FORBIDDEN)
                return
            self._serve_static("index.html")
            return
        if parsed.path in {"/app.js", "/styles.css"}:
            self._serve_static(parsed.path[1:])
            return
        if not self._authorized():
            self._error("未授权", HTTPStatus.FORBIDDEN)
            return
        try:
            if parsed.path == "/api/bootstrap":
                self._send_json(self.server.service.bootstrap())
                return
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/export"):
                session_id = unquote(parsed.path.split("/")[3])
                export_format = parse_qs(parsed.query).get("format", ["md"])[0]
                content, content_type, filename = self.server.service.export_session(
                    session_id, export_format
                )
                self._send_bytes(content, content_type, filename=filename)
                return
            if parsed.path.startswith("/api/sessions/"):
                session_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._send_json(self.server.service.get_session(session_id))
                return
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            logger.debug("GUI 客户端在响应完成前断开")
        except (ValueError, OSError) as exc:
            self._error(str(exc))

    def do_POST(self) -> None:
        if not self._authorized():
            self._error("未授权", HTTPStatus.FORBIDDEN)
            return
        try:
            payload = self._read_json()
            path = urlsplit(self.path).path
            if path == "/api/chat":
                self._stream_chat(payload)
                return
            result = self._dispatch_post(path, payload)
            self._send_json({"ok": True, "data": result})
        except (ValueError, OSError, EncryptionError, ProviderError) as exc:
            self._error(str(exc))
        except Exception:  # noqa: BLE001 - API 边界隐藏内部堆栈
            logger.exception("GUI API 处理失败")
            self._error("内部处理失败，请查看本地日志", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _dispatch_post(self, path: str, payload: dict[str, Any]) -> Any:
        service = self.server.service
        if path == "/api/vault/unlock":
            return {"status": service.unlock_vault(str(payload.get("password") or ""))}
        if path == "/api/session/new":
            return service.new_session()
        if path == "/api/session/open":
            return service.open_session(str(payload.get("id") or ""))
        if path == "/api/mode":
            service.set_mode(str(payload.get("mode") or ""))
            return {"mode": service.current_mode}
        if path == "/api/model/force":
            name = payload.get("name")
            service.force_model(str(name) if name else None)
            return {"name": service.router.get_forced_model()}
        if path == "/api/model/save":
            return service.save_model(payload)
        if path == "/api/model/delete":
            service.delete_model(str(payload.get("name") or ""))
            return None
        if path == "/api/model/toggle":
            return service.toggle_model(
                str(payload.get("name") or ""), bool(payload.get("enabled"))
            )
        if path == "/api/model/test":
            return {
                "connected": asyncio.run(
                    service.test_model(str(payload.get("name") or ""))
                )
            }
        raise ValueError("接口不存在")

    def _stream_chat(self, payload: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event: dict[str, Any]) -> None:
            line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
            self.wfile.write(line)
            self.wfile.flush()

        try:
            asyncio.run(
                self.server.service.chat(
                    str(payload.get("message") or ""),
                    payload.get("attachments")
                    if isinstance(payload.get("attachments"), list)
                    else [],
                    emit,
                )
            )
        except (ValueError, OSError, EncryptionError, ProviderError) as exc:
            emit({"type": "error", "content": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            logger.info("GUI 客户端已断开流式响应")
        except Exception:  # noqa: BLE001 - 流已开始，只能发送通用错误
            logger.exception("GUI 聊天流处理失败")
            try:
                emit({"type": "error", "content": "聊天处理失败，请查看本地日志"})
            except (BrokenPipeError, ConnectionResetError):
                pass


def run_gui(
    project_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """启动本地 GUI，默认自动打开浏览器。"""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("GUI 仅允许绑定本机回环地址")
    service = ApplicationService(project_dir)
    token = secrets.token_urlsafe(32)
    server = GuiServer((host, port), service, token)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?token={token}"
    print(f"LocalAI 可视化界面已启动：{url}")
    print("按 Ctrl+C 停止服务。")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    def stop_server(signum: int, frame: Any) -> None:
        del signum, frame
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, stop_server)
        signal.signal(signal.SIGTERM, stop_server)
    except ValueError:
        pass
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="LocalAI Agent Router 可视化界面")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    from main import resolve_project_dir

    run_gui(
        resolve_project_dir(args.project_dir),
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
