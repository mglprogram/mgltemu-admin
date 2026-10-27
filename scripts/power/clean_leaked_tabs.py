# -*- coding: utf-8 -*-
"""清掉紫鸟窗口里**脚本开完没关**的残留标签页（只读判定 + 只按 tid 关）。

为什么需要这个工具（2026-10-27 用户投诉「你新开的标签页用完不关」）：
  脚本 `finally` 里已经会关页，但**计划任务超时强杀**（结果码 `0x41306` =
  `TerminateProcess`）时，Python 连一行收尾代码都跑不到 ⇒ 临时页永远留在窗口里。
  `cdp_transport.sweep_leaked_scratch()` 已能给「登记过的页」兜底；本工具用于：
    · 排查（先看清窗口里到底有哪些页、哪个卡死、哪个是脚本页）；
    · 清理**没登记**的历史残留（注册表功能上线之前留下的那些）。

三条硬规矩（少一条就可能动到用户正在用的页，或把整个窗口关没）：
  ① 只关**脚本专用 URL** 的页 + 该页对 `Runtime.evaluate` **无响应**（卡死）——
     用户正在看的页一定答得出；
  ② 关完之后窗口里**至少还剩 1 个活着的真实页**（关最后一个 ⇒ 店铺窗口消失，
     2026-09-24 事故）；
  ③ 全程只读判定、按 targetId 关，绝不按 URL 猜、绝不点页面。

用法：
  python scripts/power/clean_leaked_tabs.py 64036                 # 只报告（不改任何东西）
  python scripts/power/clean_leaked_tabs.py 64036 <tid> [tid...]  # 关掉点名的页（受上面三条约束）
  python scripts/power/clean_leaked_tabs.py 64036 --sweep         # 跑登记表兜底清理
  python scripts/power/clean_leaked_tabs.py --window TM-BEL-MGL   # 现场探端口再报告
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cdp_transport as cdp  # noqa: E402
import ziniao_store as zs  # noqa: E402

#: 脚本开出来的临时页只会是这几个地址（用户自己也可能开，所以还要靠"卡死"来判别）
SCRIPT_URL_HINTS = (
    "/main/data-center/goods-data",     # 销量分析（限流/今日销量/预售都读它）
    "/main/flux-analysis",              # 商品流量（流量加速）
    "/newon/product-select",            # 上新生命周期
    "about:blank",                      # 建页后没来得及导航就被杀
)


def page_alive(ws_url: str, timeout: float = 6.0) -> bool:
    """页面 JS 线程还活着吗（浏览器层通不算，要看渲染进程）。"""
    try:
        cdp.Cdp(ws_url).send("Runtime.evaluate",
                             {"expression": "1", "returnByValue": True}, timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def is_script_page(url: str) -> bool:
    u = str(url or "")
    return any(h in u for h in SCRIPT_URL_HINTS)


def report(port: int) -> list:
    pages = cdp.list_pages(port)
    print("端口 %d：%d 个 page target" % (port, len(pages)))
    states = []
    for t in pages:
        tid = str(t.get("id") or "")
        url = str(t.get("url") or "")
        ok = page_alive(t.get("webSocketDebuggerUrl"))
        states.append((tid, url, ok))
        marks = []
        if not ok:
            marks.append("卡死")
        if is_script_page(url):
            marks.append("脚本页")
        print("   %-34s %-5s %s%s"
              % (tid, "alive" if ok else "HUNG", url[:60],
                 ("  ← " + "/".join(marks)) if marks else ""))
    return states


def close_named(port: int, states: list, names: list) -> int:
    live = sum(1 for x in states if x[2])
    closed = 0
    for name in names:
        hit = next((s for s in states if s[0] == name or s[0].startswith(name)), None)
        if not hit:
            print("跳过 %s：窗口里没有这个 target" % name)
            continue
        tid, url, ok = hit
        if not is_script_page(url):
            print("跳过 %s：不是脚本页（%s）" % (tid[:12], url[:50]))
            continue
        if ok:
            print("跳过 %s：它还能响应 JS，可能正是你在用的页" % tid[:12])
            continue
        if live <= 1:
            print("跳过 %s：这是最后一个活着的页，关了窗口会消失" % tid[:12])
            continue
        if cdp.close_target(port, tid):
            closed += 1
            live -= 1
            print("已关 %s  %s" % (tid[:12], url[:56]))
        else:
            print("关失败 %s（页面可能已经没了）" % tid[:12])
    print("关闭 %d 个；窗口存活：%s" % (closed, zs.window_alive(port)))
    return closed


def main() -> int:
    ap = argparse.ArgumentParser(description="清理紫鸟窗口里的脚本残留标签页")
    ap.add_argument("port", nargs="?", type=int, default=0)
    ap.add_argument("tids", nargs="*", help="要关的 targetId（可只给前缀）；不给我就只报告")
    ap.add_argument("--window", default="", help="按窗口名现场探端口（如 TM-BEL-MGL）")
    ap.add_argument("--sweep", action="store_true", help="跑登记表兜底清理（被杀运行留下的页）")
    a = ap.parse_args()

    port = a.port or (zs.probe_port_by_window(a.window) if a.window else 0)
    if not port:
        print("没拿到 CDP 端口：给 --window 或直接传端口号")
        return 2

    states = report(port)
    if a.sweep:
        closed = cdp.sweep_leaked_scratch(port, log=print)
        print("登记表兜底：清掉 %d 个" % len(closed))
    if a.tids:
        close_named(port, states, a.tids)
    elif not a.sweep:
        print("\n（只报告，没关任何东西。要关就把 targetId 跟在端口后面）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
