# -*- coding: utf-8 -*-
"""ziniao_store.py — 紫鸟店窗口生命周期 + CDP 端口现场探测（power 域单点实现）。

为什么有它（2026-09-16 立，起因＝TikTok 每日导出 TLA/TH 又一次浏览器级握手卡死）：
    「窗口未开」与「浏览器级 CDP 握手卡死」（Playwright 的 browser-level CDP 连接器连指纹浏览器：
    ws 已连、初始化永不完成 → 15s/45s/180s 超时，见 RL-013）这两件事，此前**每天靠执行会话手工**
    `ziniao-cli store close/open --id <storeId>` + 现场重探端口来救，处置步骤只留在
    当天被覆盖写的汇总文件与任务卡评论里（SKILL `ziniao-cdp-port` D2）。
    这里把「恢复动作」固化成可调用实现，供 `daily_sales_driver` 在连接失败时自动调用
    （重试 ≤1 次），不再人工介入。

对外（L1 Bridge 生命周期层；不做页面取数）：
    ensure_cli_path()                                 自愈 PATH（node/ziniao-cli 版本目录会漂移）
    ziniao(args, timeout)                             跑 `ziniao-cli <args>` → 输出文本（失败 "ERR ..."）
    open_store(store_id) / close_store(store_id)      -> bool
    probe_port_by_window(window_name) -> int          按窗口名【前缀】匹配探 CDP 端口（RL-022，现取）
    restart_and_reprobe(store_id, window_name) -> int close→open→等窗口稳定→**重探**端口（禁套用旧端口）

安全/合规：窗口名与 storeId 由调用方从 `data/ziniao_power_map.json` 传入（不写死）；
所有子进程走共享库 `lib.quiet` 的 **`hidden_run`（隐藏控制台）**——`powershell → ziniao-cli → node`
全链继承，Windows 不弹黑框（本域硬规则见 scripts/power/README.md §五）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

try:  # 静默起进程（不弹黑框）；方法库不可用时降级为 subprocess（不阻断业务）
    from lib import quiet as _quiet
except Exception:  # noqa: BLE001
    _quiet = None

#: 兜底「隐藏控制台」flags（lib/quiet 不可用时才用；非 Windows 无意义）。
#: 本模块跑的是 `powershell -Command ziniao-cli …`——**powershell 还会派生 node**。口径（2026-09-22
#: 用户立规「新脚本不许开 CMD 黑框」）：会再派生的调用给"**存在但隐藏**"的控制台（后代继承），
#: 而不是"没有控制台"的 `CREATE_NO_WINDOW`（后代归属交给 Windows；2026-09-14 控制面在这条路上翻过车）。
_HIDDEN_FLAGS = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))


def _hidden_si():
    """`STARTUPINFO(wShowWindow=SW_HIDE)`（非 Windows 返回 None）。"""
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si

__all__ = [
    "ensure_cli_path",
    "ziniao",
    "open_store",
    "close_store",
    "probe_port_by_window",
    "window_running",
    "snapshot_tabs",
    "open_tab",
    "restore_tabs",
    "ENTRY_URL_HINTS",
    "user_tabs",
    "restart_and_reprobe",
    "LOGIN_OK",
    "LOGIN_NO",
    "LOGIN_UNKNOWN",
    "probe_login",
    "require_login",
    "DEFAULT_HUNG_TAB_CFG",
    "load_hung_tab_cfg",
    "list_all_targets",
    "tab_responds",
    "close_hung_tabs",
]


def _run(cmd, timeout: float = 90, stdout=None, **kw):
    """等价 subprocess.run，但统一走 lib/quiet（不弹黑框）——**隐藏控制台**，后代继承。"""
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw["timeout"] = timeout
    if stdout is not None:
        kw["stdout"] = stdout
        kw["stderr"] = subprocess.STDOUT
    else:
        kw.setdefault("capture_output", True)
    if _quiet is not None and hasattr(_quiet, "hidden_run"):
        return _quiet.hidden_run(cmd, **kw)
    if os.name == "nt":                        # 方法库缺失/偏旧 → 自带 flags（保守），也不许裸 spawn
        return subprocess.run(cmd, startupinfo=_hidden_si(),
                              creationflags=_HIDDEN_FLAGS, **kw)
    return subprocess.run(cmd, creationflags=0, **kw)


def _q(t: str) -> str:
    """PowerShell -Command 用的参数引用（含空格/特殊字符时加双引号）。"""
    s = str(t)
    if re.search(r"[\s&?'\"]", s):
        return '"' + s.replace('"', '`"') + '"'
    return s


_CLI_DIR = None  # 惰性缓存：已解析到的 node/ziniao-cli 目录（"" ＝无需处理或没找到）


def ensure_cli_path() -> str:
    """自愈 PATH：把本机可用的 node + ziniao-cli 目录前插 PATH，返回该目录（无则 ""）。

    背景：`.workbuddy` 的 node 版本目录会漂移（22.22.2 → 22.22.2-2 → 22.22.2-3，旧目录被删），
    而调度会话继承的 PATH 可能仍指向已删除目录（Windows 上 PATH 里的死目录不报错，只是查不到命令）。
    零硬编码版本号：现场扫 `<HOME>/.workbuddy/binaries/node/versions/*`，取同时含 node.exe
    与 ziniao-cli shim 的最新目录。
    """
    global _CLI_DIR
    if _CLI_DIR is not None:
        return _CLI_DIR
    if shutil.which("ziniao-cli") and shutil.which("node"):
        _CLI_DIR = ""
        return ""
    base = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions")
    if os.path.isdir(base):
        names = [n for n in os.listdir(base) if not n.startswith(".")]

        def _mtime(n: str) -> float:
            try:
                return os.path.getmtime(os.path.join(base, n))
            except OSError:
                return 0.0

        for name in sorted(names, key=_mtime, reverse=True):
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                continue
            has_node = os.path.exists(os.path.join(d, "node.exe"))
            has_cli = any(os.path.exists(os.path.join(d, s))
                          for s in ("ziniao-cli.cmd", "ziniao-cli.ps1", "ziniao-cli"))
            if has_node and has_cli:
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                _CLI_DIR = d
                return d
    _CLI_DIR = ""
    return ""


def ziniao(args, timeout: float = 90) -> str:
    """跑 `ziniao-cli <args>`，返回输出文本；失败返回 "ERR ..."（不抛异常）。

    ziniao-cli 是 .ps1/.cmd 全局 shim，直接 CreateProcess 会 WinError 2，故一律经
    `powershell -NoProfile -Command`；stdout+stderr 合并写临时文件（不走命名管道，
    沙箱下管道会被拒）。成功提示「✓ 店铺已打开/关闭」落在 stderr，故合并读取。
    """
    ensure_cli_path()
    cmd = " ".join(_q(t) for t in ["ziniao-cli"] + [str(a) for a in args])
    fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="ziniao_")
    os.close(fd)
    text = ""
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            p = _run(["powershell", "-NoProfile", "-Command", cmd], timeout=timeout, stdout=fh)
        with open(out_path, encoding="utf-8") as fh:
            text = fh.read()
        rc = p.returncode
    except Exception as e:  # noqa: BLE001
        return "ERR %s" % str(e)[:200]
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    if rc != 0:
        return "ERR rc=%d %s" % (rc, text.strip()[:300])
    return text.strip()


def _ok(text: str) -> bool:
    """ziniao-cli 输出判成功（rc=0 且无显式失败标记；成功提示在 stderr 被合并进来）。"""
    if text.startswith("ERR"):
        return False
    flat = text.replace(" ", "")
    return '"ok":false' not in flat and "失败" not in text


# ---------------------------------------------------------------- 跑前自检：紫鸟客户端登录态
# 来历（2026-09-28 事故写回）：紫鸟客户端 09-27 12:27 自行登出（client 日志 `user logout,
# trigger_reason:5`；前两条是 store/sync/open 网关返回解不开 → 响应解密失败），此后所有依赖店窗口的
# 自动化全部停在「未探测到窗口 X 的 CDP 端口」——**报的是窗口，真因是没登录**，白丢一整天且没人及时发现。
# 这里把「登录态」固化成可调用自检，供各 run_*.py 在**动手前**先跑一次（判定唯一源见下）。
#
# 判定（唯一源；表里没有的场景 → 不拦）：
#   `ziniao-cli store list --format json` 的原始输出 → 状态
#   · `ok:true`                                        → LOGIN_OK（放行）
#   · `ok:false` + error.type=auth / message=未登录      → LOGIN_NO（**唯一 HARD STOP 条件**）
#   · 其余（rc 非 0、超时、Bridge 故障、无法解析、Boss 权限类）→ LOGIN_UNKNOWN（**放行 + 告警**）
# 为什么只有"明确未登录"才拦：登录态是确定性的、人工可修；而超时/桥抖动是瞬时的，
# 拦下去会把本来能跑通的活误杀（宁可放行后按老路失败，也不误杀）。
LOGIN_OK = "ok"
LOGIN_NO = "not_logged_in"
LOGIN_UNKNOWN = "unknown"


def _login_verdict(text: str) -> tuple:
    """把 `ziniao-cli store list --format json` 的原始输出判成 (状态, 说明)。**纯函数**（可单测）。

    容错两种形态：成功是纯 JSON；未登录时 CLI rc=1，输出被 `ziniao()` 包成 "ERR rc=1 {json}"。
    """
    raw = (text or "").strip()
    payload = None
    m = re.search(r"\{.*\}", raw, re.S)
    if m is not None:
        try:
            payload = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            payload = None
    if isinstance(payload, dict):
        if payload.get("ok") is True:
            items = (payload.get("data") or {}).get("items") or []
            return LOGIN_OK, "登录态正常（可见 %d 家店铺）" % len(items)
        err = payload.get("error") or {}
        etype = str(err.get("type") or "")
        emsg = str(err.get("message") or "")
        low = emsg.lower()
        # 排除 Boss 权限类（同为 type=auth，但不是"没登录"）——那种不拦
        if "boss" not in low and "权限" not in emsg:
            if etype == "auth" or "not logged in" in low or "未登录" in emsg:
                return LOGIN_NO, (emsg or "Ziniao user is not logged in")
    return LOGIN_UNKNOWN, (raw[:200] or "无输出")


def probe_login(timeout: float = 60.0) -> tuple:
    """现取紫鸟客户端登录态 → (LOGIN_OK|LOGIN_NO|LOGIN_UNKNOWN, 说明)。走本地 Bridge，成员账号可用。"""
    ensure_cli_path()
    return _login_verdict(ziniao(["store", "list", "--format", "json"], timeout=timeout))


def require_login(log=None) -> tuple:
    """跑前自检（供各 run_*.py 起点调用）→ (可继续?, 说明)。

    只有「明确未登录」返回 (False, 原因)；其余一律 (True, 说明)，由调用方决定是否只告警。
    """
    state, detail = probe_login()
    if log is not None:
        try:
            log("[自检] 紫鸟登录态=%s（%s）" % (state, detail))
        except Exception:  # noqa: BLE001
            pass
    if state == LOGIN_NO:
        return False, "紫鸟客户端未登录（%s）；请人工在紫鸟客户端登录后重跑" % detail
    return True, detail


# ------------------------------------------------------- 跑脚本前清「无响应标签页」（attach 假死的已复现触因）
# 来历（2026-09-28 实测复现，非推断）：窗口里只要有**一个「渲染进程无响应」的标签页**，Playwright 的
# browser-level CDP 附着就会卡到超时（实测 8.24s 超时）；**同一时刻**原生 websocket 发
# `Browser.getVersion` 仍秒回（协议层完全健康），**关掉那个标签页后立刻恢复**（0.23s）。
# 机制：Playwright 初始化会逐个 target 注入 init 脚本（`Runtime.callFunctionOn … __pwInitScripts`），
# 碰到不响应的 target 会一直等。已排除的两条：扩展 offscreen 页（一直在，attach 照样 0.23~2.2s 通）、
# 若干空白残留页（造 5 个 about:blank，attach 仍 0.72s 通）。
# 因此"清无响应页"是比重开窗口**便宜得多**的自愈动作：关 tab 零成本、不掉授权态；重开窗口会掉合作仓授权。
#
# 判据（唯一源）：
#   · 只探 `type == "page"` 的 target；
#   · **跳过** 扩展页（`chrome-extension://`）与 `devtools://`（不是触因，且关扩展页可能破坏紫鸟扩展）；
#   · 探活 = 在该 target 的 ws 上发一次 `Runtime.evaluate("1")`，`probe_timeout_sec` 内无回应 ＝ 无响应；
#   · 最多探测 `max_probe` 个、最多关 `max_close` 个（安全阀）；
#   · 任何异常一律降级（不抛、不阻断业务）。
DEFAULT_HUNG_TAB_CFG = {
    "_说明": "跑脚本前清「无响应标签页」的判据（本文件是缺省值；要改口径改 data/hung_tabs.json，不改代码）。",
    "probe_timeout_sec": 3.0,
    "max_probe": 8,
    "max_close": 8,
    "skip_url_prefixes": ["chrome-extension://", "devtools://"],
}


def load_hung_tab_cfg(path: str = None) -> dict:
    """读 `data/hung_tabs.json` 覆盖缺省判据（缺文件/坏文件 ⇒ 用缺省，不阻断业务）。

    `path` 只给单测注入用；业务侧走缺省（口径文件位置固定，AI 只改 json 不改代码）。
    """
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_HUNG_TAB_CFG.items()}
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "data", "hung_tabs.json")
    try:
        with open(path, encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                if not str(k).startswith("_"):
                    cfg[k] = v
    except Exception:  # noqa: BLE001
        pass
    return cfg


def list_all_targets(port: int, timeout: float = 5.0) -> list:
    """窗口里**全部** CDP target（含非 page：worker/iframe/扩展页）。只读 HTTP `/json/list`。"""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(port), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace")) or []


def tab_responds(ws_url: str, timeout: float = 3.0):
    """在该 target 的 websocket 上发一次只读 `Runtime.evaluate("1")` → **三态**：

    · `True`  ＝ 按时回到（有响应）
    · `False` ＝ **确定无响应**：ws 连上了、请求也发出去了，但 `timeout` 内**没有回包**
                （＝实测复现的那条签名：渲染进程卡死的页就是这个形态）
    · `None`  ＝ **无法判定**：缺 `websockets` 库 / 连不上 / 回包解析不了

    **为什么必须区分 False 与 None**：`False` 才会触发关闭标签页；把"探测本身失败"混成 `False`，
    会在缺库或网络抖动时把正常业务页全关掉。风险不对称 —— 漏关一个卡死页只是退回老的自愈路径，
    误关一个正常页是真实损失。
    """
    try:
        from websockets.sync.client import connect as _ws_connect
    except Exception:  # noqa: BLE001
        return None
    try:
        ws = _ws_connect(ws_url, open_timeout=timeout, close_timeout=1, proxy=None, max_size=None)
    except Exception:  # noqa: BLE001
        return None                      # 连不上 ⇒ 判定不了，不据此关页
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": "1", "returnByValue": True}}))
        try:
            msg = ws.recv(timeout=timeout)
        except TimeoutError:
            return False                 # 连上但不回 ⇒ 确定无响应（实测复现的形态）
        try:
            obj = json.loads(msg) if isinstance(msg, str) else {}
        except Exception:  # noqa: BLE001
            return None
        return obj.get("id") == 1
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def close_hung_tabs(port: int, cfg: dict = None, log=None, *,
                    targets: list = None, responder=None, closer=None) -> dict:
    """跑脚本前清「无响应标签页」→ `{"checked":n,"hung":[id…],"closed":[id…],"skipped":[…],"error":str|None}`。

    `targets` / `responder` / `closer` 是测试注入点（缺省走真实 HTTP `/json/list` + 原生 ws 探活 +
    HTTP `/json/close/<id>`）。任何环节失败都不抛，只如实回报（自检/自愈不能把业务拖死）。
    """
    cfg = cfg or load_hung_tab_cfg()
    timeout = float(cfg.get("probe_timeout_sec") or 3.0)
    max_probe = int(cfg.get("max_probe") or 8)
    max_close = int(cfg.get("max_close") or 8)
    skip_prefixes = tuple(cfg.get("skip_url_prefixes") or ())
    _say = (lambda m: None) if log is None else log
    out = {"checked": 0, "hung": [], "closed": [], "skipped": [], "error": None}
    try:
        tg = list(targets) if targets is not None else list_all_targets(port)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
        return out
    cand = []
    for t in tg:
        tid = str(t.get("id") or "")
        url = str(t.get("url") or "")
        if t.get("type") != "page" or not t.get("webSocketDebuggerUrl"):
            out["skipped"].append({"id": tid, "why": "非page/无ws"})
            continue
        if any(url.startswith(p) for p in skip_prefixes):
            out["skipped"].append({"id": tid, "why": "扩展/devtools页"})
            continue
        cand.append(t)
    cand = cand[:max_probe]
    _resp = responder if responder is not None else tab_responds
    _close = closer if closer is not None else close_target
    for t in cand:
        tid = str(t.get("id") or "")
        out["checked"] += 1
        try:
            alive = _resp(str(t.get("webSocketDebuggerUrl")), timeout)
        except Exception:  # noqa: BLE001
            alive = None                 # 探活自身抛错 ⇒ 无法判定（**不当成无响应**）
        if alive is True:
            continue
        if alive is None:                # 无法判定：不关（缺库/连不上时不许误关正常页）
            out["skipped"].append({"id": tid, "why": "探活无法判定"})
            continue
        out["hung"].append(tid)
        if len(out["closed"]) >= max_close:
            out["skipped"].append({"id": tid, "why": "超 max_close 未关"})
            continue
        try:
            if _close(port, tid):
                out["closed"].append(tid)
                _say("[清障] 关掉无响应标签页 %s（%s）" % (tid[:8], str(t.get("url") or "")[:60]))
        except Exception as e:  # noqa: BLE001
            out["skipped"].append({"id": tid, "why": "关闭失败:%s" % str(e)[:60]})
    return out


def close_store(store_id: str) -> bool:
    """关闭店铺窗口（紫鸟 ZClaw Bridge）。"""
    if not store_id:
        return False
    return _ok(ziniao(["store", "close", "--id", str(store_id)], timeout=90))


def open_store(store_id: str) -> bool:
    if not store_id:
        return False
    return _ok(ziniao(["store", "open", "--id", str(store_id)], timeout=180))


def _is_cdp(port: int, timeout: float = 3.0) -> bool:
    """该端口是否为真 CDP（/json/version 带 webSocketDebuggerUrl）。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % int(port),
                                    timeout=timeout) as r:
            v = json.loads(r.read().decode("utf-8", "replace"))
        return bool(v.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001
        return False


def probe_port_by_window(window_name: str) -> int:
    """按紫鸟窗口名【前缀】匹配 `ziniaobrowser` 进程 → 取其监听端口并验 CDP。找不到返回 0。

    RL-022：页面会改窗口标题（实测 `TM-BHY-YJF    |    卖家中心`），精确 `-eq` 会当场探不到；
    必须**前缀**匹配。端口每次开窗都变 → 一律现场探测，禁套用历史值（RL-002）。
    """
    if not window_name:
        return 0
    ps_cmd = ("Get-Process ziniaobrowser -ErrorAction SilentlyContinue | "
              "Where-Object { $_.MainWindowTitle -like '%s*' } | "
              "Select-Object -ExpandProperty Id" % window_name)
    try:
        ps = _run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=25)
        out = ps.stdout or ""
    except Exception:  # noqa: BLE001
        return 0
    pids = {int(x) for x in out.split() if x.strip().isdigit()}
    if not pids:
        return 0
    try:
        net = _run(["netstat", "-ano", "-p", "tcp"], timeout=20)
        lines = (net.stdout or "").splitlines()
    except Exception:  # noqa: BLE001
        return 0
    for line in lines:
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        try:
            if int(parts[4]) not in pids:
                continue
            port = int(parts[1].rsplit(":", 1)[1])
        except Exception:  # noqa: BLE001
            continue
        if 10000 < port < 70000 and _is_cdp(port):
            return port
    return 0


