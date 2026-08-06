"""Kimi Moonshot Provider（OpenAI 兼容协议）。"""

from __future__ import annotations

from typing import Any

from providers.base import BaseProvider


class KimiProvider(BaseProvider):
    """Moonshot Kimi API Provider。"""

    provider_name = "kimi"

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
        # Kimi K3 当前接口未提供 temperature 参数，模式配置中的该值不转发。
        if "max_tokens" in kwargs:
            # Kimi 当前 Chat Completions API 使用 max_completion_tokens。
            payload["max_completion_tokens"] = kwargs["max_tokens"]
        return payload
