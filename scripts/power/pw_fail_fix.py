#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pw_fail_fix.py — 帕沃尔「下单失败」自动修复：扫描 → 定方案 → 改单 → 重新下单 → 回读。

用户口径（2026-11-06）
---------------------
    「自动扫描 + 自动改能确定的（同仓有代发库存、Fnsku 不同）；
      还有一种情况：这个失败订单本在来 xx 仓，但是 Fnsku 在另一个仓库有货，
      那你就可以先修改成那个仓的 Fnsku，然后再修改成有货的仓，再去重新下单」

⇒ 每单只落三种方案之一（判不了的**绝不动**）：
    SAME_WH  同仓换 Fnsku：该仓有代发库存、Fnsku 与订单当前那个不同 → 只改 ck_sku[]
    CROSS_WH 跨仓：原仓没有可用 Fnsku、另一个仓有 → **先改仓（页面下拉提交）再改 Fnsku → 再下单**
    MANUAL   都换不了 / 订单当前 Fnsku 就是唯一有货的 / 不是「下单失败」→ 交人工，脚本不碰
    判定唯一依据＝帕沃尔库存页（`pw_hwc_stock.stock_rows`，代发库存 > 0，库龄高者优先）。

用法
----
    python scripts/power/pw_fail_fix.py --store BHY --split <split.json> [--commit]
        [--round 1] [--out <dir>] [--max-fix 80] [--max-per-sku 0]

产物（默认与 pipeline 其他步骤同目录）
    hwc_fail_round<N>.json      本轮扫描 + 方案（po / 方案 / 现仓 / 新仓 / 现Fn / 新Fn / 原因）
    hwc_fail_fix_round<N>.json  本轮执行结果（改单→改仓→下单→回读 逐步留痕）

退出码
    0 跑完（无论有没有可修的单）；2 参数/文件问题；42 有改动但回读不通过

真机坑（已在 `pw_hwc_flow` 里处理，别绕开它自己写 JS）
  1. 「查询」连点会被原生 alert 阻塞 JS —— 一律走 `pw_hwc_flow.do_query`（已打桩）。
  2. 改 sku 要 JS focus()+select() 再 insertText，提交后**必须回读**（`fix_one_sku` 已做）。
  3. 仓库没改上**不许下单**：否则按缺货的旧仓再失败一次，白耗一轮。
  4. 只动**本次拆给帕沃尔**的单：恒信那份在帕沃尔页面必然显示「未下单」，动它等于乱下单。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pw_orders", "lib"))

import pw_hwc_parse as P  # noqa: E402
import pw_hwc_stock as ST  # noqa: E402
import pw_session as S  # noqa: E402

#: 帕沃尔发货仓（与 `bel_split_by_stock.PW_SHIP_WH` 同口径；顺序即改仓时的优先顺序）
WH_ORDER = ("加州3仓", "kansas仓")
#: 只有这两种状态才能自动修。`已下单` 绝不能再「下单代发」（会重复下单）
FIXABLE = ("下单失败", "未下单")
#: 「订单当前那个 Fnsku」在「下单错误提醒」里长这样：`…-sku:D14450IAA5H库存不足发货`
CUR = re.compile(r"sku:([A-Z0-9]{8,})")


# ---------------------------------------------------------------- 纯函数（可单测）


def norm_wh(wh: str) -> str:
    """把库存页/订单页上的仓名归一到拆仓口径（`加州3仓` / `kansas仓`），**仅用于比对**。

    ★ 真机：库存在 `加州3仓`，BHY 订单页可能写 `加州三仓`；kansas 有 `kansas仓`/`kansas` 两种。
      写页面时仍用 `WH_ORDER` 的原值（`pw_hwc_flow.CK` 只认这两个键）。
    """
    s = str(wh or "").replace(" ", "")
    if not s:
        return ""
    if "kansas" in s.lower():
        return "kansas仓"
    if "加州" in s and ("三" in s or "3" in s):
        return "加州3仓"
    return s


def detect_cur_fn(status: str, err: str) -> str:
    """从「下单错误提醒」取订单**当前**的 Fnsku（同仓换 Fnsku 时必须排除它）。"""
    m = CUR.search(str(err or ""))
    return m.group(1) if m else ""


def _num(x) -> float:
    m = re.match(r"-?[\d.]+", str(x or ""))
    return float(m.group(0)) if m else -1.0


