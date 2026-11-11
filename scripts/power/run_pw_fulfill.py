#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_pw_fulfill.py — 帕沃尔发货**独立流水线**（下单代发这一段）。

用户 2026-11-11：「把帕沃尔发货这个 skill pipeline 化」。

步骤顺序的唯一定义在 `data/pw_fulfill_pipeline.json → steps`，
本文件只负责「怎么跑」。与 TEMU 整链（`run_temu_fulfill.py`）分开：
整链要重导表、买面单；单店补跑帕沃尔时不该被这些拖住。

用法
----
    python scripts/power/run_pw_fulfill.py --store BEL                 # 只看计划（dry）
    python scripts/power/run_pw_fulfill.py --store BEL --confirm       # 真跑
    python scripts/power/run_pw_fulfill.py --store BHY --split <split.json> --confirm
    python scripts/power/run_pw_fulfill.py --store BEL --orders-file <pw.txt> --confirm
    python scripts/power/run_pw_fulfill.py --store BEL --only hwc_order --confirm
    python scripts/power/run_pw_fulfill.py --store BEL --step-params query.per_po=false   # 整批贴

默认取「当天最新一份」split.json（先 `out/<日期>/fulfill/<店>/split.json`，
再退回 `out/<日期>/fulfill/split.json`）。

铁律（2026-11-11 现场踩出来的，别再推翻）
----------------------------------------
1. 帕沃尔「参考单号」搜索框**现在只认一个单号**：一次给多个单号人工也搜不出
   （record_count=0）。故 `query.per_po` 默认 true。
2. 下单前**必须**「修改仓库渠道」并提交（仓库 + temu + temu官方面单）；
   跳过这步直接「下单代发」，接口回 `{"error":1,"success"}` 是**假的**，
   「状态」列永远是「未下单」。
3. 判成功只认「状态」列离开「未下单」+ 运单号回读。
4. 发货仓库取**拆分结果**，不从页面读（没设过渠道的行页面本来就是空）。
5. 失败单只处理「一单一个产品」；多产品进 MANUAL。

退出码：0=全部有终态；42=有失败/未查到（附原文）；2=参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "pw_orders", "lib"))

SPEC_DEFAULT = os.path.join(_HERE, "data", "pw_fulfill_pipeline.json")
#: 写操作（改平台状态），不给 --confirm 一律跳过
WRITE_STEPS = ("update_orders", "set_wh", "hwc_order", "fail_fix")


def _read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def find_split(store: str, out_root: str = "") -> str:
    """找该店当天最新的 split.json：先店子目录，再退回共用目录。"""
    root = out_root or os.path.join(_HERE, "out")
    day = time.strftime("%Y-%m-%d")
    for p in (os.path.join(root, day, "fulfill", store, "split.json"),
              os.path.join(root, day, "fulfill", "split.json")):
        if os.path.exists(p):
            return p
    # 最后再退一天内任意一层的 split.json（补跑前一天的批）
    base = os.path.join(root, day, "fulfill")
    if os.path.isdir(base):
        best, bt = "", 0
        for dirpath, _dirs, files in os.walk(base):
            if "split.json" in files:
                p = os.path.join(dirpath, "split.json")
                m = os.path.getmtime(p)
                if m > bt:
                    best, bt = p, m
        if best:
            return best
    return ""


def load_batch(split_path: str, store: str, orders_file: str = "") -> tuple:
    """返回 (单号列表, {单号: 仓库})。仓库来自拆分结果，不从页面读。"""
    pos, wh = [], {}
    if orders_file:
        with open(orders_file, encoding="utf-8") as f:
            pos = [x.strip() for x in f if x.strip()]
        return pos, wh
    if not split_path or not os.path.exists(split_path):
        return [], {}
    d = _read_json(split_path)
    for r in ((d.get("groups") or {}).get("PW") or []):
        o = str(r.get("ord") or "").strip()
        if o:
            pos.append(o)
            wh[o] = str(r.get("wh") or "").strip()
    return list(dict.fromkeys(pos)), wh


