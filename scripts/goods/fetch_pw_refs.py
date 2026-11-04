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


def fetch_one(pw, fn: str, mdck: str, pb: int = 1, retries: int = 3) -> list[dict]:
    """抓一个 SKU 在某仓的明细表。

    ⚠ 2026-11-04 用户报错纠正：	ypes_pb=1 是**代发库存**（已进仓，表里有
    入库数量/出库数量/剩余库存/入库时间），	ypes_pb=2 是**代发途中**（还没进仓，
    表里是 添加时间/订单时长），两者是**两批完全不同的货**，单号也不一样。
    原来只抓了 pb=1 就当成在途，所以 1026-3BHL-S 在途 29 件却显示成
    代发库存那单 144502864 —— 正确的在途单是 144503067。
    """
    url = ("stock.php?act=huojia_log&types_pb=%d&ck_sku=%s&mdck=%s"
           "&order_types=718&is_kucun=1&view_types=%s"
           % (pb, fn, mdck, "1" if pb == 1 else ""))
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
    # pb=2（在途）没有「入库时间」列，用「添加时间」代
    i_add = heads.index("添加时间") if "添加时间" in heads else -1
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
            # 帕沃尔 Kansas 仓部分行的「参考单号」其实是 20 位快递/监管号（9631091350…），
            # 不是我们的业务单号（14450xxxx）。标出来，页面按"非业务单号"弱化显示。
            "isRef": bool(re.fullmatch(r"\d{8,12}", ref)),
            "box": cells[idx[COL_BOX]] if COL_BOX in idx and len(cells) > idx[COL_BOX] else "",
            "qty": _num(cells[idx[COL_QTY]]) if COL_QTY in idx and len(cells) > idx[COL_QTY] else 0,
            "left": _num(cells[idx[COL_LEFT]]) if COL_LEFT in idx and len(cells) > idx[COL_LEFT] else 0,
            "wh": cells[idx[COL_WH]] if COL_WH in idx and len(cells) > idx[COL_WH] else "",
            "in": (cells[idx[COL_IN]] if COL_IN in idx and len(cells) > idx[COL_IN]
                   else (cells[i_add] if 0 <= i_add < len(cells) else "")),
        })
    return out


def merge_batches(rows: list[dict], limit: int = 40) -> list[dict]:
    """同一个参考单号有多箱 → 合成一条，并把箱号列出来（前端弹窗只显示单号+件数）。"""
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["ref"], {"ref": r["ref"], "isRef": r.get("isRef", True),
                                      "qty": 0, "left": 0,
                                      "wh": r["wh"], "in": r["in"], "boxes": []})
        a["qty"] += r["qty"]
        a["left"] += r["left"]
        if r["box"] and len(a["boxes"]) < limit:
            a["boxes"].append(r["box"])
        if not a["in"] and r["in"]:
            a["in"] = r["in"]
    out = list(agg.values())
    # 业务单号（14450xxxx）排前面，超长快递号排后面
    out.sort(key=lambda x: (not x.get("isRef"), x["ref"]), reverse=False)
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
    # 只要有货**或在途**就查：在途那些行的参考单号正是「帕沃尔在途」列悬浮要显示的内容
    targets = [
        i for i in (stock.get("items") or [])
        if i.get("wh") in KEEP_WHS
        and ((i.get("stock") or 0) > 0 or (i.get("transit") or 0) > 0)
        and i.get("fnsku")
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

    # 两份结果分开存：stock = 代发库存明细（pb=1），transit = 代发途中明细（pb=2）
    result: dict[str, list[dict]] = {}
    transit: dict[str, list[dict]] = {}
    ok = fail = 0
    t0 = time.time()
    for n, it in enumerate(targets, 1):
        fn = str(it.get("fnsku") or "")
        wh = str(it.get("wh") or "")
        mdck = wh2mdck.get(wh) or fn2mdck.get(fn, "")
        if not mdck:
            fail += 1
            continue
        # 有货就抓 pb=1，有在途就抓 pb=2 —— 两批货不同，单号也不同，必须分别取
        want: list[tuple[int, str]] = []
        if (it.get("stock") or 0) > 0:
            want.append((1, "stock"))
        if (it.get("transit") or 0) > 0:
            want.append((2, "transit"))
        for pb, bucket in want:
            try:
                rows = fetch_one(pw, fn, mdck, pb=pb)
                ok += 1
            except Exception as e:  # noqa: BLE001
                print("[pw-refs]   ! %s(pb=%d) 查询失败：%s" % (fn, pb, str(e)[:60]))
                fail += 1
                continue
            if not rows:
                continue
            dst = result if bucket == "stock" else transit
            dst.setdefault(fn, []).extend(
                [{**r, "wh": r.get("wh") or wh} for r in rows]
            )
            time.sleep(0.1)
        if n % 50 == 0:
            print("[pw-refs] 进度 %d/%d（成功 %d / 失败 %d，%.0fs）"
                  % (n, len(targets), ok, fail, time.time() - t0))
        time.sleep(0.1)  # 节流，别把 IIS 打急（>=28 页那次成片 404 的教训）

    for fn in result:
        result[fn] = merge_batches(result[fn])
    for fn in transit:
        transit[fn] = merge_batches(transit[fn])

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skus": len(result),
        "rows": sum(len(v) for v in result.values()),
        "transitSkus": len(transit),
        "transitRows": sum(len(v) for v in transit.values()),
        "map": result,
        "transitMap": transit,
        "byWh": wh2mdck,
        "rule": {
            "note": "map = 点「代发库存」数字弹出的明细（已进仓，有入库/出库/剩余）；"
                    "transitMap = 点「代发途中」数字弹出的明细（还没进仓，只有添加时间）。"
                    "两者是两批不同的货，单号不同，不要混用。",
            "source": "https://www.powerwarehouse.net/stock.php?act=huojia_log（types_pb=1 库存 / 2 途中）",
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
