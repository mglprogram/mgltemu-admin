#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_pw_stock.py — 帕沃尔海外仓「库存」页全量抓取（只保留 Kansas 仓有效库存）。

用户需求（2026-11-02）
----------------------
帕沃尔库存链接 `https://www.powerwarehouse.net/stock.php?act=list`
**目前只有 Kansas 仓的库存是有效/可用的**，其它仓（加州3仓等）不动。
所以本脚本抓全量后**只保留 `仓库名称 = kansas仓`** 的行，其余丢弃。

为什么翻页而不是导出
--------------------
页面有「导出」按钮（POST `stock.php?act=daochu_excel`），但它走的是
「先建导出任务、再弹窗轮询下载文件」那套（`daochu_id` + iframe），
要额外轮询任务状态、还要处理 xlsx —— 对 2,833 行来说不值得。
实测 **`&page=N` 这个 query 参数是有效的**（第 2 页首行与第 1 页不同），
57 页 × 50 行，每页 0.3~0.5 秒，串行 30 秒级就能拉完，且与页面看到的完全一致。

⚠ `pageSize` 参数**无效**（传 200/500/1000/10000 都还是 50 行），别指望一次拉完。

产出
----
    temu-admin/public/data/pw-stock.json
    {
      "generatedAt": "...",
      "warehouse": "kansas仓",
      "total": N,            # Kansas 行数
      "scanned": M,          # 全量扫过的行数
      "pages": P,
      "items": [
        {"fnsku","sku","name","wh","transit","stock","transferTransit",
         "transferStock","pending","sales10","sales30","sales90",
         "ageDays","volume","fee","returnStock","returnPending",
         "warn","inboundAt","shelf"}
      ]
    }

用法
----
    python scripts/goods/fetch_pw_stock.py              # 全量抓 + 落盘
    python scripts/goods/fetch_pw_stock.py --dry        # 只打印统计
    python scripts/goods/fetch_pw_stock.py --all-wh     # 不按 Kansas 过滤（排障用）

退出码：0 成功；2 登录/抓取失败；3 一行没解析到（页面结构变了）；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "temu-admin" / "public" / "data" / "pw-stock.json"
RAW = ROOT / "out" / "pw-stock"

sys.path.insert(0, str(ROOT / "scripts" / "pw_orders"))
sys.path.insert(0, str(ROOT / "scripts" / "pw_orders" / "lib"))

BASE = "https://www.powerwarehouse.net/"
LIST_URL = BASE + "stock.php?act=list"

#: 目前**只有 Kansas 仓的库存是有效的**（用户 2026-11-02 明确说明）。
KEEP_WH = "kansas仓"

#: 表头顺序（页面 <th>），用来按列名取值而不是写死下标 —— 列顺序变了也能跑。
#: 页面表头：序号 / Fnsku / Seller sku / 产品名称 / 英文品名 / 产品分类 / 仓库名称 /
#:           货架 / 代发途中 / 代发库存 / 中转途中 / 中转库存 / 待处理库存 /
#:           10天销量 / 30天销量 / 90天销量 / 库龄(天) / 体积(ft³) / 仓储费($) /
#:           退货换标库存 / 退货换标处理中 / 库存预警 / 入仓时间 / 操作
COL_MAP = {
    "fnsku": "Fnsku",
    "sku": "Seller sku",
    "name": "产品名称",
    "nameEn": "英文品名",
    "category": "产品分类",
    "wh": "仓库名称",
    "shelf": "货架",
    "transit": "代发途中",
    "stock": "代发库存",
    "transferTransit": "中转途中",
    "transferStock": "中转库存",
    "pending": "待处理库存",
    "sales10": "10天销量",
    "sales30": "30天销量",
    "sales90": "90天销量",
    "ageDays": "库龄(天)",
    "volume": "体积(ft³)",
    "fee": "仓储费($)",
    "returnStock": "退货换标库存",
    "returnPending": "退货换标处理中",
    "warn": "库存预警",
    "inboundAt": "入仓时间",
}

NUM_FIELDS = {
    "transit", "stock", "transferTransit", "transferStock", "pending",
    "sales10", "sales30", "sales90", "ageDays", "volume", "fee",
    "returnStock", "returnPending",
}


def _text(html: str) -> str:
    """去标签、压空白、解 &nbsp;。"""
    s = re.sub(r"<[^>]+>", "", html)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _num(v: str):
    s = str(v or "").replace(",", "").replace("$", "").strip()
    if not s or s in ("-", "—"):
        return 0
    try:
        f = float(s)
        return int(f) if f.is_integer() else round(f, 4)
    except ValueError:
        return 0


