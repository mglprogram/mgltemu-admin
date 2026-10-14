# -*- coding: utf-8 -*-
"""run_temu_merge.py — TEMU 每日导出销量 **非在线合并** 参数化入口。

把多天导出的同店 TEMU 订单 Excel，**每店合并成一个文件**（保留 32 列订单结构，
按数据日期上下拼接），供 @每日导出销量 agent 使用（导出后统一口径）。

用法:
    python scripts/power/run_temu_merge.py --dates 2026-08-29,2026-08-30 --stores BHY,BEL,KinglamanSport
    python scripts/power/run_temu_merge.py --dates 2026-08-29,2026-08-30            # stores 缺省=map全部TEMU店
    python scripts/power/run_temu_merge.py --dates 2026-08-29,2026-08-30 --dry-run
    python scripts/power/run_temu_merge.py --auto                  # 自动区间: 上次合并截止日+1 -> 最新数据日, >=2天按店合并
    python scripts/power/run_temu_merge.py --auto --dry-run
    python scripts/power/run_temu_merge.py --list-stores

自动区间(--auto)规则(2026-08-31 用户确认, 大小周/间隔不定场景, 与 run_tk_merge.py 对称):
    - 起点 = 该店在 合并/ 目录已有 <MM.DD>-<MM.DD><店>.xlsx 的最大末日 +1 天(末日年份按"最近年份"推断);
      无历史合并文件 -> 提示首次用 --dates, 不做合并。
    - 终点 = --date-end 或缺省自动取 每日导出销量/ 下最新 YYYY-MM-DD 目录(最新已导出数据, 不依赖时区)。
    - 区间内按"该店实际有源文件"的天合并; 仅 1 天 -> 跳过提示。
    - 导出每天 16:00 定时照旧, 与本合并入口无关。

设计（对齐 ai-python-methodology）:
    - 纯离线数据脚本(openpyxl), 不涉 CDP/网络, 无 Playwright/命名管道——受限沙箱也能跑
      (与 run_temu_sales.py 的 WinError5 无关)。
    - 店清单不写死: 从 ziniao_power_map.json 取 power_platform==TEMU 的 export_suffix。
    - 起点闸门 preflight.require_plot_env(modules=openpyxl, paths=源文件) 校验依赖+源文件存在。
    - 中文读写走 openpyxl / uio；一律不用裸 open、不用裸 print 直写中文。
    - 数据外置: 只改 map json / --dates / --stores 参数; AI 不碰脚本结构与 tests。

产出:
    每日导出销量/合并/<MM.DD>-<MM.DD><店>.xlsx   (每店一个, 32列, 表头+各天数据上下拼)
"""
import sys, io, os, json, re, argparse
from datetime import date as _date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# 迁移包自包含: 附带 scripts/lib(共享闸门库) 时优先用包内 lib, 免设 METHOD_SCRIPTS_ROOT
_INC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT") or os.path.join(os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from lib import preflight, uio  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SRC = os.path.join(WS, "每日导出销量")
DEFAULT_OUT = os.path.join(WS, "每日导出销量", "合并")
MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ziniao_power_map.json")


def _log_run(action: str, scope: str, result: str, verdict: str = "ok", error=None) -> None:
    ts = _date.today().isoformat()  # 兼容 stdlib, 避免依赖 tz
    rec = {"ts": ts, "agent": "TEMU销量合并", "action": action, "scope": scope,
           "result": str(result)[:400], "verdict": verdict, "error": error}
    log_dir = os.path.join(WS, "_logs", "temu-merge")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, ts.replace("-", "") + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def _temu_stores() -> list:
    """从 ziniao_power_map.json 取全部 TEMU 店(export_suffix), 数据外置, 不写死。"""
    m = uio.read_json(MAP, default={})
    out = []
    for e in m.get("mapping", []):
        if e.get("power_platform") == "TEMU":
            sfx = e.get("export_suffix") or e.get("power_name")
            if sfx:
                out.append(sfx)
    return out


def _fname_prefix(day_s: str) -> str:
    y, m, d = day_s.split("-")
    return "%s.%s" % (str(int(m)), str(int(d)))


