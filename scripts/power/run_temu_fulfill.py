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
    """TEMU 待发货 → 精确勾选有运单号的单 → 批量确认发货（**单一入口**，四层一气呵成）。

    ★ 2026-11-06 合并：原来是 4 个脚本串跑（扫描/勾选/抽屉/二级确认），
      跨进程各扫一遍、状态对不齐（实测勾选数 8→24→16→8→0 抖动、抽屉关了却没提交）。
      现在合成 `temu_confirm_ship.py`：同一进程复用同一张页与同一份勾选状态，
      成功判据只认「待发货」计数按目标数下降。
    """
    args = [os.path.join(_HERE, "temu_confirm_ship.py"),
            "--store", ctx["store"], "--commit"]
    rc, tail = _run_py(args, log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("TEMU 批量确认发货失败（rc=%s）：%s" % (rc, (tail or "")[-500:]))
    return {"temu_confirm": "ok"}


STEPS = {
    "refresh_stock": step_refresh_stock,
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
    ap.add_argument("--prefer", default="HX", choices=("HX", "PW"),
                    help="拆仓先查哪个源：HX=恒信优先（默认，2026-11-06 口径）；PW=帕沃尔优先（旧）")
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

    ctx = {"store": a.store, "file": a.file, "out_dir": out_dir, "cfg": cfg,
           "split_json": os.path.join(out_dir, "split.json"),
           "files": {}, "reuse_page": a.reuse_page, "prefer": a.prefer,
           "hx_warehouse": "恒信加州3仓",
           "hx_carriers": hx_carriers, "pw_carriers": pw_carriers}

    # ★ 2026-11-06 修：`--only label_pw` 跳过了 split ⇒ ctx["files"] 是空的 ⇒
    #   label_pw 报「无单，跳过」然后"成功"结束（**静默什么都没做**）。
    #   现在：不给 split 时，只要磁盘上已有同一 out_dir 的 split.json 就复用它；
    #   没有就明确报错，绝不假装跑过。
    need_files = any(str(s.get("name")) in ("label_pw", "label_hx", "hx_ship") for s in picked)
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
