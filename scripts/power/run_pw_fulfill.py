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
    python scripts/power/run_pw_fulfill.py --store BEL --step-params query.per_po=true     # 逐单复核

默认取「当天最新一份」split.json（先 `out/<日期>/fulfill/<店>/split.json`，
再退回 `out/<日期>/fulfill/split.json`）。

铁律（2026-11-11 现场踩出来的，别再推翻）
----------------------------------------
1. 搜索框**一次粘贴整批**参考单号查。个别时候整批查不全（带出旁单/缺单），
   用 `query.per_po=true` 或 `--orders-file` 对那几单单点复核。
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
    """返回 (单号列表, {单号: 仓库})。仓库来自拆分结果，不从页面读。

    ★ 2026-11-11 修：`--orders-file` 原来只给单号、**丢掉了仓库映射**，
      于是 order_pos 报「拆分结果里没有该单的仓库」，补跑那几单永远下不了单。
      现在：单号以 orders-file 为准，仓库仍去 split.json 里补。
    """
    wh = {}
    if split_path and os.path.exists(split_path):
        d = _read_json(split_path)
        for grp in ("PW", "HX", "MISS"):
            for r in ((d.get("groups") or {}).get(grp) or []):
                o = str(r.get("ord") or "").strip()
                if o and str(r.get("wh") or "").strip():
                    wh.setdefault(o, str(r.get("wh") or "").strip())
    if orders_file:
        with open(orders_file, encoding="utf-8") as f:
            pos = [x.strip() for x in f if x.strip()]
        return list(dict.fromkeys(pos)), wh
    if not split_path or not os.path.exists(split_path):
        return [], {}
    d = _read_json(split_path)
    pos = [str(r.get("ord") or "").strip()
           for r in ((d.get("groups") or {}).get("PW") or [])]
    return list(dict.fromkeys([p for p in pos if p])), wh


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
    """按本批单号查 hwc_shop 状态（只读）。默认整批贴；per_po=True 逐单复核。"""
    import pw_hwc_flow as F  # noqa: PLC0415
    p = ctx["params"]
    pg = F.page()
    F.ensure_page(pg)
    pos = ctx["pos"]
    rows, missing = [], []
    if bool(p.get("per_po", False)):
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
        # ★ 2026-11-11 用户贴图：SHEIN 店必须选 Shein + Shein官方面单
        sel = F.set_selects(per_pg, wh, platform=ctx.get("platform") or "temu")
        if not (sel.get("chqd") and sel.get("st")):
            failed += [[x["po"], "选仓渠道失败 %s" % json.dumps(sel, ensure_ascii=False)[:100]]
                          for x in grp]
            continue
        log("      快递/渠道：%s + %s" % (sel.get("zdkd_text") or "-", sel.get("chqd_name") or "-"))
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
                      wh_map=ctx["wh_map"], log=log, platform=ctx.get("platform") or "temu")
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
    # ★ 2026-11-11 修（BD 实测）：`--only pw_fail_scan` 时 ctx["now"] 是空的 ⇒
    #   扫出来 0 单、fail_fix 也跟着 0，3 张「库存不足发货」被静默放过。
    #   这里**先按本批做一次实时查询**（不复用磁盘上的 pw_now.json —— 那份可能是
    #   上一轮下单前的快照，状态还全是「未下单」，照它扫必然 0 单）。
    if not ctx.get("now"):
        log("   状态为空 → 先实时查一次本批状态（不用旧快照）")
        step_query(ctx, log=log)
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


def _shein_store(store: str) -> bool:
    """该店是不是 SHEIN 店（判定与整链 `run_temu_fulfill._is_shein_store` 同一口径）。"""
    import run_temu_fulfill as T  # noqa: PLC0415  （同目录，避免启动期循环依赖）
    return bool(T._is_shein_store(store))


def _shein_shop_id(store: str) -> str:
    import run_temu_fulfill as T  # noqa: PLC0415
    return str(T._shein_shop_id(store) or "")


