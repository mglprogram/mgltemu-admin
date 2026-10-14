# -*- coding: utf-8 -*-
"""lib/cfgutil.py — 数据文件公共处理（共享库，各域入口复用，不 fork 复制）。"""
from typing import Any, Dict


def merge_config_keys(cfg: dict) -> dict:
    """把顶层公共键（store/storeId/port/outdir…）合并进每步 params。

    数据文件只写一次公共参数，步骤参数按需覆盖顶层；公共调度 run() 保持通用。
    """
    skip = {"engine", "steps", "note", "note_text", "_meta"}
    base: Dict[str, Any] = {
        k: v for k, v in cfg.items()
        if k not in skip and not isinstance(v, (dict, list))
    }
    steps = []
    for s in cfg.get("steps", []):
        p = dict(base)
        p.update(s.get("params", {}))
        steps.append({**s, "params": p})
    return {**cfg, "steps": steps}
