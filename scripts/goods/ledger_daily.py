#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_daily.py — 从**十部半托管库存管理表**读「最近7天日均」，供领星总库存映射。

用户 2026-11-11 定案（替代原来从 warehouse-probe 看板抓）：
    在 `D:\\together\\Inventory table\\` 目录里取**最新**的那份
    `十部半托管-库存管理表*.xlsx`，读 `总销售表` 的 MSKU → `最近7天日均`，
    按「款号-套系-尺码」把各店铺各平台**相加**，映射到领星总库存。

关键口径（实测 2026-11-10 文件）
------------------------------
`总销售表` 用 `MSKU` 列做键，形式 `店铺前缀-款号-套系-尺码`：

    TM-BH-202504-5SetB-S     TM-BEL-…   TM-CH-…
    SHEIN-BS-…               SHEIN-BAN-… SHEIN-BD-…
    TK-TC-…                  TK-TH-…    TK-TAE-…   TK-TSA-…

同一个「款号-套系-尺码」会有 **10 行**（10 个店铺），把这 10 行的
`最近7天日均` 加起来，正好等于看板「按小款号」合计（80），
所以这里**逐行相加**，不是取最大/取第一个。

⚠ 不要用 `总库存表` 的「7天日均」：那张表只在库存行里出现（款已在仓的），
  不少款没有行，会漏；而总销售表是全量。

用法
----
    python scripts\\goods\\ledger_daily.py --list          # 只看会选哪个文件 + 抽样
    python scripts\\goods\\ledger_daily.py --check 202504  # 抽查某款
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from datetime import datetime
from pathlib import Path

#: 台账目录（用户指定；文件由人每天下载/更新到这里）
LEDGER_DIR = Path(r"D:\together\Inventory table")
#: 文件名模式：十部半托管-库存管理表2026.11.10.xlsx
LEDGER_GLOB = "十部半托管-库存管理表*.xlsx"
#: 用哪个 sheet / 哪几列
SHEET = "总销售表"
COL_MSKU = "MSKU"
#: 日均三列（用户 2026-11-11：除了 7 天，还要 3 天 / 14 天，分别放左右两侧且默认隐藏）
COL_DAILY = "最近7天日均"
#: {输出键: 台账列名}
DAILY_COLS = {
    "daily3": "最近3天日均",
    "daily": "最近7天日均",
    "daily14": "最近14天日均",
}

#: MSKU 前缀 → 店铺（只用于日志展示，逻辑不依赖它）
SHOP_PREFIXES = ("TM-BH", "TM-BEL", "TM-CH", "SHEIN-BS", "SHEIN-BAN", "SHEIN-BD",
                 "TK-TC", "TK-TH", "TK-TAE", "TK-TSA")

__all__ = ["latest_file", "read_msku_daily", "aggregate", "probe_daily_merged",
           "daily_for", "MAX_AGE_DAYS"]


#: 台账超过多少天没更新就告警（每天都会更新，>3 天说明人没下载）
MAX_AGE_DAYS = 3


def _file_date(p: Path) -> datetime | None:
    """从文件名里抠日期（2026.11.10），抠不出就用 mtime。"""
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", p.name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        return None


def latest_file(directory: Path = LEDGER_DIR, log=print) -> Path | None:
    """目录里**最新**的那份十部库存表（按文件名日期，其次 mtime）。

    为什么按文件名日期而不是 mtime：文件可能被重新拷进来（mtime 变新但内容是旧的），
    而文件名里的日期是下载当时生成的，更可信。
    """
    files = [Path(p) for p in glob.glob(str(directory / LEDGER_GLOB))]
    files = [p for p in files if not p.name.startswith("~$")]  # Excel 临时锁文件
    if not files:
        log("[ledger] %s 里没有 %s" % (directory, LEDGER_GLOB))
        return None
    files.sort(key=lambda p: (_file_date(p) or datetime.min, p.name), reverse=True)
    newest = files[0]
    age = (datetime.now() - (_file_date(newest) or datetime.now())).days
    log("[ledger] 选中最新的台账：%s（%+d 天）" % (newest.name, -age))
    if age > MAX_AGE_DAYS:
        log("[ledger] !! 台账已 %d 天没更新（>%d 天），日均可能是旧的" % (age, MAX_AGE_DAYS))
    return newest


