# -*- coding: utf-8 -*-
"""pw_index_deliver.py — 把帕沃尔 deliver.php 的**全部**出库单建成本地索引（只读）。

为什么要它（2026-11-06 我自己的翻车）：
    用 `deliver.php?act=list&pb=2&deliver_no=<单号>` 做「逐单核对」，
    124 单里 74 单被判定 `not_found`（当时结论是「出库单没生成」）。
    **那是错的**——那个 URL 的 `deliver_no` 参数**不生效**，返回的是默认第 1 页。
    `pb=2&page_size=300` 一次扫全量，74/74 全在页面上，状态=待打包。

真因：`deliver_no` 只在**表单 POST**（`searchOrder()`）里生效，GET 拼接无效。
       而且 pb=2 有 1079 条、每页默认几十条 ⇒ **不翻页就永远只看到第一屏**。

本脚本做法（不猜、不依赖搜索参数）：按 pb 逐页抓，抓出一张
    {参考单号: {"id","pb","status","wb","wh","sku"}}
的索引落盘，后续核对直接查这张表，不再逐单请求（快且不会漏）。

坑（照抄会错）：
    · 参考单号在 `class="deliver_no_<出库单id>"` 的单元里；
    · 运单号在 `td.waybill_<id>` 里，**位置在参考单号之前**；
      必须按 `<tr>` 切行再取，不能从单号往后截。

用法：
    python scripts/power/pw_index_deliver.py                    # pb=1 + pb=2
    python scripts/power/pw_index_deliver.py --pb 2             # 只建一页
    python scripts/power/pw_index_deliver.py --out my.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "pw_orders", "lib"))
sys.path.insert(0, _HERE)

#: 运单号形态
WB_PAT = (r"(1Z[0-9A-Z]{10,}|GFUS[0-9A-Z]{6,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
          r"|YWE[0-9A-Z]{6,}|\d{12,22})")

#: 每页条数（帕沃尔上限 300，实测 1079 条 pb=2 能 4 页翻完）
PAGE_SIZE = 300


def _get(pw, path: str) -> str:
    t = pw.get(path)
    return t.decode("utf-8", "replace") if isinstance(t, bytes) else t


def _rows(html: str) -> list:
    """按 <tr> 切行，取每行的 (出库单id, 参考单号, 状态, 运单号)。"""
    out = []
    for seg in re.findall(r"<tr[^>]*>.*?</tr>", html or "", re.S):
        m = re.search(r'class="deliver_no_(\d+)"[^>]*>([^<]*)<', seg)
        if not m:
            continue
        oid, po = m.group(1), m.group(2).strip()
        if not po:
            continue
        wbm = re.search(r'class="waybill_%s"[^>]*>(.*?)</td>' % oid, seg, re.S)
        wtxt = re.sub(r"<[^>]+>", " ", wbm.group(1)) if wbm else ""
        wbm2 = re.search(WB_PAT, wtxt)
        # 状态列：运单号单元格**之前**最近的一个 td 里找关键字。
        # ★ 2026-11-06 修：不能只看 `waybill_` 之前的片段 ——
        #   pb=2 的行里运单号单元格就是**第一个** td（`waybill_` 之前几乎没有内容），
        #   于是状态恒为空。正解：整行里找（运单号本身不含这些中文词，不会误命中）。
        sm = re.findall(r"(待确认|待打包|待出库|已出库|已发货|已取消|异常)", seg)
        out.append({"id": oid, "po": po,
                    "status": sm[-1] if sm else "",
                    "wb": wbm2.group(1) if wbm2 else ""})
    return out


def _wh_sku(html: str, oid: str) -> tuple:
    """从整行里取发货仓 / 平台SKU（best-effort，取不到就空）。

    ★ 2026-11-06：pb=2 的行**没有**「发货仓库/平台SKU」这些 span（那些只在
      hwc_shop 的店铺订单页有），所以这里恒空 —— 不用它做判据，只作参考。
    """
    m = re.search(r'<tr[^>]*style="[^"]*"[^>]*>.*?deliver_no_%s.*?</tr>' % oid, html, re.S) \
        or re.search(r'<tr[^>]*>.*?deliver_no_%s.*?</tr>' % oid, html, re.S)
    seg = m.group(0) if m else ""
    wh = re.search(r"发货仓库</span>\s*:([^<]*)<", seg)
    sku = re.search(r"平台SKU</span>\s*:([^<]*)<", seg)
    return ((wh.group(1).strip() if wh else ""), (sku.group(1).strip() if sku else ""))


def build(pw, pbs=("1", "2"), page_size: int = PAGE_SIZE, log=print) -> dict:
    """逐 pb 翻页抓全量，返回 {参考单号: {...}}。"""
    idx: dict = {}
    for pb in pbs:
        page, seen, total = 1, 0, None
        while page <= 40:
            html = _get(pw, "deliver.php?act=list&pb=%s&page=%d&page_size=%d"
                        % (pb, page, page_size))
            rows = _rows(html)
            if not rows:
                break
            for r in rows:
                wh, sku = _wh_sku(html, r["id"])
                idx[r["po"]] = dict(r, pb=pb, wh=wh, sku=sku)
            seen += len(rows)
            if total is None:
                m = re.search(r'record_count[^0-9]{0,12}(\d+)', html)
                total = int(m.group(1)) if m else None
            log("   pb=%s page=%d rows=%d 累计=%d/%s"
                % (pb, page, len(rows), seen, total if total is not None else "?"))
            if total is not None and seen >= total:
                break
            page += 1
            time.sleep(0.3)
    return idx


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="帕沃尔 deliver.php 全量索引（只读）")
    ap.add_argument("--pb", nargs="*", default=["1", "2"], help="要抓的页签（默认 1 2）")
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE)
    ap.add_argument("--out", default="", help="索引 json 路径")
    a = ap.parse_args()

    import pw_session as S  # noqa: PLC0415

    pw = S.PW()
    pw.login()
    idx = build(pw, tuple(a.pb), a.page_size)

    out = a.out or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"),
                                "pw_deliver_index.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print("\n索引 %d 单 → %s" % (len(idx), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
