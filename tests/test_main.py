"""命令入口与安装版数据目录测试。"""

from __future__ import annotations

from pathlib import Path

import main


def test_source_checkout_uses_project_directory() -> None:
    assert main.resolve_project_dir() == Path(main.__file__).resolve().parent


def test_explicit_project_directory(temp_dir: Path) -> None:
    selected = temp_dir / "custom"

    assert main.resolve_project_dir(selected) == selected.resolve()


def test_installed_layout_copies_config_templates(
    temp_dir: Path, monkeypatch
) -> None:
    source_dir = temp_dir / "site-packages"
    templates = source_dir / "config"
    templates.mkdir(parents=True)
    for filename in ("models.yaml", "rules.yaml", "modes.yaml"):
        (templates / filename).write_text(f"# {filename}\n", encoding="utf-8")

    user_data = temp_dir / "user-data"
    monkeypatch.setattr(main, "__file__", str(source_dir / "main.py"))
    monkeypatch.setenv("LOCALAPPDATA", str(user_data))
    monkeypatch.setenv("XDG_DATA_HOME", str(user_data))

    result = main.resolve_project_dir()

    assert result == user_data / "localai-agent-router"
    assert (result / "config" / "models.yaml").exists()
