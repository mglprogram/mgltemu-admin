# -*- coding: utf-8 -*-
"""pw_fix_sku.py — 在**无头窗口**里改订单 sku（2026-11-07 起无头，日志全留痕）。

用户口径（2026-11-06）：
  「把复制的 Fnsku 去覆盖原有的 sku，再次下单就会成功」

页面结构（截图确认）：
  产品信息表 → `sku` 输入框（name=ck_sku[]）→ 「提交」按钮。

★ 前一次用 Python 直接 POST 表单失败（平台未接受），改为**在页面上真实操作**：
  点 sku 框 → 全选 → 输入 Fnsku → 点「提交」→ 回读列表页的平台SKU。
"""
import json
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)
from cdp_transport import CdpPage, http_targets  # noqa: E402

PORT = 9333
PEEK = r"D:\AI-workplace\TEMU\scripts\power\out\_peek"


def main():
    if len(sys.argv) < 2:
        print("用法: pw_fix_sku.py <Fnsku>")
        return
    fnsku = sys.argv[1]

    pg = CdpPage(PORT, [t for t in http_targets(PORT) if t.get("type") == "page"][0])
    cdp = pg.cdp_session()
    print("url:", pg.evaluate("() => location.href")[:110])

    # ① 点 sku 输入框
    b = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name="ck_sku[]"]');
      if (!e) return '{}';
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             old: e.value});
    }""") or "{}")
    print("① sku 框:", b)
    if not b.get("x"):
        print("   找不到")
        return
    pg.dispatch_mouse(b["x"], b["y"]); time.sleep(0.4)

    # ② 全选 + 删除 + 输入
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
                                        "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
                                        "modifiers": 2})
    time.sleep(0.2)
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    time.sleep(0.2)
    pg.dispatch_mouse(b["x"], b["y"]); time.sleep(0.2)
    cdp.send("Input.insertText", {"text": fnsku})
    time.sleep(0.5)
    print("② 填后:", pg.evaluate(r"""() => {
      const e = document.querySelector('input[name="ck_sku[]"]');
      return JSON.stringify({v: e.value, focused: document.activeElement === e});
    }"""))

    raw = pg.screenshot()
    with open(PEEK + "\\pw_edit_filled.png", "wb") as f:
        f.write(raw)
    print("   落图 pw_edit_filled.png")

    # ③ 点提交
    btn = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=submit],input[type=button]')]
        .find(e => /^\s*提交\s*$/.test((e.innerText || e.value || '').trim()));
      if (!el) return '{}';
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    print("③ 提交按钮:", btn)
    pg.dispatch_mouse(btn["x"], btn["y"])
    time.sleep(7)
    print("④ 提交后 url:", pg.evaluate("() => location.href")[:120])
    print("   页面提示:", pg.evaluate(r"""() => {
      const t = (document.body.innerText || '').replace(/\s+/g, ' ');
      const m = t.match(/(成功|失败|错误|不能为空|无效)[^，。]{0,40}/g);
      return JSON.stringify(m ? m.slice(0, 4) : t.slice(0, 120));
    }"""))
    raw = pg.screenshot()
    with open(PEEK + "\\pw_edit_submitted.png", "wb") as f:
        f.write(raw)
    print("   落图 pw_edit_submitted.png")


if __name__ == "__main__":
    main()
