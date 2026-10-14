# -*- coding: utf-8 -*-
"""把某店某板块的产物重置为「真实的 0 条」（用于线上确实没有该板块数据时）。

与 fetch_flux_all.py 的「0 条不写」保护不同：那条保护是为了防止**抓取失败**
把好数据覆盖掉；这里是**已确认页面确实为空**（人工核对过筛选选中 + 共有 0 条），
所以要显式落一个 0 条结果，让面板反映真实状态。

用法：python reset_flux_section.py <BEL|BHY> <accelerating|pending>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"D:\AI-workplace\TEMU")
store = sys.argv[1].upper()
section = sys.argv[2]

p = ROOT / "data" / "flux" / f"{store.lower()}_{section}.json"
old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
print(f"原：{old.get('total')} 个 SPU @ {old.get('generatedAt')}")

payload = {
    "store": store,
    "section": section,
    "filter": "流量加速器待开启" if section == "pending" else "流量加速中",
    "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total": 0,
    "priced": 0,
    "aborted": False,
    "filledFromPrev": 0,
    "emptyConfirmed": True,   # 人工确认页面确实为空
    "previousTotal": old.get("total"),
    "previousAt": old.get("generatedAt"),
    "list": [],
}
p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"新：0 个 SPU（已确认页面为空）-> {p}")