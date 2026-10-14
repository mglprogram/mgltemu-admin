#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DSH 全部定时任务统一查询 —— 合并三个来源。

背景：DSH 里的「定时任务」实际分散在三处，任何单一接口都看不全：
  1. DSH schedule   —— ~/.dsh/storages/schedule.json，全局唯一文件，
                       但每条带 sessionId，schedule_list 工具只显示当前会话的。
  2. Windows 计划任务 —— 真正干活的无头任务都在这（buy-bill、帕沃尔 Fnsku、
                       滞销库存跟踪等都是用 schtasks / Register-ScheduledTask 建的）。
  3. 全历史会话日志  —— 157 个多帧 zstd 日志，用来还原"曾经建过什么"，
                       包括后来被删除/改名的。

用法：
  python dsh_tasks.py                 # 合并报告
  python dsh_tasks.py --history       # 附加：历史上创建过的任务
  python dsh_tasks.py --json          # 输出 JSON

要点/坑：
  * 会话日志是多帧 zstd，必须 stream_reader(read_across_frames=True)，
    否则 3MB 的文件只解出 200 字节。
  * 标题以 projcache 为准，日志里的 session/title 是历史快照会过时。
  * 判据是 tool/call 事件的 data.name，不要去 grep "schedule_create" 字符串
    （request/header 的工具 schema 里也有，会误报）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

DSH_HOME = Path.home() / ".dsh"
SCHEDULE_STORE = DSH_HOME / "storages" / "schedule.json"
SESSIONS_DIR = DSH_HOME / "sessions"
PROJCACHE = DSH_HOME / "storages" / "session_projcache" / "sessions"

# 只关心这些关键词相关的 Windows 任务（排除 OneDrive/WPS/HP/Realtek 等系统噪音）
INTEREST_KEYWORDS = (
    "temu", "bel", "bhy", "pw", "powerwarehouse", "power", "fnsku", "sku",
    "滞销", "slow", "stock", "shipping", "fund", "copilot", "workbuddy",
    "reminder", "folder", "invoice", "overdue", "abnormal", "限流", "底部", "bottom",
    "tk-", "bd-",
)

# 明确排除的系统/厂商任务
EXCLUDE_KEYWORDS = (
    "onedrive", "wps", "office", "doubao", "rtkaud", "hp ", "printer",
    "defender", "windows", "edge", "nvidia", "adobe",
)


# ----------------------------------------------------------------------------
# 来源 1：DSH schedule.json
# ----------------------------------------------------------------------------
def load_dsh_schedules() -> list[dict]:
    if not SCHEDULE_STORE.exists():
        return []
    try:
        data = json.loads(SCHEDULE_STORE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 无法解析 {SCHEDULE_STORE}: {exc}", file=sys.stderr)
        return []
    rows = []
    for tid, row in ((data.get("tables") or {}).get("tasks") or {}).items():
        rec = row.get("record") or {}
        hist = (row.get("deliveryHistory") or {}).get("records") or []
        rows.append({
            "id": tid,
            "sessionId": row.get("sessionId"),
            "title": rec.get("title"),
            "kind": rec.get("kind"),
            "scheduledAt": rec.get("scheduledAt"),
            "status": row.get("status"),
            "deliveries": len(hist),
        })
    return rows


# ----------------------------------------------------------------------------
# 来源 2：Windows 计划任务
# ----------------------------------------------------------------------------
def load_windows_tasks() -> list[dict]:
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\\' } | "
             "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath; "
             "$a = ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ;; '; "
             "$t = ($_.Triggers | ForEach-Object { $_.StartBoundary }) -join ' ;; '; "
             "[PSCustomObject]@{ Name=$_.TaskName; State=$_.State; Trigger=$t; "
             "Last=$i.LastRunTime; Next=$i.NextRunTime; RC=$i.LastTaskResult; Action=$a } } | ConvertTo-Json -Depth 4"],
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 读取 Windows 计划任务失败: {exc}", file=sys.stderr)
        return []
    txt = (raw.stdout or "").strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, dict):
        data = [data]
    return data


def is_relevant(name: str) -> bool:
    low = (name or "").lower()
    if any(k in low for k in EXCLUDE_KEYWORDS):
        return False
    return any(k.lower() in low for k in INTEREST_KEYWORDS)