def _why(pick: dict, cur_fn: str) -> str:
    return "库龄 %s 天 / 代发 %s 件%s" % (
        pick.get("age") or "-", pick.get("avail") or "-",
        "（≠ 订单当前 %s）" % cur_fn if cur_fn else "")


def plan_row(order: dict, rows: list, left: dict) -> dict:
    """给**一个**失败单定方案（纯函数，不碰网络）。

    order: `{"po","id","status","sku","wh","err"}`（hwc_shop 行）
    rows : 该 SKU 在库存页的全部行（`pw_hwc_stock.stock_rows` 的产物）
    left : `{fnsku: 剩余可分配代发库存}`，**跨单共享**（同一 Fnsku 不许被两单同时分走）

    返回 `{"po","plan","from_wh","to_wh","cur_fn","fnsku","avail","age","why"}`
    """
    po = str(order.get("po") or "")
    wh0 = norm_wh(order.get("wh"))
    cur_fn = detect_cur_fn(order.get("status") or "", order.get("err") or "")
    out = {"po": po, "plan": "MANUAL", "from_wh": wh0, "to_wh": wh0,
           "cur_fn": cur_fn, "fnsku": "", "avail": "", "age": "", "why": ""}

    if str(order.get("status") or "") not in FIXABLE:
        out["why"] = "状态「%s」不是下单失败 → 不自动改" % (order.get("status") or "")
        return out
    if not rows:
        out["why"] = "库存页查无此 Seller sku（命名可能不对，需人工核对）"
        return out
    if not wh0:
        out["why"] = "订单没有发货仓（页面为空）→ 需人工确认发哪个仓"
        return out

    def pool(wh: str) -> list:
        """该仓有代发库存、且还剩余量的 Fnsku → 库龄高优先、再看库存多的。"""
        got = []
        for r in rows:
            if norm_wh(r.get("wh")) != wh:
                continue
            fn = str(r.get("fnsku") or "")
            avail = _num(r.get("avail"))
            if not fn or avail <= 0:
                continue
            if left.get(fn, avail) <= 0:
                continue                       # 已被前面几单分完
            got.append(dict(r, avail_n=avail, age_n=_num(r.get("age"))))
        got.sort(key=lambda r: (-r["age_n"], -r["avail_n"]))
        return got

    # ① 同仓换 Fnsku（用户口径：同一仓、有代发库存、Fnsku 不同）
    same = [r for r in pool(wh0) if str(r["fnsku"]) != cur_fn]
    if same:
        pick = same[0]
        out.update({"plan": "SAME_WH", "fnsku": pick["fnsku"], "avail": pick["avail"],
                    "age": pick["age"], "why": _why(pick, cur_fn)})
        return out

    # ② 跨仓：原仓没可用 → 另一个仓有货 ⇒ 先改仓、再换成那个仓的 Fnsku、再下单
    for wh in WH_ORDER:
        if wh == wh0:
            continue
        cand = pool(wh)
        if cand:
            pick = cand[0]
            out.update({"plan": "CROSS_WH", "to_wh": wh, "fnsku": pick["fnsku"],
                        "avail": pick["avail"], "age": pick["age"],
                        "why": "%s 无可用 Fnsku → 改 %s：%s"
                               % (wh0, wh, _why(pick, cur_fn))})
            return out

    # ③ 都换不了
    only_cur = [r for r in pool(wh0) if str(r["fnsku"]) == cur_fn]
    if only_cur:
        out["why"] = ("该仓只有订单当前那个 Fnsku（%s）有货、其余都没货 ⇒ 换不了"
                      "（补货或等它回仓）" % (cur_fn or "-"))
    else:
        out["why"] = "两个发货仓都没有可分配的代发库存 Fnsku → 补货/人工"
    return out


def build_plan(orders: list, stock_by_sku: dict, per_sku_limit: int = 0) -> list:
    """批量定方案（纯函数）：**同一 SKU 的多单共享同一份库存余量**，防超分。

    stock_by_sku: `{seller_sku: rows}`（库存按 Seller sku 查一次、多单复用）
    per_sku_limit: 0=不限；>0 时一个 SKU 本轮最多动几单
    """
    left_by_sku: dict = {}
    cnt: Counter = Counter()
    out = []
    for o in orders:
        sku_q = str(o.get("seller_sku") or "")
        rows = stock_by_sku.get(sku_q) or []
        left = left_by_sku.setdefault(sku_q, {})
        for r in rows:
            left.setdefault(str(r.get("fnsku") or ""), _num(r.get("avail")))
        if per_sku_limit and cnt[sku_q] >= per_sku_limit:
            out.append({"po": o.get("po") or "", "plan": "MANUAL",
                        "from_wh": norm_wh(o.get("wh")), "to_wh": "",
                        "cur_fn": detect_cur_fn(o.get("status"), o.get("err")),
                        "fnsku": "", "avail": "", "age": "",
                        "why": "本 SKU 本轮已达上限 %d 单（--max-per-sku）" % per_sku_limit})
            continue
        p = plan_row(o, rows, left)
        if p["plan"] in ("SAME_WH", "CROSS_WH"):
            left[p["fnsku"]] = left.get(p["fnsku"], 0) - 1
            cnt[sku_q] += 1
        out.append(p)
    return out


