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

    ★ 2026-11-09 真机事故（BHY/BEL 整链全落空）：紫鸟窗口的调试端口是**随机分发**的，
      不一定按 PID 升序落（BEL 53980 / BHY 62154 就是这样）。而 `probe_port_by_window`
      取的是「遍历 netstat 第一行命中的端口」⇒ 本文件与 `ziniao_store` 可以返回**不同端口**，
      不同脚本各连各的窗口，整链炸在「窗口里没有本批订单」上。
      ⇒ 这里**复用** `ziniao_store.probe_port_by_window`（唯一实现），不再各写一份。
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from lib import ziniao_store  # noqa: PLC0415
        p = ziniao_store.probe_port_by_window(window_name)
        if p:
            return int(p)
    except Exception:  # noqa: BLE001
        pass
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
    ap.add_argument("--domain", default="us", choices=["us", "cc", "both"],
                    help="要过哪一套授权：us=美国站 agentseller-us.temu.com（默认）；"
                         "cc=合作仓 agentseller.temu.com；"
                         "**both=两套都过**（2026-11-09 加：窗口被关过重开后，两套都失效，"
                         "只补 us 会让 cc 域那几张页永远停在授权页 —— 用户看到的就是"
                         "「所有页面都要重新登录」）。两套是独立授权，别混。")
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

    # ★ 2026-11-05：授权全程留登录态证据。`run_auth` 里会点「商家中心」并可能撞上
    #   「当前已有登录中的店铺，确认切换吗」弹窗 —— 这正是"跑完任务所有页面都要重新登录"
    #   的头号嫌疑。现在前后各记一次 cookie 指纹（seller_temp / mallid / sessionType），
    #   掉了就当场写进日志并指明是哪一段丢的，不再靠事后回忆。
    #   ⚠ 只包 import：`run_auth` 自己的异常必须原样抛出去（吞了会重跑一遍授权）。
    _ls = None
    try:
        import login_state as _ls  # noqa: E402
    except Exception:  # noqa: BLE001 - 留证模块缺失不能影响授权本身
        _ls = None

    # ★ 2026-11-09：`--domain both` —— 两套授权**逐套过**，任一失败即整体失败。
    #   为什么需要（用户当天报「BEL 所有页面登录态都失效」的真实成因）：
    #   11:14 自愈动作把 BEL 窗口 close→open 了一次（CDP 端口假死），窗口重开
    #   必然丢会话；而事后只补了 `us`（订单页那条线），`cc`（商品列表/销量分析/
    #   生命周期）**一直没人补** ⇒ 那三张页永远停在 auth/authentication，
    #   用户看到的就是"所有页面都要重新登录"。
    #   两套是独立授权（见 `run_auth` 文档），必须显式逐套过，不能指望一套带另一套。
    domains = ["us", "cc"] if args.domain == "both" else [args.domain or "us"]
    results = []
    for dom in domains:
        if _ls is not None:
            with _ls.watch(port, "run_temu_auth(%s/%s)" % (store, dom),
                           log=lambda m: print(m)):
                r = atd.run_auth(port, store, domain=dom)
        else:
            r = atd.run_auth(port, store, domain=dom)
        results.append((dom, r))
        _log_run(store, "auth", r, verdict=("ok" if r.startswith("OK") else "fail"))
        print("[%s] %s: %s" % ("OK" if r.startswith("OK") else "FAIL", dom, r))
        if not r.startswith("OK"):
            # 一套失败就停：继续过下一套只会把窗口搅得更乱
            break
    result = results[-1][1] if results else "FAIL: 没跑任何域"
    verdict = "ok" if (len(results) == len(domains)
                       and all(r.startswith("OK") for _, r in results)) else "fail"
    # 授权成功后顺手再清一次：把本次流程可能留下的**空白授权页**收掉（治本的一半——
    # 残留就是这么一页页攒起来的；不清就等下次开窗被紫鸟还原回来继续拖慢 attach）。
    # ★ 2026-11-09：清障**可能把窗口关没**（实测把仅剩的真实页也关掉后窗口消失，
    #   下游接着连死端口）。这里清完必须复检端口，掉了就当场重开一次，别把锅甩给下一个任务。
    if verdict == "ok":
        try:
            import ziniao_store as _zs  # noqa: E402
            _cl = _zs.clean_dead_tabs(port=port, log=lambda x: print(x))
            if _cl.get("closed"):
                _log_run(store, "clean_tabs_after_auth", "关掉空页 %d" % len(_cl["closed"]))
            if _cl.get("window_alive") is False:
                print("[清障] ⚠ 授权后清障把窗口关没了 → 重开一次（否则下一个任务连死端口）")
                _m = _store_by_suffix(store) or {}
                try:
                    _zs.open_store(_m.get("ziniao_window_store_id"))
                except Exception as e:  # noqa: BLE001
                    print("[清障] 重开失败（%s）" % str(e)[:80])
        except Exception as e:  # noqa: BLE001
            print("[清障] 授权后清理跳过（%s）" % str(e)[:80])
    print("[%s] %s" % ("OK" if verdict == "ok" else "FAIL",
                       result if len(results) == 1 else
                       "；".join("%s=%s" % (d, r[:40]) for d, r in results)))
    return 0 if verdict == "ok" else 42


if __name__ == "__main__":
    raise SystemExit(main())
