"""Provider 抽象基类与通用工具。"""

from __future__ import annotations

import ipaddress
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger


class ProviderError(Exception):
    """Provider 基础异常。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RateLimitError(ProviderError):
    """限流异常。"""


class AuthenticationError(ProviderError):
    """认证失败。"""


def validate_base_url(base_url: str) -> str:
    """仅允许 HTTPS；本机回环服务可使用 HTTP。"""
    value = str(base_url).strip().rstrip("/")
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("模型 API 地址无效，且不得包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("模型 API 地址不得包含查询参数或片段")
    if parsed.scheme == "https":
        return value
    is_loopback = parsed.hostname.lower() == "localhost"
    try:
        is_loopback = is_loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme == "http" and is_loopback:
        return value
    raise ValueError("模型 API 地址必须使用 HTTPS（本机回环地址可使用 HTTP）")


def is_loopback_url(base_url: str) -> bool:
    """判断 URL 是否指向本机回环地址。"""
    hostname = urlsplit(base_url).hostname
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class BaseProvider(ABC):
    """所有 LLM Provider 的抽象基类。

    子类只需实现 ``_chat_request`` 与 ``validate`` 即可，
    通用 SSE 流式解析由基类提供。
    """

    provider_name: str = ""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        capabilities: list[str] | None = None,
        priority: int = 100,
        timeout: float = 60.0,
        **extra: Any,
    ) -> None:
        self.name = name
        self.base_url = validate_base_url(base_url)
        self.api_key = api_key
        self.model = model
        self.capabilities = set(capabilities or [])
        self.priority = priority
        self.timeout = timeout
        self.extra = extra
        self.last_usage: dict[str, int] = {}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers=self._default_headers(),
            # 本地 Ollama/兼容服务不能被 HTTP(S)_PROXY 劫持；远端仍尊重用户代理。
            trust_env=not is_loopback_url(self.base_url),
        )

    def _default_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @abstractmethod
    def _chat_request(
        self,
        messages: list[dict[str, str]],
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """构造 chat completion 请求体。"""

    def _handle_http_error(self, exc: httpx.HTTPStatusError) -> ProviderError:
        status = exc.response.status_code
        logger.warning(f"Provider {self.name} 请求失败，HTTP 状态码 {status}")
        if status in (401, 403):
            return AuthenticationError("API Key 无效或已过期", status)
        if status == 429:
            return RateLimitError("请求过于频繁，请稍后再试", status)
        if 500 <= status < 600:
            return ProviderError(f"服务端错误: {status}", status)
        return ProviderError(f"请求失败: HTTP {status}", status)

    async def chat(
        self,
        messages: list[dict[str, str]],
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """调用模型并返回文本流。"""
        self.last_usage = {}
        payload = self._chat_request(messages, stream=stream, **kwargs)
        try:
            if stream:
                async with self.client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.is_error:
                        await response.aread()
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        text = self._parse_sse_line(line)
                        if text is not None:
                            yield text
            else:
                response = await self.client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                self._capture_usage(data)
                content = data["choices"][0]["message"]["content"]
                yield content
        except httpx.HTTPStatusError as exc:
            raise self._handle_http_error(exc) from exc
        except httpx.TimeoutException as exc:
            logger.error(f"Provider {self.name} 请求超时")
            raise ProviderError("请求超时", status_code=408) from exc
        except httpx.RequestError as exc:
            logger.error(f"Provider {self.name} 网络错误: {exc}")
            raise ProviderError(f"网络错误: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error(f"Provider {self.name} 返回格式无效: {exc}")
            raise ProviderError("服务返回了无法解析的数据") from exc

    def _parse_sse_line(self, line: str) -> str | None:
        """解析 OpenAI 兼容 SSE 流的一行数据。"""
        line = line.strip()
        if not line or line.startswith(":"):
            return None
        if not line.startswith("data: "):
            return None
        data = line[len("data: "):].strip()
        if data == "[DONE]":
            return None
        try:
            chunk = json.loads(data)
            self._capture_usage(chunk)
            if not chunk.get("choices"):
                return None
            delta = chunk["choices"][0].get("delta", {})
            return delta.get("content", "")
        except (json.JSONDecodeError, KeyError, IndexError):
            return None

    def _capture_usage(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        try:
            self.last_usage = {
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
            }
        except (TypeError, ValueError):
            logger.debug(f"Provider {self.name} 返回了无效的 usage 数据")

    def get_usage(self) -> dict[str, int]:
        """返回最近一次请求的 token usage；服务未提供时为空。"""
        return dict(self.last_usage)

    async def validate(self) -> bool:
        """测试连接是否可用。默认发送最小请求。"""
        try:
            payload = self._chat_request(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
                max_tokens=1,
            )
            response = await self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            logger.info(f"Provider {self.name} 连接验证通过")
            return True
        except httpx.HTTPStatusError as exc:
            raise self._handle_http_error(exc) from exc
        except Exception as exc:
            logger.error(f"Provider {self.name} 验证失败: {exc}")
            raise ProviderError(f"验证失败: {exc}") from exc

    def get_capabilities(self) -> list[str]:
        return sorted(self.capabilities)

    async def close(self) -> None:
        await self.client.aclose()
