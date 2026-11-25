# -*- coding: utf-8 -*-
"""修被写坏的地址：删单重同步 → 等平台重新拉下来 → 换 Fnsku → 重下单（全接口）。

顺序（用户 2026-11-25 定）：
  ① 修地址： POST hwc_shop.php {act:"deletes_shop_order", cate_id:<oid>}
              → 等平台重新同步（几分钟内会重新出现）
  ② 换 Fnsku：POST hwc_shop.php?act=edit_update，**只传产品行字段**（不传地址）
  ③ 重下单：  POST hwc_shop.php?act=xiadan_df&id=<oid>&is_ajax=1

★ 为什么只传产品行字段：
  编辑页地址是级联下拉（country/city_name/city/district），静态解析出来是空/0；
  整表单回传会把地址写成 "0 0" ⇒ 下单报「州0不存在」（2026-11-25 实测）。

产品行字段（编辑页实测，订单 3013984）：
  order_id / act=edit_update / ck_sku[] / sku[] / huan_id[] / pname[] /
  products_nums[] / pvalue[] / is_tj / return_canshu / user_id_old
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
STATUSES = ("下单失败", "已下单", "未下单", "已取消")


# ---------------------------------------------------------------- ① 删单重同步
def delete_order(pw, oid: str, log=print) -> dict:
    """删单（`deletes_shop_order`）—— 平台会重新同步，地址恢复。"""
    r = pw.post("hwc_shop.php", {"act": "deletes_shop_order", "cate_id": str(oid)})
    try:
        d = json.loads(r) if isinstance(r, str) else r
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": str(r)[:200]}
    log("   删单 %s → error=%s msg=%s" % (oid, d.get("error"), str(d.get("msg"))[:60]))
    return d


# ---------------------------------------------------------------- ② 只传产品行
PRODUCT_FIELDS = ("act", "order_id", "ck_sku[]", "sku[]", "huan_id[]", "pname[]",
                  "products_nums[]", "pvalue[]", "is_tj", "return_canshu", "user_id_old")


def read_rows(pw, oid: str, log=print) -> dict:
    """从编辑页读产品行字段（不碰地址）。"""
    r = pw.get("hwc_shop.php?act=edit_order&id=%s" % oid)
    t = r if isinstance(r, str) else str(r)
    out = {}
    for name in PRODUCT_FIELDS:
        if name == "act":
            out[name] = ["edit_update"]
            continue
        vals = re.findall(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), t)
        # 过滤 JS 模板残留
        vals = [v for v in vals if v and "+" not in v]
        out[name] = vals
    log("   产品行：ck_sku=%s huan_id=%s" % (out.get("ck_sku[]"), out.get("huan_id[]")))
    return out


def post_product_rows(pw, oid: str, new_fnsku: str, log=print) -> dict:
    """只提交产品行（不传地址字段）。

    ★ 2026-11-25：编辑页这三个隐藏字段解析不到静态值，按页面实测补默认：
        is_tj         = 0
        return_canshu = &pb=&is_tui=0
        user_id_old   = 14450
    """
    rows = read_rows(pw, oid, log=log)
    DEFAULTS = {"is_tj": ["0"], "return_canshu": ["&pb=&is_tui=0"],
                "user_id_old": ["14450"]}
    fields = []
    for name in PRODUCT_FIELDS:
        vals = rows.get(name) or DEFAULTS.get(name) or [""]
        if name == "sku[]":
            vals = [new_fnsku] * max(1, len(rows.get("huan_id[]") or [1]))
        if name == "is_tj":
            vals = ["0"]
        if name == "order_id":
            vals = [str(oid)]
        for v in vals:
            fields.append((name, v))
    data = urllib.parse.urlencode(fields, doseq=True).encode()
    req = urllib.request.Request(BASE + "hwc_shop.php", data=data, method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "hwc_shop.php?act=edit_order&id=%s" % oid)
    with pw.op.open(req, timeout=pw.timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        d = json.loads(body)
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": body[:200]}
    log("   edit_update(仅产品行) → error=%s msg=%s"
        % (d.get("error"), str(d.get("msg"))[:60]))
    return d


# ---------------------------------------------------------------- 读状态
def read_status(pw, po: str) -> dict:
    q = H.query_orders(pw, po)
    html = q.get("html") or ""
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if po not in tr:
            continue
        st = ""
        for s in STATUSES:
            if s in tr:
                st = s
        err = ""
        for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
            c2 = re.sub(r"<[^>]+>", " ", c)
            if "条" in c2 and "下单" in c2:
                err = re.sub(r"\s+", " ", c2).strip()
        addr = re.search(r"收件地址\s*[:：]\s*([^<]{0,60})", re.sub(r"<[^>]+>", " ", tr))
        return {"id": oid, "status": st, "err": err[:130],
                "addr": (addr.group(1).strip()[:40] if addr else "")}
    return {"id": "", "status": "查不到", "err": "", "addr": ""}


if __name__ == "__main__":
    po = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
    fn = sys.argv[2] if len(sys.argv) > 2 else "D14450HD6AL"
    do = "--apply" in sys.argv
    p = H._pw()
    st = read_status(p, po)
    print("当前：", json.dumps(st, ensure_ascii=False))
    if not st["id"]:
        sys.exit("查不到订单")
    if not do:
        print("\n[dry-run] 将执行：删单 %s → 等同步 → 换 Fnsku %s → 重下单" % (st["id"], fn))
        sys.exit(0)
    print("\n① 删单重同步")
    delete_order(p, st["id"])
    print("   等平台重新同步（轮询 5 分钟）…")
    newid = ""
    for i in range(20):
        time.sleep(15)
        s2 = read_status(p, po)
        if s2["id"]:
            newid = s2["id"]
            print("   ✅ 重新出现 id=%s 状态=%s 地址=%s"
                  % (newid, s2["status"], s2["addr"]))
            break
        print("   …第 %d 次未出现" % (i + 1))
    if not newid:
        sys.exit("平台没重新同步下来，需人工点「更新订单」")
    print("\n② 换 Fnsku（只传产品行）")
    post_product_rows(p, newid, fn, log=print)
    print("\n③ 重下单代发")
    d = H.xiadan_df(p, newid)
    print("   →", str(d.get("msg"))[:100])
    time.sleep(3)
    print("\n④ 最终：", json.dumps(read_status(p, po), ensure_ascii=False))
