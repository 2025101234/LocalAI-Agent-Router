"""LocalAI Agent Router 入口文件。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer

from cli.terminal import TerminalApp
from storage.permissions import secure_directory

app = typer.Typer(
    help="LocalAI Agent Router — 纯本地多模型 AI 调度中心",
    invoke_without_command=True,
    no_args_is_help=False,
)


def resolve_project_dir(project_dir: Path | None = None) -> Path:
    """解析运行目录，并为 wheel 安装创建用户级配置副本。"""
    if project_dir is not None:
        return project_dir.expanduser().resolve()

    source_dir = Path(__file__).resolve().parent
    if (source_dir / "pyproject.toml").is_file():
        return source_dir

    if os.name == "nt":
        base_dir = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
    else:
        base_dir = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
    target_dir = base_dir / "localai-agent-router"
    config_dir = target_dir / "config"
    secure_directory(config_dir)

    templates_dir = source_dir / "config"
    for filename in ("models.yaml", "rules.yaml", "modes.yaml"):
        destination = config_dir / filename
        if not destination.exists():
            shutil.copy2(templates_dir / filename, destination)
    return target_dir


@app.callback()
def entry(
    ctx: typer.Context,
    project_dir: Path | None = typer.Option(  # noqa: B008 - Typer 声明式参数
        None,
        "--project-dir",
        "-d",
        help="项目根目录，包含 config/ 与 data/",
    ),
) -> None:
    """未指定子命令时直接进入交互终端。"""
    if ctx.invoked_subcommand is None:
        TerminalApp(resolve_project_dir(project_dir)).run()


@app.command()
def run(
    project_dir: Path | None = typer.Option(  # noqa: B008 - Typer 声明式参数
        None,
        "--project-dir",
        "-d",
        help="项目根目录，包含 config/ 与 data/",
    ),
) -> None:
    """启动交互式终端。"""
    terminal = TerminalApp(resolve_project_dir(project_dir))
    terminal.run()


@app.command()
def version() -> None:
    """显示版本信息。"""
    typer.echo("LocalAI Agent Router 0.1.0")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
