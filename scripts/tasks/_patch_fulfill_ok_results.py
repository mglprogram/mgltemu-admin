# -*- coding: utf-8 -*-
"""把 TEMU-Fulfill-Daily 的 ok_results 从 [0,5] 改成 [0,3,5]，并补一条 3 的说明。

只改这一处，且**按条目切片**定位（全局 `"ok_results": [0,5]` 不止一处）。
BOM 保留（原文件是 utf-8-sig，python 读它要 utf-8-sig）。
"""
import io
import json
import re

P = r"C:\Users\saing\.agents\skills\scheduled-tasks\tasks.json"
TASK = '"TEMU-Fulfill-Daily"'

DOC_KEY = '_ok_results说明_20261112'
DOC_VAL = ("`3` = **只有软失败**（某店有单没买成 / 缺货 / 平台条件未满足），"
           "主体发货与报表都已完成 ⇒ 带告警成功（面板琥珀色，飞书/手机不推失败告警）。"
           "用户 2026-11-12 口径：「软失败不该让整链判红」。`1`/`42` 仍是真故障。")

s = io.open(P, encoding="utf-8-sig").read()
i = s.find(TASK)
if i < 0:
    raise SystemExit("找不到条目")
j = s.find('"name"', i + len(TASK))
end = j if j > 0 else len(s)

seg = s[i:end]
if DOC_KEY in seg:
    raise SystemExit("已经改过了，不重复改")

m = re.search(r'"ok_results": \[\s*0,\s*5\s*\],', seg)
if not m:
    raise SystemExit("ok_results 形状不符，没敢改：%r" % seg[:400])

new_arr = '"ok_results": [\n        0,\n        3,\n        5\n      ],'
seg2 = seg[:m.start()] + new_arr + seg[m.end():]

# 在 _ok_results说明 那一行之后插入新的说明键
m2 = re.search(r'\n(\s*)"_ok_results说明": "[^"]*",\n', seg2)
if not m2:
    raise SystemExit("找不到 _ok_results说明 行")
indent = m2.group(1)
insert = '%s"%s": "%s",\n' % (indent, DOC_KEY, DOC_VAL)
seg2 = seg2[:m2.end()] + insert + seg2[m2.end():]

out = s[:i] + seg2 + s[end:]
json.loads(out)                      # 语法自检
io.open(P, "w", encoding="utf-8-sig").write(out)

reg = json.loads(io.open(P, encoding="utf-8-sig").read())
for t in reg["tasks"]:
    if t.get("name") == "TEMU-Fulfill-Daily":
        print("ok_results =", t["ok_results"])
        print("说明键在：", DOC_KEY in t)
        break
