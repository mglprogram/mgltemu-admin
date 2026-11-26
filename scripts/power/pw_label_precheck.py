# -*- coding: utf-8 -*-
"""买面单预检：**先查这批单是否已经买过面单**，避免 dry-run 报"可下单"、真提交才报"重复"。

★ 2026-11-26 真机（KLS 5 单）：
    dry-run 全部 ✅ 可下单（GOFO/SwiftX/UPS 都有报价）
    真提交 → 4 单「发货订单重复」+ 1 单「已买过面单（重复提交）」
  ⇒ 说明 **dry-run 只查渠道/时段，不查「是否已买」**，判据缺失。

本脚本用 `confirmShippingList`（待确认包裹列表）反查：
    某单若已有包裹号，说明面单已买过 → 跳过。

用法：
    python pw_label_precheck.py --store KINGLAMANSPORT --file pos.txt
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_confirm_api as TC  # noqa: E402


def order_packages(store: str, pos: list, log=print) -> dict:
    """用 `orderDetail` 查每单的包裹号 → `{po: packageSn}`（非空=已买过）。

    ★ 2026-11-26：`confirmShippingList` 只列**待确认**的包裹，确认发货后就查不到了；
      `orderDetail.packageSnList` 才是准的。
    """
    import json as _json  # noqa: PLC0415
    import asyncio as _aio  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    import temu_buylabel_api as _A  # noqa: PLC0415

    out = {}
    ws_url, _u = _A._page_ws(store)
    hdr = _A.load_headers(store) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k.lower() in
            ("mallid", "web-version", "content-type")}
    keep["mallid"] = _A.mallid_of(store, ws_url)
    for i, po in enumerate(pos, 1):
        try:
            body = {"parentOrderSn": po}
            js = "(%s)(%s, %s, %s)" % (
                _A.JS_CALL, _json.dumps("/kirogi/bg/mms/orderDetail"),
                _json.dumps(body, ensure_ascii=False),
                _json.dumps(keep, ensure_ascii=False))
            raw = _aio.run(_A._ev(ws_url, js)) or "{}"
            wrap = _json.loads(raw)              # {status, body}
            d = _json.loads(wrap.get("body") or "{}")
            txt = _json.dumps(d.get("result") or {}, ensure_ascii=False)
            pks = sorted(set(_re.findall(r"PK-\d+", txt)))
            if pks:
                out[po] = pks[0]
        except Exception:  # noqa: BLE001
            pass
        if i % 20 == 0:
            log("   [深查] %d/%d（已判已买 %d）" % (i, len(pos), len(out)))
    return out


def bought_set(store: str, page_size: int = 100, pos: list = None) -> dict:
    """返回 `{parentOrderSn: packageSn}` —— 已有包裹的单（=面单已买）。

    实现：按 `pos` 分批查 `confirmShippingList`（该接口支持 `parentOrderSnList` 过滤，
    一次最多 100 单）。`pos` 为空时只查第一页（全量 145 单会漏，务必传 pos）。
    """
    out = {}
    todo = [str(x).strip() for x in (pos or []) if str(x).strip()]
    if not todo:
        d = TC.list_pending(store, page_size=page_size)
        for it in (d.get("items") or []):
            for p in TC._po_list(it):
                po = p.get("parentOrderSn")
                if po:
                    out[po] = it.get("packageSn")
        return out

    for i in range(0, len(todo), page_size):
        chunk = todo[i:i + page_size]
        try:
            d = TC.list_pending(store, pos=chunk, page_size=page_size)
        except Exception:  # noqa: BLE001
            continue
        for it in (d.get("items") or []):
            for p in TC._po_list(it):
                po = p.get("parentOrderSn")
                if po:
                    out[po] = it.get("packageSn")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="KINGLAMANSPORT")
    ap.add_argument("--file", required=True)
    a = ap.parse_args()

    pos = [x.strip() for x in open(a.file, encoding="utf-8").read().splitlines()
           if x.strip().startswith("PO-") or x.strip().startswith("GSU")]
    seen, uniq = set(), []
    for p in pos:
        if p not in seen:
            seen.add(p)
            uniq.append(p)

    print("清单 %d 条（去重 %d）" % (len(pos), len(uniq)))
    bought = bought_set(a.store, pos=uniq)
    print("待确认列表里已有包裹（=已买面单）%d 单" % len(bought))

    # ★ 再对「待确认里没有」的单走 orderDetail 深查（确认过的包裹不在待确认列表里）
    rest = [p for p in uniq if p not in bought]
    if rest and os.environ.get("TEMU_LABEL_PRECHECK_DEEP", "1") not in ("0", "false", "False"):
        print("对剩下 %d 单做 orderDetail 深查（确认过的包裹也能查出来）…" % len(rest))
        deep = order_packages(a.store, rest)
        if deep:
            print("  深查又发现 %d 单已买过" % len(deep))
            bought.update(deep)
    print("合计已买过 %d 单\n" % len(bought))

    yes = [p for p in uniq if p in bought]
    no = [p for p in uniq if p not in bought]
    print("✅ 已买过（跳过）%d 单：" % len(yes))
    for p in yes[:15]:
        print("   %-26s pkg=%s" % (p, bought.get(p)))
    if len(yes) > 15:
        print("   …（共 %d 单）" % len(yes))
    print("\n🆕 可下单 %d 单：" % len(no))
    for p in no[:20]:
        print("   %s" % p)
    if len(no) > 20:
        print("   …（共 %d 单）" % len(no))

    out = os.path.join(_HERE, "out", "kls_todo.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(no) + ("\n" if no else ""))
    print("\n可下单清单 → %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