def window_running(window_name: str) -> bool:
    """该店窗口**进程还在不在**（`MainWindowTitle` 前缀匹配，与 `probe_port_by_window` 同口径）。

    为什么必须有这条判据（2026-10-29 用户事故）：「触发定时任务去两个店铺开新标签页，把我之前开的
    页面也杀掉」。根因就是自愈只看**端口通不通**：端口探不到（刚开窗 / 调试端口还没监听 / 抖动）
    就 close→open，而关窗 = 把用户自己打开的标签页**全部**杀掉。当天 BEL 窗口在一次异常单任务里
    被重开 6 次，用户开着的页全没了，窗口只剩一张登录页。
    ⇒ **要不要重开**的唯一判据是「窗口进程在不在」，不是端口通不通。窗口还在 ⇒ 宁可不自愈。
    """
    if not window_name:
        return False
    ps_cmd = ("Get-Process ziniaobrowser -ErrorAction SilentlyContinue | "
              "Where-Object { $_.MainWindowTitle -like '%s*' } | "
              "Select-Object -First 1 -ExpandProperty Id" % window_name)
    try:
        ps = _run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=25)
    except Exception:  # noqa: BLE001
        return False
    return bool((ps.stdout or "").strip())


def snapshot_tabs(port: int) -> list:
    """把窗口里**真实标签页**的 URL 记下来（重开窗口前存证、重开后复原用）。"""
    try:
        urls = [str(t.get("url") or "") for t in list_page_targets(int(port or 0))]
    except Exception:  # noqa: BLE001
        return []
    return [u for u in urls if is_real_tab(u) and u != "about:blank"]