def _src_for(store: str, day_s: str, base: str) -> str:
    """源文件路径: <base>/<date>/TEMU/<MM.DD><store>.xlsx"""
    return os.path.join(base, day_s, "TEMU", _fname_prefix(day_s) + store + ".xlsx")


_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MERGED_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})(.+?)\.xlsx$")


def _latest_data_date(base: str = DEFAULT_SRC) -> str | None:
    """扫描 每日导出销量/ 下 YYYY-MM-DD 格式目录，返回最大日期字符串；无则 None。"""
    best = None
    if not os.path.isdir(base):
        return None
    for name in os.listdir(base):
        if _DATE_DIR_RE.match(name) and os.path.isdir(os.path.join(base, name)):
            if best is None or name > best:
                best = name
    return best


def _last_merged_date(store: str, out: str, end: str) -> str | None:
    """从 合并/ 目录解析该店已合并到的最大末日(YYYY-MM-DD)。

    end=终点日期(用于年份推断)：解析出的末日 > end 视为上一年(覆盖跨年合并边缘)。
    """
    best = None
    if not os.path.isdir(out):
        return None
    end_d = _date.fromisoformat(end)
    for name in os.listdir(out):
        m = _MERGED_RE.match(name)
        if not m:
            continue
        _m1, _d1, m2, d2, suffix = m.groups()
        if suffix != store:
            continue
        try:
            d = _date(end_d.year, int(m2), int(d2))
        except ValueError:
            continue
        if d > end_d:
            d = _date(end_d.year - 1, int(m2), int(d2))
        if best is None or d > best:
            best = d
    return best.isoformat() if best else None


def _auto_dates(store: str, start: str, end: str, base: str = DEFAULT_SRC) -> list:
    """返回 [start, end] 区间内该店实际有源文件的日期(升序)。"""
    out = []
    d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    while d <= end_d:
        ds = d.isoformat()
        if os.path.isfile(_src_for(store, ds, base)):
            out.append(ds)
        d += timedelta(days=1)
    return out


