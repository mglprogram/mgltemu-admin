# -*- coding: utf-8 -*-
"""无头 Chrome 抓 www.temu.com 热销标题：临时起浏览器、用完就关。

用户口径（2026-11-28）：「当前 temu 前端找热销链接标题，改成无头打开浏览器，用完就关闭」。

⚠ **关键：必须注入 cookie，否则 100% 被踢到登录页**（2026-11-28 实测）
   全新 profile 的 Chrome（无头和有头都一样）打开搜索页会跳到
   `/login.html?from=...&login_scene=2`，抓到的只有「Continue with Google」。
   解决办法：从已登录的紫鸟店铺窗口把 `*.temu.com` 的 cookie 读出来，
   用 `Network.setCookie` 注入临时无头浏览器 —— 之后页面正常渲染（imgs 从 43 → 137）。

设计要点：
  · 不碰紫鸟窗口的页面，只借 cookie（读一次 getAllCookies）
  · 每次用**独立临时 user-data-dir**，用完 `kill` + `rmtree`，不留痕迹
  · `--headless=new`（老版 --headless 拿不到布局尺寸，取不到 Sort by 坐标）
  · 异常路径也保证关进程、删 profile

用法：
    python scripts/goods/scrape_temu_headless.py --q "9pcs hoodies" --scroll 6
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

#: 标题在 img[alt]，剥掉 "item picture" 之类前缀（与 scrape_temu_www 保持一致）
EXTRACT_JS = r"""(function(){
  var out=[], seen={};
  var imgs=document.querySelectorAll('img[alt]');
  for(var i=0;i<imgs.length;i++){
    var alt=imgs[i].getAttribute('alt')||'';
    var t=alt.replace(/^\s*(item\s+picture|image|img)\s*/i,'').trim();
    if(t.length<12){continue;}
    var a=imgs[i].closest('a');
    var href=a?(a.getAttribute('href')||''):'';
    var m=href.match(/-g-(\d+)/);
    var gid=m?m[1]:('n'+i);
    if(seen[gid]){continue;}
    seen[gid]=1;
    var card=imgs[i].closest('[data-testid],div');
    var box=card?card.parentElement||card:null;
    var txt=box?((box.innerText||'').replace(/\s+/g,' ')):'';
    out.push({
      gid:gid,
      title:t,
      href:href,
      sold:(txt.match(/([\d.,]+)\s*sold/i)||[])[0]||'',
      price:(txt.match(/\$\s?[\d.,]+/)||[])[0]||''
    });
  }
  return JSON.stringify(out);
})()"""

SORT_BTN = r"""(function(){
  var bs=document.querySelectorAll('button,div,span,a');
  for(var i=0;i<bs.length;i++){
    var t=(bs[i].innerText||'').trim();
    if(/^Sort by/i.test(t)&&bs[i].children.length<=3){
      var r=bs[i].getBoundingClientRect();
      if(r.width>10){return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}
    }
  }
  return JSON.stringify({ok:false});
})()"""

TOP_SALES = r"""(function(){
  var bs=document.querySelectorAll('div,span,li,a');
  for(var i=0;i<bs.length;i++){
    var t=(bs[i].innerText||'').trim();
    if(/^Top sales$/i.test(t)){
      var r=bs[i].getBoundingClientRect();
      if(r.width>10&&r.height>5){return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}
    }
  }
  return JSON.stringify({ok:false});
})()"""


def find_chrome() -> str:
    for p in CHROME_CANDIDATES:
        if p and Path(p).is_file():
            return p
    raise RuntimeError("找不到 Chrome / Edge，无法起无头浏览器")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_devtools(port: int, timeout: float = 25.0) -> dict:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            with urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.3)
    raise RuntimeError("无头浏览器没起来（端口 %d）：%s" % (port, last))


def _to_cdp_cookie(c: dict) -> dict:
    out = {"name": c["name"], "value": c["value"],
           "domain": c["domain"], "path": c.get("path") or "/"}
    for k in ("httpOnly", "secure", "sameSite"):
        if c.get(k) not in (None, ""):
            out[k] = c[k]
    exp = c.get("expires")
    if isinstance(exp, (int, float)) and exp > 0:
        out["expires"] = exp
    return out


async def fetch_temu_cookies(src_port: int) -> list[dict]:
    """从紫鸟店铺窗口读 `*.temu.com` 的 cookie（只读，不动它的页面）。"""
    import urllib.request
    import websockets

    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % src_port,
                               timeout=8) as r:
        targets = json.loads(r.read().decode("utf-8", "replace"))
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("店铺窗口没有可读 cookie 的页面（端口 %d）" % src_port)
    async with websockets.connect(pages[0]["webSocketDebuggerUrl"],
                                  max_size=64 * 1024 * 1024,
                                  ping_interval=None, ping_timeout=None) as w:
        await w.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await w.send(json.dumps({"id": 2, "method": "Network.getAllCookies"}))
        while True:
            m = json.loads(await asyncio.wait_for(w.recv(), 25.0))
            if m.get("id") == 2:
                cookies = (m.get("result") or {}).get("cookies") or []
                return [c for c in cookies if "temu.com" in str(c.get("domain") or "")]


class HeadlessBrowser:
    """临时无头浏览器上下文管理器：进去起浏览器，出来关进程。

    两种 profile 策略（2026-11-28 实测后加的）：
      · `persist=True`（默认）—— 用 `data/temu-www-profile/` **固定持久 profile**。
        TEMU 搜前台的登录墙靠 cookie 顶不住（注入了照样被踢 login.html），
        但 profile 里存着完整的 localStorage / IndexedDB / service worker 状态，
        实测能直接过。**这个 profile 需要人工登录一次**（见 login_profile()）。
      · `persist=False` —— 用一次性 temp profile，用完删。干净但一定被踢去登录页，
        只适合抓完全公开的页面。
    """

    PROFILE_DIR = ROOT / "data" / "temu-www-profile"

    def __init__(self, log=print, width: int = 1440, height: int = 1000,
                 persist: bool = True, clear_profile: bool = False):
        self.log = log
        self.width, self.height = width, height
        self.persist = persist
        self.clear_profile = clear_profile
        self.proc: subprocess.Popen | None = None
        self.profile = ""
        self.port = 0
        self._temp_profile = False

    def _profile_dir(self) -> str:
        if not self.persist:
            self._temp_profile = True
            return tempfile.mkdtemp(prefix="temu_headless_")
        d = Path(self.PROFILE_DIR)
        if self.clear_profile and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            self.log("  已清空持久 profile")
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def __enter__(self) -> "HeadlessBrowser":
        chrome = find_chrome()
        self.port = _free_port()
        self.profile = self._profile_dir()
        args = [
            chrome,
            "--headless=new",
            "--remote-debugging-port=%d" % self.port,
            "--user-data-dir=%s" % self.profile,
            "--window-size=%d,%d" % (self.width, self.height),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-notifications",
            "--mute-audio",
            "--hide-scrollbars",
            "--disable-blink-features=AutomationControlled",
            "--lang=en-US",
            "about:blank",
        ]
        self.log("  起无头浏览器 port=%d profile=%s" % (
            self.port, "持久" if self.persist else "临时"))
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _wait_devtools(self.port)
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
            self.log("  已关闭无头浏览器")
        # 持久 profile 不能删；只有临时 profile 才 rmtree
        if self.profile and self._temp_profile:
            for _ in range(6):
                try:
                    shutil.rmtree(self.profile)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.3)
            else:
                shutil.rmtree(self.profile, ignore_errors=True)
        self.profile = ""
        return False

    def page_ws(self) -> str:
        with urlopen("http://127.0.0.1:%d/json/list" % self.port, timeout=8) as r:
            targets = json.loads(r.read().decode("utf-8", "replace"))
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("无头浏览器没有可用页面")
        return pages[0]["webSocketDebuggerUrl"]


async def scrape_async(query: str, scroll: int = 6, do_sort: bool = True,
                       log=print, cookie_port: int = 0,
                       browser: HeadlessBrowser | None = None,
                       persist: bool = True) -> dict:
    """抓一个关键词的 Top sales 标题。

    persist=True 用固定持久 profile（需先 login_profile() 登录一次），
    否则每次都是干净临时 profile —— 后者 100% 被 TEMU 踢去登录页。
    cookie_port 仅作兼容保留：实测注入 cookie 顶不过登录墙，真正管用的是 profile。
    """
    from scrape_temu_www import Sess  # 复用现有 CDP 会话封装

    cookies = await fetch_temu_cookies(cookie_port) if cookie_port else []

    own = browser is None
    b = browser or HeadlessBrowser(log=log, persist=persist)
    if own:
        b.__enter__()
    try:
        ws = b.page_ws()
        url = ("https://www.temu.com/search_result.html?search_key=%s&search_method=user"
               % quote(query))
        async with Sess(ws) as s:
            await s.call("Page.enable")
            await s.call("Network.enable")
            if cookies:
                ok = 0
                for c in cookies:
                    r = await s.call("Network.setCookie", _to_cdp_cookie(c), 10)
                    if (r or {}).get("success"):
                        ok += 1
                log("  注入 cookie %d/%d（可能不够，见模块注释）" % (ok, len(cookies)))
            log("  导航 → %s" % query)
            await s.call("Page.navigate", {"url": url}, 40)
            await asyncio.sleep(9)

            cur = str(await s.ev("location.href") or "")
            if "/login" in cur:
                raise RuntimeError(
                    "被踢到登录页。先跑一次登录："
                    "python scripts/goods/scrape_temu_headless.py --login"
                    "\n当前 URL: %s" % cur[:150])

            if do_sort:
                btn = json.loads(await s.ev(SORT_BTN) or "{}")
                if btn.get("ok"):
                    await s.click(btn["x"], btn["y"])
                    await asyncio.sleep(1.8)
                    ts = json.loads(await s.ev(TOP_SALES) or "{}")
                    if ts.get("ok"):
                        await s.click(ts["x"], ts["y"])
                        await asyncio.sleep(6)
                        log("  已切 Top sales")
                    else:
                        log("  !! 没找到 Top sales")
                else:
                    log("  !! 没找到 Sort by")

            for _ in range(scroll):
                await s.wheel(2400)
                await asyncio.sleep(1.8)

            raw = await s.ev(EXTRACT_JS)
            try:
                items = json.loads(raw or "[]")
            except Exception:
                items = []
            final_url = await s.ev("location.href")
    finally:
        if own:
            b.__exit__(None, None, None)

    clean = []
    for it in items:
        t = str(it.get("title") or "").strip()
        if len(t) < 20 or ".png" in t.lower() or "card" in t.lower():
            continue
        it["title"] = t
        clean.append(it)
    return {"query": query, "url": final_url, "items": clean,
            "headless": True, "cookies": len(cookies)}


def scrape(query: str, scroll: int = 6, do_sort: bool = True, log=print,
           cookie_port: int = 0, persist: bool = True) -> dict:
    return asyncio.run(scrape_async(query, scroll, do_sort, log, cookie_port,
                                    persist=persist))


def login_profile(url: str = "https://www.temu.com/") -> int:
    """**有头**打开持久 profile，让用户手工登录一次；关掉窗口后无头浏览器复用这份 profile。

    用户 2026-11-28：「实在不行要不我给你登录一次谷歌账号」—— 就是这个入口。
    无头浏览器没法交互，所以必须先用有头模式登进去，profile 才能存下完整会话。
    """
    chrome = find_chrome()
    profile = Path(HeadlessBrowser.PROFILE_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    print("用持久 profile 打开有头 Chrome：%s" % profile)
    print("  1) 在弹出的窗口里登录 TEMU（Google / 邮箱 / 手机号都行）")
    print("  2) 登录成功、能看到商品页后，**直接关掉这个 Chrome 窗口**")
    print("  3) 之后再跑抓取就会自动复用，不用再登")
    print("  打开：%s" % url)
    args = [
        chrome,
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--lang=en-US",
        url,
    ]
    proc = subprocess.Popen(args)
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    print("窗口已关闭。现在可以：python scripts/goods/scrape_temu_headless.py --q \"9pcs hoodies\"")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="无头浏览器抓 temu 热销标题")
    ap.add_argument("--q", default="", help="搜索关键词")
    ap.add_argument("--scroll", type=int, default=6)
    ap.add_argument("--no-sort", dest="do_sort", action="store_false", default=True)
    ap.add_argument("--cookie-port", type=int, default=0,
                    help="（兼容保留）从该 CDP 端口读 temu.com cookie")
    ap.add_argument("--store", default="", help="按店铺名读 cookie（兼容保留）")
    ap.add_argument("--login", action="store_true",
                    help="有头打开持久 profile 让你手工登录一次")
    ap.add_argument("--fresh-profile", action="store_true",
                    help="清空持久 profile 重来（会丢登录态）")
    ap.add_argument("--temp-profile", action="store_true",
                    help="用一次性临时 profile（一定被踢登录页，仅供排查）")
    a = ap.parse_args()

    if a.login:
        return login_profile()
    if not a.q:
        ap.error("要么给 --q 抓取，要么给 --login 登录")

    cport = a.cookie_port
    if not cport and a.store:
        import temu_publish_api as api
        cport = api.resolve_port(a.store, 0)

    r = scrape(a.q, a.scroll, a.do_sort, cookie_port=cport,
               persist=not a.temp_profile)
    print("\n== query=%r ==" % a.q)
    print("抓到 %d 条（%s）\n" % (
        len(r["items"]), "Top sales" if "1%3A1" in str(r["url"]) else str(r["url"])[-50:]))
    for i, it in enumerate(r["items"], 1):
        print("%2d. %s" % (i, it["title"][:115]))
    outdir = ROOT / "out" / "temu-www-scrape"
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / ("headless_%s_%s.json" % (re.sub(r"[^a-z0-9]+", "_", a.q.lower()),
                                            time.strftime("%Y%m%d-%H%M%S")))
    p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘", p)
    return 0 if r["items"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
