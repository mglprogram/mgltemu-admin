# -*- coding: utf-8 -*-
"""temu_stock_topup.py — TEMU「立即加库存」补量计算域驱动。

一条链两个数据源：
  ① **海外仓库存看板**（warehouse-probe.app.workbuddy.host，静态页，数据内嵌在 `const SNAP = {...}`）
     提供 `近7天日均` / `代发库存` / `代发途中` / `未交货订单数`（键 = 小款号）。
  ② **TEMU 销量分析页**（agentseller.temu.com/main/data-center/goods-data）
     提供每个 SKU 的 `SKU货号` / `SKU ID` / `销售可用库存`（走 `cdp_transport` target-level CDP）。

口径（用户 2026-10-10 确认，配置在 data/temu_stock_topup.json）：
    增加量 = round(近7天日均 × 目标天数) − 代发库存 − TEMU现有销售可用库存   （负数记 0）
    其中 `近7天日均` / `代发库存` 先按 `变体换算` 折成 TEMU 单位：
        TEMU 的 10SetX 一件 = 看板 5SetX 两件（factor = 看板单位 ÷ TEMU 单位 = 2）

本模块只做「取数 + 算数」，**不点任何按钮、不改 TEMU 页面**（写操作在入口脚本里另留开关）。
"""
from __future__ import annotations

import json
import math
import re
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_CFG_PATH = HERE.parent / "data" / "temu_stock_topup.json"


# ------------------------------------------------------------------ 配置
def load_cfg(path: Optional[str] = None) -> dict:
    p = Path(path) if path else DEFAULT_CFG_PATH
    if not p.exists():
        raise FileNotFoundError("找不到口径配置: %s" % p)
    with p.open(encoding="utf-8-sig") as f:
        return json.load(f)


# ------------------------------------------------------------------ ① 看板快照
def _snapshot_cache_path(cfg: dict) -> Path:
    out = Path(cfg.get("输出目录") or "out")
    if not out.is_absolute():
        out = HERE.parent / out
    return out / "probe_snap_cache.json"


