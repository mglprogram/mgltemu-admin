# -*- coding: utf-8 -*-
"""run_temu_sales.py — TEMU 每日导出销量参数化入口（TEMU 美国站订单 Excel）。

用法:
    python scripts/power/run_temu_sales.py --store BHY --date 2026-08-27   # 全自动(查窗口+探测端口)
    python scripts/power/run_temu_sales.py --store BHY --dry-run
    python scripts/power/run_temu_sales.py --list-stores
    python scripts/power/run_temu_sales.py --scan

流程（用户 2026-08-28 监控固化）:
    TEMU订单页 → 筛订单时间(点日期两次) → 查询 → 每页500 → 勾选全选 →
    导出订单 → 弹窗确认导出 → 点<a>下载 → CDP注入 save_as 落盘。

工程控制:
    - 起点闸门: CDP 端口须可连(现场探测/传入), 失败 HARD_STOP(42)
    - 端口不写死: --store 从 ziniao_power_map.json 查窗口+进程探测, 或 --port
    - 数据外置: 店后缀/日期走参数; AI 只改 data/*.json
    - 日志: _logs/temu-sales/<日期>.jsonl
"""
import sys, io, os, json, argparse, subprocess
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# 迁移包自包含: 附带 scripts/lib(共享闸门库) 时优先用包内 lib, 免设 METHOD_SCRIPTS_ROOT
_INC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT") or os.path.join(os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from lib import preflight, zport  # noqa: E402
import temu_sales_driver as tsd  # noqa: E402
import ziniao_store as zs        # noqa: E402  # 清障步（关空白残留页）；窗口生命周期单点实现

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(WS, "每日导出销量")
MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ziniao_power_map.json")


def _log_run(action: str, scope: str, result, verdict: str = "ok", error=None) -> None:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    rec = {"ts": ts, "agent": "TEMU销量导出", "action": action, "scope": scope,
           "result": str(result)[:400], "verdict": verdict, "error": error}
    log_dir = os.path.join(WS, "_logs", "temu-sales")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


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

    为什么要前缀匹配（2026-09-16 实测）：TEMU 页面会改窗口标题，授权后标题变成
    `TM-BHY-YJF    |    卖家中心` / `TM-KinglamanSport    |    首页`，`-eq` 精确匹配就找不到窗口
    → 误报「未探测到 CDP 端口」。用 `<窗口名>*` 前缀匹配对标题漂移免疫（窗口名本身仍是前缀）。
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except Exception:
        return 0
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
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


def _default_yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _fname_prefix(day_s: str, store: str) -> str:
    y, m, d = day_s.split("-")
    return "%s.%s%s" % (str(int(m)), str(int(d)), store)