def _orders_txt(ctx: dict) -> str:
    """本批 `groups.PW` 单号写成 txt（`pw_deliver_ship --by po` 只认一行一个）。"""
    p = os.path.join(ctx["out_dir"], "pw_receive_orders.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ctx["pos"]) + "\n")
    return p


def step_pw_receive(ctx: dict, log=print) -> dict:
    """帕沃尔 pb=1「待确认订单」→ 确认订单 → 回读（整链的 `pw_receive` 走这里）。

    ★ 2026-11-06 用户口径（贴 pb=1 截图纠）：待确认页按**店铺代码或复制参考单号**筛出来 →
      勾选序号 → 「请选择操作=确认订单」→ 提交，**然后才**去获取单号。
      ⇒ **只确认本批**（`groups.PW`），绝不在「整个店」或「全部店铺」上批量提交
      （2026-11-06 就误中过别人的 124 单）。
    ★ 2026-11-10：SHEIN 店也开这一步 —— 走 REST 版 `shein-buylabel/scripts/confirm_orders.py`
      （帕沃尔确认订单接口），仍按店 + 本批参考单号收敛。
    """
    import run_temu_fulfill as T  # noqa: PLC0415
    store, out = ctx["store"], ctx["out_dir"]
    if _shein_store(store):
        # `confirm_orders.py` 只认发货单 ID：先把本批参考单号映射成 ID，再 --only 提交。
        # 没有本批单号 → 跳过（绝不在整店上批量确认）
        if not ctx["pos"]:
            log("   ⚠ 没有本批帕沃尔单号 → 跳过（不在整店上批量确认订单）")
            return {"pw_receive": "skipped-no-orders"}
        _shein_dir = os.path.join(os.path.expanduser("~/.agents/skills"),
                                  "shein-buylabel", "scripts")
        if _shein_dir not in sys.path:
            sys.path.insert(0, _shein_dir)
        import confirm_orders as C  # noqa: PLC0415
        shop = _shein_shop_id(store)
        start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 20 * 86400))
        end = time.strftime("%Y-%m-%d")
        pw = C.load_pw()
        pw.login()
        ds, _rc = C.query_delivers(pw, shop_id=shop, start=start, end=end, pb="1")
        want = set(ctx["pos"])
        ids = [d["id"] for d in ds if (d.get("deliver_no") or "") in want]
        if not ids:
            log("   本批 %d 单在帕沃尔 pb=1 里没找到（可能还没生成发货单）→ 跳过" % len(want))
            return {"pw_receive": "no-match", "want": len(want)}
        argv = [sys.executable, os.path.join(os.path.dirname(C.__file__), "confirm_orders.py"),
                "--pb", "1", "--shop-id", shop, "--only", ",".join(ids),
                "--start", start, "--end", end, "--out", out, "--apply"]
        rc, tail = T._run_py(argv[1:], log=log, timeout=1800)
        for ln in [x for x in (tail or "").strip().splitlines() if x.strip()][-10:]:
            log("   " + ln[:200])
        if rc != 0:
            raise RuntimeError("SHEIN 店帕沃尔确认订单失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
        return {"pw_receive": "shein", "shop_id": shop, "ids": len(ids)}
    argv = [os.path.join(T._HERE, "pw_deliver_ship.py"), "--shop", store, "--stage", "confirm",
            "--by", "po", "--orders-file", _orders_txt(ctx)]
    rc, tail = T._run_py(argv, log=log, timeout=2400)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-8:]:
        log("   " + ln[:200])
    if rc != 0:
        raise RuntimeError("帕沃尔确认订单失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"pw_receive": "ok", "tail": lines[-1][:160] if lines else ""}


def step_pw_track(ctx: dict, log=print) -> dict:
    """帕沃尔批量获取单号（整链的 `pw_track` 走这里）。

    ★ 2026-11-10：SHEIN 店没有「取号」这一步 —— SHEIN 单的运单号是 `place-express-order`
      之后由平台生成、直接出现在 `order-detail.packageWaybillList[].waybillNo` 里
      （没有独立的获取单号接口）。这里只做**批量回读运单号**并落一份 json 给 report 用。
    ★ pb=2 行**没有参考单号** ⇒ 只能按「店铺 + 未出跟踪号」筛，行数对照本批。
    """
    import run_temu_fulfill as T  # noqa: PLC0415
    store = ctx["store"]
    if _shein_store(store):
        script = os.path.join(os.path.expanduser("~/.agents/skills"), "shein-buylabel",
                              "scripts", "shein_verify_labels.py")
        # ⚠ 该脚本没有 --out（只认 --rep-dir），别硬塞参数（2026-11-10 真机 rc=2）
        rc, tail = T._run_py([script, "--hours", "72"], log=log, timeout=1800)
        for ln in [x for x in (tail or "").strip().splitlines() if x.strip()][-10:]:
            log("   " + ln[:200])
        log("   （SHEIN：运单号由平台自动生成，无独立取号接口 ⇒ 本步＝批量回读核对）")
        if rc != 0:
            raise RuntimeError("SHEIN 运单号回读有问题（rc=%s）：%s" % (rc, (tail or "")[-300:]))
        return {"pw_track": "shein-verify"}
    argv = [os.path.join(T._HERE, "pw_deliver_ship.py"), "--shop", store, "--stage", "track"]
    rc, tail = T._run_py(argv, log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("帕沃尔获取单号失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"pw_track": "ok"}


def step_summary(ctx: dict, log=print) -> dict:
    """汇总：最终状态 + 失败原文 + 未查到清单。

    ★ 2026-11-11：**一次整批查**，不逐单（逐单 62×28s 会把整条链拖死）。
    """
    import pw_hwc_flow as F  # noqa: PLC0415
    pg = F.page()
    F.ensure_page(pg)
    try:
        raw = F.do_query(pg, ctx["pos"], shop_id="", wait=120)
    except SystemExit as ex:
        log("   ⚠ 汇总整批查询失败：%s" % str(ex)[:160])
        raw = []
    want = set(ctx["pos"])
    by = {}
    for r in raw:
        if r["po"] in want:
            by.setdefault(r["po"], []).append(r)
    rows = []
    for po in ctx["pos"]:
        v = by.get(po) or []
        if v:
            rows.append({"po": po, "status": v[0]["status"], "sku": v[0]["sku"],
                         "wh": v[0]["wh"], "qd": v[0]["qd"], "err": v[0]["err"],
                         "id": v[0]["id"], "lines": len(v)})
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
    "pw_receive": step_pw_receive,
    "pw_track": step_pw_track,
    "summary": step_summary,
}

#: 整链（`run_temu_fulfill.py`）用的**段**映射：段名 → 本文件 steps 里要跑的子序列。
#:
#: ★ 2026-11-11 用户令：「新写的帕沃尔发货 skill 里已经有的功能，整链里直接去重、改用它」。
#:   TEMU 整链不再自己实现帕沃尔那一段，改调这里（`--stage <段>`）。子序列仍由
#:   `data/pw_fulfill_pipeline.json` 的顺序裁剪而来，**这里只是"取哪几段"的映射**，
#:   不是第二份顺序 —— 增删步骤仍只改 json。
#:
#:   query      ⊂ update_orders + query + summary   （整链的 pw_update_orders 步）
#:   hwc_order  ⊂ set_wh + hwc_order                 （整链的 hwc_order 步）
#:   fail_scan  ⊂ fail_scan                          （只读）
#:   fail_fix   ⊂ fail_fix
#:
#: ★ 2026-11-11 去掉后三段的 `summary`：它每段都要**整批重查一次**（`do_query` wait=120，
#:   实测 150s/次），而这三段的产出本身已经带状态（`pw_order_result.json` /
#:   `pw_fail_round*.json`）。BD/BAN 实测每段白花 2.5 分钟，整链被拖长 8 分钟以上。
#:   `query` 段的 summary 保留：它产 `pw_fulfill_result.json`，capture_pw 要用。
STAGES = {
    "query": ("update_orders", "query", "summary"),
    "hwc_order": ("set_wh", "hwc_order"),
    "fail_scan": ("fail_scan",),
    "fail_fix": ("fail_fix",),
    "receive": ("pw_receive",),
    "track": ("pw_track",),
}


def _pw_lock(stage: str, store: str, log=print):
    """帕沃尔页面锁（跨店 / 跨进程串行）—— 拿不到就抛 `WindowBusy`。

    ★ 2026-11-12 真机（当天 85 单帕沃尔全灭的直接原因）：
      帕沃尔是**单实例单页**（`pw_hwc_flow.PORT = 9333`），但每日任务把
      BHY / BEL / BANGL→BSGL 并行跑（只有 BANGL/BSGL/BD 串行化了）⇒ 四个店抢同一张页面：
      一个店粘贴的单号被另一个店的查询/跳转冲掉，`listCallback` 回来的是**整店默认列表**
      （record_count=61，本批只有 2 单）⇒ 判据 `pos_now ⊆ 本批` 不成立 ⇒ 整批 `[FATAL]`，
      **一单都没提交**。实测 BHY 61/61、BEL 22/22、BAN 2/2 全失败，只有 BS 在窗口间隙里跑成。
      ⇒ 帕沃尔段一律先拿名为 `PW` 的窗口锁（与紫鸟店铺锁互不相干），锁粒度＝整个帕沃尔页面。
      等着排队而不是直接失败：`PW_LOCK_WAIT`（默认 900s，可用环境变量改）。
    """
    try:
        pp = os.path.dirname(_HERE)
        if pp not in sys.path:
            sys.path.insert(0, pp)
        from window_lock import WindowBusy, window_lock  # noqa: PLC0415
    except Exception as ex:                       # noqa: BLE001
        log("[锁] 拿不到窗口锁模块（%s）→ 不加锁继续跑" % str(ex)[:120])
        import contextlib  # noqa: PLC0415
        return contextlib.nullcontext()
    wait = float(os.environ.get("PW_LOCK_WAIT", "900"))
    return window_lock(os.environ.get("PW_LOCK_KEY", "PW"),
                       "pw-fulfill-%s-%s" % (store, stage or "all"),
                       wait_seconds=wait, log=lambda m: log("   [锁] %s" % m))


def _run_steps(picked: list, ctx: dict, a, log=print) -> list:
    """按顺序跑选中的步骤（整段包在帕沃尔页面锁里）。"""
    try:
        with _pw_lock(a.stage or "all", ctx.get("store") or "", log=log):
            return _run_steps_locked(picked, ctx, a, log=log)
    except Exception as ex:                       # noqa: BLE001
        if type(ex).__name__ != "WindowBusy":
            raise
        log("[锁] 帕沃尔页面被别的店占着，等不到锁 → 本店本次跳过：%s" % str(ex)[:200])
        return [{"name": str(s.get("name")), "status": "skip",
                 "why": "帕沃尔页面被别的店占着（PW 锁）"} for s in picked]


def _run_steps_locked(picked: list, ctx: dict, a, log=print) -> list:
    runs: list = []
    for st in picked:
        name = str(st.get("name"))
        fn = STEPS.get(name)
        params = dict(st.get("params") or {})
        params.update((getattr(a, "_overrides", {}) or {}).get(name) or {})
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
    return runs


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
    ap.add_argument("--stage", default="", choices=("",) + tuple(STAGES),
                    help="整链调用用：只跑这一段（" + " / ".join(STAGES) + "）")
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
    if a.stage:
        # ★ 整链只调一段：按 STAGES 取子序列，顺序仍按 json
        want = list(STAGES[a.stage])
        picked = [s for s in steps if str(s.get("name")) in want]
    elif a.only:
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

    # ★ 2026-11-11 用户贴图：指定快递/出货渠道按平台取 —— TEMU 店 temu + temu官方面单，
    #   SHEIN 店 Shein + Shein官方面单。
    try:
        import pw_hwc_flow as _F  # noqa: PLC0415
        platform = _F.platform_of_store(store)
    except Exception:  # noqa: BLE001
        platform = "shein" if store.upper() in ("BS", "BD", "BAN", "BANGL", "BEGL", "BSGL") else "temu"
    log("[平台] %s → 指定快递/出货渠道 = %s" % (store, platform))
    ctx = {"store": store, "pos": pos, "wh_map": wh_map, "out_dir": out_dir,
           "split": split, "now": {}, "params": {}, "confirm": a.confirm,
           "platform": platform}
    overrides = {}
    for kv in a.step_params:
        if "=" in kv:
            k, v = kv.split("=", 1)
            step, _, key = k.partition(".")
            overrides.setdefault(step, {})[key or step] = (
                v.lower() in ("1", "true", "yes") if v.lower() in ("0", "1", "true", "false", "yes", "no")
                else (int(v) if v.isdigit() else v))
    a._overrides = overrides        # 步骤参数覆盖（_run_steps 里读，不再穿一层参数）

    runs = _run_steps(picked, ctx, a, log)

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