def _norm(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def read_msku_daily(path: Path, log=print) -> dict:
    """`{MSKU: {daily3, daily, daily14}}`（原始小数，未取整）。

    三列缺任何一列都不算致命：缺的那列给 `None`，其它照常读 ——
    以后台账改列名/拆表也不会整份失效。
    """
    try:
        import openpyxl  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log("[ledger] openpyxl 不可用：%s" % str(e)[:120])
        return {}
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        log("[ledger] 打不开 %s：%s" % (path.name, str(e)[:140]))
        return {}
    try:
        if SHEET not in wb.sheetnames:
            log("[ledger] %s 没有 sheet「%s」（现有：%s）" % (path.name, SHEET, wb.sheetnames[:6]))
            return {}
        ws = wb[SHEET]
        rows = ws.iter_rows(min_row=1, values_only=True)
        head = [("" if v is None else str(v).strip()) for v in next(rows)]
        if COL_MSKU not in head:
            log("[ledger] 表头变了，找不到 %s：%s" % (COL_MSKU, head[:16]))
            return {}
        i_m = head.index(COL_MSKU)
        # {输出键: 列下标}，找不到的列直接不参与
        idx = {}
        for out_key, col_name in DAILY_COLS.items():
            if col_name in head:
                idx[out_key] = head.index(col_name)
            else:
                log("[ledger] 表里没有「%s」列（%s 将留空）" % (col_name, out_key))
        if not idx:
            log("[ledger] 三列日均一个都没找到：%s" % head[:16])
            return {}
        need = max([i_m] + list(idx.values()))
        out: dict[str, dict] = {}
        n = 0
        for r in rows:
            if not r or len(r) <= need:
                continue
            m = r[i_m]
            if not m:
                continue
            m = str(m).strip()
            out[m] = {k: (_norm(r[i]) if i < len(r) else None) for k, i in idx.items()}
            n += 1
        log("[ledger] %s / %s：%d 个 MSKU（日均列：%s）"
            % (path.name, SHEET, n, "、".join(idx)))
        return out
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass


#: 店铺前缀正则：MSKU = 前缀-款号-套系-尺码
_PREFIX_RE = re.compile(r"^(?:TM-BEL|TM-BH|TM-CH|SHEIN-BAN|SHEIN-BS|SHEIN-BD|"
                        r"TK-TAE|TK-TSA|TK-TC|TK-TH)-", re.I)


def aggregate(msku_daily: dict, log=print) -> dict:
    """`{款号-套系-尺码: {daily3, daily, daily14}}` —— 同一款的所有店铺行**按列相加**。

    `MSKU` 去掉店铺前缀就是「款号-套系-尺码」，与领星/面板的键完全同一套写法
    （`202504-5SetB-S`），所以不需要再做解析。

    ⚠ 只累加**有值**的（`None` = 台账没这列/这行没填），否则会把缺失当 0 稀释分母。
       如果某个键三列都是 None，就整个不输出（页面显示 —）。
    """
    keys = list(DAILY_COLS)
    acc: dict[str, dict] = {}
    shops_seen: dict[str, set] = {}
    for msku, vals in msku_daily.items():
        key = _PREFIX_RE.sub("", msku).strip()
        if not key:
            continue
        a = acc.setdefault(key, {k: None for k in keys})
        for k in keys:
            v = vals.get(k) if isinstance(vals, dict) else None
            if v is None:
                continue
            a[k] = (a[k] or 0.0) + _norm(v)
        shops_seen.setdefault(key, set()).add(msku[: len(msku) - len(key)].rstrip("-"))
    out = {k: v for k, v in acc.items() if any(x is not None for x in v.values())}
    multi = sum(1 for k, s in shops_seen.items() if len(s) > 1)
    log("[ledger] 聚合出 %d 个「款号-套系-尺码」（其中 %d 个跨多个店铺相加）"
        % (len(out), multi))
    return out


def probe_daily_merged(path: Path | None = None, log=print) -> dict:
    """对外主入口：`{款号-套系-尺码: {daily3, daily, daily14}}`（**保留原始小数**）。

    保留小数是有意的：可售天数要算比值，取整会放大误差；
    页面上显示时再决定怎么格式化（当前页面 `toFixed(1)`）。
    """
    p = path or latest_file(log=log)
    if p is None:
        return {}
    raw = read_msku_daily(p, log=log)
    if not raw:
        return {}
    agg = aggregate(raw, log=log)
    return {
        k: {kk: (None if vv is None else round(vv, 4)) for kk, vv in v.items()}
        for k, v in agg.items()
    }


def daily_for(daily_map: dict, code, variant, size):
    """按 款号/套系/尺码 查三列日均 → `{daily3, daily, daily14}`（或 None）。"""
    if not daily_map:
        return None
    key = "-".join(str(x) for x in (code, variant, size) if x)
    if key in daily_map:
        return daily_map[key]
    low = key.lower()
    for k, v in daily_map.items():
        if k.lower() == low:
            return v
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="十部库存表 → 3/7/14 天日均（按款号-套系-尺码）")
    ap.add_argument("--list", action="store_true", help="只打印选中的文件与抽样")
    ap.add_argument("--check", default="", help="抽查包含该字符串的键")
    ap.add_argument("--file", default="", help="指定文件（默认目录里最新的）")
    args = ap.parse_args(argv)

    def fmt(v):
        return "—" if v is None else "%.3f" % v

    path = Path(args.file) if args.file else None
    m = probe_daily_merged(path)
    if not m:
        return 1
    if args.check:
        hit = {k: v for k, v in m.items() if args.check.lower() in k.lower()}
        for k in sorted(hit):
            print("  %-24s 3天 %s / 7天 %s / 14天 %s"
                  % (k, fmt(hit[k].get("daily3")), fmt(hit[k].get("daily")),
                     fmt(hit[k].get("daily14"))))
        print("命中 %d 个" % len(hit))
    else:
        for k in list(sorted(m))[:12]:
            print("  %-24s 3天 %s / 7天 %s / 14天 %s"
                  % (k, fmt(m[k].get("daily3")), fmt(m[k].get("daily")),
                     fmt(m[k].get("daily14"))))
        print("共 %d 个键" % len(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
