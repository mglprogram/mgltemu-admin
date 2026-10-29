# -*- coding: utf-8 -*-
"""zpage.py — 紫鸟店窗口「页面内 JS」薄封装（goods 域单点实现）。

与 scripts/bel_goods/zpage.py 同构（那边是 bel_goods 域的实现，不改动它）。
差别只在这里额外支持 `page visit`，因为本域的抓取要自己把窗口导到商品列表页。

用法：
    from zpage import page_exec, page_visit, current_url
    page_visit(STORE_ID, "https://agentseller.temu.com/goods/list")
    out = page_exec(JS_FILE, STORE_ID, parse_json=True)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ENTRY = (Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"
          / "@ziniao-open" / "cli" / "scripts" / "run.js")

#: 当前要操作的标签页（由 `use_target()` 设定）。**空 = 桥自己挑"当前页"= 用户的页**。
#: 2026-10-29 事故：goods 域全靠"桥挑当前页"，于是定时任务把用户开着的订单页导航成了商品列表页。
_CURRENT_TARGET = ""


def use_target(tid: str) -> None:
    """把后续 `page_exec*` 限定到这张标签页（自己开的）。传空串恢复旧行为。"""
    global _CURRENT_TARGET
    _CURRENT_TARGET = str(tid or "")


def _run(args, timeout: int = 180):
    last = None
    for _ in range(2):                      # 桥抖动重试一次（watch_focus 同口径）
        proc = subprocess.run(
            ["node", str(_ENTRY)] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        if proc.stdout.strip():
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as e:   # noqa: PERF203
                last = f"输出不是 JSON: {proc.stdout[:200]}"
        else:
            last = f"无输出 rc={proc.returncode} stderr={proc.stderr[:300]}"
    raise RuntimeError(last or "ziniao-cli 调用失败")


def page_exec(js_file: str | Path, store_id: str, parse_json: bool = True,
              timeout: int = 180):
    """在店铺**自己的**标签页执行 JS 文件；parse_json=True 时把返回字符串再解析成对象。

    ★ 2026-10-29：带 `--target-id`（由本模块 `use_target()` 设定）。不带的话桥会打在
      **窗口当前页**＝用户正开着的那页上 —— 那就是"定时任务把用户的页面改掉"的机制。
    """
    js = Path(js_file).read_text(encoding="utf-8").strip()
    data = _run(["page", "exec", "--store-id", str(store_id)]
                + (["--target-id", _CURRENT_TARGET] if _CURRENT_TARGET else [])
                + ["--script", js], timeout)
    inner = data.get("data", {}).get("data", {})
    exc = inner.get("exceptionDetails")
    if exc:
        raise RuntimeError("页面异常: " + json.dumps(exc, ensure_ascii=False)[:600])
    result = inner.get("result")
    if isinstance(result, dict):            # 有些版本直接回对象
        result = result.get("value", result)
    if not parse_json or not isinstance(result, str):
        return result
    return json.loads(result)


def page_visit(store_id: str, url: str, wait_until: str = "domcontentloaded",
               timeout_ms: int = 90000) -> tuple[int, str]:
    """把**自己的**标签页导到指定 URL；返回 (returncode, 输出尾部)。

    ★ 两个 2026-10-29 实测修正：
      1. 必须带 `--target-id`（`use_target()`），否则导航的是**用户的当前页**；
      2. `page visit` 成功时 **stdout 为空、stderr 才有一行「✓ 页面已导航」** ——
         旧实现走 `_run`（只认 stdout JSON），把成功判成「无输出 rc=0」并在日志里
         刷成 rc=1，调用方误以为导航失败去重试，等于又导航一次用户的页。
    """
    args = (["page", "visit", "--store-id", str(store_id)]
            + (["--target-id", _CURRENT_TARGET] if _CURRENT_TARGET else [])
            + ["--url", url, "--wait-until", wait_until, "--timeout", str(timeout_ms)])
    try:
        proc = subprocess.run(["node", str(_ENTRY)] + args, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=int(timeout_ms / 1000) + 60)
    except Exception as e:                  # noqa: BLE001
        return 1, str(e)[:300]
    tail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return int(proc.returncode or 0), tail[:300]


def current_url(store_id: str) -> str:
    js = "(function(){try{return JSON.stringify({url:location.href,host:location.host,path:location.pathname});}catch(e){return '';}})()"
    try:
        d = page_exec_str(js, store_id)
        return json.loads(d).get("url", "") if d else ""
    except Exception:                       # noqa: BLE001
        return ""


def page_exec_str(js: str, store_id: str, timeout: int = 120):
    """直接执行一段 JS 源码（字符串），返回其字符串结果（限定到 `use_target()` 的页）。"""
    data = _run(["page", "exec", "--store-id", str(store_id)]
                + (["--target-id", _CURRENT_TARGET] if _CURRENT_TARGET else [])
                + ["--script", js], timeout)
    inner = data.get("data", {}).get("data", {})
    if inner.get("exceptionDetails"):
        raise RuntimeError("页面异常: "
                           + json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:400])
    r = inner.get("result")
    return r.get("value") if isinstance(r, dict) else r


def page_click(selector: str, store_id: str, timeout: int = 120) -> dict:
    """CLI 可信点击（真实鼠标事件）——**合成事件推不动的按钮靠它**。

    实测（2026-10-12）：商品列表的「查询」按钮对页面内 dispatch 的全套
    pointer/mouse 事件无反应；CLI 的 page click 会走 Bridge 的鼠标指令。
    """
    try:
        data = _run(["page", "click", "--store-id", str(store_id)]
                    + (["--target-id", _CURRENT_TARGET] if _CURRENT_TARGET else [])
                    + ["--selector", selector], timeout)
    except Exception as e:                  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    return data


def page_input(selector: str, text: str, store_id: str, clear: bool = True,
               submit: bool = False, timeout: int = 120) -> dict:
    """CLI 输入文本（真实键盘事件），可选提交。"""
    args = (["page", "input", "--store-id", str(store_id)]
            + (["--target-id", _CURRENT_TARGET] if _CURRENT_TARGET else [])
            + ["--selector", selector, "--text", text])
    if clear:
        args.append("--clear")
    if submit:
        args.append("--submit")
    try:
        return _run(args, timeout)
    except Exception as e:                  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