def cap_plan(plan: list, max_fix: int) -> list:
    """本轮最多动 `max_fix` 单；超出的改成 MANUAL 并**写明原因**（不许静默丢单）。"""
    if max_fix <= 0:
        return plan
    n = 0
    for p in plan:
        if p["plan"] in ("SAME_WH", "CROSS_WH"):
            if n >= max_fix:
                p["plan"] = "MANUAL"
                p["why"] = "超过本轮上限 --max-fix=%d，下一轮再修" % max_fix
            else:
                n += 1
    return plan


def summarize(plan: list, fixed_ok: list | None = None) -> dict:
    """给 pipeline / 报告用的小结（纯函数）。"""
    c = Counter(p.get("plan") for p in plan)
    todo = c.get("SAME_WH", 0) + c.get("CROSS_WH", 0)
    ok = len(fixed_ok or [])
    return {"total": len(plan), "same_wh": c.get("SAME_WH", 0),
            "cross_wh": c.get("CROSS_WH", 0), "manual": c.get("MANUAL", 0),
            "fixed_ok": ok, "left": max(0, todo - ok)}


# ---------------------------------------------------------------- 网络部分


def scan(pos: list, start: str, end: str, log=print) -> list:
    """按参考单号批量查 hwc_shop 当前状态（只读，走接口不开浏览器）。"""
    pw = S.PW()
    pw.login()
    rows: list = []
    for i in range(0, len(pos), 60):
        rows += P.query(pw, pos[i:i + 60], start=start, end=end)["rows"]
        log("   扫描 %d/%d" % (min(i + 60, len(pos)), len(pos)))
    return rows


#: 本店在帕沃尔里的店铺 ID（BEL / BHY 不同；由 `--store` 决定，见 main）
STORE_SHOP_ID = "634418227477419"


def _open_split(pos: list, tag: str, log=print) -> "object":
    """把这批单号**直接查进页面**（文本框逐行输入 + 点查询），返回可操作的页面对象。

    ★ 2026-11-07 修：① `log` 没传进来（NameError）；② 店铺 ID 必须按**本店**传 ——
      `pw_hwc_flow` 原来写死 BHY 的 234...171，BEL 的单在 BHY 店铺下查永远是 0 行。
    """
    import pw_hwc_flow as FL  # noqa: PLC0415
    import pw_browser  # noqa: PLC0415
    pg, _ = pw_browser.ensure(PORT)
    if not pg:
        raise RuntimeError("帕沃尔浏览器起不来（端口 %d）" % PORT)
    if not pw_browser.ensure_logged_in(pg):
        raise RuntimeError("帕沃尔实例登录失效，且自动登录失败")
    rows = FL.do_query(pg, list(pos), shop_id=STORE_SHOP_ID, wait=40)
    FL.arm(pg)
    log("   页面就绪：%d 行（本批 %d 单）" % (len(rows), len(pos)))
    return pg


