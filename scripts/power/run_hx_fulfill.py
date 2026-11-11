#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_hx_fulfill.py — 恒信（HX）领星 OMS 发货**独立流水线**。

用户 2026-11-11：「把 hx-lingxing-fulfill 这个 skill pipeline 化」。

步骤顺序的唯一定义在 `data/hx_fulfill_pipeline.json → steps`（8 步），本文件只负责
「怎么跑」。页面动作实现全部在 `hx_oms_ship.py`（skill 的可执行版），这里只做编排。

用法
----
    python scripts/power/run_hx_fulfill.py --pos-file <txt>                 # 只看计划（dry）
    python scripts/power/run_hx_fulfill.py --pos-file <txt> --confirm       # 真跑
    python scripts/power/run_hx_fulfill.py --split <split.json> --confirm   # 直接吃拆仓结果
    python scripts/power/run_hx_fulfill.py --pos A,B,C --only search,verify
    python scripts/power/run_hx_fulfill.py --pos-file <txt> --stage ship    # 整链调用用

退出码：0=全部有终态（处理中/已发货）；42=有失败或未查到的单；2=参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hx_oms_ship as D  # noqa: E402

SPEC_DEFAULT = os.path.join(HERE, "data", "hx_fulfill_pipeline.json")
#: 写操作（改平台状态），不给 --confirm 一律跳过
WRITE_STEPS = ("allocate", "fetch_track")

#: 整链（`run_temu_fulfill.step_hx_ship`）用的**段**映射：段名 → 本文件 steps 子序列。
#: 子序列顺序仍由 `data/hx_fulfill_pipeline.json` 决定；这里只是「取哪几段」。
STAGES = {
    "ship": ("open", "pending_tab", "page_size", "search", "select_all",
             "allocate", "fetch_track", "verify", "summary"),
    "verify": ("open", "verify", "summary"),
    "search": ("open", "pending_tab", "page_size", "search", "verify"),
}


def _read_json(p: str) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_pos(split: str, pos_file: str, pos: str) -> list:
    out = []
    if pos:
        out += [x.strip() for x in pos.replace("\n", ",").split(",") if x.strip()]
    if pos_file and os.path.exists(pos_file):
        with open(pos_file, encoding="utf-8") as f:
            out += [x.strip() for x in f if x.strip()]
    if split and os.path.exists(split):
        d = _read_json(split)
        for r in ((d.get("groups") or {}).get("HX") or []):
            o = str(r.get("ord") or "").strip()
            if o:
                out.append(o)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- 各步

def step_open(ctx: dict, log=print) -> dict:
    D.ensure_page(dry=ctx["dry"])
    return {"open": "ok"}


def step_pending_tab(ctx: dict, log=print) -> dict:
    return {"pending_tab": D.step_tab_pending()}


def step_page_size(ctx: dict, log=print) -> dict:
    D.step_page_size()
    return {"page_size": 200}


def step_search(ctx: dict, log=print) -> dict:
    n = D.step_search(ctx["pos"])
    ctx["found"] = n
    return {"search": n}


def step_select_all(ctx: dict, log=print) -> dict:
    D.step_select_all()
    return {"select_all": "ok"}


def step_allocate(ctx: dict, log=print) -> dict:
    r = D.step_allocate(dry=ctx["dry"])
    ctx["alloc"] = r
    return r


def step_fetch_track(ctx: dict, log=print) -> dict:
    r = D.step_wait_track(ctx["pos"])
    ctx["retry"] = r
    return r


def step_verify(ctx: dict, log=print) -> dict:
    rows = D.dump_rows(ctx["pos"])
    ok, bad = [], []
    for r in rows:
        po = str(r.get("platformOrderNo") or "").strip()
        st = str(r.get("status") or "").strip()
        if any(s in st for s in D.OK_STATUS):
            ok.append({"po": po, "status": st, "wh": r.get("sendWhName"),
                       "channel": r.get("logisticsChannelName"),
                       "track": r.get("trackNo")})
        else:
            bad.append({"po": po, "status": st,
                        "err": str(r.get("auditCause") or "")[:200]})
    found = {str(r.get("platformOrderNo") or "").strip() for r in rows}
    missing = [p for p in ctx["pos"] if p not in found]
    ctx["ok"], ctx["bad"], ctx["missing"] = ok, bad, missing
    path = os.path.join(ctx["out_dir"], "hx_oms_ship.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"store": "HX", "total": len(ctx["pos"]), "found": len(found),
                   "ok": ok, "fail": bad, "missing": missing,
                   "retry": ctx.get("retry") or {}}, f, ensure_ascii=False, indent=1)
    log("   回读：处理中/已发货 %d｜失败 %d｜未查到 %d"
        % (len(ok), len(bad), len(missing)))
    return {"ok": len(ok), "fail": len(bad), "missing": len(missing), "path": path}


