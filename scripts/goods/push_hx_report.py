# -*- coding: utf-8 -*-
"""每跑完一轮库存同步：把结果表上传飞书云盘「temu更新库存」文件夹，并推 TEMU 库存预警群。

产出两件东西（每轮都新建，文件名带时间，不会互相覆盖）：
    1) 已修改清单   <日期>_<HHMM>_修改明细.xlsx   货号 / 尺码 / 仓库 / 改动前 / 改动后 / 差额
    2) 待确认清零   <日期>_<HHMM>_待清零.xlsx     货号 / 尺码 / SPU / 领星可用 / 帕沃尔可用

用法：
    python scripts/goods/push_hx_report.py --store BHY --apply     # --apply 才真上传+推送
    python scripts/goods/push_hx_report.py --all
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts"))

LOGS = ROOT / "_logs"
OUTDIR = LOGS / "hx-reports"
FOLDER_NAME = "temu更新库存"
STORE_PREFIX = {"BHY": "BHY-", "BEL": "BEL-", "KLS": "BK"}


# ---------------------------------------------------------------- 表格生成
def build_xlsx(rows_change: list, rows_zero: list, stamp: str) -> tuple[Path, Path]:
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Font  # noqa: PLC0415

    OUTDIR.mkdir(parents=True, exist_ok=True)
    p1 = OUTDIR / ("%s_修改明细.xlsx" % stamp)
    wb = Workbook()
    ws = wb.active
    ws.title = "修改明细"
    ws.append(["货号", "尺码", "仓库", "改动前", "改动后", "差额", "时间"])
    for r in rows_change:
        ws.append([r["code"], r["size"], r["wh"], r["before"], r["after"],
                   r["diff"], r.get("ts", "")])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFG", (24, 8, 14, 10, 10, 8, 20)):
        ws.column_dimensions[col].width = w
    wb.save(p1)

    p2 = OUTDIR / ("%s_待清零.xlsx" % stamp)
    wb2 = Workbook()
    w2 = wb2.active
    w2.title = "待确认清零"
    w2.append(["货号", "尺码", "SPU", "领星可用", "帕沃尔可用", "说明"])
    for r in rows_zero:
        w2.append([r.get("code"), r.get("size"), r.get("spu"),
                   r.get("lx"), r.get("pw"), "两边都为 0，待人工确认"])
    for c in w2[1]:
        c.font = Font(bold=True)
    w2.freeze_panes = "A2"
    for col, w in zip("ABCDEF", (24, 8, 16, 12, 14, 24)):
        w2.column_dimensions[col].width = w
    wb2.save(p2)
    return p1, p2


# ---------------------------------------------------------------- kdocs
def kd(*args: str) -> dict:
    """调 kdocs-cli。**大参数走临时文件**：Base64 内容塞不进命令行（WinError 206 命令过长）。

    约定：最后一个是 JSON 参数时，若超过 6000 字符就写成临时文件，用 @<path> 传入。
    """
    a = list(args)
    tmp = None
    if a and a[-1].startswith("{") and len(a[-1]) > 6000:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        Path(tmp).write_text(a[-1], encoding="utf-8")
        a[-1] = "@" + tmp
    try:
        p = subprocess.run(["kdocs-cli"] + a, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    raw = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {"raw": raw[:400], "rc": p.returncode}


def _fid(r: dict) -> str:
    """kdocs 返回是**扁平 JSON**（id 在顶层），也兼容包了一层 data 的情况。"""
    d = r.get("data") if isinstance(r.get("data"), dict) else r
    return str((d or {}).get("id") or (d or {}).get("fileId") or "")


def ensure_folder() -> str:
    """取/建云盘里的「temu更新库存」文件夹，返回 folder id。"""
    r = kd("drive", "find-folder-by-path", json.dumps({"path": "/" + FOLDER_NAME},
                                                      ensure_ascii=False))
    fid = _fid(r)
    if fid:
        return fid
    r = kd("drive", "create-folder", json.dumps(
        {"name": FOLDER_NAME, "on_name_conflict": "rename"}, ensure_ascii=False))
    return _fid(r)


def upload(path: Path, parent_id: str) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = {"name": path.name, "parent_id": parent_id, "content_base64": b64}
    return kd("drive", "upload-new-file", json.dumps(body))


# ---------------------------------------------------------------- 主流程
def load_ledger_rows(codes_prefix: tuple) -> list:
    """从台账还原「改动前/改动后」。台账里只存了差额，改动前 = currentStockAvailable。"""
    p = LOGS / "api_hx_ca3.jsonl"
    rows = []
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") != "write" or not r.get("ok"):
            continue
        code = str(r.get("code") or "")
        if not code.startswith(codes_prefix):
            continue
        for d in r.get("detail") or []:
            rows.append({"code": code, "size": d[0], "wh": d[1], "before": d[2],
                         "after": d[3], "diff": d[4], "ts": r.get("ts")})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="真上传云盘并推送（默认只生成文件）")
    a = ap.parse_args()
    stores = ["BHY", "BEL", "KLS"] if a.all else [a.store]

    changes, zeros = [], []
    for st in stores:
        changes += load_ledger_rows((STORE_PREFIX[st],))
        zp = LOGS / ("%s_zero_confirm.json" % st.lower())
        if zp.exists():
            zeros += json.loads(zp.read_text(encoding="utf-8"))

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    p1, p2 = build_xlsx(changes, zeros, stamp)
    print("修改明细 %d 行 → %s" % (len(changes), p1))
    print("待清零 %d 行 → %s" % (len(zeros), p2))
    if not a.apply:
        print("（未加 --apply：只生成文件，不上传不推送）")
        return 0

    fid = ensure_folder()
    print("云盘文件夹 id:", fid or "创建失败")
    up_ok = 0
    if fid:
        for p in (p1, p2):
            r = upload(p, fid)
            ok = bool(_fid(r))
            up_ok += 1 if ok else 0
            print("上传 %s → %s" % (p.name, "OK" if ok else str(r)[:120]))

    # 推送 TEMU 库存预警群
    try:
        import notify_push as NP  # noqa: PLC0415
        lines = ["TEMU 库存同步完成（%s）" % ", ".join(stores),
                 "时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "",
                 "已修改：%d 条（货号-尺码-仓库）" % len(changes),
                 "待确认清零：%d 条（恒信与加州3 两边都为 0）" % len(zeros),
                 "",
                 "表格已上传飞书云盘「%s」：" % FOLDER_NAME,
                 "· %s" % p1.name,
                 "· %s" % p2.name,
                 "",
                 "口径：恒信=⌈领星可用×80%⌉；加州3=⌈帕沃尔可用×80%⌉；Kansas=0"]
        ok, why = NP.push_group("TEMU 库存同步", "\n".join(lines))
        print("[推送] %s %s" % ("OK" if ok else "失败", why))
    except Exception as e:  # noqa: BLE001
        print("[推送] 异常：%s" % str(e)[:160])
    return 0 if up_ok == 2 else 5


if __name__ == "__main__":
    raise SystemExit(main())
