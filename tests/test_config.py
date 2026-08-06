"""配置读取测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.analyzer import TaskAnalyzer
from models.manager import ModelConfig, ModelManager
from storage.encryption import SecureVault


def test_model_config_roundtrip():
    cfg = ModelConfig(
        name="test",
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4",
        capabilities=["coding"],
        priority=5,
    )
    data = cfg.to_dict()
    restored = ModelConfig.from_dict(data)
    assert restored.name == cfg.name
    assert restored.priority == cfg.priority
    assert restored.capabilities == cfg.capabilities


def test_model_manager_crud(temp_dir: Path):
    vault = SecureVault(temp_dir / "data")
    config_path = temp_dir / "models.yaml"
    mm = ModelManager(config_path, vault)
    mm.add_model(
        name="ds",
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-test",
        capabilities=["coding"],
        priority=10,
    )
    assert mm.get_model("ds") is not None
    assert mm.get_decrypted_key("ds") == "sk-test"
    mm.set_enabled("ds", False)
    assert mm.get_model("ds").enabled is False
    mm.remove_model("ds")
    assert mm.get_model("ds") is None


def test_model_manager_persists_encrypted_key(temp_dir: Path):
    vault = SecureVault(temp_dir / "data")
    config_path = temp_dir / "models.yaml"
    mm = ModelManager(config_path, vault)
    mm.add_model(
        name="kimi",
        provider="kimi",
        base_url="https://api.moonshot.cn/v1",
        model="moonshot-v1-8k",
        api_key="sk-secret",
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model_data = raw["models"][0]
    assert model_data["api_key_encrypted"]
    assert model_data["api_key_encrypted"] != "sk-secret"


def test_model_config_uses_defaults_for_missing_fields() -> None:
    cfg = ModelConfig.from_dict({"name": "openai-test"})

    assert cfg.provider == "openai"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "openai-test"


def test_model_manager_ignores_invalid_yaml(temp_dir: Path) -> None:
    config_path = temp_dir / "models.yaml"
    config_path.write_text("models: not-a-list", encoding="utf-8")

    manager = ModelManager(config_path, SecureVault(temp_dir / "data"))

    assert manager.list_models() == []


def test_update_api_key_encrypts_value(temp_dir: Path) -> None:
    manager = ModelManager(temp_dir / "models.yaml", SecureVault(temp_dir / "data"))
    manager.add_model("x", "openai", "https://example.com/v1", "x", "old")

    manager.update_model("x", api_key="new-secret")

    assert manager.get_decrypted_key("x") == "new-secret"
    assert "new-secret" not in (temp_dir / "models.yaml").read_text(encoding="utf-8")


def test_rules_reload_immediately(temp_dir: Path) -> None:
    rules_path = temp_dir / "rules.yaml"
    rules_path.write_text("rules: []\n", encoding="utf-8")
    analyzer = TaskAnalyzer(rules_path)
    assert analyzer.get_rule_model("special request") is None

    rules_path.write_text(
        "rules:\n  - name: special\n    keywords: [SPECIAL]\n    model: qwen\n",
        encoding="utf-8",
    )
    analyzer.reload()

    assert analyzer.get_rule_model("special request") == "qwen"


def test_builtin_modes_contains_all_presets() -> None:
    config_path = Path(__file__).parents[1] / "config" / "modes.yaml"
    modes = yaml.safe_load(config_path.read_text(encoding="utf-8"))["modes"]

    assert set(modes) == {"coder", "writer", "translator", "researcher"}


def test_invalid_model_entry_is_skipped_without_crashing(temp_dir: Path) -> None:
    config_path = temp_dir / "models.yaml"
    config_path.write_text(
        "models:\n  - name: broken\n    priority: not-a-number\n",
        encoding="utf-8",
    )

    manager = ModelManager(config_path, SecureVault(temp_dir / "data"))

    assert manager.list_models() == []


@pytest.mark.parametrize(
    "url",
    ["http://example.com/v1", "ftp://example.com/v1", "https://user:pass@example.com/v1"],
)
def test_insecure_or_credentialed_model_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        ModelConfig.from_dict({"name": "x", "model": "x", "base_url": url})


def test_loopback_http_model_url_is_allowed() -> None:
    cfg = ModelConfig.from_dict(
        {"name": "local", "model": "local", "base_url": "http://127.0.0.1:11434/v1"}
    )
    assert cfg.base_url.startswith("http://127.0.0.1")


def test_invalid_enabled_value_is_rejected() -> None:
    with pytest.raises(TypeError, match="enabled"):
        ModelConfig.from_dict(
            {
                "name": "x",
                "model": "x",
                "base_url": "https://example.com/v1",
                "enabled": "sometimes",
            }
        )


def test_empty_model_alias_is_rejected(temp_dir: Path) -> None:
    manager = ModelManager(temp_dir / "models.yaml", SecureVault(temp_dir / "data"))

    with pytest.raises(ValueError, match="别名不能为空"):
        manager.add_model(" ", "openai", "https://example.com/v1", "model", "key")


def test_non_finite_cost_is_rejected() -> None:
    with pytest.raises(ValueError, match="有限"):
        ModelConfig.from_dict(
            {
                "name": "x",
                "model": "x",
                "base_url": "https://example.com/v1",
                "cost_per_1k_input": float("nan"),
            }
        )


def test_invalid_fallback_settings_are_rejected(temp_dir: Path) -> None:
    manager = ModelManager(temp_dir / "models.yaml", SecureVault(temp_dir / "data"))

    with pytest.raises(TypeError, match="fallback_chain"):
        manager.update_settings({"fallback_chain": "model-a"})
