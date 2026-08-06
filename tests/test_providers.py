"""Provider 调用测试（使用 mock HTTP）。"""

from __future__ import annotations

import httpx
import pytest
import respx

from providers.base import ProviderError
from providers.deepseek import DeepSeekProvider
from providers.kimi import KimiProvider
from providers.openai import OpenAIProvider
from providers.qwen import QwenProvider


def _create_provider(cls, base_url: str = "https://api.example.com/v1"):
    return cls(
        name="test",
        base_url=base_url,
        api_key="sk-test",
        model="test-model",
        capabilities=["coding"],
    )


@pytest.mark.asyncio
async def test_deepseek_chat_stream():
    provider = _create_provider(DeepSeekProvider)
    with respx.mock:
        route = respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                text=(
                    "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n"
                    "data: {\"choices\":[{\"delta\":{\"content\":\" World\"}}]}\n\n"
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
        chunks = [c async for c in provider.chat([{"role": "user", "content": "hi"}])]
        assert "".join(chunks) == "Hello World"
        assert route.called
    await provider.close()


@pytest.mark.asyncio
async def test_openai_validate_success():
    provider = _create_provider(OpenAIProvider)
    with respx.mock:
        route = respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                },
            )
        )
        ok = await provider.validate()
        assert ok is True
        assert route.called
    await provider.close()


@pytest.mark.asyncio
async def test_kimi_rate_limit():
    provider = _create_provider(KimiProvider)
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(429, text="Rate limited")
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.validate()
        assert exc_info.value.status_code == 429
    await provider.close()


@pytest.mark.asyncio
async def test_qwen_auth_error():
    provider = _create_provider(QwenProvider)
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.validate()
        assert exc_info.value.status_code == 401
    await provider.close()


def test_provider_capabilities():
    provider = _create_provider(DeepSeekProvider)
    provider.capabilities = {"coding", "math"}
    assert provider.get_capabilities() == ["coding", "math"]


def test_kimi_uses_current_completion_parameters():
    provider = _create_provider(KimiProvider)
    payload = provider._chat_request([], stream=False, max_tokens=12, temperature=0.2)

    assert payload["max_completion_tokens"] == 12
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_remote_plain_http_is_rejected_before_client_creation():
    with pytest.raises(ValueError, match="HTTPS"):
        _create_provider(OpenAIProvider, "http://example.com/v1")


@pytest.mark.asyncio
async def test_http_error_does_not_expose_response_body():
    provider = _create_provider(OpenAIProvider)
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(400, text="internal-secret-detail")
        )
        with pytest.raises(ProviderError) as exc_info:
            await provider.validate()
        assert "internal-secret-detail" not in exc_info.value.message
    await provider.close()


@pytest.mark.asyncio
async def test_stream_captures_provider_usage():
    provider = _create_provider(OpenAIProvider)
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":2}}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        )
        assert "".join([c async for c in provider.chat([])]) == "ok"
        assert provider.get_usage() == {"input_tokens": 8, "output_tokens": 2}
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("dns failed"), httpx.ReadTimeout("timeout")])
async def test_network_errors_are_normalized(error: httpx.RequestError):
    provider = _create_provider(OpenAIProvider)
    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(side_effect=error)
        with pytest.raises(ProviderError, match="网络错误|请求超时"):
            _ = [chunk async for chunk in provider.chat([])]
    await provider.close()
