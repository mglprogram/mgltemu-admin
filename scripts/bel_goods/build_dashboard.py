# -*- coding: utf-8 -*-
"""生成自包含的 HTML 运营看板（双击即可用浏览器打开，无需服务、无外部依赖）。

数据来源：
  data/<store>/<store>_products.db   —— 在售商品 + 限流明细
  data/focus_watch_latest.json       —— 监控脚本最近一次结果

输出：data/temu_dashboard.html
"""
from __future__ import annotations

import html
import json
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "data"
OUT = DATA / "temu_dashboard.html"
TREND_JSON = DATA / "trend_data.json"

FOCUS = ["7502713672", "5745761504", "3082143968", "1224162395",
         "8118634665", "3740374018", "1249844154"]
FOCUS_BEL = FOCUS   # 向后兼容旧引用，实际以 FOCUS_BY_STORE 为准

# Apache ECharts（Apache-2.0）—— 内联进 HTML，保证单文件离线可用
VENDOR = Path(__file__).resolve().parent / "vendor" / "echarts.min.js"
ECHARTS_JS = ""

DB = {
    "BEL": (DATA / "bel" / "bel_products.db", "traffic_limited", "BEL 店", "TM-BEL-MGL"),
    "BHY": (DATA / "bhy" / "bhy_products.db", "traffic_limited_bhy", "BHY 店", "TM-BHY-YJF"),
}

# 各店重点关注 SPU（用户指定）
FOCUS_BY_STORE = {
    "BEL": ["7502713672", "5745761504", "3082143968", "1224162395",
            "8118634665", "3740374018", "1249844154"],
    "BHY": ["1289576127", "3248169459", "3775612405", "3194032323",
            "1586673761", "7557422941", "7235257044", "8312381638"],
}


def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def load_store(path, tl):
    con = sqlite3.connect(path)
    c = lambda s, a=(): q(con, s, a)
    d = {
        "sku": c("SELECT COUNT(*) FROM skus")[0][0],
        "spu": c("SELECT COUNT(*) FROM products")[0][0],
        "skc": c("SELECT COUNT(DISTINCT skc_id) FROM skus")[0][0],
        "code": c("SELECT COUNT(DISTINCT skc_code) FROM skus")[0][0],
        "cat": c("SELECT COUNT(DISTINCT category) FROM skus")[0][0],
        "stock": c("SELECT SUM(stock) FROM skus")[0][0] or 0,
        "limited": c(f"SELECT COUNT(*) FROM {tl}")[0][0],
        "with_daily": c(f"SELECT COUNT(*) FROM {tl} WHERE daily_target_price IS NOT NULL")[0][0],
        "with_act": c(f"SELECT COUNT(*) FROM {tl} WHERE activity_target_price IS NOT NULL")[0][0],
        "has_sales": c(f"SELECT COUNT(*) FROM {tl} WHERE sales7 > 0")[0][0],
        "flow": c(f"SELECT flow_status, COUNT(*) FROM {tl} GROUP BY flow_status ORDER BY COUNT(*) DESC"),
        "cats": c("SELECT category, COUNT(*), SUM(stock) FROM skus GROUP BY category ORDER BY COUNT(*) DESC LIMIT 10"),
        "top": c(f"""SELECT sku_code, declare_price, daily_target_price FROM {tl}
                     WHERE declare_price IS NOT NULL AND daily_target_price IS NOT NULL
                     ORDER BY (declare_price - daily_target_price) DESC LIMIT 10"""),
    }
    con.close()
    return d


def load_focus(store="BEL"):
    path, tl, _, _ = DB[store]
    con = sqlite3.connect(path)
    rows = []
    for s in FOCUS_BY_STORE[store]:
        r = con.execute("SELECT title, category, sku_count, total_stock FROM products WHERE spu_id=?", (s,)).fetchone()
        codes = [x[0] for x in con.execute("SELECT DISTINCT skc_code FROM skus WHERE spu_id=?", (s,))]
        lim = con.execute(f"""SELECT flow_status, declare_price, daily_target_price, activity_target_price
                               FROM {tl} WHERE spu_id=?""", (s,)).fetchone()
        rows.append({
            "spu": s,
            "store": store,
            "title": (r[0] if r else ""),
            "cat": r[1] if r else "",
            "sku": r[2] if r else 0,
            "stock": r[3] if r else 0,
            "codes": codes,
            "limited": bool(lim),
            "flow": lim[0] if lim else "",
            "cur": lim[1] if lim else None,
            "daily": lim[2] if lim else None,
            "act": lim[3] if lim else None,
        })
    con.close()
    return rows