def _fetch_html(url: str, timeout: float = 60.0) -> str:
    """取看板 HTML。**绕开系统代理**（本机系统代理 127.0.0.1:10808 会打断 Cloudflare 直连）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def extract_snap(html: str) -> dict:
    """从看板 HTML 里抠出 `const SNAP = {...}` 那个大对象（括号配平 + 跳过字符串）。"""
    marker = "const SNAP ="
    i = html.index(marker) + len(marker)
    while i < len(html) and html[i] in " \r\n\t":
        i += 1
    if html[i] != "{":
        raise ValueError("SNAP 不是 JSON 对象（实际 %r）" % html[i])
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
    raise ValueError("SNAP 括号不闭合")


def fetch_snapshot(cfg: dict, force: bool = False, log=print) -> dict:
    """取看板快照（带磁盘缓存）。返回 {rows, built_at, sources, _fetched_at, _from_cache}。"""
    cache = _snapshot_cache_path(cfg)
    ttl = float((cfg.get("看板") or {}).get("缓存秒") or 0)
    if not force and cache.exists() and ttl > 0:
        age = time.time() - cache.stat().st_mtime
        if age < ttl:
            snap = json.loads(cache.read_text(encoding="utf-8"))
            snap["_from_cache"] = True
            log("[看板] 用缓存（%.0fs 前抓的，TTL %.0fs）built_at=%s"
                % (age, ttl, snap.get("built_at")))
            return snap
    url = (cfg.get("看板") or {}).get("url") or "https://warehouse-probe.app.workbuddy.host/"
    log("[看板] 下载 %s" % url)
    # ⚠ 看板偶发连接超时（实测 2026-10-13：BEL 整店因此全废，紧接着再拉就好）
    #   ⇒ 重试 3 次；仍失败则退回磁盘缓存（宁可数据略旧，也别让整条链断掉）。
    html = None
    last_err = None
    for attempt in range(3):
        try:
            html = _fetch_html(url)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log("[看板] 第 %d 次下载失败：%s" % (attempt + 1, str(e)[:80]))
            time.sleep(3)
    if html is None:
        if cache.exists():
            snap = json.loads(cache.read_text(encoding="utf-8"))
            snap["_from_cache"] = True
            snap["stale"] = True
            log("[看板] !! 下载失败，退回缓存 built_at=%s（标记 stale）" % snap.get("built_at"))
            return snap
        raise RuntimeError("看板下载失败且无缓存：%s" % str(last_err)[:120])
    snap = extract_snap(html)
    snap["_source_url"] = url
    snap["_fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    snap["_from_cache"] = False
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    log("[看板] rows=%d built_at=%s" % (len(snap.get("rows") or []), snap.get("built_at")))
    return snap


def aggregate_by_sku(rows: list) -> dict:
    """按「小款号」合并（跨货源/店铺/仓库相加）；7天日均/未交货订单数是款级字段，取一次。

    额外给出 `按仓`：`{仓库名: {代发库存, 代发途中, 货源}}`。
    用户 2026-10-12 要看「这些库存数在领星还是帕沃尔」——看板每行带 `货源`（帕沃尔/恒信…）
    与 `仓库名称`（帕沃尔·kansas仓 等），这里按仓拆开留着给前端悬浮展示。
    """
    agg: dict[str, dict] = {}
    for r in rows or []:
        k = r.get("小款号")
        if not k:
            continue
        a = agg.setdefault(k, {"小款号": k, "代发库存": 0, "代发途中": 0, "10天销量": 0,
                               "近7天日均": None, "未交货订单数": None, "仓库": [],
                               "按仓": {}})
        for f in ("代发库存", "代发途中", "10天销量"):
            v = r.get(f)
            if isinstance(v, (int, float)):
                a[f] += v
        wh = r.get("货源仓") or r.get("仓库名称")
        # ⚠ 看板里有**没写仓库名**的行（`仓库名称` 为空）——之前会让 `按仓` 里多出一个
        #   `None` 键，前端悬浮面板就渲染出一条空白行（2026-10-12 实测）。
        #   这种行只计入总数，不进 `按仓` 明细。
        if wh and wh not in a["仓库"]:
            a["仓库"].append(wh)
        if wh:
            w = a["按仓"].setdefault(wh, {"仓库": wh, "货源": r.get("货源") or "",
                                          "代发库存": 0, "代发途中": 0})
            for f in ("代发库存", "代发途中"):
                v = r.get(f)
                if isinstance(v, (int, float)):
                    w[f] += v
            if not w["货源"]:
                w["货源"] = r.get("货源") or ""
        for f in ("近7天日均", "未交货订单数"):
            v = r.get(f)
            if v is not None and a[f] is None:
                a[f] = v
    return agg


# ------------------------------------------------------------------ ② 款号解析 / 变体换算
def parse_sku_code(code: str, cfg: dict) -> dict:
    """`BHY-202504-10SetA-L` → {shop, product, variant, size, ok}。

    前缀正则去掉店铺段（BHY-/BEL-/BS2-…），余部按**最后一个** `-` 切尺码，
    再按**倒数第二个** `-` 切变体（尺码后面没有东西了）。
    """
    out = {"原始": code, "ok": False, "为什么": ""}
    s = (code or "").strip()
    prefix_re = cfg.get("店铺前缀正则") or r"^[A-Za-z]{2,4}\d?-"
    m = re.match(prefix_re, s)
    shop = ""
    if m:
        shop = m.group(0).rstrip("-")
        s = s[m.end():]
    parts = s.split("-")
    if len(parts) < 3:
        out["为什么"] = "SKU货号段数不足（期望 产品-变体-尺码）：%s" % code
        return out
    out.update({"shop": shop, "product": parts[0], "size": parts[-1],
                "variant": "-".join(parts[1:-1]), "ok": True})
    return out


def strip_trailing_digits(variant: str, cfg: dict) -> str:
    """剥掉变体名末尾的 1~2 位数字（用户 2026-10-12 口径）。

    TEMU 侧有些货号被多打了一个序号：`3BHL1`→`3BHL`、`1B1`→`1B`、`5SetA1`→`5SetA`、
    `5SetC2`→`5SetC`。**注意别误伤 `10SetA` 这种本身以数字结尾、且已配换算的变体**：
    调用方应「先按原样查，查不到再剥」；本函数只负责剥，不做判断。
    例外表里的变体（`_例外`）不参与剥离。
    """
    rule = cfg.get("变体末尾数字") or {}
    if not rule.get("剥末尾数字"):
        return variant
    if variant in (rule.get("_例外") or []):
        return variant
    m = re.match(r"^(.*?)(\d{1,2})$", variant or "")
    return m.group(1) if m and m.group(1) else variant


def map_variant(variant: str, cfg: dict) -> dict:
    """变体 → 看板变体 + 换算系数（factor = 看板单位 ÷ TEMU 单位）。"""
    tbl = cfg.get("变体换算") or {}
    rule = tbl.get(variant)
    if isinstance(rule, dict):
        return {"看板变体": rule.get("看板变体") or variant,
                "factor": float(rule.get("factor") or 1),
                "换算说明": "TEMU %s = 看板 %s × %s" % (variant, rule.get("看板变体"), rule.get("factor"))}
    return {"看板变体": variant, "factor": 1.0, "换算说明": "原样对应（无换算规则）"}


def map_variant_fallback(variant: str, cfg: dict, agg: dict, product: str,
                         size: str) -> tuple:
    """原始变体查看板查不到时的兜底：剥掉货号末尾多打的序号再试一次。

    返回 `(conv, 说明)`；没兜底成功返回 `(None, "")`。

    ⚠ **只在原变体没有配置换算时才剥**：`10SetA` 这类本身以数字结尾、且已在 `变体换算`
    里配好（看板变体 5SetA / factor 2）的，绝不能剥成 `10Set`。
    """
    if (cfg.get("变体换算") or {}).get(variant):
        return None, ""
    stripped = strip_trailing_digits(variant, cfg)
    if stripped and stripped != variant:
        conv = map_variant(stripped, cfg)
        key = "%s-%s-%s" % (product, conv["看板变体"], size)
        if key in (agg or {}):
            return conv, "货号末尾数字已忽略（%s → %s）" % (variant, key)
    return None, ""



def round_half_up(x: float) -> int:
    """四舍五入（half-up）。Python 内置 round 是 banker's rounding，round(418.5)=418 会少 1。"""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


