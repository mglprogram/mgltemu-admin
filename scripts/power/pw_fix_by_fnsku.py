# -*- coding: utf-8 -*-
"""失败单「换 Fnsku 重下」——**完整正确版**（2026-11-25 定稿）。

用户口径：失败单 → 库存页查有货的 Fnsku → 编辑页把原 sku 覆盖成有货的那个 → 再下单代发

★ 已查清的三套字段（曾经搞混，导致「SKU:状态异常无法下单」）：
    订单编辑页 `ck_sku[]` = **平台SKU**      （如 BHY-YOGA-4SetA-L）
    选品器     `ck_sku[]` = **Fnsku**        （如 D14450HD6AL）
    选品器     `pname[]`  = 产品名/Seller sku（如 BS2-YOGA-4SetA-L）
    选品器     `checkboxes value` = **产品 id**（= 编辑页的 `huan_id[]`）

★ 正确替换三件套（必须同时换，只换一个会「状态异常」）：
    huan_id[] = 选品器 pid
    ck_sku[]  = Fnsku
    pname[]   = 选品器 pname

流程：
  ① 库存页 keywords=<Seller sku> 找有货 Fnsku
  ② 选品器 keywords=<Fnsku> 拿 pid/pname/ck_sku
  ③ 编辑页整表单提交（地址原样，只换产品行三件套）
  ④ xiadan_df 下单代发 → 回读状态
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
STATUSES = ("下单失败", "已下单", "未下单", "已取消")
DATE = ("2026-11-01", "2026-11-25")


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


# ------------------------------------------------------------------ 查订单
def query(pw, po: str) -> str:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": DATE[0], "end_date": DATE[1],
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
    return d.get("content") or ""


def state(pw, po: str) -> dict:
    c = query(pw, po)
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', c, re.S):
        if po not in tr:
            continue
        txt = clean(tr)
        st = next((s for s in STATUSES if s in tr), "")
        err = ""
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
            cc = clean(cell)
            if "条" in cc and "下单" in cc:
                err = cc
        sku = re.search(r"平台SKU\s*[:：]\s*(\S+)", txt)
        wh = re.search(r"发货仓库\s*[:：]\s*(\S+)", txt)
        return {"id": oid, "status": st, "err": err[:130],
                "sku": sku.group(1) if sku else "", "wh": wh.group(1) if wh else ""}
    return {"id": "", "status": "查不到", "err": "", "sku": "", "wh": ""}


# ------------------------------------------------------------------ 找产品
def picker_lookup(pw, fnsku: str, ck: str = "22") -> dict:
    """选品器里按 Fnsku 搜，返回 `{pid, pname, ck_sku, fh_weight}`。"""
    u = ("deliver.php?act=add_products&type=view&user_id=14450&user_name=&ck=%s"
         "&keywords=%s" % (ck, fnsku))
    t = pw.get(u)
    t = t if isinstance(t, str) else str(t)
    blocks = re.split(r'(?=<input type="checkbox" name="checkboxes")', t)
    for b in blocks:
        m = re.match(r'<input type="checkbox" name="checkboxes"\s+class="checkboxes"\s+'
                     r'value="(\d+)"', b)
        if not m:
            continue
        head = b[:1600]

        def g(name):
            mm = re.search(r'name="%s" class="[^"]*" value="([^"]*)"' % re.escape(name),
                           head)
            return mm.group(1) if mm else ""

        if g("ck_sku[]").upper() == fnsku.upper():
            return {"pid": m.group(1), "pname": g("pname[]"),
                    "ck_sku": g("ck_sku[]"), "fh_weight": g("fh_weight[]")}
    return {}


# ------------------------------------------------------------------ 读/写表单
def read_form(pw, oid: str) -> list:
    """同名字段 **input 优先**（city 的隐藏 input 优先于级联 select）。"""
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % oid)
    t = r if isinstance(r, str) else str(r)
    m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', t, re.S | re.I)
    body = m.group(1) if m else t
    inputs, selects = [], []
    for tag in re.findall(r"<input[^>]*>", body, re.I):
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        n = nm.group(1)
        typ = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1].lower()
        if typ in ("button", "submit", "reset", "file", "image"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        val = re.search(r'value="([^"]*)"', tag)
        v = _html.unescape(val.group(1)) if val else ""
        if v.startswith("'+") or v.startswith('"+'):
            v = ""
        inputs.append((n, v))
    for n, inner in re.findall(
            r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
        s = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
        selects.append((n, _html.unescape(s.group(1)) if s else ""))
    have = {n for n, _v in inputs}
    return list(inputs) + [(n, v) for n, v in selects if n not in have]


def post_edit(pw, pairs: list) -> dict:
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


def reship(pw, oid: str) -> dict:
    r = pw.post("hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % oid, {})
    try:
        return json.loads(r if isinstance(r, str) else json.dumps(r))
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": str(r)[:150]}


# ------------------------------------------------------------------ 主流程
def fix_order(pw, po: str, new_fnsku: str, ck: str = "22", apply=False, log=print) -> dict:
    st0 = state(pw, po)
    oid = st0.get("id")
    log("   单号 %s id=%s 状态=%s 平台SKU=%s" % (po, oid, st0["status"], st0["sku"]))
    if not oid:
        return {"ok": False, "why": "查不到订单"}

    prod = picker_lookup(pw, new_fnsku, ck)
    log("   选品器 → pid=%s pname=%s ck_sku=%s"
        % (prod.get("pid"), prod.get("pname"), prod.get("ck_sku")))
    if not prod.get("pid"):
        return {"ok": False, "why": "选品器查不到 Fnsku %s" % new_fnsku}

    fields = read_form(pw, oid)
    addr = {n: v for n, v in fields if n in ("country", "city_name", "city", "district")}
    log("   地址: %s" % json.dumps(addr, ensure_ascii=False))
    if any(not v or v == "0" for v in addr.values()):
        return {"ok": False, "why": "地址字段不完整，拒绝提交（会写坏地址）: %s" % addr}

    if not apply:
        return {"ok": True, "dry": True, "oid": oid, "prod": prod, "addr": addr}

    pairs = []
    for n, v in fields:
        if n == "huan_id[]":
            v = prod["pid"]
        elif n == "ck_sku[]":
            v = prod["ck_sku"]
        elif n == "pname[]":
            v = prod["pname"]
        pairs.append((n, v))
    r = post_edit(pw, pairs)
    log("   edit_update → error=%s msg=%s" % (r.get("error"), str(r.get("msg"))[:60]))

    st1 = state(pw, po)
    log("   回读 平台SKU=%s 状态=%s" % (st1["sku"], st1["status"]))

    d = reship(pw, oid)
    log("   下单代发 → %s" % str(d.get("msg"))[:90])
    import time  # noqa: PLC0415
    time.sleep(3)
    st2 = state(pw, po)
    log("   最终 状态=%s｜%s" % (st2["status"], st2["err"][:90]))
    return {"ok": st2["status"] not in ("下单失败", "未下单", "?", ""),
            "before": st0["status"], "after": st2["status"],
            "err": st2["err"], "api": str(d.get("msg"))[:100]}


if __name__ == "__main__":
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = sys.argv[2] if len(sys.argv) > 2 else ""
    do = "--apply" in sys.argv
    p = SS.PW()
    p.login()
    print(json.dumps(fix_order(p, po, fn, apply=do), ensure_ascii=False)[:700])
