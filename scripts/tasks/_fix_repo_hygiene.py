# -*- coding: utf-8 -*-
"""一次修掉 6 条红单测背后的「仓库卫生」问题（都是 11-12 那几轮 agent 留下的）。

1. scripts/data/task_watch.json 带 BOM → 单测 json.load(utf-8) 直接炸；
2. scripts/tasks/run_shein_stock_apply.cmd 混了 CRLF/LF（cmd 契约要求纯 CRLF）；
3. 注册表 TEMU-Fulfill-Daily 的「实测耗时分钟」=60，实测 p90=113.7 → 填 115；
4. 新任务 TEMU-SHEIN-Stock-Sync 没显式声明「自动重试」（默认关，但要写出来）。

只改这四处，改完打印 diff 摘要。用法：python scripts/tasks/_fix_repo_hygiene.py [--apply]
"""
import io
import json
import os
import re
import sys

PROJ = r"D:\AI-workplace\TEMU"
REG = r"C:\Users\saing\.agents\skills\scheduled-tasks\tasks.json"
WATCH = os.path.join(PROJ, "scripts", "data", "task_watch.json")
CMD = os.path.join(PROJ, "scripts", "tasks", "run_shein_stock_apply.cmd")

APPLY = "--apply" in sys.argv


def fix_bom(path: str) -> str:
    raw = open(path, "rb").read()
    if not raw.startswith(b"\xef\xbb\xbf"):
        return "无 BOM，跳过"
    if APPLY:
        open(path, "wb").write(raw[3:])
    json.loads(raw[3:].decode("utf-8"))
    return "去掉 BOM（%d 字节）" % len(raw)


def fix_crlf(path: str) -> str:
    raw = open(path, "rb").read()
    norm = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if norm == raw:
        return "已是纯 CRLF，跳过"
    if APPLY:
        open(path, "wb").write(norm)
    return "统一成 CRLF（%d → %d 字节）" % (len(raw), len(norm))


def main() -> int:
    print("[1] task_watch.json:", fix_bom(WATCH))
    print("[2] run_shein_stock_apply.cmd:", fix_crlf(CMD))

    s = io.open(REG, encoding="utf-8-sig").read()
    orig = s

    # [3] 实测耗时分钟 60 → 115（只在 TEMU-Fulfill-Daily 条目内）
    i = s.find('"TEMU-Fulfill-Daily"')
    j = s.find('"name"', i + 20)
    end = j if j > 0 else len(s)
    seg = s[i:end]
    m = re.search(r'"实测耗时分钟":\s*60\b', seg)
    if m:
        seg = seg[:m.start()] + '"实测耗时分钟": 115' + seg[m.end():]
        s = s[:i] + seg + s[end:]
        print("[3] 实测耗时分钟：60 → 115")
    else:
        print("[3] 实测耗时分钟：没找到 60（可能已改）")

    # [4] 新任务显式声明「自动重试」
    n = '"TEMU-SHEIN-Stock-Sync"'
    val = s.find(n)
    if val < 0:
        print("[4] 注册表里没有 TEMU-SHEIN-Stock-Sync（跳过）")
    else:
        start = s.rfind("{", 0, val)                  # 该条目的对象开头
        start = start if start > 0 else val
        end2 = s.find('"name"', val + len(n))
        end2 = end2 if end2 > 0 else len(s)
        tseg = s[start:end2]
        if '"自动重试"' in tseg:
            print("[4] 已有「自动重试」声明，跳过")
        else:
            m2 = re.search(r'\n(\s*)"name":\s*"TEMU-SHEIN-Stock-Sync",\n', tseg)
            if not m2:
                print("[4] 形状不符，没敢加")
            else:
                ind = m2.group(1)
                ins = (ind + '"自动重试": false,\n'
                       + ind + '"_自动重试说明": "重跑等于再执行一次业务动作'
                       + '（抓数/调库存），未逐条核实幂等前保持关闭。",\n')
                tseg = tseg[:m2.end()] + ins + tseg[m2.end():]
                s = s[:start] + tseg + s[end2:]
                print("[4] TEMU-SHEIN-Stock-Sync: 已声明 自动重试=false")

    if s != orig:
        json.loads(s)                                   # 语法自检
        if APPLY:
            io.open(REG, "w", encoding="utf-8-sig").write(s)
    print("（预览；加 --apply 生效）" if not APPLY else "已写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
