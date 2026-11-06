# -*- coding: utf-8 -*-
"""pw_confirm_orders.py — pb=2「待打包订单」批量确认订单（有头可见）。

用户口径（2026-11-06）：
  「deliver.php?act=list&pb=2 确认之后 → 批量获取跟踪号」

真机：底部 `select[name=select_types]` 的选项
    请选择操作='' / **确认订单='order_queren'** / 批量修改渠道='pl_xgqd'
旁边有 `提交` 按钮（同 hwc_shop 的「请选择操作 + 提交」套路）。

流程：按店铺代码筛选 → 勾选全部行 → 选 select_types=order_queren → 点提交 → 回读状态。
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", required=True, choices=list(SHOPS))
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    pg = CdpPage(PORT, [t for t in http_targets(PORT) if t.get("type") == "page"][0])
    cdp = pg.cdp_session()
    pg.evaluate("(u) => { location.href = u; return 'ok'; }",
                "https://www.powerwarehouse.net/deliver.php?act=list&pb=2&uselastfilter=1")
    time.sleep(8)

    # 店铺筛选
    box = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name=shop_id]');
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }"""))
    pg.dispatch_mouse(box["x"], box["y"]); time.sleep(0.3)
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
    pg.dispatch_mouse(box["x"], box["y"]); time.sleep(0.2)
    cdp.send("Input.insertText", {"text": SHOPS[a.shop]})
    time.sleep(0.5)
    q = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button],a')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }"""))
    pg.dispatch_mouse(q["x"], q["y"])
    time.sleep(8)

    # 勾选全部
    ck = pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
      const cbs = [...document.querySelectorAll('input[type=checkbox]')].filter(vis)
        .filter(c => /PO-\d+|GSU|SEMI|\d{10,}/.test(((c.closest('tr') || {}).innerText || '')));
      cbs.forEach(c => { c.checked = true; c.setAttribute('checked', 'checked'); });
      return JSON.stringify({rows: cbs.length});
    }""")
    print("%s 勾选: %s" % (a.shop, ck))

    # 选中「确认订单」
    sel = pg.evaluate(r"""() => {
      const s = document.querySelector('select[name=select_types]');
      if (!s) return 'no-select';
      s.value = 'order_queren';
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return JSON.stringify({now: s.value, text: s.options[s.selectedIndex].text});
    }""")
    print("下拉:", sel)

    # 找提交按钮（与下拉同一行/附近）
    sb = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
      const el = [...document.querySelectorAll('input[type=button],button,input[type=submit]')].filter(vis)
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    print("提交按钮:", sb)

    raw = pg.screenshot()
    with open(PEEK + "\\pw_confirm_ready.png", "wb") as f:
        f.write(raw)
    print("落图 pw_confirm_ready.png（勾选+已选确认订单）")

    if not a.commit:
        print("[dry-run] 未给 --commit")
        return
    if not sb.get("x"):
        print("[FAIL] 找不到提交按钮")
        return
    pg.evaluate("() => { window.confirm = function(){ return true; }; return 'ok'; }")
    pg.dispatch_mouse(sb["x"], sb["y"])
    time.sleep(12)
    print("提交后:", pg.evaluate(r"""() => {
      const b = (document.body.innerText||'').replace(/\s+/g,' ');
      const m = b.match(/总计\s*(\d+)\s*个记录/);
      return JSON.stringify({total: m ? m[1] : '',
        msg: (b.match(/(成功|失败|错误|确认)[^，。]{0,40}/g)||[]).slice(0,4)});
    }"""))
    raw = pg.screenshot()
    with open(PEEK + "\\pw_confirm_after.png", "wb") as f:
        f.write(raw)
    print("落图 pw_confirm_after.png")


if __name__ == "__main__":
    main()
