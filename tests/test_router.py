"""Router 调度逻辑测试。"""

from __future__ import annotations

from agent.router import Router


def test_force_model_priority(router: Router):
    router.force_model("openai")
    provider = router.select_model("帮我写一段代码")
    assert provider is not None
    assert provider.name == "openai"


def test_user_rule_priority(router: Router):
    router.force_model(None)
    provider = router.select_model("C++ 算法 debug")
    assert provider is not None
    assert provider.name == "deepseek"


def test_auto_capability_match(router: Router):
    router.force_model(None)
    provider = router.select_model("请把这段中文翻译成英文")
    assert provider is not None
    assert provider.name == "qwen"


def test_default_fallback(router: Router):
    router.force_model(None)
    # 输入无法匹配任何标签
    provider = router.select_model("你好")
    assert provider is not None
    assert provider.name == router.model_manager.default_model()


def test_fallback_chain(router: Router):
    providers = router.build_fallback_chain("deepseek")
    names = [p.name for p in providers]
    assert "deepseek" in names
    assert "qwen" in names


def test_mode_default_model(router: Router):
    provider = router.select_model("普通请求", mode_model="qwen")

    assert provider is not None
    assert provider.name == "qwen"
