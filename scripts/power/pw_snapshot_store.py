# -*- coding: utf-8 -*-
"""抓「店铺切换」的底层线索 —— 页面切换后哪些地方变了。

思路（用户 2026-11-26 要求）：店铺切换走的是前端路由 + 换 token，
不一定发 `/mmsos/` 请求。真正变的东西通常在：
  ① cookie（`mallid` / `seller_temp` / `storeId` 之类）
  ② localStorage / sessionStorage
  ③ 页头文本（店铺名）

本脚本把这三样**并排快照**，你切换一次，我前后对比就知道差异在哪。
用法：
    python pw_snapshot_store.py before    # 切换前跑
    （你手动切换）
    python pw_snapshot_store.py after     # 切换后跑
    python pw_snapshot_store.py diff      # 对比
"""
import asyncio
import io
import json
import os
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cdp_transport  # noqa: E402
import temu_buylabel_api as A  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\store_snap"
JS = r"""(() => {
  const ls = {}, ss = {};
  try { for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i); ls[k] = String(localStorage.getItem(k)).slice(0, 120); } } catch (e) {}
  try { for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i); ss[k] = String(sessionStorage.getItem(k)).slice(0, 120); } } catch (e) {}
  const t = (document.body && document.body.innerText) || '';
  const i = t.indexOf('订单管理');
  return JSON.stringify({
    url: location.href,
    head: t.slice(0, 260).replace(/\s+/g, ' '),
    hasOrderMenu: t.indexOf('订单管理') >= 0,
    ls: ls, ss: ss,
  });
})()"""


def snap(tag: str):
    ws_url, url = A._page_ws("KinglamanSport")
    raw = asyncio.run(A._ev(ws_url, JS))
    d = json.loads(raw or "{}")
    try:
        r = cdp_transport.Cdp(ws_url).send("Network.getCookies", {}, timeout=10)
        cks = {c.get("name"): str(c.get("value"))[:60]
               for c in ((r.get("result") or {}).get("cookies") or [])}
    except Exception:  # noqa: BLE001
        cks = {}
    d["cookies"] = cks
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "%s.json" % tag)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[%s] url=%s" % (tag, d.get("url", "")[:80]))
    print("       有订单菜单:", d.get("hasOrderMenu"))
    print("       页头:", (d.get("head") or "")[:160])
    print("       cookie %d 条, localStorage %d, sessionStorage %d"
          % (len(cks), len(d.get("ls") or {}), len(d.get("ss") or {})))
    print("       → %s" % p)


def diff():
    a = json.load(open(os.path.join(OUT, "before.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(OUT, "after.json"), encoding="utf-8"))

    def dk(x, y, label):
        ka, kb = set(x), set(y)
        added = kb - ka
        removed = ka - kb
        changed = {k: (x[k], y[k]) for k in (ka & kb) if x[k] != y[k]}
        print("\n=== %s ===" % label)
        if added:
            print("  新增:", sorted(added)[:10])
        if removed:
            print("  消失:", sorted(removed)[:10])
        for k, (v1, v2) in list(changed.items())[:10]:
            print("  变了 %-24s %s → %s" % (k, str(v1)[:40], str(v2)[:40]))
        if not (added or removed or changed):
            print("  无变化")

    print("before url:", a.get("url"))
    print("after  url:", b.get("url"))
    print("订单菜单: %s → %s" % (a.get("hasOrderMenu"), b.get("hasOrderMenu")))
    dk(a.get("cookies") or {}, b.get("cookies") or {}, "cookie")
    dk(a.get("ls") or {}, b.get("ls") or {}, "localStorage")
    dk(a.get("ss") or {}, b.get("ss") or {}, "sessionStorage")


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "before"
    if tag == "diff":
        diff()
    else:
        snap(tag)
