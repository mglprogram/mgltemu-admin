# -*- coding: utf-8 -*-
"""诊断 `query_orders` 现在全部返回 rows=0 —— 是会话掉了还是接口变了。

只读：直接打一次 `act=query_shop_order`，看原始响应。
  · 若返回 login 页 / error → 会话掉了
  · 若返回正常 HTML 但解析不出 → 解析器坏了
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def main():
    pw = SS.PW()
    pw.login()
    print("登录 OK")

    body = {
        "act": "query_shop_order", "deliver_no": "", "shop_id": "", "ck_id": "",
        "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
        "country": "", "shop_types": "", "pb_str": "", "page": "1",
    }
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    txt = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    print("响应长度:", len(txt))
    print("前 200:", txt[:200])

    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        d = {}
    if not d:
        print("\n⚠ 不是 JSON —— 可能被踢回登录页")
        if "login" in txt.lower():
            print("   → 会话掉了，需要重新 login")
        return 1

    print("\nerror:", d.get("error"), "| message:", d.get("message"))
    content = d.get("content") or ""
    print("content 长度:", len(content))
    print("含 tr_html_:", "tr_html_" in content)
    print("含 checkboxes:", "checkboxes" in content)
    rows = re.findall(r'<tr class="tr_html_(\d+)[^"]*">', content)
    print("行数:", len(rows), rows[:5])
    m = re.search(r"总计\s*(\d+)", re.sub(r"<[^>]+>", "", content))
    print("页面里「总计」:", m.group(1) if m else "(没有)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
