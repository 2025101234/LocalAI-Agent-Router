"""Provider 运行时注册表。"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

from loguru import logger

from providers.base import BaseProvider


class ProviderRegistry:
    """自动发现并管理 Provider 类。

    新增 provider 只需在 ``providers/`` 目录下新建文件并继承 ``BaseProvider``，
    无需修改核心代码。
    """

    def __init__(self, providers_dir: Path | None = None) -> None:
        self.providers_dir = providers_dir or Path(__file__).parent.parent / "providers"
        self._providers: dict[str, type[BaseProvider]] = {}
        self._discover()

    def _discover(self) -> None:
        for file in self.providers_dir.glob("*.py"):
            if file.name in ("__init__.py", "base.py"):
                continue
            module_name = f"providers.{file.stem}"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001 - 第三方 provider 导入可能抛任意异常
                logger.warning(f"加载 provider 模块 {module_name} 失败: {exc}")
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseProvider)
                    and obj is not BaseProvider
                    and obj.provider_name
                ):
                    self._providers[obj.provider_name] = obj
                    logger.debug(f"注册 provider: {obj.provider_name}")

    def get_provider_class(self, provider_name: str) -> type[BaseProvider] | None:
        return self._providers.get(provider_name)

    def list_providers(self) -> list[str]:
        return sorted(self._providers.keys())

    def create_provider(
        self,
        provider_name: str,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        **kwargs: Any,
    ) -> BaseProvider | None:
        cls = self.get_provider_class(provider_name)
        if cls is None:
            logger.error(f"未找到 provider: {provider_name}")
            return None
        return cls(
            name=name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            **kwargs,
        )
