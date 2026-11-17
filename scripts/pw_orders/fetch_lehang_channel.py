# -*- coding: utf-8 -*-
"""从乐航后台「运单」页读出每票的**服务/渠道**（以星卡派-包税 这类）。

为什么走浏览器而不是接口
------------------------
乐航是 ParcelOS（umi + pod 配置化框架）。运单列表的真实接口
`POST /rest/tms/wos/shipment/lists` 我试过十几种 body 形状（page/pageSize /
current+size / search / filters / data 包装 …），**全部 500**，
连空 body 也 500 —— 说明它要的入参结构来自服务端下发的 pod 配置，
不是能猜出来的。而 `tracking/app?inajax=1` 那个公开查件口**不返回服务字段**，
只给 shipment_id/parcels/traces。

所以最终手段：用已有 headless Chrome（CDP）打开乐航运单页，
按单号搜出来，直接读表格 DOM 里的「服务」列。

用法：
    python scripts/pw_orders/fetch_lehang_channel.py --lh LH10058731
    python scripts/pw_orders/fetch_lehang_channel.py --refs 144503040,144503037
    python scripts/pw_orders/fetch_lehang_channel.py --all      # 从 shipment-track.json 取全部 LH 号

产物：out/lehang/channel_map.json  {"LH10058731": "以星卡派-包税", ...}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "lehang" / "channel_map.json"
TRACK = ROOT / "temu-admin" / "public" / "data" / "shipment-track.json"
BASE = "http://lehang.nextsls.com"
LOGIN = BASE + "/rest/tms/wos/auth/login"
SHIP_URL = BASE + "/tms/wos/shipment?page=1&pageSize=30&activeTab=all&keywords={no}"


# --------------------------------------------------------------------- CDP
class CDP:
    def __init__(self, port: int = 9333):
        self.port = port
        self.ws = None
        self.tid = 0

    def _http(self, path: str):
        return json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=15).read())

    def new_tab(self, url: str) -> str:
        # ⚠ CDP 的 /json/new 只接受 **PUT**（GET 回 405 Method Not Allowed）
        url = urllib.parse.quote(url, safe=":/?&=%")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?{url}",
                                     method="PUT")
        return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("id", "")

    def attach(self, target_id: str):
        import websocket  # type: ignore
        info = None
        for t in self._http("/json/list"):
            if t.get("id") == target_id:
                info = t
                break
        if not info:
            raise RuntimeError("target 不见了")
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"],
                                              timeout=45, suppress_origin=True)
        return self

    def call(self, method: str, params: dict | None = None):
        self.tid += 1
        self.ws.send(json.dumps({"id": self.tid, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.tid:
                return msg

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        return (r.get("result", {}).get("result") or {}).get("value")

    def close(self):
        try:
            self.ws and self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def login_token() -> str:
    """拿一个 API token（给 ensure_login 兜底写 localStorage 用）。"""
    body = json.dumps({"username": "博盈通豪", "password": "123456"}).encode()
    req = urllib.request.Request(LOGIN, data=body, headers={
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": "Mozilla/5.0",
    })
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    j = json.loads(op.open(req, timeout=30).read().decode())
    return j["data"]["token"]


def ensure_login(c: CDP, token: str) -> None:
    """在浏览器里真实登录一次（不只是写 localStorage）。

    ⚠ 踩过的坑：光 `localStorage.setItem('token', …)` 不够 ——
    乐航后端靠 **cookie `os_wos` / `_identity-os_wos`** 判登录态，
    只塞 token 会被 `redirect_url` 弹回 /tms/wos/login。
    所以这里直接在登录页里点输入框、点登录按钮走一遍真实表单，
    让服务端把 cookie 种上；之后跳运单页才不会被打回。
    """
    c.js("location.href='%s/tms/wos/login'" % BASE)
    time.sleep(2)
    # 已登录则直接返回
    if c.js("!location.pathname.includes('/login')"):
        return
    c.js("""
    (() => {
      const ins = [...document.querySelectorAll('input')];
      const set = (el, v) => {
        if (!el) return;
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
      };
      set(ins[0], '博盈通豪');
      set(ins[1], '123456');
      return ins.length;
    })()
    """)
    time.sleep(0.4)
    c.js("""
    (() => {
      const btns = [...document.querySelectorAll('button')];
      const b = btns.find(x => (x.innerText || '').replace(/\\s/g,'').includes('登录'));
      if (b) { b.click(); return b.innerText.trim(); }
      const form = document.querySelector('form');
      if (form) { form.submit(); return 'form.submit'; }
      return '';
    })()
    """)
    time.sleep(4)

    body = json.dumps({"username": "博盈通豪", "password": "123456"}).encode()
    req = urllib.request.Request(LOGIN, data=body, headers={
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": "Mozilla/5.0",
    })
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    j = json.loads(op.open(req, timeout=30).read().decode())
    return j["data"]["token"]


def read_service(c: CDP, no: str) -> str:
    """在已打开的运单页里按单号搜，读「服务」列。

    ⚠ DOM 结构（2026-11-17 实测）：乐航运单页是 **ant-design Table**，
    表头 `th` 里「运单号」是第 2 列（第 1 列是勾选框，表头文字为空），
    「服务」紧随其后。所以不能靠 `tbody tr` 遍历猜，直接
    「找表头里『服务』的下标 → 取该行的第 i 个 td」最稳。
    """
    c.js("location.href='%s'" % SHIP_URL.format(no=no))
    time.sleep(2)
    # 等结果渲染（最多 25 秒）：表格行出现且正文含这个单号
    for _ in range(25):
        ok = c.js("(() => {const b=document.body.innerText||'';"
                  "return b.includes('%s') && "
                  "document.querySelectorAll('.ant-table-tbody tr').length>0;})()" % no)
        if ok:
            break
        time.sleep(1)
    return c.js("""
    (() => {
      const heads = [...document.querySelectorAll('.ant-table-thead th')]
        .map(t => (t.innerText || '').trim());
      const si = heads.findIndex(h => h.includes('服务'));
      if (si < 0) return '';
      const rows = [...document.querySelectorAll('.ant-table-tbody tr')];
      for (const tr of rows) {
        const txt = tr.innerText || '';
        if (!txt.includes('%s')) continue;
        const tds = [...tr.querySelectorAll('td')];
        return (tds[si]?.innerText || '').split('\\n')[0].trim();
      }
      return '';
    })()
    """ % no)


def main() -> int:
    ap = argparse.ArgumentParser(description="乐航运单服务/渠道抓取")
    ap.add_argument("--lh", default="", help="单个 LH 运单号")
    ap.add_argument("--refs", default="", help="逗号分隔的帕沃尔参考单号")
    ap.add_argument("--all", action="store_true", help="从 shipment-track.json 取全部 LH 号")
    a = ap.parse_args()

    targets: list[str] = []
    if a.lh:
        targets = [a.lh.strip()]
    elif a.all:
        d = json.loads(TRACK.read_text(encoding="utf-8"))
        for r in d["rows"]:
            sid = str(r["logistics"].get("shipmentId") or "")
            if sid.startswith("LH") and sid not in targets:
                targets.append(sid)
    elif a.refs:
        d = json.loads(TRACK.read_text(encoding="utf-8"))
        want = {x.strip() for x in a.refs.split(",") if x.strip()}
        for r in d["rows"]:
            if r["inboundNo"] in want:
                sid = str(r["logistics"].get("shipmentId") or "")
                if sid.startswith("LH"):
                    targets.append(sid)
    if not targets:
        print("没有目标单号，用 --lh / --refs / --all")
        return 2
    print(f"目标 {len(targets)} 个运单")

    token = login_token()
    print(f"token {token[:8]}…")

    # 复用现成 headless chrome：新开一个标签，真实登录后进运单页
    cdp = CDP(9333)
    tid = cdp.new_tab(BASE + "/tms/wos/login")
    c = cdp.attach(tid)
    c.call("Runtime.enable")
    ensure_login(c, token)
    if c.js("location.pathname.includes('/login')"):
        print("  ! 还在登录页，后续很可能读不到数据")
    else:
        print("  登录成功")

    out: dict[str, str] = {}
    for i, no in enumerate(targets, 1):
        try:
            svc = read_service(c, no)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {no} 失败：{str(e)[:60]}")
            svc = ""
        if svc:
            out[no] = svc
        print(f"  {i}/{len(targets)} {no} -> {svc or '（空）'}")

    c.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT}（{len(out)}/{len(targets)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
