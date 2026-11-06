# -*- coding: utf-8 -*-
"""pw_pick_track.py — 待打包页：店铺筛选 + 「未出跟踪号」筛选 → 勾选 → 批量获取单号。

用户口径（2026-11-06，截图确认）：
  「这样筛选」—— 页头 `全部订单` 下拉要选 **`未出跟踪号`**（value=2），
  再配合店铺代码（BEL=634418227477419 / BHY=634418228526171）→ 查询 → 勾选 → 批量获取单号。

真机字段：`select[name=is_cd]`
    全部订单='' / 已出跟踪号=1 / **未出跟踪号=2** / 缺货=5 / 无物料订单=6
    / 有物料订单=7 / 已出跟踪号店铺订单=9 / 已出跟踪号面单异常=10
底部按钮：`批量获取单号` onclick=edit_order('huoqumd')
"""
import argparse
import json
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)
from cdp_transport import CdpPage, http_targets  # noqa: E402

PORT = 9333
PEEK = r"D:\AI-workplace\TEMU\scripts\power\out\_peek"
SHOPS = {"BEL": "634418227477419", "BHY": "634418228526171"}
URL = "https://www.powerwarehouse.net/deliver.php?act=list&pb=2"

JS_SUM = r"""() => {
  const b = (document.body.innerText || '').replace(/\s+/g, ' ');
  const m = b.match(/总计\s*(\d+)\s*个记录分为\s*(\d+)\s*页[^ ]*每页\s*(\d+)/);
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('tr')].filter(vis).map(txt)
    .filter(t => /PO-\d+-\d+|GSU/.test(t));
  const withTrack = rows.filter(t => /1Z[0-9A-Z]{16}|GFUS\d+|SPX[A-Z0-9]{6,}|SWX\d+/.test(t)).length;
  return JSON.stringify({summary: m ? m[0] : '', rows: rows.length, withTrack,
    shop: (document.querySelector('input[name=shop_id]') || {}).value || '',
    cd: (document.querySelector('select[name=is_cd]') || {}).value || ''});
}"""

JS_CHECK = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const cbs = [...document.querySelectorAll('input[type=checkbox]')].filter(vis)
    .filter(c => /PO-\d+-\d+|GSU/.test(((c.closest('tr') || {}).innerText || '')));
  cbs.forEach(c => { c.checked = true; c.setAttribute('checked', 'checked'); });
  return JSON.stringify({rows: cbs.length,
    checked: cbs.filter(c => c.getAttribute('checked') === 'checked').length});
}"""


def kill_dialog(pg):
    try:
        pg.evaluate("() => { window.alert = function(){}; window.confirm = function(){ return true; }; "
                    "window.onbeforeunload = null; return 'ok'; }")
    except Exception as ex:  # noqa: BLE001
        print("   ⚠ 废弹窗失败:", str(ex)[:50])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", required=True, choices=list(SHOPS))
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    pg = CdpPage(PORT, [t for t in http_targets(PORT) if t.get("type") == "page"][0])
    kill_dialog(pg)
    if "pb=2" not in (pg.evaluate("() => location.href") or ""):
        pg.evaluate("(u) => { location.href = u; return 'ok'; }", URL)
        time.sleep(9)
        pg = CdpPage(PORT, [t for t in http_targets(PORT) if t.get("type") == "page"][0])
        kill_dialog(pg)
    print("①", pg.evaluate(JS_SUM))

    cdp = pg.cdp_session()

    # ② 店铺代码
    bx = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name=shop_id]');
      e.scrollIntoView({block:'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), cur: e.value});
    }"""))
    if str(bx.get("cur")).strip() != SHOPS[a.shop]:
        pg.dispatch_mouse(bx["x"], bx["y"]); time.sleep(0.3)
        for _ in range(2):
            cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65,
                                                "nativeVirtualKeyCode": 65, "modifiers": 2})
            cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65,
                                                "nativeVirtualKeyCode": 65, "modifiers": 2})
            cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                                "code": "Backspace", "windowsVirtualKeyCode": 8,
                                                "nativeVirtualKeyCode": 8})
            cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                                "code": "Backspace", "windowsVirtualKeyCode": 8,
                                                "nativeVirtualKeyCode": 8})
        time.sleep(0.3)
        pg.dispatch_mouse(bx["x"], bx["y"]); time.sleep(0.2)
        cdp.send("Input.insertText", {"text": SHOPS[a.shop]})
        time.sleep(0.6)
        print("② 店铺:", pg.evaluate("() => document.querySelector('input[name=shop_id]').value"))

    # ③ 订单类型 = 未出跟踪号(2)   ← 用户截图口径
    print("③ 订单类型:", pg.evaluate(r"""() => {
      const s = document.querySelector('select[name=is_cd]');
      s.value = '2';
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return JSON.stringify({v: s.value, t: s.options[s.selectedIndex].text});
    }"""))
    time.sleep(0.6)

    # ④ 查询（只点一次）
    q = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button],a')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
    }"""))
    pg.dispatch_mouse(q["x"], q["y"])
    time.sleep(12)
    kill_dialog(pg)
    print("④ 筛选后:", pg.evaluate(JS_SUM))

    # ⑤ 勾选
    print("⑤ 勾选:", pg.evaluate(JS_CHECK))

    btns = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
      const txt = e => (e.innerText||e.value||'').replace(/\s+/g,' ').trim();
      const el = [...document.querySelectorAll('input[type=button],button,a')].filter(vis)
        .find(e => /批量获取单号/.test(txt(e)));
      if (!el) return '{}';
      el.scrollIntoView({block:'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({t: txt(el), x: Math.round(r.x+r.width/2),
                             y: Math.round(r.y+r.height/2), on: el.getAttribute('onclick')||''});
    }""") or "{}")
    print("⑥ 批量获取单号:", btns)
    raw = pg.screenshot()
    with open(PEEK + "\\pw_trk_ready_%s.png" % a.shop, "wb") as f:
        f.write(raw)
    print("   落图 pw_trk_ready_%s.png" % a.shop)

    if not (a.commit and btns.get("x")):
        print("\n[dry-run] 未提交")
        return 0
    pg.dispatch_mouse(btns["x"], btns["y"])
    time.sleep(18)
    kill_dialog(pg)
    print("\n⑦ 获取后:", pg.evaluate(JS_SUM))
    raw = pg.screenshot()
    with open(PEEK + "\\pw_trk_done_%s.png" % a.shop, "wb") as f:
        f.write(raw)
    print("   落图 pw_trk_done_%s.png" % a.shop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