def apply_one(pg, item: dict, commit: bool, log=print) -> dict:
    """执行一单：跨仓先改仓（回读校验）→ 改 Fnsku（回读校验）。**不在这一步下单**。"""
    import pw_hwc_flow as FL  # noqa: PLC0415
    FL.arm(pg)
    r = {"po": item["po"], "plan": item["plan"], "ok": False, "steps": [], "why": ""}

    if item["plan"] == "CROSS_WH":
        wh = item["to_wh"]
        if wh not in FL.CK:
            r["why"] = "仓名 %r 不在下拉表里（只有 %s）" % (wh, list(FL.CK))
            return r
        # ★ 2026-11-07 真机（截图「请选择需要操作的数据」）：
        #   顺序**必须**是 查询 → 勾选 → 选下拉 → 提交。
        #   反了（先选下拉再传单号查询）会被查询清掉勾选，提交时平台回「请选择需要操作的数据」。
        qr = FL.do_query(pg, [item["po"]], shop_id=STORE_SHOP_ID, wait=40)
        hit0 = [x for x in qr if str(x.get("po")) == item["po"]]
        if not hit0:
            r["why"] = "改仓前页面上查不到这一单 → 不动"
            return r
        n_chk = FL.set_checks(pg, [hit0[0]["id"]])
        FL.goto_batch_zone(pg)
        if not commit:
            r["steps"].append({"query": item["po"], "checked": n_chk, "setwh": wh,
                               "ok": bool(n_chk), "dry": True})
        else:
            if not n_chk:
                r["why"] = "勾选失败（0 单）→ 不提交"
                return r
            info = FL.set_selects(pg, wh)
            if not info.get("chqd"):
                r["steps"].append({"setwh": info, "ok": False})
                r["why"] = "拿不到「temu官方面单」渠道 id → 不改仓（免得发错渠道）"
                return r
            FL.click_submit(pg)
            time.sleep(8)
            FL.arm(pg)
            after = FL.do_query(pg, [item["po"]], shop_id=STORE_SHOP_ID, wait=40)
            hit = [x for x in after if str(x.get("po")) == item["po"]]
            got = norm_wh(hit[0].get("wh")) if hit else ""
            if got != norm_wh(wh):
                # ★ 仓没改上就停：否则按原缺货仓再下一次，白耗一轮
                r["steps"].append({"setwh": info, "readback_wh": (hit[0].get("wh") if hit else ""),
                                   "ok": False})
                r["why"] = "改仓后回读不符（期望 %s，读到 %r）→ 不改 Fnsku、不下单" % (
                    wh, hit[0].get("wh") if hit else "")
                return r
            r["steps"].append({"setwh": info, "readback_wh": wh, "ok": True})

    fx = FL.fix_one_sku(pg, str(item.get("id") or item["po"]), item["fnsku"])
    r["steps"].append({"fix_sku": {"old": fx.get("old"), "new": fx.get("new") or item["fnsku"]},
                       "ok": bool(fx.get("ok"))})
    r["ok"] = bool(fx.get("ok")) and all(s.get("ok") for s in r["steps"])
    if not r["ok"]:
        r["why"] = fx.get("why") or "改单后回读不符"
    return r


def place(pg, pos: list, commit: bool, log=print) -> dict:
    """重新下单代发 + 回读状态（只对**已改成功**的单做）。"""
    import pw_hwc_flow as FL  # noqa: PLC0415
    if not pos:
        return {"place": "none"}
    rows = FL.do_query(pg, pos, wait=40)
    want = set(pos)
    got = [x for x in rows if str(x.get("po")) in want]
    if len(got) < len(pos):
        log("   ⚠ 只查到 %d/%d 单（没查到的**不下单**）" % (len(got), len(pos)))
    if not commit or not got:
        return {"place": "dry-run", "queried": len(got)}
    n = FL.set_checks(pg, [x["id"] for x in got])
    log("   勾选 %d 单" % n)
    # ★ 只对**这批**勾选：set_checks 会把其他行取消勾选（含刚才改仓的说明）
    FL.click_xiadan(pg)
    st = FL.wait_xiadan(pg, n)
    FL.arm(pg)
    rows2 = FL.do_query(pg, pos, wait=40)
    after = Counter(str(x.get("status")) for x in rows2)
    ok_po = [str(x.get("po")) for x in rows2 if str(x.get("status")) == "已下单"]
    return {"place": "ok", "checked": n, "wait": st, "after": dict(after), "ok_po": ok_po}