# ------------------------------------------------------------------ ③ 计算
def plan_item(sku_code: str, available: Optional[float], agg: dict, cfg: dict,
              target_days: Optional[float] = None, sku_id: str = "",
              product_info: str = "") -> dict:
    """单个 SKU 的补量计算（返回带全过程的 dict，方便人核对）。"""
    days = float(target_days if target_days is not None else (cfg.get("目标天数") or 30))
    item: dict[str, Any] = {"SKU货号": sku_code, "SKU ID": sku_id, "商品": product_info,
                            "TEMU可售库存": available, "目标天数": days}
    parsed = parse_sku_code(sku_code, cfg)
    if not parsed["ok"]:
        item.update({"结论": "跳过", "原因": parsed["为什么"]})
        return item
    conv = map_variant(parsed["variant"], cfg)
    probe_key = "%s-%s-%s" % (parsed["product"], conv["看板变体"], parsed["size"])
    item.update({"店铺": parsed["shop"], "产品": parsed["product"], "变体": parsed["variant"],
                 "尺码": parsed["size"], "看板小款号": probe_key,
                 "换算": conv["换算说明"], "factor": conv["factor"]})
    src = (agg or {}).get(probe_key)
    if not src:
        item.update({"结论": "跳过", "原因": "看板里没有这个小款号（%s），无法取数" % probe_key})
        return item
    d7_raw = src.get("近7天日均")
    if d7_raw is None:
        item.update({"结论": "跳过", "原因": "看板台账里这个款没有 7天日均（缺数据≠0）"})
        return item
    f = conv["factor"]
    stock_t = float(src.get("代发库存") or 0) / f      # 折成 TEMU 单位
    d7_t = float(d7_raw) / f
    need = d7_t * days
    item.update({
        "看板_近7天日均": d7_raw,
        "看板_代发库存": src.get("代发库存"),
        "看板_代发途中": src.get("代发途中"),
        "TEMU单位_近7天日均": round(d7_t, 4),
        "TEMU单位_代发库存": round(stock_t, 4),
        "TEMU单位_目标天数需求": round(need, 4),
    })
    deduct = cfg.get("扣减项") or ["代发库存", "TEMU可售"]
    cut = 0.0
    if "代发库存" in deduct:
        cut += stock_t
    if "TEMU可售" in deduct:
        if available is None:
            item.update({"结论": "跳过", "原因": "TEMU 现有销售可用库存读不到，而口径要求扣它"})
            return item
        cut += float(available)
    raw = need - cut
    add = max(0, round_half_up(raw))
    item.update({"扣减合计": round(cut, 4), "净缺口(未取整)": round(raw, 4),
                 "建议增加量": add,
                 "增加后TEMU可售": (None if available is None else float(available) + add),
                 "结论": "需加库存" if add > 0 else "不用加"})
    return item


