# -*- coding: utf-8 -*-
"""lib/paths.py — 多设备去绑定：统一「会漂移路径」解析（不写死电脑名/用户名）。

对治：业务代码 / 数据 JSON 写死 C:\\Users\\<某用户名>\\...，换一台设备（用户名/盘符不同）即失效；
      而旧信号（只抓某一个已废弃账号名）属单值绑定，抓不住「任意用户名硬编码」。
原则：任何「本机 / 项目根 / 库根 / 用户主目录 / 应用数据」路径一律经本模块解析，
      正文禁写死用户名绝对路径；可移植 = 环境变量覆盖 + Path.home()/expanduser + 相对项目根派生。

环境变量覆盖（多设备 / 非默认布局建议设置，否则用默认值）：
    TEAM_TOOL_ROOT         项目根（默认 当前工作区根）
    METHOD_SCRIPTS_ROOT    ai-python-methodology/scripts 根（默认 = LIB_ROOT，本文件所在包根，不绑特定机器）

用法：
    from lib import paths as p
    p.PROJECT_ROOT / "发票相关/产品型号库.xlsx"
    p.HOME / "AppData/Roaming/ziniaobrowserdatas/..."
    p.resolve("相对路径")            # 相对 PROJECT_ROOT
    p.resolve("相对路径", base=p.PROJECT_ROOT / "scripts")
    p.put_lib_on_path()              # 幂等把 lib 包根加入 sys.path
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = [
    "HOME", "PROJECT_ROOT", "LIB_ROOT", "METHOD_ROOT",
    "env_path", "resolve", "resolve_config", "put_lib_on_path",
]

# 用户主目录（自动跟随设备用户，不绑定 Colorful）
HOME = Path.home()

# lib 包根 = <ai-python-methodology>/scripts（lib/paths.py 的两级上级）
LIB_ROOT = Path(__file__).resolve().parent.parent


def env_path(name: str, default: Path) -> Path:
    """读环境变量（可含 ~ 展开），未设置则返回 default。"""
    v = os.environ.get(name)
    if not v:
        return default
    return Path(os.path.expanduser(v)).resolve()


METHOD_ROOT = env_path("METHOD_SCRIPTS_ROOT", LIB_ROOT)
PROJECT_ROOT = env_path("TEAM_TOOL_ROOT", HOME / "Desktop" / "团队工具开发")


def resolve(p: str | Path, *, base: Path | None = None) -> Path:
    """把路径展开并**重锚定**到当前 PROJECT_ROOT（多设备去绑定，2026-08-31）。

    规则：
      - 相对路径 → 拼 base(默认 PROJECT_ROOT)。
      - 绝对路径凡含 `\\Desktop\\团队工具开发\\` 前缀（**任意用户名**）→ 取其尾部重锚到当前 PROJECT_ROOT
        （数据 JSON 里写死的 C:\\Users\\<旧用户名>\\Desktop\\团队工具开发\\X，换任意用户名/盘符的机器都自动
        跟随当前 PROJECT_ROOT——不再依赖 HOME 用户名，跨设备/跨用户名去绑定）。
      - 其它绝对路径（AppData、网络盘等非项目数据）→ 原样返回，不重锚。
    """
    s = os.path.expanduser(str(p))
    pl = Path(s)
    if not pl.is_absolute():
        return ((base or PROJECT_ROOT) / pl).resolve()
    # 任意用户名下的 \Desktop\团队工具开发\ 前缀 → 重锚到当前 PROJECT_ROOT（跨用户名去绑定）
    m = re.match(r"^.*[\\/]Desktop[\\/]团队工具开发[\\/](.+)$", s)
    if m:
        return (PROJECT_ROOT / m.group(1)).resolve()
    return pl  # 不在项目根下（AppData 等）→ 原样


def resolve_config(obj):
    """递归遍历已加载的配置对象，把「绝对且在标准项目根之下」的字符串路径重锚到当前 PROJECT_ROOT。

    用法：config 加载后统一 `config = paths.resolve_config(config)`（只需一处调用，覆盖所有路径字段，
    含 images 等嵌套字典/列表里的 img 路径）。相对路径 / 非项目绝对路径(%TEMP%、AppData、http、网络盘)
    原样保留。数据 JSON 里无需再手写相对路径，多设备自动跟随 TEAM_TOOL_ROOT/项目根。
    """
    if isinstance(obj, dict):
        return {k: resolve_config(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_config(v) for v in obj]
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith(("C:\\", "C:/", "\\\\", "/")):
            try:
                return str(resolve(s))
            except Exception:
                return obj
        return obj
    return obj


def put_lib_on_path() -> str:
    """幂等把 lib 包根加进 sys.path（供无法依赖 PYTHONPATH 的入口场景），返回其字符串。"""
    s = str(METHOD_ROOT)
    if s not in sys.path:
        sys.path.insert(0, s)
    return s


if __name__ == "__main__":
    print("HOME          =", HOME)
    print("PROJECT_ROOT  =", PROJECT_ROOT)
    print("METHOD_ROOT   =", METHOD_ROOT)
    print("LIB_ROOT      =", LIB_ROOT)
    print("resolve('foo')=", resolve("foo"))
