# -*- coding: utf-8 -*-
"""换 `ck_sku[]` 再下单 —— **修好字段解析重名问题**。

上一版 dry-run 发现 `city` / `district` 解析成空：
    页面同时存在
      <input type="hidden" name="city" value="17">
      <select name="city"> <option value="0">请选择州</option> ...   （级联占位）
    我的解析先收 input，再收 select，**后者覆盖前者** ⇒ 提交时把 city 写成 0。

修法：**同名字段 input 优先**，select 只在没有同名 input 时才收。
"""
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


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def read_form(pw, oid: str) -> list:
    """解析 form[name=myform]，同名字段 **input 优先**。"""
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % oid)
    t = r if isinstance(r, str) else str(r)
    m = re.search(r'<form[^>]*name="myform"[^>]*>(.*?)</form>', t, re.S | re.I)
    body = m.group(1) if m else t

    inputs, selects = [], []
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
        inputs.append((name, v))

    for name, inner in re.findall(
            r'<select[^>]*name="([^"]*)"[^>]*>(.*?)</select>', body, re.S | re.I):
        s = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', inner, re.I)
        selects.append((name, _html.unescape(s.group(1)) if s else ""))

    have = {n for n, _v in inputs}
    out = list(inputs) + [(n, v) for n, v in selects if n not in have]
    return out


def submit(pw, pairs: list) -> dict:
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


def find_oid(pw, po: str) -> str:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
    c = d.get("content") or ""
    m = re.search(r'<tr class="tr_html_(\d+)[^"]*">(?:(?!</tr>).)*?%s' % re.escape(po),
                  c, re.S)
    return m.group(1) if m else ""


def state(pw, po: str) -> dict:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
    c = d.get("content") or ""
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
        addr = re.search(r"收件地址\s*[:：]\s*(\S+\s*\S*)", txt)
        return {"id": oid, "status": st, "err": err[:120],
                "sku": sku.group(1) if sku else "", "addr": addr.group(1) if addr else ""}
    return {"id": "", "status": "查不到", "err": "", "sku": "", "addr": ""}


def main():
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    new_sku = sys.argv[2] if len(sys.argv) > 2 else ""
    do = "--apply" in sys.argv
    pw = SS.PW()
    pw.login()
    st0 = state(pw, po)
    print("① 当前:", json.dumps(st0, ensure_ascii=False))
    oid = st0.get("id") or find_oid(pw, po)
    if not oid:
        sys.exit("查不到订单")

    fields = read_form(pw, oid)
    addr = {n: v for n, v in fields
            if n in ("country", "city_name", "city", "district", "zip")}
    cur = [v for n, v in fields if n == "ck_sku[]"]
    print("② 表单 %d 字段 | ck_sku=%s" % (len(fields), cur))
    print("   地址: %s" % json.dumps(addr, ensure_ascii=False))
    miss = [k for k, v in addr.items() if not v or v == "0"]
    if miss:
        print("   ⚠ 地址仍缺: %s —— 提交会把它们写空，已中止" % miss)
        return 1
    if not do:
        print("\n[dry-run] 将把 ck_sku[] %s → %s" % (cur, new_sku))
        return 0

    print("\n③ 提交（只改 ck_sku[]）")
    pairs = [(n, (new_sku if n == "ck_sku[]" else v)) for n, v in fields]
    r = submit(pw, pairs)
    print("   → error=%s msg=%s" % (r.get("error"), str(r.get("msg"))[:70]))
    st1 = state(pw, po)
    print("④ 回读:", json.dumps(st1, ensure_ascii=False))

    if st1.get("sku") == new_sku:
        print("\n⑤ 再下单代发")
        import pw_hwc_api as H  # noqa: PLC0415
        d = H.xiadan_df(pw, oid)
        print("   →", str(d.get("msg"))[:100])
        import time  # noqa: PLC0415
        time.sleep(3)
        print("⑥ 最终:", json.dumps(state(pw, po), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