def _axis_vals(ys, pad=34, h=170, w=760, hi=None):
    lo = min(ys)
    hi2 = hi or (max(ys) if max(ys) > lo else lo + 1)
    n = len(ys)
    px = lambda i: pad + i * (w - pad * 2) / max(n - 1, 1)
    py = lambda v: h - pad - (v - lo) * (h - pad * 2) / max(hi2 - lo, 1)
    return px, py, lo, hi2


# ---------------------------------------------------------------- ECharts 图表
EC_AXIS = ("#6f7682", "#272c36")
EC_PALETTE = ["#e05561", "#e0a33e", "#599ce7", "#2fa37c",
              "#9b7fd4", "#4fb3c9", "#c98a4b", "#8a8f9a"]


def echarts_block(div_id, option, height=300, _reg=None):
    """输出一个 ECharts 容器 div + 其 option 的 JSON，并登记 id 供初始化用。"""
    if _reg is not None:
        _reg.append(div_id)
    return (f'<div id="{div_id}" class="chart" style="height:{height}px"></div>'
            f'<script>window.__OPT_{div_id}__ = {json.dumps(option, ensure_ascii=False)};</script>')


def ec_line_option(categories, series, colors=None, yname=""):
    """折线图 option。series=[{'name':..,'data':[..]}]"""
    colors = colors or EC_PALETTE
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "grid": {"left": 52, "right": 20, "top": 34, "bottom": 34},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 2, "textStyle": {"color": "#a3a9b5", "fontSize": 11},
                   "itemWidth": 14, "itemHeight": 8},
        "xAxis": {
            "type": "category", "data": categories,
            "axisLine": {"lineStyle": {"color": EC_AXIS[1]}},
            "axisTick": {"show": False},
            "axisLabel": {"color": EC_AXIS[0], "fontSize": 11},
        },
        "yAxis": {
            "type": "value", "name": yname,
            "nameTextStyle": {"color": EC_AXIS[0], "fontSize": 11},
            "axisLine": {"show": False}, "axisTick": {"show": False},
            "axisLabel": {"color": EC_AXIS[0], "fontSize": 11},
            "splitLine": {"lineStyle": {"color": "#1e222b"}},
        },
        "series": [
            {"name": s["name"], "type": "line", "data": s["data"],
             "smooth": True, "symbol": "circle", "symbolSize": 6,
             "lineStyle": {"width": 2, "color": colors[i % len(colors)]},
             "itemStyle": {"color": colors[i % len(colors)]}}
            for i, s in enumerate(series)
        ],
    }


def ec_bar_option(labels, values, colors=None, horizontal=True, yname=""):
    """条形图 option。"""
    colors = colors or EC_PALETTE
    cat = {"type": "category", "data": labels,
           "axisLine": {"lineStyle": {"color": EC_AXIS[1]}}, "axisTick": {"show": False},
           "axisLabel": {"color": EC_AXIS[0], "fontSize": 11}}
    val = {"type": "value", "name": yname,
           "nameTextStyle": {"color": EC_AXIS[0], "fontSize": 11},
           "axisLine": {"show": False}, "axisTick": {"show": False},
           "axisLabel": {"color": EC_AXIS[0], "fontSize": 11},
           "splitLine": {"lineStyle": {"color": "#1e222b"}}}
    grid = ({"left": 130, "right": 44, "top": 14, "bottom": 26} if horizontal
            else {"left": 52, "right": 20, "top": 24, "bottom": 54})
    ser = {"type": "bar", "data": values, "barMaxWidth": 18,
           "itemStyle": {"borderRadius": [0, 3, 3, 0] if horizontal else [3, 3, 0, 0]},
           "label": {"show": True, "position": "right" if horizontal else "top",
                     "color": "#a3a9b5", "fontSize": 11}}
    if horizontal:
        ser["yAxis"] = cat
        return {"animation": False, "backgroundColor": "transparent",
                "grid": grid, "tooltip": {"trigger": "axis"},
                "xAxis": val, "yAxis": {**cat, "inverse": True},
                "series": [{**ser, "itemStyle": {"color": colors[0], "borderRadius": [0, 3, 3, 0]},
                            "data": [{"value": v, "itemStyle": {"color": colors[i % len(colors)]}}
                                     for i, v in enumerate(values)]}]}
    ser["xAxis"] = cat
    return {"animation": False, "backgroundColor": "transparent",
            "grid": grid, "tooltip": {"trigger": "axis"},
            "xAxis": {**cat, "axisLabel": {"color": EC_AXIS[0], "fontSize": 11, "interval": 0,
                                           "rotate": 30}},
            "yAxis": val, "series": [ser]}