def build_plan(skus: list, agg: dict, cfg: dict, target_days: Optional[float] = None) -> list:
    """skus: [{'SKU货号':…, '可售库存':…, 'SKU ID':…, '商品':…}, …]"""
    out = []
    for s in skus:
        out.append(plan_item(s.get("SKU货号") or s.get("sku_code") or "",
                             s.get("可售库存", s.get("available")),
                             agg, cfg, target_days,
                             sku_id=s.get("SKU ID", s.get("sku_id", "")),
                             product_info=s.get("商品", s.get("product", ""))))
    return out


# ------------------------------------------------------------------ ④ TEMU 页面取数（只读）
_LIVE_JS = r"""
() => {
  const num = t => { const m = String(t||'').replace(/,/g,'').match(/-?\d+(\.\d+)?/); return m ? Number(m[0]) : null; };
  const out = [];
  let prod = '', prodTitle = '';
  for (const tr of document.querySelectorAll('tbody tr')) {
    const cells = [...tr.children].map(td => (td.innerText || '').trim().replace(/\s+/g, ' '));
    const joined = cells.join(' | ');
    const pm = joined.match(/SPU[：:]\s*(\d+)[\s\S]*?SKC[：:]\s*(\d+)[\s\S]*?SKC货号[：:]\s*([A-Za-z0-9\-]+)/);
    if (pm) {
      prod = 'SPU ' + pm[1] + ' / SKC ' + pm[2] + ' / SKC货号 ' + pm[3];
      const own = cells.find(c => c.indexOf('SPU') >= 0) || '';
      prodTitle = own.split('SPU')[0].trim();
    }
    const cm = joined.match(/SKU ID[：:]\s*(\d+)[\s\S]*?SKU货号[：:]\s*([A-Za-z0-9\-]+)/);
    if (!cm) continue;
    // 销售可用库存 = 「发货模式」单元格的下一个单元格（列会因 rowspan 合并而错位，不按固定下标取）
    let avail = null;
    for (let i = 0; i < cells.length; i++) {
      if (/发货$/.test(cells[i]) || cells[i] === '卖家自发货' || cells[i] === '平台发货') {
        avail = num(cells[i + 1]); break;
      }
    }
    out.push({sku_code: cm[2], sku_id: cm[1], available: avail, product: prod, title: prodTitle});
  }
  return out;
}
"""


