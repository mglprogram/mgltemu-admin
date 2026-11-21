# -*- coding: utf-8 -*-
"""整理「今天改过库存的货号-尺码清单」。

数据来源：
  · `_logs/hx_kls_13c.log` / `13d` / `13e` —— KLS 加恒信仓 + 加州3仓清零（提交成功的款）
  · `_logs/switch_watch.jsonl` + `_logs/api_switch.jsonl` —— BEL 发货模式切换
  · `_logs/kls_combo_*.jsonl` —— KLS 组合品切回
输出：`_logs/changed_sku_list.md` / `.csv`
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

LOGS = Path(__file__).resolve().parents[2] / "_logs"
OUT_MD = LOGS / "changed_sku_list.md"
OUT_CSV = LOGS / "changed_sku_list.csv"


def read_log(name: str) -> str:
    p = LOGS / name
    if not p.exists():
        return ""
    raw = p.read_bytes()
    # 这些日志是 PowerShell `*>` 重定向出来的 UTF-16LE
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:200]:
            return raw.decode("utf-16-le", "replace").replace("\x00", "")
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


def parse_hx_logs() -> list[dict]:
    """从 add_hx_warehouse 的日志里取出「提交成功」的款，解析每个尺码填了多少。"""
    rows: list[dict] = []
    for name in ("hx_kls_13c.log", "hx_kls_13d.log", "hx_kls_13e.log"):
        txt = read_log(name)
        if not txt:
            continue
        cur_spu = cur_code = ""
        last_fill: list[str] = []
        last_zero: list[str] = []
        for line in txt.splitlines():
            m = re.match(r"^\[(\d+)/(\d+)\]\s+SPU\s+(\d+)", line.strip())
            if m:
                cur_spu, last_fill, last_zero = m.group(3), [], []
                continue
            if "恒信仓填量" in line:
                q = re.search(r"（([^）]*)）", line)
                # 日志里只有 code 才知道是哪个货号，先记下 raw
                if q:
                    last_fill = ["直方: " + q.group(1)]
                continue
            m2 = re.search(r"(\w+)\s+已勾\s+恒信.*?填数\s+(\[.*\])", line)
            if m2:
                last_fill = re.findall(r"'([^']*)'", m2.group(2))
                continue
            m3 = re.search(r"(\w+)\s+加州3仓\s+清零：(\[.*\])", line)
            if m3:
                last_zero = re.findall(r"'([^']*)'", m3.group(2))
                continue
            m4 = re.search(r"^\"spu\": \"(\d+)\".*?\"ok\": true", line.strip())
            m4b = re.search(r"^\{.*?\"spu\": \"(\d+)\".*?\"ok\": true", line.strip())
            hit = m4 or m4b
            if hit and cur_spu:
                # 同一行 JSON 里就有 SPU，货号从下面「已勾」行或后续日志拿不到，用 spu→code 映射补
                rows.append({"spu": hit.group(1), "code": "",
                             "filled": list(last_fill), "zeroed": len(last_zero)})
                continue
            m5 = re.search(r"^(\S+)\s+提交成功（页面已返回商品列表）", line.strip())
            if m5 and cur_spu:
                rows.append({"spu": cur_spu, "code": m5.group(1),
                             "filled": list(last_fill), "zeroed": len(last_zero)})
    return rows


def parse_bel_switch() -> list[dict]:
    rows = []
    p = LOGS / "switch_watch.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("event") == "done" and r.get("sku"):
                rows.append({"store": "BEL", "sku": r["sku"], "stock": r.get("filled"),
                             "base": r.get("base")})
    return rows


def main():
    hx = parse_hx_logs()
    # 货号：优先用日志里带的，没有就用 SPU→货号映射
    cm = {}
    cmf = LOGS / "kls_spu_code.json"
    if cmf.exists():
        cm = json.loads(cmf.read_text(encoding="utf-8"))
    for r in hx:
        if not r.get("code"):
            r["code"] = cm.get(r["spu"], r["spu"])
    # 去重（同一款可能重跑过）
    seen = {}
    for r in hx:
        if r.get("code") and r["code"] == r["spu"]:
            continue          # 没解析出货号的丢弃，不写进清单
        seen[(r["spu"], r["code"])] = r
    hx = list(seen.values())
    hx.sort(key=lambda r: r["code"])

    bel = parse_bel_switch()

    lines = ["# 今天改过库存/发货模式的货号-尺码清单", "",
             "生成时间：2026-11-19", ""]
    lines += [f"## KLS · 恒信加州3 加库存 + 加州3仓清零（{len(hx)} 个货号，已提交成功）", ""]
    lines.append("| SPU | 货号 | 恒信仓填的数值 | 加州3仓清零 |")
    lines.append("|---|---|---|---|")
    for r in hx:
        vals = " / ".join(r["filled"]) if r["filled"] else "（0）"
        lines.append(f"| {r['spu']} | {r['code']} | {vals} | {'是' if r['zeroed'] else '否'} |")
    lines.append("")

    lines += [f"## BEL · 发货模式切到卖家自发货（{len(bel)} 个尺码）", ""]
    if bel:
        lines.append("| SKU | 原库存 | 填入（加州3仓/kansas仓） |")
        lines.append("|---|---|---|")
        for r in bel:
            lines.append(f"| {r['sku']} | {r.get('base')} | {r.get('stock')} |")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["店", "SPU", "货号", "改了什么", "数值", "加州3仓清零"])
        for r in hx:
            w.writerow(["KLS", r["spu"], r["code"], "加恒信仓库存",
                        " / ".join(r["filled"]), "是" if r["zeroed"] else "否"])
        for r in bel:
            w.writerow(["BEL", "", r["sku"], "切卖家自发货", r.get("stock"), ""])

    print("KLS 货号", len(hx), "；BEL 尺码", len(bel))
    print("→", OUT_MD)
    print("→", OUT_CSV)


if __name__ == "__main__":
    main()
