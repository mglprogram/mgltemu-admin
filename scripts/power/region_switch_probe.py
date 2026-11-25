# -*- coding: utf-8 -*-
"""诊断 BEL「全球 / 美国」区域切换对接口的影响。

用户 2026-11-25：「你看不到可以切换到美国，如果在全球是没有订单的」。
⇒ 区域选择（全球/美国/欧区）是**页面状态**，可能也决定接口参数（regionId1 / siteId）。

做法（只读）：
  ① 找区域切换控件（全球 / 美国 / 欧区 三个 tab）
  ② 看当前选中哪个
  ③ 点「美国」，等页面重载，再发一次 recentOrderList 看 200/403
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402
import order_list_api as O  # noqa: E402

FIND_REGION = r"""(() => {
  const vis = (e) => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const out = [];
  document.querySelectorAll('div,span,a,button,li').forEach((e) => {
    if (e.children.length > 2 || !vis(e)) return;
    const t = (e.innerText || '').replace(/\s+/g, '');
    if (t === '全球' || t === '美国' || t === '欧区') {
      const r = e.getBoundingClientRect();
      out.push({t: t, x: Math.round(r.x + r.width / 2),
                y: Math.round(r.y + r.height / 2),
                cls: String(e.className || '').slice(0, 40),
                selected: String(e.className || '').match(/active|selected|checked/i) ? true : false});
    }
  });
  return JSON.stringify(out.slice(0, 12));
})()"""

CLICK = r"""(() => {
  const vis = (e) => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  let hit = null;
  document.querySelectorAll('div,span,a,button,li').forEach((e) => {
    if (hit || e.children.length > 2 || !vis(e)) return;
    if ((e.innerText || '').replace(/\s+/g, '') === '美国') hit = e;
  });
  if (!hit) return 'not-found';
  hit.click();
  return 'clicked';
})()"""


async def probe():
    ws_url, _ = A._page_ws("BEL")
    hdr = A.load_headers("BEL") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep.setdefault("mallid", A.MALLID["BEL"])
    body = dict(O.BASE_BODY, pageNumber=1, pageSize=2)
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(O.PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await asyncio.wait_for(A._ev(ws_url, js), timeout=45) or "{}")
    return out.get("status"), str(out.get("body"))[:90]


async def main():
    ws_url, _ = A._page_ws("BEL")
    print("区域控件:", await A._ev(ws_url, FIND_REGION))
    st, bd = await probe()
    print("点美国前 status:", st, bd)
    print("点「美国」:", await A._ev(ws_url, CLICK))
    await asyncio.sleep(6)
    st2, bd2 = await probe()
    print("点美国后 status:", st2, bd2)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
