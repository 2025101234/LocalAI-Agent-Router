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
    source_dir = Path(__file__).resolve().parent
    if project_dir is not None:
        target_dir = project_dir.expanduser().resolve()
    elif (source_dir / "pyproject.toml").is_file():
        target_dir = source_dir
    else:
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
def gui(
    project_dir: Path | None = typer.Option(  # noqa: B008 - Typer 声明式参数
        None,
        "--project-dir",
        "-d",
        help="项目根目录，包含 config/ 与 data/",
    ),
    host: str = typer.Option("127.0.0.1", help="监听地址，仅允许本机回环地址"),
    port: int = typer.Option(8765, min=0, max=65535, help="本地监听端口，0 表示自动选择"),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="启动后不自动打开浏览器",
    ),
) -> None:
    """启动本地浏览器可视化界面。"""
    from gui.server import run_gui

    run_gui(
        resolve_project_dir(project_dir),
        host=host,
        port=port,
        open_browser=not no_browser,
    )


@app.command()
def version() -> None:
    """显示版本信息。"""
    typer.echo("LocalAI Agent Router 0.2.0")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