def _close_store_window(store_id: str, store: str = "") -> None:
    """TEMU 导出成功后关闭相应紫鸟店铺窗口（用户规则 2026-08-31：只限 TEMU）。

    走 ziniao-cli store close --id <storeId>（本地 ZClaw Bridge 关窗）。只在该店导出
    成功后被调用；导出失败不关窗（保留窗口便于排查/重跑）。关窗后登录态丢失属预期
    （下次跑前需 ziniao-cli store open 重新开窗，登录态可能需重新授权）。
    注意：ziniao-cli 为 .ps1 全局 shim，需经 powershell 调用（subprocess 直接
    CreateProcess 会 WinError 2）；成功提示「✓ 店铺已关闭」落在 stderr，故以
    returncode==0 为准、再排除显式失败标记。
    """
    if not store_id:
        return
    try:
        cmd = "ziniao-cli store close --id %s" % str(store_id)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        outs = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0
        if ok and ('"ok": false' in outs or '"ok":false' in outs.replace(" ", "") or "失败" in outs or "FAIL" in outs):
            ok = False
        if ok:
            print("[CLOSE] store=%s closed" % store_id)
        else:
            print("[CLOSE] store=%s close_failed: %s" % (store_id, outs.strip()[:150]))
        _log_run("close", "store=%s store_id=%s" % (store, store_id),
                 "closed" if ok else "close_failed", verdict="ok" if ok else "fail")
    except Exception as e:
        print("[CLOSE] store=%s close_error: %s" % (store_id, str(e)[:150]))
        _log_run("close", "store=%s store_id=%s" % (store, store_id), "close_error", verdict="fail")


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 每日导出销量: 订单 Excel")
    ap.add_argument("--port", type=int, default=None, help="紫鸟窗口 CDP 端口(现场探测/传入)")
    ap.add_argument("--store", default=None, help="店后缀(如 BHY; 从 ziniao_power_map.json 查窗口并探测端口)")
    ap.add_argument("--date", default=None, help="导出日期 YYYY-MM-DD(缺省=昨天 PT)")
    ap.add_argument("--out", default=None, help="输出目录(缺省 工作区/每日导出销量/<数据日期>/TEMU)")
    ap.add_argument("--dry-run", action="store_true", help="只打印要做的, 不操作浏览器")
    ap.add_argument("--scan", action="store_true", help="现场探测紫鸟 CDP 端口")
    ap.add_argument("--list-stores", action="store_true", help="列出 TEMU 店(后缀/窗口/storeId)")
    args = ap.parse_args()

    day_s = args.date or _default_yesterday()
    store = args.store or "销量"
    fname = _fname_prefix(day_s, store)
    # 输出目录：缺省 每日导出销量/<数据日期>/TEMU/（按日期+平台归档；与 TK 分开，勿混放）
    out = args.out or os.path.join(DEFAULT_OUT, day_s, "TEMU")

    if args.list_stores:
        for m in _load_store_map().get("mapping", []):
            if m.get("power_platform") == "TEMU" or m.get("power_type") == "本土店":
                sfx = m.get("export_suffix") or m["power_name"]
                print("%-10s window=%-20s storeId=%s" % (sfx, m["ziniao_window_name"], m["ziniao_window_store_id"]))
        return 0

    if args.scan:
        found = zport.find_cdp_port()
        if not found:
            msg = "未探测到任何 CDP 环境。先开紫鸟店窗口(紫鸟客户端须已启动)"
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        for e in found:
            print("port=%s env=%s tags=%s" % (e["port"], e["env"], ",".join(e["tags"])))
        return 0

    port = args.port
    m = None
    if port is None and store != "销量":
        m = _store_by_suffix(store)
        if m is None:
            msg = "未知店后缀 %s（可用 --list-stores 查）" % store
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        # 跑前自检（2026-09-28 事故写回）：**先问登录态、再探端口**。紫鸟客户端登出时端口必然探不到，
        # 只报"未探测到 CDP 端口"会把读者引向窗口问题（事故实测真因＝客户端登出 21 小时）。
        # 只有"明确未登录"拦（HARD STOP 42）；桥抖动/无法判定放行，按老路失败。
        if not args.dry_run:
            _login_ok, _login_why = zs.require_login(log=lambda s: print("[INFO] " + s))
            if not _login_ok:
                msg = "紫鸟登录态自检未过: %s" % _login_why
                print("[HARD STOP] " + msg, file=sys.stderr)
                return preflight.HARD_STOP_CODE
        port = _find_store_port(m["ziniao_window_name"])
        if not port:
            msg = "未探测到窗口 %s 的 CDP 端口（先开该店窗口/紫鸟客户端已启动）" % m["ziniao_window_name"]
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
    if args.dry_run:
        msg = "dry-run: export %s store=%s port=%s -> %s (fname=%s.xlsx 筛日期两次+查询+每页500+全选+导出+确认+下载)" % (day_s, store, port, out, fname)
        print("[OK] " + msg)
        _log_run("export_dry", "date=%s port=%s store=%s" % (day_s, port, store), msg)
        return 0

    # ---- 清障步（用户 2026-09-24：「跑脚本前先清卡死页」）：连浏览器**之前**关掉窗口里的空白残留页 ----
    # 为什么要它：残留空页会把 Playwright 的 browser-level attach 拖慢/拖死（RL-013），并让"按 URL 挑页"
    # 挑错。只清**探针证明是空页**的候选页；认证页（有内容）保留——那是"需要授权"的信号，不是垃圾。
    # best-effort：拿不到端口/探针失败都不影响本次导出。
    if port:
        try:
            _cl = zs.clean_dead_tabs(port=port, log=lambda m: print(m))
            _log_run("clean_tabs", "port=%s store=%s" % (port, store),
                     "空页 %d 关掉 %d 保留有内容 %d" % (len(_cl.get("dead") or []), len(_cl.get("closed") or []),
                                                        len(_cl.get("kept") or [])),
                     verdict="ok")
        except Exception as e:  # noqa: BLE001
            print("[清障] 跳过（%s）" % str(e)[:80])

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

    from datetime import date as _date
    day = _date.fromisoformat(day_s)
    result = tsd.export_daily_sales(port, day, out, base_name=fname)
    verdict = "ok" if result.startswith("OK") else "fail"
    _log_run("export", "date=%s port=%s out=%s store=%s" % (day_s, port, out, store), result,
             verdict=verdict, error=None if verdict == "ok" else result)
    print("[%s] %s" % ("OK" if verdict == "ok" else "FAIL", result))
    # 规则(用户 2026-08-31): TEMU 导出成功后关闭相应紫鸟店铺窗口(只限 TEMU 入口)
    if verdict == "ok" and m is not None:
        _close_store_window(m.get("ziniao_window_store_id"), store)
    return 0 if verdict == "ok" else 42


if __name__ == "__main__":
    raise SystemExit(main())
