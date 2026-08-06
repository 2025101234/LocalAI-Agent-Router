"""通义千问 Provider（OpenAI 兼容协议）。"""

from __future__ import annotations

from typing import Any

from providers.base import BaseProvider


class QwenProvider(BaseProvider):
    """通义千问 API Provider。"""

    provider_name = "qwen"

    def _chat_request(
        self,
        messages: list[dict[str, str]],
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        return payload
