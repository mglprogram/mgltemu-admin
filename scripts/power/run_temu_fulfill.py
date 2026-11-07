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
    pw_fail_scan 帕沃尔下单失败自动扫描（只读）：定 同仓换Fn / 跨仓改仓 两种方案
    pw_fail_fix  按方案改单 + 改仓 + 重新下单代发（写）
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
import re
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
WRITE_STEPS = ("label_pw", "label_hx", "pw_receive", "pw_track", "hx_ship", "temu_confirm",
               # ★ 2026-11-06 加：改单 + 改仓 + 重新下单代发，全是写平台状态的操作
               "pw_fail_fix")


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


#: 这些步骤失败**不阻断**后续（只是那一段没做成，后面的段独立成立）
#:
#: ★ 2026-11-06 真机教训：`label_hx`（恒信那份买面单）因仓名写错抛 no-option，
#:   整链**在第一步就停**，导致后面的 `pw_receive` / `pw_track` / `temu_confirm`
#:   一个都没跑 —— 而帕沃尔那 124 单其实早就下好单、只等确认发货。
#:   用户 2026-11-06 口径：「可以调…最后一步批量确认发货」+「这个是发货的最后一步」
#:   ⇒ 中间段失败要**继续往下跑**，把能做完的做完，最后如实汇总哪段没成。
SOFT_STEPS = ("label_pw", "label_hx", "hx_ship",
              # ★ 2026-11-06：修不动的失败单不能把整链拖死 —— 它会被 report 收进
              #   失败 sheet 交人工；后面 pw_track / temu_confirm 对已成功的单照样有意义。
              "pw_fail_fix")


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