# ----------------------------------------------------------------------------
# 来源 3：会话日志历史
# ----------------------------------------------------------------------------
def _dec(path: Path) -> str:
    import zstandard
    out = bytearray()
    with path.open("rb") as fh:
        r = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        while True:
            c = r.read(1 << 20)
            if not c:
                break
            out.extend(c)
    return out.decode("utf-8", errors="replace")


def live_titles() -> dict[str, str]:
    out = {}
    if not PROJCACHE.exists():
        return out
    for f in PROJCACHE.glob("*.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            out[f.stem] = (((j.get("record") or {}).get("rows") or {}).get("title") or {}).get("val")
        except Exception:  # noqa: BLE001
            pass
    return out


def scan_history() -> dict:
    """找出所有会话里真实建过定时任务的动作（DSH 的 + Windows 的）。"""
    titles = live_titles()
    dsh_calls, win_calls = [], []
    files = sorted(SESSIONS_DIR.rglob("*.zstd"))
    for zf in files:
        try:
            text = _dec(zf)
        except Exception:  # noqa: BLE001
            continue
        sid = zf.parent.name
        if '"tool/call"' not in text:
            continue
        for line in text.splitlines():
            if '"tool/call"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("type") != "tool/call":
                continue
            d = rec.get("data") or {}
            name = d.get("name")
            args = d.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001
                    args = {"_raw": args[:300]}
            if name == "schedule_create":
                dsh_calls.append({"session": sid, "title": titles.get(sid), "args": args})
            elif name == "pwsh":
                cmd = (args or {}).get("command", "") if isinstance(args, dict) else ""
                if any(k in cmd for k in ("schtasks", "New-ScheduledTask", "Register-ScheduledTask")):
                    win_calls.append({"session": sid, "title": titles.get(sid), "cmd": cmd})
    return {"dsh": dsh_calls, "windows": win_calls, "scanned": len(files)}


# ----------------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------------
def build_origin_map() -> dict[str, set[str]]:
    """任务名 -> 创建它的会话标题集合（扫全历史日志得出）。"""
    import re as _re
    titles = live_titles()
    origin: dict[str, set[str]] = {}
    if not SESSIONS_DIR.exists():
        return origin
    for zf in sorted(SESSIONS_DIR.rglob("*.zstd")):
        try:
            text = _dec(zf)
        except Exception:  # noqa: BLE001
            continue
        if "schtasks" not in text and "New-ScheduledTask" not in text:
            continue
        who = titles.get(zf.parent.name) or zf.parent.name[:24]
        for pat in (r'/TN\s+"?([A-Za-z0-9\u4e00-\u9fff\-_.]+)"?',
                    r'-TaskName\s+"?([A-Za-z0-9\u4e00-\u9fff\-_.]+)"?'):
            for m in _re.finditer(pat, text):
                origin.setdefault(m.group(1), set()).add(who)
    return origin


def state_name(v) -> str:
    """State 经 JSON 序列化后可能是枚举整数，也可能是字符串。"""
    if isinstance(v, str) and v.strip():
        return v.strip()
    return {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}.get(v, str(v))


def fmt_rc(rc) -> str:
    """返回码含义（Task Scheduler 常见值）。"""
    known = {
        0: "0 OK",
        267009: "267009 正在运行",
        267010: "267010 已禁用",
        267011: "267011 尚未运行",
        267014: "267014 被用户中止(弹窗未关)",
        3221225786: "0xC000013A 被 Ctrl+C 中断",
    }
    if rc is None:
        return "-"
    return known.get(rc, f"{rc} 失败")


def fmt_date(v) -> str:
    """把 /Date(1791680400000)/ 或 ISO 串转成本地可读时间。"""
    if v in (None, "", "None"):
        return "-"
    s = str(v)
    import re as _re
    from datetime import datetime, timezone, timedelta
    m = _re.search(r"/Date\((\d+)\)/", s)
    if m:
        ts = int(m.group(1)) / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    return s.replace("+08:00", "").replace("T", " ")[:19]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true", help="附加历史上创建过的任务")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all-windows", action="store_true", help="列出全部 Windows 任务(默认只列相关的)")
    ap.add_argument("--no-origin", action="store_true", help="跳过会话来源扫描(更快)")
    args = ap.parse_args()

    dsh = load_dsh_schedules()
    win = load_windows_tasks()
    win_rel = win if args.all_windows else [w for w in win if is_relevant(w.get("Name", ""))]
    origin = build_origin_map() if not args.no_origin else {}

    if args.json:
        out = {"dsh": dsh, "windows": win_rel}
        if args.history:
            out["history"] = scan_history()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    # ---- DSH ----
    print("=" * 100)
    print(f"【来源 1】DSH 定时任务（{SCHEDULE_STORE}）")
    print("=" * 100)
    if not dsh:
        print("  (空 —— 没有任何 DSH 会话内的定时任务)")
    else:
        by_sess = defaultdict(list)
        for r in dsh:
            by_sess[r["sessionId"]].append(r)
        for sid, items in by_sess.items():
            print(f"  会话 {sid} —— {len(items)} 个")
            for r in items:
                print(f"    [{r['status']:8s}] {r['kind']:7s} {r['scheduledAt']}  {r['title']}")
                print(f"        投递次数={r['deliveries']}  (只记投递，不记执行)")
        print(f"\n  合计: {len(dsh)} 个")
    print("  注意: schedule_list 工具只能看到【当前会话】，所以会漏掉上面其他会话的。")

    # ---- Windows ----
    print()
    print("=" * 100)
    print(f"【来源 2】Windows 计划任务（{len(win_rel)} 个相关任务 / 根目录共 {len(win)} 个）")
    print("=" * 100)
    if not win_rel:
        print("  (无)")
    for w in sorted(win_rel, key=lambda x: (state_name(x.get("State")) == "Disabled", x.get("Name", ""))):
        rc = w.get("RC")
        st = state_name(w.get("State"))
        tags = []
        if st == "Disabled":
            tags.append("已禁用")
        elif rc == 0:
            pass
        elif rc in (267009,):
            tags.append("正在运行")
        elif rc == 267011:
            tags.append("尚未运行")
        elif rc in (267014, 3221225786):
            tags.append("需人工关窗(非故障)")
        elif rc is None:
            tags.append("无记录")
        else:
            tags.append("失败")
        flag = ("   <== " + ", ".join(tags)) if tags else ""
        print(f"  ● {w.get('Name')}{flag}")
        print(f"      触发: {fmt_date(w.get('Trigger'))}   下次: {fmt_date(w.get('Next'))}")
        print(f"      上次: {fmt_date(w.get('Last'))}   返回码: {fmt_rc(rc)}")
        act = (w.get("Action") or "").strip()
        print(f"      动作: {act[:150]}")
        who = origin.get(w.get("Name"))
        if who:
            print(f"      来自会话: {', '.join(sorted(who))}")
    print(f"\n  合计: {len(win_rel)} 个")

    # ---- history ----
    if args.history:
        print()
        print("=" * 100)
        print("【来源 3】全历史会话日志中创建过定时任务的记录")
        print("=" * 100)
        h = scan_history()
        print(f"  扫描 {h['scanned']} 个会话日志\n")
        if h["dsh"]:
            print(f"  -- DSH schedule_create（{len(h['dsh'])} 次）--")
            for e in h["dsh"]:
                print(f"     [{e['title'] or e['session'][:20]}] {e['args'].get('title')}")
        else:
            print("  -- 无 DSH schedule_create 记录 --")
        if h["windows"]:
            seen = {}
            for e in h["windows"]:
                for m in __import__("re").finditer(r'/TN\s+"?([\w\-.]+)"?', e["cmd"]):
                    seen.setdefault(m.group(1), set()).add(e["title"] or e["session"][:20])
                for m in __import__("re").finditer(r'-TaskName\s+"?([\w\-.]+)"?', e["cmd"]):
                    seen.setdefault(m.group(1), set()).add(e["title"] or e["session"][:20])
            print(f"\n  -- Windows 任务创建/修改（{len(h['windows'])} 次调用，涉及 {len(seen)} 个任务名）--")
            for tname, sess in sorted(seen.items()):
                print(f"     {tname}")
                print(f"        来自会话: {sorted(sess)}")
        else:
            print("\n  -- 无 Windows 任务创建记录 --")

    return 0


if __name__ == "__main__":
    sys.exit(main())