def step_summary(ctx: dict, log=print) -> dict:
    ok = ctx.get("ok") or []
    bad = ctx.get("bad") or []
    miss = ctx.get("missing") or []
    c = {}
    for x in ok:
        c[x.get("status") or "?"] = c.get(x.get("status") or "?", 0) + 1
    log("   [汇总] 共 %d 单：成功 %s｜失败 %d｜未查到 %d"
        % (len(ctx["pos"]), c or "-", len(bad), len(miss)))
    for b in bad[:10]:
        log("     ✗ %-24s %-10s %s" % (b.get("po"), b.get("status"),
                                       str(b.get("err") or "")[:80]))
    for m in miss[:10]:
        log("     ? %-24s 未查到（可能不在待处理页签/尚未同步）" % m)
    res = {"total": len(ctx["pos"]), "ok": len(ok), "fail": len(bad),
           "missing": len(miss), "counts": c}
    ctx["result"] = res
    return res


STEPS = {
    "open": step_open,
    "pending_tab": step_pending_tab,
    "page_size": step_page_size,
    "search": step_search,
    "select_all": step_select_all,
    "allocate": step_allocate,
    "fetch_track": step_fetch_track,
    "verify": step_verify,
    "summary": step_summary,
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                    # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="恒信 领星OMS 发货独立流水线")
    ap.add_argument("--pos", default="", help="平台单号，逗号分隔")
    ap.add_argument("--pos-file", default="", help="单号 txt（一行一个）")
    ap.add_argument("--split", default="", help="拆仓 split.json（取 groups.HX）")
    ap.add_argument("--spec", default=SPEC_DEFAULT, help="步骤定义 json")
    ap.add_argument("--only", default="", help="只跑这些步（逗号分隔）")
    ap.add_argument("--frm", default="", help="从这步开始")
    ap.add_argument("--to", default="", help="到这步结束")
    ap.add_argument("--stage", default="", choices=("",) + tuple(STAGES),
                    help="整链调用用：只跑这一段")
    ap.add_argument("--confirm", action="store_true", help="写操作闸门")
    ap.add_argument("--dry", action="store_true", help="只出计划（写步骤一律跳过）")
    ap.add_argument("--out-dir", default="", help="产物目录")
    a = ap.parse_args()

    spec = _read_json(a.spec)
    steps = spec.get("steps") or []
    names = [str(s.get("name")) for s in steps]
    if a.stage:
        want = list(STAGES[a.stage])
        picked = [s for s in steps if str(s.get("name")) in want]
    elif a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        bad = [x for x in want if x not in names]
        if bad:
            print("[FAIL] 不存在的步骤：%s（可选 %s）" % (", ".join(bad), ", ".join(names)),
                  file=sys.stderr)
            return 2
        picked = [s for s in steps if str(s.get("name")) in want]
    else:
        i0 = names.index(a.frm) if a.frm in names else 0
        i1 = names.index(a.to) if a.to in names else len(steps) - 1
        if i0 > i1:
            print("[FAIL] --frm 在 --to 之后", file=sys.stderr)
            return 2
        picked = steps[i0:i1 + 1]

    pos = load_pos(a.split, a.pos_file, a.pos)
    if not pos:
        print("[FAIL] 没有单号：给 --pos / --pos-file / --split", file=sys.stderr)
        return 2

    day = time.strftime("%Y-%m-%d")
    out_dir = a.out_dir or os.path.join(HERE, "out", day, "hx_oms")
    os.makedirs(out_dir, exist_ok=True)

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    log("[恒信OMS] %d 单｜%d 步：%s" % (len(pos), len(picked),
                                     " → ".join(str(s.get("name")) for s in picked)))
    ctx = {"pos": pos, "out_dir": out_dir, "confirm": a.confirm, "dry": a.dry,
           "params": {}, "ok": [], "bad": [], "missing": []}

    runs = []
    for st in picked:
        name = str(st.get("name"))
        fn = STEPS.get(name)
        ctx["params"] = dict(st.get("params") or {})
        if not fn:
            log("[步骤] %s ✗ 没有实现" % name)
            runs.append({"name": name, "status": "fail", "why": "no-impl"})
            continue
        if (name in WRITE_STEPS or st.get("write")) and not (a.confirm and not a.dry):
            log("[步骤] %s ⊘ 跳过（写操作，缺 --confirm 或在 --dry）" % name)
            runs.append({"name": name, "status": "skip"})
            continue
        t0 = time.time()
        log("[步骤] %s …" % name)
        try:
            out = fn(ctx, log=log) or {}
        except Exception as ex:                          # noqa: BLE001
            dt = round(time.time() - t0, 1)
            log("[步骤] %s ⚠ 失败（不阻断）%.1fs %s" % (name, dt, str(ex)[:300]))
            runs.append({"name": name, "status": "fail", "why": str(ex)[:300],
                         "seconds": dt})
            continue
        dt = round(time.time() - t0, 1)
        log("[步骤] %s ✅ %.1fs %s" % (name, dt, json.dumps(out, ensure_ascii=False)[:200]))
        runs.append({"name": name, "status": "ok", "out": out, "seconds": dt})

    res = ctx.get("result") or {}
    doc = {"total": len(pos), "steps": runs, "result": res, "logs": logs[-200:]}
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    log("已存 %s" % os.path.join(out_dir, "run.json"))

    if a.dry or not a.confirm:
        log("[闸门] 未给 --confirm（或 --dry）：写步骤全部跳过，以上只是计划")
        return 0
    bad = len(res.get("fail") or []) + len(res.get("missing") or [])
    return 0 if bad == 0 else 42


if __name__ == "__main__":
    raise SystemExit(main())