def _split_file(store: str, xlsx: str, out_json: str, log=print,
                prefer: str = "HX") -> dict:
    """拆仓（复用 bel_split_by_stock.py，纯读 + 落 json）。

    `prefer` 透传「先查哪个源」：默认 "HX"（用户 2026-11-06 口径：恒信优先），
    "PW" 是 11-05 的旧口径（帕沃尔优先）。
    """
    rc, tail = _run_py([os.path.join(_HERE, "bel_split_by_stock.py"),
                        "--file", xlsx, "--out", out_json,
                        "--prefer", prefer], log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("拆仓失败（rc=%s）：%s" % (rc, tail[-400:]))
    d = _read_json(out_json)
    counts = d.get("counts") or {}
    log("   拆仓结果（优先 %s）：恒信 %s / 帕沃尔 %s / 缺货 %s"
        % (prefer, counts.get("HX"), counts.get("PW"), counts.get("MISS")))
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
    # 帕沃尔再按仓拆（帕沃尔主仓 / kansas仓），因为买面单要分两批。
    # ★ 2026-11-06 真机（用户两次纠正）：
    #   **两个店的仓名不是一套写法** —— BEL=`加州3仓`（数字 3）、BHY=`加州三仓`（中文三）。
    #   写死任意一个，另一个店就报 `no-option:<仓名>`（下拉里没这个选项）。
    #   权威位置＝`data/temu_buylabel.json → stores.<店>.warehouse_default`（有实测证据）。
    #   这里按 split.json 里**实际出现的 wh 值**分组，键就是仓名本身。
    for wh in {str(r.get("wh") or "") for r in ((split.get("groups") or {}).get("PW") or [])}:
        if not wh:
            continue
        fn = "pw_%s.txt" % wh.replace("仓", "")
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


def step_refresh_stock(ctx: dict, log=print) -> dict:
    """**拉最新库存 → 再更新系统的「库存跟踪」**（只读抓取，不碰浏览器写操作）。

    用户 2026-11-06 口径：
      · 原话1：「在发货前加一个步骤：抓取恒信领星和帕沃尔的最新库存」
      · 原话2（追问）：「发货的第一步，更新系统的跟踪库存」
    ★ 第一版只做了两个源、**漏了聚合**，跟踪页还是旧数；现已补 fetch_stock_track.py。
    为什么必须在拆仓之前：拆仓全靠 `pw-stock.json` / `linxing-stock.json` 判
    「哪个仓有货、能分几件」；用隔夜快照就会**按旧数拆仓** ⇒
    明明有货被判缺货，或按已卖掉的库存超卖。

    两源各一个脚本，只读抓取、原地覆盖 json：
      · 帕沃尔  scripts/goods/fetch_pw_stock.py     → temu-admin/public/data/pw-stock.json
      · 恒信    scripts/goods/fetch_linxing_stock.py → temu-admin/public/data/linxing-stock.json
    """
    srcs = [("帕沃尔", os.path.join(_WS, "scripts", "goods", "fetch_pw_stock.py")),
            ("恒信领星", os.path.join(_WS, "scripts", "goods", "fetch_linxing_stock.py")),
            # ★ 2026-11-06 补（用户追问「发货的第一步，更新系统的跟踪库存」）：
            #   只抓两个源**不算**更新跟踪库存 —— 还要跑聚合脚本，
            #   否则 temu-admin/public/data/stock-track.json（面板看的那个）仍是旧数。
            #   顺序固定：两个源 → 聚合。
            ("库存跟踪", os.path.join(_WS, "scripts", "goods", "fetch_stock_track.py"))]
    out = {}
    for name, p in srcs:
        if not os.path.exists(p):
            raise RuntimeError("找不到库存脚本：%s" % p)
        rc, tail = _run_py([p], log=log, timeout=1800)
        if rc != 0:
            raise RuntimeError("%s 失败（rc=%s）：%s" % (name, rc, (tail or "")[-400:]))
        lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
        out[name] = lines[-1][:130] if lines else "ok"
        log("   %s：%s" % (name, out[name]))
    return {"stock": out}


def step_split(ctx: dict, log=print) -> dict:
    # ★ 2026-11-06：不给 --file 时别把空串甩给子进程（报「文件不存在：」看不出所以然）。
    #   能跑到这里说明用户只想跑后半段（货已在仓库），此时应复用磁盘上的 split.json。
    if not (ctx.get("file") or "").strip():
        if os.path.exists(ctx["split_json"]):
            log("   没给 --file → 复用已有拆分结果 %s" % ctx["split_json"])
            files = _write_group_txt(_read_json(ctx["split_json"]), ctx["out_dir"])
            ctx["files"] = files
            d = _read_json(ctx["split_json"])
            return {"split": d.get("counts"), "files": files, "reused": True}
        raise RuntimeError("没给 --file，磁盘上也没有拆分结果 %s —— 先给导出表跑 split"
                           % ctx["split_json"])
    split = _split_file(ctx["store"], ctx["file"], ctx["split_json"], log,
                        prefer=ctx.get("prefer") or "HX")
    files = _write_group_txt(split, ctx["out_dir"])
    miss = len(((split.get("groups") or {}).get("MISS") or []))
    if miss:
        log("   ⚠ 缺货 %d 单 → 已单独记账，需人工（可跑 gen_pw_manual_xlsx.py 出表）" % miss)
    return {"split": split.get("counts"), "files": files, "miss": miss}


def map_warehouse(store: str, wh: str, cfg: dict) -> str:
    """把拆仓得到的仓名映射成该店 **TEMU 下拉里的真值**（纯函数）。

    ★ 2026-11-06 用户两次纠正 + 真机 no-option：
        BEL 下拉 = `加州3仓`（数字 3）；BHY 下拉 = `加州三仓`（中文三）。
        而帕沃尔库存源（pw-stock.json）两个店都写 `加州3仓` ⇒ 拆仓结果对 BHY 是错的。
        映射表在 `data/temu_buylabel.json → stores.<店>.warehouse_map`
        （只影响"填页面下拉"，不改库存源、不改拆仓口径）。
    """
    st = ((cfg.get("stores") or {}).get(store) or {})
    mp = st.get("warehouse_map") or {}
    return str(mp.get(wh) or wh)


def _label_batch(ctx: dict, ords: list, warehouse: str, carriers: str,
                 tag: str, log=print) -> dict:
    """一批单买面单：**同一进程**跑 fill_dims → fill_logistics → place_only。

    ★ 2026-11-06 修（差点真跑炸）：原来把三段拆成**三个子进程**分别调
      `--action fill_dims / fill_logistics / place_only`。但批量页只在**同进程内**共享
      （`_opened_targets` 是进程内状态），跨进程 `--reuse-page` 只能靠 URL 片段找回，
      找不到就新开一张空页 ⇒ `fill_logistics` / `place_only` 会在空页上操作或直接失败。
      正确做法：写一份临时 steps json，用 `--action pipeline` 在**一个进程**里串跑三段
      （这正是 data/temu_buylabel_pipeline.json 的设计意图）。
    """
    tmp = os.path.join(ctx["out_dir"], "buylabel_steps_%s.json" % tag)
    steps = [
        {"name": "fill_dims", "params": {}},
        {"name": "fill_logistics", "params": {"carriers": carriers,
                                              "carriers_mode": "add"} if carriers else {}},
        {"name": "place_only", "params": {}},
    ]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"steps": steps}, f, ensure_ascii=False, indent=1)
    wh_ui = map_warehouse(ctx["store"], warehouse, ctx.get("cfg") or {})
    if wh_ui != warehouse:
        log("   [仓名] %s → %s（TEMU 下拉真值）" % (warehouse, wh_ui))
    args = [os.path.join(_HERE, "run_temu_buylabel.py"),
            "--action", "pipeline", "--store", ctx["store"],
            "--orders", " ".join(ords), "--warehouse", wh_ui,
            "--steps-config", tmp, "--confirm"]
    rc, tail = _run_py(args, log=log, timeout=5400)
    if rc != 0:
        raise RuntimeError("[%s] 买面单失败（rc=%s）：%s" % (tag, rc, (tail or "")[-600:]))
    return {"tag": tag, "orders": len(ords), "warehouse": warehouse}