def _read_sheet(path: str):
    """读单个导出 xlsx: 返回 (表头tuple, 数据行list[tuple])。表头一致校验在 merge 层做。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return (), []
    return tuple(rows[0]), list(rows[1:])


def merge_store(store: str, dates: list, base: str = DEFAULT_SRC, out: str = DEFAULT_OUT) -> str:
    """把 store 在 dates 各天的导出合并成 1 个文件。返回产出路径(或报错文本)。"""
    dates = sorted(set(dates))
    srcs = [_src_for(store, d, base) for d in dates]
    # 表头一致性校验(任一源文件表头与首文件不同 -> 停止, 不下错口径)
    header, first_rows = _read_sheet(srcs[0])
    if not header:
        raise ValueError("首个源文件无表头: %s" % srcs[0])
    for p in srcs[1:]:
        h, _ = _read_sheet(p)
        if h != header:
            raise ValueError("表头不一致 %s vs %s" % (srcs[0], p))

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sheet1"
    for i, v in enumerate(header, 1):
        ws.cell(row=1, column=i, value=v if isinstance(v, str) else str(v))
    r = 2
    total = 0
    for p in srcs:
        _, rows = _read_sheet(p)
        for row in rows:
            for i, v in enumerate(row, 1):
                ws.cell(row=r, column=i, value=v)
            r += 1
            total += 1

    prefix = _fname_prefix(dates[0]) + "-" + _fname_prefix(dates[-1])
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, "%s%s.xlsx" % (prefix, store))
    wb.save(dst)
    return "%s (rows=%d, cols=%d, files=%s)" % (dst, total, len(header), len(srcs))


def _run_auto(stores: list, date_end: str | None, out_arg: str | None, dry_run: bool) -> int:
    """--auto 自动区间合并：起点=上次合并末日+1，终点=--date-end 或最新数据日，>=2 天按店合并。"""
    out = out_arg or DEFAULT_OUT
    end = date_end or _latest_data_date(DEFAULT_SRC)
    if not end:
        msg = "每日导出销量 下无日期目录(YYYY-MM-DD)，无法自动确定区间"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE
    print("auto: end=%s out=%s stores=%s" % (end, out, stores))
    for s in stores:
        lm = _last_merged_date(s, out, end)
        if lm is None:
            msg = "无历史合并文件；首次请显式 --dates（自动区间从上次合并截止日+1 起算）"
            print("  [%s] %s" % (s, msg))
            continue
        start = (_date.fromisoformat(lm) + timedelta(days=1)).isoformat()
        dates = _auto_dates(s, start, end, DEFAULT_SRC)
        if dry_run:
            print("  %s start=%s dates=%s" % (s, start, dates))
            continue
        if len(dates) >= 2:
            try:
                res = merge_store(s, dates, DEFAULT_SRC, out)
                print("[OK] %s" % res)
                _log_run("merge_auto", "store=%s dates=%s" % (s, dates), res, "ok")
            except Exception as e:
                print("[FAIL] store=%s: %s" % (s, str(e)[:120]), file=sys.stderr)
                _log_run("merge_auto", "store=%s dates=%s" % (s, dates), str(e)[:120], "fail", error=str(e)[:200])
        elif len(dates) == 1:
            msg = "仅 1 天新数据(%s)，无需合并（如需单日请手动 --dates）" % dates[0]
            print("  [%s] %s" % (s, msg))
        else:
            msg = "区间(%s..%s)内无该店数据" % (start, end)
            print("  [%s] %s" % (s, msg))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 每店多日销量合并(保留32列)")
    ap.add_argument("--dates", default=None, help="数据日期, 逗号分隔 YYYY-MM-DD, 如 2026-08-29,2026-08-30")
    ap.add_argument("--stores", default=None, help="店后缀, 逗号分隔(缺省=map全部TEMU店)")
    ap.add_argument("--out", default=None, help="输出目录(缺省 每日导出销量/合并)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-stores", action="store_true")
    ap.add_argument("--auto", action="store_true", help="自动区间: 上次合并截止日+1 -> 最新数据日, >=2天按店合并")
    ap.add_argument("--date-end", default=None, help="自动区间终点(YYYY-MM-DD, 缺省=最新数据日期目录)")
    args = ap.parse_args()

    if args.list_stores:
        for s in _temu_stores():
            print(s)
        return 0

    stores = ([s.strip() for s in args.stores.split(",") if s.strip()]
              if args.stores else _temu_stores())
    if not stores:
        msg = "无 TEMU 店(检查 data/ziniao_power_map.json 是否有 power_platform==TEMU)"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE

    if args.auto:
        return _run_auto(stores, args.date_end, args.out, args.dry_run)

    if not args.dates:
        msg = "缺 --dates <YYYY-MM-DD,...>"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

    out = args.out or DEFAULT_OUT
    if args.dry_run:
        for s in stores:
            for d in dates:
                print("  %s -> %s" % (s, _src_for(s, d, DEFAULT_SRC)))
        print("[OK] dry-run: stores=%s dates=%s out=%s" % (stores, dates, out))
        _log_run("merge_dry", "stores=%s dates=%s" % (stores, dates), "dry-run")
        return 0

    # 起点闸门: 依赖 + 源文件存在
    try:
        paths = []
        for s in stores:
            for d in dates:
                paths.append(_src_for(s, d, DEFAULT_SRC))
        preflight.require_plot_env(modules=("openpyxl",), paths=tuple(paths))
    except preflight.PreflightError as e:
        print("[HARD STOP] " + str(e), file=sys.stderr)
        return preflight.HARD_STOP_CODE

    ok, failed = True, []
    for s in stores:
        try:
            res = merge_store(s, dates, DEFAULT_SRC, out)
            print("[OK] %s" % res)
            _log_run("merge", "store=%s dates=%s" % (s, dates), res, "ok")
        except Exception as e:
            ok = False
            failed.append("%s: %s" % (s, str(e)[:120]))
            print("[FAIL] store=%s: %s" % (s, str(e)[:120]), file=sys.stderr)
            _log_run("merge", "store=%s dates=%s" % (s, dates), str(e)[:120], "fail", error=str(e)[:200])
    return 0 if ok else 42


if __name__ == "__main__":
    raise SystemExit(main())
