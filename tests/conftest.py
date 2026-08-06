"""pytest fixtures。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from agent.analyzer import TaskAnalyzer
from agent.router import Router
from models.manager import ModelManager
from models.registry import ProviderRegistry
from storage.database import Database
from storage.encryption import SecureVault
from storage.history import ConversationHistory


@pytest.fixture(autouse=True)
def disable_system_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不读写真实系统凭据管理器。"""
    monkeypatch.setattr(SecureVault, "_get_keyring", lambda self: None)
    monkeypatch.setenv("LOCALAI_MASTER_PASSWORD", "localai-test-master-password")


@pytest.fixture
def temp_dir():
    path = Path(tempfile.mkdtemp())
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def config_dir(temp_dir: Path) -> Path:
    cfg = temp_dir / "config"
    cfg.mkdir()
    models_data = {
        "settings": {
            "default_model": "deepseek",
            "fallback_chain": ["deepseek", "qwen"],
        },
        "models": [
            {
                "name": "deepseek",
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key_encrypted": "",
                "enabled": True,
                "capabilities": ["coding", "math"],
                "priority": 10,
            },
            {
                "name": "qwen",
                "provider": "qwen",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-max",
                "api_key_encrypted": "",
                "enabled": True,
                "capabilities": ["translation", "writing"],
                "priority": 20,
            },
        ],
    }
    rules_data = {
        "rules": [
            {
                "name": "编程问题",
                "keywords": ["C++", "算法", "debug"],
                "model": "deepseek",
            }
        ]
    }
    (cfg / "models.yaml").write_text(yaml.safe_dump(models_data), encoding="utf-8")
    (cfg / "rules.yaml").write_text(yaml.safe_dump(rules_data), encoding="utf-8")
    return cfg


@pytest.fixture
def vault(temp_dir: Path) -> SecureVault:
    return SecureVault(temp_dir / "data")


@pytest.fixture
def model_manager(config_dir: Path, vault: SecureVault) -> ModelManager:
    mm = ModelManager(config_dir / "models.yaml", vault)
    # 为内置模型配置加密后的测试 API Key
    for name in ("deepseek", "qwen"):
        cfg = mm.get_model(name)
        if cfg is not None and not cfg.api_key_encrypted:
            mm.update_model(
                name,
                api_key_encrypted=vault.encrypt(f"sk-test-{name}"),
            )
    mm.add_model(
        name="openai",
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key="sk-test-openai",
        capabilities=["writing", "document"],
        priority=30,
    )
    return mm


@pytest.fixture
def registry() -> ProviderRegistry:
    return ProviderRegistry()


@pytest.fixture
def analyzer(config_dir: Path) -> TaskAnalyzer:
    return TaskAnalyzer(config_dir / "rules.yaml")


@pytest.fixture
def router(model_manager: ModelManager, registry: ProviderRegistry, analyzer: TaskAnalyzer) -> Router:
    return Router(model_manager, registry, analyzer)


@pytest.fixture
def database(temp_dir: Path) -> Database:
    db = Database(temp_dir / "test.db")
    db.create_tables()
    return db


@pytest.fixture
def history(database: Database) -> ConversationHistory:
    return ConversationHistory(database.get_session())
