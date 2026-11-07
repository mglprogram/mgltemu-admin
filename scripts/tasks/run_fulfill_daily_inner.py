# -*- coding: utf-8 -*-
"""run_fulfill_daily_inner.py — 每日发货任务的**业务执行体**（被 dsh_run.py 调用）。

用户 2026-11-06 口径：
    「设一个每天 6:30 的发货任务，这个定时任务用 dsh 会话激活；
      激活的会话在**发货**这个工作区出现」
    追问后确认：**跑整链**（导出 → 拆仓 → 买面单 → 两仓发货 → 确认发货 → 出表），
    覆盖 **BHY + BEL 两个店**。

为什么这里只做「按店调度」而不自己实现业务：
    业务全在 `scripts/power/run_temu_fulfill.py`（11 步整链，已 pipeline 化）。
    本文件负责它不该管的三件事：
      ① **两个店串行**（同一扇紫鸟窗口，绝不能并行）；
      ② **先导出**（每天的单从 TEMU 导出来，文件名带时间戳，得现找）；
      ③ **失败隔离**（BEL 挂了不能让 BHY 也发不出去；反之亦然）。

店序固定 BEL → BHY：BEL 单少跑得快，先把它发掉；BHY 即使中途卡住，
BEL 的货已经出去了。两个店都跑完才返回总码。

退出码：
    0   两店都成功
    1   至少一个店失败（详见日志里每个店的 rc）
    5   窗口锁被占（由外层 run_with_window_lock.py 抛出，本文件不会产生）
    42  两店都失败
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time

# ★ 2026-11-08 修 BUG：本文件在 scripts\tasks\ 下，往上要退**三层**才是项目根
#   （scripts\tasks → scripts → TEMU）。原来只退了两层，PROJ 变成 ...\TEMU\scripts，
#   POWER 就成了 ...\scripts\scripts\power ⇒ 导出脚本 rc=2 "can't open file"，
#   结果是每天整链在第 1 步就挂（两店都 rc=2 → exit 42）。
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POWER = os.path.join(PROJ, "scripts", "power")

#: 跑店的顺序（**不许改成并行**：同一扇紫鸟窗口，双跑会互相读错列表）
STORE_ORDER = ("BEL", "BHY")

#: 紫鸟窗口名（导出脚本内部也用这张表）
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


def log(msg: str) -> None:
    print(msg, flush=True)


def _run(args: list, timeout: int = 10 * 3600) -> tuple[int, str]:
    """起子进程跑一个业务脚本，返回 (rc, 输出尾部)。"""
    cmd = [sys.executable] + args
    log("   $ %s" % " ".join(os.path.basename(a) if i == 0 else a
                             for i, a in enumerate(args))[:400])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.run(cmd, cwd=PROJ, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.returncode else "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "超时（%ds）" % timeout


def export_today(store: str) -> tuple[int, str]:
    """导出该店当前「待发货」全部订单 → 返回 (rc, xlsx 绝对路径)。

    为什么每天必须现导：整链的输入就是这张表（订单号 / SKU货号 / 应履约件数），
    而每天的单不一样。文件名是 `订单导出<yyyymmdd><序号>.xlsx`，序号每天从 001 起，
    所以**只能按修改时间**取最新的那个，不能猜文件名。
    """
    win_dir = os.path.join(os.environ.get("APPDATA", ""), "ziniaobrowserdatas",
                           "ziniao browser", WINDOWS.get(store, store))
    before = {}
    if os.path.isdir(win_dir):
        for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
            try:
                before[f] = os.path.getmtime(f)
            except OSError:
                pass

    rc, out = _run([os.path.join(POWER, "temu_export_by_select.py"),
                    "--store", store], timeout=1800)
    if rc != 0:
        # 2026-11-08 诊断加固：以前失败时把子进程输出整个丢掉，日志只剩 rc=2，
        # 分不清是「窗口没开」还是「解释器打不开脚本」。现在把尾巴也写进日志。
        log("   ⚠ py=%s rc=%s 输出尾部：" % (sys.executable, rc))
        for line in (out or "").strip().splitlines()[-8:]:
            log("      " + line[:300])
        if not (out or "").strip():
            log("      （子进程没有任何输出）")
        return rc, ""

    # 导出脚本自己会打印 [RESULT]{"file": "..."}，优先用它给的确切路径
    newest = ""
    for line in reversed(out.splitlines()):
        if "[RESULT]" in line:
            try:
                newest = str(json.loads(line.split("[RESULT]", 1)[1]).get("file") or "")
            except Exception:  # noqa: BLE001
                pass
            break
    if not newest or not os.path.isfile(newest):
        # 兜底：扫下载目录里「点击之后新增/更新」的最新 xlsx
        cands = []
        if os.path.isdir(win_dir):
            for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if before.get(f) != mt:
                    cands.append((mt, f))
        if not cands:
            return 42, ""
        newest = max(cands)[1]
    return 0, newest


def run_store(store: str) -> dict:
    """一个店：导出 → 整链（--confirm）。返回该店的结论。"""
    log("")
    log("=" * 70)
    log("[%s] 开始" % store)
    log("=" * 70)

    t0 = time.time()
    rc_e, xlsx = export_today(store)
    if rc_e != 0 or not xlsx:
        log("[%s] ✗ 导出失败（rc=%s）→ 本店跳过" % (store, rc_e))
        return {"store": store, "ok": False, "stage": "export",
                "rc": rc_e or 42, "seconds": round(time.time() - t0, 1)}
    log("[%s] 导出：%s" % (store, os.path.basename(xlsx)))

    rc, out = _run([os.path.join(POWER, "run_temu_fulfill.py"),
                    "--store", store, "--file", xlsx, "--confirm"], timeout=8 * 3600)
    tail = [x for x in out.strip().splitlines() if x.strip()][-6:]
    for line in tail:
        log("   " + line[:300])

    ok = rc == 0
    log("[%s] %s rc=%s %.1f 分钟" % (store, "✅" if ok else "✗",
                                     rc, (time.time() - t0) / 60))
    return {"store": store, "ok": ok, "stage": "fulfill", "rc": rc,
            "xlsx": os.path.basename(xlsx),
            "seconds": round(time.time() - t0, 1)}


def push_reports() -> dict:
    """收尾：把两个店的「缺货/失败订单」表推 TEMU 群 + 传云盘。

    用户 2026-11-07：「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群
    和云盘把两个表格」。
    实现在 `push_fulfill_report.py`（它自己找表、上传、推群、永不抛异常）。
    """
    log("")
    log("[push] 推 TEMU 群 + 上传云盘…")
    rc, out = _run([os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "push_fulfill_report.py")], timeout=900)
    lines = [x for x in (out or "").strip().splitlines() if x.strip()]
    for ln in lines[-8:]:
        log("   " + ln[:220])
    res = {"rc": rc}
    for ln in reversed(lines):
        if "[RESULT]" in ln:
            try:
                res.update(json.loads(ln.split("[RESULT]", 1)[1]))
            except Exception:  # noqa: BLE001
                pass
            break
    return res


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    log("[fulfill-daily] %s 开始，店序 %s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                             " → ".join(STORE_ORDER)))
    results = []
    for store in STORE_ORDER:
        try:
            results.append(run_store(store))
        except Exception as ex:  # noqa: BLE001
            # 一个店炸了不能拖死另一个店（用户口径：两店都要发）
            log("[%s] ✗ 异常：%s" % (store, str(ex)[:200]))
            results.append({"store": store, "ok": False, "stage": "exception",
                            "rc": 1, "why": str(ex)[:200]})

    log("")
    log("[fulfill-daily] 汇总：")
    for r in results:
        log("   %-4s %s rc=%s %s"
            % (r["store"], "✅" if r.get("ok") else "✗", r.get("rc"),
               ("%s 分钟" % round((r.get("seconds") or 0) / 60, 1))))
    bad = [r for r in results if not r.get("ok")]

    # ★ 2026-11-07 用户口径：「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群
    #   和云盘把两个表格」⇒ 收尾必须推群 + 传云盘。
    #   ⚠ 放在「算完 bad」之后、return 之前：即使有一店失败，另一店的表也要推出去
    #   （用户要的是「今天发了什么」，不是「只有全成功才通知」）。
    #   ⚠ 推送/上传失败**不改本任务的退出码**：发货已经做完了，
    #   通知不到位只该在日志里报出来，不该把成功的业务判成失败。
    push_result = {}
    try:
        push_result = push_reports()
    except Exception as ex:  # noqa: BLE001
        log("[push] ✗ 收尾推送异常（不影响发货结论）：%s" % str(ex)[:200])
        push_result = {"ok": False, "why": str(ex)[:200]}

    log("\n[RESULT] " + json.dumps({"results": results, "bad": len(bad),
                                   "push": push_result}, ensure_ascii=False))
    if not bad:
        return 0
    return 42 if len(bad) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