def open_tab(port: int, url: str) -> str:
    """新开一个（后台）标签页：HTTP `PUT /json/new?<url>` → targetId（失败 ""）。

    `GET /json/new` 在新版 Chromium 上会被 405 拒掉，必须 PUT（2026-10-29 实测）。
    """
    import urllib.parse
    import urllib.request
    if not url:
        return ""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?%s" % (int(port), urllib.parse.quote(url, safe="")),
            method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return str(d.get("id") or "")
    except Exception:  # noqa: BLE001
        return ""


def restore_tabs(port: int, urls: list, log=None) -> list:
    """重开窗口后把用户原来的页**一页页开回来**（去重、跳过空白页；best-effort 不抛）。"""
    def _say(msg):
        if log is not None:
            try:
                log(msg)
            except Exception:  # noqa: BLE001
                pass

    opened, seen = [], set()
    for u in urls or []:
        u = str(u or "").strip()
        if not u or u == "about:blank" or u in seen or not is_real_tab(u):
            continue
        seen.add(u)
        tid = open_tab(port, u)
        if tid:
            opened.append(u)
            _say("[保页] 重开后已恢复：%s" % u[:90])
        else:
            _say("[保页] 恢复失败（跳过）：%s" % u[:90])
    return opened


#: 「可再生」的入口页 URL 片段：登录/授权/空白页。除此之外的真实标签页都算**用户的页面**。
ENTRY_URL_HINTS = (
    "seller.kuajingmaihuo.com/login",
    "seller.kuajingmaihuo.com/settle/seller-login",
    "agentseller-us.temu.com/auth/authentication",
    "about:blank",
)


