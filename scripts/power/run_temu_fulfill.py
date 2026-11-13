# -*- coding: utf-8 -*-
"""run_temu_fulfill.py — TEMU 发货**整链**（拆仓 → 买面单 → 两个仓各自发货 → 批量确认发货）。

为什么单独一个入口（2026-11-06 用户令「把这个发货流程封装成 skill 并 pipeline 化」）：
    这一段履约横跨**两个平台**（TEMU 商家中心 + 帕沃尔海外仓/领星 OMS），
    单看任何一个脚本都拼不出「42 单拆两仓 → 分别买面单 → 各自发货 → 回 TEMU 确认」这条链，
    而顺序又是固定的。故按 ai-python-methodology「流水线定义规定」D1：
    **顺序放数据（`data/temu_fulfill_pipeline.json`），怎么跑放代码（本文件）。**

链条（16 步 · 2026-11-07 固化 · 唯一权威＝`data/temu_fulfill_pipeline.json → steps`）：
    refresh_stock 拉两源最新库存 + 聚合库存跟踪（必须最先）
    export_orders ★ 导表：TEMU 订单导出（Start=当天-7 / End=当天，过滤已取消）
    split      拆仓（恒信优先、帕沃尔兜底；缺货单出 Excel 交人工）
    label_pw   帕沃尔那份：TEMU 买面单（按仓分两批：加州3仓 / kansas仓）
    label_hx   恒信那份：TEMU 买面单（恒信加州3 + 物流商 7 家含燕文）
    hwc_order  ★ 帕沃尔 hwc_shop「店铺→全部订单」按参考单号 **下单代发**（＝下单）
    pw_fail_scan 下单失败自动扫描（只读）：定 同仓换Fn / 跨仓改仓 / 交人工
    pw_fail_fix  按方案改仓 + 改 Fnsku + 重新下单代发（写）
    pw_receive 帕沃尔 deliver pb=1「待确认订单」：确认订单 → 回读状态
    pw_track   帕沃尔 deliver pb=2：未出跟踪号 → 批量获取单号
    hx_ship    恒信领星 OMS：分配仓库&物流 → 确定并审核 → 「处理中」复核
    capture_pw / hx_oms_check 抓两仓终态（判失败的唯一来源）
    temu_confirm TEMU：待发货 → 精确勾选有运单号的单 → 批量确认发货（四层）
    report     出 `<店>_缺货与失败订单_<日期>.xlsx`（`<店> 恒信仓 / <店> 帕沃尔仓 / <店> 缺货 / 失败订单`）

⚠ `hwc_order`（下单代发）与 `pw_receive`（确认订单）是**两个不同的下单环节**，
  顺序不许对调：先 hwc 下单代发才产生失败单，修完才去确认订单、最后取号。

写操作闸门：`--confirm` 才真跑写操作（WRITE_STEPS）；不给只做只读步骤。
顺序改动只改 json，别在本文件里另写一份顺序（`tests/test_pipeline_frozen.py` 守着）。

看流程图之前先记三件事（2026-11-11）：

1. **帕沃尔那一段（下单代发 → 失败单）只有一份实现**：`run_pw_fulfill.py` + `data/pw_fulfill_pipeline.json`
   （pw-fulfill skill）。本文件里 `pw_update_orders` / `hwc_order` / `pw_fail_scan` / `pw_fail_fix`
   只做转调（`_run_pw`）。要改帕沃尔的动作，去改那边，别回到这里再写第二份。
2. 下面这份表是 **TEMU / 恒信 / SHEIN / 出表推送** 这一圈的顺序（唯一权威仍是 json）。
3. `label_pw` / `label_hx` / `temu_confirm` 当前 `enabled: false`（用户 2026-11-10 令）。

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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
#: skill 根目录（SHEIN 导出脚本在这里；不在本仓里，别搬进 TEMU/scripts）
_SKILLS = os.path.expanduser("~/.agents/skills")
_WS = os.path.dirname(os.path.dirname(_HERE))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

DEFAULT_STEPS = os.path.join(_HERE, "data", "temu_fulfill_pipeline.json")
DEFAULT_CFG = os.path.join(_HERE, "data", "temu_buylabel.json")

#: 面板刷新入口（发布 admin 面板到 Cloudflare Pages；`--request --wait` = 同步等这一轮发完）
PANEL_REFRESH = os.path.join(_WS, "scripts", "panel_refresh.py")

#: 需要**真写**（改平台状态 / 花钱）的步骤 → 没有 --confirm 一律跳过。
#:
#: ★ 2026-11-07 加 `hwc_order`：hwc_shop「下单代发」是真下单（会扣库存/生成出库单），
#:   之前漏了它 ⇒ 只读预演会**照样下单**。`tests/test_pipeline_frozen.py` 现在守着这条。
#: ★ 2026-11-08 加 `pw_update_orders`：帕沃尔「更新订单」会触发店铺订单同步（写操作），
#:   同样必须挡在 --confirm 之后。
WRITE_STEPS = ("pw_update_orders", "label_shein", "label_pw", "label_hx", "hwc_order", "pw_fail_fix",
               "pw_receive", "pw_track", "hx_ship", "temu_confirm")

#: **不参与自动发货整链**的店（用户 2026-11-11 令：「BEGL 店铺不参与一整个发货流程，人工发现阶段」）。
#: BEGL 没有 OpenAPI 密钥 ⇒ 买不了面单、进不了闭环；整链直接拒绝，不给它派子代理。
#: 真要人工试跑必须显式 `--allow-manual-store`。
MANUAL_ONLY_STORES = ("BEGL",)


# ---------------------------------------------------------------- 纯函数（可单测）

def step_enabled(step: dict, store: str) -> bool:
    """该步骤对**这个店**是否启用（纯函数）。

    支持三种写法（`enabled` 优先，其次 `enabled_stores` / `disabled_stores`）：
      · `enabled: false`                      → 对所有店禁用
      · `enabled_stores: ["BS","BD"]`         → **只**对这些店启用，其他店禁用
      · `disabled_stores: ["BEL"]`            → 只对这些店禁用

    ★ 2026-11-10 用户口径：「pw_receive / pw_track / hx_ship 这三步 SHEIN 店铺是开启的」
      ⇒ 一个店一张口径。判定与 `select_steps` 共用本函数，别在两处各写一套。
    """
    s = (store or "").strip().upper()
    if step.get("enabled", True) is False:
        return False
    only = [str(x).upper() for x in (step.get("enabled_stores") or [])]
    if only:
        return s in only
    off = [str(x).upper() for x in (step.get("disabled_stores") or [])]
    if off:
        return s not in off
    return True


def select_steps(steps: list, only: str = "", frm: str = "", to: str = "",
                 store: str = "") -> list:
    """按 `--only` / `--from` / `--to` 截取本次要跑的步骤（纯函数）。

    规则：
      · `only` 非空 → 只要这些（逗号分隔），顺序仍按定义走
      · `from` 指定起点（含），`to` 指定终点（含）；两者都空＝全跑
      · 名字不存在 → 抛错（**不静默跳过**，否则会"跑完了但其实没跑"）
    """
    names = [str(s.get("name") or "") for s in (steps or [])]
    disabled = [str(s.get("name") or "") for s in (steps or [])
                if not step_enabled(s, store)]
    if only:
        want = [x.strip() for x in only.split(",") if x.strip()]
        bad = [w for w in want if w not in names]
        if bad:
            raise ValueError("--only 里有不存在的步骤：%s（可选项 %s）"
                             % (", ".join(bad), ", ".join(names)))
        off = [w for w in want if w in disabled]
        if off:
            raise ValueError("--only 里的步骤对本店已禁用（enabled/enabled_stores/disabled_stores）：%s"
                             % ", ".join(off))
        # ★ 2026-11-10：`enabled=false` 的步骤**连 --only 也点名不到** ——
        #   用户明确「TEMU 买面单已被禁用」，不能靠显式点名把它跑起来。
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
    # ★ 2026-11-10：`enabled=false` / 本店不在 `enabled_stores` 的步骤在整链里**剔除**
    #   （不是跳过留痕），否则 --to label_hx 这类会把区间撑大却什么都没跑。
    return [s for s in steps[i0:i1 + 1] if step_enabled(s, store)]


def gate_write(step_name: str, confirm: bool) -> str:
    """写操作闸门（纯函数）：返回 `"run"` / `"skip"`；不通过就跳过而不是假装跑了。"""
    if step_name in WRITE_STEPS and not confirm:
        return "skip"
    return "run"


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
              "pw_fail_fix",
              # ★ 2026-11-07 用户口径（BEL 实跑后定）：「最重要就是你的 pipeline 规定的
              #   硬失败改成软失败」。真机依据：BEL 19 单恒信面单买到 19/19、OMS 19 单
              #   全部 status=2 仓库处理中且有运单号 —— 但「已受理但没运单号」/「已下单
              #   但取号没回」这类判据当时把 19 单误报成失败，还把后面的 pw_track /
              #   temu_confirm 全带停。发货链是**每单一独立**的，任何一段的判断不成立
              #   都不该让别的单发不出去。
              #   ⚠ 软失败不是"当成功"：这一步照样记 fail，由 `report` 落进失败 sheet
              #     交人工；`stopped_at` 只认硬失败（见下面 summarize 那段）。
              "hwc_order", "pw_receive", "pw_track",
              "capture_pw", "hx_oms_check", "temu_confirm", "report")

#: 只有这两个**结构性问题**才允许停下整链（其余一律软失败继续）：
#:   `refresh_stock` 拉不到最新库存 / `split` 没有拆仓结果 → 后面每一步都没有输入，
#:   继续跑只会按空清单"假装跑完"。名字不许出现在 SOFT_STEPS 里（测试守着）。
STRUCTURAL_STEPS = ("refresh_stock", "split")


def step_is_soft(name: str) -> bool:
    """这一步失败要不要**继续往下跑**（纯函数）。

    ★ 2026-11-07 用户口径：「最重要就是你的 pipeline 规定的硬失败改成软失败」。
      默认软；只有 `STRUCTURAL_STEPS` 是硬 —— 它们失败＝整条链没有输入。
    """
    return name not in STRUCTURAL_STEPS


def summarize(runs: list) -> dict:
    """把每步执行结果汇总成一份可判定的总账（纯函数）。

    `ok` 只表示「整链没被结构性步骤打断」；`failed` / `soft_failed` 必须一起看，
    否则会把「跑完了但有单没发成」读成成功（2026-11-07 用户在意的那一点）。
    """
    out = {"total": len(runs), "ran": [], "skipped": [], "failed": [],
           "soft_failed": [], "hard_failed": [], "ok": True, "stopped_at": ""}
    for r in runs or []:
        n = r.get("name") or ""
        if r.get("status") == "ok":
            out["ran"].append(n)
        elif r.get("status") == "skip":
            out["skipped"].append(n)
        else:
            out["failed"].append(n)
            if r.get("soft"):
                out["soft_failed"].append(n)
            else:
                out["hard_failed"].append(n)
                if not out["stopped_at"]:
                    out["stopped_at"] = n
                    out["ok"] = False
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
                        "--prefer", prefer, "--store", store], log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("拆仓失败（rc=%s）：%s" % (rc, tail[-400:]))
    d = _read_json(out_json)
    counts = d.get("counts") or {}
    log("   拆仓结果（优先 %s）：恒信 %s / 帕沃尔 %s / 缺货 %s / 预售 %s"
        % (prefer, counts.get("HX"), counts.get("PW"), counts.get("MISS"),
           counts.get("PRESALE")))
    return d


def _dedupe_orders(rows: list) -> list:
    """同一单号只保留一条（顺序不变）。

    ★ 2026-11-08 修（真机踩到）：拆仓是按 SKU 行分的，**同一订单两个 SKU 会出现两行**，
      写进 txt 就是重复单号。BHY 今天 hx 148 行 / 145 个单号 ⇒ TEMU 批量页只认 145 个订单，
      `place_only` 的闸门「页面订单数 148」永远过不去 ⇒ 报
      「页面未就绪，拒绝确认下单：页面订单数 145 < 目标 148」，看着像页面没加载好，其实是清单重了。
      买面单本来就是**一单一张运单**，按单号去重才是对的。
    """
    seen, out = set(), []
    for r in rows:
        o = str(r.get("ord") or "").strip()
        if not o or o in seen:
            continue
        seen.add(o)
        out.append(r)
    return out


def _write_group_txt(split: dict, out_dir: str) -> dict:
    """把三组单号写成 txt（后续步骤按组喂）。返回 {组名: 文件路径}。

    ★ 2026-11-08：写盘前**按单号去重**（见 `_dedupe_orders` 的踩坑说明）。
    """
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    for key, fn in (("PW", "pw.txt"), ("HX", "hx.txt"), ("MISS", "miss.txt"),
                    ("PRESALE", "presale.txt")):
        rows = _dedupe_orders(((split.get("groups") or {}).get(key)) or [])
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
        rows = _dedupe_orders([r for r in ((split.get("groups") or {}).get("PW") or [])
                               if str(r.get("wh") or "") == wh])
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


def _publish_panel(store: str, log=print) -> str:
    """**刷新面板并上线**（`panel_refresh.py --request --wait`）；失败只留痕，绝不抛。

    ★ 2026-11-12 用户令：「refresh_stock 这一步再加一个步骤：从帕沃尔和领星拉取最新库存后，
      领星总库存面板重新上线发布」。
    为什么必须单列这一步：`fetch_stock_track.py` 只是把聚合结果写进
    `temu-admin/public/data/stock-track.json`，而 `temu-admin/.gitignore` 把 `public/data/`
    挡在 git 外 ⇒ **只写文件永远不会上线**。真正上线＝`panel_refresh` → `publish_admin.py`
    → `wrangler pages deploy`（`--data-only` 约 35s，需要重建时 2~4 分钟）。
    2026-11-12 实测：手动跑三店整链，refresh_stock 各写了一遍磁盘，线上仍是 12:30
    定时任务发布的那版 —— 就因为整链里没有这一步。
    用 `--wait` 而不是默认异步：异步子进程会被 DSH 的 KILL_ON_JOB_CLOSE job 带走
    （`notify_panel.cmd` 里记着这个坑），同步跑完才敢说「这一步跑完线上就是新的」。
    发布失败**不算 refresh_stock 失败**（它是通知类副作用，不该把结构性步骤带挂）。

    ★ 2026-11-12 用户令「面板发布加单飞锁（当天只一家店发）」：
      五店并发时每个店的 refresh_stock 都会调一次 `wrangler pages deploy` ⇒ 5 个部署互相覆盖，
      线上那版成了"最后提交的那个"，`generatedAt` 抖动、还会白等几分钟。
      落地两层：① 窗口锁键 `PANEL-PUB` 串行（`PANEL_LOCK_WAIT`，默认 600s）；
      ② 当天 `out/<日期>/panel_publish.json` 打戳 —— 已发过就跳过，**一天只发一次**
      （`PANEL_PUBLISH_ONCE=0` 可关掉这个单飞，回到每店各发一次）。
    """
    if not os.path.exists(PANEL_REFRESH):
        log("   面板发布：跳过（找不到 %s）" % PANEL_REFRESH)
        return "skip(missing)"
    stamp = (os.environ.get("PANEL_STAMP_FILE")
             or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"), "panel_publish.json"))
    once = os.environ.get("PANEL_PUBLISH_ONCE", "1") not in ("0", "false", "False")
    old = _read_json(stamp) if once else {}
    if once and old.get("ok") and str(old.get("date") or "") == time.strftime("%Y-%m-%d"):
        log("   面板发布：跳过（今天已由 %s 发布过：%s）"
            % (old.get("store") or "-", str(old.get("result") or "")[:120]))
        return "skip(already:%s)" % (old.get("store") or "-")

    lock = _panel_lock(store, log)
    try:
        with lock:
            # 拿到锁后再看一眼戳：并发时先到的店已经发完了
            old2 = _read_json(stamp) if once else {}
            if once and old2.get("ok") and str(old2.get("date") or "") == time.strftime("%Y-%m-%d"):
                log("   面板发布：跳过（刚被 %s 发过）" % (old2.get("store") or "-"))
                return "skip(already:%s)" % (old2.get("store") or "-")
            rc, tail = _run_py([PANEL_REFRESH, "--request", "--wait",
                                "--reason", "fulfill-refresh-stock:%s" % store],
                               log=log, timeout=900)
            lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
            last = (lines[-1] if lines else "")[:200]
            try:
                os.makedirs(os.path.dirname(stamp), exist_ok=True)
                with open(stamp, "w", encoding="utf-8") as f:
                    json.dump({"date": time.strftime("%Y-%m-%d"),
                               "store": store, "ok": rc == 0, "result": last,
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              f, ensure_ascii=False, indent=1)
            except OSError:
                pass
    except Exception as ex:                       # noqa: BLE001
        if type(ex).__name__ != "WindowBusy":
            raise
        log("   ⚠ 面板发布被占（PANEL-PUB 锁等不到）→ 本次跳过：%s" % str(ex)[:160])
        return "skip(lock)"
    if rc != 0:
        log("   ⚠ 面板发布失败（rc=%s，不影响发货）：%s" % (rc, last))
        return "fail rc=%s %s" % (rc, last)
    log("   面板发布：%s" % (last or "ok"))
    return last or "ok"


def _panel_lock(store: str, log=print):
    """面板发布单飞锁（与本文件其它窗口锁同一实现，键 `PANEL-PUB`）。"""
    try:
        pp = os.path.dirname(_HERE)
        if pp not in sys.path:
            sys.path.insert(0, pp)
        from window_lock import window_lock  # noqa: PLC0415
    except Exception as ex:                       # noqa: BLE001
        log("   [锁] 拿不到窗口锁模块（%s）→ 不加锁继续跑" % str(ex)[:100])
        import contextlib  # noqa: PLC0415
        return contextlib.nullcontext()
    return window_lock(os.environ.get("PANEL_LOCK_KEY", "PANEL-PUB"),
                       "panel-publish-%s" % (store or "all"),
                       wait_seconds=float(os.environ.get("PANEL_LOCK_WAIT", "600")),
                       log=lambda m: log("   [锁] %s" % m))


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
    ★ 2026-11-12 用户令：抓完之后**把领星总库存面板重新上线发布**（第 4 个动作，
      见 `_publish_panel`）—— 只落盘不发布，面板上看到的还是旧数。
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
    # ④ 面板上线：三个源都落盘之后再做（不然发布出去的是旧数据）
    out["面板发布"] = _publish_panel(ctx.get("store") or "", log)
    return {"stock": out}


def _is_shein_store(store: str) -> bool:
    """★ 2026-11-10 用户令：「export_orders 这一步针对 SHEIN 店铺走 shein 导出订单的脚本去导出订单」。

    判定来源＝`data/ziniao_power_map.json → mapping`（唯一权威）：
      有 `platform` / 平台字段且含 shein / Shein / SHEIN 就算 SHEIN 店；
    也兼容在 `data/temu_buylabel.json → stores.<店>` 里写 `"platform": "shein"`（临时覆盖用）。
    判不出来一律按 TEMU（老路径），**不猜**。
    """
    s = (store or "").strip()
    if not s:
        return False
    p = os.path.join(_HERE, "data", "ziniao_power_map.json")
    for m in (_read_json(p).get("mapping") or []):
        if str(m.get("power_name") or "").upper() != s.upper():
            continue
        # 实键是 `power_platform`（2026-11-10 实测：写成 platform 会永远判成 TEMU）
        plat = " ".join(str(m.get(k) or "") for k in
                        ("power_platform", "platform", "platform_name", "platformName")).lower()
        if "shein" in plat:
            return True
    cfg = _read_json(os.path.join(_HERE, "data", "temu_buylabel.json"))
    st = (cfg.get("stores") or {}).get(s) or {}
    return str(st.get("platform") or "").strip().lower() == "shein"


def _shein_shop_id(store: str) -> str:
    """SHEIN 店的帕沃尔店铺 ID。取 `data/temu_buylabel.json → stores.<店>.pw_shop_id`，
    没有就回落到 `ziniao_power_map.json → power_shop_id`。"""
    cfg = _read_json(os.path.join(_HERE, "data", "temu_buylabel.json"))
    sid = str(((cfg.get("stores") or {}).get(store) or {}).get("pw_shop_id") or "").strip()
    if sid:
        return sid
    for m in (_read_json(os.path.join(_HERE, "data", "ziniao_power_map.json")).get("mapping") or []):
        if str(m.get("power_name") or "").upper() == store.upper():
            return str(m.get("power_shop_id") or "").strip()
    return ""


def _shein_subject(store: str) -> str:
    """店铺 → SHEIN 主体 key（config.shein_openapi.stores 的键）。

    ★ 2026-11-10：密钥按主体分套 —— `TELALEO`=BS 店、`BD`=BD 店。
    权威在 `data/temu_buylabel.json → stores.<店>.shein_subject`，
    其次 `ziniao_power_map.json → shein_subject`；都没有就用店名本身（同名主体）。
    """
    cfg = _read_json(os.path.join(_HERE, "data", "temu_buylabel.json"))
    s = str(((cfg.get("stores") or {}).get(store) or {}).get("shein_subject") or "").strip()
    if s:
        return s
    for m in (_read_json(os.path.join(_HERE, "data", "ziniao_power_map.json")).get("mapping") or []):
        if str(m.get("power_name") or "").upper() == store.upper() and m.get("shein_subject"):
            return str(m["shein_subject"]).strip()
    return store


def _step_export_shein(ctx: dict, log=print) -> dict:
    """SHEIN 店导表：走 `shein_export_pending.py`（OpenAPI 拉「待处理」订单 → xlsx）。

    与 TEMU 的差别（2026-11-10 实测）：
      · 接口 `POST /open-api/order/order-list`：queryType/startTime/endTime/**page**/pageSize，
        窗口 ≤48h、pageSize ≤30、结果 ≤10000 —— 脚本内部自动切窗翻页；
      · 再调 `/open-api/order/order-detail` 拿商品行（卖家SKU/规格/数量/仓库）；
      · 只用**授权主体**那一套密钥（config.json → shein_openapi.stores），每次请求都有出口 IP 白名单重试。
    """
    store = ctx["store"]
    day = ctx.get("export_date") or latest_data_date()
    subj = _shein_subject(store)
    target = os.path.join(_WS, "每日导出销量", day, "SHEIN",
                          "%s_订单_%s.xlsx" % (store, day))
    days = float((ctx.get("params") or {}).get("days") or 3)
    script = os.path.join(_SKILLS, "shein-buylabel", "scripts", "shein_export_pending.py")
    if not os.path.exists(script):
        raise RuntimeError("找不到 SHEIN 导出脚本：%s" % script)
    log("   导表（SHEIN）：%s，最近 %s 天的「待处理」订单（主体 %s）"
        % (store, days, subj))
    rc, tail = _run_py([script, "--status", "1", "--days", str(days),
                        "--store", subj, "--out", target],
                       log=log, timeout=1800)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-6:]:
        log("   " + ln[:180])
    if rc != 0 or not os.path.exists(target):
        raise RuntimeError("SHEIN 导表失败（rc=%s），期望产物 %s：%s"
                           % (rc, target, (tail or "")[-400:]))
    n = 0
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
        wb = load_workbook(target, read_only=True)
        n = max(0, (wb.active.max_row or 1) - 1)
        wb.close()
    except Exception:  # noqa: BLE001
        n = -1
    if n == 0:
        raise RuntimeError("SHEIN 导出表 0 行（%s），拒绝拿空表拆仓" % target)
    ctx["file"] = target
    log("   导表完成：%s（%s 个商品行）" % (target, n))
    return {"export": target, "rows": n, "date": day, "platform": "shein"}


def _shein_web_window(store: str) -> str:
    """没开放平台密钥的 SHEIN 店（BEGL / BANGL）→ 用哪个紫鸟窗口做网页导出。

    权威＝`ziniao_power_map.json → mapping.<店>.shein_web_window`（可空：空＝该窗口当前
    登录的正是这家店时才能用，脚本会核对页面上的店铺名）。没有就返回 ""，调用方报错。
    """
    for m in (_read_json(os.path.join(_HERE, "data", "ziniao_power_map.json")).get("mapping") or []):
        if str(m.get("power_name") or "").upper() == store.upper():
            return str(m.get("shein_web_window") or "").strip()
    return ""


def _shein_has_api(store: str) -> bool:
    """该店在 `shein-buylabel/config.json → shein_openapi.stores` 里有没有可用的密钥对。

    有 → 走 OpenAPI（BS=TELALEO / BD=BD）；没有 → 走网页导出（BEGL / BANGL）。
    """
    cfg = os.path.join(_SKILLS, "shein-buylabel", "config.json")
    try:
        seg = (_read_json(cfg).get("shein_openapi") or {})
    except Exception:  # noqa: BLE001
        return False
    subj = _shein_subject(store)
    v = (seg.get("stores") or {}).get(subj) or {}
    return bool(v.get("openKeyId") and v.get("secretKey"))


def _step_export_shein_web(ctx: dict, log=print) -> dict:
    """SHEIN 店但**没有 OpenAPI 密钥**（BEGL / BANGL）→ 走网页导出。

    脚本＝`shein-web-export/scripts/shein_order_export.py`：紫鸟窗口里点 sellerhub
    「导出订单 → 订单商品信息导出 → 确定导出 → 确认 → 下载中心 → 下载」，
    产物是后台全字段 xlsx（54 列，含供货价/佣金/履约费/省市）。
    ★ 2026-11-10 用户口径：「如果出现下载就说明文件已经生成了」⇒ 脚本已改成点【下载】即取文件，
      不再等「文件已生成」文案。

    ⚠ 与 OpenAPI 那条的差别：网页导出**不受 key 约束**，但依赖紫鸟窗口的登录态；
      窗口里当前登录的是哪家店，就导出哪家店的单 —— 脚本会把页面店铺名打出来供核对。
    """
    store = ctx["store"]
    day = ctx.get("export_date") or latest_data_date()
    target = os.path.join(_WS, "每日导出销量", day, "SHEIN",
                          "%s_订单_%s.xlsx" % (store, day))
    script = os.path.join(_SKILLS, "shein-web-export", "scripts", "shein_order_export.py")
    if not os.path.exists(script):
        raise RuntimeError("找不到 SHEIN 网页导出脚本：%s" % script)
    win = _shein_web_window(store)
    if not win:
        raise RuntimeError("%s 没有开放平台密钥，也没配 shein_web_window —— "
                           "在 ziniao_power_map.json 里给该店加 `shein_web_window` 指向已登录的紫鸟窗口"
                           % store)
    log("   导表（SHEIN 网页，无 OpenAPI 密钥）：%s → 紫鸟窗口 %s" % (store, win))
    rc, tail = _run_py([script, "--store-name", win, "--out", target],
                       log=log, timeout=1800)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-8:]:
        log("   " + ln[:200])
    if rc != 0 or not os.path.exists(target):
        raise RuntimeError("SHEIN 网页导出失败（rc=%s），期望产物 %s：%s"
                           % (rc, target, (tail or "")[-400:]))
    n = -1
    for ln in lines:                      # 脚本会打「xlsx 核对：订单 N / 商品行 M / 件数 Q」
        m = re.search(r"订单 (\d+) / 商品行 (\d+)", ln)
        if m:
            n = int(m.group(2))
    if n == 0:
        raise RuntimeError("SHEIN 网页导出表 0 行（%s），拒绝拿空表拆仓" % target)
    ctx["file"] = target
    log("   导表完成：%s（%s 个商品行）" % (target, n))
    return {"export": target, "rows": n, "date": day, "platform": "shein-web"}


def step_export_orders(ctx: dict, log=print) -> dict:
    """**导表**：TEMU「订单导出·美国」按 最近 8 天（Start=day-7 / End=day）导出 xlsx。

    ★ 2026-11-07 用户口径：「把导表封装成 skill，然后把发货那步导表换成这个最新的
      导表方式」。以前这步是**人工/外部**先导出再 `--file` 传进来；现在作为 pipeline
      第一步自动跑，`--file` 只是覆盖入口（要补历史日期时手动给）。

    口径（全在 `scripts/power/lib/temu_sales_driver.py`，坑的清单见
    `~/.agents/skills/temu-order-export/SKILL.md`）：
      · 日期区间 Start=当天-7 / End=当天；日历没有「确定」按钮，两次点击即提交
      · 必须排除 `RPR_outOfMonth_123` 溢出格并按面板月锁定，否则会点到邻月的同一数字
      · 「已取消的商品」选「过滤」
      · 导出记录按顶部行时间戳判新记录（列表分页 20 行/页，行数判据必失效）
      · 下载扫紫鸟默认下载目录（CDP 改下载目录不生效）

    产物落 `每日导出销量/<数据日期>/TEMU/<店>_订单_<数据日期>.xlsx`，
    路径写回 `ctx["file"]` 供 `split` 直接用。
    """
    store = ctx["store"]
    # ★ 2026-11-09：调用方已经给了表（`--file`）就不该再导一次 —— 否则会用
    #   「缺省日期＝昨天」把今天的表覆盖掉（真机就是这么把今天换成昨天的）。
    given = str(ctx.get("file") or "").strip()
    if given and os.path.exists(given):
        log("   已给 --file（%s）→ 跳过导表" % os.path.basename(given))
        return {"export": given, "rows": -1, "date": "", "skipped": True}
    # ★ 2026-11-10 用户令：SHEIN 店走 SHEIN 导出脚本，TEMU 店走原来的 TEMU 导表。
    if _is_shein_store(store):
        # SHEIN 店再分两条：有 OpenAPI 密钥（BS/BD）走接口；没有（BEGL/BANGL）走网页导出。
        if _shein_web_window(store) and not _shein_has_api(store):
            log("   店铺平台=Shein（无 OpenAPI 密钥）-> 走 SHEIN 网页导出脚本")
            return _step_export_shein_web(ctx, log=log)
        log("   店铺平台=Shein -> 走 SHEIN OpenAPI 导出脚本")
        return _step_export_shein(ctx, log=log)
    day = ctx.get("export_date") or latest_data_date()
    target = os.path.join(_WS, "每日导出销量", day, "TEMU",
                          "%s_订单_%s.xlsx" % (store, day))
    log("   导表：%s，区间 %s ~ %s（Start=当天-7）"
        % (store, (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=7)), day))
    rc, tail = _run_py([os.path.join(_HERE, "run_temu_sales.py"),
                        "--store", store, "--date", day],
                       log=log, timeout=1800)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-6:]:
        log("   " + ln[:180])
    # ★ 落盘名由 driver 打小票 `OK saved=<绝对路径>`；以它为准（旧名 <MM.DD><店>.xlsx
    #   与新名并存，不能靠拼路径认定成功）。
    real = ""
    for ln in lines:
        m = re.search(r"OK saved=(\S+\.xlsx)", ln)
        if m:
            real = m.group(1)
    # ★★ 2026-11-09 真机：`--file`（今天的表）被**这一步覆盖成了昨天的表**。
    #   本步"不给 --file 才导表"只在 main 里判了错：pipeline 的其中一条路径
    #   （export_orders 被无条件选中）导致调用方传了 `--file` 也照样导，
    #   而且缺省日期是**昨天** ⇒ split 用的是隔夜旧表。两道闸门都补上。
    if rc != 0 or not real:
        raise RuntimeError("导表失败（rc=%s）：%s" % (rc, (tail or "")[-400:]))
    if os.path.abspath(real) != os.path.abspath(target):
        hint = ""
        # ★ 2026-11-13 真机（BHY + BEL 双双卡在 split「没有 --file」）：
        #   `run_temu_sales.py` 落盘名一直是**旧格式** `<M.D><店>.xlsx`（`11.12BHY.xlsx`），
        #   而这里期望 `<店>_订单_<YYYY-MM-DD>.xlsx` ⇒ 两道闸门同时挂：导出步骤被误判失败、
        #   接着 split 结构性停链，一单没发。旧格式本身**日期是对的**（11.12＝2026-11-12），
        #   只是名字不含年份。所以：名字里的 M.D 能对上数据日 → 认它，并就地改名成标准名。
        base = os.path.basename(real)
        legacy = ""
        try:
            _y, _m, _d = day.split("-")
            legacy = "%d.%d%s.xlsx" % (int(_m), int(_d), store)
        except ValueError:
            legacy = ""
        if legacy and base == legacy:
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    os.remove(target)
                os.replace(real, target)
                log("   导表名是旧格式 %s（日期对得上 %s）→ 已改名 %s"
                    % (base, day, os.path.basename(target)))
                real = target
            except OSError as ex:
                log("   ⚠ 旧格式改名失败（%s），按原路径继续" % str(ex)[:100])
    if os.path.abspath(real) != os.path.abspath(target):
        hint = ""
        if not re.search(r"订单_%s\.xlsx$" % re.escape(day), os.path.basename(real)):
            hint = ("\n  ⚠ 导出表名里没有 %s：平台很可能按**默认区间（前一天）**出单，"
                    "导表步骤没有真正生效 —— 拒绝把旧表当今天用。" % day)
        raise RuntimeError("导出落盘名与标准名不一致：%s（期望 %s）%s%s"
                           % (real, target,
                              "" if hint else "\n  ⚠ 日期对不上（可能导成了别的日期）。",
                              hint))
    if not os.path.exists(real):
        raise RuntimeError("导表小票给了路径但文件不存在：%s" % real)
    # 行数闸门：0 行等于没导到表，别拿空表去拆仓
    n = 0
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
        wb = load_workbook(real, read_only=True)
        ws = wb.active
        n = max(0, (ws.max_row or 1) - 1)
        wb.close()
    except Exception:  # noqa: BLE001
        n = -1
    if n == 0:
        raise RuntimeError("导出表 0 行（%s），拒绝拿空表拆仓" % real)
    ctx["file"] = real
    log("   导表完成：%s（%s 行）%s"
        % (real, n, "" if n > 0 else "  ⚠ 行数读取失败，按成功处理前请人工核对"))
    return {"export": real, "rows": n, "date": day}


def latest_data_date() -> str:
    """默认数据日：昨天（PT 口径与 run_temu_sales 一致）。

    不让它自己"猜最新的目录"——那会把隔夜旧表当今天的表（用户 2026-11-07 明确担心过）。
    """
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


#: ★ 2026-11-10 用户令：「split 之后加 SHEIN 店铺的买面单」
#:   只对 SHEIN 店（BS/BD）生效；TEMU 店即使被叫到也直接跳过、不报错。
def step_label_shein(ctx: dict, log=print) -> dict:
    """SHEIN 店按拆仓结果买面单：`shein-buylabel/scripts/shein_buy_label_split.py`。

    仓口径（用户 2026-11-10 贴图定稿，写死在那个脚本的 `WH_RULES`）：
      BS：恒信→`恒信加州3` / 帕沃尔加州3→`Warehouse 加州3+` / kansas→`Warehouse kansas`
      BD：恒信、帕沃尔加州3→`Warehouse 加州3` / kansas→`Kansas`
    重量：BD 默认 1.1 磅（≈499g）；BS 按件数 × 1000g。缺货单**不买**。
    """
    store = ctx["store"]
    if not _is_shein_store(store):
        log("   非 SHEIN 店，跳过（本步只给 SHEIN 店铺买面单）")
        return {"label_shein": "skipped"}
    split_json = ctx.get("split_json") or ""
    if not os.path.exists(split_json):
        raise RuntimeError("没有拆仓结果 %s —— 本步必须在 split 之后" % split_json)
    script = os.path.join(_SKILLS, "shein-buylabel", "scripts", "shein_buy_label_split.py")
    if not os.path.exists(script):
        raise RuntimeError("找不到 SHEIN 买面单脚本：%s" % script)
    p = ctx.get("params") or {}
    rep = os.path.join(ctx["out_dir"], "shein_label_%s.json" % store)
    args = [script, "--store", store, "--split", split_json, "--out", rep]
    if p.get("weight_g"):
        args += ["--weight-g", str(int(p["weight_g"]))]
    if ctx.get("confirm"):
        args += ["--apply"]
    rc, tail = _run_py(args, log=log, timeout=3600)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-10:]:
        log("   " + ln[:200])
    d = _read_json(rep)
    n = (d.get("counts") or {})
    if rc != 0:
        # 软失败：买面单没买成不影响后面的仓库动作，report 会把没号单收进失败 sheet
        raise RuntimeError("SHEIN 买面单有失败（rc=%s）：%s" % (rc, (tail or "")[-300:]))
    return {"label_shein": n or {}, "report": rep}


def step_split(ctx: dict, log=print) -> dict:
    if not (ctx.get("file") or "").strip():
        if os.path.exists(ctx["split_json"]):
            log("   没给 --file → 复用已有拆分结果 %s" % ctx["split_json"])
            files = _write_group_txt(_read_json(ctx["split_json"]), ctx["out_dir"])
            ctx["files"] = files
            d = _read_json(ctx["split_json"])
            return {"split": d.get("counts"), "files": files, "reused": True}
        raise RuntimeError("没给 --file，磁盘上也没有拆分结果 %s —— 先跑 export_orders 或给 --file"
                           % ctx["split_json"])
    split = _split_file(ctx["store"], ctx["file"], ctx["split_json"], log,
                        prefer=ctx.get("prefer") or "HX")
    files = _write_group_txt(split, ctx["out_dir"])
    # ★ 2026-11-08 修 BUG：这里原来只 return 不回写 ctx["files"]（只有 reuse 分支回写），
    #   于是下游 label_pw / label_hx / hwc_order / pw_track / hx_ship 全都读到空清单 ⇒
    #   每一步 0.0s「成功但什么都没做」，整链最后只有 report 出了缺货/失败表。
    ctx["files"] = files
    miss = len(((split.get("groups") or {}).get("MISS") or []))
    pre = len(((split.get("groups") or {}).get("PRESALE") or []))
    if miss:
        log("   ⚠ 缺货 %d 单 → 已单独记账，需人工（可跑 gen_pw_manual_xlsx.py 出表）" % miss)
    if pre:
        log("   ★ 预售 %d 单（D 组，清单 presale.txt）→ 等补货，不进缺货、不发" % pre)
    return {"split": split.get("counts"), "files": files, "miss": miss, "presale": pre}


def step_split_dedupe(ctx: dict, log=print) -> dict:
    """**拆仓结果按订单去重**（只碰我们自己的产物：split.json + 三份 txt）。

    ★ 2026-11-12 用户令：「这一步加一步拆分完后，把恒信仓、帕沃尔仓、缺货三个 sheet
      去重，不能一个订单重复出现多个 sheet」。
    一单只有一张面单、一次发货 ⇒ 同一单出现在两个组＝两个仓各买一张面单＝**重复发货**
    （report 出的 `<店> 恒信仓` / `<店> 帕沃尔仓` / `<店> 缺货` 三个 sheet 直接来自这三个组）。
    `allocate()` 已改成整单定档（正常不会冲突），本步是**闸门**：
    复用旧 split.json、或以后谁改回按行分，都在这里收敛（缺货 > 恒信 > 帕沃尔），
    冲突条数写进 run.json，收敛后重写 txt 供下游步骤直接使用。
    """
    path = ctx["split_json"]
    split = _read_json(path)
    if not split or not (split.get("groups") or {}):
        raise RuntimeError("没有拆分结果 %s —— 先去跑 split" % path)
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import bel_split_by_stock as _bs  # noqa: PLC0415  同目录纯函数模块
    before = dict(split.get("counts") or {})
    groups, conflicts = _bs.dedupe_groups(split.get("groups") or {})
    counts = {k: len(v) for k, v in groups.items()}
    if conflicts:
        log("   ⚠ 有 %d 条同一订单落在多个组，已按「预售>缺货>恒信>帕沃尔」收敛："
            % len(conflicts))
        for c in conflicts[:12]:
            log("      %s 保留 %s，从 %s 删掉（sku=%s）"
                % (c["ord"], c["kept"], c["dropped"], c["sku"]))
    split["groups"] = groups
    split["counts"] = counts
    split["dedupe_conflicts"] = conflicts
    with open(path, "w", encoding="utf-8") as f:
        json.dump(split, f, ensure_ascii=False, indent=1)
    files = _write_group_txt(split, ctx["out_dir"])
    ctx["files"] = files
    log("   去重后：恒信 %d 行 / 帕沃尔 %d 行 / 缺货 %d 行 / 预售 %d 行（冲突 %d）"
        % (counts.get("HX"), counts.get("PW"), counts.get("MISS"),
           counts.get("PRESALE"), len(conflicts)))
    return {"dedupe": counts, "before": before, "conflicts": len(conflicts),
            "files": files}


def map_warehouse(store: str, wh: str, cfg: dict) -> str:
    """把拆仓得到的仓名映射成该店 **TEMU 下拉里的真值**（纯函数）。

    ★ 2026-11-08 用户令（现行权威，记死）：
        BEL 下拉三种 = `加州3仓` / `kansas` / `恒信加州3仓`（恒信那个**带「仓」字**）
        BHY 下拉三种 = `加州三仓`（中文三） / `kansas` / `恒信加州3`（恒信那个**不带「仓」字**）
        两个店写死任意一套，另一个店必报 `no-option`。
        映射表在 `data/temu_buylabel.json → stores.<店>.warehouse_map`
        （只影响「填页面下拉」，不改库存源、不改拆仓口径）。
    """
    st = ((cfg.get("stores") or {}).get(store) or {})
    mp = st.get("warehouse_map") or {}
    return str(mp.get(wh) or wh)


def allowed_warehouses(store: str, cfg: dict) -> list:
    """该店买面单**允许的仓库名**（TEMU 下拉真值）。空列表 = 未登记 ⇒ 不校验。"""
    st = ((cfg.get("stores") or {}).get(store) or {})
    return [str(x) for x in (st.get("warehouses") or [])]


def assert_warehouse(store: str, wh_ui: str, cfg: dict, tag: str = "") -> None:
    """★ 2026-11-08 新增闸门：仓名不在该店允许清单 ⇒ **开浏览器之前**就报错。

    真机上仓名写错要等批量页开起来、填到「发货仓库」那步才抛 `no-option:<仓名>`，
    那时已勾了子订单、页面也开了，排查成本高。本地先比对 `stores.<店>.warehouses`，
    错误信息直接给「本店允许哪些」，不必再爬日志。
    """
    allow = allowed_warehouses(store, cfg)
    if not allow:
        return
    if wh_ui not in allow:
        raise RuntimeError(
            "[%s] 仓库名 %r 不在 %s 买面单允许清单 %s 内 —— 拒绝开浏览器。\n"
            "  权威：data/temu_buylabel.json → stores.%s.warehouses / warehouse_map"
            % (tag or "label", wh_ui, store, allow, store))


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
    # ★ 2026-11-08：仓名先本地校验（不在该店三仓清单里就别开浏览器）
    assert_warehouse(ctx["store"], wh_ui, ctx.get("cfg") or {}, tag)
    args = [os.path.join(_HERE, "run_temu_buylabel.py"),
            "--action", "pipeline", "--store", ctx["store"],
            "--orders", " ".join(ords), "--warehouse", wh_ui,
            "--steps-config", tmp, "--confirm",
            # ★ 2026-11-08：先清残留批量页（place_only 见到多张已填页会拒绝下单，越跑越堵）
            "--clean-stale"]
    if ctx.get("reuse_page"):
        args += ["--reuse-page", str(ctx["reuse_page"])]
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


def step_pw_update_orders(ctx: dict, log=print) -> dict:
    """帕沃尔「更新订单」+ 按本批查状态：转调 `run_pw_fulfill.py --stage query`。

    ★ 2026-11-08 用户令（原话）：「在 temu 发货的 skill 的 pipeline 的下单代发前一步
      加一个更新订单的步骤：深圳市帕沃尔国际仓储服务有限公司
      (https://www.powerwarehouse.net/hwc_shop.php?act=update_order_news)
      勾选 BE（1640478289）、BHY（634418228526171）、BEL（634418227477419）→ 点击更新勾选
      这也就是今天复制订单找不到订单失败的原因」
    ★ 2026-11-10 追加（同用户）：「加上 BD、BAN、BS 店铺的更新订单」
      ⇒ 勾选清单在 `pw_update_orders.SHOPS` 一处维护（ID 现场读回，不在这里重写）。

    ★ 2026-11-11 去重（用户令）：改用帕沃尔发货流水线 `run_pw_fulfill.py` 的
      `update_orders → query` 两段（`--stage query`），产物 `pw_now.json` 直接是
      `<店>_hwc_now.json` 的数据源（见 `capture_pw`），不再自己起 `pw_update_orders.py`。
    """
    r = _run_pw(ctx["store"], "query", ctx["out_dir"], log=log,
                split=str(ctx.get("split_json") or ""))
    got = r.get("update_orders") or {}
    q = r.get("query") or {}
    ctx["pw_now"] = os.path.join(ctx["out_dir"], "pw_now.json")
    if not (q or got):
        raise RuntimeError("帕沃尔更新订单/查询没有结果（rc=%s），见 %s"
                           % (r["_rc"], os.path.join(ctx["out_dir"], PW_RUN_ARTIFACT)))
    return {"pw_update_orders": got.get("update_orders", "?"), "query": q.get("query"),
            "missing": q.get("missing"), "now": ctx["pw_now"]}


#: pw-fulfill 独立流水线（`run_pw_fulfill.py`）拿到的 argv 会被归档到交付记录的固定文件名。
PW_RUN_ARTIFACT = "pw_fulfill_run.json"


def _run_pw(store: str, stage: str, out_dir: str, log=print,
            extra: list | None = None, split: str = "") -> dict:
    """跑 **pw-fulfill 独立流水线** 的一段（`run_pw_fulfill.py --stage <stage> --confirm`）。

    ★ 2026-11-11 用户令：「新写的帕沃尔发货 skill 里已经有的功能，整链里直接去重、改用它」。
      帕沃尔下单代发这一段（更新订单 / 查询 / 定仓 / 下单代发 / 失败单扫描与修复 / 汇总）
      的**唯一实现**现在是 `run_pw_fulfill.py` + `data/pw_fulfill_pipeline.json`，
      本文件里的 `step_pw_update_orders` / `step_hwc_order` / `step_pw_fail_scan` /
      `step_pw_fail_fix` **只做转调，不再自己拼页面动作**。
      要改帕沃尔那段的顺序或动作 → 改 `pw_fulfill_pipeline.json` / `run_pw_fulfill.py`，
      别回到这里再写一份（2026-11-11 之前两份实现已经漂移过：整链那份没做「定仓」）。

    `--stage`（整链步骤 → 帕沃尔子流水线段）：
        query          ⊂ ① + ② + ⑦   更新订单、按本批查状态、汇总 → `pw_now.json`
        hwc_order      ⊂ ③ + ④      定仓补渠道 → 下单代发 → 回读
        fail_scan      ∈ ⑤ + ⑦     扫下单失败单定方案 → 汇总
        fail_fix       ∈ ⑥ + ⑦     按方案改单并重新下单 → 汇总

    产物落在整链自己的目录（整链跑 `out/<日>/fulfill/<店>/`，单跑在 `power/out/...`）：
        pw_now.json / pw_order_result.json / pw_fail_round<N>.json / pw_fulfill_result.json / run.json
    返回 run.json 里的 `steps`，按名字取各步输出。
    """
    args = [sys.executable, os.path.join(_HERE, "run_pw_fulfill.py"),
            "--store", store, "--stage", stage, "--out-dir", out_dir, "--confirm"]
    if split:
        args += ["--split", split]
    args += list(extra or [])
    rc, tail = _run_py(args[1:], log=log, timeout=7200)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-16:]:
        log("   " + ln[:300])
    run_p = os.path.join(out_dir, "run.json")
    doc = {}
    if os.path.exists(run_p):
        doc = _read_json(run_p)
        # 整链自己的交付记录里也留一份（run.json 会被下一次 --only 覆盖）
        try:
            with open(os.path.join(out_dir, PW_RUN_ARTIFACT), "w", encoding="utf-8") as f:
                json.dump({"stage": stage, "argv": args[2:], "rc": rc,
                           "summary": {k: doc.get(k) for k in ("total", "bad", "counts", "result")},
                           "steps": doc.get("steps") or []}, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
    # rc 42 = 有失败单/未查到的单（业务判定，不是程序崩），原样带回去由调用方决定软/硬
    if rc not in (0, 42):
        raise RuntimeError("帕沃尔流水线 stage=%s 失败（rc=%s）：%s"
                           % (stage, rc, (tail or "")[-500:]))
    out = {str(s.get("name")): (s.get("out") or {}) for s in (doc.get("steps") or [])}
    out["_rc"] = rc
    out["_bad"] = doc.get("bad")
    out["_counts"] = doc.get("counts")
    return out


def step_hwc_order(ctx: dict, log=print) -> dict:
    """帕沃尔 **下单代发**（＝下单）：转调 `run_pw_fulfill.py --stage hwc_order`。

    ★ 2026-11-07 用户纠正：「在有失败订单之前，hwc_shop 应该要根据订单去下单代发
      才会有之后的失败订单」。顺序：hwc_shop 下单代发 → 修失败单 → 再确认订单 → 取号。
    ★ 只下本批 `groups.PW`：按店铺筛会把恒信那份也下掉（它的 SKU 也在帕沃尔）。
    ★ 2026-11-11 去重：真正的动作（整批粘贴搜索 → 勾选 → 修改仓库渠道并提交 →
      下单代发 → 回读）在 `pw_hwc_flow.order_pos`，由 `run_pw_fulfill.py` 的
      `set_wh` + `hwc_order` 两步负责。这里不再拼 `-c` 代码。
    """
    store = ctx["store"]
    pos = _ords((ctx.get("files") or {}).get("PW") or "")
    if not pos:
        log("   帕沃尔 0 单，跳过")
        return {"hwc_order": 0}
    r = _run_pw(store, "hwc_order", ctx["out_dir"], log=log,
                split=str(ctx.get("split_json") or ""))
    place = r.get("hwc_order") or {}
    ctx["pw_summary"] = {"bad": r.get("_bad"), "counts": r.get("_counts")}
    total = sum(int(place.get(k) or 0) for k in ("ok", "fail", "skip", "unfinished"))
    if total == 0 and r["_rc"] != 0:
        raise RuntimeError("帕沃尔下单代发没有结果（rc=%s），见 %s"
                           % (r["_rc"], os.path.join(ctx["out_dir"], "pw_fulfill_run.json")))
    # ★★ 2026-11-12 修**假绿**（真机：BHY 61 单 ok=0/fail=60、BEL 23 单 ok=0/fail=22，
    #   整链却报 ok=true / rc=0 —— 因为本步「跑完没抛异常」就被当成功）。
    #   判据必须是「本批真有单被下出去」：ok==0 且本批非空 ⇒ 抛错（软失败留痕），
    #   这样 summary.soft_failed 会带上 hwc_order，report 才会把这些单记进失败订单。
    ok_n = int(place.get("ok") or 0)
    skip_n = int(place.get("skip") or 0)
    fail_n = int(place.get("fail") or 0)
    unf_n = int(place.get("unfinished") or 0)
    # ★ 2026-11-13 修（BHY 55 单全「已下单」被判失败）：
    #   `skip` 全部来自「状态≠未下单」＝这些单**早就下过单**（已下单/待确认/已出库），
    #   本步本来就无事可做 ⇒ 不是失败。只有真出现「未完成 / fail」或「跳过的是别的异常状态」
    #   才按失败记账。
    if pos and ok_n == 0 and skip_n and unf_n == 0 and fail_n == 0:
        log("   本批 %d 单全部已是「已下单」等状态 → 无需再下单代发" % skip_n)
        return {"hwc_order": len(pos), "ok": 0, "skip": skip_n, "noop": True,
                "tail": "全批已有状态，无需下单"}
    if pos and ok_n == 0:
        raise RuntimeError(
            "帕沃尔下单代发 0 单成功（本批 %d 单：fail=%s 未完成=%s）—— 不是跑完就算成功"
            % (len(pos), place.get("fail"), place.get("unfinished")))
    return {"hwc_order": len(pos), "ok": place.get("ok"), "fail": place.get("fail"),
            "skip": place.get("skip"), "unfinished": place.get("unfinished"),
            "tail": "ok=%s fail=%s skip=%s 未完成=%s" % (
                place.get("ok"), place.get("fail"), place.get("skip"), place.get("unfinished"))}


def step_pw_receive(ctx: dict, log=print) -> dict:
    """帕沃尔 pb=1「待确认订单」→ 确认订单 → 回读：转调 `run_pw_fulfill.py --stage receive`。

    ★ 2026-11-06 用户口径（贴 pb=1 截图纠）：待确认页按**店铺代码或复制参考单号**筛出来 →
      勾选序号 → 「请选择操作=确认订单」→ 提交，**然后才**去获取单号。
    ★ 2026-11-11 用户令：本步是帕沃尔发货流水线的最后两步之一（`pw_receive → pw_track`），
      整链这里**只做转调**。「只确认本批 groups.PW、绝不在整店上批量提交」与
      「SHEIN 店走 confirm_orders 的 REST 映射」这两条口径都在
      `run_pw_fulfill.step_pw_receive` 里，别在这里再写一份。
    """
    r = _run_pw(ctx["store"], "receive", ctx["out_dir"], log=log,
                split=str(ctx.get("split_json") or ""))
    out = r.get("pw_receive") or {}
    if not out:
        raise RuntimeError("帕沃尔确认订单没有结果（rc=%s），见 %s"
                           % (r["_rc"], os.path.join(ctx["out_dir"], PW_RUN_ARTIFACT)))
    return dict(out, stage="receive", rc=r["_rc"])



def _pw_fail(ctx: dict, log=print, commit: bool = False) -> dict:
    """帕沃尔「下单失败」扫描 / 修复：转调 `run_pw_fulfill.py --stage fail_scan|fail_fix`。

    ★ 2026-11-06 用户口径：「下单完成后，应该立即处理帕沃尔的失败订单，修改完失败订单后，
      重新下单，再去确认 → 获取单号」。所以这步卡在 `pw_receive` 与 `pw_track` 之间。
    ★ 2026-11-11 去重（用户令）：改为用新写的帕沃尔发货流水线（pw-fulfill skill）。
      那边已按用户 2026-11-11 口径定型：
        · 一单多产品 → MANUAL（不处理）；同款只有一个 Fnsku → MANUAL（没有"换 Fnsku"这条）
        · 有货 Fnsku 走 `pw_hwc_stock.relax` + 库存页「代发库存/库龄」
        · 跨仓先改仓并回读 → 编辑页覆盖 ck_sku 并回读 → 重新「下单代发」→ 回读状态
      本文件不再自己跑 `pw_fail_fix.py`（两份实现会漂移）。
    """
    split = str(ctx.get("split_json") or "")
    if not os.path.exists(split):
        raise RuntimeError("没有拆分结果 %s —— 先跑 split" % split)
    stage = "fail_fix" if commit else "fail_scan"
    r = _run_pw(ctx["store"], stage, ctx["out_dir"], log=log, split=split,
                extra=["--step-params", "fail_scan.round=%d" % int(ctx.get("fail_round") or 1)])
    ctx["pw_fail"] = r.get("fail_scan") or r.get("fail_fix") or {}
    ctx["pw_summary"] = {"bad": r.get("_bad"), "counts": r.get("_counts")}
    return {"stage": stage, "rc": r["_rc"], "out": ctx["pw_fail"],
            "tail": "bad=%s counts=%s" % (r.get("_bad"), r.get("_counts"))}


def step_pw_fail_scan(ctx: dict, log=print) -> dict:
    """帕沃尔下单失败**自动扫描**（只读）：`下单失败` → 逐单定 同仓换Fn / 跨仓改仓 / 交人工。"""
    ctx["fail_round"] = int(ctx.get("fail_round") or 1)
    return _pw_fail(ctx, log=log, commit=False)


def step_pw_fail_fix(ctx: dict, log=print) -> dict:
    """帕沃尔下单失败**自动修复**（写）：按方案改仓 → 改 Fnsku → 重新下单代发 → 回读。

    ★ 只动**本次拆给帕沃尔**的单（恒信那份在帕沃尔页面必然「未下单」）；
      该仓只有订单当前那个 Fnsku 有货、或两个仓都没货 ⇒ 一律不碰，交 `report` 记账。
    """
    ctx["fail_round"] = int(ctx.get("fail_round") or 1)
    return _pw_fail(ctx, log=log, commit=True)


def step_pw_track(ctx: dict, log=print) -> dict:
    """帕沃尔批量获取单号：转调 `run_pw_fulfill.py --stage track`。

    ★ 2026-11-10：SHEIN 店没有「取号」这一步 —— SHEIN 单的运单号由平台自动生成，
      本步只做**批量回读运单号**（逻辑在 `run_pw_fulfill.step_pw_track`）。
    ★ 2026-11-11 用户令：接到帕沃尔发货流水线的最后两步（`pw_receive → pw_track`），
      整链这里只转调。
    """
    r = _run_pw(ctx["store"], "track", ctx["out_dir"], log=log,
                split=str(ctx.get("split_json") or ""))
    out = r.get("pw_track") or {}
    if not out:
        raise RuntimeError("帕沃尔获取单号没有结果（rc=%s），见 %s"
                           % (r["_rc"], os.path.join(ctx["out_dir"], PW_RUN_ARTIFACT)))
    return dict(out, stage="track", rc=r["_rc"])



def step_hx_ship(ctx: dict, log=print) -> dict:
    """恒信那份 → **领星 OMS 真发货**（skill `hx-lingxing-fulfill`）：`hx_oms_ship.py`。

    ★ 2026-11-11 用户令：「用 hx-lingxing-fulfill 这个 skill 直接替代整链里恒信领星发货那一步」。
      ⇒ 本步不再走 `oms_ship_one.py`（TEMU 窗口）也不再是「SHEIN 店只做只读核对」——
      两种店**同一套**：切「待处理」→ 200 条/页 → 平台单号整批贴主输入框 → 回车 →
      全选 → 分配仓库&物流（分配全部数据 / ZOO WAREHOUSE ⑥美西 USW01 /
      Upload_Shipping_Label / 承运商自动）→ 确定并审核 → 「待获取平台面单」重新获取。

    ★ 判据（skill 定稿）：`处理中` = 下单成功；点过「重新获取」仍留「获取异常」
      = 没买到面单 = 失败。失败/未查到的单照旧由 `report` 记进「失败订单」sheet。

    ★ 软失败：本步抛错只说明渠道/仓这类参数没对上，恒信那批还在「待处理」，
      不影响帕沃尔那批的确认发货（2026-11-07 BEL 口径）。
    """
    ords = _ords(ctx["files"].get("HX"))
    if not ords:
        log("   恒信 0 单，跳过")
        return {"hx_ship": 0}
    pos_file = os.path.join(ctx["out_dir"], "hx_oms_pos.txt")
    with open(pos_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ords) + "\n")
    # 国家码来源：本批导出订单表。`--only hx_ship` 时 ctx["file"] 可能是空的，
    # 去当天导出目录找一份（拿不到就用 DEFAULT_COUNTRY=US 兜底并打日志）。
    xlsx = str(ctx.get("file") or "")
    if not (xlsx and os.path.exists(xlsx)):
        day = ctx.get("export_date") or time.strftime("%Y-%m-%d")
        cand = os.path.join(_WS, "每日导出销量", day, "SHEIN",
                            "%s_订单_%s.xlsx" % (ctx["store"], day))
        xlsx = cand if os.path.exists(cand) else ""
    if not xlsx:
        log("   ⚠ 没找到本批导出表 → 国家码按 US 兜底（多国订单会建单失败）")
    rep = os.path.join(ctx["out_dir"], "hx_oms_ship_%s.json" % ctx["store"])
    # ★ 2026-11-11 二次收敛（用户令「把 hx-lingxing-fulfill 这个 skill pipeline 化」）：
    #   页面动作仍由 `hx_oms_ship.py` 实现，但**编排**统一走 `run_hx_fulfill.py --stage ship`
    #   （步骤顺序的唯一定义＝`data/hx_fulfill_pipeline.json`）。这里只传单号 + 产物目录。
    # ★ 2026-11-12 用户令「把领星发货替换成 API 接口」：`--stage ship` 现在是 **API 5 步**
    #   （api_sku_map → api_prepare → api_create → api_verify → api_summary）；
    #   API 建单要用**系统SKU**，所以必须把拆仓结果一起传进去（单号 txt 里没有 SKU）。
    rc, tail = _run_py([os.path.join(_HERE, "run_hx_fulfill.py"),
                        "--stage", "ship", "--pos-file", pos_file,
                        "--split", str(ctx.get("split_json") or ""),
                        # ★ 2026-11-12：网关段要店铺名（挑 shein_label_<店>.json 取承运商）
                        "--store", ctx["store"],
                        # ★ 2026-11-12：create 必填 `countryRegionCode`，split.json 没有地址列
                        #   ⇒ 把本批导出订单表一起传下去取「收件人国家」。
                        "--orders-xlsx", xlsx,
                        "--out-dir", ctx["out_dir"], "--confirm"], log=log, timeout=5400)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-12:]:
        log("   " + ln[:220])
    # 退出码 42 = 有失败/未查到（业务判定），产物仍要读回来记账
    doc = {}
    for cand in (rep, os.path.join(ctx["out_dir"], "hx_oms_ship.json")):
        if os.path.exists(cand):
            doc = _read_json(cand)
            break
    if rc not in (0, 42):
        raise RuntimeError("恒信领星发货失败（rc=%s）：%s" % (rc, (tail or "")[-500:]))
    if not doc:
        raise RuntimeError("恒信领星发货没产出结果 json（rc=%s）：%s"
                           % (rc, (tail or "")[-400:]))
    ok = doc.get("ok") or []
    bad = doc.get("fail") or []
    missing = doc.get("missing") or []
    # 只有真进了 OMS 的单才记进「本次送单」，report 才对它们用「搜不到＝失败」的判据
    ctx.setdefault("hx_shipped_ords", set()).update(
        [str(x.get("po")) for x in ok] + [str(x.get("po")) for x in bad])
    log("   恒信OMS：处理中/已发货 %d｜失败 %d｜未查到 %d"
        % (len(ok), len(bad), len(missing)))
    return {"hx_ship": len(ords), "ok": len(ok), "fail": len(bad),
            "missing": len(missing), "report": rep}


def step_report(ctx: dict, log=print) -> dict:
    """出「缺货 / 失败订单」Excel（**只读 + 落表**，不碰浏览器）。

    ★ 2026-11-06 用户口径：「把今天 bel 和 BHY 的缺货清单和失败订单整理到一个 excel 表
      分成两个 sheet」→ 追问后定：「分两个表，一个 BHY，一个 BEL，**一个表分为一个
      缺货 sheet 和失败订单 sheet**」。
    ★ 2026-11-10 用户改口径（贴图）：每店一张表，sheet ＝ `<店> 恒信仓` / `<店> 帕沃尔仓`
      / `<店> 缺货` / `失败订单`（三张仓 sheet 直接来自 split 的三组）。

    数据来源全是前面步骤的只读产物：
      split.json                拆仓结果（MISS 就是缺货）
      pw_hwc_now.json          帕沃尔 hwc_shop 当前状态（下单失败/未下单）
      pw_deliver_index.json    帕沃尔 deliver 索引（判「其实已出单有运单号」）
      <店>_hx_oms_status.json  恒信那份的领星 OMS 终态（**不给就查不出恒信失败单**）
    """
    day_dir = os.path.dirname(ctx["out_dir"])
    store = ctx["store"]
    # ★ 2026-11-09：两店并行时本店产物在 fulfill/<店>/，跨店文件（deliver 索引 / 缺货失败表）
    #   一律落 fulfill/ 父目录 —— 与并行前的路径完全一致，下游 push_fulfill_report.py 不用改。
    base_dir = day_dir if os.path.basename(day_dir) != "fulfill" \
        else os.path.dirname(day_dir)
    hwc = os.path.join(day_dir, "%s_hwc_now.json" % store.lower())
    idx = os.path.join(base_dir, "pw_deliver_index.json")
    hx = os.path.join(day_dir, "%s_hx_oms_status.json" % store.lower())
    out = os.path.join(base_dir, "%s_缺货与失败订单_%s.xlsx"
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
        #   新落到恒信的 16 单被判成 10 单失败）。
        # ★ 2026-11-07 修正：`--hx-shipped` 原来是一个**全体**开关，只要 hx_ship 跑过
        #   就打开 ⇒ 它没真正带进 OMS 的单也被判「查无此单」。现在按单号传。
        shipped = ctx.get("hx_shipped_ords") or set()
        if shipped:
            p = os.path.join(ctx["out_dir"], "hx_shipped.txt")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(sorted(shipped)) + "\n")
            args += ["--hx-shipped-file", p]
        else:
            log("   ℹ hx_ship 本次没跑（或没成功带单进 OMS）"
                "⇒ 恒信「OMS 搜不到」按**待发**处理，不算失败")
    else:
        log("   ⚠ 没有 %s ⇒ **恒信那份没核对**（等于没查恒信失败单）"
            % os.path.basename(hx))

    # ★ 2026-11-07 用户口径：「去领星发货数量对不上，直接归类到失败订单」。
    #   发出去 N 单、OMS 里只查到 M 单 —— 差额**不许当正常**，也不许靠"表里恰好没行"
    #   来说明；这里把台账落成 json，作为失败 sheet 的「数量对不上」输入。
    if os.path.exists(hx):
        d = _read_json(hx)
        rows = d.get("rows") or ([] if not isinstance(d, dict) else [])
        # ★★ 2026-11-11 修（BD/BAN 实测的**假阳性**）：`check_po_status` 对查不到的单
        #   也会返回一行 `{"po": ..., "accounts": []}` —— 只有 po、没有账号/状态。
        #   原来直接把这些 po 收进 `oms_pos` ⇒ 日志打「OMS 查到 48 单」，而实际 0 单进过
        #   OMS（恒信那批根本没发货）。判据必须是「有账号或状态」，不能只看有 po。
        oms_pos = {str(r.get("po") or "") for r in rows
                   if str(r.get("po") or "")
                   and (r.get("accounts") or r.get("account") or r.get("status"))}
        # ★ 2026-11-07 真机（BEL）：数量核对的基准**必须**是「本批真送进 OMS 的清单」，
        #   不是 split.json 的 HX 组。BEL 当天拆仓给了恒信 38 单，实际送进 OMS 的是
        #   19 单（其余 19 单一直没进 OMS，用户据此人工处理过）。拿 38 当分母会凭空
        #   多报 19 条「数量对不上」，等于把已经知道的情况又当新失败报一遍。
        #   基准优先级：本次送进 OMS 的清单 > 磁盘上的 hx_ok.txt > split 的 HX 组
        base = sorted(ctx.get("hx_shipped_ords") or [])
        if not base:
            p = os.path.join(ctx["out_dir"], "hx_ok.txt")
            if os.path.exists(p):
                base = _ords(p)
        base_src = "hx_ship 实际送单"
        if not base:
            base = _ords(ctx["files"].get("HX"))
            base_src = "split 的 HX 组"
        ledger = {
            "store": store,
            "base": base_src,
            "split_hx": len(base),
            "sent_to_oms": sorted(ctx.get("hx_shipped_ords") or []),
            "oms_found": sorted(oms_pos & set(base)),
            "missing_in_oms": sorted(set(base) - oms_pos),
        }
        # ★ 2026-11-07：只跑 `--only report`（hx_ship 本次没跑）时，**不许**把
        #   「本批没送出」当成失败 —— 那是重出表，不是重发货。只有本次真跑过
        #   `hx_ship`（`hx_shipped_ords` 有值）才核对"送没送"；否则只核对
        #   「本批的单在 OMS 里查得到吗」。
        if ledger["sent_to_oms"]:
            ledger["ok"] = (not ledger["missing_in_oms"]
                            and len(ledger["sent_to_oms"]) == len(base))
            ledger["checked_send"] = True
        else:
            ledger["ok"] = not ledger["missing_in_oms"]
            ledger["checked_send"] = False
        lp = os.path.join(ctx["out_dir"], "hx_count_check.json")
        with open(lp, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=1)
        log("   领星数量核对（基准＝%s）：本批 %d / 送进 OMS %d / OMS 查到 %d%s"
            % (base_src, ledger["split_hx"], len(ledger["sent_to_oms"]),
               len(ledger["oms_found"]),
               "" if ledger["ok"] else "  ⚠ 对不上，差额会进失败 sheet"))
        args += ["--hx-count-check", lp]

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


def step_notify(ctx: dict, log=print) -> dict:
    """**收尾：把两个表推 TEMU 飞书群 + 传云盘**（读表 + 外部推送）。

    用户 2026-11-07 口径：
        「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群和云盘把两个表格」

    ★ 为什么是独立一步而不是塞进 `report`：
      `report` 是「**本店**出表」，每店跑一次；用户要的是**两店都出完**之后
      一次性推一条群消息、传两个文件 —— 那是「整批完成」的语义。
      放成 pipeline 最后一步才对得上「最后完成后」。

    ★ 失败不改整链结论：发货已经做完了，只是通知没到，如实打日志即可
      （`push_fulfill_report.py` 自身也永不抛、永远 exit 0）。
    """
    script = os.path.join(_WS, "scripts", "tasks", "push_fulfill_report.py")
    if not os.path.exists(script):
        log("   ⚠ 没有 %s（跳过推送）" % script)
        return {"notify": "skipped"}
    args = [script]
    # 日期取 out_dir 往上找到的第一层 `out/<日期>/...`（并行子目录下也别退化成空日期）
    p = os.path.abspath(ctx["out_dir"])
    day = ""
    while True:
        p, leaf = os.path.dirname(p), os.path.basename(p)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", leaf or ""):
            day = leaf
            break
        if not os.path.dirname(p) or p == os.path.dirname(p):
            break
    if day:
        args += ["--date", day]
    rc, tail = _run_py(args, log=log, timeout=1200)
    lines = [x for x in (tail or "").strip().splitlines() if x.strip()]
    for ln in lines[-6:]:
        log("   " + ln[:220])
    res = {"rc": rc}
    for ln in reversed(lines):
        if "[RESULT]" in ln:
            try:
                res.update(json.loads(ln.split("[RESULT]", 1)[1]))
            except Exception:  # noqa: BLE001
                pass
            break
    if rc != 0:
        log("   ⚠ 推送/上传未全部成功（rc=%s），但发货已完成，不影响结论" % rc)
    return {"notify": res}


def _pw_page_lock(store: str, log=print):
    """帕沃尔单页锁（与 `run_pw_fulfill._pw_lock` **同一把键 `PW`**）。

    ★ 2026-11-12 用户令：「capture_pw 回落分支套 `_pw_lock`」。
    整链里帕沃尔段的每一步都经过 `run_pw_fulfill`，那里进页面之前先拿 `PW` 锁；
    只有 `capture_pw` 的**回落分支**（`pw_now.json` 不存在时自己起 `pw_session.PW()`）
    绕过了它 —— 那就是五店并发时唯一能抢页面的洞。
    """
    try:
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        import run_pw_fulfill as _rpw  # noqa: PLC0415
        return _rpw._pw_lock("capture_pw", store or "all", log=log)
    except Exception as ex:                       # noqa: BLE001
        log("   [锁] 拿不到 PW 锁（%s）→ 不加锁继续跑" % str(ex)[:120])
        import contextlib  # noqa: PLC0415
        return contextlib.nullcontext()


def step_capture_pw(ctx: dict, log=print) -> dict:
    """抓帕沃尔 hwc_shop 当前状态（只读）→ `<店>_hwc_now.json`（失败订单表要用）。

    为什么要单独一步：`hwc_shop.php` 的 `query_shop_order` 接口能一次拿到
    「状态 / 下单错误提醒 / 发货仓库 / 平台SKU」，是判「下单失败 / 未下单」的**唯一来源**
    （deliver.php 那边只有出库单，看不到下单失败原因）。

    ★ 2026-11-11 去重（用户令）：优先用帕沃尔发货流水线 `run_pw_fulfill.py --only query`
      产出的 `pw_now.json`（`step_pw_update_orders` / `step_hwc_order` 跑过就有）；
      文件不存在才回落到老的 `pw_hwc_parse` 直查（`--only capture_pw` 单跑的场景）。
    """
    now_p = ctx.get("pw_now") or os.path.join(ctx["out_dir"], "pw_now.json")
    tmp = os.path.join(os.path.dirname(ctx["out_dir"]), "%s_hwc_now.json" % ctx["store"].lower())
    if os.path.exists(now_p):
        d = _read_json(now_p)
        rows = [{"po": r.get("po"), "id": r.get("id"), "status": r.get("status"),
                 "sku": r.get("sku"), "wh": r.get("wh"), "qd": r.get("qd"), "err": r.get("err")}
                for r in (d.get("rows") or [])]
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        log("   复用帕沃尔流水线状态 %s → %d 行" % (os.path.basename(now_p), len(rows)))
        return {"hwc": tmp, "from": now_p}
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
    # ★ 2026-11-12 用户令：这条回落分支是**唯一没拿 PW 锁**的帕沃尔页面访问 ——
    #   五店并发时会和其它店的 query/hwc_order 抢同一张页面，抢到别人的列表 ⇒ 状态串味、
    #   失败单表错乱。拿同一把 `PW` 锁（与 run_pw_fulfill 同键）后再碰页面。
    try:
        with _pw_page_lock(ctx.get("store") or "", log=log):
            rc, tail = _run_py(["-c", code], log=log, timeout=1800)
    except Exception as ex:                       # noqa: BLE001
        if type(ex).__name__ != "WindowBusy":
            raise
        raise RuntimeError("帕沃尔页面被别的店占着（PW 锁等不到）⇒ 本次抓不到状态：%s"
                           % str(ex)[:200])
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
    "export_orders": step_export_orders,
    "split": step_split,
    "split_dedupe": step_split_dedupe,
    "label_shein": step_label_shein,
    "label_pw": step_label_pw,
    "label_hx": step_label_hx,
    "pw_receive": step_pw_receive,
    "pw_update_orders": step_pw_update_orders,
    "hwc_order": step_hwc_order,
    "pw_fail_scan": step_pw_fail_scan,
    "pw_fail_fix": step_pw_fail_fix,
    "pw_track": step_pw_track,
    "hx_ship": step_hx_ship,
    "capture_pw": step_capture_pw,
    "hx_oms_check": step_hx_oms_check,
    "temu_confirm": step_temu_confirm,
    "report": step_report,
    "notify": step_notify,
}


#: 需要「拆分结果 txt」才能跑的步骤（`--only` 截取时缺了 split 就要从磁盘复用）
#:
#: ★ 2026-11-11 补：帕沃尔那几步（pw_update_orders / hwc_order / pw_fail_scan /
#:   pw_fail_fix / pw_receive / pw_track）同样要本批单号。原来不在这个清单里 ⇒
#:   `--only hwc_order` 时 `ctx["files"]` 为空 ⇒ 整步"0 单，跳过"（假跑）。
NEEDS_FILES = ("label_pw", "label_hx", "hx_ship", "hx_oms_check", "capture_pw", "report",
               "pw_update_orders", "hwc_order", "pw_fail_scan", "pw_fail_fix",
               "pw_receive", "pw_track")

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
#:
#: ★★ 2026-11-09 用户收紧口径：「**BHY和BEL都拿子代理去跑，这个主会话用于监控和修复**」
#:    ⇒ 粒度从「某些步骤派子代理」升级到「**一个店一个子代理**」：BHY、BEL 各一个，并行跑，
#:    主会话不下场做业务动作，只监控进度 + 收结果 + 跑挂了就改代码让子代理重跑那一段。
#:    下面这个名单仍然有意义：它标的是"**同店内部**哪些步骤最该前置给子代理/最该并行"
#:    （帕沃尔窗口与 TEMU/领星是两套登录态），子代理内部照它分派，别自己再发明一套顺序。
DELEGATE_STEPS = ("label_pw", "hwc_order", "pw_fail_fix", "pw_receive", "pw_track")

#: 派子代理时必须交代的 7 件事（漏一条就白跑，SKILL 里有详细版）
DELEGATE_BRIEF = (
    "店名 + 该店紫鸟窗口名；禁跨店操作（一店一子代理，两店可并行）",
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
    ap.add_argument("--file", default="", help="TEMU 导出的订单 xlsx（不给＝自动跑 export_orders 导表）")
    ap.add_argument("--export-date", default="",
                    help="导表用的数据日 YYYY-MM-DD（缺省=昨天；End=这天，Start=这天-7）")
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
    # ★ 2026-11-09 用户令：「BHY和BEL都拿子代理去跑」⇒ 一店一子代理**并行**。
    #   并行时两个店同写 out/<日期>/fulfill 会互相覆盖 run.json / split.json / notify，
    #   故本店的整链产物（run/split/单号 txt/缺货失败表）一律落独立子目录：
    #     out/<日期>/fulfill/<店>/，跨店的 deliver 索引仍共用父目录。
    ap.add_argument("--subdir-by-store", action="store_true",
                    help="产物落到 out/<日期>/fulfill/<店>/（两店并行跑时必给，否则互相覆盖）")
    ap.add_argument("--skip-notify", action="store_true",
                    help="跳过 notify（两店并行时由主会话在两店都跑完后统一推送一次）")
    ap.add_argument("--agent", default="",
                    help="执行者标识：子代理跑整店整链时必填（BHY / BEL）；主会话只许用 --only 取证")
    ap.add_argument("--allow-manual-store", dest="allow_manual_store", action="store_true",
                    help="人工试跑用：允许跑 MANUAL_ONLY_STORES（BEGL）—— 它不参与自动发货")
    a = ap.parse_args()

    # ★ 2026-11-11 用户令：「BEGL 店铺不参与一整个发货流程，人工发现阶段」。
    #   BEGL 没有开放平台密钥、买不了面单、也进不了自动发货闭环 ⇒ 整链**直接拒绝**，
    #   不给它派子代理、不占窗口。要人工试跑，必须显式加 `--allow-manual-store`。
    if str(a.store).upper() in MANUAL_ONLY_STORES and not a.allow_manual_store:
        print("[HARD STOP] %s 不参与自动发货整链（用户 2026-11-11 令：人工发现阶段）。\n"
              "  ⇒ 该店没有 OpenAPI 密钥，买不了面单、进不了闭环。\n"
              "  ⇒ 真要人工试跑请显式加 --allow-manual-store。" % a.store, file=sys.stderr)
        return 2

    spec = _read_json(a.steps_config)
    steps = spec.get("steps") or []
    if not steps:
        print("[FAIL] %s 里没有 steps" % a.steps_config, file=sys.stderr)
        return 42
    try:
        picked = select_steps(steps, only=a.only, frm=a.frm, to=a.to, store=a.store)
    except ValueError as ex:
        print("[FAIL] %s" % ex, file=sys.stderr)
        return 42
    if a.skip_notify:
        # 两店并行：各店的 notify 会在「另一店还没出表」时就推一条不完整的群消息，
        # 故由主会话在两店都跑完后统一推一次。
        picked = [s for s in picked if str(s.get("name")) != "notify"]

    cfg = _read_json(a.config)
    lg = (cfg.get("logistics") or {})
    hx_carriers = ",".join((lg.get("carriers_default") or []) + (lg.get("carriers_hx_extra") or []))
    pw_carriers = ",".join(lg.get("carriers_default") or [])

    day = time.strftime("%Y-%m-%d")
    out_dir = a.out_dir or os.path.join(_HERE, "out", day, "fulfill",
                                        a.store if a.subdir_by_store else "")
    if not out_dir:
        out_dir = os.path.join(_HERE, "out", day, "fulfill")
    os.makedirs(out_dir, exist_ok=True)

    # ★ 2026-11-11 修 BUG：`ctx` 里从来没有 `confirm` 键，而 `step_label_shein`
    #   是 `if ctx.get("confirm"): args += ["--apply"]` —— 于是 SHEIN 店的买面单
    #   **永远只跑预览**（2026-11-11 实测：BAN 09:09 那轮 apply=false、0 张真买）。
    #   写操作闸门在 `gate_write` 里已挡过一次，这里把真值透传给步骤。
    ctx = {"store": a.store, "file": a.file, "out_dir": out_dir, "cfg": cfg,
           "confirm": bool(a.confirm),
           "export_date": a.export_date,
           "split_json": os.path.join(out_dir, "split.json"),
           "files": {}, "reuse_page": a.reuse_page, "prefer": a.prefer,
           "fail_round": a.fail_round,
           # ★ 2026-11-08 用户令（记死）：恒信仓名**分店不同** ——
           #   BEL = `恒信加州3仓`（带「仓」字）/ BHY = `恒信加州3`（不带「仓」字）。
           #   优先级：stores.<店>.hx_warehouse → 顶层 hx_warehouse → 兜底。
           "hx_warehouse": str(((cfg.get("stores") or {}).get(a.store) or {}).get("hx_warehouse")
                               or cfg.get("hx_warehouse") or "恒信加州3"),
           "hx_carriers": hx_carriers, "pw_carriers": pw_carriers}

    # ★ 2026-11-08：开跑前把「本店买面单允许的三仓」打一行，省得再去翻配置
    _allow = allowed_warehouses(a.store, cfg)
    if _allow:
        print("[仓名] %s 买面单允许仓库 = %s（恒信份用 %s）"
              % (a.store, " / ".join(_allow), ctx["hx_warehouse"]), flush=True)

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

    # ★★ 2026-11-09 用户收紧口径：「BHY和BEL都拿子代理去跑，这个主会话用于监控和修复」
    #   ⇒ 真正的铁律是「**一店一子代理**」。下面这段提示保留两层意思：
    #     ① 本店整条链本来就该在子代理里跑（`--store X` 由子代理执行）；
    #     ② 同店内部这些帕沃尔步骤仍按仓并行，别在子代理里串成一条。
    # ★★ 2026-11-09 二次强调「两个店铺的全流程发货各用一个子代理去跑，记住」：
    #   当天主会话亲自跑过一次 `--store BEL --confirm`，事后被点出来。这里加**硬闸门**：
    #   带 `--confirm` 的**多步整链**只许子代理跑；主会话要么给 `--agent` 说明自己是子代理，
    #   要么用 `--only <单步>` 做取证（只读/单步不算整链），否则直接拒绝。
    delegated = [str(s.get("name")) for s in picked
                 if str(s.get("name")) in DELEGATE_STEPS]
    if a.confirm and not a.agent and not a.only:
        print("\n[HARD STOP] 用户口径：「BHY 与 BEL 的全流程发货各用一个子代理去跑，"
              "主会话只监控与修复」。\n"
              "  ⇒ 整店整链（带 --confirm）必须由该店的子代理执行。\n"
              "  ⇒ 子代理请加 --agent BHY / --agent BEL；主会话要取证请用 --only <单步>。\n"
              "  SKILL：~/.agents/skills/temu-fulfill/SKILL.md → 「铁律：BHY 与 BEL 各自派一个子代理跑」",
              file=sys.stderr)
        return 42
    if a.confirm:
        log("")
        log("=" * 78)
        log("⚠ 用户 2026-11-09 口径：**%s 整店应交给它自己的子代理跑**，"
            "主会话只监控与修复（BHY、BEL 各一个子代理，可并行）。" % a.store)
        if delegated:
            log("   其中这些步骤在**帕沃尔窗口**里（同店内部按仓并行，别串行）：%s"
                % " / ".join(delegated))
        for i, b in enumerate(DELEGATE_BRIEF, 1):
            log("   %d) %s" % (i, b))
        log("   同一帕沃尔窗口只能有**一个**子代理在动；要并行就按仓拆（加州3仓 / kansas 各一路）。")
        log("   SKILL：~/.agents/skills/temu-fulfill/SKILL.md → "
            "「铁律：BHY 与 BEL 各自派一个子代理跑」/「帕沃尔仓那份必须派子代理跑」")
        log("=" * 78)
        log("")

    if not a.confirm:
        log("[闸门] 未给 --confirm → 写操作步骤会被**跳过**（只做只读步骤）")

    # ★ 2026-11-09：紫鸟窗口的调试端口是**随机分发**的，且本文件 / 各驱动各有各的探测实现
    #   （run_temu_sales.py 自己一份、ziniao_store 一份）⇒ 同一次整链可能连到**不同端口**，
    #   表现成「窗口里查不到本批订单」。这里开跑就钉一次端口，整条链（含子进程）只用它。
    #   ⚠ 必须用 `ziniao_power_map.json` 里的**窗口名**（不是店后缀 BEL/BHY）。
    if not os.environ.get("TEMU_CDP_PORT"):
        try:
            import importlib.util  # noqa: PLC0415
            import subprocess as _sp_  # noqa: PLC0415
            _win = ""
            for _m in (_read_json(os.path.join(_HERE, "data", "ziniao_power_map.json"))
                       .get("mapping") or []):
                if _m.get("power_name") == a.store:
                    _win = str(_m.get("ziniao_window_name") or "")
                    break
            _pr = subprocess.run(
                [sys.executable, "-c",
                 "import sys;sys.path.insert(0,r'%s');import ziniao_store as z;"
                 "print(z.probe_port_by_window(r'%s'))" % (os.path.join(_HERE, "lib"), _win)],
                capture_output=True, text=True, timeout=60)
            _p = (_pr.stdout or "").strip().splitlines()
            if _p and _p[-1].strip().isdigit() and int(_p[-1]) > 0:
                os.environ["TEMU_CDP_PORT"] = _p[-1].strip()
                log("[端口] %s 钉住 CDP 端口 %s（窗口 %s）" % (a.store, _p[-1], _win))
        except Exception as _e:  # noqa: BLE001
            log("[端口] ⚠ 钉端口失败（各脚本仍会自己探测）：%s" % str(_e)[:100])

    runs = []
    for st in picked:
        name = str(st.get("name") or "")
        fn = STEPS.get(name)
        if not fn:
            soft = step_is_soft(name)
            log("[步骤] %s %s 没有实现（只能是 %s）"
                % (name, "⚠" if soft else "✗", ", ".join(STEPS)))
            runs.append({"name": name, "status": "fail", "why": "no-impl",
                         "soft": soft})
            if not soft:
                break
            continue
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
            # ★ 2026-11-07：默认软 —— 只有结构性问题（库存/拆仓）才停整链。
            soft = step_is_soft(name)
            log("[步骤] %s %s %.1fs %s"
                % (name, "⚠ 失败（不阻断，继续）" if soft else "✗ 结构性问题，停链",
                   dt, str(ex)[:300]))
            runs.append({"name": name, "status": "fail", "why": str(ex)[:300],
                         "soft": soft, "seconds": dt})
            if soft:
                continue     # ★ 中间段失败继续跑，别把最后一步也丢掉
            break
        dt = round(time.time() - t0, 1)
        log("[步骤] %s ✅ %.1fs %s" % (name, dt,
                                     json.dumps(out, ensure_ascii=False)[:220]))
        runs.append({"name": name, "status": "ok", "out": out, "seconds": dt})

    s = summarize(runs)
    # ★ 2026-11-07：软失败不再"悄悄过去"，收尾必须把两件事分开说清楚 ——
    #   ① 有没有单没发成（`soft_failed`，这些已被 report 收进失败 sheet）；
    #   ② 整链是不是真跑完了（`ok`，只看有没有结构性问题把链打断）。
    #   `ok=True` 不等于"今天全发完了"，所以继续返回非 0（42）让调用方看得见。
    if s.get("soft_failed"):
        log("⚠ 以下步骤软失败（整链继续跑完，未完成的单已进失败 sheet 交人工）：%s"
            % " / ".join(s["soft_failed"]))
    print("\n[RESULT] " + json.dumps({"store": a.store, "summary": s,
                                      "out_dir": out_dir}, ensure_ascii=False))
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"store": a.store, "steps": runs, "summary": s,
                   "logs": logs[-200:]}, f, ensure_ascii=False, indent=1)
    log("已存 %s" % os.path.join(out_dir, "run.json"))
    return 0 if (s["ok"] and not s.get("soft_failed")) else 42


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    # ★ 2026-11-09：后台 Job / 重定向到文件时 stdout 被块缓冲 ⇒ 日志要等进程结束才落盘，
    #   主会话监控跑挂的店时看到的是「一行都没有」。强制行缓冲。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
