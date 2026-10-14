# -*- coding: utf-8 -*-
"""lib/uio.py — UTF-8 IO 强制封装（工程控制闸门层）。

对治第二高反复错误：**中文编码 GBK 乱码**（历史会话 chcp 被调 1,231 次专程救）。
原则：含中文字符串的读写**一律显式 encoding='utf-8'**，杜绝裸 open()/print() 在
GBK 控制台乱码。同时提供：读/写/追加，以及 sqlite 连接（url 之外的读写）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

__all__ = ["read_utf8", "write_utf8", "append_utf8", "read_json", "write_json", "connect_sqlite"]


def _p(path: Union[str, Path]) -> Path:
    return Path(path)


def read_utf8(path: Union[str, Path], default: Optional[str] = None) -> str:
    """读文本，显式 utf-8；不存在返回 default。"""
    p = _p(path)
    if not p.exists():
        return default if default is not None else ""
    return p.read_text(encoding="utf-8", errors="replace")


def write_utf8(path: Union[str, Path], content: str, bom: bool = False) -> None:
    """写文本，强制 utf-8。bom=True 时写 UTF-8 BOM（供 .ps1 中文脚本）。

    注意 AGENTS.md 铁律：PowerShell 脚本文件需带 BOM；edit 工具改 .ps1 会丢 BOM。
    """
    p = _p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if bom:
        p.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    else:
        p.write_text(content, encoding="utf-8")


def append_utf8(path: Union[str, Path], content: str) -> None:
    """追加文本，强制 utf-8。"""
    p = _p(path)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)


def read_json(path: Union[str, Path], default: Any = None) -> Any:
    """读 JSON，显式 utf-8；解析失败/不存在返回 default。"""
    p = _p(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Union[str, Path], obj: Any, indent: int = 2) -> None:
    """写 JSON，强制 utf-8 + ensure_ascii=False（中文不转义）。"""
    p = _p(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")


def connect_sqlite(path: Union[str, Path]) -> sqlite3.Connection:
    """开 sqlite 连接（默认 UTF-8 文本工厂）。"""
    return sqlite3.connect(str(_p(path)))


if __name__ == "__main__":
    # 自检：读/写一处 UTF-8 中文，验证不乱码
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "中文.txt"
        write_utf8(str(p), "中文测试：达人、样品、紫鸟\n")
        s = read_utf8(str(p))
        assert "达人" in s and "紫鸟" in s, s
        p.unlink(missing_ok=True)
    print("uio 自检 OK")
