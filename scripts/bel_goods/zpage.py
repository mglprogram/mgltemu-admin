# -*- coding: utf-8 -*-
"""page exec 的薄封装：在指定店铺页面执行本地 JS 文件，返回解析后的结果。

ziniao-cli 走 node 入口脚本（run.js），多行 JS 以 argv 传入不会丢换行。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"


def page_exec(js_file: str | Path, store_id: str, parse_json: bool = False,
              timeout: int = 180):
    """执行 JS 文件；parse_json=True 时把返回字符串再按 JSON 解析。"""
    js = Path(js_file).read_text(encoding="utf-8").strip()
    proc = subprocess.run(
        ["node", str(_ENTRY), "page", "exec", "--store-id", store_id, "--script", js],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )
    if not proc.stdout.strip():
        raise RuntimeError(f"无输出: rc={proc.returncode} stderr={proc.stderr[:400]}")
    data = json.loads(proc.stdout)
    inner = data["data"]["data"]
    exc = inner.get("exceptionDetails")
    if exc:
        raise RuntimeError("页面异常: " + json.dumps(exc, ensure_ascii=False)[:600])
    result = inner["result"]
    return json.loads(result) if parse_json else result


def main():
    import sys
    store_id = os.environ.get("BEL_STORE_ID", "27677489075954")
    js_file = sys.argv[1]
    as_json = "--json" in sys.argv
    out = page_exec(js_file, store_id, parse_json=as_json)
    print(json.dumps(out, ensure_ascii=False, indent=1) if as_json else out)


if __name__ == "__main__":
    main()