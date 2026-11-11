# -*- coding: utf-8 -*-
"""只读排查：修改库存弹窗底部「确认」到底有几个、分属哪一层、点了会不会 loading。

    python scripts/goods/probe_confirm_btn.py --spu 1208899974

不点「确认」提交，只：① 列按钮 DOM 层级；② 点一次并采样按钮 class/disabled 变化前后。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
from ziniao_port import probe_port  # noqa: E402


JS_LIST = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('button')).forEach(function (b) {
  if (t(b) !== '确认') return;
  var q = b.getBoundingClientRect();
  if (!q.width) return;
  // 往上数层级，找带 MDL_ 的祖先（弹窗根），并记下 modal 标题
  var chain = [], root = '', rootIdx = -1, depth = 0;
  for (var e = b; e && e !== document.body; e = e.parentElement, depth++) {
    var cls = String(e.className || '');
    chain.push(depth + ':' + e.tagName + '.' + cls.slice(0, 28));
    if (!root && /MDL_|PT_|PP_/.test(cls)) { root = cls.slice(0, 40); rootIdx = depth; }
  }
  var p = window.__zr.findProps(b);
  out.push({
    xy: [Math.round(q.x + q.width / 2), Math.round(q.y + q.height / 2)],
    cls: String(b.className).slice(0, 60),
    disabled: !!b.disabled,
    onClick: !!(p && typeof p.onClick === 'function'),
    root: root, rootDepth: rootIdx, chain: chain.slice(0, 10),
    parentText: (b.parentElement ? (b.parentElement.innerText || '').slice(0, 40) : '')
  });
});
return JSON.stringify(out);"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spu", required=True)
    ap.add_argument("--store", default="BHY")
    a = ap.parse_args()
    win = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}[a.store]
    port = probe_port(win)
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.store = a.store
    assert await tab.open(), "工作页打不开"
    await tab.ensure()
    await zreact.inject(tab.session)
    if not await B.dialog_open(tab):
        print("打开弹窗:", await B.open_stock_dialog(tab, a.spu))
        await asyncio.sleep(3)
    print("弹窗在:", await B.dialog_open(tab))

    print("\n=== ① 当前页面上所有「确认」按钮 ===")
    lst = json.loads(await B.ev(tab, JS_LIST) or "[]")
    for i, b in enumerate(lst):
        print("[%d] xy=%s disabled=%s onClick=%s" % (i, b["xy"], b["disabled"], b["onClick"]))
        print("    class=%s" % b["cls"])
        print("    modalRoot(depth=%s)=%s  parentText=%r" % (b["rootDepth"], b["root"], b["parentText"]))
        for c in b["chain"]:
            print("      ", c)

    # ② 点亮一次：只点「弹窗底部」那个（y 最大、在 MDL 根里），并采样 0.3/1/2.5 秒后的 class
    target = None
    for b in lst:
        if b["rootDepth"] > 0 and b["xy"][1] > 600:
            target = b
    if not target:
        print("\n未找到弹窗底部的确认按钮")
        return 0
    print("\n=== ② 点这一个:", target["xy"], "===")
    await B.click_xy(tab, target["xy"])
    for d in (0.3, 1.0, 2.5):
        await asyncio.sleep(d)
        st = await B.ev(tab, r"""
var t = function (e) { return (e.innerText || '').trim(); };
return JSON.stringify(Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  var q = b.getBoundingClientRect();
  return t(b) === '确认' && q.width && q.y > 600; }).map(function (b) {
  return {cls: String(b.className).slice(0, 70), disabled: !!b.disabled,
          hasLoading: /loading|Loading/.test(String(b.className) + (b.innerHTML || '')),
          text: (b.innerText || '').trim()};
}));""")
        print("  +%ss 按钮态: %s" % (d, st))
    print("\n风险框:", await B.ev(tab, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var q = d.getBoundingClientRect(); var s = d.innerText || '';
  return q.width > 200 && q.width < 700 && q.height > 60 && q.height < 350 && (s.indexOf('建议SKU') >= 0 || s.indexOf('风险') >= 0) && d.querySelectorAll('button').length > 0; });
return JSON.stringify(box.map(function (d) { return t(d).slice(0, 90); }));"""))
    print("弹窗还开:", await B.dialog_open(tab))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
