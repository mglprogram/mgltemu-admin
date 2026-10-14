# -*- coding: utf-8 -*-
"""在浏览器内抽取「商品流量视角」待处理商品（限流记录），结果落 JSON。

不经由 shell 传多行脚本（PowerShell + BOM 会破坏 JS 语法），
而是把 JS 读成 UTF-8 文本后用 subprocess 传 argv。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STORE_ID = os.environ.get("BEL_STORE_ID", "27677489075954")
JS_PATH = Path(__file__).resolve().parent / "extract_traffic.js"
OUT_PATH = Path(os.environ.get(
    "TRAFFIC_OUT", r"D:\AI-workplace\TEMU\data\bel\_traffic_raw.json"))


def main():
    js = JS_PATH.read_text(encoding="utf-8").strip()
    # ziniao-cli.cmd 会经 cmd 吞掉多行参数里的换行，改为直接调 node 入口脚本
    npm_dir = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts"
    entry = next(npm_dir.glob("run*.js"), None) or next(npm_dir.glob("*.js"), None)
    if not entry:
        print("未找到 ziniao-cli 入口脚本:", npm_dir)
        return 3
    proc = subprocess.run(
        ["node", str(entry), "page", "exec", "--store-id", STORE_ID, "--script", js],
        capture_output=True, text=True, encoding="utf-8",
    )
    raw = proc.stdout
    data = json.loads(raw)
    inner = data["data"]["data"]
    if inner.get("exceptionDetails"):
        print("EXCEPTION:", json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:800])
        return 2
    records = json.loads(inner["result"])
    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"records: {len(records)}  ->  {OUT_PATH}")
    print(f"SKC 去重: {len({r['skc_id'] for r in records})}")
    print(f"货号去重: {len({r['sku_code'] for r in records})}")
    print(f"缺货号  : {sum(1 for r in records if not r['sku_code'])}")
    print(f"有活动价: {sum(1 for r in records if r['activity_price'] != '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())