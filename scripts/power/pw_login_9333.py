# -*- coding: utf-8 -*-
"""在 9333 的有头 Chrome 里登录帕沃尔（真实输入 + 提交）。账号读 scripts/pw_orders/pw_account.local.json。

用户 2026-11-07 令：「以后自己登录」。所以这个脚本是可复用的常驻能力，不是一次性探针。
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"scripts\power\lib")

from cdp_transport import CdpPage, http_targets  # noqa: E402

PORT = 9333
ACC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pw_orders",
                   "pw_account.local.json")


def main() -> int:
    acc = json.load(open(os.path.abspath(ACC), encoding="utf-8"))
    user, pwd = acc["user"], acc["password"]
    ts = [t for t in http_targets(PORT) if t.get("type") == "page"]
    if not ts:
        print("[FAIL] 9333 没有页面（先开有头 Chrome）")
        return 2
    pg = CdpPage(PORT, ts[0])
    if "login.php" not in (pg.url or ""):
        # 已登录就什么都不做
        body = pg.evaluate("() => (document.body.innerText || '').slice(0, 120)")
        if "登录" not in str(body or ""):
            print("[OK] 已登录：%s" % pg.url[:90])
            return 0
        pg.goto("https://www.powerwarehouse.net/login.php?act=login&lan=")
        time.sleep(5)
    cdp = pg.cdp_session()
    # ① 用户名：JS focus+select 再 insertText（单纯点击焦点不落进去，实测）
    pg.evaluate("() => { const e=document.querySelector('input[name=username]');"
                " e.focus(); e.select(); return 1; }")
    cdp.send("Input.insertText", {"text": user})
    time.sleep(0.3)
    # ② 密码
    pg.evaluate("() => { const e=document.querySelector('input[name=password]');"
                " e.focus(); e.select(); return 1; }")
    cdp.send("Input.insertText", {"text": pwd})
    time.sleep(0.3)
    got = pg.evaluate("() => { const u=document.querySelector('input[name=username]');"
                      " const p=document.querySelector('input[name=password]');"
                      " return {user:(u||{}).value, pwd_len:((p||{}).value||'').length}; }")
    print("回读:", {"user": (got or {}).get("user"), "pwd_len": (got or {}).get("pwd_len")})
    if (got or {}).get("user") != user or not (got or {}).get("pwd_len"):
        print("[FAIL] 账号/密码没写进输入框")
        return 42
    # ③ 提交：**踩过的坑** —— 页头有个导航链接「用户登录」(href=./user.php)，
    #    按「登录」模糊匹配会点到它，结果还是停在 login.php。真正的按钮是 `<button>立即登录</button>`。
    b = pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const cands = [...document.querySelectorAll('button,input[type=submit],input[type=button]')]
        .filter(vis).filter(e => !(e.tagName === 'A'));
      const txt = e => (e.value || e.innerText || '').replace(/\s+/g, '').trim();
      const el = cands.find(e => /立即登录|^登录$|^登陆$/.test(txt(e)))
              || cands.find(e => /登录|登陆/.test(txt(e)));
      if (!el) return null;
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
              text: txt(el), tag: el.tagName};
    }""")
    print("登录按钮:", b)
    if not b:
        print("[FAIL] 找不到登录按钮")
        return 42
    # ★ 2026-11-07 真机：鼠标点「立即登录」**不触发提交**（无 onclick，URL 不变、页面也不报错）；
    #   Enter 也无效。唯一有效的是 **JS `button.click()`**（实测跳 user.php = 登录成功）。
    print("   用 JS 点击提交按钮")
    pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const el = [...document.querySelectorAll('button,input[type=submit]')].filter(vis)
        .find(e => /立即登录|登录|登陆/.test((e.value || e.innerText || '').replace(/\s+/g, '')));
      (el || document.querySelector('button')).click();
      return 1;
    }""")
    for i in range(20):
        time.sleep(1.5)
        url = pg.url or ""
        if "login.php" not in url:
            print("[OK] 登录成功，现在在：%s" % url[:100])
            pg.goto("https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1")
            time.sleep(6)
            print("[OK] 已打开 hwc_shop 全部订单页：", (pg.url or "")[:100])
            return 0
        err = pg.evaluate("() => (document.body.innerText || '').replace(/\\s+/g,' ').slice(0,160)")
        if "错误" in str(err) or "失败" in str(err):
            print("[FAIL] 登录被拒：%s" % str(err)[:160])
            return 42
    print("[FAIL] 20 次轮询后仍停在登录页")
    return 42


if __name__ == "__main__":
    raise SystemExit(main())