def user_tabs(port: int) -> list:
    """窗口里**用户自己的**标签页（排除登录/授权/空白这类关掉也能再生的入口页）。

    用途：任何"要不要关窗"的决定都必须先看它（2026-10-29 用户：「触发定时任务去两个店铺
    开新标签页，把我之前开的页面也杀掉」）。窗口一关，用户开着的商品页/订单页全没；
    所以判据必须是「还有没有用户页面」，而不是「我的活干完了没有」。
    """
    try:
        targets = list_page_targets(int(port or 0))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for t in targets:
        u = str(t.get("url") or "")
        if not is_real_tab(u):
            continue
        if any(h in u for h in ENTRY_URL_HINTS):
            continue
        out.append(u)
    return out


def restart_and_reprobe(store_id: str, window_name: str, wait: float = 10.0,
                        log=None, poll_timeout: float = 45.0, port: int = 0) -> int:
    """close → open 重开窗口 → 等窗口稳定 → **现场重探**端口，返回新端口（失败 0）。

    用途：浏览器级 CDP 握手卡死 / 窗口未开 的自愈动作（RL-013、SKILL `ziniao-cdp-port` D2）。
    `log(msg)` 可选，用于把恢复过程写进调用方的标准日志（留痕）。

    ★ 2026-10-29（用户：「把我之前开的页面也杀掉」）：重开是**破坏性**动作 —— 关窗会把用户打开的
    标签页全杀掉。所以这里先 `snapshot_tabs(port)` 存一份 URL，重开后 `restore_tabs` 逐页开回来。
    能不失忆就不要失忆；调用方能用「窗口还在就不重开」的判据（`window_running`）就别走到这里。
    """
    def _say(msg: str) -> None:
        if log is None:
            return
        try:
            log(msg)
        except Exception:  # noqa: BLE001
            pass

    _say("close store=%s" % store_id)
    # ★ 先存一份用户当前的标签页（best-effort：CDP 卡死时 HTTP /json/list 通常仍可用）
    old_port = int(port or 0) or probe_port_by_window(window_name)
    saved = snapshot_tabs(old_port) if old_port else []
    if saved:
        _say("[保页] 重开前记下 %d 个标签页（重开后开回来）" % len(saved))
    closed = close_store(store_id)
    _say("close ok=%s" % closed)
    time.sleep(3)
    _say("open store=%s" % store_id)
    opened = open_store(store_id)
    _say("open ok=%s" % opened)
    time.sleep(max(wait, 0))
    deadline = time.time() + max(poll_timeout, 5)
    new_port = 0
    while True:
        new_port = probe_port_by_window(window_name)
        if new_port or time.time() >= deadline:
            break
        time.sleep(3)
    _say("reprobe window=%s port=%s" % (window_name, new_port))
    if new_port and saved:
        try:
            got = restore_tabs(new_port, saved, log=_say)
            _say("[保页] 重开后恢复 %d/%d 个标签页" % (len(got), len(saved)))
        except Exception as e:  # noqa: BLE001
            _say("[保页] 恢复失败（不影响主流程）：%s" % str(e)[:80])
    return new_port