def ec_stack_option(categories, series, colors=None):
    """堆叠柱 option。series=[{'name':..,'data':[..]}]"""
    colors = colors or EC_PALETTE
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "grid": {"left": 52, "right": 20, "top": 34, "bottom": 34},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 2, "textStyle": {"color": "#a3a9b5", "fontSize": 11},
                   "itemWidth": 14, "itemHeight": 8},
        "xAxis": {"type": "category", "data": categories,
                  "axisLine": {"lineStyle": {"color": EC_AXIS[1]}}, "axisTick": {"show": False},
                  "axisLabel": {"color": EC_AXIS[0], "fontSize": 11}},
        "yAxis": {"type": "value", "axisLine": {"show": False}, "axisTick": {"show": False},
                  "axisLabel": {"color": EC_AXIS[0], "fontSize": 11},
                  "splitLine": {"lineStyle": {"color": "#1e222b"}}},
        "series": [
            {"name": s["name"], "type": "bar", "stack": "t", "data": s["data"],
             "barMaxWidth": 26, "itemStyle": {"color": colors[i % len(colors)]}}
            for i, s in enumerate(series)
        ],
    }


def spark_cells(track, maxw=18):
    """重点 SPU 的逐次状态小方块：绿=正常 红=限流"""
    cells = "".join(
        f'<span title="{html.escape(t["at"])} · {"限流" if t["limited"] else "正常"}" '
        f'style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:3px;'
        f'background:{"var(--bad)" if t["limited"] else "var(--ok)"}"></span>'
        for t in track[-maxw:])
    return cells


