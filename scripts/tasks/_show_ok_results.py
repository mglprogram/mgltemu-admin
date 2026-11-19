# -*- coding: utf-8 -*-
"""打印 tasks.json 里 TEMU-Fulfill-Daily 条目的 ok_results 段落（只读）。"""
import io

P = r"C:\Users\saing\.agents\skills\scheduled-tasks\tasks.json"
s = io.open(P, encoding="utf-8-sig").read()
j = s.find('"TEMU-Fulfill-Daily"')
print("entry at", j)
seg = s[j:j + 12000]
k = seg.find('"ok_results"')
if k < 0:
    raise SystemExit("no ok_results near entry")
print(seg[k - 200:k + 900])