def read_live_rows(port: int, url_part: str = "data-center/goods-data") -> list:
    """从紫鸟店窗口的「销量分析」标签页读 SKU 行（**只读**，走 target-level CDP）。

    一个窗口里可能同时开着**两个域名**的销量分析页（`agentseller.temu.com` 中国站 /
    `agentseller-us.temu.com` 美国站），而其中一个常是「该区暂无权限」的空壳。
    所以这里**逐页试读、取行数最多的那一页**，不认标签顺序。
    """
    import sys
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from cdp_transport import connect  # noqa: WPS433  (域内共享传输层)

    browser = connect(int(port))
    pages = [p for p in browser.contexts[0].pages if url_part in (p.url or "")]
    if not pages:
        raise RuntimeError("端口 %d 上没找到 %s 标签页" % (port, url_part))
    best: list = []
    for p in pages:
        try:
            rows = p.evaluate(_LIVE_JS) or []
        except Exception:  # noqa: BLE001  单个标签页读失败不影响其它页
            rows = []
        if len(rows) > len(best):
            best = rows
    return best


# ------------------------------------------------------------------ 报告
def render_markdown(items: list, cfg: dict, snapshot_meta: dict, source_note: str = "") -> str:
    lines = []
    lines.append("# TEMU 加库存建议清单（dry-run）")
    lines.append("")
    lines.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 看板快照：%s（%s）" % (snapshot_meta.get("built_at") or "?",
                                       "缓存" if snapshot_meta.get("_from_cache") else "实时下载"))
    lines.append("- 口径：%s" % (cfg.get("口径") or ""))
    if source_note:
        lines.append("- TEMU 侧数据来源：%s" % source_note)
    lines.append("")
    lines.append("| SKU货号 | 尺码 | 看板小款号 | 7天日均(看板) | 代发库存(看板) | "
                 "折TEMU日均 | 折TEMU库存 | TEMU可售 | %s天需求 | **建议增加量** | 结论 |"
                 % int(cfg.get("目标天数") or 30))
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for it in items:
        if it.get("结论") in ("跳过",):
            lines.append("| %s | %s | %s | — | — | — | — | %s | — | — | %s：%s |"
                         % (it.get("SKU货号"), it.get("尺码", "—"), it.get("看板小款号", "—"),
                            it.get("TEMU可售库存", "—"), it.get("结论"), it.get("原因", "")))
            continue
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | **%s** | %s |" % (
            it.get("SKU货号"), it.get("尺码"), it.get("看板小款号"),
            it.get("看板_近7天日均"), it.get("看板_代发库存"),
            it.get("TEMU单位_近7天日均"), it.get("TEMU单位_代发库存"),
            it.get("TEMU可售库存"), it.get("TEMU单位_目标天数需求"),
            it.get("建议增加量"), it.get("结论")))
    lines.append("")
    lines.append("> 说明：这是**只读**产物，还没有在 TEMU 上提交任何改动。")
    return "\n".join(lines)


def print_table(items: list) -> None:
    hdr = ("%-26s %-5s %-18s %8s %9s %9s %9s %8s %10s %9s" %
           ("SKU货号", "尺码", "看板小款号", "日均", "库存", "折日均", "折库存", "T可售", "需求", "建议加"))
    print(hdr)
    print("-" * len(hdr))
    for it in items:
        if it.get("结论") == "跳过":
            print("%-26s %-5s %-18s  %s" % (it.get("SKU货号"), it.get("尺码", "-"),
                                            it.get("看板小款号", "-"), "跳过：" + str(it.get("原因"))[:60]))
            continue
        print("%-26s %-5s %-18s %8s %9s %9s %9s %8s %10s %9s" % (
            it.get("SKU货号"), it.get("尺码"), it.get("看板小款号"),
            it.get("看板_近7天日均"), it.get("看板_代发库存"),
            it.get("TEMU单位_近7天日均"), it.get("TEMU单位_代发库存"),
            it.get("TEMU可售库存"), it.get("TEMU单位_目标天数需求"), it.get("建议增加量")))
