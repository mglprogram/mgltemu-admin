# -*- coding: utf-8 -*-
"""run_temu_auth.py — TEMU 商家中心登录/授权参数化入口（按 temu-login-auth SKILL 固化）。

用法:
    python scripts/power/run_temu_auth.py --store BHY            # 按店查窗口+探测端口 → 自动授权
    python scripts/power/run_temu_auth.py --store BHY --dry-run   # 只打印端口，不授权
    python scripts/power/run_temu_auth.py --list-stores           # 列 TEMU 店（后缀/窗口/storeId）

流程（由 lib/auth_temu_driver.run_auth 完成）:
    A. 认证页(agentseller-us.temu.com/auth/authentication, 区域选择) → 点可用区域「商家中心」
       (div.authentication_goto；若弹「确认切换吗」先点确认切换) → 打开授权确认框。
    B. 授权确认框(seller.kuajingmaihuo.com/settle/seller-login；只认这个，勿误抓 .com/login 扫码登录页)
       → 勾选授权条款 checkbox(input 尺寸0, 点可见外层 LABEL/DIV)
       → 点授权/登录确认（BEL=「确认授权并前往」；BHY/KinglamanSport=「授权登录」，候选列表）
    C. 验证进入 orders.html(host+path 判，避开 redirectUrl 里的 orders.html 误导；TargetClosedError 属预期)。

工程控制:
    - 起点闸门: CDP 端口须可连(现场探测/传入), 失败 HARD_STOP(42)
    - 端口不写死: --store 从 ziniao_power_map.json 查窗口+进程探测, 或 --port
    - 数据外置: 店后缀走参数; AI 只给 --store <店后缀>
    - 日志: _logs/temu-sales/<日期>.jsonl（TEMU-auth 记录）
"""
import sys, os, io, json, argparse, subprocess
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# 迁移包自包含: 附带 scripts/lib(共享闸门库) 时优先用包内 lib, 免设 METHOD_SCRIPTS_ROOT
_INC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT") or os.path.join(os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from lib import preflight  # noqa: E402
from lib import quiet      # noqa: E402  # 子进程静默起（不弹 CMD 黑框）

# stdout 在此只包装一次。auth_temu_driver 顶层不再包装 stdout（避免嵌套/closed file），
# 其 print 依赖本入口已包装好的 stdout；独立跑 driver 时由它自己 __main__ 兜底。

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ziniao_power_map.json")
LOG_DIR = os.path.join(WS, "_logs", "temu-sales")


def _load_store_map():
    with open(MAP, encoding="utf-8") as f:
        return json.load(f)


def _store_by_suffix(suffix: str):
    for m in _load_store_map().get("mapping", []):
        if m.get("export_suffix") == suffix or m["power_name"] == suffix:
            return m
    return None


