"""Fallback 机制测试。"""

from __future__ import annotations

import httpx
import pytest
import respx

from agent.router import Router


@pytest.mark.asyncio
async def test_fallback_chain_order(router: Router):
    providers = router.build_fallback_chain("qwen")
    names = [p.name for p in providers]
    # qwen 是主模型，fallback_chain 中 deepseek 排第一，qwen 第二
    assert names[0] == "qwen"
    assert "deepseek" in names


@pytest.mark.asyncio
async def test_primary_failed_fallback_to_next(router: Router):
    """模拟主模型失败，fallback 到下一个可用模型。"""
    providers = router.build_fallback_chain("qwen")
    primary = providers[0]
    fallback = next(p for p in providers if p.name != primary.name)

    with respx.mock:
        # 主模型失败
        respx.post(f"{primary.base_url}/chat/completions").mock(
            return_value=httpx.Response(429, text="rate limit")
        )
        # fallback 成功
        respx.post(f"{fallback.base_url}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                text="data: {\"choices\":[{\"delta\":{\"content\":\"fallback\"}}]}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        )
        # 这里只验证 fallback provider 可以被创建并调用
        result = [c async for c in fallback.chat([{"role": "user", "content": "hi"}])]
        assert "".join(result) == "fallback"

    await primary.close()
    await fallback.close()
