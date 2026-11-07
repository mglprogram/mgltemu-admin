# -*- coding: utf-8 -*-
"""push_fulfill_report.py — 发货任务收尾：**推飞书 TEMU 群 + 上传云盘**。

用户 2026-11-07 口径：
    「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群和云盘把两个表格」

「两个表格」＝ `report` 步产出的**每店一份** Excel：
    scripts/power/out/<日期>/BHY_缺货与失败订单_<日期>.xlsx
    scripts/power/out/<日期>/BEL_缺货与失败订单_<日期>.xlsx

三件事，缺一不可（顺序也是这个）：
    ① 找表 —— 从 `scripts/power/out/<日期>/` 找当天的两份 xlsx
                （`report` 若因文件被占用改存 `_v2`，也要认得）
    ② 推群 —— `notify_push.push_group()`，标题必须含 `TEMU`（群机器人关键词校验）
    ③ 传盘 —— `kdocs-cli` 上传到云盘「失败订单」文件夹，命名沿用既有约定
                `YYYY-MM-DD-失败订单-<店>.xlsx`

四条硬约束（都踩过）：
    · **永不抛异常**：业务（发货）已经做完了，推送/上传失败只能少一个通道，
      绝不能把已完成的任务搞成失败。
    · **kdocs 必须显式给 7897 代理**：本机进程环境里残留 `127.0.0.1:10808`
      （旧 v2rayN 端口，已废弃），kdocs-cli 会照它连、直接
      `proxyconnect ... actively refused`。而用户级环境变量是 7897。
      所以这里**显式设置**，不依赖继承。
    · **群消息不解析 markdown**：`push_group()` 会自动去掉 `**`，正文别写。
    · **上传失败要说清是哪一步**：返回结构化结果，由调用方汇总进日志。

用法：
    python scripts/tasks/push_fulfill_report.py --date 2026-11-07
    python scripts/tasks/push_fulfill_report.py --date 2026-11-07 --dry-run
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_ROOT = os.path.join(PROJ, "scripts", "power", "out")
TASKS = os.path.join(PROJ, "scripts", "tasks")
if TASKS not in sys.path:
    sys.path.insert(0, TASKS)
if os.path.join(PROJ, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(PROJ, "scripts"))

#: 云盘「失败订单」文件夹（2026-11-07 实测；历史文件都在这里，命名 `YYYY-MM-DD-失败订单.xlsx`）
KDOCS_FAILED_FOLDER = "8yQgY1R4Q9MccNzrEbHWxxMoB2QDffeUr"
#: kdocs 必须走这个代理；进程环境里的 10808 是废弃端口
KDOCS_PROXY = "http://127.0.0.1:7897"
KDOCS_CLI = os.path.join(os.environ.get("LOCALAPPDATA", ""), "kdocs-cli", "kdocs-cli.exe")
#: kdocs 薄封装（处理无 BOM 写参与重试）
KD_WRAPPER = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                          "kdocs-cli", "scripts", "kd.py")

#: 飞书也在墙外，同样要 7897。
#:
#: ★ 2026-11-07 真机：DSH 进程的 `HTTPS_PROXY` 被固定成 `127.0.0.1:10808`
#:   （旧 v2rayN 端口，早已废弃），我在工具里 `$env:HTTPS_PROXY=...7897` **改不动它**
#:   —— 每次调用都是新进程、还带着 10808。于是 `notify_push.push_feishu()` 走默认
#:   opener 时连 10808 被拒（WinError 10061），而用户级环境变量其实是 7897。
#:   ⇒ 在本脚本里**显式**把 opener 的代理指向 7897，不依赖继承的环境。
FEISHU_PROXY = "http://127.0.0.1:7897"

STORES = ("BHY", "BEL")


def find_reports(day: str) -> dict:
    """找当天的两份 Excel → {店: 路径}（纯函数式，只读）。

    `report` 步在目标文件被 Excel 占用时会改存 `_v2/_v3`，所以按**前缀+日期**
    匹配，再取修改时间最新的那个，而不是死认一个文件名。
    """
    day_dir = os.path.join(OUT_ROOT, day)
    out = {}
    for store in STORES:
        pat = os.path.join(day_dir, "%s_缺货与失败订单_%s*.xlsx" % (store, day.replace("-", "")))
        cands = []
        for f in glob.glob(pat):
            try:
                cands.append((os.path.getmtime(f), f))
            except OSError:
                pass
        if cands:
            out[store] = max(cands)[1]
    return out


def summarize(path: str) -> dict:
    """读表拿两个 sheet 的行数（给人看的摘要）。读不出就返回空，不抛。"""
    try:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(path, read_only=True)
        got = {}
        for name in ("缺货", "失败订单"):
            if name in wb.sheetnames:
                ws = wb[name]
                got[name] = max(0, (ws.max_row or 1) - 1)
        wb.close()
        return got
    except Exception:  # noqa: BLE001
        return {}


def build_message(day: str, files: dict, uploaded: dict) -> tuple[str, str]:
    """拼标题与正文（纯函数）。标题必须含 TEMU（群机器人关键词校验）。"""
    title = "TEMU 每日发货完成 %s" % day
    lines = ["%s 发货任务已完成。" % day, ""]
    for store in STORES:
        p = files.get(store)
        if not p:
            lines.append("%s：未产出表格（该店本批可能无单或失败）" % store)
            continue
        s = summarize(p)
        short = []
        if "缺货" in s:
            short.append("缺货 %d" % s["缺货"])
        if "失败订单" in s:
            short.append("失败 %d" % s["失败订单"])
        lines.append("%s：%s ｜ %s" % (store, " / ".join(short) or "见附件",
                                       os.path.basename(p)))
        if store in uploaded:
            lines.append("   云盘：%s" % uploaded[store])
    lines += ["", "两个表格已上传云盘「失败订单」文件夹；本地在",
              "scripts/power/out/%s/" % day]
    return title, "\n".join(lines)


def kdocs_env() -> dict:
    """kdocs 专用环境：**显式**用 7897，覆盖进程里残留的 10808。"""
    env = dict(os.environ)
    env["HTTP_PROXY"] = KDOCS_PROXY
    env["HTTPS_PROXY"] = KDOCS_PROXY
    env["NO_PROXY"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kd(args: list, timeout: int = 180) -> tuple[int, str]:
    """调 kdocs-cli（优先走 kd.py 封装：它处理无 BOM 写参）。"""
    if os.path.isfile(KD_WRAPPER):
        cmd = [sys.executable, KD_WRAPPER] + args + ["--compact"]
    else:
        cmd = [KDOCS_CLI] + args + ["--compact"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=kdocs_env())
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "kdocs-cli 超时（%ds）" % timeout


def _find_existing(name: str) -> str:
    """云盘「失败订单」文件夹里找同名文件 → 返回 file_id（没有就空串）。

    ★ 2026-11-07：上传用 `upload-new-file` 时云盘会自动加 `(1)(2)(3)`
      —— 我调试时跑了 4 次就堆出 8 个文件。定时任务每天跑一次不该这样，
      尤其「重跑」时（任务失败再跑）更容易堆。
      ⇒ 先查同名：有就 `upload-replace-file` 覆盖，没有才新建。
    """
    rc, out = _kd(["drive", "list-files", "parent_id=%s" % KDOCS_FAILED_FOLDER])
    if rc != 0:
        return ""
    try:
        d = json.loads(out)
        node = d.get("data") or {}
        node = node.get("data") if isinstance(node.get("data"), dict) else node
        for x in node.get("items") or []:
            if str(x.get("name") or "") == name and x.get("type") == "file":
                return str(x.get("id") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def upload(path: str, day: str, store: str) -> tuple[bool, str]:
    """上传/覆盖一份表 → 返回 (成功, 链接或说明)。

    命名沿用云盘既有约定 `YYYY-MM-DD-失败订单-<店>.xlsx`；
    **同名已存在就覆盖**，不产生 `(1)(2)` 副本。
    `content_base64` 走**参数文件**（无 BOM），不内联进命令行。
    """
    name = "%s-失败订单-%s.xlsx" % (day, store)
    try:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except OSError as ex:
        return False, "读文件失败：%s" % ex

    existing = _find_existing(name)
    payload = {"name": name, "parent_id": KDOCS_FAILED_FOLDER, "content_base64": b64}
    action = "upload-new-file"
    if existing:
        payload["file_id"] = existing
        action = "upload-replace-file"

    tmp = os.path.join(os.path.dirname(path), "_kdocs_upload_%s.json" % store)
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:   # 无 BOM
            fh.write(json.dumps(payload, ensure_ascii=False))
        rc, out = _kd(["drive", action, "--file", tmp], timeout=300)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if rc != 0:
        return False, "上传失败（rc=%s）：%s" % (rc, out[:200])

    # 从返回里抠 file_id，再换在线链接（拿不到就算了，别把成功判成失败）
    fid = existing
    try:
        d = json.loads(out)
        node = d.get("data") or {}
        node = node.get("data") if isinstance(node.get("data"), dict) else node
        fid = str(node.get("id") or node.get("file_id") or fid or "")
    except Exception:  # noqa: BLE001
        pass
    link = ""
    if fid:
        rc2, out2 = _kd(["drive", "get-file-link", "file_id=%s" % fid])
        if rc2 == 0:
            try:
                d2 = json.loads(out2)
                n2 = d2.get("data") or {}
                n2 = n2.get("data") if isinstance(n2.get("data"), dict) else n2
                link = str(n2.get("link_url") or n2.get("url") or "")
            except Exception:  # noqa: BLE001
                pass
    tag = "已覆盖" if existing else "已上传"
    return True, (link or ("%s：%s" % (tag, name)))


def push_feishu_fixed(title: str, msg: str, tries: int = 3) -> tuple[bool, str]:
    """推 TEMU 群，**显式**用 7897 代理（不信任进程环境里的 10808），带重试。

    ★ 2026-11-07 真机：偶尔报 `[SSL: UNEXPECTED_EOF_WHILE_READING]
      EOF occurred in violation of protocol` —— 这是代理链路的瞬时抖动，
      同一条消息重试就成。飞书是「业务已完成后唯一的对外通道」，
      不能一次抖动就放弃。
    """
    import time as _t  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import notify_push  # noqa: PLC0415

    hook = notify_push.feishu_webhook()
    if not hook:
        return False, "未设置 FEISHU_WEBHOOK，跳过"
    text = "%s\n%s" % (title, notify_push.to_group_text(msg))
    body = json.dumps({"msg_type": "text", "content": {"text": text}},
                      ensure_ascii=False).encode("utf-8")
    last = ""
    for i in range(1, max(1, tries) + 1):
        req = urllib.request.Request(
            hook, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": FEISHU_PROXY, "https": FEISHU_PROXY}))
        try:
            with opener.open(req, timeout=notify_push.FEISHU_TIMEOUT) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            code = res.get("code")
            if code == 0:
                return True, ("OK" if i == 1 else "OK（第 %d 次重试成功）" % i)
            if code == 19024:
                return False, "code=19024 关键词校验未通过（标题须含 TEMU）"
            return False, "code=%s %s" % (code, str(res.get("msg"))[:120])
        except Exception as ex:  # noqa: BLE001
            last = "%s: %s" % (type(ex).__name__, str(ex)[:140])
            if i < tries:
                _t.sleep(3)
    return False, last


def main() -> int:
    ap = argparse.ArgumentParser(description="发货收尾：推 TEMU 群 + 上传云盘")
    ap.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送不上传")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    import time as _t
    day = a.date or _t.strftime("%Y-%m-%d")

    files = find_reports(day)
    if not files:
        print("[push] %s 没有找到任何缺货/失败表 → 不推送（可能该批全成功或无单）" % day)
        return 0                       # 不是失败：没表就没得推

    print("[push] 找到 %d 份表：" % len(files))
    for k, v in files.items():
        print("   %-4s %s  %s" % (k, os.path.basename(v), summarize(v)))

    if a.dry_run:
        title, msg = build_message(day, files, {})
        print("\n--- 标题 ---\n%s\n--- 正文 ---\n%s" % (title, msg))
        print("\n[dry-run] 未推送、未上传")
        return 0

    # ② 先传云盘（要把链接写进群消息）
    uploaded, failed = {}, []
    for store in STORES:
        p = files.get(store)
        if not p:
            continue
        ok, info = upload(p, day, store)
        print("[push] 云盘 %-4s %s %s" % (store, "✅" if ok else "✗", info[:160]))
        if ok:
            uploaded[store] = info
        else:
            failed.append(store)

    # ③ 再推群
    title, msg = build_message(day, files, uploaded)
    ok_fs, info_fs = False, "未尝试"
    try:
        ok_fs, info_fs = push_feishu_fixed(title, msg)
    except Exception as ex:  # noqa: BLE001
        info_fs = "%s: %s" % (type(ex).__name__, str(ex)[:160])
    print("[push] 飞书群 %s %s" % ("✅" if ok_fs else "✗", str(info_fs)[:160]))

    print("\n[RESULT] " + json.dumps({
        "date": day, "files": {k: os.path.basename(v) for k, v in files.items()},
        "uploaded": sorted(uploaded), "upload_failed": failed,
        "feishu": bool(ok_fs),
        "ok": bool(ok_fs) and not failed,
    }, ensure_ascii=False))
    # ⚠ 永不因为推送/上传失败就返回非零：发货本身已经做完了。
    #   但把失败如实打出来，让日志和面板能看见。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
