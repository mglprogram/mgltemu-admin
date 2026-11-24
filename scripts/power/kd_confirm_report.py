# -*- coding: utf-8 -*-
"""在金山云盘根目录建「确认成功」文件夹 + 「发货确认清单.xlsx」（三个店各一个 sheet）。

用法：
    python kd_confirm_report.py --init          # 只建文件夹与表
    python kd_confirm_report.py --fill          # 按今日产物写三个 sheet

表结构（每个店一个 sheet）：
  参考单号 | 包裹号 | 渠道 | 闸门结论 | 拦截原因 | 采集时间
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLI = r"C:\Users\saing\AppData\Local\kdocs-cli\kdocs-cli.exe"
STATE = r"D:\AI-workplace\TEMU\data\kd_confirm_report.json"
FOLDER_NAME = "确认成功"
FILE_NAME = "发货确认清单"
STORES = ("BHY", "BEL", "KinglamanSport")
SHEET_TITLES = {"BHY": "BHY", "BEL": "BEL", "KinglamanSport": "KLS"}


def run(args, timeout=120):
    p = subprocess.run([CLI] + args + ["--compact"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    raw = (p.stdout or "").strip()
    i = raw.find("{")
    if i < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[i:])
        return obj
    except Exception:  # noqa: BLE001
        return {}


def load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(d: dict):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def init():
    st = load_state()
    if not st.get("folder_id"):
        d = run(["drive", "create-folder", "name=%s" % FOLDER_NAME])
        fid = ((d.get("data") or {}).get("id") or (d.get("data") or {}).get("file_id")
               or (d.get("data") or {}).get("file", {}).get("id"))
        if not fid:
            print("建文件夹失败：", json.dumps(d, ensure_ascii=False)[:300])
            return 1
        st["folder_id"] = fid
        save_state(st)
        print("✓ 建文件夹「%s」→ %s" % (FOLDER_NAME, fid))
    else:
        print("文件夹已存在 →", st["folder_id"])

    if not st.get("file_id"):
        d = run(["drive", "create-empty-file", "name=%s" % FILE_NAME,
                 "file_extension=xlsx", "parent_id=%s" % st["folder_id"]])
        fid = ((d.get("data") or {}).get("id") or (d.get("data") or {}).get("file_id")
               or (d.get("data") or {}).get("file", {}).get("id"))
        if not fid:
            print("建表格失败：", json.dumps(d, ensure_ascii=False)[:300])
            return 1
        st["file_id"] = fid
        save_state(st)
        print("✓ 建表格「%s.xlsx」→ %s" % (FILE_NAME, fid))
    else:
        print("表格已存在 →", st["file_id"])
    print("\nstate →", STATE)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--fill", action="store_true")
    a = ap.parse_args()
    if a.init:
        raise SystemExit(init())
    ap.print_help()
