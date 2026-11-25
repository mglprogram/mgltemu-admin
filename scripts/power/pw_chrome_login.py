# -*- coding: utf-8 -*-
"""在调试 Chrome（端口 9222）里自动登录帕沃尔并打开发货页，然后监听用户操作。

凭据从命令行传入，不落盘。
"""
import asyncio
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

PORT = 9222
USER = sys.argv[1] if len(sys.argv) > 1 else ""
PWD = sys.argv[2] if len(sys.argv) > 2 else ""
SHOP = sys.argv[3] if len(sys.argv) > 3 else "14450"

JS_LOGIN = r"""(() => {
  const u = document.querySelector('input[name=username],input[name=user],input[name=account],#username,#user');
  const p = document.querySelector('input[name=password],input[name=pwd],#password,#pwd');
  const f = document.forms[0] || (u ? u.form : null);
  const out = {foundUser: !!u, foundPwd: !!p, hasForm: !!f,
               inputs: [...document.querySelectorAll('input')].map(i => (i.name||i.id||i.type))};
  if (u && p && f) {
    u.value = %s; p.value = %s;
    f.submit();
    out.submitted = true;
  }
  return JSON.stringify(out);
})()"""


async def main():
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % PORT, timeout=10).read())
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/list" % PORT, timeout=10).read())
    login_tab = next((t for t in tabs if "login.php" in str(t.get("url") or "")), None)
    if not login_tab:
        print("没有登录页，当前页：", [str(t.get("url"))[:60] for t in tabs if t.get("type") == "page"])
        return 1
    ws_url = login_tab["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        mid = [0]

        async def ev(expr):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate", "params": {
                "expression": expr, "awaitPromise": True, "returnByValue": True,
                "userGesture": True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    r = m.get("result") or {}
                    if r.get("exceptionDetails"):
                        return "EXC " + str(r["exceptionDetails"])[:200]
                    return (r.get("result") or {}).get("value")

        # 先看表单结构
        info = await ev("(() => JSON.stringify({inputs:[...document.querySelectorAll('input')]"
                        ".map(i=>({n:i.name,id:i.id,t:i.type}))}))()")
        print("登录页输入框:", info)
        if not USER:
            print("需要账号密码：python %s <账号> <密码> [店铺号]" % __file__)
            return 0
        js = JS_LOGIN % (json.dumps(USER), json.dumps(PWD))
        print("提交登录:", await ev(js))
        await asyncio.sleep(8)
        print("登录后 URL:", await ev("(() => location.href)()"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
