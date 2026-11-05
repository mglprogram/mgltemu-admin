#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从金山「2026发货申请表」9/10/11 月 BH 店铺提取 参考单号 → 运输渠道。

用户 2026-11-05 需求
--------------------
库存跟踪页「帕沃尔在途」的悬浮里，每个参考单号后面要显示这票货走的渠道
（美森 / 以星 / 盐田 …）。渠道只在发货申请表里，帕沃尔系统没有。

表结构（实测 2026-11-05，11 月 BH 店铺表）
----------------------------------------
    c1  = 「帕沃尔-144503077」/「恒信-IB106261027RZ」  ← 纵向合并，覆盖整票的所有尺码行
    c11 = 「发货方式」列，如「海运美西盐田」            ← 与 c1 同高的纵向合并
    c13 = 「备注」列，写 恒信 / 帕沃尔                    ← 区分供应商

所以做法是：按合并单元格展开成 (row, col) → 文本，扫 c1，把 `帕沃尔-<数字>`
里的数字当参考单号，再取同一行 c11 里的渠道关键字。

⚠ 踩过的坑
  · `get-range-data` 的 `range` 必须走 `--file`（PowerShell 会把对象拆散 → "range 须为对象"）
  · rowTo 超过表的实际末行（该 sheet 的 rowTo）会返回 `rangeData: null`
  · 表里「系统对应单号」列是空的，真正的单号在 c1 的「帕沃尔-xxx」合并格里

产出
----
    temu-admin/public/data/ref-channel.json
    {"generatedAt": "...", "count": N,
     "map": {"144503077": "盐田", ...},
     "raw": {"144503077": "海运美西盐田", ...},
     "bySheet": {"11月份BH店铺": N}}

用法
----
    python scripts/goods/fetch_ref_channel.py            # 抓 + 落盘
    python scripts/goods/fetch_ref_channel.py --dry      # 只打印统计
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "out" / "_kd"
OUT = ROOT / "temu-admin" / "public" / "data" / "ref-channel.json"

FILE_ID = "fd6DUHuRpxMxs9JhZCYkrxbhDN1kzqM4L"
#: 名称 → (worksheet_id, 该表实际末行)
SHEETS = {
    "11月份BH店铺": (40, 269),
    "10月份BH店铺": (36, 1327),
    "9月份BH店铺": (33, 1052),
}
CLI = "kdocs-cli"

#: 从「海运美西盐田」这类文本里认渠道（顺序有讲究：先长词）
CHANNEL_WORDS = (
    "美森", "以星", "盐田", "长荣", "中远", "空运", "普船", "快船",
    "COSCO", "EMC", "OOCL", "MSC", "HPL",
)

#: 单号所在的列（表头 c1 = 系统对应单号；「帕沃尔-xxx」也写在这一列）
COL_NO = 1
#: 发货方式所在列（表头 c11）
COL_CHANNEL = 11


def write_params(name: str, obj: dict) -> str:
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def run(args: list[str], retries: int = 4) -> dict:
    """调 kdocs-cli。金山偶发 TLS handshake timeout / request failed，必须重试。"""
    last = ""
    for i in range(retries):
        p = subprocess.run([CLI, *args, "--compact"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for line in (p.stdout or "").strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                last = line
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        last = ((p.stdout or "") + " || " + (p.stderr or ""))[:200]
        time.sleep(1.5 + 1.5 * i)
    raise RuntimeError("kdocs 输出异常：%s" % last)


def read_cells(ws: int, r0: int, r1: int, c0: int, c1: int) -> list[dict]:
    pf = write_params("r_%d_%d.json" % (ws, r0), {
        "file_id": FILE_ID,
        "worksheet_id": ws,
        "range": {"rowFrom": r0, "rowTo": r1, "colFrom": c0, "colTo": c1},
    })
    resp = run(["sheet", "get-range-data", "--file", pf])
    detail = (resp.get("data") or {}).get("detail") or {}
    return detail.get("rangeData") or []


def channel_of(text: str) -> str:
    """「海运美西盐田」→「盐田」；认不出就原样返回（截断到 12 字）。"""
    for w in CHANNEL_WORDS:
        if w in text:
            return w
    t = text.strip()
    # 去掉「海运/美西」这类前缀噪音
    for pre in ("海运", "空运"):
        if t.startswith(pre):
            t = t[len(pre):]
    if t.startswith("美西"):
        t = t[len("美西"):]
    return t[:12]


def scan_sheet(name: str, ws: int, last_row: int, step: int = 120) -> dict[str, str]:
    """返回 {参考单号: 渠道原文}。"""
    out: dict[str, str] = {}
    for r0 in range(0, last_row + 1, step):
        r1 = min(r0 + step - 1, last_row)
        cells = read_cells(ws, r0, r1, 0, 13)
        if not cells:
            continue
        # 合并单元格只出现在起点，按 rowFrom/rowTo 展开
        grid: dict[tuple[int, int], str] = {}
        for c in cells:
            txt = str(c.get("cellText") or "").strip()
            if not txt:
                continue
            for rr in range(int(c.get("rowFrom", 0)), int(c.get("rowTo", 0)) + 1):
                for cc in range(int(c.get("colFrom", 0)), int(c.get("colTo", 0)) + 1):
                    grid[(rr, cc)] = txt
        for (r, c), v in grid.items():
            if c != COL_NO:
                continue
            m = re.search(r"^(?:帕沃尔|恒信)\s*[-—]?\s*(\d{8,9})", v)
            if not m:
                # 也兼容纯数字
                m2 = re.fullmatch(r"(\d{8,9})", v)
                if not m2:
                    continue
                ref = m2.group(1)
            else:
                ref = m.group(1)
            chan = grid.get((r, COL_CHANNEL), "")
            if not chan:
                continue
            out.setdefault(ref, chan)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发货申请表 → 参考单号渠道")
    ap.add_argument("--dry", action="store_true", help="只打印统计，不落盘")
    args = ap.parse_args(argv)

    raw: dict[str, str] = {}
    by_sheet: dict[str, int] = {}
    for name, (ws, last_row) in SHEETS.items():
        try:
            got = scan_sheet(name, ws, last_row)
        except Exception as e:  # noqa: BLE001
            print("[ref-channel] %s 读取失败：%s" % (name, str(e)[:120]))
            by_sheet[name] = 0
            continue
        before = len(raw)
        raw.update(got)
        by_sheet[name] = len(got)
        print("[ref-channel] %s：%d 个单号（新增 %d，累计 %d）"
              % (name, len(got), len(raw) - before, len(raw)))

    mapping = {k: channel_of(v) for k, v in raw.items()}
    dist = Counter(mapping.values())
    print("[ref-channel] 渠道分布：", dict(dist.most_common(8)))

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(mapping),
        "map": mapping,
        "raw": raw,
        "bySheet": by_sheet,
        "rule": {
            "note": "参考单号 → 运输渠道（美森/以星/盐田…），取自金山「2026发货申请表」"
                    "9/10/11 月 BH 店铺：c1 的「帕沃尔-xxx」合并格里是单号，"
                    "同一行的 c11「发货方式」是渠道。",
            "source": "https://www.kdocs.cn/l/cb5Uxw7aD1Jp",
        },
    }

    if args.dry:
        print(json.dumps({k: v for k, v in payload.items() if k not in ("map", "raw")},
                         ensure_ascii=False, indent=2))
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print("[ref-channel] 已写 %s（%d 个单号，%.0f KB）"
          % (OUT.relative_to(ROOT), len(mapping), OUT.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