def load_trend():
    if not TREND_JSON.exists():
        return None
    try:
        return json.loads(TREND_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def check_freshness(max_hours: float = 6.0):
    """生成前的闸门：监控数据必须是刚更新的，否则拒绝出图。

    2026-10-12 教训：抓取全失败时旧脚本仍 exit=0 并照常出图，
    结果面板拿几天前的旧数据显示「一切正常」。宁可不出，不可出错。
    返回 (ok, reason)。
    """
    p = DATA / "focus_watch_latest.json"
    if not p.exists():
        return False, "focus_watch_latest.json 不存在"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"监控结果无法解析: {e}"
    ok = [d for d in data if "error" not in d]
    if not ok:
        return False, "全部店铺抓取失败"
    ts = max((d.get("checked_at", "") for d in ok), default="")
    try:
        age = (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except ValueError:
        return False, f"checked_at 格式异常: {ts}"
    if age > max_hours:
        return False, f"监控数据已过期 {age:.1f} 小时（最后检查 {ts}）"
    return True, ts


def bar(items, maxv=None, color="var(--accent)"):
    """横向条形图（纯 div，无 JS）。items = [(label, value)]"""
    if not items:
        return ""
    mx = maxv or max(v for _, v in items) or 1
    out = []
    for lb, v in items:
        pct = max(2, round(v / mx * 100))
        out.append(
            f'<div class="bar-row"><span class="bar-lb" title="{html.escape(str(lb))}">{html.escape(str(lb))}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%;background:{color}"></span></span>'
            f'<span class="bar-v">{v:,}</span></div>'
        )
    return "".join(out)


CSS = """
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1c2029;--line:#272c36;--fg:#e6e8ec;
--fg2:#a3a9b5;--fg3:#6f7682;--accent:#599ce7;--ok:#2fa37c;--warn:#e0a33e;--bad:#e05561;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:28px 22px 64px}
h1{font-size:22px;margin:0;font-weight:600}
h2{font-size:17px;margin:0;font-weight:600}
h3{font-size:14px;margin:0;font-weight:600;color:var(--fg2)}
.sub{color:var(--fg3);font-size:12px;margin-top:6px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
padding-bottom:16px;border-bottom:1px solid var(--line);margin-bottom:24px}
.pill{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;
font-size:12px;border:1px solid var(--line);color:var(--fg2)}
.pill.bad{background:rgba(224,85,97,.14);border-color:rgba(224,85,97,.5);color:#f0a0a8}
.pill.ok{background:rgba(47,163,124,.14);border-color:rgba(47,163,124,.5);color:#7fd0b4}
.pill.warn{background:rgba(224,163,62,.14);border-color:rgba(224,163,62,.5);color:#e8c88a}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.stat .v{font-size:24px;font-weight:600;line-height:1.2}
.stat .l{font-size:12px;color:var(--fg3);margin-top:2px}
.stat.bad .v{color:var(--bad)}.stat.warn .v{color:var(--warn)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:16px 0;overflow:hidden}
.card>.hd{padding:12px 16px;border-bottom:1px solid var(--line);font-size:13px;font-weight:600;
display:flex;justify-content:space-between;align-items:center}
.card>.bd{padding:16px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--fg3);font-weight:500;font-size:12px;
padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
tr.bad td{background:rgba(224,85,97,.07)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ttl{max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.bar-row{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}
.bar-lb{width:132px;flex:none;color:var(--fg2);overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.bar-track{flex:1;height:9px;background:var(--panel2);border-radius:3px;overflow:hidden}
.bar-fill{display:block;height:100%;border-radius:3px}
.bar-v{width:56px;text-align:right;color:var(--fg);font-variant-numeric:tabular-nums;flex:none}
.cap{font-size:11px;color:var(--fg3);margin-top:10px}
.chart{width:100%}
.lgwrap{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:11px;color:var(--fg2)}
.lg{display:inline-flex;align-items:center;gap:5px}
.lg i{display:inline-block;width:9px;height:9px;border-radius:2px}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--panel2);
padding:1px 5px;border-radius:3px;color:#c8cdd6}
.note{font-size:12px;color:var(--fg3);line-height:1.8}
.k{color:var(--fg2)}
.sep{height:1px;background:var(--line);margin:26px 0}
a{color:var(--accent)}
"""


def build() -> str:
    global ECHARTS_JS
    stores = {k: load_store(*v[:2]) for k, v in DB.items()}
    labels = {k: v[2] for k, v in DB.items()}
    focus = load_focus("BEL") + load_focus("BHY")
    trend = load_trend()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chart_ids: list[str] = []

    hits = [f for f in focus if f["limited"]]
    safe = [f for f in focus if not f["limited"]]
    total_lim = sum(s["limited"] for s in stores.values())
    total_sku = sum(s["sku"] for s in stores.values())

    # ---- 告警区 ----
    money = lambda v: (f"${v:.2f}" if isinstance(v, (int, float)) else "—")
    if hits:
        rows = "".join(
            f'<tr class="bad"><td>{f["store"]}</td><td class="mono">{f["spu"]}</td>'
            f'<td class="mono">{" / ".join(f["codes"])}</td>'
            f'<td class="ttl" title="{html.escape(f["title"])}">{html.escape(f["title"])}</td>'
            f'<td class="num">{money(f["cur"])}</td>'
            f'<td class="num" style="color:var(--warn)">{money(f["daily"])}</td>'
            f'<td class="num" style="color:var(--warn)">{money(f["act"])}</td>'
            f'<td class="num" style="color:var(--bad)">'
            f'{money(f["cur"]-f["daily"]) if isinstance(f["cur"],(int,float)) and isinstance(f["daily"],(int,float)) else "—"}</td>'
            f'<td>{f["flow"]}</td></tr>' for f in hits)
        safe_by_store = {}
        for f in safe:
            safe_by_store.setdefault(f["store"], []).append(f)
        safe_html = ""
        for store, lst in safe_by_store.items():
            safe_html += (f'<div style="margin-top:10px"><b style="font-size:12px">{store}</b> '
                          + " ".join(f'<span class="pill ok">{f["spu"]} · {f["codes"][0] if f["codes"] else ""}</span>'
                                     for f in lst) + "</div>")
        alert_body = (
            f'<p style="margin:0 0 12px">{len(focus)} 个重点关注 SPU（BEL {len(FOCUS_BY_STORE["BEL"])} 个 / '
            f'BHY {len(FOCUS_BY_STORE["BHY"])} 个）中，有 '
            f'<b style="color:var(--bad)">{len(hits)} 个</b> 已被限流，需在 24 小时内调整申报价。</p>'
            '<table><thead><tr><th>店铺</th><th>SPU ID</th><th>货号(款号)</th><th>商品标题</th>'
            '<th class="num">当前申报价</th><th class="num">建议日常价</th>'
            '<th class="num">建议活动价</th><th class="num">需降</th><th>流量状态</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            '<div class="sep"></div><h3>安全（未被限流）</h3>'
            f'{safe_html}'
        )
        alert_badge = f'<span class="pill bad">重点款限流 {len(hits)}/{len(focus)}</span>'
    else:
        alert_body = f'<p style="margin:0;color:var(--ok)">本次检查：{len(focus)} 个重点关注 SPU 全部未被限流。</p>'
        alert_badge = '<span class="pill ok">重点款全部正常</span>'

    # ---- 店铺区块 ----
    def store_block(k):
        s = stores[k]
        flow_items = [(r[0], r[1]) for r in s["flow"]]
        cat_items = [(r[0], r[1]) for r in s["cats"]]
        stock_items = [(r[0], r[2]) for r in s["cats"]]
        top_rows = "".join(
            f'<tr><td class="mono">{html.escape(r[0])}</td>'
            f'<td class="num">${r[1]:.2f}</td>'
            f'<td class="num" style="color:var(--warn)">${r[2]:.2f}</td>'
            f'<td class="num">${r[1]-r[2]:.2f}</td>'
            f'<td class="num" style="color:var(--warn)">{(r[1]-r[2])/r[1]*100:.0f}%</td></tr>'
            for r in s["top"])
        return f"""
<section>
  <div style="display:flex;justify-content:space-between;align-items:center;margin:22px 0 12px">
    <h2>{labels[k]}</h2><span class="pill warn">限流 {s['limited']} 个</span>
  </div>
  <div class="stats">
    <div class="stat"><div class="v">{s['sku']}</div><div class="l">SKU</div></div>
    <div class="stat"><div class="v">{s['spu']}</div><div class="l">SPU</div></div>
    <div class="stat"><div class="v">{s['skc']}</div><div class="l">SKC</div></div>
    <div class="stat"><div class="v">{s['code']}</div><div class="l">款号</div></div>
    <div class="stat"><div class="v">{s['cat']}</div><div class="l">叶子类目</div></div>
    <div class="stat"><div class="v">{s['stock']:,}</div><div class="l">总库存</div></div>
  </div>
  <div class="grid2">
    <div class="card"><div class="hd">限流商品的流量状态分布（个）</div><div class="bd">
      {echarts_block(f"flow_{k}", ec_bar_option([r[0] for r in s["flow"]], [r[1] for r in s["flow"]], colors=["#e0a33e"], yname="个"), 300, chart_ids)}
      <div class="cap">数据源：TEMU 商品流量视角·待处理商品 · {now} 快照</div></div></div>
    <div class="card"><div class="hd">在售 SKU 数 Top10 叶子类目（个）</div><div class="bd">
      {echarts_block(f"catsku_{k}", ec_bar_option([r[0] for r in s["cats"]], [r[1] for r in s["cats"]], colors=["#599ce7"], yname="个"), 300, chart_ids)}
      <div class="cap">数据源：TEMU 商品列表·在售中导出 · {now} 快照</div></div></div>
  </div>
  <div class="grid2" style="margin-top:16px">
    <div class="card"><div class="hd">调价空间最大的 10 个款号（USD）</div><div class="bd">
      <table><thead><tr><th>货号(款号)</th><th class="num">当前申报价</th>
      <th class="num">建议日常价</th><th class="num">需降</th><th class="num">降幅</th></tr></thead>
      <tbody>{top_rows}</tbody></table></div></div>
    <div class="card"><div class="hd">类目库存 Top10（件）</div><div class="bd">
      {echarts_block(f"catstock_{k}", ec_bar_option([r[0] for r in s["cats"]], [r[2] for r in s["cats"]], colors=["#2fa37c"], yname="件"), 300, chart_ids)}
      <div class="cap">口径：在售 SKU 库存求和，按类目降序 · {now} 快照</div></div></div>
  </div>
</section>"""

    # ---- 趋势区块 ----
    trend_html = ""
    if trend and trend.get("timeline"):
        tl = trend["timeline"]
        days_n = trend.get("distinct_days", 0)
        snap_n = trend.get("snapshot_count", 0)
        # 按店 + 天取最后一次快照（同一天多次运行取最新）
        by_store_day = {}
        for t in tl:
            by_store_day[(t.get("store", "?"), t["day"])] = t
        stores_seen = sorted({t.get("store", "?") for t in tl})
        FLOW_KEYS = ["流量预计100%+", "流量预计80%+", "流量预计70%+", "流量预计60%+",
                     "流量预计50%+", "流量预计40%+", "流量预计10%+"]
        day_axis = sorted({t["day"] for t in tl})
        # 只保留出现过的状态键，避免图例全是 0
        flow_keys = [k for k in FLOW_KEYS
                     if any(k in (t.get("flow") or {}) for t in tl)]

        cats = [d[5:] for d in day_axis]

        def stack_series(key):
            return [{"name": k, "data": [by_store_day[(key, d)].get("flow", {}).get(k, 0)
                                          if (key, d) in by_store_day else 0
                                          for d in day_axis]}
                    for k in flow_keys]

        def total_series(key):
            return ([{"name": "限流商品数",
                      "data": [by_store_day[(key, d)]["limited_total"]
                               if (key, d) in by_store_day else 0 for d in day_axis]},
                     {"name": "重点SPU命中数",
                      "data": [by_store_day[(key, d)]["hit_count"]
                               if (key, d) in by_store_day else 0 for d in day_axis]}])

        charts = ""
        for key in stores_seen:
            charts += f"""
  <div class="card"><div class="hd">{html.escape(key)} 店 · 每日限流商品总数走势（个）</div><div class="bd">
    {echarts_block(f"trline_{key}", ec_line_option(cats, total_series(key), colors=["#e0a33e", "#e05561"], yname="个"), 280, chart_ids)}
    <div class="cap">X 轴：日期（月-日）· Y 轴：当日该店限流商品数（个）· 同日多次运行取最新快照</div>
  </div></div>
  <div class="card"><div class="hd">{html.escape(key)} 店 · 每日限流商品的流量状态构成（个）</div><div class="bd">
    {echarts_block(f"trstack_{key}", ec_stack_option(cats, stack_series(key)), 280, chart_ids)}
    <div class="cap">X 轴：日期（月-日）· Y 轴：当日限流商品数（个），按流量状态堆叠</div>
  </div></div>"""

        # 反复被限流排行（跨天优先）
        rec = trend.get("recurring", [])
        persistent = [r for r in rec if r["days"] >= 2]
        rec_rows = "".join(
            f'<tr><td class="mono">{html.escape(r["sku_code"])}</td>'
            f'<td class="mono">{html.escape(r["spu_id"])}</td>'
            f'<td class="num">{r["appear"]}</td>'
            f'<td class="num">{r["days"]}</td>'
            f'<td>{html.escape(r["first_seen"][:10])} → {html.escape(r["last_seen"][:10])}</td>'
            f'<td>{html.escape(r["flow_status"])}</td>'
            f'<td class="num">{r["sales7"] if r["sales7"] is not None else "—"}</td></tr>'
            for r in (persistent or rec)[:12])
        rec_head = ("<th>货号(款号)</th><th>SPU ID</th><th class=num>出现次数</th>"
                    "<th class=num>跨天数</th><th>首次 → 最近</th><th>流量状态</th>"
                    "<th class=num>近7日销量</th>").replace("class=num", 'class="num"')

        track_rows = "".join(
            f'<tr class="{"bad" if f["current"] else ""}">'
            f'<td>{f.get("store","BEL")}</td>'
            f'<td class="mono">{f["spu"]}</td>'
            f'<td>{spark_cells(f["timeline"])}</td>'
            f'<td class="num">{f["hit_times"]}/{f["checks"]}</td>'
            f'<td>{"<span class=pill bad>限流中</span>" if f["current"] else "<span class=pill ok>正常</span>"}</td>'
            f'<td class="num">{html.escape(f["last"][:10])}</td></tr>'
            for f in trend.get("focus_track", []))

        resolved = trend.get("resolved", [])
        res_html = ""
        if resolved:
            items = " ".join(
                f'<span class="pill ok">{html.escape(r["sku_code"])} · {html.escape(r["cleared_at"][:10])}</span>'
                for r in resolved[:20])
            res_html = (f'<h3 style="margin:16px 0 8px">已解除限流（{len(resolved)} 个）</h3>'
                        f'<div>{items}</div>')

        warn = ""
        if days_n < 2:
            warn = (f'<div class="cap" style="color:var(--warn)">'
                    f'当前仅覆盖 {days_n} 天数据，折线趋势需至少 2 天。'
                    f'定时任务每天 08:00 追加一条快照，明日起即可看到按天走势。</div>')

        trend_html = f"""
<div class="sep"></div>
<h2>限流历史趋势</h2>
<p class="sub">来源：focus_watch_history.jsonl · 共 {snap_n} 次快照，覆盖 {days_n} 天
（{html.escape(trend.get('first_check','')[:16])} → {html.escape(trend.get('last_check','')[:16])}）</p>
{warn}
<div class="grid2" style="margin-top:12px">
{charts}
</div>
<div class="card"><div class="hd">反复被限流货号排行{'(跨天)' if persistent else '(按出现次数，暂无跨天数据)'}</div><div class="bd">
  <table><thead><tr>{rec_head}</tr></thead><tbody>{rec_rows}</tbody></table>
  <div class="cap">「跨天数」= 该货号出现在多少个不同日期的快照中；数值越高说明调价后流量仍未恢复</div>
</div></div>
<div class="card"><div class="hd">重点 SPU 逐次监控状态</div><div class="bd">
  <table><thead><tr><th>店铺</th><th>SPU ID</th><th>状态序列（由左至右）</th>
  <th class="num">限流/检查</th><th>当前</th><th class="num">最近检查</th></tr></thead>
  <tbody>{track_rows}</tbody></table>
  <div class="cap">绿=正常，红=被限流；最多显示最近 18 次检查</div>
  {res_html}
</div></div>"""

    focus_rows = "".join(
        f'<tr class="{"bad" if f["limited"] else ""}">'
        f'<td><span class="pill">{f["store"]}</span></td>'
        f'<td class="mono">{f["spu"]}</td>'
        f'<td>{html.escape(f["cat"])}</td>'
        f'<td class="mono">{" / ".join(f["codes"])}</td>'
        f'<td class="num">{f["sku"]}</td><td class="num">{f["stock"]:,}</td>'
        f'<td>{"<span class=pill bad>限流</span>" if f["limited"] else "<span class=pill ok>正常</span>"}</td>'
        f'<td class="num">{money(f["cur"]) + " → " + money(f["daily"]) if f["limited"] else "—"}</td></tr>'
        for f in focus)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TEMU 店铺运营看板 · {now}</title><style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>TEMU 店铺运营看板</h1>{alert_badge}
  <span class="pill">BEL · BHY</span>
  <div style="flex:1"></div>
  <span class="pill">生成于 {now}</span>
</header>

<div class="stats">
  <div class="stat"><div class="v">{total_sku}</div><div class="l">两店 SKU 合计</div></div>
  <div class="stat"><div class="v">{stores['BEL']['spu']+stores['BHY']['spu']}</div><div class="l">两店 SPU 合计</div></div>
  <div class="stat warn"><div class="v">{total_lim}</div><div class="l">限流商品合计</div></div>
  <div class="stat {'bad' if hits else 'ok'}"><div class="v">{len(hits)} 个</div><div class="l">重点 SPU 被限流</div></div>
</div>

<div class="card"><div class="hd">重点关注 SPU · 限流告警
  <span class="pill {'bad' if hits else 'ok'}">{'需立即处理' if hits else '正常'}</span></div>
  <div class="bd">{alert_body}</div></div>

{store_block('BEL')}
<div class="sep"></div>
{store_block('BHY')}

{trend_html}

<div class="sep"></div>
<h2>重点关注 SPU 全量台账</h2>
<p class="sub">BEL {len(FOCUS_BY_STORE["BEL"])} 个 + BHY {len(FOCUS_BY_STORE["BHY"])} 个，限流状态取自监控快照</p>
<div class="card"><div class="bd"><table>
<thead><tr><th>店铺</th><th>SPU ID</th><th>叶子类目</th><th>款号</th><th class="num">SKU 数</th>
<th class="num">库存</th><th>限流</th><th class="num">申报价 → 建议日常价</th></tr></thead>
<tbody>{focus_rows}</tbody></table></div></div>

<div class="sep"></div>
<div class="card"><div class="hd">监控机制</div><div class="bd note">
<p style="margin:0 0 8px"><span class="k">脚本</span> <code>scripts/bel_goods/watch_focus.py</code>
 — 自动把店铺窗口切到商品流量视角页，抓取「待处理商品」清单并比对重点 SPU 名单。</p>
<p style="margin:0 0 8px"><span class="k">调度</span> 每天 08:00 自动运行（Windows 计划任务 <code>TEMU限流监控</code>），
顺序：抓限流 → 聚合趋势 → 刷面板 → 推飞书。</p>
<p style="margin:0 0 8px"><span class="k">产出</span> <code>data/focus_watch_latest.json</code>（覆盖）
、<code>data/focus_watch_history.jsonl</code>（追加历史）。命中重点款时退出码为 1。</p>
<p style="margin:0 0 8px"><span class="k">图表</span> Apache ECharts <code>echarts.min.js</code> 已内联进本文件，
单文件离线可用，双击即可打开。</p>
<p style="margin:0"><span class="k">口径</span> 全部只读，不会代点「一键提效」等任何写操作按钮。</p>
</div></div>
</div>
<script>{ECHARTS_JS}</script>
<script>
(function () {{
  var ids = {json.dumps(chart_ids)};
  var insts = [];
  ids.forEach(function (id) {{
    var el = document.getElementById(id);
    if (!el || typeof echarts === 'undefined') return;
    var opt = window['__OPT_' + id + '__'];
    if (!opt) return;
    var c = echarts.init(el, null, {{renderer: 'canvas'}});
    c.setOption(opt);
    insts.push(c);
  }});
  window.addEventListener('resize', function () {{
    insts.forEach(function (c) {{ try {{ c.resize(); }} catch (e) {{}} }});
  }});
}})();
</script></body></html>"""


def main():
    global ECHARTS_JS
    if not VENDOR.exists():
        print(f"缺少图表库: {VENDOR}", file=sys.stderr)
        return 2

    # 数据闸门：不新鲜就拒绝出图（保留上一次的产物，不覆盖成错的）
    ap = argparse.ArgumentParser(description="生成 TEMU 运营看板")
    ap.add_argument("--allow-stale", action="store_true",
                    help="跳过新鲜度检查（仅在手动查看历史数据时用）")
    a = ap.parse_args()

    if not a.allow_stale:
        fresh, reason = check_freshness()
        if not fresh:
            print(f"[DASH] 拒绝生成：{reason}", file=sys.stderr)
            print("[DASH] 保留上一次的看板文件；先修好监控或加 --allow-stale 强制生成",
                  file=sys.stderr)
            return 5
        print(f"[DASH] 数据新鲜（{reason}）")

    ECHARTS_JS = VENDOR.read_text(encoding="utf-8")
    doc = build()
    OUT.write_text(doc, encoding="utf-8")
    print(f"已生成 {OUT}  ({len(doc):,} 字节，含内联 ECharts {len(ECHARTS_JS):,} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())