# -*- coding: utf-8 -*-
"""店铺视图切换：**改 mallid 即切店**（2026-11-26 真机抓到的机制）。

★ 前后快照对比（`_logs/store_snap/before2.json` vs `after2.json`）：
    cookie        mallid:                    634418220469472 → 634418220317839
    localStorage  agentseller-mall-info-id:  634418220469472 → 634418220317839
    ⇒ **切店 = 同时改这两处 + reload**，其它都没变。

★ 各店的 mallid（local/半托管 那个才是能用订单接口的）：
    KinglamanSport local   634418220469472   ← 我们代码里 MALLID 用的就是这个
    KinglamanSport         634418220317839   （非 local，没有订单管理菜单）

用法：
    python pw_switch_store.py local      # 切到 KinglamanSport local（半托管）
    python pw_switch_store.py plain      # 切到 KinglamanSport（非 local）
    python pw_switch_store.py show       # 只看当前是哪个
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

#: KLS 两个视图的 mallid
KLS = {"local": "634418220469472", "plain": "634418220317839"}
JS_SHOW = r"""(() => {
  const t = (document.body && document.body.innerText) || '';
  return JSON.stringify({
    url: location.href,
    hasOrder: t.indexOf('订单管理') >= 0,
    head: t.slice(0, 120).replace(/\s+/g, ' '),
    ls: (() => { try { return localStorage.getItem('agentseller-mall-info-id'); }
                 catch (e) { return ''; } })(),
  });
})()"""


def cookie_value(ws_url: str, name: str) -> str:
    import cdp_transport  # noqa: PLC0415
    r = cdp_transport.Cdp(ws_url).send("Network.getCookies", {}, timeout=10)
    for c in ((r.get("result") or {}).get("cookies") or []):
        if c.get("name") == name:
            return str(c.get("value"))
    return ""


def show(ws_url: str) -> dict:
    d = json.loads(asyncio.run(A._ev(ws_url, JS_SHOW)) or "{}")
    d["cookie_mallid"] = cookie_value(ws_url, "mallid")
    return d


def switch(which: str) -> int:
    mallid = KLS.get(which)
    if not mallid:
        sys.exit("用法: pw_switch_store.py local|plain|show")
    ws_url, url = A._page_ws("KinglamanSport")
    cur = show(ws_url)
    print("当前: cookie_mallid=%s ls=%s 有订单菜单=%s"
          % (cur.get("cookie_mallid"), cur.get("ls"), cur.get("hasOrder")))
    if cur.get("cookie_mallid") == mallid and cur.get("ls") == mallid:
        print("已经是目标店铺（%s）" % which)
        return 0

    print("切到 %s（mallid=%s）…" % (which, mallid))
    js = r"""(() => {
      try { localStorage.setItem('agentseller-mall-info-id', '%s'); } catch (e) {}
      document.cookie = 'mallid=%s; path=/; domain=.temu.com';
      document.cookie = 'mallid=%s; path=/';
      return JSON.stringify({ls: (() => { try {
        return localStorage.getItem('agentseller-mall-info-id'); } catch (e) { return ''; } })(),
        ck: document.cookie.indexOf('mallid=%s') >= 0});
    })()""" % (mallid, mallid, mallid, mallid)
    print("  注入:", asyncio.run(A._ev(ws_url, js)))
    asyncio.run(A._ev(ws_url, "(() => { location.reload(); return 1; })()"))
    time.sleep(12)
    try:
        ws2, _u = A._page_ws("KinglamanSport")
        after = show(ws2)
        print("切换后: cookie_mallid=%s ls=%s 有订单菜单=%s"
              % (after.get("cookie_mallid"), after.get("ls"), after.get("hasOrder")))
    except Exception as ex:  # noqa: BLE001
        print("  回读失败（页面可能还在加载）:", str(ex)[:80])
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "show"
    if arg == "show":
        w, _u = A._page_ws("KinglamanSport")
        print(json.dumps(show(w), ensure_ascii=False, indent=1))
        sys.exit(0)
    raise SystemExit(switch(arg))
