# -*- coding: utf-8 -*-
"""run_temu_fulfill.py — TEMU 发货**整链**（拆仓 → 买面单 → 两个仓各自发货 → 批量确认发货）。

为什么单独一个入口（2026-11-06 用户令「把这个发货流程封装成 skill 并 pipeline 化」）：
    这一段履约横跨**两个平台**（TEMU 商家中心 + 帕沃尔海外仓/领星 OMS），
    单看任何一个脚本都拼不出「42 单拆两仓 → 分别买面单 → 各自发货 → 回 TEMU 确认」这条链，
    而顺序又是固定的。故按 ai-python-methodology「流水线定义规定」D1：
    **顺序放数据（`data/temu_fulfill_pipeline.json`），怎么跑放代码（本文件）。**

链条（详见 data/temu_fulfill_pipeline.json）：
    split     拆仓（帕沃尔优先；恒信兜底；缺货单出 Excel 交人工）
    label_pw  帕沃尔那份：TEMU 买面单（按仓分两批：加州3仓 / kansas仓）
    label_hx  恒信那份：TEMU 买面单（恒信加州3仓 + 物流商 7 家含燕文）
    pw_receive 帕沃尔 OMS：待确认 → 确认订单（审核）
    pw_track   帕沃尔 OMS：未出跟踪号 → 批量获取单号
    hx_ship    恒信领星 OMS：分配仓库&物流 → 确定并审核 → 「处理中」复核
    temu_confirm TEMU：待发货 → 精确勾选有运单号的单 → 批量确认发货（抽屉 → 确认发货 N → 二级确认）

写操作闸门：`--confirm` 才真跑写操作；不给只做只读步骤（split/screen/verify）。

用法：
    python scripts/power/run_temu_fulfill.py --store BEL --file "<导出订单.xlsx>" --confirm
    python scripts/power/run_temu_fulfill.py --store BEL --file ... --from label_pw --to pw_track --confirm
    python scripts/power/run_temu_fulfill.py --store BEL --file ... --only split
    python scripts/power/run_temu_fulfill.py --store BEL --file ... --reuse-page online-shipping.html
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

DEFAULT_STEPS = os.path.join(_HERE, "data", "temu_fulfill_pipeline.json")
DEFAULT_CFG = os.path.join(_HERE, "data", "temu_buylabel.json")

#: 需要**真写**（改平台状态 / 花钱）的步骤 → 没有 --confirm 一律跳过
WRITE_STEPS = ("label_pw", "label_hx", "pw_receive", "pw_track", "hx_ship", "temu_confirm")


# ---------------------------------------------------------------- 纯函数（可单测）

def select_steps(steps: list, only: str = "", frm: str = "", to: str = "") -> list:
    """按 `--only` / `--from` / `--to` 截取本次要跑的步骤（纯函数）。

    规则：
      · `only` 非空 → 只要这些（逗号分隔），顺序仍按定义走
      · `from` 指定起点（含），`to` 指定终点（含）；两者都空＝全跑
      · 名字不存在 → 抛错（**不静默跳过**，否则会"跑完了但其实没跑"）
    """
    names = [str(s.get("name") or "") for s in (steps or [])]
    if only:
        want = [x.strip() for x in only.split(",") if x.strip()]
        bad = [w for w in want if w not in names]
        if bad:
            raise ValueError("--only 里有不存在的步骤：%s（可选项 %s）"
                             % (", ".join(bad), ", ".join(names)))
        return [s for s in steps if s.get("name") in want]
    i0, i1 = 0, len(names) - 1
    if frm:
        if frm not in names:
            raise ValueError("--from 步骤不存在：%s（可选项 %s）" % (frm, ", ".join(names)))
        i0 = names.index(frm)
    if to:
        if to not in names:
            raise ValueError("--to 步骤不存在：%s（可选项 %s）" % (to, ", ".join(names)))
        i1 = names.index(to)
    if i0 > i1:
        raise ValueError("--from(%s) 在 --to(%s) 之后，顺序反了" % (frm, to))
    return list(steps[i0:i1 + 1])


def gate_write(step_name: str, confirm: bool) -> str:
    """写操作闸门（纯函数）：返回 `"run"` / `"skip"`；不通过就跳过而不是假装跑了。"""
    if step_name in WRITE_STEPS and not confirm:
        return "skip"
    return "run"


def summarize(runs: list) -> dict:
    """把每步执行结果汇总成一份可判定的总账（纯函数）。"""
    out = {"total": len(runs), "ran": [], "skipped": [], "failed": [],
           "ok": True, "stopped_at": ""}
    for r in runs or []:
        n = r.get("name") or ""
        if r.get("status") == "ok":
            out["ran"].append(n)
        elif r.get("status") == "skip":
            out["skipped"].append(n)
        else:
            out["failed"].append(n)
            out["ok"] = False
            out["stopped_at"] = out["stopped_at"] or n
    return out


# ---------------------------------------------------------------- 各步实现

def _run_py(args: list, log=print, timeout: int = 3600) -> tuple[int, str]:
    """起子进程跑另一个脚本（每个平台一套会话，子进程隔离更稳）。"""
    cmd = [sys.executable] + args
    log("   $ %s" % " ".join(os.path.basename(a) if i == 0 else a
                             for i, a in enumerate(args)))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=_WS, env=env)
        tail = (p.stdout or "")[-1500:]
        if p.returncode != 0:
            tail += "\n[stderr]\n" + (p.stderr or "")[-800:]
        return p.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, "超时（%ds）" % timeout


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _split_file(store: str, xlsx: str, out_json: str, log=print) -> dict:
    """拆仓（复用 bel_split_by_stock.py，纯读 + 落 json）。"""
    rc, tail = _run_py([os.path.join(_HERE, "bel_split_by_stock.py"),
                        "--file", xlsx, "--out", out_json], log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("拆仓失败（rc=%s）：%s" % (rc, tail[-400:]))
    d = _read_json(out_json)
    counts = d.get("counts") or {}
    log("   拆仓结果：帕沃尔 %s / 恒信 %s / 缺货 %s"
        % (counts.get("PW"), counts.get("HX"), counts.get("MISS")))
    return d


def _write_group_txt(split: dict, out_dir: str) -> dict:
    """把三组单号写成 txt（后续步骤按组喂）。返回 {组名: 文件路径}。"""
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    for key, fn in (("PW", "pw.txt"), ("HX", "hx.txt"), ("MISS", "miss.txt")):
        rows = ((split.get("groups") or {}).get(key)) or []
        p = os.path.join(out_dir, fn)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(r["ord"] for r in rows) + ("\n" if rows else ""))
        files[key] = p
    # 帕沃尔再按仓拆（加州3仓 / kansas仓），因为买面单要分两批
    for wh, fn in (("加州3仓", "pw_jiazhou3.txt"), ("kansas仓", "pw_kansas.txt")):
        rows = [r for r in ((split.get("groups") or {}).get("PW") or [])
                if str(r.get("wh") or "") == wh]
        p = os.path.join(out_dir, fn)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(r["ord"] for r in rows) + ("\n" if rows else ""))
        files[wh] = p
    return files


def _ords(path: str) -> list:
    try:
        return [x.strip() for x in open(path, encoding="utf-8") if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def step_split(ctx: dict, log=print) -> dict:
    split = _split_file(ctx["store"], ctx["file"], ctx["split_json"], log)
    files = _write_group_txt(split, ctx["out_dir"])
    miss = len(((split.get("groups") or {}).get("MISS") or []))
    if miss:
        log("   ⚠ 缺货 %d 单 → 已单独记账，需人工（可跑 gen_pw_manual_xlsx.py 出表）" % miss)
    return {"split": split.get("counts"), "files": files, "miss": miss}


def step_label_pw(ctx: dict, log=print) -> dict:
    """帕沃尔那份：按仓分两批买面单（加州3仓不选燕文 / kansas 同样不选燕文）。"""
    runners = [("加州3仓", ctx["files"].get("加州3仓")), ("kansas仓", ctx["files"].get("kansas仓"))]
    done = {}
    for wh, f in runners:
        ords = _ords(f) if f else []
        if not ords:
            log("   [%s] 无单，跳过" % wh)
            done[wh] = 0
            continue
        log("   [%s] %d 单" % (wh, len(ords)))
        common = ["--store", ctx["store"], "--orders", " ".join(ords),
                  "--warehouse", wh]
        for action in ("fill_dims", "fill_logistics", "place_only"):
            args = [os.path.join(_HERE, "run_temu_buylabel.py"), "--action", action] + common
            if action == "fill_logistics":
                args += ["--carriers", ctx["pw_carriers"]]
            if action in ("fill_logistics", "place_only") and ctx.get("reuse_page"):
                args += ["--reuse-page", ctx["reuse_page"]]
            if action == "place_only":
                args += ["--confirm"]
            rc, tail = _run_py(args, log=log, timeout=2400)
            if rc != 0:
                raise RuntimeError("[%s] %s 失败（rc=%s）：%s" % (wh, action, rc, tail[-500:]))
        done[wh] = len(ords)
    return {"by_warehouse": done}


def step_label_hx(ctx: dict, log=print) -> dict:
    """恒信那份：在 TEMU 页买面单（仓=恒信加州3仓，物流商 7 家含燕文）。"""
    ords = _ords(ctx["files"].get("HX"))
    if not ords:
        log("   恒信 0 单，跳过")
        return {"hx": 0}
    common = ["--store", ctx["store"], "--orders", " ".join(ords),
              "--warehouse", ctx["hx_warehouse"]]
    for action in ("fill_dims", "fill_logistics", "place_only"):
        args = [os.path.join(_HERE, "run_temu_buylabel.py"), "--action", action] + common
        if action == "fill_logistics":
            args += ["--carriers", ctx["hx_carriers"]]
        if action in ("fill_logistics", "place_only") and ctx.get("reuse_page"):
            args += ["--reuse-page", ctx["reuse_page"]]
        if action == "place_only":
            args += ["--confirm"]
        rc, tail = _run_py(args, log=log, timeout=2400)
        if rc != 0:
            raise RuntimeError("恒信 %s 失败（rc=%s）：%s" % (action, rc, tail[-500:]))
    return {"hx": len(ords)}


def step_pw_receive(ctx: dict, log=print) -> dict:
    rc, tail = _run_py([os.path.join(_HERE, "pw_deliver_ship.py"),
                        "--shop", ctx["store"], "--stage", "confirm"], log=log, timeout=1800)
    log("   " + (tail or "").strip().splitlines()[-1][:200] if tail else "")
    if rc != 0:
        raise RuntimeError("帕沃尔确认订单失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"pw_receive": "ok"}


def step_pw_track(ctx: dict, log=print) -> dict:
    rc, tail = _run_py([os.path.join(_HERE, "pw_deliver_ship.py"),
                        "--shop", ctx["store"], "--stage", "track"], log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("帕沃尔获取单号失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"pw_track": "ok"}


def step_hx_ship(ctx: dict, log=print) -> dict:
    """恒信那份 → 领星 OMS 发货（分配仓库&物流 + 审核 + 「处理中」复核）。"""
    ords = _ords(ctx["files"].get("HX"))
    if not ords:
        log("   恒信 0 单，跳过")
        return {"hx_ship": 0}
    rc, tail = _run_py([os.path.join(_HERE, "oms_ship_one.py"),
                        "--orders", " ".join(ords), "--commit"],
                       log=log, timeout=3600)
    if rc != 0:
        raise RuntimeError("恒信 OMS 发货失败（rc=%s）：%s" % (rc, (tail or "")[-500:]))
    return {"hx_ship": len(ords)}


def step_temu_confirm(ctx: dict, log=print) -> dict:
    """TEMU 待发货 → 精确勾选有运单号的单 → 批量确认发货（三段式）。"""
    scan = os.path.join(_HERE, "temu_scan_confirmable.py")
    if not os.path.exists(scan):
        log("   ⚠ 缺少 temu_scan_confirmable.py（扫描可确认单），跳过")
        return {"temu_confirm": "skip"}
    rc, tail = _run_py([scan, ctx["store"]], log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("扫描可确认单失败：%s" % (tail or "")[-300:])
    for script in ("temu_batch_confirm.py", "temu_confirm_drawer.py", "temu_confirm_final.py"):
        p = os.path.join(_HERE, script)
        if not os.path.exists(p):
            log("   ⚠ 缺少 %s，跳过该层" % script)
            continue
        args = [p, "--store", ctx["store"]]
        if script != "temu_confirm_final.py":
            args += ["--commit"]
        else:
            args += ["--commit"]
        rc, tail = _run_py(args, log=log, timeout=1800)
        if rc != 0:
            raise RuntimeError("%s 失败（rc=%s）：%s" % (script, rc, (tail or "")[-400:]))
        time.sleep(2)
    return {"temu_confirm": "ok"}


STEPS = {
    "split": step_split,
    "label_pw": step_label_pw,
    "label_hx": step_label_hx,
    "pw_receive": step_pw_receive,
    "pw_track": step_pw_track,
    "hx_ship": step_hx_ship,
    "temu_confirm": step_temu_confirm,
}


# ---------------------------------------------------------------- 主流程

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="TEMU 发货整链（拆仓→买面单→两仓发货→批量确认）")
    ap.add_argument("--store", required=True, help="店后缀（BEL / BHY）")
    ap.add_argument("--file", default="", help="TEMU 导出的订单 xlsx")
    ap.add_argument("--steps-config", default=DEFAULT_STEPS, help="步骤定义 json")
    ap.add_argument("--config", default=DEFAULT_CFG, help="买面单口径 json")
    ap.add_argument("--only", default="", help="只跑这些步（逗号分隔）")
    ap.add_argument("--from", dest="frm", default="", help="从这步开始")
    ap.add_argument("--to", dest="to", default="", help="到这步结束")
    ap.add_argument("--confirm", action="store_true", help="写操作闸门")
    ap.add_argument("--reuse-page", default="", help="跨进程复用批量页（URL 片段）")
    ap.add_argument("--out-dir", default="", help="产物目录（默认 out/<日期>/fulfill）")
    a = ap.parse_args()

    spec = _read_json(a.steps_config)
    steps = spec.get("steps") or []
    if not steps:
        print("[FAIL] %s 里没有 steps" % a.steps_config, file=sys.stderr)
        return 42
    try:
        picked = select_steps(steps, only=a.only, frm=a.frm, to=a.to)
    except ValueError as ex:
        print("[FAIL] %s" % ex, file=sys.stderr)
        return 42

    cfg = _read_json(a.config)
    lg = (cfg.get("logistics") or {})
    hx_carriers = ",".join((lg.get("carriers_default") or []) + (lg.get("carriers_hx_extra") or []))
    pw_carriers = ",".join(lg.get("carriers_default") or [])

    day = time.strftime("%Y-%m-%d")
    out_dir = a.out_dir or os.path.join(_HERE, "out", day, "fulfill")
    os.makedirs(out_dir, exist_ok=True)

    ctx = {"store": a.store, "file": a.file, "out_dir": out_dir,
           "split_json": os.path.join(out_dir, "split.json"),
           "files": {}, "reuse_page": a.reuse_page,
           "hx_warehouse": "恒信加州3仓",
           "hx_carriers": hx_carriers, "pw_carriers": pw_carriers}

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    log("[整链] %s，%d 步：%s" % (a.store, len(picked),
                                " → ".join(str(s.get("name")) for s in picked)))
    log("[口径] 恒信仓=%s｜恒信物流商=%s｜帕沃尔物流商=%s"
        % (ctx["hx_warehouse"], hx_carriers or "(页面默认)", pw_carriers or "(页面默认)"))
    if not a.confirm:
        log("[闸门] 未给 --confirm → 写操作步骤会被**跳过**（只做只读步骤）")

    runs = []
    for st in picked:
        name = str(st.get("name") or "")
        fn = STEPS.get(name)
        if not fn:
            log("[步骤] %s ✗ 没有实现（只能是 %s）" % (name, ", ".join(STEPS)))
            runs.append({"name": name, "status": "fail", "why": "no-impl"})
            break
        act = gate_write(name, a.confirm)
        if act == "skip":
            log("[步骤] %s ⊘ 跳过（写操作，缺 --confirm）" % name)
            runs.append({"name": name, "status": "skip"})
            continue
        t0 = time.time()
        log("[步骤] %s …" % name)
        try:
            out = fn(ctx, log=log) or {}
        except Exception as ex:  # noqa: BLE001
            dt = round(time.time() - t0, 1)
            log("[步骤] %s ✗ %.1fs %s" % (name, dt, str(ex)[:300]))
            runs.append({"name": name, "status": "fail", "why": str(ex)[:300],
                         "seconds": dt})
            break
        dt = round(time.time() - t0, 1)
        log("[步骤] %s ✅ %.1fs %s" % (name, dt,
                                     json.dumps(out, ensure_ascii=False)[:220]))
        runs.append({"name": name, "status": "ok", "out": out, "seconds": dt})

    s = summarize(runs)
    print("\n[RESULT] " + json.dumps({"store": a.store, "summary": s,
                                      "out_dir": out_dir}, ensure_ascii=False))
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"store": a.store, "steps": runs, "summary": s,
                   "logs": logs[-200:]}, f, ensure_ascii=False, indent=1)
    log("已存 %s" % os.path.join(out_dir, "run.json"))
    return 0 if s["ok"] else 42


if __name__ == "__main__":
    raise SystemExit(main())
