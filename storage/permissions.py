"""敏感目录、文件权限与原子写入工具。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def secure_directory(path: Path) -> None:
    """创建仅当前用户可访问的目录（POSIX: 0700）。"""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def secure_file(path: Path) -> None:
    """限制敏感文件权限（POSIX: 0600）。"""
    if path.exists() and os.name != "nt":
        path.chmod(0o600)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """将文本写入同目录临时文件并原子替换目标。"""
    secure_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding=encoding, newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        secure_file(temporary)
        os.replace(temporary, path)
        secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
