# -*- coding: utf-8 -*-
"""挖 KLS 页面的**店铺视图切换器**结构 —— 为「自动切到 local 半托管」做准备。

用户 2026-11-26：「默认不是 local 半托管店铺，你需要切换到 local 半托管店铺」

本脚本只读：打开 KLS 订单页，抓：
  ① 页面上所有含「local / 半托管 / KinglamanSport / Elanvoke」的元素（文本 + 位置 + class）
  ② 顶栏的店铺切换控件（可能是下拉 / 弹层 / 二级菜单）
  ③ 当前店铺名（如果读得到）
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

JS = r"""(() => {
  const vis = (e) => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const out = {url: location.href, title: document.title, hits: [], topbar: []};

  // ① 含关键字的元素
  document.querySelectorAll('div,span,a,li,button,td').forEach((e) => {
    if (e.children.length > 3 || !vis(e)) return;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 60) return;
    if (/local|半托管|KinglamanSport|Elanvoke|Temu-|切换店铺|店铺切换/i.test(t)) {
      const r = e.getBoundingClientRect();
      out.hits.push({t: t, x: Math.round(r.x), y: Math.round(r.y),
                     cls: String(e.className || '').slice(0, 60),
                     tag: e.tagName.toLowerCase()});
    }
  });

  // ② 顶栏区域（y < 120）的元素文本
  document.querySelectorAll('div,span,a').forEach((e) => {
    if (!vis(e)) return;
    const r = e.getBoundingClientRect();
    if (r.y > 120 || r.height > 60) return;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length <= 40 && e.children.length <= 2) {
      out.topbar.push({t: t, x: Math.round(r.x), y: Math.round(r.y),
                       cls: String(e.className || '').slice(0, 50)});
    }
  });
  out.topbar = out.topbar.slice(0, 40);
  out.hits = out.hits.slice(0, 30);
  return JSON.stringify(out);
})()"""


def main():
    ws_url, url = A._page_ws("KinglamanSport")
    print("页面:", url[:100])
    raw = asyncio.run(A._ev(ws_url, JS))
    d = json.loads(raw or "{}")
    print("标题:", d.get("title"))
    print("\n=== 含 local/半托管/店铺名 的元素 ===")
    for h in (d.get("hits") or []):
        print("  [%s] (%d,%d) %-50s cls=%s"
              % (h.get("tag"), h.get("x"), h.get("y"), h.get("t")[:50], h.get("cls")))
    print("\n=== 顶栏元素（y<120）===")
    for h in (d.get("topbar") or []):
        print("  (%d,%d) %-40s cls=%s" % (h.get("x"), h.get("y"),
                                          h.get("t")[:40], h.get("cls")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
