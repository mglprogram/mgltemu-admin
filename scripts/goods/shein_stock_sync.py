# -*- coding: utf-8 -*-
"""shein_stock_sync.py — SHEIN 按「领星可售天数」双向同步库存。

规则（用户 2026-11-11 确认）：
    可售天数 = 仓内总库存（恒信可用 + 帕沃尔加州3 + Kansas）÷ 7天日均
    · 可售天数 >= 3  → 目标 = ceil(7天日均 × 3)，只加不减：ADD(目标 - 现有)，现有>=目标则不动
    · 可售天数 <  3  → 清零：OVERWRITE 0
    · 7天日均 = 0 或面板查不到该款-尺码 → 不处理（列进 unreachable）

数据源（先刷再算）：
    fetch_pw_stock.py     → pw-stock.json（帕沃尔加州3 + Kansas）
    fetch_stock_track.py  → stock-track.json（领星 LM739 + 并入帕沃尔）

用法：
    # 预览（默认，不提交）
    python scripts\\goods\\shein_stock_sync.py --export "<商家库存导出.xlsx>" --store BAN

    # 真提交（提交前自动再刷一次数据，提交后自动回读）
    python scripts\\goods\\shein_stock_sync.py --export "..." --store BAN --apply

    # 不刷数据 / 只刷领星
    ... --no-refresh | --skip-pw-refresh
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from statistics import pstdev

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import shein_stock_api as api  # noqa: E402
import shein_zero_plan as zp   # noqa: E402

STOCK_TRACK = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"


def refresh(skip_pw: bool = False, refresh_track: bool = False) -> None:
    """刷库存源（默认只刷这两个，够「领星总库存」面板用）：

        ① fetch_pw_stock.py      → pw-stock.json        帕沃尔 加州3 + Kansas
        ② fetch_linxing_stock.py → linxing-stock.json   领星 LM739 恒信（可用/在途/7天日均）

    ⚠ 九部（TTSA-9）/ 十部（warehouse-probe）两个外观看板**有专门的定时任务在更新**，
      不归本脚本管，所以 ③ fetch_stock_track.py 默认**不跑**；只有显式
      `--refresh-track` 才会顺带重抓一次（它同时会覆盖 stock-track.json）。
      14 个跟踪款号的原值直接读现有的 stock-track.json。
    """
    steps = []
    if not skip_pw:
        steps.append(("帕沃尔", [str(ROOT / "scripts" / "goods" / "fetch_pw_stock.py")]))
    steps.append(("领星明细", [str(ROOT / "scripts" / "goods" / "fetch_linxing_stock.py")]))
    if refresh_track:
        steps.append(("库存跟踪合并(可选)", [str(ROOT / "scripts" / "goods" / "fetch_stock_track.py")]))
    for name, cmd in steps:
        print("[refresh] %s ← %s" % (name, " ".join(cmd)), flush=True)
        t0 = time.time()
        rc = subprocess.call([sys.executable] + cmd)
        print("[refresh] %s 退出码 %s，%.0fs" % (name, rc, time.time() - t0), flush=True)
        if rc != 0:
            print("[refresh] ⚠ %s 刷新失败，继续（数据可能是旧的）" % name, flush=True)


def data_ages() -> str:
    """打印三个数据文件的生成时间，便于判断口径新旧。"""
    from datetime import datetime
    pub = ROOT / "temu-admin" / "public" / "data"
    out = []
    for name, key in (("linxing-stock.json", "generatedAt"), ("pw-stock.json", "generatedAt"),
                      ("stock-track.json", "generatedAt")):
        try:
            d = json.loads((pub / name).read_text(encoding="utf-8"))
            ts = str(d.get(key) or "?")
            age = ""
            try:
                dt = datetime.strptime(ts[:19].replace("/", "-"), "%Y-%m-%d %H:%M:%S")
                age = "（%.1fh 前）" % ((datetime.now() - dt).total_seconds() / 3600.0)
            except Exception:
                pass
            out.append("%s=%s%s" % (name.replace(".json", ""), ts, age))
        except Exception:
            out.append("%s=缺失" % name)
    return " | ".join(out)


def load_panel(pub_dir: Path = None) -> dict:
    """全部款-尺码，口径与看板「库存跟踪 → 领星明细总库存」完全一致：

        总库存 = 恒信可用（linxing-stock.json，LM739 USW01）
               + 帕沃尔 加州3 + 帕沃尔 Kansas（pw-stock.json）
        7天日均 = linxing-stock.json 的 daily（= 页面「7天日均」列）
        可售天数 = 总库存 ÷ 7天日均

    key = (款号, 套系, 尺码) 全小写；覆盖 38 个款号 / 800+ 个款-尺码，
    **不再局限于 stock-track.json 里那 14 个跟踪款号**（2026-11-11 修）。
    """
    import fetch_stock_track as fst

    pub = Path(pub_dir or (ROOT / "temu-admin" / "public" / "data"))
    lx = json.loads((pub / "linxing-stock.json").read_text(encoding="utf-8"))
    pw = json.loads((pub / "pw-stock.json").read_text(encoding="utf-8"))

    panel: dict[tuple, dict] = {}

    def rec(k):
        return panel.setdefault(k, dict(lxAvail=0.0, lxTotal=0.0, transit=0.0, daily=None,
                                        daily3=None, daily14=None,
                                        pw3=0.0, pwk=0.0, pwTransit=0.0))

    for it in lx.get("items") or []:
        k = (str(it.get("code") or "").lower(), str(it.get("variant") or "").lower(),
             str(it.get("size") or "").lower())
        if not all(k):
            continue
        r = rec(k)
        r["lxAvail"] += fst.num(it.get("avail"))
        r["lxTotal"] += fst.num(it.get("total"))
        r["transit"] += fst.num(it.get("transit"))
        # 面板「3天/7天/14天日均」三列（2026-11-11 用户在看板加的）
        for fld, name in (("daily3", "daily3"), ("daily", "daily"), ("daily14", "daily14")):
            d = it.get(fld)
            if d is not None:
                r[name] = max(r[name] or 0.0, fst.num(d))

    for it in pw.get("items") or []:
        wh = str(it.get("wh") or "")
        if wh not in ("加州3仓", "kansas仓"):
            continue
        parsed = fst.split_sku_any(str(it.get("sku") or "")) or \
            fst.split_sku(str(it.get("name") or ""))
        if not parsed:
            continue
        code, variant, size = parsed
        k = (str(code).lower(), str(variant or "").lower(), str(size or "").lower())
        r = rec(k)
        stock = fst.num(it.get("stock"))
        r["pwTransit"] += fst.num(it.get("transit"))
        if wh == "加州3仓":
            r["pw3"] += stock
        else:
            r["pwk"] += stock

    for r in panel.values():
        r["total"] = r["lxAvail"] + r["pw3"] + r["pwk"]
        r["daily"] = r["daily"] if (r["daily"] or 0) > 0 else 0.0
        r["days"] = round(r["total"] / r["daily"], 2) if r["daily"] > 0 else None
        r["transitAll"] = r["transit"] + r["pwTransit"]

    # stock-track.json 只做**兜底**：新鲜数据（linxing-stock + pw-stock）里没有的
    # 「款-套系-尺码」才用它。
    #
    # ⚠ 2026-11-12 用户报「203311-4SetA 后两个码没库存 / 数字对不上」后查出的 bug：
    #   原来这里是**无条件覆盖**，把看板「库存跟踪」（14 个跟踪款号，定时任务 05:40 才刷）
    #   的旧快照盖在刚刷新的领星/帕沃尔上 ⇒ 203311-4SetA 的 L/M/S 被写成
    #   68/226/58（= 旧快照 avail×2），而领星实际可用是 0、帕沃尔只有 34/113/27
    #   ⇒ 仓内虚高，既不该清零的没清、也可能多挂货。
    #   现在改成「新鲜优先，只在缺 key 时用快照」。
    try:
        st = json.loads((pub / "stock-track.json").read_text(encoding="utf-8"))
        for _, src in (st.get("sources") or {}).items():
            for style in src.get("styles") or []:
                for v in style.get("variants") or []:
                    for row in v.get("rows") or []:
                        code = str(row.get("code") or "").lower()
                        size = str(row.get("size") or "").lower()
                        var = str(v.get("variant") or "").lower()
                        if not code or not size:
                            continue
                        k = (code, var, size)
                        if k in panel:
                            continue                    # 有新鲜数据 → 不用旧快照
                        lx_total = row.get("lxTotal")
                        total = fst.num(row.get("stock") if lx_total is None else lx_total)
                        daily = fst.num(row.get("daily"))
                        panel[k] = dict(lxAvail=fst.num(row.get("lxAvail")), lxTotal=total,
                                        transit=fst.num(row.get("transit")), daily=daily,
                                        daily3=None, daily14=None,
                                        pw3=0.0, pwk=0.0, pwTransit=0.0, total=total,
                                        transitAll=fst.num(row.get("transit")),
                                        days=round(total / daily, 2) if daily > 0 else None,
                                        src="stock-track")
    except Exception as e:
        print("  [warn] 读 stock-track.json 失败，全部用 linxing+pw 推算：%s" % e)
    return panel


def load_ledger(xlsx: str, store: str, sheet: str = "总销售表") -> dict:
    """读库存管理表 总销售表 → 每个「款号-套系-尺码」的三档日均 + 其中该店自己的日均。

    返回 {(code, variant, size): dict(sum3, sum7, sum14, store3, store7, store14)}，
    其中 sum* = 全渠道相加（= 看板口径），store* = 只取 `SHEIN-<store>-` 那一行。
    σ_d 用 (sum3, sum7, sum14) 的离散度估。
    """
    try:
        import openpyxl
    except ImportError:
        return {}
    p = Path(xlsx)
    if not p.exists():
        print("[ledger] 文件不存在：%s" % p)
        return {}
    try:
        from ledger_daily import _PREFIX_RE  # noqa: PLC0415
    except Exception:
        _PREFIX_RE = None
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
    it = ws.iter_rows(min_row=1, values_only=True)
    hdr = [str(x or "").strip() for x in next(it)]
    need = ["MSKU", "最近3天日均", "最近7天日均", "最近14天日均"]
    if not all(h in hdr for h in need):
        print("[ledger] 表头缺列，需要 %s；实际 %s" % (need, hdr[:10]))
        return {}
    iM, i3, i7, i14 = (hdr.index(h) for h in need)
    store_pfx = "SHEIN-%s-" % store.upper()

    def num(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    out: dict[tuple, dict] = {}
    for r in it:
        if not r or not r[iM]:
            continue
        msku = str(r[iM])
        if _PREFIX_RE is not None:
            body = _PREFIX_RE.sub("", msku).strip()
        else:
            body = "-".join(msku.split("-")[2:]) if msku.upper().startswith("SHEIN-") else \
                "-".join(msku.split("-")[1:])
        if not body:
            continue
        # 口径必须与面板一致（N204003 / H103902 这类字母前缀在 norm_key 里归一）
        k = norm_key(msku)
        if not k:
            continue
        rec = out.setdefault(k, dict(sum3=0.0, sum7=0.0, sum14=0.0,
                                     store3=0.0, store7=0.0, store14=0.0))
        d3, d7, d14 = num(r[i3]), num(r[i7]), num(r[i14])
        rec["sum3"] += d3
        rec["sum7"] += d7
        rec["sum14"] += d14
        if msku.upper().startswith(store_pfx):
            rec["store3"] += d3
            rec["store7"] += d7
            rec["store14"] += d14
    print("[ledger] %s → %d 个「款-套系-尺码」，其中该店 SHEIN-%s 有销量的 %d 个"
          % (p.name, len(out), store.upper(),
             sum(1 for v in out.values() if v["store7"] > 0)))
    return out


RULES_PATH = ROOT / "config" / "shein_stock_rules.json"


def _style_key(style: str):
    """'2038-4SetA' / 'H103902-8B' → (code, variant) 小写；面板 code 不带前缀字母。"""
    s = str(style).strip().lower()
    if "-" not in s:
        return None
    code, var = s.split("-", 1)
    return code.lstrip("abcdefghijklmnopqrstuvwxyz"), var


def rule_partners(store: str, rules_path: Path = None) -> dict:
    """替代规则 → {被替代款 key: [对方款 key]}（双向）。key=(code, variant) 小写。"""
    p = Path(rules_path or RULES_PATH)
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    seg = (cfg.get("by_store") or {}).get(str(store).upper()) or {}
    out = {}
    for a, b in seg.get("equivalents") or []:
        ka, kb = _style_key(a), _style_key(b)
        if not ka or not kb:
            continue
        out.setdefault(ka, []).append(kb)
        out.setdefault(kb, []).append(ka)
    return out


def apply_rules(panel: dict, store: str, rules_path: Path = None) -> dict:
    """套用「替代 / 拆解」规则，给缺货的款补出可用库存（只影响仓内 total）。

    · equivalents：本款某尺码仓内 <=0 时，用对方的同尺码库存（**不相加**，避免重复计数）
    · recipes    ：目标款某尺码可得 = floor(min(组件同尺码库存 / 用量))
    · 取 max(本款, 规则推算)，并把来源写进 rule_src，计划表里能看到
    """
    p = Path(rules_path or RULES_PATH)
    if not p.exists():
        return panel
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("[rules] 规则文件解析失败：%s" % e)
        return panel
    seg = (cfg.get("by_store") or {}).get(str(store).upper()) or {}
    eq = seg.get("equivalents") or []
    rc = seg.get("recipes") or []
    if not eq and not rc:
        return panel

    by_style: dict[tuple, dict] = {}
    for (code, var, size), v in panel.items():
        by_style.setdefault((code, var), {})[size] = (v.get("total") or 0.0)

    def style_stock(style):
        k = _style_key(style)
        if not k:
            return {}
        if k in by_style:
            return by_style[k]
        for kk in by_style:
            if kk[1] == k[1] and kk[0].lstrip("abcdefghijklmnopqrstuvwxyz") == k[0]:
                return by_style[kk]
        return {}

    touched = []

    def bump(key, size, qty, src):
        rec = panel.get((key[0], key[1], size))
        if rec is None:
            rec = dict(lxAvail=0.0, lxTotal=0.0, transit=0.0, daily=0.0, daily3=None,
                       daily14=None, pw3=0.0, pwk=0.0, pwTransit=0.0, total=0.0,
                       transitAll=0.0, days=None, src="rule")
            panel[(key[0], key[1], size)] = rec
        if qty > (rec.get("total") or 0):
            rec["total"] = float(qty)
            rec["rule_src"] = src
            if (rec.get("daily") or 0) > 0:
                rec["days"] = round(rec["total"] / rec["daily"], 2)
            touched.append(("%s-%s" % (key[0], key[1]), size, qty, src))

    # ① 替代（双向）
    for a, b in eq:
        ka, kb = _style_key(a), _style_key(b)
        if not ka or not kb:
            continue
        sa, sb = style_stock(a), style_stock(b)
        for size in set(sa) | set(sb):
            ta, tb = sa.get(size, 0.0), sb.get(size, 0.0)
            if ta <= 0 < tb:
                bump(ka, size, tb, "替代 %s→%s" % (b, a))
            elif tb <= 0 < ta:
                bump(kb, size, ta, "替代 %s→%s" % (a, b))

    # ② 拆解
    for r in rc:
        tgt, comps = r.get("target"), r.get("components") or []
        kt = _style_key(tgt)
        if not kt or not comps:
            continue
        st = [style_stock(c.get("style")) for c in comps]
        sizes = set()
        for s in st:
            sizes |= set(s)
        for size in sizes:
            avail = []
            for s, c in zip(st, comps):
                q = max(1.0, float(c.get("qty") or 1))
                avail.append(s.get(size, 0.0) / q)
            if not avail:
                continue
            derived = int(min(avail))
            if derived > 0:
                desc = "拆解 " + "+".join("%s×%g" % (c.get("style"), float(c.get("qty") or 1)) for c in comps)
                bump(kt, size, derived, desc)

    if touched:
        print("[rules] %s 替代/拆解：补出 %d 个「款-尺码」的可用库存" % (store.upper(), len(touched)))
        for t in touched[:12]:
            print("        %-18s %-4s → %-6d（%s）" % (t[0], t[1].upper(), t[2], t[3]))
    else:
        print("[rules] %s 有规则，但本轮没有需要补的「款-尺码」" % store.upper())
    return panel


def load_store_daily(xlsx: str, store: str, sheet: str = "总销售表") -> dict:
    """从「十部半托管-库存管理表」的 总销售表 取**该店自己**的 最近7天日均。

    该表每行是一个渠道 MSKU（`TM-BH-…` / `SHEIN-BD-…` / `TK-…`），
    「最近7天日均」是**按渠道**的值；所有渠道相加 = 看板上的汇总日均。
    这里只取 `SHEIN-<store>-<款号-套系-尺码>` 那一行。

    key = (款号, 套系, 尺码) 全小写 → daily（该店口径）。
    """
    try:
        import openpyxl
    except ImportError:
        return {}
    p = Path(xlsx)
    if not p.exists():
        print("[store-daily] 文件不存在：%s" % p)
        return {}
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
    it = ws.iter_rows(min_row=1, values_only=True)
    hdr = [str(x or "").strip() for x in next(it)]
    if "MSKU" not in hdr or "最近7天日均" not in hdr:
        print("[store-daily] 表头缺 MSKU/最近7天日均：%s" % hdr[:12])
        return {}
    i_msku, i7 = hdr.index("MSKU"), hdr.index("最近7天日均")
    prefix = "SHEIN-%s-" % store.upper()
    out = {}
    for r in it:
        if not r or not r[i_msku]:
            continue
        m = str(r[i_msku])
        if not m.upper().startswith(prefix):
            continue
        body = m[len(prefix):].lower()      # 204203-4seta-l
        parts = body.split("-")
        if len(parts) < 2:
            continue
        try:
            v = float(r[i7] or 0)
        except (TypeError, ValueError):
            v = 0.0
        k = norm_key(m)
        if not k:
            continue
        out[k] = round(v, 3)
    print("[store-daily] %s 取到 %d 个 SHEIN-%s 渠道的日均" % (p.name, len(out), store.upper()))
    return out


def parse_bands(spec: str):
    """'3:1,7:2,inf:3' → ((3.0,1.0),(7.0,2.0),(inf,3.0))"""
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        hi_s, m_s = part.split(":")
        hi = float("inf") if hi_s.lower() in ("inf", "infinity", "∞") else float(hi_s)
        out.append((hi, float(m_s)))
    out.sort(key=lambda x: x[0])
    return tuple(out)


def build(export: str, store: str, days_target: float, gap: float = 50.0,
          fallback_days: float = 1.0,
          bands=((3.0, 1.0), (7.0, 2.0), (float("inf"), 3.0)),
          safety_days: float = 1.0, min_units: float = 5.0, cap: bool = True,
          share: float = 1.0, alert_days: float = 2.0,
          store_daily_xlsx: str = "", store_daily_sheet: str = "总销售表",
          policy: str = "v1", lead_days: float = 45.0, local_days: float = 3.0,
          z_value: float = 1.65, dos=(7.0, 3.0, 1.0), abc_cuts=(0.2, 0.5),
          min_listing: float = 5.0, trend: bool = True, trend_up: float = 1.3,
          trend_down: float = 0.7, trend_boost: float = 1.4, trend_cut: float = 0.7,
          use_rules: bool = True, rules_path: str = "", fallback: bool = True,
          force_zero=()):
    """分档 + 防超卖封顶 + 渠道份额 + 补货预警。

    分档（用户 2026-11-11 口径）：
        可售 <= days_target(1)       → 清零
        1 < 可售 <= 3               → 目标 = ceil(日均 × 1)
        3 < 可售 <= 7               → 目标 = ceil(日均 × 2)
        可售 > 7                    → 目标 = ceil(日均 × 3)
        任一档下「仓内 - N天量 <= gap(50)」 → 目标降成 ceil(日均 × fallback_days(1))

    ★ 防超卖（cap=True，默认开）：
        安全库存 = max(日均 × safety_days(1), min_units(5))
        可支配   = 仓内总库存 × share(1.0)
        上架上限 = 可支配 - 安全库存
        目标 = min(档位目标, 上架上限)   ← 永远不能超过仓里真能发的货

    ★ 渠道份额（share）：
        同一批帕沃尔库存还供 TEMU / TikTok，SHEIN 只该拿一部分。
        share=1.0 → 不分配（默认）；share=0.5 → SHEIN 最多占一半。

    ★ 补货预警（alert_days）：
        可售 < alert_days 且 恒信在途 + 帕沃尔在途 = 0  → 「该补货」
        （无在途 = 断了补给，再怎么改 SHEIN 库存也撑不住）

    背景：204605-3BHP-L 仓内只有 19 件、日均 17.3，档位目标是 17，
    相当于把仓里 90% 的货一次挂到 SHEIN 上 → 封顶后 = 19×1 - max(17.3,5) ≈ 1 件。

    日均 = 面板「7天日均」列（全渠道汇总）。永远只加不减：现有 >= 目标就不动。

    ★ 该店日均（store_daily_xlsx）：
        给了库存管理表的路径时，**目标量改用该店自己的日均**
        （总销售表里 `SHEIN-<store>-<款>-<尺码>` 的「最近7天日均」），
        而「可售天数 / 分档」仍用全渠道汇总日均（那才是仓被卖空的速度）。
        两者分开的理由：仓里的货是所有渠道一起卖的，但 SHEIN 这条链接只按自己的销量卖。
    """
    exp = zp.load_export(export)
    panel = load_panel()
    if use_rules:
        panel = apply_rules(panel, store, rules_path)
    sdaily = load_store_daily(store_daily_xlsx, store, store_daily_sheet) if store_daily_xlsx else {}
    ledger = load_ledger(store_daily_xlsx, store, store_daily_sheet) if (store_daily_xlsx and policy == "v2") else {}

    # ABC 分级（按全渠道汇总日均排名）：A=前 cut0，B=到 cut1，C=其余
    abc: dict[tuple, str] = {}
    if ledger:
        ranked = sorted(ledger.items(), key=lambda kv: -kv[1]["sum7"])
        n = len(ranked)
        c0, c1 = int(n * abc_cuts[0]), int(n * abc_cuts[1])
        for i, (k, _) in enumerate(ranked):
            abc[k] = "A" if i < c0 else ("B" if i < c1 else "C")

    by_seller: dict[str, dict] = {}
    for e in exp:
        by_seller.setdefault(e["seller"], e)     # 同一商家SKU只留一行（平台SKU相同）

    codes = sorted({e["sku"] for e in by_seller.values()})
    live = api.query(codes, store) if codes else {}

    def key_of(seller: str):
        # 与面板（领星/帕沃尔）统一口径：N204003/BS2- 前缀这类差异都在 norm_key 里吃掉
        return norm_key(seller)

    # 替代款：**只在本款该尺码仓内为 0 时才借对方**（apply_rules 里已把对方的量填进来）。
    # 用户 2026-11-12 明确：自己的尺码有货就一定用自己的，不做合并池。
    zero, topup, ok, noinfo, decisions, alerts = [], [], [], [], [], []
    # 清零台账里「线上还是 0」的款-尺码：**不要再走保底挂把它拉起来**。
    # 用户 2026-11-12：204605 后三个码点名清零，下一轮「本店无销量 → 保底挂 5 件」
    # 又给加回去了（清零和保底挂互相对冲）。要恢复就走 restore_shein_clear.py。
    cleared_keys = set()
    for x in load_clear_ledger():
        if (x.get("store") or "").upper() != store.upper():
            continue
        if (x.get("onlineNow") or 0) > 0:
            continue
        cleared_keys.add((str(x.get("code") or "").lower(),
                          str(x.get("variant") or "").lower(),
                          str(x.get("size") or "").lower()))
    # 手工清零名单（--force-zero）：可写 商家SKU / 平台SKU / 「款-变体-尺码」，大小写不敏感。
    # 用户 2026-11-12：「把 BAN 的 204605 后三个码清零」——运营明确点名的不走分档规则。
    forced = {str(x).strip().lower() for x in (force_zero or ()) if str(x).strip()}
    for seller, e in sorted(by_seller.items()):
        ko = key_of(seller)
        p = panel.get(ko or ())
        lv = live.get(e["sku"]) or {}
        now = lv.get("usable") or 0
        # 仓库码：以线上「真有货」的仓为准（导出表那行可能指向 0 件的仓，改不动）
        whs = {k: v for k, v in (lv.get("warehouses") or {}).items()
               if (v.get("usable") or 0) > 0}
        wh = max(whs.items(), key=lambda kv: kv[1]["usable"])[0] if whs else e["whcode"]
        it = dict(seller=seller, sku=e["sku"], now=now, wh=wh)
        if forced:
            cand = {seller.lower(), str(e["sku"]).lower(),
                    "-".join(x for x in (ko or ()) if x).lower()}
            if cand & forced:
                pp = p or {}
                rule_f = ("手工清零（点名 %s）：仓内%g 日均%.2f 可售%s"
                          % (sorted(cand & forced)[0], pp.get("total") or 0,
                             pp.get("daily") or 0,
                             "—" if pp.get("days") is None else pp["days"]))
                decisions.append(dict(it, days=pp.get("days"), daily=pp.get("daily") or 0,
                                      total=pp.get("total"), target=0, rule=rule_f))
                if now > 0:
                    zero.append(dict(it, days=pp.get("days"), daily=pp.get("daily") or 0,
                                     total=pp.get("total"), rule=rule_f))
                else:
                    ok.append(dict(it, days=pp.get("days"), daily=pp.get("daily") or 0,
                                   total=pp.get("total"), target=0, rule=rule_f))
                continue
        if not p or p["days"] is None or p["daily"] <= 0:
            # ===== 「无数据」兜底（用户 2026-11-12 定，默认开，--no-fallback 关）=====
            # 背景：BAN 有 105 个 SKU 线上还挂着货（BK-2072-4SetB-L 128 件、
            #       BAN-2039-4SetA-S 113 件…），但领星面板查不到这套「款-套系-尺码」的
            #       日均 ⇒ 既不补也不清，长期裸奔（仓内断货了线上还在卖）。
            # 兜底口径（只看仓内，不看日均，所以叫兜底）：
            #   ① 面板里**根本没有这个款-尺码** → 不动（数据缺失，不猜）
            #   ② 仓内可用 <= 0：
            #        · 线上有货 + 无在途  → 清零（真断货了，不能再卖）
            #        · 线上有货 + 有在途  → 不动，等货到（2038-4SetB 就是货在途被早清零的教训）
            #   ③ 仓内可用 > 0 → 保底挂 min_listing(5) 件（只加不减，扣安全库存/渠道份额）
            total0 = (p or {}).get("total")
            total0 = float(total0 or 0)
            transit0 = float((p or {}).get("transitAll") or 0)
            if not p:
                # 面板里连这个「款-变体-尺码」都没有 = 领星 + 帕沃尔都没有它的库存记录
                # ⇒ 仓内就是 0（用户 2026-11-12：按领星总库存对应，没有就是没有）
                if fallback and now > 0:
                    r = "无库存记录兜底：领星+帕沃尔都查不到该 款-变体-尺码 → 清零"
                    zero.append(dict(it, days=0.0, daily=0.0, total=0.0, rule=r))
                else:
                    noinfo.append(dict(it, days=None, daily=0.0, total=0.0, transitAll=0.0))
                continue
            if total0 <= 0:
                if fallback and now > 0 and transit0 <= 0:
                    r = "无日均兜底：仓内0件、无在途 → 清零（不能再卖）"
                    zero.append(dict(it, days=0.0, daily=0.0, total=total0, rule=r))
                else:
                    noinfo.append(dict(it, days=None, daily=0.0, total=total0,
                                       transitAll=transit0))
                continue
            if not fallback:
                # 老口径：只有「线上为 0 且有货」才保底挂，线上还有货的不动
                if not (min_listing > 0 and now == 0):
                    noinfo.append(dict(it, days=p.get("days"), daily=0.0, total=total0))
                    continue
            lg0 = (ledger or {}).get(key_of(seller) or ()) or {}
            d0 = lg0.get("store7") or 0.0
            # 无日均时没法算他渠占用：只按「该店日均 × 本地发货天数」估一份占用量。
            # 不扣安全库存 —— 没有卖速就谈不上 σ，扣固定件数只会把长尾款挂单量压成 0。
            avail0b = total0 - max(0.0, d0) * local_days
            base0 = min(min_listing, max(0, int(avail0b * share))) if min_listing > 0 else 0
            if ko in cleared_keys and now <= 0:
                # 已经清零过、线上仍是 0 → 不要保底挂（否则清零刚做完又被拉起来）
                ok.append(dict(it, days=p.get("days"), daily=d0, total=total0,
                               target=0, rule="已清零（台账保持 0，不保底挂）"))
                continue
            if fallback and base0 > now:
                rs = p.get("rule_src") or "无日均"
                rule0 = ("无日均兜底：仓内%g件 → 保底挂 %d 件（扣该店日均占用 %.1f 件；%s）"
                         % (total0, base0, max(0.0, d0) * local_days, rs))
                decisions.append(dict(it, days=p.get("days"), daily=d0, total=total0,
                                      target=base0, rule=rule0, store_daily=d0))
                topup.append(dict(it, days=p.get("days"), daily=d0, total=total0,
                                  target=base0, delta=base0 - now, rule=rule0))
            else:
                ok.append(dict(it, days=p.get("days"), daily=d0, total=total0,
                               target=now, store_daily=d0))
            continue
        if p["days"] <= days_target:
            (zero if now > 0 else ok).append(dict(it, days=p["days"], daily=p["daily"], total=p["total"]))
            continue
        # ===== v2：服务水平 + 补货点(ROP) + ABC 的 DOS + 可支配封顶 =====
        if policy == "v2" and ledger:
            lg = ledger.get(key_of(seller) or ())
            if not lg:
                noinfo.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"]))
                continue
            d_tot = lg["sum7"]
            d_store = lg["store7"]
            # σ：优先用面板「3天/7天/14天日均」三列（和用户在页面上看到的一致），
            #     面板没这三列时回落到库存管理表的三档
            trio = [p.get("daily3"), p.get("daily"), p.get("daily14")]
            if all(v is not None and v >= 0 for v in trio) and (trio[1] or 0) > 0:
                sigma = pstdev([float(v) for v in trio])
                sigma_src = "面板3/7/14"
            else:
                sigma = pstdev([lg["sum3"], lg["sum7"], lg["sum14"]]) if d_tot > 0 else 0.0
                sigma_src = "台账3/7/14"
            ss_wh = z_value * sigma * math.sqrt(max(lead_days, 0))
            rop = d_tot * lead_days + ss_wh
            on_hand = p["total"] + p.get("transitAll", 0)
            if on_hand < rop:
                alerts.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                   rop=round(rop, 1), on_hand=on_hand,
                                   transit=p.get("transitAll", 0), target=0))
            if d_store <= 0:
                # 本店这条链接 7 天内一单没卖 → 没有历史卖速可算 DOS。
                # 但如果仓里明明有货、线上却挂着 0，链接就永远起不来（死循环），
                # 所以给一个「保底挂单量」（--min-listing，默认 5 件），让链接能试卖。
                other0 = max(0.0, d_tot) * local_days
                avail0 = p["total"] - other0
                base = min(min_listing, max(0, int(avail0 * share)))
                if ko in cleared_keys and now <= 0:
                    # 清零过、线上仍是 0 → 不保底挂（否则清完立刻又被加回来）
                    ok.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                   target=0, rule="已清零（台账保持 0，不保底挂）",
                                   store_daily=0.0))
                    continue
                if min_listing > 0 and now < base:
                    rule0 = "本店无销量 → 保底挂 %d 件（仓内%g − 他渠%.1f；可支配%.1f）" % (
                        base, p["total"], other0, avail0)
                    decisions.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                          target=base, rule=rule0, store_daily=0.0))
                    topup.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                      target=base, delta=base - now, rule=rule0))
                else:
                    ok.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                   target=now, store_daily=0.0))
                continue
            cls = abc.get(key_of(seller) or (), "C")
            dos_days = {"A": dos[0], "B": dos[1], "C": dos[2]}[cls]
            # ★ 趋势：3天日均 vs 14天日均（面板三列），起量加档、降温降档
            trend_note, trend_mult = "", 1.0
            d3, d14 = p.get("daily3"), p.get("daily14")
            if trend and d3 is not None and d14 and d14 > 0:
                ratio_t = float(d3) / float(d14)
                if ratio_t >= trend_up:
                    trend_mult = trend_boost
                    trend_note = "；起量 3天%.1f/14天%.1f=%.2f→DOS×%.1f" % (d3, d14, ratio_t, trend_boost)
                elif ratio_t <= trend_down:
                    trend_mult = trend_cut
                    trend_note = "；降温 3天%.1f/14天%.1f=%.2f→DOS×%.1f" % (d3, d14, ratio_t, trend_cut)
            dos_eff = dos_days * trend_mult
            ratio = (d_store / d_tot) if d_tot > 0 else 1.0
            ss_store = z_value * sigma * ratio * math.sqrt(max(local_days, 0))
            other = max(0.0, d_tot - d_store) * local_days
            avail = p["total"] - other - ss_store
            s_target = int(-(-d_store * dos_eff // 1))            # S = ceil(本店日均 × DOS_eff)
            target = min(s_target, max(0, int(avail * share)))
            rule = "v2 %s类 DOS%g天 → S=%d；可支配 %.1f（仓内%g − 他渠%.1f − SS%.1f）；" \
                   "ROP=%.0f(在库+在途 %.0f, σ=%.2f %s)%s" % (
                       cls, dos_eff, s_target, avail, p["total"], other, ss_store,
                       rop, on_hand, sigma, sigma_src, trend_note)
            if cap and now < target and target > now:
                pass
            decisions.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                  target=target, rule=rule, store_daily=d_store))
            if target > now:
                topup.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                                  target=target, delta=target - now, rule=rule))
            else:
                ok.append(dict(it, days=p["days"], daily=d_tot, total=p["total"],
                               target=target, rule=rule))
            continue

        # ===== v1：可售天数分档 + 仓余降档 + 封顶 =====
        # 分档：挑第一个 days <= 上界的档
        lo = days_target
        mult, tier = bands[-1][1], "可售>%g天→%g天量" % (bands[-2][0], bands[-1][1])
        for hi, m in bands:
            if p["days"] <= hi:
                mult = m
                tier = "可售%g~%g天→%g天量" % (lo, hi, m)
                break
            lo = hi
        # 目标量用的日均：给了库存管理表就用该店自己的，否则用全渠道汇总
        d_tgt = sdaily.get(key_of(seller) or (), p["daily"]) if sdaily else p["daily"]
        if d_tgt <= 0 and sdaily:
            # 该店这条链接没销量 → 不该再往上加库存
            ok.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                           target=now, store_daily=0.0))
            continue
        tn = int(-(-d_tgt * mult // 1))
        remain = p["total"] - tn
        if remain <= gap:
            target = int(-(-d_tgt * fallback_days // 1))
            rule = "%s, 但仓内只余%g件<=%g → %g天量" % (tier, remain, gap, fallback_days)
        else:
            target = tn
            rule = "%s(仓内余%g件)" % (tier, remain)
        if d_tgt != p["daily"]:
            rule += " | 目标用该店日均 %.2f（汇总 %.2f）" % (d_tgt, p["daily"])
        # ★ 防超卖封顶（含渠道份额）
        safety = max(p["daily"] * safety_days, min_units)
        limit = max(0, int(p["total"] * share - safety))
        if cap and target > limit:
            rule += " | 封顶 %d 件（仓内 %g×%.2f - 安全 %g）" % (limit, p["total"], share, safety)
            target = limit
        # ★ 补货预警：可售不足且完全没有在途
        if p["days"] < alert_days and p.get("transitAll", 0) <= 0:
            alerts.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                               target=target))
        decisions.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                              target=target, tn=tn, remain=remain, limit=limit, rule=rule,
                              store_daily=d_tgt))
        if target > now:
            topup.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                              target=target, delta=target - now, rule=rule))
        else:
            ok.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                           target=target, rule=rule))
    return zero, topup, ok, noinfo, decisions, alerts


def save_alerts(alerts: list, out_path: str = "") -> str:
    """把补货预警写成 xlsx，返回路径（没有预警则不写）。"""
    if not alerts:
        return ""
    try:
        import openpyxl
    except ImportError:
        return ""
    from datetime import datetime
    p = Path(out_path) if out_path else Path(
        r"D:\together\全托管\SHEIN\BD-库存\补货预警_%s.xlsx" % datetime.now().strftime("%Y%m%d"))
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "补货预警"
    ws.append(["商家SKU", "平台SKU", "可售天数", "7天日均", "仓内可用", "在途", "SHEIN现有", "建议"])
    for x in alerts:
        ws.append([x["seller"], x["sku"], x["days"], round(x["daily"], 2), x["total"],
                   x.get("transitAll", 0), x["now"], "补货（无在途，靠改 SHEIN 库存撑不住）"])
    for i, w in enumerate([24, 18, 11, 11, 12, 8, 12, 40], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    wb.save(p)
    return str(p)


#: SHEIN「清零台账」（面板「修改库存 · 清零」的数据源）。
#: 用户 2026-11-12：SHEIN 半托管**没有预售**，只有「可售<=1天 → 线上清零」，
#: 所以 SHEIN 页签不给「预售中/已过预售」，只列清零记录。
#: 结构：{ "updatedAt": "...", "items": [ {store, code, variant, size, seller, sku, wh,
#:         before, total, daily, days, clearedAt, firstClearedAt, times, confirmed, source} ] }
CLEAR_LEDGER = ROOT / "data" / "shein-clear-ledger.json"
CLEAR_KEEP_DAYS = 60


def norm_key(text: str):
    """商家SKU / MSKU / 领星产品名 → (款号, 变体, 尺码) 小写三元组；拆不出返回 None。

    ⚠ 2026-11-12 用户报「BAN 在售款对不上领星总库存」查出的 bug：
      两边用的**不是同一个拆法**。领星那一侧把 `BS2-N204003-2B-S` 拆成
      code=204003（N 前缀被吃掉），而商家SKU 那侧 `BW-N204003-2B-S` 被手工
      split 成 code=n204003 ⇒ 同一个 SKU 变成两个 key ⇒ 面板查不到 → 判「无数据」
      → 既不补也不清（BW-N204003-* 五条一直裸奔）。
      这里统一走 `fetch_stock_track.split_sku_any()`（能吃 BS2-/BH-/BW- 前缀、
      吃掉款号字母前缀、归一 1XL 这类尺码），两边口径就一致了。
    """
    import fetch_stock_track as fst          # noqa: PLC0415

    s = str(text or "").strip()
    if not s:
        return None
    try:
        got = fst.split_sku_any(s)
    except Exception:                        # noqa: BLE001
        got = None
    if got and got[0] and got[2]:
        return (str(got[0]).lower(), str(got[1] or "").lower(), str(got[2]).lower())
    # 兜底：老的手工拆法（前缀-款-变体-尺码）
    body = s.split("-", 1)[1] if "-" in s else s
    parts = body.split("-")
    if len(parts) < 2:
        return None
    return (parts[0].lower(), "-".join(parts[1:-1]).lower(), parts[-1].lower())


def split_seller(seller: str) -> tuple:
    """'BD-202504-5SetE-XL' → ('202504', '5SetE', 'XL')（与 build() 的 key_of 同口径）。"""
    k = norm_key(seller)
    if k:
        return k
    body = seller.split("-", 1)[1] if "-" in seller else seller
    return (body, "", "")


def load_clear_ledger() -> list:
    try:
        if CLEAR_LEDGER.exists():
            d = json.loads(CLEAR_LEDGER.read_text(encoding="utf-8"))
            return list(d.get("items") or [])
    except Exception:  # noqa: BLE001
        pass
    return []


def record_clear(store: str, rows: list, confirmed: set | None = None,
                 when: str = "", source: str = "pipeline") -> tuple:
    """把本轮「清零」记进台账（按 店+款+变体+尺码 去重，重复清零只累加次数）。

    rows 直接吃 build() 的 zero 列表（含 seller/sku/wh/now/days/daily/total）。
    confirmed 里是回读确认线上已=0 的平台SKU；when 不给就用现在。
    记录只保留 CLEAR_KEEP_DAYS 天内的（按最近清零时间），避免无限长。
    返回 (新增, 更新, 台账总数)。
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    stamp = when or now.strftime("%Y-%m-%d %H:%M:%S")
    items = load_clear_ledger()
    idx = {(x.get("store"), x.get("code"), x.get("variant"), x.get("size")): x for x in items}
    conf = set(confirmed or ())
    added = upd = 0
    for z in rows:
        code, variant, size = split_seller(z["seller"])
        key = (store.upper(), code, variant, size)
        cur = idx.get(key)
        rec = {
            "platform": "SHEIN",
            "store": store.upper(),
            "code": code,
            "variant": variant,
            "size": size,
            "seller": z["seller"],
            "sku": z["sku"],
            "wh": z.get("wh") or "",
            "before": z.get("now"),
            "total": z.get("total"),
            "daily": round(float(z.get("daily") or 0), 2),
            "days": z.get("days"),
            "clearedAt": stamp,
            "firstClearedAt": stamp,
            "times": 1,
            "confirmed": (z["sku"] in conf) if conf else True,
            "source": source,
        }
        if cur:
            # 已有记录：保留首次时间，最近时间只往**新**了推（回填旧计划表不会把它改早）
            first = cur.get("firstClearedAt") or cur.get("clearedAt") or stamp
            last = cur.get("clearedAt") or stamp
            rec["firstClearedAt"] = min(first, stamp)
            rec["clearedAt"] = max(last, stamp)
            rec["times"] = int(cur.get("times") or 1) + (1 if stamp >= last else 0)
            rec["confirmed"] = rec["confirmed"] or bool(cur.get("confirmed"))
            items[items.index(cur)] = rec
            idx[key] = rec
            upd += 1
        else:
            items.append(rec)
            idx[key] = rec
            added += 1

    # 只留最近 CLEAR_KEEP_DAYS 天
    cut = (now - timedelta(days=CLEAR_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    items = [x for x in items if (x.get("clearedAt") or "") >= cut]
    items.sort(key=lambda x: ((x.get("clearedAt") or ""), x.get("seller") or ""), reverse=True)
    CLEAR_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    CLEAR_LEDGER.write_text(json.dumps({"updatedAt": stamp, "items": items},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
    return added, upd, len(items)


def sync_clear_online(store: str, live: dict, when: str = "") -> int:
    """把清零台账里该店记录与**线上现值**核对，写 onlineNow / checkedAt。

    为什么需要（2026-11-12 用户问「领星明明有库存为什么清零了」）：
    台账是历史账，清零后线上可能又被补回去（或货 11-12 才到）。面板只显示
    清零那一刻的状态就会误导，所以每轮都用 stock-query 回填线上现值：
      onlineNow > 0 → 面板标「已回补」，= 0 → 仍「已清零」。
    live = api.query() 的返回 {skuCode: {usable, ...}}。
    """
    from datetime import datetime

    items = load_clear_ledger()
    if not items:
        return 0
    stamp = when or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for x in items:
        if (x.get("store") or "").upper() != store.upper():
            continue
        sku = x.get("sku")
        if sku in live:
            x["onlineNow"] = (live.get(sku) or {}).get("usable") or 0
            x["checkedAt"] = stamp
            n += 1
    if n:
        CLEAR_LEDGER.write_text(json.dumps({"updatedAt": stamp, "items": items},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return n


def clear_skus(store: str) -> list:
    """台账里该店的平台SKU（给上面那个核对用）。"""
    return [x["sku"] for x in load_clear_ledger()
            if (x.get("store") or "").upper() == store.upper() and x.get("sku")]


PUSH_CFG = ROOT / "config" / "shein_stock_push.json"


def stock_webhook() -> str:
    """库存推送 webhook 优先级：env FEISHU_WEBHOOK_SHEIN_STOCK → config/shein_stock_push.json
    → env FEISHU_WEBHOOK_STOCK → 默认业务群（None）。

    ⚠ config/shein_stock_push.json 已在 .gitignore 里：webhook 属凭证，不入库。
    """
    import os
    u = (os.environ.get("FEISHU_WEBHOOK_SHEIN_STOCK") or "").strip()
    if u:
        return u
    try:
        if PUSH_CFG.exists():
            d = json.loads(PUSH_CFG.read_text(encoding="utf-8"))
            u = str(d.get("webhook") or "").strip()
            if u:
                return u
    except Exception:  # noqa: BLE001
        pass
    return (os.environ.get("FEISHU_WEBHOOK_STOCK") or "").strip()


def push_summary(a, zero: list, topup: list, alerts: list, applied: bool) -> tuple:
    """推飞书群（库存调整专用群；没配就退回 TEMU 业务群）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import notify_push as NP
    except Exception as e:
        return False, "notify_push 不可用：%s" % e
    lines = ["SHEIN %s 库存%s" % (a.store, "已执行" if applied else "预览"),
             "清零 %d 个 / 补货 %d 个 / 需补货预警 %d 个" % (len(zero), len(topup), len(alerts))]
    for z in zero[:10]:
        lines.append("清零 %s 可售%s天 原%s件" % (z["seller"], z["days"], z["now"]))
    for t in topup[:10]:
        lines.append("补货 %s +%d（目标%d，现有%d）" % (t["seller"], t["delta"], t["target"], t["now"]))
    if alerts:
        lines.append("—— 需补货（可售<%.0f天且无在途）——" % a.alert_days)
        for x in alerts[:12]:
            lines.append("%s 可售%s天 日均%s 仓内%s" % (x["seller"], x["days"], round(x["daily"], 1), x["total"]))
    hook = stock_webhook() or None
    return NP.push_group("TEMU SHEIN 库存调整", "\n".join(lines), hook)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 库存双向同步（≥N天补 3 天量 / <N天清零）")
    ap.add_argument("--export", required=True)
    ap.add_argument("--store", default="BAN")
    ap.add_argument("--days", type=float, default=1.0, help="清零阈值（<=N 天清零），默认 1 天")
    ap.add_argument("--bands", default="3:1,7:2,inf:3",
                    help="分档 上界:倍率，逗号分隔，默认 3:1,7:2,inf:3")
    ap.add_argument("--gap", type=float, default=50.0,
                    help="仓总库存 - N天量 <= gap 时降成 1 天量（默认 50 件）")
    ap.add_argument("--fallback-days", type=float, default=1.0, help="上面那种情况用的天数，默认 1")
    ap.add_argument("--safety-days", type=float, default=1.0,
                    help="安全库存 = 日均 × N 天（防超卖），默认 1")
    ap.add_argument("--min-units", type=float, default=5.0,
                    help="安全库存下限（件），默认 5")
    ap.add_argument("--no-cap", action="store_true",
                    help="关掉防超卖封顶（默认开：上架 <= 仓内库存 - 安全库存）")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--skip-pw-refresh", action="store_true")
    ap.add_argument("--share", type=float, default=1.0,
                    help="SHEIN 渠道可占仓内库存的比例（多渠共享时用，如 0.5），默认 1.0")
    ap.add_argument("--alert-days", type=float, default=2.0,
                    help="可售低于 N 天且无在途 → 记入补货预警，默认 2")
    ap.add_argument("--alert-out", default="",
                    help="补货预警 xlsx 输出路径（默认 D:\\together\\全托管\\SHEIN\\BD-库存\\补货预警_<日期>.xlsx）")
    ap.add_argument("--push", action="store_true", help="把补货预警 + 本轮改动推到飞书群")
    ap.add_argument("--apply", action="store_true", help="真提交；不带就是预览")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个补货（调试用）")
    ap.add_argument("--refresh-track", action="store_true",
                    help="顺带重抓 九部/十部 看板（平时不用：那两个板块有自己的定时任务）")
    ap.add_argument("--store-daily", default="",
                    help="库存管理表 xlsx（总销售表）：给了就用该店自己的「最近7天日均」定目标量；"
                         "不给则用全渠道汇总日均")
    ap.add_argument("--store-daily-sheet", default="总销售表", help="该表 sheet 名，默认 总销售表")
    ap.add_argument("--policy", default="v2", choices=["v1", "v2"],
                    help="v1=可售天数分档（旧）；v2=服务水平+ROP+ABC的DOS（默认）")
    ap.add_argument("--lead-days", type=float, default=45.0,
                    help="从国内发到帕沃尔仓的补货周期 L（天），默认 45")
    ap.add_argument("--local-days", type=float, default=3.0,
                    help="SHEIN 侧发货时效（天），默认 3")
    ap.add_argument("--z", dest="z_value", type=float, default=1.65,
                    help="服务水平 z 值（1.65≈95%，2.33≈99%），默认 1.65")
    ap.add_argument("--dos", default="7,3,1", help="A/B/C 类目标可售天数，默认 7,3,1")
    ap.add_argument("--abc", default="0.2,0.5", help="ABC 切分比例，默认 0.2,0.5")
    ap.add_argument("--min-listing", type=float, default=5.0,
                    help="本店无销量时的保底挂单量（件），默认 5；0=关闭")
    ap.add_argument("--no-trend", action="store_true",
                    help="关掉趋势调档（默认开：3天日均/14天日均 ≥1.3 加档、≤0.7 降档）")
    ap.add_argument("--trend-up", type=float, default=1.3)
    ap.add_argument("--trend-down", type=float, default=0.7)
    ap.add_argument("--trend-boost", type=float, default=1.4)
    ap.add_argument("--trend-cut", type=float, default=0.7)
    ap.add_argument("--no-rules", action="store_true",
                    help="不套用「替代/拆解」规则（config/shein_stock_rules.json）")
    ap.add_argument("--no-fallback", action="store_true",
                    help="关掉「无数据」兜底（默认开：仓内0+无在途→清零；仓内有货→保底挂 min_listing）")
    ap.add_argument("--rules", default="", help="规则文件路径（默认 config/shein_stock_rules.json）")
    a = ap.parse_args(argv)

    if not a.no_refresh:
        refresh(skip_pw=a.skip_pw_refresh, refresh_track=a.refresh_track)
    print("[数据] %s" % data_ages())

    zero, topup, ok, noinfo, decisions, alerts = build(
        a.export, a.store, a.days, a.gap, a.fallback_days, parse_bands(a.bands),
        a.safety_days, a.min_units, not a.no_cap, a.share, a.alert_days,
        a.store_daily, a.store_daily_sheet, a.policy, a.lead_days, a.local_days,
        a.z_value, tuple(float(x) for x in a.dos.split(",")),
        tuple(float(x) for x in a.abc.split(",")), a.min_listing,
        not a.no_trend, a.trend_up, a.trend_down, a.trend_boost, a.trend_cut, not a.no_rules, a.rules,
        not a.no_fallback)
    if a.limit:
        topup = topup[:a.limit]

    print("\n[决策明细] 可售>=%g 天的 %d 个：" % (a.days, len(decisions)))
    for d in decisions:
        print("   %-24s 可售%-7s 日均%-6s 仓内%-6s 目标%-5s 现有%-5s → %s"
              % (d["seller"], d["days"], round(d["daily"], 2), d["total"], d["target"],
                 d["now"], d["rule"]))
    print("\n[结果] 需清零 %d 个 / 需补库存 %d 个 / 已达标 %d 个 / 无销量数据 %d 个 / 补货预警 %d 个"
          % (len(zero), len(topup), len(ok), len(noinfo), len(alerts)))
    if alerts:
        print("\n--- 补货预警（触发条件见规则）---")
        for x in alerts:
            print("   %-24s 可售%-6s 日均%-6s 仓内%-6s 线上%-5s %s" % (
                x["seller"], x["days"], round(x["daily"], 2), x["total"], x["now"],
                ("ROP=%.0f 在库+在途=%.0f" % (x["rop"], x["on_hand"])) if "rop" in x else ""))
    if zero:
        print("\n--- 清零 ---")
        for z in zero:
            print("   %-24s %-16s 可售%-6s 现有%-5s → 0" % (z["seller"], z["sku"], z["days"], z["now"]))
    if topup:
        print("\n--- 补库存（分档：<=%g天清零；%s；仓余<=%g 降 %g 天量）---"
              % (a.days, a.bands, a.gap, a.fallback_days))
        for t in topup:
            print("   %-24s %-16s 可售%-7s 日均%-6s 现有%-5s 目标%-5s 加 %s"
                  % (t["seller"], t["sku"], t["days"], round(t["daily"], 2), t["now"], t["target"], t["delta"]))

    out = save_alerts(alerts, a.alert_out)
    if out:
        print("[alert] 补货预警 → %s" % out)
    if a.push:
        ok, why = push_summary(a, zero, topup, alerts, applied=a.apply)
        print("[push] %s %s" % ("OK" if ok else "FAIL", why))

    if not a.apply:
        print("\n[preview] 加 --apply 才会真的提交")
        return 0

    rows = []
    for z in zero:
        rows.append(dict(sku=z["sku"], warehouse=z["wh"], quantity=0, reason="可售<3天清零"))
    if rows:
        res = api.change(rows, a.store, "OVERWRITE", apply=True)
        for x in res:
            print("   [OVERWRITE %s] %-16s %s %s" % ("OK" if x["ok"] else "FAIL", x["sku"], x["code"], x["msg"]))
    if topup:
        rows = [dict(sku=t["sku"], warehouse=t["wh"], quantity=t["delta"], reason="补3天量(可售>=3)")
                for t in topup]
        res = api.change(rows, a.store, "ADD", apply=True)
        for x in res:
            print("   [ADD %s] %-16s %s %s" % ("OK" if x["ok"] else "FAIL", x["sku"], x["code"], x["msg"]))

    # 回读
    check = [z["sku"] for z in zero] + [t["sku"] for t in topup]
    live = api.query(check, a.store) if check else {}
    print("\n---- 回读 ----")
    bad = 0
    for z in zero:
        u = (live.get(z["sku"]) or {}).get("usable")
        bad += 0 if u == 0 else 1
        print("   %-16s 可用%-5s 期望 0    %s" % (z["sku"], u, "✓" if u == 0 else "✗"))
    for t in topup:
        u = (live.get(t["sku"]) or {}).get("usable")
        good = (u or 0) >= t["target"]
        bad += 0 if good else 1
        print("   %-16s 可用%-5s 期望 >=%-4s %s" % (t["sku"], u, t["target"], "✓" if good else "✗"))
    print("[结果] 未达标 %d 个" % bad)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