# ---------------------------------------------------------------- 卡死残留页清理（跑脚本前的清障步）
# 来历（用户 2026-09-24：「残留死页把 playwright attach 拖死……这已经是第三次为它排障了——真该在流程里
# 加一步『跑脚本前先清卡死页』」）：窗口里常躺着上一次流程留下的**空白页**，它们会让
# ① Playwright browser-level attach 变慢/卡死（RL-013 那类握手超时）；② 按 URL 挑页时挑错。
#
# **判据（先取现场样本再定，2026-09-24 实测）**：
#   · BHY 窗口 `seller.kuajingmaihuo.com/settle/seller-login` → 正文 0 字、0 个 input ⇒ **真空死页**（清）；
#   · BEL 窗口 `agentseller-us.temu.com/auth/authentication` → 正文 32 字「商家中心 中国地区…」、1 个可见 input
#     ⇒ **不是垃圾，是「需要授权」的信号**（**保留**：清掉等于把真问题擦掉）。
# 所以**只清"探针证明是空页"的候选页**，有内容的认证页一律留着。
DEFAULT_DEAD_TAB_CFG = {
    "_说明": "跑脚本前的清障步判据（本文件是缺省值；要改口径改 data/dead_tabs.json，不改代码）。",
    "candidate_patterns": [
        "seller.kuajingmaihuo.com/login",
        "seller.kuajingmaihuo.com/settle/seller-login",
        "agentseller-us.temu.com/auth/authentication",
    ],
    "keep_patterns": [],
    "blank_text_max": 40,
    "blank_visible_inputs_max": 0,
}