# ---------------------------------------------------------------- main


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="帕沃尔下单失败自动修复（扫描→定方案→改单→下单）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--split", required=True, help="split.json（取帕沃尔那份的单号）")
    ap.add_argument("--out", default="", help="产物目录（默认 split.json 所在目录）")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--commit", action="store_true", help="真改真下单；不给就只出方案")
    ap.add_argument("--max-fix", type=int, default=80, help="本轮最多动几单（默认 80）")
    ap.add_argument("--max-per-sku", type=int, default=0, help="同 SKU 本轮最多动几单（0=不限）")
    ap.add_argument("--start", default="2026-10-01")
    ap.add_argument("--end", default="2026-12-31")
    a = ap.parse_args()

    global STORE_SHOP_ID
    import pw_hwc_flow as _F  # noqa: PLC0415
    STORE_SHOP_ID = _F.SHOP_IDS.get((a.store or "").upper(), _F.SHOP_IDS.get("BEL"))

    if not os.path.exists(a.split):
        print("[FAIL] 找不到拆分结果 %s" % a.split, file=sys.stderr)
        return 2
    split = json.load(open(a.split, encoding="utf-8"))
    pos = [str(r.get("ord")) for r in ((split.get("groups") or {}).get("PW") or [])
           if r.get("ord")]
    out_dir = a.out or os.path.dirname(os.path.abspath(a.split))
    os.makedirs(out_dir, exist_ok=True)
    tag = "round%d" % a.round

    def log(s):
        print(s, flush=True)

    log("[失败修复] %s 第 %d 轮｜帕沃尔 %d 单｜commit=%s" % (a.store, a.round, len(pos), a.commit))
    if not pos:
        log("   帕沃尔 0 单，跳过")
        return 0

    rows = scan(pos, a.start, a.end, log=log)
    want = set(pos)
    # ★ 只处理**本次拆给帕沃尔**的单：恒信那份在这里必然「未下单」，动它会乱下单
    mine = [x for x in rows if str(x.get("po")) in want]
    bad = [x for x in mine if str(x.get("status")) in FIXABLE]
    log("   状态分布 %s；可修候选 %d 单"
        % (dict(Counter(str(x.get("status")) for x in mine)), len(bad)))

    pw = S.PW()
    pw.login()
    stock_by_sku: dict = {}
    orders = []
    for x in bad:
        sku = str(x.get("sku") or "").split(" / ")[0].strip()
        q = ST.relax(sku)
        orders.append({"po": x.get("po"), "id": x.get("id"), "status": x.get("status"),
                       "sku": sku, "seller_sku": q, "wh": x.get("wh"), "err": x.get("err")})
        if q not in stock_by_sku:
            try:
                stock_by_sku[q] = ST.stock_rows(pw, q)
            except Exception as ex:  # noqa: BLE001
                stock_by_sku[q] = []
                log("   [warn] 查库存失败 %s：%s" % (q, str(ex)[:80]))

    plan = cap_plan(build_plan(orders, stock_by_sku, per_sku_limit=a.max_per_sku), a.max_fix)
    by_po = {str(o["po"]): o for o in orders}
    fixable = [p for p in plan if p["plan"] in ("SAME_WH", "CROSS_WH")]
    for p in fixable:
        o = by_po.get(p["po"]) or {}
        p["id"] = o.get("id")
        p["sku"] = o.get("sku")
    for p in plan:
        log("  %-8s %-26s %s→%s  Fn:%s→%s  %s"
            % (p["plan"], p["po"], p["from_wh"] or "-", p["to_wh"] or "-",
               p["cur_fn"] or "-", p["fnsku"] or "-", p["why"]))
    plan_p = os.path.join(out_dir, "hwc_fail_%s.json" % tag)
    json.dump(plan, open(plan_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("   方案落盘 %s" % plan_p)

    res = {"plan": summarize(plan), "items": [], "place": None, "fixed_ok": []}
    if fixable:
        pg = _open_split([p["po"] for p in fixable], tag, log=log)
        for p in fixable:
            r = apply_one(pg, p, a.commit, log=log)
            res["items"].append(r)
            log("   %s %-26s %s" % ("OK  " if r["ok"] else "FAIL", p["po"], r["why"]))
        ok_po = [r["po"] for r in res["items"] if r["ok"]]
        res["place"] = place(pg, ok_po, a.commit, log=log)
        fixed_ok = list((res["place"] or {}).get("ok_po") or [])
        if not a.commit:
            fixed_ok = ok_po          # dry-run：只算"改单成功"，不代表下单成功
        res["fixed_ok"] = fixed_ok
        res["plan"] = summarize(plan, fixed_ok)
    res_p = os.path.join(out_dir, "hwc_fail_fix_%s.json" % tag)
    json.dump(res, open(res_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("[失败修复] 小结 %s｜%s" % (json.dumps(res["plan"], ensure_ascii=False), res_p))
    # 「改单失败」本身不算异常（下一轮还能再试），只有**改成功了但下单没成**才值得报警
    placed = set((res["place"] or {}).get("ok_po") or [])
    stuck = [po for po in (res.get("fixed_ok") or []) if a.commit and po not in placed]
    if stuck:
        log("   ⚠ 改单成功但重新下单没成：%s" % ", ".join(stuck[:8]))
        return 42
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