def check_batch_store(split_path: str, store: str) -> str:
    """防串店：split.json 里记的文件名/店铺要与 --store 对得上，否则给出警告。"""
    if not split_path or not os.path.exists(split_path):
        return ""
    d = _read_json(split_path)
    src = str(d.get("file") or "")
    if not src:
        return ""
    if store.upper() not in src.upper() and store.lower() not in src.lower():
        return "拆分结果来自 %s，与 --store %s 对不上" % (os.path.basename(src), store)
    return ""


# ---------------------------------------------------------------- 各步实现


def step_update_orders(ctx: dict, log=print) -> dict:
    import subprocess  # noqa: PLC0415
    args = [sys.executable, os.path.join(_HERE, "pw_update_orders.py"), "--confirm"]
    rc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=900)
    tail = [x for x in (rc.stdout or "").strip().splitlines() if x.strip()]
    for ln in tail[-7:]:
        log("   " + ln[:200])
    if rc.returncode != 0:
        raise RuntimeError("更新订单失败 rc=%s：%s" % (rc.returncode, (rc.stderr or "")[-300:]))
    return {"update_orders": len([x for x in tail if "更新成功" in x])}


def step_query(ctx: dict, log=print) -> dict:
    """按本批单号查 hwc_shop 状态（只读）。per_po=True 逐单查。"""
    import pw_hwc_flow as F  # noqa: PLC0415
    p = ctx["params"]
    pg = F.page()
    F.ensure_page(pg)
    pos = ctx["pos"]
    rows, missing = [], []
    if bool(p.get("per_po", True)):
        for i, po in enumerate(pos, 1):
            try:
                r = F._query_one_po(pg, po, "", float(p.get("wait", 40)))
            except Exception:  # noqa: BLE001
                r = []
            if r:
                rows.extend(r)
            else:
                missing.append(po)
            if i % 10 == 0:
                log("   逐单查询 %d/%d（已命中 %d）" % (i, len(pos), len(rows)))
    else:
        try:
            rows = F.do_query(pg, pos, shop_id="", wait=float(p.get("wait", 40)))
        except SystemExit as ex:
            log("   ⚠ 整批查询失败：%s" % str(ex)[:200])
    want = set(pos)
    by_po = {}
    for r in rows:
        if r["po"] in want:
            by_po.setdefault(r["po"], []).append(r)
    out = [{"po": po, "id": v[0]["id"], "status": v[0]["status"], "sku": v[0]["sku"],
            "wh": v[0]["wh"], "qd": v[0]["qd"], "err": v[0]["err"],
            "lines": len(v)} for po, v in by_po.items()]
    path = os.path.join(ctx["out_dir"], "pw_now.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rows": out, "missing": missing}, f, ensure_ascii=False, indent=1)
    ctx["now"] = {x["po"]: x for x in out}
    ctx["missing"] = missing
    log("   命中 %d/%d，未查到 %d；状态 %s"
        % (len(out), len(pos), len(missing), dict(Counter(x["status"] for x in out))))
    return {"query": len(out), "missing": len(missing), "path": path}


def _need_wh(rows: list) -> list:
    return [x for x in rows if not (x.get("wh") and x.get("qd"))]


def step_set_wh(ctx: dict, log=print) -> dict:
    """给没有仓库/渠道的行补「修改仓库渠道」并提交，回读 wh+qd 判成功。"""
    import pw_hwc_flow as F  # noqa: PLC0415
    now = ctx.get("now") or {}
    todo = _need_wh([x for x in now.values() if x["po"] in set(ctx["pos"])])
    if not todo:
        log("   所有行都已有仓库+渠道，无需设置")
        return {"set_wh": 0}
    # 只补有仓库信息的（仓库来自 split；没有仓的不猜）
    todo = [x for x in todo if ctx["wh_map"].get(x["po"])]
    if not todo:
        log("   ⚠ 需要设渠道的行在拆分结果里没有仓库信息 → 跳过（不猜仓库）")
        return {"set_wh": 0, "why": "no-wh-in-split"}
    per_pg = F.page()
    F.ensure_page(per_pg)
    done, failed = 0, []
    for wh in ("加州3仓", "kansas仓"):
        grp = [x for x in todo if ctx["wh_map"].get(x["po"]) == wh]
        if not grp:
            continue
        log("   [%s] 设仓库渠道 %d 单" % (wh, len(grp)))
        try:
            rows = F.do_query(per_pg, [x["po"] for x in grp], shop_id="", wait=90)
        except SystemExit as ex:
            failed += [[x["po"], str(ex)[:120]] for x in grp]
            continue
        ids = [r["id"] for r in rows if r["po"] in {x["po"] for x in grp}]
        if not F.set_checks(per_pg, ids):
            failed += [[x["po"], "勾选 0 行"] for x in grp]
            continue
        sel = F.set_selects(per_pg, wh)
        if not (sel.get("chqd") and sel.get("st")):
            failed += [[x["po"], "选仓渠道失败 %s" % json.dumps(sel, ensure_ascii=False)[:100]]
                          for x in grp]
            continue
        F.click_submit(per_pg)
        time.sleep(5)
        F.arm(per_pg)
        try:
            back = F.do_query(per_pg, [x["po"] for x in grp], shop_id="", wait=90)
        except SystemExit:
            back = []
        ok = {r["po"] for r in back if r.get("wh") and r.get("qd")}
        done += len(ok)
        failed += [[x["po"], "回读不到仓库/渠道"] for x in grp if x["po"] not in ok]
    log("   设置完成 %d 单，未成功 %d" % (done, len(failed)))
    return {"set_wh": done, "failed": failed[:50]}


