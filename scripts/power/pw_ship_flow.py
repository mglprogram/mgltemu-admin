# -*- coding: utf-8 -*-
"""端到端验证：**设仓渠道 → 下单代发**，全部走 HTTP。

用户 2026-11-25 指出的关键顺序：
    ① 先选仓渠道（修改仓库渠道 → 加州3仓 → Temu → temu官方面单 → 提交）
    ② 才点「下单代发」
少了 ①，`act=xiadan_df` 只会回 `{"error":1,"msg":"下单成功","html":""}`，状态仍是「未下单」。

复用：
  · `pw_hwc_flow.http_set_wh`   改仓渠道（已是 HTTP 直提）
  · `pw_hwc_api.query_orders`   查单拿订单 id
  · `pw_hwc_api.xiadan_df`      下单代发
常量：
  CK = {"加州3仓": "22", "kansas仓": "10"}
  PLATFORM_ROUTE = {"temu": ["temu", "temu官方面单"], "shein": ["Shein", "Shein官方面单"]}
"""
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402
import pw_hwc_flow as F  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
WH = sys.argv[2] if len(sys.argv) > 2 else "加州3仓"
PLATFORM = sys.argv[3] if len(sys.argv) > 3 else "temu"
APPLY = "--apply" in sys.argv
STATUSES = ("下单失败", "下单成功", "已下单", "未下单")


def state_of(html: str, po: str) -> str:
    import re  # noqa: PLC0415
    blocks = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S)
    for _oid, tr in blocks:
        if po in tr:
            hit = [s for s in STATUSES if s in tr]
            return hit[-1] if hit else "?"
    return "?"


def main():
    p = H._pw()
    q = H.query_orders(p, PO)
    oid = (q["rows"] or [{}])[0].get("id")
    print("单号 %s | id=%s | 当前状态=%s"
          % (PO, oid, state_of(q.get("html") or "", PO)))
    if not oid:
        return 1

    print("\n① 改仓库渠道：%s / %s（%s）"
          % (WH, PLATFORM, "真提交" if APPLY else "dry-run"))
    if APPLY:
        r = F.http_set_wh([int(oid)], WH, platform=PLATFORM, log=print)
        print("   →", json.dumps(r, ensure_ascii=False)[:200])
        import time  # noqa: PLC0415
        time.sleep(2)

    print("\n② 下单代发：")
    if APPLY:
        d = H.xiadan_df(p, oid)
        print("   →", json.dumps(d, ensure_ascii=False)[:300])
        import time  # noqa: PLC0415
        time.sleep(3)

    q2 = H.query_orders(p, PO)
    print("\n③ 最终状态：", state_of(q2.get("html") or "", PO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