def step_label_pw(ctx: dict, log=print) -> dict:
    """帕沃尔那份：按仓分两批买面单（物流商 6 家，不含燕文）。"""
    # ★ 仓名与文件都来自 split 结果（见 _write_group_txt 注释）：
    #   BEL=加州3仓 / BHY=加州三仓，写死任意一个另一个店就 no-option。
    runners = [(k, v) for k, v in (ctx.get("files") or {}).items()
               if k not in ("PW", "HX", "MISS")]
    done = {}
    for wh, f in runners:
        ords = _ords(f) if f else []
        if not ords:
            log("   [%s] 无单，跳过" % wh)
            done[wh] = 0
            continue
        log("   [%s] %d 单" % (wh, len(ords)))
        _label_batch(ctx, ords, wh, ctx["pw_carriers"], "pw_%s" % wh, log)
        done[wh] = len(ords)
    return {"by_warehouse": done}


def step_label_hx(ctx: dict, log=print) -> dict:
    """恒信那份：在 TEMU 页买面单（仓=恒信加州3仓，物流商 7 家含燕文）。"""
    ords = _ords(ctx["files"].get("HX"))
    if not ords:
        log("   恒信 0 单，跳过")
        return {"hx": 0}
    _label_batch(ctx, ords, ctx["hx_warehouse"], ctx["hx_carriers"], "hx", log)
    return {"hx": len(ords)}