#: 空页探针（目标级 CDP 执行；只读 DOM，不点击）
_JS_BLANK_PROBE = r"""() => {
  const vis = e => !!(e.getClientRects().length);
  const inputs = [...document.querySelectorAll('input')];
  const t = (document.body ? (document.body.innerText || '') : '').replace(/\s+/g, ' ').trim();
  return {textLen: t.length, text: t.slice(0, 80), visInputs: inputs.filter(vis).length};
}"""


def load_dead_tab_cfg() -> dict:
    """读 `data/dead_tabs.json` 覆盖缺省判据（缺文件/坏文件 ⇒ 用缺省，不阻断业务）。"""
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_DEAD_TAB_CFG.items()}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "dead_tabs.json")
    try:
        with open(path, encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                if not str(k).startswith("_"):
                    cfg[k] = v
    except Exception:  # noqa: BLE001
        pass
    return cfg


def list_page_targets(port: int) -> list:
    """窗口里所有 page 目标（走 HTTP `/json/list`，起手 0.1s，不需要 websocket）。"""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(port), timeout=5) as r:
        tg = json.loads(r.read().decode("utf-8", "replace"))
    return [t for t in (tg or []) if t.get("type") == "page"]


def close_target(port: int, target_id: str) -> bool:
    """关掉一个 tab（HTTP `/json/close/<id>`）。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (int(port), target_id),
                                    timeout=8) as r:
            return b"Target is closing" in r.read() or True
    except Exception:  # noqa: BLE001
        return False


#: **不算"真实标签页"的目标**（紫鸟窗口里恒有这些杂页，算进去会让"最后一个页面"判错）：
#: 2026-09-24 实测事故——窗口里只有 [卖家中心登录页 + chrome-extension://…/offscreen]，
#: 把扩展页也算成一个"页面" ⇒ 判"登录页不是最后一个"⇒ 关掉 ⇒ **整个窗口消失**（端口随即失效）。
_NON_TAB_PREFIXES = ("chrome-extension://", "devtools://", "chrome://")


def is_real_tab(url: str) -> bool:
    """这个 target 是不是**真实标签页**（排除扩展/devtools 等杂页）。"""
    u = str(url or "").strip()
    if not u:
        return False
    return not u.startswith(_NON_TAB_PREFIXES)


def window_alive(port: int) -> bool:
    """端口还活着吗（`/json/version` 能答就算活）。清障后必须用它自检：窗口没了就别再往下走。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % int(port), timeout=4) as r:
            return bool(json.loads(r.read().decode("utf-8", "replace")).get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001
        return False


def clean_dead_tabs(window_name: str = "", port: int = 0, dry_run: bool = False, log=None) -> dict:
    """**跑脚本前的清障步**：关掉窗口里的空白残留页 / 入口页，**但绝不把窗口关没**。

    返回 `{"window","port","closed","kept","dead","errors","real_tabs","window_alive"}`。
    全程 best-effort：探不到端口/单个页探针失败都只记进 `errors`，**不抛异常、不影响主流程**。
    调用点（2026-09-24 接线）：`run_temu_auth.py` / `run_temu_sales.py` / `run_daily_sales.py`
    在**连浏览器之前**调用；也可手工跑 `python ziniao_store.py "<窗口名>" --clean [--dry-run]`。

    **两条硬规矩（2026-09-24 事故后立）**：
      ① "页面数"只算**真实标签页**（`is_real_tab`）——扩展/devtools 杂页不算；
      ② **最后一个真实标签页永不关**（关掉它＝整个窗口消失，端口随即失效，下游报
         「连不上 CDP 端口 / WinError 1225」）。新开窗口的唯一落地页（卖家中心登录页 /
         认证页）因此会被保留：宁可留着，也不能把窗口关没。
    收尾再自检一次端口是否还活（`window_alive`），把结论写进 `window_alive` 字段供调用方决策。
    """
    def _say(msg):
        if log is not None:
            try:
                log(msg)
            except Exception:  # noqa: BLE001
                pass

    cfg = load_dead_tab_cfg()
    cands = [str(x) for x in (cfg.get("candidate_patterns") or [])]
    always = [str(x) for x in (cfg.get("always_close_patterns") or [])]
    keeps = [str(x) for x in (cfg.get("keep_patterns") or [])]
    text_max = int(cfg.get("blank_text_max") or 40)
    vis_max = int(cfg.get("blank_visible_inputs_max") or 0)
    out = {"window": window_name, "port": int(port or 0), "closed": [], "kept": [],
           "dead": [], "errors": [], "real_tabs": 0, "window_alive": None}
    if not port and window_name:
        try:
            port = probe_port_by_window(window_name)
        except Exception as e:  # noqa: BLE001
            out["errors"].append("probe:%s" % str(e)[:60])
    out["port"] = int(port or 0)
    if not port:
        out["errors"].append("no-port")
        _say("[清障] 拿不到窗口 %s 的端口 → 跳过（不影响主流程）" % (window_name or "?"))
        return out
    try:
        targets = list_page_targets(port)
    except Exception as e:  # noqa: BLE001
        out["errors"].append("list:%s" % str(e)[:60])
        _say("[清障] 读 tab 列表失败：%s（跳过）" % str(e)[:60])
        return out

    real = [t for t in targets if is_real_tab(str(t.get("url") or ""))]
    out["real_tabs"] = len(real)
    n_real = len(real)

    # 先"点名"再动手（只有真实标签页参与"最后一个"的判定）
    todos = []
    for t in targets:
        url = str(t.get("url") or "")
        tid = str(t.get("id") or t.get("targetId") or "")
        if not url or not tid or not is_real_tab(url):
            continue                                   # 扩展/devtools 杂页：不算页面、也不碰
        if any(k in url for k in keeps):
            continue
        must = any(c in url for c in always)           # 入口/落地页：可直接关（可再生）
        if not must and not any(c in url for c in cands):
            continue
        if must:
            todos.append((t, tid, url, {"url": url[:110], "text_len": -1, "visible_inputs": -1,
                                        "text": "(入口页·可直接关)"}, True))
            continue
        try:
            import cdp_transport                      # 目标级探针（同目录单点实现）
            d = cdp_transport.CdpPage(port, t).evaluate(_JS_BLANK_PROBE) or {}
        except Exception as e:  # noqa: BLE001
            out["errors"].append("probe_page:%s" % str(e)[:60])
            continue
        rec = {"url": url[:110], "text_len": int(d.get("textLen") or 0),
               "visible_inputs": int(d.get("visInputs") or 0), "text": str(d.get("text") or "")[:60]}
        blank = (int(d.get("textLen") or 0) <= text_max
                 and int(d.get("visInputs") or 0) <= vis_max)
        if not blank:
            out["kept"].append(rec)
            _say("[清障] 保留（有内容）：%s" % url[:80])
            continue
        todos.append((t, tid, url, rec, False))

    # ★ 硬规矩②：**关完之后必须还剩 ≥1 个真实标签页**（关光＝窗口消失）。
    #   ⚠ 2026-10-10 修（无人值守实测踩到）：旧写法 `if len(todos) >= n_real: pop()` 用
    #   「待关数 ≥ 真实页总数」当判据 —— 窗口是【2 个候选入口页 + 1 个别的真实页】时，
    #   2 >= 3 为假、**保护根本不触发**，两个入口页全关。若那个"别的页"其实是 `about:blank`
    #   （很常见），窗口就只剩空白页；而被关掉的里面正有 `auth/authentication`（唯一能继续授权的页）
    #   ⇒ 下游 `find_auth_page` 找不到认证页 ⇒ 报「未找到认证/授权页」⇒ 无人值守失败。
    #   ⇒ 改成按「**最多能关几个**」裁剪：最多关到只剩 1 个真实页，多余的候选从**队尾**退回保留。
    if n_real and todos:
        max_close = max(0, n_real - 1)              # 至少留 1 个真实页
        excess = len(todos) - max_close
        for _ in range(max(0, excess)):
            dropped = todos.pop()                   # 从队尾退（保留靠前的候选）
            out["kept"].append(dict(dropped[3], text="(最后一个真实标签页·不关：关光了窗口会只剩空页/消失)"))
            _say("[清障] 保底留页（最后一个真实标签页·不关）：%s" % dropped[2][:80])
    if not n_real:
        _say("[清障] 窗口里没有真实标签页（只有扩展/杂页）→ 什么都不关")

    for t, tid, url, rec, must in todos:
        out["dead"].append(rec)
        if dry_run:
            _say("[清障] （演练）会关掉%s：%s" % ("入口页" if must else "空页", url[:80]))
            continue
        # `# noqa: tab-safety`：只关**探针证明过的空页/入口页**（候选来自
        # `data/dead_tabs.json` 的 URL 白名单，且已保证「最后一个真实页永不关」）。
        # 这不是按"死活"批量关，而是按可再生的 URL 特征关，所以不需要登记表判归属。
        if close_target(port, tid):  # noqa: tab-safety 只关探明为空的入口页
            out["closed"].append(rec)
            _say("[清障] 已关掉%s：%s" % ("入口页" if must else "空页", url[:80]))
        else:
            out["errors"].append("close_failed:%s" % url[:60])

    # ★ 收尾自检：窗口还在吗？（调用方据此决定要不要重开，而不是接着去连死端口）
    out["window_alive"] = window_alive(port)
    if not out["window_alive"]:
        out["errors"].append("window_gone")
        _say("[清障] ⚠ 清障后端口 %s 已不可连（窗口没了）→ 调用方应重开窗口，别连死端口" % port)
    _say("[清障] %s：真实标签页 %d 个；关掉 %d / 保留 %d；端口存活=%s；错误 %d"
         % (window_name or ("port %s" % port), n_real, len(out["closed"]),
            len(out["kept"]), out["window_alive"], len(out["errors"])))
    return out


if __name__ == "__main__":  # 手工排查用：python ziniao_store.py <窗口名> [--clean]
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    w = args[0] if args else ""
    if "--clean" in sys.argv:
        print(clean_dead_tabs(window_name=w, dry_run="--dry-run" in sys.argv, log=print))
    else:
        print("port=%s window=%s" % (probe_port_by_window(w), w))
