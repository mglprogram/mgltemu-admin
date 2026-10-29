# -*- coding: utf-8 -*-
"""own_page.py — 让紫鸟桥（`page visit` / `page exec`）只操作**我们自己开的**标签页。

来历（用户 2026-10-29）：「上午执行的定时任务还存在覆盖页面的情况」。

根因（2026-10-29 实测复现，不是推断）：
    `ziniao-cli page visit --store-id X --url U` **不带 `--target-id`** 时，Bridge 拿的是
    窗口的**当前页** —— 也就是用户正开着的那一页。于是：
      · `page visit goods/list` 把用户的**订单页**直接导航成商品列表页；
      · `page exec` 也在用户那页上点「在售中」、切筛选条件、勾导出弹窗。
    实测证据：对一个 about:blank 用户页做 `page visit --url about:blank#probe`，
    该页 URL 从 `mmsos/orders.html` 变成 `about:blank#probe`（其它页不动）；
    带 `--target-id <自建页>` 时，只有自建页变、别人的页原样。

    ⇒ 这与「关窗/关页」是两个不同的破坏面：关窗是**误杀**（已修），
      在别人的当前页上导航/点击是**误覆盖**（本模块修）。

用法：
    from own_page import OwnPage
    with OwnPage(store_id, window_name) as pg:
        pg.visit(url)      # 只导航自建页
        pg.exec(js)        # 只在自己的页上执行
    # 退出时自动关掉自建页；用户的页一个字节都没动

单点实现：标签页的建立/关闭走 `ziniao_store.open_tab` / `close_target`（power/lib）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_POWER_LIB = Path(__file__).resolve().parent
if str(_POWER_LIB) not in sys.path:
    sys.path.insert(0, str(_POWER_LIB))

ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"


def _run(args, timeout: int = 180):
    """跑一次 ziniao-cli（桥抖动重试一次，与 zpage/watch_focus 同口径）。"""
    last = None
    for _ in range(2):
        proc = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if proc.stdout.strip():
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                last = "输出不是 JSON: %s" % proc.stdout[:200]
        else:
            last = "无输出 rc=%s stderr=%s" % (proc.returncode, (proc.stderr or "")[:300])
    raise RuntimeError(last or "ziniao-cli 调用失败")


class OwnPage:
    """自己开一张标签页，桥的 visit/exec 一律带 `--target-id` 指向它。

    `close_on_exit`：默认关掉；跑完还想留着给人看时传 False。
    """

    def __init__(self, store_id: str, window_name: str, url: str = "about:blank",
                 log=print, close_on_exit: bool = True):
        self.store_id = str(store_id)
        self.window = str(window_name or "")
        self.start_url = url or "about:blank"
        self.log = log or (lambda m: None)
        self.close_on_exit = close_on_exit
        self.port = 0
        self.target_id = ""

    # ---- 生命周期
    def __enter__(self):
        import ziniao_store as zs  # noqa: PLC0415
        if self.window:
            self.port = zs.probe_port_by_window(self.window)
        if not self.port:
            raise RuntimeError("探不到窗口 %s 的 CDP 端口（窗口没开？）" % (self.window or "?"))
        tid = zs.open_tab(self.port, self.start_url)
        if not tid:
            raise RuntimeError("开自建标签页失败（窗口 %s）" % self.window)
        self.target_id = tid
        time.sleep(1.0)
        self.log("[页] 自建标签页 %s（只动这一页，不碰你打开的）" % tid[:8])
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> bool:
        if not self.target_id or not self.close_on_exit:
            return False
        try:
            import ziniao_store as zs  # noqa: PLC0415
            ok = zs.close_target(self.port, self.target_id)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.log("[页] 已关掉自建标签页 %s" % self.target_id[:8])
        self.target_id = ""
        return ok

    # ---- 桥调用（一律带 target-id）
    def visit(self, url: str, wait_until: str = "domcontentloaded",
              timeout_ms: int = 90000) -> tuple[int, str]:
        """导航**自建页**。返回 `(rc, 说明)`；rc=0 才算成功。

        ⚠ 不能复用 `_run`：`page visit` 成功时 **stdout 为空**、只有 stderr 一行
        「✓ 页面已导航」（实测 2026-10-29）。旧 `page_visit` 因此把成功判成
        「无输出 rc=0」并在日志里刷成 rc=1，调用方还以为导航失败。
        """
        args = ["page", "visit", "--store-id", self.store_id, "--target-id", self.target_id,
                "--url", url, "--wait-until", wait_until, "--timeout", str(timeout_ms)]
        try:
            proc = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=int(timeout_ms / 1000) + 60)
        except Exception as e:  # noqa: BLE001
            return 1, str(e)[:300]
        tail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return int(proc.returncode or 0), tail[:300]

    def exec(self, script: str, timeout: int = 180):
        """执行 JS 源码，返回原始字符串结果。"""
        d = _run(["page", "exec", "--store-id", self.store_id,
                  "--target-id", self.target_id, "--script", script], timeout)
        inner = d.get("data", {}).get("data", {})
        if inner.get("exceptionDetails"):
            raise RuntimeError("页面异常: "
                               + json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:400])
        r = inner.get("result")
        return r.get("value") if isinstance(r, dict) else r

    def exec_json(self, script: str, timeout: int = 180):
        raw = self.exec(script, timeout)
        return json.loads(raw) if isinstance(raw, str) else raw

    def url(self) -> str:
        try:
            return self.exec_json("(() => JSON.stringify(location.href))()")
        except Exception:  # noqa: BLE001
            return ""


def store_ids(store: str, map_path: str = "") -> tuple[str, str]:
    """店后缀 → `(storeId, 窗口名)`；查不到返回 `("", "")`。"""
    p = map_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 os.pardir, "data", "ziniao_power_map.json")
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:  # noqa: BLE001
        return "", ""
    for it in m.get("mapping") or []:
        keys = (it.get("export_suffix"), it.get("power_name"), it.get("ziniao_account"))
        if store in keys:
            return str(it.get("ziniao_window_store_id") or ""), str(it.get("ziniao_window_name") or "")
    return "", ""
