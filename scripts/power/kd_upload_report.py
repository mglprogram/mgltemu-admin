# -*- coding: utf-8 -*-
"""失败订单自动上传金山云盘「失败订单」文件夹（用户 2026-11-25 要求）。

之前这一步不存在 —— 6 个 `*_缺货与失败订单_<日期>.xlsx` 只留在本地
`out/<日期>/`，用户得手动传。现在接进 `step_report` 末尾自动传。

目标：金山云盘 `/失败订单/`
      （folder_id = 8yQgY1R4Q9MccNzrEbHWxxMoB2QDffeUr，2026-11-25 实测）

去重：云盘里同名文件已存在 ⇒ 用 `upload-replace-file` 覆盖（同一天重跑不会出 (1)(2)）。

开关：`TEMU_UPLOAD_REPORT_KDOCS`（默认 1，设 0 关闭）
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

CLI = r"C:\Users\saing\AppData\Local\kdocs-cli\kdocs-cli.exe"
#: 金山云盘「失败订单」文件夹（用户 2026-11-25 指定）
FOLDER_ID = "8yQgY1R4Q9MccNzrEbHWxxMoB2QDffeUr"


def _run(args, timeout=180):
    p = subprocess.run([CLI] + args + ["--compact"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    raw = (p.stdout or "").strip()
    i = raw.find("{")
    if i < 0:
        return {"raw": raw[:200], "_stderr": (p.stderr or "")[:200]}
    try:
        return json.JSONDecoder().raw_decode(raw[i:])[0]
    except Exception:  # noqa: BLE001
        return {"raw": raw[:200]}


def list_folder() -> dict:
    """`{文件名: file_id}`。"""
    d = _run(["drive", "list-files", "parent_id=%s" % FOLDER_ID])
    out = {}
    for it in ((d.get("data") or {}).get("items") or []):
        f = it.get("file") or it
        n = f.get("name")
        if n:
            out[n] = f.get("id")
    return out


def upload(path: str, log=print) -> bool:
    """上传单个 xlsx：同名已存在就覆盖，否则新建。"""
    if not os.path.exists(path):
        log("   [金山] %s 不存在，跳过" % os.path.basename(path))
        return False
    name = os.path.basename(path)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    exist = list_folder()
    # ★ kdocs `upload-new-file` 要 `name`（带后缀），不是 `file_name`（实测 400001）
    body = {"name": name, "content_base64": b64}
    if name in exist:
        body["file_id"] = exist[name]
        body.pop("name", None)      # replace 用 file_id 定位
        r = _run(["drive", "upload-replace-file", "--file", _write_tmp(body)])
        act = "覆盖"
    else:
        body["parent_id"] = FOLDER_ID
        r = _run(["drive", "upload-new-file", "--file", _write_tmp(body)])
        act = "新建"
    ok = r.get("code") == 0
    log("   [金山] %s %s → %s" % (act, name, "成功" if ok else json.dumps(r, ensure_ascii=False)[:120]))
    return ok


def _write_tmp(body: dict) -> str:
    p = r"D:\AI-workplace\TEMU\_logs\_kd_upload.json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:      # UTF-8 无 BOM
        json.dump(body, f, ensure_ascii=False)
    return p


def upload_all(paths: list, log=print) -> dict:
    ok, fail = 0, []
    for p in paths:
        try:
            if upload(p, log=log):
                ok += 1
            else:
                fail.append(os.path.basename(p))
        except Exception as ex:  # noqa: BLE001
            fail.append("%s(%s)" % (os.path.basename(p), str(ex)[:40]))
    return {"ok": ok, "fail": fail}


if __name__ == "__main__":
    fs = sys.argv[1:]
    print(upload_all(fs))
