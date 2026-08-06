"""模型配置管理：增删改查、启用禁用、API Key 加解密。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from providers.base import validate_base_url
from storage.encryption import SecureVault
from storage.permissions import atomic_write_text, secure_file


def _non_negative_number(value: Any, default: float, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} 必须是有限的非负数")
    return number


def _boolean(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "on"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no", "0", "off"}:
        return False
    raise TypeError("enabled 必须是布尔值")


@dataclass
class ModelConfig:
    """单个模型配置。"""

    name: str
    provider: str
    base_url: str
    model: str
    api_key_encrypted: str = ""
    enabled: bool = True
    capabilities: list[str] = field(default_factory=list)
    priority: int = 100
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_encrypted": self.api_key_encrypted,
            "enabled": self.enabled,
            "capabilities": self.capabilities,
            "priority": self.priority,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelConfig:
        provider = str(data.get("provider") or "openai").strip().lower()
        default_urls = {
            "deepseek": "https://api.deepseek.com",
            "openai": "https://api.openai.com/v1",
            "kimi": "https://api.moonshot.cn/v1",
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        model = str(data.get("model") or data.get("name") or "").strip()
        name = str(data.get("name") or model or provider).strip()
        raw_capabilities = data.get("capabilities", [])
        if not isinstance(raw_capabilities, list):
            raise TypeError("capabilities 必须是列表")
        base_url = validate_base_url(
            str(data.get("base_url") or default_urls.get(provider, "")).strip()
        )
        priority_number = _non_negative_number(data.get("priority", 100), 100, "priority")
        if not priority_number.is_integer():
            raise ValueError("priority 必须是整数")
        return cls(
            name=name,
            provider=provider,
            base_url=base_url,
            model=model,
            api_key_encrypted=str(data.get("api_key_encrypted") or ""),
            enabled=_boolean(data.get("enabled", True)),
            capabilities=[str(item).strip() for item in raw_capabilities if str(item).strip()],
            priority=int(priority_number),
            cost_per_1k_input=_non_negative_number(
                data.get("cost_per_1k_input", 0.0), 0.0, "cost_per_1k_input"
            ),
            cost_per_1k_output=_non_negative_number(
                data.get("cost_per_1k_output", 0.0), 0.0, "cost_per_1k_output"
            ),
        )


class ModelManager:
    """管理 models.yaml 中的模型配置。"""

    def __init__(self, config_path: Path, vault: SecureVault) -> None:
        self.config_path = config_path
        self.vault = vault
        self._data: dict[str, Any] = {"settings": {}, "models": []}
        self._models: dict[str, ModelConfig] = {}
        self._load()

    def _load(self) -> None:
        self._data = {"settings": {}, "models": []}
        if self.config_path.exists():
            try:
                secure_file(self.config_path)
                with self.config_path.open("r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if not isinstance(loaded, dict):
                    raise TypeError("配置根节点必须是映射")
                self._data["settings"] = loaded.get("settings", {})
                self._data["models"] = loaded.get("models", [])
                if not isinstance(self._data["settings"], dict):
                    self._data["settings"] = {}
                if not isinstance(self._data["models"], list):
                    raise TypeError("models 必须是列表")
            except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
                logger.error(f"加载模型配置失败: {exc}")
                self._data = {"settings": {}, "models": []}

        self._models = {}
        for index, item in enumerate(self._data.get("models", [])):
            if not isinstance(item, dict):
                logger.warning(f"忽略第 {index + 1} 个无效模型配置")
                continue
            try:
                cfg = ModelConfig.from_dict(item)
            except (TypeError, ValueError) as exc:
                logger.warning(f"忽略第 {index + 1} 个无效模型配置: {exc}")
                continue
            if not cfg.name or not cfg.provider or not cfg.model or not cfg.base_url:
                logger.warning(f"忽略字段不完整的模型配置: {cfg.name or index + 1}")
                continue
            if cfg.name in self._models:
                logger.warning(f"忽略重复的模型配置: {cfg.name}")
                continue
            self._models[cfg.name] = cfg
        logger.debug(f"加载 {len(self._models)} 个模型配置")

    def save(self) -> None:
        self._data["models"] = [m.to_dict() for m in self._models.values()]
        content = yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False)
        atomic_write_text(self.config_path, content)
        logger.info(f"模型配置已保存: {self.config_path}")

    def reload(self) -> None:
        """重新读取模型配置。"""
        self._load()

    def list_models(self) -> list[ModelConfig]:
        return list(self._models.values())

    def get_model(self, name: str) -> ModelConfig | None:
        return self._models.get(name)

    def add_model(
        self,
        name: str,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        capabilities: list[str] | None = None,
        priority: int = 100,
        enabled: bool = True,
    ) -> ModelConfig:
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("模型别名不能为空")
        if normalized_name in self._models:
            raise ValueError(f"模型 {normalized_name} 已存在")
        cfg = ModelConfig.from_dict(
            {
                "name": normalized_name,
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "api_key_encrypted": self.vault.encrypt(api_key) if api_key else "",
                "enabled": enabled,
                "capabilities": list(capabilities or []),
                "priority": priority,
            }
        )
        if not cfg.name or not cfg.model:
            raise ValueError("模型名称和模型标识不能为空")
        self._models[cfg.name] = cfg
        self.save()
        logger.info(f"添加模型: {cfg.name}")
        return cfg

    def update_model(self, name: str, **kwargs: Any) -> ModelConfig:
        cfg = self._models.get(name)
        if cfg is None:
            raise ValueError(f"模型 {name} 不存在")
        candidate = cfg.to_dict()
        for key, value in kwargs.items():
            if key == "api_key":
                candidate["api_key_encrypted"] = self.vault.encrypt(str(value)) if value else ""
                continue
            if key in candidate:
                candidate[key] = value
        updated = ModelConfig.from_dict(candidate)
        if updated.name != name:
            raise ValueError("不能通过更新操作修改模型名称")
        self._models[name] = updated
        self.save()
        logger.info(f"更新模型: {name}")
        return updated

    def remove_model(self, name: str) -> None:
        if name not in self._models:
            raise ValueError(f"模型 {name} 不存在")
        del self._models[name]
        settings = self.get_settings()
        if settings.get("default_model") == name:
            settings["default_model"] = None
        fallback = settings.get("fallback_chain", [])
        if not isinstance(fallback, list):
            fallback = []
        settings["fallback_chain"] = [
            item for item in fallback if isinstance(item, str) and item != name
        ]
        self.save()
        logger.info(f"删除模型: {name}")

    def set_enabled(self, name: str, enabled: bool) -> ModelConfig:
        return self.update_model(name, enabled=enabled)

    def get_decrypted_key(self, name: str) -> str | None:
        cfg = self._models.get(name)
        if cfg is None or not cfg.api_key_encrypted:
            return None
        return self.vault.decrypt(cfg.api_key_encrypted)

    def get_settings(self) -> dict[str, Any]:
        settings = self._data.get("settings", {})
        return settings if isinstance(settings, dict) else {}

    def update_settings(self, settings: dict[str, Any]) -> None:
        if not isinstance(settings, dict):
            raise TypeError("settings 必须是映射")
        fallback = settings.get("fallback_chain", [])
        if not isinstance(fallback, list) or not all(
            isinstance(item, str) for item in fallback
        ):
            raise TypeError("fallback_chain 必须是模型名称列表")
        default = settings.get("default_model")
        if default is not None and not isinstance(default, str):
            raise TypeError("default_model 必须是字符串或 null")
        self._data["settings"] = dict(settings)
        self.save()

    def default_model(self) -> str | None:
        default = self.get_settings().get("default_model")
        if default is None or isinstance(default, str):
            return default
        logger.warning("忽略无效的 default_model 配置")
        return None

    def fallback_chain(self) -> list[str]:
        fallback = self.get_settings().get("fallback_chain", [])
        if not isinstance(fallback, list):
            logger.warning("忽略无效的 fallback_chain 配置")
            return []
        return [item for item in fallback if isinstance(item, str)]