def step_hwc_order(ctx: dict, log=print) -> dict:
    """下单代发（pw_hwc_flow.order_pos：勾选→设仓→提交→下单代发→回读）。"""
    import pw_hwc_flow as F  # noqa: PLC0415
    pg = F.page()
    F.ensure_page(pg)
    res = F.order_pos(pg, ctx["pos"], shop_id="", budget_s=float(ctx["params"].get("budget_s") or 4200),
                      wh_map=ctx["wh_map"], log=log)
    ctx["place"] = res
    path = os.path.join(ctx["out_dir"], "pw_order_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    log("   下单代发：ok=%d fail=%d skip=%d 未完成=%d"
        % (len(res["ok"]), len(res["fail"]), len(res["skip"]), len(res["unfinished"])))
    return {"ok": len(res["ok"]), "fail": len(res["fail"]),
            "skip": len(res["skip"]), "unfinished": len(res["unfinished"]), "path": path}


def step_fail_scan(ctx: dict, log=print) -> dict:
    """扫「下单失败」单并按库存页定方案（只读）。一单多产品 → MANUAL。"""
    import pw_fail_fix as FF  # noqa: PLC0415
    import pw_hwc_stock as ST  # noqa: PLC0415
    import pw_session as SS  # noqa: PLC0415
    rnd = int(ctx["params"].get("round") or 1)
    pw = SS.PW()
    pw.login()
    orders, stock_by_sku = [], {}
    failed_pos = []
    for po in ctx["pos"]:
        st = (ctx.get("now") or {}).get(po) or {}
        if str(st.get("status")) != "下单失败":
            continue
        failed_pos.append(po)
        # 多产品检查（不处理）
        try:
            html = pw.get("/hwc_shop.php?act=edit_order&id=%s" % st.get("id"))
            import re  # noqa: PLC0415
            rows = re.findall(r'<tr class="tr_html">(.*?)</tr>', html, re.S)
            n_lines = len([r for r in rows
                           if (re.search(r'name="ck_sku\[\][^>]*value="([^"]*)"', r) or [None, ""])[1]])
        except Exception:  # noqa: BLE001
            n_lines = 1
        sku = str(st.get("sku") or "")
        if n_lines > 1:
            orders.append({"po": po, "id": st.get("id"), "status": "下单失败",
                           "sku": sku, "seller_sku": "", "wh": st.get("wh"),
                           "err": st.get("err"), "_multi": n_lines})
            continue
        q = ST.relax(sku)
        orders.append({"po": po, "id": st.get("id"), "status": "下单失败",
                       "sku": sku, "seller_sku": q, "wh": st.get("wh"), "err": st.get("err")})
        if q not in stock_by_sku:
            try:
                stock_by_sku[q] = ST.stock_rows(pw, q)
            except Exception as ex:  # noqa: BLE001
                stock_by_sku[q] = []
                log("   [warn] 查库存失败 %s：%s" % (q, str(ex)[:80]))
    plan = []
    for o in orders:
        if o.get("_multi"):
            plan.append({"po": o["po"], "plan": "MANUAL", "why":
                         "一单多产品（%d 行）→ 按用户口径不处理" % o["_multi"]})
            continue
        plan.append(FF.plan_row(o, stock_by_sku.get(o["seller_sku"]) or [], {}))
    path = os.path.join(ctx["out_dir"], "pw_fail_round%d.json" % rnd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    ctx["fail_plan"] = plan
    for p in plan:
        log("   %-8s %-26s %s %s" % (p.get("plan"), p.get("po"),
                                     p.get("fnsku") or "-", (p.get("why") or "")[:80]))
    return {"scan": len(plan), "fixable": len([p for p in plan if p["plan"] in ("SAME_WH", "CROSS_WH")]),
            "manual": len([p for p in plan if p["plan"] == "MANUAL"]), "path": path}


def step_fail_fix(ctx: dict, log=print) -> dict:
    """按方案改仓/改 Fnsku 并重新下单（写）。"""
    import pw_fail_fix as FF  # noqa: PLC0415
    fixable = [p for p in (ctx.get("fail_plan") or []) if p["plan"] in ("SAME_WH", "CROSS_WH")]
    if not fixable:
        log("   没有可自动修的失败单")
        return {"fail_fix": 0}
    pg = FF._open_split([p["po"] for p in fixable], "pwpf", log=log)
    done = []
    for p in fixable:
        r = FF.apply_one(pg, p, True, log=log)
        log("   %s %-26s %s" % ("OK  " if r.get("ok") else "FAIL", p["po"], (r.get("why") or "")[:100]))
        if r.get("ok"):
            done.append(p["po"])
    placed = FF.place(pg, done, True, log=log) if done else {"place": "none"}
    return {"fail_fix": len(done), "place": placed.get("place"),
            "ok_po": (placed or {}).get("ok_po") or []}


def step_summary(ctx: dict, log=print) -> dict:
    """汇总：逐单最终状态 + 失败原文 + 未查到清单。"""
    import pw_hwc_flow as F  # noqa: PLC0415
    pg = F.page()
    F.ensure_page(pg)
    rows = []
    for po in ctx["pos"]:
        try:
            r = F._query_one_po(pg, po, "", 30)
        except Exception:  # noqa: BLE001
            r = []
        if r:
            rows.append({"po": po, "status": r[0]["status"], "sku": r[0]["sku"],
                         "wh": r[0]["wh"], "qd": r[0]["qd"], "err": r[0]["err"],
                         "id": r[0]["id"], "lines": len(r)})
        else:
            rows.append({"po": po, "status": "未查到", "err": ""})
    c = Counter(x["status"] for x in rows)
    bad = [x for x in rows if x["status"] not in ("已下单", "已完成", "已出库", "已发货")]
    path = os.path.join(ctx["out_dir"], "pw_fulfill_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"store": ctx["store"], "total": len(rows), "counts": dict(c),
                   "rows": rows, "bad": bad}, f, ensure_ascii=False, indent=1)
    log("   [汇总] 共 %d 单：%s" % (len(rows), dict(c)))
    for x in bad[:30]:
        log("     ✗ %-26s %-8s %s" % (x["po"], x["status"], (x.get("err") or "")[:90]))
    ctx["result"] = {"total": len(rows), "counts": dict(c), "bad": len(bad), "path": path}
    return ctx["result"]


