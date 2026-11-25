# -*- coding: utf-8 -*-
"""换 Fnsku 再下单：**只改产品行里的 `ck_sku[]`**（用户口径：把原来的 sku 覆盖换成有库存的）。

★ 与之前失败的做法的区别：
   上次整表单回传 → 地址被写坏（"0 0"）
   这次**地址字段原样读出来再原样传**（现在地址已恢复：Georgia(GA)/17/SPRINGFIELD），
   只把 `ck_sku[]` 换成目标 Seller sku。

★ 目标映射（2026-11-25 库存页实测，关键词 YOGA-4SetA-L）：
   H320187483     BK-YOGA-4SetA-L   kansas仓  1     ← 当前（不够）
   D14450HD6AL    BS2-YOGA-4SetA-L  加州3仓   172   ← 换这个
"""
import html as _html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
NEW_SELLER = sys.argv[2] if len(sys.argv) > 2 else "BS2-YOGA-4SetA-L"
APPLY = "--apply" in sys.argv
STATUSES = ("下单失败", "已下单", "未下单", "已取消")


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def order_id(pw, po: str) -> str:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    d = json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    c = d.get("content") or ""
    m = re.search(r'<tr class="tr_html_(\d+)[^"]*">(?:(?!</tr>).)*?%s' % re.escape(po),
                  c, re.S)
    return m.group(1) if m else ""


def read_form(pw, oid: str) -> list:
    """解析编辑页 form[name=myform] 全部字段（地址走当前值，现在是好的）。"""
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % oid)
    t = r if isinstance(r, str) else str(r)
    m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', t, re.S | re.I)
    body = m.group(1) if m else t
    out = []
    for tag in re.findall(r"<input[^>]*>", body, re.I):
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        name = nm.group(1)
        typ = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1].lower()
        if typ in ("button", "submit", "reset", "file", "image"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        val = re.search(r'value="([^"]*)"', tag)
        v = _html.unescape(val.group(1)) if val else ""
        if v.startswith("'+") or v.startswith('"+'):
            v = ""
        out.append((name, v, typ))
    for name, inner in re.findall(
            r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
        s = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
        out.append((name, _html.unescape(s.group(1)) if s else "", "select"))
    return out


def submit(pw, fields: list) -> dict:
    pairs = [(n, v) for n, v, _t in fields]
    data = urllib.parse.urlencode(pairs, doseq=True).encode()
    req = urllib.request.Request(BASE + "hwc_shop.php", data=data, method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "hwc_shop.php?act=edit_order")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": txt[:200]}


def read_state(pw, po: str) -> dict:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    d = json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    c = d.get("content") or ""
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', c, re.S):
        if po not in tr:
            continue
        st = ""
        for s in STATUSES:
            if s in tr:
                st = s
        err = ""
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
            cc = clean(cell)
            if "条" in cc and "下单" in cc:
                err = cc
        addr = re.search(r"收件地址\s*[:：]\s*(\S+)", clean(tr))
        sku = re.search(r"平台SKU\s*[:：]\s*(\S+)", clean(tr))
        return {"id": oid, "status": st, "err": err[:130],
                "addr": addr.group(1) if addr else "", "sku": sku.group(1) if sku else ""}
    return {"id": "", "status": "查不到"}


def main():
    pw = SS.PW()
    pw.login()
    st0 = read_state(pw, PO)
    print("① 当前:", json.dumps(st0, ensure_ascii=False))
    if not st0["id"]:
        sys.exit("查不到订单")

    fields = read_form(pw, st0["id"])
    cur = [v for n, v, _t in fields if n == "ck_sku[]"]
    addr = {n: v for n, v, _t in fields
            if n in ("country", "city_name", "city", "district", "zip", "address")}
    print("② 表单 %d 字段 | ck_sku=%s" % (len(fields), cur))
    print("   地址: %s" % json.dumps(addr, ensure_ascii=False))

    if not APPLY:
        print("\n[dry-run] 将把 ck_sku[] %s → %s" % (cur, NEW_SELLER))
        return 0

    print("\n③ 改 ck_sku[] → %s 并提交" % NEW_SELLER)
    new_fields = [(n, (NEW_SELLER if n == "ck_sku[]" else v), t) for n, v, t in fields]
    r = submit(pw, new_fields)
    print("   edit_update → error=%s msg=%s" % (r.get("error"), str(r.get("msg"))[:70]))

    st1 = read_state(pw, PO)
    print("④ 回读:", json.dumps(st1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
