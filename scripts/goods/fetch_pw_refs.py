#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_pw_refs.py — 帕沃尔「代发库存」明细里的**参考单号**（点数字弹窗那一页）。

用户需求（2026-11-04）
---------------------
在库存跟踪页的「帕沃尔 加州3 / 帕沃尔 Kansas」两列上，**点大于 0 的数字**，
弹出的是帕沃尔后台点同一个数字看到的那个明细表，用户要的是里面的**参考单号**
（如 144503047），拿它去客户那边查这票货。

那一页是怎么来的（实测）
------------------------
库存页 `stock.php?act=list` 里每个非零数字都带
    onclick="stock_tuzhong('<mdck>','<Fnsku>',2)"    // 2 = 代发库存
然后 layer 里打开 iframe：
    stock.php?act=huojia_log&types_pb=1&ck_sku=<Fnsku>&mdck=<mdck>
        &order_types=718&is_kucun=1&view_types=1
返回一张表：序号 / 客户名称 / 入仓单号 / **参考单号** / 装箱单号 / Fnsku /
            数量 / 入库数量 / 出库数量 / 剩余库存 / 货架 …

⚠ 三个坑
  · `ck_sku` 用的是 **Fnsku**（D14450xxxx），不是卖家 SKU `BS2-…`；传卖家 SKU 返回空表。
  · `mdck` 是**仓库** ID（加州3 = 22，Kansas = 10 附近的另一档），要按 SKU 所在的仓取对，
    传错仓同样是空表。
  · 结果**不跨仓合并**：同一个 Fnsku 在两个仓是两个不同的 mdck，必须分开查。

产出
----
    temu-admin/public/data/pw-refs.json
    {
      "generatedAt": "...", "skus": N, "rows": M,
      "map": {
        "<fnsku>": [                     # 前端按 Fnsku（= pw-stock 的 fnsku 字段）索引
          {"ref": "144503054", "box": "144503054-1_1", "qty": 18, "left": 18,
           "wh": "加州3仓", "in": "2026-11-04 14:50:54"}
        ]
      },
      "byWh": {"加州3仓": "22", "kansas仓": "..."}   # 用到的仓库 ID，排障用
    }

用法
----
    python scripts/goods/fetch_pw_refs.py            # 全量（约 1,400 次请求 / 12 分钟）
    python scripts/goods/fetch_pw_refs.py --dry      # 只打印统计
    python scripts/goods/fetch_pw_refs.py --limit 20 # 先跑 20 个 SKU 看看

退出码：0 成功；2 登录失败；3 一条都没抓到；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "temu-admin" / "public" / "data" / "pw-refs.json"
PW_JSON = ROOT / "temu-admin" / "public" / "data" / "pw-stock.json"

sys.path.insert(0, str(ROOT / "scripts" / "pw_orders"))
sys.path.insert(0, str(ROOT / "scripts" / "pw_orders" / "lib"))

#: 这些仓才查（与 fetch_pw_stock.KEEP_WHS 保持一致）
KEEP_WHS = ("加州3仓", "kansas仓")

#: 从表头找列号，页面列顺序变了也能跑
COL_REF = "参考单号"
COL_BOX = "装箱单号"
COL_QTY = "数量"
COL_LEFT = "剩余库存"
COL_WH = "仓库名称"
COL_IN = "入库时间"


def _text(html: str) -> str:
    s = re.sub(r"<[^>]+>", "", html)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _num(v) -> int:
    s = str(v or "").replace(",", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return 0


def login():
    from lib.pw_session import PW  # noqa: PLC0415
    pw = PW()
    pw.login()
    return pw


def _table_grid(html: str) -> list[list[str]]:
    """库存页的表格（含 rowspan 展开），与 fetch_pw_stock._table_grid 同源。"""
    from fetch_pw_stock import _table_grid as _f  # noqa: PLC0415
    return _f(html)


def warehouse_ids(pw) -> tuple[dict[str, str], dict[str, str]]:
    """扫库存页，摸出「Fnsku → mdck」与「仓库名 → mdck」。

    实测：**同一个仓库的所有 SKU 用同一个 mdck**（加州3 = 22，Kansas = 10），
    所以拿到仓库映射就能对任意 SKU 拼出 URL，不必每个 SKU 再扫一遍。

    行结构（_table_grid 展开 rowspan 后每行 18 列）：
        0 序号 / 1 Fnsku / 2 Seller sku / 3 产品名称 / 4 英文品名 / 5 仓库名称 …
    注意序号可能是空的（续行），所以**不能**靠「首格是数字」当记录边界，
    这里按「Fnsku 形如 D14450xxxx」来认行。
    """
    fns: dict[str, str] = {}
    whs: dict[str, str] = {}
    page = 1
    total = 0
    while page <= 80:
        html = pw.get("stock.php?act=list" + ("&page=%d" % page if page > 1 else ""))
        pairs = re.findall(r"stock_tuzhong\('(\d+)','([^']*)',2\)", html)
        if not pairs:
            break
        for mdck, fn in pairs:
            fns[fn] = mdck
        seg = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        heads = [_text(t) for t in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]
        i_wh = heads.index("仓库名称") if "仓库名称" in heads else -1
        if i_wh >= 0:
            for row in _table_grid(seg):
                if len(row) <= i_wh:
                    continue
                fn = row[1] if len(row) > 1 else ""
                if not re.match(r"^(D14450[A-Z0-9]+)$", fn):
                    continue
                wh = row[i_wh]
                if wh in KEEP_WHS and fns.get(fn):
                    whs.setdefault(wh, fns[fn])
        if not total:
            m = re.search(r'id="totalPages"[^>]*>\s*(\d+)', html)
            total = int(m.group(1)) if m else 1
        if page >= total:
            break
        page += 1
        time.sleep(0.2)
    return fns, whs


def fetch_one(pw, fn: str, mdck: str, retries: int = 3) -> list[dict]:
    url = ("stock.php?act=huojia_log&types_pb=1&ck_sku=%s&mdck=%s"
           "&order_types=718&is_kucun=1&view_types=1" % (fn, mdck))
    last = None
    html = ""
    for i in range(retries):
        try:
            html = pw.get(url)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.8 + 0.8 * i)
    if not html:
        raise RuntimeError(str(last)[:100])

    seg = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    i = seg.find("<table")
    j = seg.find("</table>", i)
    if i < 0 or j < 0:
        return []
    seg = seg[i:j]
    heads = [_text(t) for t in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]
    idx = {}
    for key in (COL_REF, COL_BOX, COL_QTY, COL_LEFT, COL_WH, COL_IN):
        if key in heads:
            idx[key] = heads.index(key)
    if COL_REF not in idx:
        return []

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) <= idx[COL_REF]:
            continue
        ref = cells[idx[COL_REF]]
        if not re.fullmatch(r"\d{6,}", ref or ""):
            continue
        out.append({
            "ref": ref,
            "box": cells[idx[COL_BOX]] if COL_BOX in idx and len(cells) > idx[COL_BOX] else "",
            "qty": _num(cells[idx[COL_QTY]]) if COL_QTY in idx and len(cells) > idx[COL_QTY] else 0,
            "left": _num(cells[idx[COL_LEFT]]) if COL_LEFT in idx and len(cells) > idx[COL_LEFT] else 0,
            "wh": cells[idx[COL_WH]] if COL_WH in idx and len(cells) > idx[COL_WH] else "",
            "in": cells[idx[COL_IN]] if COL_IN in idx and len(cells) > idx[COL_IN] else "",
        })
    return out