STEPS = {
    "update_orders": step_update_orders,
    "query": step_query,
    "set_wh": step_set_wh,
    "hwc_order": step_hwc_order,
    "fail_scan": step_fail_scan,
    "fail_fix": step_fail_fix,
    "summary": step_summary,
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="帕沃尔发货独立流水线（下单代发段）")
    ap.add_argument("--store", required=True, help="店铺：BEL / BHY / BS / BD …")
    ap.add_argument("--split", default="", help="拆分结果 json（缺省取当天最新一份）")
    ap.add_argument("--orders-file", default="", help="直接给单号 txt（覆盖 split）")
    ap.add_argument("--spec", default=SPEC_DEFAULT, help="步骤定义 json")
    ap.add_argument("--only", default="", help="只跑这些步（逗号分隔）")
    ap.add_argument("--frm", default="", help="从这步开始")
    ap.add_argument("--to", default="", help="到这步结束")
    ap.add_argument("--confirm", action="store_true", help="写操作闸门")
    ap.add_argument("--out-dir", default="", help="产物目录")
    ap.add_argument("--step-params", action="append", default=[],
                    help="覆盖步骤参数，如 query.per_po=false（可重复）")
    a = ap.parse_args()

    store = a.store.upper()
    spec = _read_json(a.spec)
    steps = spec.get("steps") or []

    names = [str(s.get("name")) for s in steps]
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        bad = [x for x in want if x not in names]
        if bad:
            print("[FAIL] 不存在的步骤：%s（可选 %s）" % (", ".join(bad), ", ".join(names)),
                  file=sys.stderr)
            return 2
        picked = [s for s in steps if s.get("name") in want]
    else:
        i0 = names.index(a.frm) if a.frm in names else 0
        i1 = names.index(a.to) if a.to in names else len(steps) - 1
        if i0 > i1:
            print("[FAIL] --frm 在 --to 之后", file=sys.stderr)
            return 2
        picked = steps[i0:i1 + 1]

    split = a.split or find_split(store, os.path.dirname(a.out_dir) if a.out_dir else "")
    warn = check_batch_store(split, store)
    pos, wh_map = load_batch(split, store, a.orders_file)
    if not pos:
        print("[FAIL] 没有本批单号：给 --split / --orders-file，或先跑拆仓", file=sys.stderr)
        return 2

    day = time.strftime("%Y-%m-%d")
    out_dir = a.out_dir or os.path.join(_HERE, "out", day, "fulfill", store)
    os.makedirs(out_dir, exist_ok=True)

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    log("[帕沃尔][%s] %d 单｜拆分：%s" % (store, len(pos), split or a.orders_file or "(无)"))
    if warn:
        log("   ⚠ %s —— 先确认这批单真的属于 %s" % (warn, store))

    ctx = {"store": store, "pos": pos, "wh_map": wh_map, "out_dir": out_dir,
           "split": split, "now": {}, "params": {}, "confirm": a.confirm}
    overrides = {}
    for kv in a.step_params:
        if "=" in kv:
            k, v = kv.split("=", 1)
            step, _, key = k.partition(".")
            overrides.setdefault(step, {})[key or step] = (
                v.lower() in ("1", "true", "yes") if v.lower() in ("0", "1", "true", "false", "yes", "no")
                else (int(v) if v.isdigit() else v))

    runs = []
    for st in picked:
        name = str(st.get("name"))
        fn = STEPS.get(name)
        params = dict(st.get("params") or {})
        params.update(overrides.get(name) or {})
        ctx["params"] = params
        if not fn:
            log("[步骤] %s ✗ 没有实现" % name)
            runs.append({"name": name, "status": "fail", "why": "no-impl"})
            continue
        if name in WRITE_STEPS and not a.confirm:
            log("[步骤] %s ⊘ 跳过（写操作，缺 --confirm）" % name)
            runs.append({"name": name, "status": "skip"})
            continue
        t0 = time.time()
        log("[步骤] %s …" % name)
        try:
            out = fn(ctx, log=log) or {}
        except Exception as ex:  # noqa: BLE001
            dt = round(time.time() - t0, 1)
            log("[步骤] %s ⚠ 失败（不阻断）%.1fs %s" % (name, dt, str(ex)[:300]))
            runs.append({"name": name, "status": "fail", "why": str(ex)[:300], "seconds": dt})
            continue
        dt = round(time.time() - t0, 1)
        log("[步骤] %s ✅ %.1fs %s" % (name, dt, json.dumps(out, ensure_ascii=False)[:200]))
        runs.append({"name": name, "status": "ok", "out": out, "seconds": dt})

    res = ctx.get("result") or {}
    summary = {"store": store, "total": len(pos),
               "steps": runs,
               "bad": res.get("bad"),
               "counts": res.get("counts"),
               "result": res.get("path")}
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({**summary, "logs": logs[-200:]}, f, ensure_ascii=False, indent=1)
    log("已存 %s" % os.path.join(out_dir, "run.json"))
    if not a.confirm:
        log("[闸门] 未给 --confirm：写操作全部跳过，以上只是计划")
        return 0
    return 0 if (res.get("bad", 1) == 0) else 42


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