def parse_headers(html: str) -> list[str]:
    """表头（**从去注释后的表格段**取，见 parse_rows 的说明）。"""
    i = html.find("<table")
    j = html.find("</table>", i)
    seg = re.sub(r"<!--.*?-->", "", html[i:j], flags=re.S) if i >= 0 else html
    return [_text(t) for t in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]


def _table_grid(html: str) -> list[list[str]]:
    """把表格解析成「每行一样宽」的二维数组，**正确处理 rowspan**。

    为什么必须这样（2026-11-02 实测，踩了两次）
    -------------------------------------------
    ① 一个 SKU 有多个仓时，页面用 `rowspan="2"` 把
       「序号 / Fnsku / Seller sku / 产品名称 / 英文品名」这 5 列合并成一个格子，
       第二个仓那一行（续行）**只写仓库名和数字** ⇒ 早先「td 数 < 10 就丢」的实现
       会静默少行（2833 行只拿到 2788，差的 45 行全是这种续行）。
    ② 表格里还有**被注释掉的列**（`<!-- <td ></td> -->`），
       不先去掉注释，`re.findall('<td...')` 会把注释里的空 td 也算进去，
       列位置整体错位（表头 24 列 vs 实际渲染 22 列，怎么对都对不上）。

    做法：先去注释 → 再按 rowspan 向下「铺」被合并的格子，得到等宽矩阵。
    实测每行稳定 18 列，与表头逐列对齐。
    """
    i = html.find("<table")
    j = html.find("</table>", i)
    if i < 0 or j < 0:
        return []
    seg = re.sub(r"<!--.*?-->", "", html[i:j], flags=re.S)

    grid: list[list[str]] = []
    pending: dict[int, list] = {}  # 列号 -> [剩余行数, 值]

    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S)
        if not tds:
            continue
        row: list[str] = []
        col, ti = 0, 0
        while ti < len(tds) or col in pending:
            if col in pending:
                row.append(pending[col][1])
                pending[col][0] -= 1
                if pending[col][0] <= 0:
                    del pending[col]
                col += 1
                continue
            attrs, inner = tds[ti]
            ti += 1
            row.append(_text(inner))
            m = re.search(r'rowspan=["\']?(\d+)', attrs)
            if m and int(m.group(1)) > 1:
                pending[col] = [int(m.group(1)) - 1, _text(inner)]
            col += 1
        grid.append(row)
    return grid


def parse_rows(html: str) -> list[dict]:
    """解析主表数据行（一行 = 一个 SKU × 一个仓库）。"""
    heads = parse_headers(html)
    if not heads:
        return []
    idx: dict[str, int] = {}
    for key, name in COL_MAP.items():
        if name in heads:
            idx[key] = heads.index(name)

    out: list[dict] = []
    for row in _table_grid(html):
        if len(row) < 6:
            continue
        # 只认第一格是纯数字（序号）的行 —— 那是真正的记录边界
        if not re.fullmatch(r"\d+", row[0] or ""):
            continue
        rec: dict = {}
        for key, k in idx.items():
            if k >= len(row):
                continue
            val = row[k]
            rec[key] = _num(val) if key in NUM_FIELDS else val
        if not rec.get("fnsku"):
            continue
        out.append(rec)
    return out


def total_pages(html: str) -> int:
    m = re.search(r'id="totalPages"[^>]*>\s*(\d+)', html)
    if m:
        return int(m.group(1))
    m = re.search(r"共\s*(\d+)\s*页", html)
    return int(m.group(1)) if m else 1