def merge_batches(rows: list[dict], limit: int = 40) -> list[dict]:
    """同一个参考单号有多箱 → 合成一条，并把箱号列出来（前端弹窗只显示单号+件数）。"""
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["ref"], {"ref": r["ref"], "qty": 0, "left": 0,
                                      "wh": r["wh"], "in": r["in"], "boxes": []})
        a["qty"] += r["qty"]
        a["left"] += r["left"]
        if r["box"] and len(a["boxes"]) < limit:
            a["boxes"].append(r["box"])
        if not a["in"] and r["in"]:
            a["in"] = r["in"]
    out = list(agg.values())
    out.sort(key=lambda x: x["ref"], reverse=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="帕沃尔代发库存明细 → 参考单号")
    ap.add_argument("--dry", action="store_true", help="只打印统计，不落盘")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个 SKU（排障用）")
    args = ap.parse_args(argv)

    if not PW_JSON.exists():
        print("[pw-refs] 缺 pw-stock.json，先跑 fetch_pw_stock.py")
        return 3
    stock = json.loads(PW_JSON.read_text(encoding="utf-8"))
    targets = [
        i for i in (stock.get("items") or [])
        if i.get("wh") in KEEP_WHS and (i.get("stock") or 0) > 0 and i.get("fnsku")
    ]
    if args.limit:
        targets = targets[:args.limit]
    print("[pw-refs] 要查 %d 个「SKU×仓」（来自 pw-stock.json %s）"
          % (len(targets), stock.get("generatedAt") or "—"))

    try:
        pw = login()
    except Exception as e:  # noqa: BLE001
        print("[pw-refs] 登录失败：%s" % str(e)[:160])
        return 2
    print("[pw-refs] 登录 OK，先扫仓库 ID…")
    fn2mdck, wh2mdck = warehouse_ids(pw)
    print("[pw-refs] 仓库 ID：%s（扫到 %d 个 Fnsku 的映射）"
          % (wh2mdck or "未识别", len(fn2mdck)))

    result: dict[str, list[dict]] = {}
    ok = fail = 0
    t0 = time.time()
    for n, it in enumerate(targets, 1):
        fn = str(it.get("fnsku") or "")
        wh = str(it.get("wh") or "")
        mdck = wh2mdck.get(wh) or fn2mdck.get(fn, "")
        if not mdck:
            fail += 1
            continue
        try:
            rows = fetch_one(pw, fn, mdck)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("[pw-refs]   ! %s 查询失败：%s" % (fn, str(e)[:70]))
            fail += 1
            continue
        if rows:
            result.setdefault(fn, []).extend(
                [{**r, "wh": r.get("wh") or wh} for r in rows]
            )
        if n % 50 == 0:
            print("[pw-refs] 进度 %d/%d（成功 %d / 失败 %d，%.0fs）"
                  % (n, len(targets), ok, fail, time.time() - t0))
        time.sleep(0.12)  # 节流，别把 IIS 打急（≥28 页那次成片 404 的教训）

    for fn in result:
        result[fn] = merge_batches(result[fn])

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skus": len(result),
        "rows": sum(len(v) for v in result.values()),
        "map": result,
        "byWh": wh2mdck,
        "rule": {
            "note": "帕沃尔库存页点「代发库存」数字弹出的那张明细表，按参考单号（14450xxxx）"
                    "合并箱号后的结果。只有代发库存 > 0 的 SKU 才有。",
            "source": "https://www.powerwarehouse.net/stock.php?act=huojia_log",
        },
    }

    if args.dry:
        print(json.dumps({k: v for k, v in payload.items() if k != "map"},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[pw-refs] 落盘失败：%s" % str(e)[:200])
        return 4

    print("[pw-refs] 已写 %s（%d 个 SKU / %d 条参考单号，%.0fs，成功 %d 失败 %d）"
          % (OUT.relative_to(ROOT), len(result), payload["rows"], time.time() - t0, ok, fail))
    if not result:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
