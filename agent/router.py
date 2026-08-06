"""核心调度器：四级优先级模型选择。"""

from __future__ import annotations

from loguru import logger

from agent.analyzer import TaskAnalyzer
from models.manager import ModelConfig, ModelManager
from models.registry import ProviderRegistry
from providers.base import BaseProvider
from storage.exceptions import EncryptionError


class Router:
    """AI Router：根据用户输入、规则与模型能力选择最佳模型。"""

    def __init__(
        self,
        model_manager: ModelManager,
        provider_registry: ProviderRegistry,
        analyzer: TaskAnalyzer,
    ) -> None:
        self.model_manager = model_manager
        self.registry = provider_registry
        self.analyzer = analyzer
        self.forced_model: str | None = None
        self.last_error: str | None = None

    def force_model(self, name: str | None) -> None:
        """强制指定模型，None 表示恢复自动。"""
        self.forced_model = name
        logger.info(f"强制模型设置为: {name}")

    def get_forced_model(self) -> str | None:
        return self.forced_model

    def _get_enabled_models(self) -> list[ModelConfig]:
        return [m for m in self.model_manager.list_models() if m.enabled]

    def _create_provider(self, cfg: ModelConfig) -> BaseProvider | None:
        try:
            api_key = self.model_manager.get_decrypted_key(cfg.name)
        except EncryptionError as exc:
            logger.error(f"模型 {cfg.name} 的 API Key 无法解密: {exc}")
            self.last_error = (
                f"模型 {cfg.name} 的 API Key 无法解密，请确认本地主密码，"
                "或使用 /model update 重新录入密钥"
            )
            return None
        if api_key is None:
            logger.warning(f"模型 {cfg.name} 未配置 API Key")
            return None
        return self.registry.create_provider(
            provider_name=cfg.provider,
            name=cfg.name,
            base_url=cfg.base_url,
            api_key=api_key,
            model=cfg.model,
            capabilities=cfg.capabilities,
            priority=cfg.priority,
        )

    def select_model(
        self,
        text: str,
        preferred_tags: set[str] | None = None,
        mode_model: str | None = None,
    ) -> BaseProvider | None:
        """四级优先级选择模型。"""
        self.last_error = None
        enabled = {m.name: m for m in self._get_enabled_models()}
        if not enabled:
            logger.error("没有可用的模型")
            return None

        # 1. 用户强制指定
        if self.forced_model and self.forced_model in enabled:
            logger.info(f"[Router] 强制模型: {self.forced_model}")
            return self._create_provider(enabled[self.forced_model])

        # 2. 用户自定义规则
        rule_model = self.analyzer.get_rule_model(text)
        if rule_model and rule_model in enabled:
            provider = self._create_provider(enabled[rule_model])
            if provider is not None:
                logger.info(f"[Router] 规则命中: {rule_model}")
                return provider

        # 工作模式默认模型位于用户规则之后、通用能力匹配之前。
        if mode_model and mode_model in enabled:
            provider = self._create_provider(enabled[mode_model])
            if provider is not None:
                logger.info(f"[Router] 工作模式默认模型: {mode_model}")
                return provider

        # 3. 自动能力匹配
        analysis = self.analyzer.analyze(text)
        tags: set[str] = set(analysis.get("tags", []))
        if preferred_tags:
            tags.update(preferred_tags)

        candidates: list[ModelConfig] = []
        for cfg in enabled.values():
            cap_set = set(cfg.capabilities)
            score = len(cap_set & tags)
            if score > 0 or not tags:
                candidates.append(cfg)

        if candidates:
            # 优先按匹配分数降序，再按优先级升序
            candidates.sort(key=lambda m: (-len(set(m.capabilities) & tags), m.priority))
            for chosen in candidates:
                provider = self._create_provider(chosen)
                if provider is not None:
                    logger.info(f"[Router] 自动选择: {chosen.name}, tags={tags}")
                    return provider

        # 4. 默认模型
        default = self.model_manager.default_model()
        if default and default in enabled:
            provider = self._create_provider(enabled[default])
            if provider is not None:
                logger.info(f"[Router] 默认模型: {default}")
                return provider

        # 兜底：取第一个启用模型
        for fallback in sorted(enabled.values(), key=lambda m: m.priority):
            provider = self._create_provider(fallback)
            if provider is not None:
                logger.info(f"[Router] 兜底选择: {fallback.name}")
                return provider
        return None

    def build_fallback_chain(
        self,
        primary_name: str | None = None,
    ) -> list[BaseProvider]:
        """构建 fallback 链。"""
        enabled = {m.name: m for m in self._get_enabled_models()}
        chain_names = self.model_manager.fallback_chain()
        providers: list[BaseProvider] = []
        seen: set[str] = set()

        # 优先将主模型放到首位
        if primary_name and primary_name in enabled:
            prov = self._create_provider(enabled[primary_name])
            if prov:
                providers.append(prov)
                seen.add(primary_name)

        for name in chain_names:
            if name in seen:
                continue
            cfg = enabled.get(name)
            if not cfg:
                continue
            prov = self._create_provider(cfg)
            if prov:
                providers.append(prov)
                seen.add(name)

        # 补充其他启用模型
        for cfg in sorted(enabled.values(), key=lambda m: m.priority):
            if cfg.name in seen:
                continue
            prov = self._create_provider(cfg)
            if prov:
                providers.append(prov)

        logger.debug(f"fallback 链: {[p.name for p in providers]}")
        return providers
