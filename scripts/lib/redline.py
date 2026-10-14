# -*- coding: utf-8 -*-
"""lib/redline.py — 经验飞轮的『错误清单』加载与匹配（数据驱动，非硬编码）。

对治：经验飞轮把"坑"写进 SKILL 正文（散文、LLM 自觉），不靠程序拦。
原则：错误统一存 `scripts/data/red_lines.json`（结构化，一条=信号+动作+提示）；
     `lib/audit`/`lib/preflight` 从这里读信号生成检测规则，命中即 HARD_STOP。
     以后踩到新坑 → `python lib/add_redline.py "标题" "signal_pattern" ["hint"]` 加一条，
     不再改任何 markdown 正文。

用法：
    from lib import redline
    redline.load()                      # -> list[dict]，每次现读（不缓存）
    redline.match(text)                 # -> list[dict]，text 里命中的红线
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

__all__ = ["DEFAULT_REDLINES", "load", "match", "rel_path"]

# 红线清单默认路径（相对 scripts/ 根；可用 REDLINES_PATH 环境变量覆盖）
DEFAULT_REDLINES = "data/red_lines.json"


class RedlineNotFound(Exception):
    """红线清单文件缺失。"""


def _root() -> Path:
    # lib/redline.py 上一级 = scripts/
    return Path(__file__).resolve().parent.parent


def _redlines_path() -> Path:
    import os
    env = os.environ.get("REDLINES_PATH")
    return Path(env).expanduser() if env else _root() / DEFAULT_REDLINES


def load() -> list[dict]:
    """加载红线清单（每次现读，`data/red_lines.json` 为唯一权威）。"""
    p = _redlines_path()
    if not p.exists():
        raise RedlineNotFound(f"red_lines.json 不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("red_lines", [])


def match(text: str) -> list[dict]:
    """返回 text 中命中的红线列表：{id,title,hint,start,matched}。"""
    out: list[dict] = []
    for rl in load():
        if rl.get("status") != "active":
            continue
        sig = rl.get("signal") or ""
        if not sig:
            continue
        try:
            rx = re.compile(sig)
        except re.error:
            continue
        m = rx.search(text)
        if m:
            out.append({
                "id": rl.get("id"),
                "title": rl.get("title"),
                "hint": rl.get("hint", ""),
                "start": m.start(),
                "matched": m.group(0),
            })
    return out


def rel_path() -> str:
    try:
        return str(_redlines_path().relative_to(Path.cwd()))
    except ValueError:
        return str(_redlines_path())


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # 自检：python lib/redline.py "<text>"
        for hit in match(sys.argv[1]):
            print(f"- [{hit['id']}] {hit['title']} @{hit['start']} 命中={hit['matched']!r}  提示: {hit['hint']}")
        sys.exit(0 if match(sys.argv[1]) else 0)
    print("redline.load() → N 条；redline.match(text) → 命中列表")