def step_pw_receive(ctx: dict, log=print) -> dict:
    """帕沃尔 pb=1「待确认订单」→ 确认订单（＝下单）→ 提交 → 回读状态。

    ★ 2026-11-06 用户口径（贴 pb=1 截图纠）：待确认页按**店铺代码或复制参考单号**筛出来 →
      勾选序号 → 「请选择操作=确认订单」→ 提交，**然后才**去获取单号。
    ⇒ 本步传 `--by po --orders-file <groups.PW 单号>`：只确认本批，
      绝不在「整个店」或「全部店铺」上批量提交（2026-11-06 就误中过别人的 124 单）。
    """
    pf = (ctx.get("files") or {}).get("PW") or ""
    args = [os.path.join(_HERE, "pw_deliver_ship.py"),
            "--shop", ctx["store"], "--stage", "confirm"]
    if pf and os.path.exists(pf) and _ords(pf):
        args += ["--by", "po", "--orders-file", pf]
    else:
        log("   ⚠ 没有本批帕沃尔单号 → 退回按店筛（会动整店待确认单，慎用）")
    rc, tail = _run_py(args, log=log, timeout=2400)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-8:]:
        log("   " + ln[:200])
    if rc != 0:
        raise RuntimeError("帕沃尔确认订单失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"pw_receive": "ok", "tail": lines[-1][:160] if lines else ""}


def _pw_fail_fix(ctx: dict, log=print, commit: bool = False) -> dict:
    """跑一轮 `pw_fail_fix.py`（`--store/--split/--round`），返回它的小结。

    ★ 2026-11-06 用户口径：「下单完成后，应该立即处理帕沃尔的失败订单，修改完失败订单后，
      重新下单，再去确认 → 获取单号」。所以这步卡在 `pw_receive` 与 `pw_track` 之间。
    """
    if not os.path.exists(ctx["split_json"]):
        raise RuntimeError("没有拆分结果 %s —— 先跑 split" % ctx["split_json"])
    args = [os.path.join(_HERE, "pw_fail_fix.py"),
            "--store", ctx["store"], "--split", ctx["split_json"],
            "--out", ctx["out_dir"], "--round", str(ctx.get("fail_round") or 1)]
    if commit:
        args.append("--commit")
    rc, tail = _run_py(args, log=log, timeout=5400)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-8:]:
        log("   " + ln[:200])
    if rc not in (0, 42):
        raise RuntimeError("帕沃尔失败单修复失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    return {"rc": rc, "tail": lines[-1][:200] if lines else ""}


def step_pw_fail_scan(ctx: dict, log=print) -> dict:
    """帕沃尔下单失败**自动扫描**（只读）：`下单失败/未下单` → 逐单定 同仓换Fn / 跨仓改仓。"""
    ctx["fail_round"] = int(ctx.get("fail_round") or 1)
    return _pw_fail_fix(ctx, log=log, commit=False)


def step_pw_fail_fix(ctx: dict, log=print) -> dict:
    """帕沃尔下单失败**自动修复**（写）：按方案改仓 → 改 Fnsku → 重新下单代发 → 回读。

    ★ 只动**本次拆给帕沃尔**的单（恒信那份在帕沃尔页面必然「未下单」）；
      该仓只有订单当前那个 Fnsku 有货、或两个仓都没货 ⇒ 一律不碰，交 `report` 记账。
    """
    ctx["fail_round"] = int(ctx.get("fail_round") or 1)
    return _pw_fail_fix(ctx, log=log, commit=True)


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
    # 让 report 步知道「恒信的单一律进过 OMS 了」，搜不到就是真失败
    ctx["ran_hx_ship"] = True
    return {"hx_ship": len(ords)}


def step_report(ctx: dict, log=print) -> dict:
    """出「缺货 / 失败订单」两 sheet Excel（**只读 + 落表**，不碰浏览器）。

    ★ 2026-11-06 用户口径：「把今天 bel 和 BHY 的缺货清单和失败订单整理到一个 excel 表
      分成两个 sheet」→ 追问后定：「分两个表，一个 BHY，一个 BEL，**一个表分为一个
      缺货 sheet 和失败订单 sheet**」。

    数据来源全是前面步骤的只读产物：
      split.json                拆仓结果（MISS 就是缺货）
      pw_hwc_now.json          帕沃尔 hwc_shop 当前状态（下单失败/未下单）
      pw_deliver_index.json    帕沃尔 deliver 索引（判「其实已出单有运单号」）
      <店>_hx_oms_status.json  恒信那份的领星 OMS 终态（**不给就查不出恒信失败单**）
    """
    day_dir = os.path.dirname(ctx["out_dir"])
    store = ctx["store"]
    hwc = os.path.join(day_dir, "%s_hwc_now.json" % store.lower())
    idx = os.path.join(day_dir, "pw_deliver_index.json")
    hx = os.path.join(day_dir, "%s_hx_oms_status.json" % store.lower())
    out = os.path.join(day_dir, "%s_缺货与失败订单_%s.xlsx"
                       % (store, time.strftime("%Y%m%d")))

    # ★ 2026-11-06 真机：索引必须**新鲜**。
    #   旧逻辑只在"文件不存在"时建 —— 结果用了几小时前的快照，
    #   当晚重新下单成功的 6 单不在索引里 ⇒ 既查不到运单号，又会被静默放过。
    #   ⇒ 每次都重建（只读、翻页抓全量，1079 条约 4 页，几十秒）。
    log("   重建帕沃尔 deliver 索引（只读；判运单号必须有它且必须新鲜）")
    rc, tail = _run_py([os.path.join(_HERE, "pw_index_deliver.py"),
                        "--out", idx], log=log, timeout=1800)
    if rc != 0:
        log("   ⚠ 建索引失败（rc=%s）⇒ 运单号无法核对，表里会把这些单标成「未核对」" % rc)
        idx = ""            # 宁可不用，也别用过期数据

    args = [os.path.join(_HERE, "gen_shortage_fail_xlsx.py"),
            "--store", store, "--split", ctx["split_json"], "--out", out]
    if os.path.exists(hwc):
        args += ["--hwc", hwc]
    else:
        log("   ⚠ 没有 %s ⇒ 失败订单 sheet 会是空的（需先抓帕沃尔状态）" % os.path.basename(hwc))
    if os.path.exists(idx):
        args += ["--index", idx]
    if os.path.exists(hx):
        args += ["--hx-oms", hx]
        # ★ 2026-11-06：`hx_ship` 没跑过时，恒信的单**还没进 OMS**，
        #   "OMS 里搜不到"是「还没发」不是失败（BEL 重新拆仓后就踩了这个：
        #   新落到恒信的 16 单被判成 10 单失败）。所以按实际跑没跑过来传。
        if ctx.get("ran_hx_ship"):
            args += ["--hx-shipped"]
        else:
            log("   ℹ hx_ship 本次没跑 ⇒ 恒信「OMS 搜不到」按**待发**处理，不算失败")
    else:
        log("   ⚠ 没有 %s ⇒ **恒信那份没核对**（等于没查恒信失败单）"
            % os.path.basename(hx))

    rc, tail = _run_py(args, log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("出缺货/失败 Excel 失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    real = out
    for ln in lines[:4]:
        log("   " + ln[:180])
        # 子脚本可能因文件被占用改存 `_v2`，以它打印的实际路径为准
        m = re.search(r"→\s*(\S+\.xlsx)\s*$", ln)
        if m:
            real = m.group(1)
    return {"report": real}


def step_capture_pw(ctx: dict, log=print) -> dict:
    """抓帕沃尔 hwc_shop 当前状态（只读）→ `<店>_hwc_now.json`（失败订单表要用）。

    为什么要单独一步：`hwc_shop.php` 的 `query_shop_order` 接口能一次拿到
    「状态 / 下单错误提醒 / 发货仓库 / 平台SKU」，是判「下单失败 / 未下单」的**唯一来源**
    （deliver.php 那边只有出库单，看不到下单失败原因）。
    """
    tmp = os.path.join(os.path.dirname(ctx["out_dir"]), "%s_hwc_now.json" % ctx["store"].lower())
    code = (
        "import json,sys;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');"
        "import pw_hwc_parse as P, pw_session as S;"
        "d=json.load(open(r'%s',encoding='utf-8'));"
        "pos=[r['ord'] for g in ('PW','HX','MISS') for r in (d['groups'].get(g) or [])];"
        "pw=S.PW();pw.login();rows=[];"
        "exec(\"for i in range(0,len(pos),60):\\n rows+=P.query(pw,pos[i:i+60],"
        "start='2026-10-01',end='2026-12-31')['rows']\");"
        "json.dump(rows,open(r'%s','w',encoding='utf-8'),ensure_ascii=False,indent=1);"
        "print('hwc 拿到',len(rows))"
        % (_HERE, os.path.join(os.path.dirname(_HERE), "pw_orders", "lib"),
           ctx["split_json"], tmp)
    )
    rc, tail = _run_py(["-c", code], log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("抓帕沃尔状态失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    last = [x for x in (tail or "").strip().splitlines() if x.strip()]
    log("   " + (last[-1][:160] if last else "ok"))
    return {"hwc": tmp}


def step_hx_oms_check(ctx: dict, log=print) -> dict:
    """查恒信那份在**领星 OMS** 的终态（只读）→ `<店>_hx_oms_status.json`。

    ★ 为什么必须单独查：帕沃尔页面对恒信那份必然显示「未下单」（它走领星 OMS），
      所以**恒信的失败单在帕沃尔页面上看不出来**。不查 OMS 就等于不知道恒信有没有成。
      走 `check_po_status.py` 开放接口（`status=2 仓库处理中 / 3 已出库` 才算受理），
      比抓页面稳（页面页签文本带空格、表格结构常变）。
    """
    pos_file = (ctx.get("files") or {}).get("HX")
    pos = _ords(pos_file) if pos_file else []
    if not pos:
        log("   恒信 0 单，跳过")
        return {"hx_oms": 0}
    tmp = os.path.join(os.path.dirname(ctx["out_dir"]),
                       "%s_hx_oms_status.json" % ctx["store"].lower())
    rc, tail = _run_py([os.path.join(_WS, "scripts", "oms", "check_po_status.py"),
                        "--pos", ",".join(pos), "--only-tags", "LM739",
                        "--json", tmp], log=log, timeout=1200)
    if rc not in (0, 1):
        raise RuntimeError("查恒信 OMS 状态失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    last = [x for x in (tail or "").strip().splitlines() if x.strip()]
    log("   " + (last[-1][:160] if last else "ok"))
    return {"hx_oms": len(pos), "file": tmp}


def step_temu_confirm(ctx: dict, log=print) -> dict:
    """TEMU 待发货 → 精确勾选有运单号的单 → 批量确认发货（**单一入口**，四层一气呵成）。

    ★ 2026-11-06 合并：原来是 4 个脚本串跑（扫描/勾选/抽屉/二级确认），
      跨进程各扫一遍、状态对不齐（实测勾选数 8→24→16→8→0 抖动、抽屉关了却没提交）。
      现在合成 `temu_confirm_ship.py`：同一进程复用同一张页与同一份勾选状态。

    ★ 2026-11-06 用户口径：「确认完后，等待 30s 刷新检查」+「这个是发货的最后一步」
      ⇒ ① `temu_confirm_ship.py` 内部已 等 30s → reload → 重切待发货 再判；
         ② 本步再加一道**逐单复验**（`verify_confirmed.py`）：按本批单号回读是否
            真的离开了待发货。计数会因别的单变化而不可靠，单号不会。
         ③ 本步是**最后一步**，它失败等于整批没发完 ⇒ 抛错（不许"看起来跑完"）。
    """
    confirm_json = os.path.join(ctx["out_dir"], "temu_confirm_%s.json" % ctx["store"])
    rc, tail = _run_py([os.path.join(_HERE, "temu_confirm_ship.py"),
                        "--store", ctx["store"], "--commit",
                        "--out", confirm_json], log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("TEMU 批量确认发货失败（rc=%s）：%s" % (rc, (tail or "")[-500:]))

    # ② 逐单复验（只读）。本批单号从扫描结果里取；取不到就跳过复验但要留痕。
    pos = []
    try:
        with open(confirm_json, encoding="utf-8") as f:
            d = json.load(f)
        pos = list(d.get("confirmable") or [])
    except Exception:  # noqa: BLE001
        pass
    if not pos:
        log("   ⚠ 没有本批单号清单，跳过逐单复验（只有计数判据）")
        return {"temu_confirm": "ok", "verified": "skipped"}

    lst = os.path.join(ctx["out_dir"], "temu_confirm_%s_orders.txt" % ctx["store"])
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(pos) + "\n")
    rc2, tail2 = _run_py([os.path.join(_HERE, "verify_confirmed.py"),
                          "--store", ctx["store"], "--file", lst], log=log, timeout=900)
    last = [x for x in (tail2 or "").strip().splitlines() if x.strip()]
    if rc2 != 0:
        raise RuntimeError("逐单复验未通过（%d 单里还有没离开待发货的）：%s"
                           % (len(pos), last[-1][:300] if last else ""))
    log("   ✅ 逐单复验通过：%d/%d 已离开待发货" % (len(pos), len(pos)))
    return {"temu_confirm": "ok", "verified": len(pos)}


STEPS = {
    "refresh_stock": step_refresh_stock,
    "split": step_split,
    "label_pw": step_label_pw,
    "label_hx": step_label_hx,
    "pw_receive": step_pw_receive,
    "pw_fail_scan": step_pw_fail_scan,
    "pw_fail_fix": step_pw_fail_fix,
    "pw_track": step_pw_track,
    "hx_ship": step_hx_ship,
    "capture_pw": step_capture_pw,
    "hx_oms_check": step_hx_oms_check,
    "temu_confirm": step_temu_confirm,
    "report": step_report,
}


#: 需要「拆分结果 txt」才能跑的步骤（`--only` 截取时缺了 split 就要从磁盘复用）
NEEDS_FILES = ("label_pw", "label_hx", "hx_ship", "hx_oms_check", "capture_pw", "report")

#: ★ 2026-11-06 用户铁律：**发帕沃尔仓的货用子代理跑**（BHY / BEL 都一样）。
#:
#: 用户原话：「不管 BHY 还是 BEL 的发货 —— 发帕沃尔仓的货用子代理跑」
#:
#: 这三个步骤都在**帕沃尔窗口**里长跑，跟 TEMU / 领星 OMS 是三套互不相干的
#: 登录态与页面；放一个代理里串行做会互相抢页、长任务超时，失败了也没有
#: 并行补救的余地（2026-11-06 实测：一个代理串三套窗口，卡了好几轮）。
#:
#: 代码不拦（`--confirm` 真跑时用户可能就是要本地跑），但会在开头**大声提示**，
#: 并把每个需要外派的步骤连同「要交代什么」一起打印出来，避免又想不起来。
DELEGATE_STEPS = ("label_pw", "pw_receive", "pw_fail_fix", "pw_track")

#: 派子代理时必须交代的 7 件事（漏一条就白跑，SKILL 里有详细版）
DELEGATE_BRIEF = (
    "单号清单用绝对路径 txt（别让它自己拆仓）",
    "仓名真值按店不同：BEL=加州3仓 / BHY=加州三仓 / kansas仓→BHY 写 kansas（读 data/temu_buylabel.json）",
    "物流商 6 家：USPS/SpeedX/GOFO/UPS/SwiftX/FedEx（不含燕文）",
    "16:30–19:00 禁下单时段，按钮不出现不是没权限",
    "成功判据只认运单号回读（页面关闭/接口 msg 都不算）",
    "只许碰自己那个窗口，禁关别人窗口",
    "交回来要每单的运单号 + 失败单原始报错，不是「成功了 N 单」",
)


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
    ap.add_argument("--prefer", default="HX", choices=("HX", "PW"),
                    help="拆仓先查哪个源：HX=恒信优先（默认，2026-11-06 口径）；PW=帕沃尔优先（旧）")
    ap.add_argument("--out-dir", default="", help="产物目录（默认 out/<日期>/fulfill）")
    ap.add_argument("--fail-round", type=int, default=1,
                    help="帕沃尔失败单修复到第几轮（同一天第 2 次跑用 2，产物不互相覆盖）")
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

    ctx = {"store": a.store, "file": a.file, "out_dir": out_dir, "cfg": cfg,
           "split_json": os.path.join(out_dir, "split.json"),
           "files": {}, "reuse_page": a.reuse_page, "prefer": a.prefer,
           "fail_round": a.fail_round,
           # ★ 2026-11-06 修：恒信仓名**从配置读**，不许硬编码。
           #   配置真值是 `恒信加州3`（**没有「仓」字**）；这里原写死 `恒信加州3仓`
           #   ⇒ 真机抛 `no-option:恒信加州3仓`（2026-11-06 用户当场纠正）。
           #   权威位置：data/temu_buylabel.json → hx_warehouse。
           "hx_warehouse": str(cfg.get("hx_warehouse") or "恒信加州3"),
           "hx_carriers": hx_carriers, "pw_carriers": pw_carriers}

    # ★ 2026-11-06 修：`--only label_pw` 跳过了 split ⇒ ctx["files"] 是空的 ⇒
    #   label_pw 报「无单，跳过」然后"成功"结束（**静默什么都没做**）。
    #   现在：不给 split 时，只要磁盘上已有同一 out_dir 的 split.json 就复用它；
    #   没有就明确报错，绝不假装跑过。
    need_files = any(str(s.get("name")) in NEEDS_FILES for s in picked)
    if need_files:
        if any(str(s.get("name")) == "split" for s in picked):
            pass  # 本次会跑 split，文件自然会有
        elif os.path.exists(ctx["split_json"]):
            files = _write_group_txt(_read_json(ctx["split_json"]), out_dir)
            ctx["files"] = files
            print("[复用] 已有拆分结果 %s（本次不跑 split）" % ctx["split_json"], flush=True)
        else:
            print("[FAIL] 要跑 %s 但没有拆分结果 %s —— 先跑 --only split 或 --from split"
                  % (a.only or a.frm, ctx["split_json"]), file=sys.stderr)
            return 42

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    log("[整链] %s，%d 步：%s" % (a.store, len(picked),
                                " → ".join(str(s.get("name")) for s in picked)))
    log("[口径] 恒信仓=%s｜恒信物流商=%s｜帕沃尔物流商=%s"
        % (ctx["hx_warehouse"], hx_carriers or "(页面默认)", pw_carriers or "(页面默认)"))

    # ★ 2026-11-06 用户铁律：帕沃尔仓那份**用子代理跑**（BHY / BEL 都一样）。
    delegated = [str(s.get("name")) for s in picked
                 if str(s.get("name")) in DELEGATE_STEPS]
    if delegated and a.confirm:
        log("")
        log("=" * 78)
        log("⚠ 这些步骤属于「帕沃尔仓那份」，按用户铁律**应当派子代理跑**：%s"
            % " / ".join(delegated))
        for i, b in enumerate(DELEGATE_BRIEF, 1):
            log("   %d) %s" % (i, b))
        log("   同一帕沃尔窗口只能有**一个**子代理在动；要并行就按仓拆（加州3仓 / kansas 各一路）。")
        log("   SKILL：~/.agents/skills/temu-fulfill/SKILL.md → 「铁律：帕沃尔仓那份必须派子代理跑」")
        log("=" * 78)
        log("")

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
            soft = name in SOFT_STEPS
            log("[步骤] %s %s %.1fs %s"
                % (name, "⚠ 失败（不阻断，继续）" if soft else "✗", dt, str(ex)[:300]))
            runs.append({"name": name, "status": "fail", "why": str(ex)[:300],
                         "soft": soft, "seconds": dt})
            if soft:
                continue     # ★ 2026-11-06：中间段失败继续跑，别把最后一步也丢掉
            break
        dt = round(time.time() - t0, 1)
        log("[步骤] %s ✅ %.1fs %s" % (name, dt,
                                     json.dumps(out, ensure_ascii=False)[:220]))
        runs.append({"name": name, "status": "ok", "out": out, "seconds": dt})

    s = summarize(runs)
    # ★ `stopped_at` 只该指向**硬失败**（真正让链条停下的那步）；
    #   soft 失败继续跑时不算 "stopped"，否则会误报「停在中途」。
    hard = [r for r in runs if r.get("status") == "fail" and not r.get("soft")]
    s["stopped_at"] = hard[0]["name"] if hard else ""
    s["soft_failed"] = [r["name"] for r in runs
                        if r.get("status") == "fail" and r.get("soft")]
    print("\n[RESULT] " + json.dumps({"store": a.store, "summary": s,
                                      "out_dir": out_dir}, ensure_ascii=False))
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"store": a.store, "steps": runs, "summary": s,
                   "logs": logs[-200:]}, f, ensure_ascii=False, indent=1)
    log("已存 %s" % os.path.join(out_dir, "run.json"))
    return 0 if s["ok"] else 42


if __name__ == "__main__":
    raise SystemExit(main())
