# -*- coding: utf-8 -*-
"""lib/pw_browser.py — 帕沃尔专用**无头** Chrome 的启动/复用（独立 profile）。

★ 2026-11-07 用户口径：帕沃尔仓的发货改成**无头**；脚本**自己起**无头 Chrome
  （独立 profile），首次没登录用账号密码自动登录（凭据在
  `scripts/pw_orders/pw_account.local.json`，gitignored）。

为什么要独立 profile（不能挂紫鸟、也不能挂你的日常 Chrome profile）：
  · 紫鸟店铺窗口是**有头**的、且 CDP 端口各店不同；帕沃尔是独立站点，
    不需要挂紫鸟也不会和 TEMU 抢窗口；
  · `--headless=new` 下同一个 profile 不能被两个进程同时占用，
    用固定独立目录才能「起一次、后面复用」。

对外就三个函数：
    ensure(port=9333, log=print)   → 确保端口上有可用 page，返回 CdpPage
    ensure_logged_in(pg, log=print) → 已登录返回 True；没登录就用账密登录
    close(port=9333)                → 关掉自己起的那个无头实例（可选）

⚠ 无头模式踩过的坑（已在 ensure 里处理）：
  1. `--headless=new` 必须配 `--remote-debugging-port` 才有 CDP；
  2. 新版 Chrome 要求 `--user-data-dir` **不能是默认目录**，否则直接拒绝起调试端口；
  3. 起完端口不是立刻可用，要轮询 `/json/version`；
  4. 无头下没有窗口焦点，`Input.insertText` 仍可用（pw_login 已验证），
     但**坐标点击前必须 `scrollIntoView`**，否则点在视口外静默无效。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from cdp_transport import CdpPage, create_target, http_targets  # noqa: E402

PORT = 9333
HOME = "https://www.powerwarehouse.net/"
HWC_URL = HOME + "hwc_shop.php?act=shop_order&pb=1&uselastfilter=1"

#: 独立 profile（gitignored；首次登录后 cookie 常驻）
PROFILE = os.path.join(os.path.dirname(_HERE), "..", "..", "scripts", "pw_orders",
                       ".chrome-headless-profile")
_PROFILE = os.path.abspath(PROFILE)


def _chrome_exe() -> str:
    """找 Chrome / Edge 可执行文件（允许用 PW_CHROME 指定）。"""
    env = os.environ.get("PW_CHROME")
    if env and os.path.exists(env):
        return env
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return ""


def port_alive(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port,
                                    timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def launch(port: int = PORT, log=print) -> bool:
    """起无头 Chrome（独立 profile）。已活着就直接返回 True。"""
    if port_alive(port):
        log("   [无头] 端口 %d 已有实例，复用" % port)
        return True
    exe = _chrome_exe()
    if not exe:
        log("   [无头] [FAIL] 找不到 chrome.exe（可用 PW_CHROME 指定路径）")
        return False
    os.makedirs(_PROFILE, exist_ok=True)
    args = [
        exe,
        "--headless=new",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % _PROFILE,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--window-size=1440,900",
        "about:blank",
    ]
    log("   [无头] 启动 %s（port %d）" % (os.path.basename(exe), port))
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as e:  # noqa: BLE001
        log("   [无头] [FAIL] 启动失败：%s" % str(e)[:160])
        return False
    for _ in range(30):
        time.sleep(0.5)
        if port_alive(port):
            log("   [无头] 端口 %d 就绪" % port)
            return True
    log("   [无头] [FAIL] 15s 内端口没起来")
    return False


def ensure(port: int = PORT, url: str = HWC_URL, log=print):
    """确保有无头实例 + 一个可用 page，返回 (CdpPage, 是否本函数新起的 page)。"""
    if not launch(port, log=log):
        return None, False
    ts = [t for t in http_targets(port) if t.get("type") == "page"
          and "powerwarehouse" in (t.get("url") or "")]
    if ts:
        return CdpPage(port, ts[0]), False
    # 没有帕沃尔页 ⇒ 自己开一个（原生 CDP createTarget，不抢前台）
    try:
        ts = [t for t in http_targets(port) if t.get("type") == "page"]
        if ts:
            pg = CdpPage(port, ts[0])
            pg.goto(url)
            time.sleep(5)
            return pg, True
        tid = create_target(port, url, background=True)
        deadline = time.time() + 12
        while time.time() < deadline:
            for t in http_targets(port):
                if (t.get("id") or t.get("targetId")) == tid:
                    return CdpPage(port, t), True
            time.sleep(0.3)
        log("   [无头] [FAIL] 新开页没出现（tid=%s）" % tid)
    except Exception as e:  # noqa: BLE001
        log("   [无头] [FAIL] 开页异常：%s" % str(e)[:160])
    return None, False


def is_logged_in(pg) -> bool:
    """登录判定：URL 不在 login.php，且页面里没有登录表单。"""
    try:
        if "login.php" in (pg.url or ""):
            return False
        got = pg.evaluate(
            "() => !!document.querySelector('input[name=username]')")
        return not got
    except Exception:  # noqa: BLE001
        return False


def ensure_logged_in(pg, log=print) -> bool:
    """没登录就用账号密码自动登录（复用 pw_login_9333 的实机逻辑）。"""
    if is_logged_in(pg):
        return True
    acc = os.path.join(os.path.dirname(_HERE), "..", "pw_orders", "pw_account.local.json")
    acc = os.path.abspath(acc)
    if not os.path.exists(acc):
        log("   [登录] [FAIL] 没有凭据文件 %s" % acc)
        return False
    d = json.load(open(acc, encoding="utf-8"))
    user, pwd = d["user"], d["password"]
    log("   [登录] 无头实例未登录 → 自动登录（%s）" % user)
    pg.goto("https://www.powerwarehouse.net/login.php?act=login&lan=")
    time.sleep(4)
    cdp = pg.cdp_session()
    pg.evaluate("() => { const e=document.querySelector('input[name=username]');"
                " if(e){e.focus();e.select();} return 1; }")
    cdp.send("Input.insertText", {"text": user})
    time.sleep(0.3)
    pg.evaluate("() => { const e=document.querySelector('input[name=password]');"
                " if(e){e.focus();e.select();} return 1; }")
    cdp.send("Input.insertText", {"text": pwd})
    time.sleep(0.3)
    got = pg.evaluate("() => { const u=document.querySelector('input[name=username]');"
                      " const p=document.querySelector('input[name=password]');"
                      " return {user:(u||{}).value, len:((p||{}).value||'').length}; }")
    if (got or {}).get("user") != user or not (got or {}).get("len"):
        log("   [登录] [FAIL] 账号/密码没写进输入框：%s" % got)
        return False
    # ★ 踩过的坑：鼠标点「立即登录」不触发提交，Enter 也无效；只有 JS click() 有效。
    pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0; };
      const el = [...document.querySelectorAll('button,input[type=submit]')].filter(vis)
        .find(e => /立即登录|登录|登陆/.test((e.value||e.innerText||'').replace(/\s+/g,'')));
      (el || document.querySelector('button')).click();
      return 1;
    }""")
    for _ in range(20):
        time.sleep(1.5)
        if "login.php" not in (pg.url or ""):
            log("   [登录] ✅ 成功：%s" % (pg.url or "")[:90])
            return True
    log("   [登录] [FAIL] 20 次轮询后仍停在登录页")
    return False


def close(port: int = PORT, log=print) -> None:
    """关掉无头实例（需要时用；正常跑完**不关**，让登录态留在 profile 里）。"""
    try:
        import urllib.request  # noqa: PLC0415
        urllib.request.urlopen("http://127.0.0.1:%d/json/close" % port, timeout=2)
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(["taskkill", "/F", "/IM", "chrome.exe",
                              "/FI", "COMMANDLINE eq *--headless=new*"],
                             capture_output=True, text=True, timeout=10)
        log("   [无头] 关闭：%s" % (out.stdout or out.stderr or "")[:80])
    except Exception:  # noqa: BLE001
        pass


def clean_profile(log=print) -> None:
    """profile 坏了（起不来/一直未登录）时清空重来。调用前必须先 close()。"""
    try:
        shutil.rmtree(_PROFILE, ignore_errors=True)
        log("   [无头] 已清空 profile：%s" % _PROFILE)
    except Exception as e:  # noqa: BLE001
        log("   [无头] 清 profile 失败：%s" % str(e)[:120])
