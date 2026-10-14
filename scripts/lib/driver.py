# -*- coding: utf-8 -*-
"""lib/driver.py — 把"和外部打交道"封成一层。对外只暴露 Driver 接口。

原则：入口脚本 只依赖 Driver 接口（perform），不关心背后是浏览器 / 文件 / 网络。
本示例给一个 ConsoleDriver（只打印）。换真实系统时，实现同名接口即可，
入口不用改 —— 这就是"外部系统封一层"。
"""
from abc import ABC, abstractmethod
from typing import Any


class Driver(ABC):
    """统一借口。新能力 = 新增 action / 方法，不改现有签名。"""

    @abstractmethod
    def perform(self, action: str, params: dict) -> Any:
        raise NotImplementedError


class ConsoleDriver(Driver):
    """示例实现：只打印动作。换成真实系统（浏览器/文件/网络）时替换本实现。"""

    def perform(self, action: str, params: dict) -> Any:
        print(f"[do] {action} {params}")
        return {"ok": True}
