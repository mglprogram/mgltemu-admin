# -*- coding: utf-8 -*-
"""失败单「换 Fnsku 重下」—— 用户确认的目标已明确。

用户 2026-11-25 确认：
    订单 PO-211-07055826094713286
    当前 Fnsku: H320187483   (BK-YOGA-4SetA-L)  kansas仓   库存 1   → 不够，下单失败
    换到:       D14450HD6AL  (BS2-YOGA-4SetA-L) 加州3仓   库存 172 ← 换这个

三步（全 HTTP）：
    ① GET  hwc_shop.php?act=edit_order&id=<oid>   解析整个表单
    ② POST hwc_shop.php?act=edit_update   multipart，只改 `sku[]`（Fnsku，hidden）
    ③ POST hwc_shop.php?act=xiadan_df&id=<oid>&is_ajax=1   再次下单代发

★ 关键：`sku[]` 是真 Fnsku（hidden input），`ck_sku[]` 是平台SKU（保持不动）。
★ 表单要**整份回传**，只传 sku[] 会把仓库/地址/数量清空。
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
EDIT_URL = "hwc_shop.php?act=edit_order&id=%s"
UPDATE_URL = "hwc_shop.php?act=edit_update"
STATUSES = ("下单失败", "下单成功", "已下单", "未下单")


# ---------------------------------------------------------------- 表单解析
def parse_form(page_html: str) -> list:
    """解析 `form[name=myform]` 的全部可提交字段 → `[(name, value), ...]`。"""
    m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', page_html, re.S | re.I)
    body = m.group(1) if m else page_html
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
        # hidden 的模板串（如 '+html[i].sku+'）视为空
        if v.startswith("'+") or v.startswith("\"+"):
            v = ""
        out.append((name, v))

    for name, inner in re.findall(
            r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
        s = (re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
             or re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', inner, re.I)
             or re.search(r'<option[^>]*value="([^"]*)"', inner, re.I))
        out.append((name, _html.unescape(s.group(1)) if s else ""))

    for name, inner in re.findall(
            r'<textarea[^>]*name="([^"]*)"[^>]*>(.*?)</textarea>', body, re.S | re.I):
        out.append((name, _html.unescape(inner)))
    return out


def get_form(pw, oid: str) -> list:
    r = pw.get(EDIT_URL % oid)
    txt = r if isinstance(r, str) else str(r)
    return parse_form(txt)


def post_update(pw, fields: list) -> dict:
    data = urllib.parse.urlencode(fields, doseq=True).encode()
    req = urllib.request.Request(BASE + UPDATE_URL, data=data, method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "hwc_shop.php?act=edit_order")
    with pw.op.open(req, timeout=pw.timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": body[:200]}


def read_status(pw, po: str) -> dict:
    q = H.query_orders(pw, po)
    html = q.get("html") or ""
    for _oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if po in tr:
            st = ""
            for s in STATUSES:
                if s in tr:
                    st = s
            err = re.search(r"下单成功\d+条.*?</td>", tr.replace("\n", " "), re.S)
            return {"status": st, "err": (err.group(0) if err else "")[:150],
                    "id": (q["rows"] or [{}])[0].get("id")}
    return {"status": "?", "err": "", "id": (q["rows"] or [{}])[0].get("id")}


# ---------------------------------------------------------------- 主流程
def fix_and_reship(pw, po: str, new_fnsku: str, apply: bool = False, log=print) -> dict:
    st0 = read_status(pw, po)
    oid = st0["id"]
    log("   单号 %s id=%s 状态=%s" % (po, oid, st0["status"]))
    if not oid:
        return {"ok": False, "why": "查不到订单"}

    fields = get_form(pw, oid)
    old_sku = [v for n, v in fields if n == "sku[]"]
    platform_sku = [v for n, v in fields if n == "ck_sku[]"]
    log("   当前 Fnsku=%s | 平台SKU=%s" % (old_sku, platform_sku))
    if not apply:
        return {"ok": True, "dry": True, "oid": oid, "old": old_sku,
                "platform_sku": platform_sku, "fields": len(fields)}

    # 只改 sku[]（Fnsku）
    new_fields = [(n, new_fnsku if n == "sku[]" else v) for n, v in fields]
    if not any(n == "sku[]" for n, _v in new_fields):
        new_fields.append(("sku[]", new_fnsku))
    r = post_update(pw, new_fields)
    log("   edit_update → error=%s msg=%s" % (r.get("error"), str(r.get("msg"))[:60]))

    # 回读 Fnsku
    f2 = get_form(pw, oid)
    after = [v for n, v in f2 if n == "sku[]"]
    log("   回读 Fnsku=%s %s" % (after, "✅" if after == [new_fnsku] else "❌"))

    # ③ 再次下单代发
    d = H.xiadan_df(pw, oid)
    log("   下单代发 → %s" % str(d.get("msg"))[:80])
    st1 = read_status(pw, po)
    log("   最终状态=%s｜%s" % (st1["status"], st1["err"][:80]))
    ok = st1["status"] not in ("", "?", "未下单", "下单失败")
    return {"ok": ok, "old": old_sku, "after": after,
            "status_before": st0["status"], "status_after": st1["status"],
            "err": st1["err"]}


if __name__ == "__main__":
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    sku = sys.argv[2] if len(sys.argv) > 2 else ""
    do = "--apply" in sys.argv
    p = H._pw()
    print(json.dumps(fix_and_reship(p, po, sku, apply=do), ensure_ascii=False)[:600])
