# -*- coding: utf-8 -*-
"""lib/pw_browser.py — 帕沃尔专用 Chrome 的启动/复用（独立 profile）。

★ 2026-11-09 用户令：「把帕沃尔仓发货改成**有头**」
  ⇒ 脚本自己起的 Chrome **默认有窗口**（能人眼核步骤顺序）。
  要无头：`PW_HEADED=0`（无人值守 / 不想抢焦点时用）。
  前身是 2026-11-07 的口径「改成无头」——那天是为了无人值守跑通；
  现在反过来了，因为无头下点错、点在视口外、弹窗没等到全都看不出来。

为什么要独立 profile（不能挂紫鸟、也不能挂你的日常 Chrome profile）：
  · 紫鸟店铺窗口各店端口不同，帕沃尔是独立站点，不需要挂紫鸟；
  · 同一个 profile 不能被两个 Chrome 进程同时占用，
    用固定独立目录才能「起一次、后面复用」。

对外就三个函数：
    ensure(port=9333, log=print)   → 确保端口上有可用 page，返回 CdpPage
    ensure_logged_in(pg, log=print) → 已登录返回 True；没登录就用账密登录
    close(port=9333)                → 关掉自己起的那个实例（可选）

⚠ 无头模式踩过的坑（`PW_HEADED=0` 时仍要记）：
  1. `--headless=new` 必须配 `--remote-debugging-port` 才有 CDP；
  2. 新版 Chrome 要求 `--user-data-dir` **不能是默认目录**，否则直接拒绝起调试端口；
  3. 起完端口不是立刻可用，要轮询 `/json/version`；
  4. 无头下没有窗口焦点，`Input.insertText` 仍可用（pw_login 已验证），
     但**坐标点击前必须 `scrollIntoView`**，否则点在视口外静默无效。
     有头模式同样要 `scrollIntoView`（窗口可能被其他窗口遮住 / 页面滚到顶）。
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

#: 独立 profile（gitignored；首次登录后 cookie 常驻）。
#: ★ 2026-11-13 用户令「五个店铺并行发货」⇒ **每个端口一个 profile**：
#:   同一个 `--user-data-dir` 不能被两个 Chrome 进程同时占用（第二个直接起不来调试端口），
#:   所以要按端口分目录。9333（默认端口）沿用老目录，避免迁移 cookie 要重新登录。
PROFILE = os.path.join(os.path.dirname(_HERE), "..", "..", "scripts", "pw_orders",
                       ".chrome-headless-profile")
_PROFILE = os.path.abspath(PROFILE)


def profile_dir(port: int = PORT) -> str:
    """端口 → 独立 profile 目录（9333 用老目录，其它端口各一份）。"""
    if int(port or PORT) == PORT:
        return _PROFILE
    return os.path.abspath("%s-%d" % (_PROFILE, int(port)))


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
    """起 Chrome（**每端口一份独立 profile**；默认**无头**，`PW_HEADED=1` 才有头）。

    ★ 2026-11-13 用户令：「五个店铺的帕沃尔发货改成**无头**，并且**并行**（不是串行）」
      ⇒ ① 默认翻成无头（之前 11-09 为了人眼核步骤临时改的有头）；
         ② profile 按端口隔离（见 `profile_dir`），否则第二个店起不来；
         ③ 无头下所有坐标点击前必须 `scrollIntoView`（`pw_hwc_flow` 已统一处理），
            失败要留截图 —— 无头出错时没有窗口可看。

    `PW_HEADED=1` 仍可回有头（人肉核对时用）。
    """
    headed = str(os.environ.get("PW_HEADED") or "0").strip().lower() in ("1", "true", "yes")
    tag = "有头" if headed else "无头"
    prof = profile_dir(port)
    if port_alive(port):
        if headed and _headless_on(port):
            log("   [%s] 端口 %d 上是**无头**实例 → 关掉重开有头" % (tag, port))
            close(port, log=log)
            for _ in range(20):
                if not port_alive(port):
                    break
                time.sleep(0.5)
        elif not headed and not _headless_on(port):
            log("   [%s] 端口 %d 上是**有头**实例 → 关掉重开无头" % (tag, port))
            close(port, log=log)
            for _ in range(20):
                if not port_alive(port):
                    break
                time.sleep(0.5)
        else:
            log("   [%s] 端口 %d 已有实例，复用（profile=%s）"
                % (tag, port, os.path.basename(prof)))
            return True
    exe = _chrome_exe()
    if not exe:
        log("   [%s] [FAIL] 找不到 chrome.exe（可用 PW_CHROME 指定路径）" % tag)
        return False
    os.makedirs(prof, exist_ok=True)
    # ★ 2026-11-09 用户令「帕沃尔仓发货改成有头」：默认起**有头**窗口，
    #   便于人眼核步骤顺序（无头下点错/空点看不出来）。
    #   要回到无头：`PW_HEADED=0`（定时任务无人值守时可这么设）。
    args = [
        exe,
    ]
    if not headed:
        args.append("--headless=new")
    args += [
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % prof,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--window-size=1440,900",
        "about:blank",
    ]
    log("   [%s] 启动 %s（port %d）" % (tag, os.path.basename(exe), port))
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as e:  # noqa: BLE001
        log("   [%s] [FAIL] 启动失败：%s" % (tag, str(e)[:160]))
        return False
    for _ in range(30):
        time.sleep(0.5)
        if port_alive(port):
            log("   [%s] 端口 %d 就绪" % (tag, port))
            return True
    log("   [%s] [FAIL] 15s 内端口没起来" % tag)
    return False


def _headless_on(port: int = PORT) -> bool:
    """端口上的 Chrome **主进程**是不是无头实例。

    ⚠ 只认主进程（命令行里没有 `--type=`）：
      renderer / gpu 子进程也会带 `--remote-debugging-port` 之外的 profile 参数，
      但它们**不带** `--headless=new`，所以原实现「取第一个匹配」一旦先命中
      子进程，就会把无头实例误判成有头，进而跳过「关掉重开」这一步
      （2026-11-09 实测：窗口已经是无头，这里却返回 False）。
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*remote-debugging-port=%d*' "
             "-and $_.CommandLine -notlike '*--type=*' } | "
             "Select-Object -First 1 -ExpandProperty CommandLine" % port],
            capture_output=True, text=True, timeout=20)
        return "--headless" in (out.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def ensure(port: int = PORT, url: str = HWC_URL, log=print):
    """确保有实例 + 一个可用 page，返回 (CdpPage, 是否本函数新起的 page)。

    实例模式由 `launch()` 决定：**默认有头**，`PW_HEADED=0` 才无头。
    """
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
        log("   [PW] [FAIL] 新开页没出现（tid=%s）" % tid)
    except Exception as e:  # noqa: BLE001
        log("   [PW] [FAIL] 开页异常：%s" % str(e)[:160])
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
    log("   [登录] 未登录 → 自动登录（%s）" % user)
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


def _pids_on_port(port: int = PORT) -> list:
    """找占用这个调试端口的 Chrome **主进程** PID 列表（按命令行筛）。

    为什么不用 `taskkill /FI "COMMANDLINE eq ..."`：taskkill 的过滤器**不支持
    通配**（`eq *--headless=new*` 会报 `ERROR: The search filter cannot be
    recognized.`），2026-11-09 实测原实现等于什么都没杀，
    旧的无头实例一直占着 9333，新模式根本起不来还被判定成「已有实例，复用」。
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*remote-debugging-port=%d*' "
             "-and $_.CommandLine -notlike '*--type=*' } | "
             "ForEach-Object { $_.ProcessId }" % port],
            capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001
        return []
    pids = []
    for line in (out.stdout or "").splitlines():
        s = line.strip()
        if s.isdigit():
            pids.append(int(s))
    return pids


def close(port: int = PORT, log=print) -> None:
    """关掉占用这个端口的 Chrome 实例（整个进程树）。

    正常跑完**不关**（让登录态留在 profile 里）；只有「模式不对要重开」
    或排查时才调。
    """
    pids = _pids_on_port(port)
    if not pids:
        log("   [关闭] 端口 %d 上没有 Chrome 主进程" % port)
        return
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=20)
        except Exception:  # noqa: BLE001
            pass
    log("   [关闭] 已结束端口 %d 上的 Chrome 主进程 %s" % (port, pids))


def clean_profile(log=print) -> None:
    """profile 坏了（起不来/一直未登录）时清空重来。调用前必须先 close()。"""
    try:
        shutil.rmtree(_PROFILE, ignore_errors=True)
        log("   [PW] 已清空 profile：%s" % _PROFILE)
    except Exception as e:  # noqa: BLE001
        log("   [PW] 清 profile 失败：%s" % str(e)[:120])
