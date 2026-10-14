# -*- coding: utf-8 -*-
"""自检：大屏「款·库存预警」卡片的前端算法 vs 构建期权威 restock 计数。"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
g = json.loads((ROOT / "temu-admin/public/data/goods.json").read_text(encoding="utf-8"))

print("=== 「款·库存预警」卡片：前端 rows.filter 算法 vs 权威 restock.verdictCounts ===")
cases = [
    ("ALL", g["lowStock"]["rows"], g["lowStock"]["restock"]),
    ("BEL", g["stores"]["BEL"]["lowStock"]["rows"], g["stores"]["BEL"]["lowStock"]["restock"]),
    ("BHY", g["stores"]["BHY"]["lowStock"]["rows"], g["stores"]["BHY"]["lowStock"]["restock"]),
]
for name, rows, rest in cases:
    rc = (rest or {}).get("verdictCounts") or {}
    front_add = sum(1 for r in rows if r.get("verdict") == "可加库存" or r.get("canRestock"))
    front_zero = sum(1 for r in rows if r.get("stock") == 0)
    auth_add = rc.get("可加库存")
    auth_zero = rc.get("需补货")
    flag = "" if front_add == auth_add else "   <<< 不一致"
    print("  %-4s 可加库存 前端=%-3s 权威=%-3s%s" % (name, front_add, auth_add, flag))
    print("       已断货   前端=%-3s  (权威「需补货」=%-3s)" % (front_zero, auth_zero))

print()
print("=== 三个数字卡在 ALL 下会显示什么（含我上轮改的预售卡）===")
rc = g["lowStock"]["restock"]["verdictCounts"]
print("  在售款数 SKC   =", g["totals"]["onSaleSkc"])
print("  在售库存       =", g["totals"]["stock"])
print("  限流商品       =", sum((s or {}).get("total", 0) for s in []) or "见 limited.json")
print("  低库存款数     =", g["lowStock"]["summary"]["style"], " 尺码 =", g["lowStock"]["summary"]["sku"])
print("  可开预售       =", rc["可开预售"])
print("  可加库存       =", rc["可加库存"])
print("  需补货         =", rc["需补货"])
print()
print("  预警卡小字「可加库存/已断货」用的是 rows.filter，与上面权威一致吗：")
print("     可加库存 filter =", sum(1 for r in g["lowStock"]["rows"]
                                   if r.get("verdict") == "可加库存" or r.get("canRestock")),
      " 权威 =", rc["可加库存"])