def fetch_page(pw, page: int, retries: int = 4, gap: float = 0.25) -> str:
    """抓一页。

    ⚠ 两个实测坑（2026-11-02）：
      1. **必须节流**：不加间隔连续请求，第 22~28 页会成片 `404`（IIS 侧限速），
         而单页重试时又能成功 —— 说明不是页面不存在，是打太快了。
      2. `504 / WinError 10060` 也会偶发（帕沃尔 IIS 长尾），同样靠退避重试。
    """
    url = LIST_URL + ("&page=%d" % page if page > 1 else "")
    last = None
    for i in range(retries):
        try:
            time.sleep(gap)  # 节流，别把 IIS 打急
            with pw.op.open(urllib.request.Request(url), timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(1.0 + 1.5 * i)
    raise RuntimeError("第 %d 页抓取失败：%s" % (page, str(last)[:150]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="帕沃尔库存（只保留 Kansas 仓）")
    ap.add_argument("--dry", action="store_true", help="只打印统计，不落盘")
    ap.add_argument("--all-wh", action="store_true", help="不过滤仓库（排障用）")
    ap.add_argument("--max-pages", type=int, default=200, help="最多抓多少页（保险）")
    args = ap.parse_args(argv)

    from lib.pw_session import PW, PWError  # noqa: PLC0415

    RAW.mkdir(parents=True, exist_ok=True)
    try:
        pw = PW()  # 账号走 pw_account.local.json / 环境变量
        pw.login()
        print("[pw-stock] 登录 OK")
    except PWError as e:
        print("[pw-stock] 登录失败：%s" % str(e)[:200])
        return 2
    except Exception as e:  # noqa: BLE001
        print("[pw-stock] 登录异常：%s" % str(e)[:200])
        return 2

    try:
        first = fetch_page(pw, 1)
    except Exception as e:  # noqa: BLE001
        print("[pw-stock] 首页抓取失败：%s" % str(e)[:200])
        return 2
    (RAW / "page1.html").write_text(first, encoding="utf-8")

    pages = min(total_pages(first), args.max_pages)
    rows = parse_rows(first)
    print("[pw-stock] 第 1 页：%d 行（共 %d 页）" % (len(rows), pages))

    failed: list[int] = []
    for p in range(2, pages + 1):
        try:
            html = fetch_page(pw, p)
        except Exception as e:  # noqa: BLE001
            # 单页失败不整批报废：记下来，末尾统一补抓
            print("[pw-stock] !! 第 %d 页失败：%s" % (p, str(e)[:120]))
            failed.append(p)
            continue
        got = parse_rows(html)
        rows += got
        if p % 10 == 0 or p == pages:
            print("[pw-stock] 进度 %d/%d 页，累计 %d 行" % (p, pages, len(rows)))

    # 补抓失败的页：拉长间隔再试一轮 —— 成片 404 基本都是打太快，缓一下就好
    if failed:
        print("[pw-stock] 补抓 %d 个失败页（间隔 2s）：%s" % (len(failed), failed))
        still: list[int] = []
        for p in failed:
            try:
                time.sleep(2.0)
                got = parse_rows(fetch_page(pw, p, retries=5, gap=1.0))
                rows += got
                print("[pw-stock]   补抓第 %d 页成功：%d 行" % (p, len(got)))
            except Exception as e:  # noqa: BLE001
                print("[pw-stock]   补抓第 %d 页仍失败：%s" % (p, str(e)[:100]))
                still.append(p)
        if still:
            # 丢页 = 少货，必须让人看见（不静默）
            print("[pw-stock] ⚠ 仍有 %d 页没抓到：%s —— 本次结果**不完整**"
                  % (len(still), still))

    if not rows:
        print("[pw-stock] 一行都没解析到 —— 页面结构可能变了")
        return 3

    # 去重（同一 Fnsku + 仓 只留一条）
    seen, uniq = set(), []
    for r in rows:
        k = (r.get("fnsku"), r.get("wh"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    scanned = len(uniq)
    if not args.all_wh:
        uniq = [r for r in uniq if r.get("wh") == KEEP_WH]

    print("[pw-stock] 扫过 %d 行，%s %d 行"
          % (scanned, "全部仓" if args.all_wh else ("Kansas（%s）" % KEEP_WH), len(uniq)))

    if not uniq:
        print("[pw-stock] Kansas 仓一行都没有 —— 先确认仓库名是否还叫 %r" % KEEP_WH)
        return 3

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "warehouse": "" if args.all_wh else KEEP_WH,
        "total": len(uniq),
        "scanned": scanned,
        "pages": pages,
        "items": uniq,
        "rule": {
            "note": "帕沃尔库存页只有 **Kansas 仓**的库存是有效的（用户 2026-11-02 说明），"
                    "其它仓库的行已丢弃。代发库存 = 可发的现货；代发途中 = 在途。",
            "source": LIST_URL,
        },
    }

    if args.dry:
        print(json.dumps({k: v for k, v in payload.items() if k != "items"},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[pw-stock] 落盘失败：%s" % str(e)[:200])
        return 4

    stock_sum = sum(r.get("stock") or 0 for r in uniq)
    print("[pw-stock] 已写 %s（%d 行，%.1f KB，代发库存合计 %s 件）"
          % (OUT.relative_to(ROOT), len(uniq), OUT.stat().st_size / 1024, int(stock_sum)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