def _find_store_port(window_name: str) -> int:
    """按紫鸟窗口名（进程 MainWindowTitle）**前缀**探测 CDP 端口。

    为什么要前缀匹配（2026-09-16 实测）：授权后 TEMU 页面会改窗口标题
    （`TM-BHY-YJF    |    卖家中心`），`-eq` 精确匹配找不到窗口 → 误报「未探测到 CDP 端口」。
    """
    try:
        # netstat＝**叶子进程**（不再派生）→ 无控制台的 `quiet.run` 即可，不弹窗。
        out = quiet.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except Exception:
        return 0
    try:
        # powershell＝**会再派生**的进程 → 用 `hidden_run`（隐藏控制台，后代继承不弹窗）。
        ps = quiet.hidden_run(["powershell", "-NoProfile", "-Command",
                               "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } | Select-Object -ExpandProperty Id" % window_name],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout or ""
    except Exception:
        return 0
    pids = {int(x) for x in ps.split() if x.strip().isdigit()}
    if not pids:
        return 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                if int(parts[4]) in pids:
                    port = int(parts[1].rsplit(":", 1)[1])
                    if 10000 < port < 70000:
                        import urllib.request
                        try:
                            v = json.loads(urllib.request.urlopen(
                                "http://127.0.0.1:%s/json/version" % port, timeout=2).read())
                            if v.get("webSocketDebuggerUrl"):
                                return port
                        except Exception:
                            continue
            except Exception:
                continue
    return 0


def _log_run(shop, stage, result, verdict="ok"):
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "agent": "TEMU-auth", "shop": shop, "stage": stage,
                            "result": str(result)[:400], "verdict": verdict}, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 商家中心登录/授权参数化入口")
    ap.add_argument("--port", type=int, default=None, help="紫鸟窗口 CDP 端口(现场探测/传入)")
    ap.add_argument("--store", default=None, help="店后缀(如 BHY; 从 ziniao_power_map.json 查窗口并探测端口)")
    ap.add_argument("--dry-run", action="store_true", help="只打印端口/目标, 不操作浏览器")
    ap.add_argument("--list-stores", action="store_true", help="列出 TEMU 店(后缀/窗口/storeId)")
    args = ap.parse_args()

    if args.list_stores:
        for m in _load_store_map().get("mapping", []):
            if m.get("power_platform") == "TEMU" or m.get("power_type") == "本土店":
                sfx = m.get("export_suffix") or m["power_name"]
                print("%-10s window=%-20s storeId=%s" % (sfx, m["ziniao_window_name"], m["ziniao_window_store_id"]))
        return 0

    port = args.port
    store = args.store or "?"
    if port is None and store != "?":
        m = _store_by_suffix(store)
        if m is None:
            msg = "未知店后缀 %s（可用 --list-stores 查）" % store
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        port = _find_store_port(m["ziniao_window_name"])
        if not port:
            msg = "未探测到窗口 %s 的 CDP 端口（先开该店窗口/紫鸟客户端已启动）" % m["ziniao_window_name"]
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE

    if args.dry_run:
        drymsg = "[OK] dry-run: auth store=%s port=%s（按 temu-login-auth 完成 TEMU 登录/授权）" % (store, port)
        print(drymsg)
        _log_run(store, "auth_dry", "port=%s" % port)
        return 0

    if not port:
        msg = "缺 CDP 端口(--port 或 --store <店后缀>)"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE
    try:
        import urllib.request
        json.loads(urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=4).read())
    except Exception as e:
        msg = "CDP 端口 %s 不可连: %s" % (port, str(e)[:80])
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE

    import auth_temu_driver as atd  # noqa: E402

    # ---- 清障步（用户 2026-09-24：「跑脚本前先清卡死页」）----
    # 授权是本域**唯一**还用 Playwright browser-level attach 的一环（`auth_temu_driver`），
    # 也是被残留空页拖死的那一环；买面单/合作仓链路都会经它（子进程调用）⇒ 这里先清一次。
    # ⚠ 2026-09-24 事故：清障曾把窗口里**唯一一个真实标签页**（卖家中心登录页）当垃圾关掉 ⇒
    #   整个窗口消失 ⇒ 接着去连死端口（WinError 1225）。现在清障内部保证"最后一个真实标签页不关"，
    #   并在收尾自检端口是否还活；这里再做一次兜底：窗口没了就**重开一次**，绝不连死端口。
    try:
        import ziniao_store as _zs  # noqa: E402
        _cl = _zs.clean_dead_tabs(port=port, log=lambda x: print(x))
        _log_run(store, "clean_tabs",
                 "真实标签页 %s；关掉 %d 保留 %d；端口存活=%s"
                 % (_cl.get("real_tabs"), len(_cl.get("closed") or []),
                    len(_cl.get("kept") or []), _cl.get("window_alive")))
        if _cl.get("window_alive") is False:
            print("[清障] ⚠ 清障后窗口没了 → 重开窗口一次再连（不连死端口）")
            if store != "?":
                _m = _store_by_suffix(store) or {}
                # ★ 2026-10-29：进程还在就别开新窗（开窗会杀掉用户打开的页面）；只重探端口。
                if _zs.window_running(_m.get("ziniao_window_name") or ""):
                    print("[清障] 窗口进程还在（只是端口不答）→ 不开新窗，直接重探")
                else:
                    _zs.open_store(_m.get("ziniao_window_store_id"))
                _t0 = time.time()
                while time.time() - _t0 < 90:
                    time.sleep(3)
                    _p = _find_store_port((_m or {}).get("ziniao_window_name", ""))
                    if _p:
                        port = _p
                        print("[清障] 重开后新端口=%s" % port)
                        break
    except Exception as e:  # noqa: BLE001
        print("[清障] 跳过（%s）" % str(e)[:80])

    result = atd.run_auth(port, store)
    verdict = "ok" if result.startswith("OK") else "fail"
    # 授权成功后顺手再清一次：把本次流程可能留下的**空白授权页**收掉（治本的一半——
    # 残留就是这么一页页攒起来的；不清就等下次开窗被紫鸟还原回来继续拖慢 attach）。
    if verdict == "ok":
        try:
            import ziniao_store as _zs  # noqa: E402
            _cl = _zs.clean_dead_tabs(port=port, log=lambda x: print(x))
            if _cl.get("closed"):
                _log_run(store, "clean_tabs_after_auth", "关掉空页 %d" % len(_cl["closed"]))
        except Exception as e:  # noqa: BLE001
            print("[清障] 授权后清理跳过（%s）" % str(e)[:80])
    _log_run(store, "auth", result, verdict=verdict)
    print("[%s] %s" % ("OK" if verdict == "ok" else "FAIL", result))
    return 0 if verdict == "ok" else 42


if __name__ == "__main__":
    raise SystemExit(main())
