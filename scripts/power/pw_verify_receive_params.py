# -*- coding: utf-8 -*-
"""从「待确认订单」(pb=1) 里挑几单，**只读**验证 `order_queren` 接口参数是否正确。

不真提交，只做两件事：
  ① 列出 pb=1 里真实的订单 id（checkboxes 解析验证）
  ② 把 `deliver.php?act=operate` 的请求体打出来（dry-run 不发送）

这样即使没有指定单，也能确认：
    · checkboxes 解析对不对
    · ids 拼接格式（`123i456i`）对不对
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_retry(n=8):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(12)
    raise last


def main():
    pw = login_retry()
    t = pw.get("deliver.php?act=list&pb=1&uselastfilter=1")
    t = t if isinstance(t, str) else str(t)
    print("pb=1 页面长度:", len(t))

    # 找所有 checkboxes
    ids = re.findall(r'name="(?:checkboxes\[\]?|ids\[\]?)"[^>]*value="(\d+)"', t)
    print("checkboxes 数量:", len(ids), ids[:10])

    # 找那行附近的实际 input 命名（取第一行）
    m = re.search(r'<tr[^>]*class="[^"]*tr_html[^"]*"[^>]*>(.*?)</tr>', t, re.S)
    if m:
        print("\n第一行里的 input：")
        for x in re.finditer(r"<input[^>]*>", m.group(1)):
            print("   ", x.group(0)[:170])

    # 从表单解析 select_types 与 hidden
    fm = re.search(r'<form[^>]*name="listForm"[^>]*>(.*?)</form>', t, re.S | re.I)
    if fm:
        body = fm.group(1)
        print("\nlistForm 里的 hidden：")
        for x in re.finditer(r'<input[^>]*type="hidden"[^>]*>', body):
            print("   ", x.group(0)[:150])
        print("\nlistForm 里的 select：")
        for x in re.finditer(r'<select[^>]*name="([^"]*)"[^>]*>', body):
            print("   ", x.group(0)[:150])

    # 构造请求体（dry-run，不发送）
    if ids:
        sample = ids[:2]
        data = [("act", "batch"), ("pb", "1"), ("pb_str", ""),
                ("ids", "".join("%si" % i for i in sample)),
                ("select_types", "order_queren")]
        print("\n[dry-run] 将 POST deliver.php?act=operate")
        print("   body:", json.dumps(dict(data), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
