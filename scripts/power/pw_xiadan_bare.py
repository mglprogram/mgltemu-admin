# -*- coding: utf-8 -*-
"""用**裸 POST**（无 Content-Type / 无 body）重试下单代发。

页面 JS 的原始调用（已挖到）：
    $.ajax({
      url: "./hwc_shop.php?act=xiadan_df&id=" + ids + '&is_ajax=1',
      type: "post",
      dataType: "json",
      processData: false,   // 不处理发送的数据
      contentType: false,   // **不设置 Content-type 请求头**
    })

而 `pw.post()` 会带 `application/x-www-form-urlencoded` + body
⇒ 后端可能因此判「参数错误」并返回 `{"error":1,"msg":"下单成功","html":""}`（假成功）。

本脚本用裸 POST 重试，并对比结果。
"""
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else ""
BASE = "https://www.powerwarehouse.net/"


def bare_post(pw, path: str) -> str:
    """裸 POST：不带 Content-Type，body 为空，复用 pw 的 cookie jar。"""
    url = BASE + path.lstrip("/")
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", BASE + "hwc_shop.php?act=shop_order&pb=1")
    # 注意：不设 Content-Type（页面就是 contentType:false）
    with pw.op.open(req, timeout=pw.timeout) as r:
        return r.read().decode("utf-8", "replace")


def main():
    p = H._pw()
    po = PO or "PO-211-07055826094713286"
    q = H.query_orders(p, po)
    oid = (q["rows"] or [{}])[0].get("id")
    print("单号:", po, "| 订单id:", oid)
    if not oid:
        return 1
    print("\n① 裸 POST（无 Content-Type、无 body）：")
    r1 = bare_post(p, "hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % oid)
    print("   ", r1[:400])
    print("\n② 普通 POST（带 form-urlencoded）：")
    r2 = p.post("hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % oid, {})
    print("   ", str(r2)[:400])
    q2 = H.query_orders(p, po)
    html = q2.get("html") or ""
    st = [s for s in ("下单失败", "下单成功", "已下单", "未下单") if s in html]
    print("\n③ 状态:", st or "?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
