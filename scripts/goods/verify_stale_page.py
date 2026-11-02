# -*- coding: utf-8 -*-
"""验证线上「滞销库存」页关键改动是否已生效。"""
import re
import sys

import requests

BASE = "https://mgltemu.us.ci"


def main():
    r = requests.get(BASE + "/", timeout=30)
    print("index:", r.status_code, "| gate:", "temu_admin_gate_ok" in r.text)

    m = re.findall(r"/js/(router\.[A-Za-z0-9_-]+\.js)", r.text)
    if not m:
        print("[FAIL] 找不到 router bundle")
        return 1
    rj = requests.get(BASE + "/js/" + m[0], timeout=30).text
    print("router:", m[0], "| TemuStaleStock:", "TemuStaleStock" in rj,
          "| 滞销库存:", "滞销库存" in rj)

    c = re.findall(r"js/(stale-stock\.[A-Za-z0-9_-]+\.js)", rj)
    if not c:
        print("[FAIL] 找不到 stale-stock chunk")
        return 1
    x = requests.get(BASE + "/js/" + c[0], timeout=30).text
    print("chunk:", c[0])

    checks = {
        "默认 tab = all": '("all")' in x or "(`all`)" in x or '"all"' in x,
        "全部按钮": "全部" in x,
        "其中 Kansas 列": "其中 Kansas" in x,
        "Kansas 概览卡": "已含 Kansas" in x,
        "合计 tooltip": "合计 = 代发库存" in x,
    }
    for k, v in checks.items():
        print(f"  {'OK ' if v else 'MISS'} {k}")

    # 找默认值那一行，确认是 all 而不是 inactive
    i = x.find("useState") if "useState" in x else -1
    for pat in (r'\.value=\s*"inactive"', r'\(\s*"inactive"\s*\)'):
        if re.search(pat, x):
            print("  [warn] 代码里仍出现 inactive 默认值，需人工确认")

    d = requests.get(BASE + "/data/stale-stock.json", timeout=30).json()
    s = d["summary"]
    print("\n数据: 库存 %s = 加州 %s + Kansas %s （途中 %s）"
          % (s["stock"], s["stock"] - s["kansas"], s["kansas"], s["transit"]))
    print("     无动销 Kansas %s 件；导出时间 %s"
          % (s["inactiveKansas"], s.get("kansasExportAt") or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
