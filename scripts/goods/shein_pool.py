# -*- coding: utf-8 -*-
"""shein_pool.py — SHEIN 跨店「池子分配」（用户 2026-11-14 口径）

为什么做：四家店（BD / BAN / BS / BE）卖的是**同一池库存**，
原来「可售<=1天 → OVERWRITE 0」是逐店独立判的 ⇒ 同一款在四家店同时归零，
产品在 SHEIN 全线下线（权重掉、重新起量难），而仓里往往还有货、在途还在路上。

三层口径：

    L1 头程 ETA    该「款-套系-尺码」的在途票里最早一票 ETA <= eta_keep_days(默认7) → **不清零**，
                  改成「保活分摊」（仓里那点货按销量分给几家店，链接不死）；
                  ETA > 7 天 或**没有 ETA** → 按清零处理（接了单也发不出，用户 2026-11-14 定）
    L2 池内分摊   池子按 该店日均 ÷ 池内日均合计 分配额；每家保底 min_keep(默认2) 件；
                  线上超配额的只**降到配额**（SUB 到配额），不是清零
    L3 池子唯一   同一 「款-变体-尺码」各店配额合计 <= 仓内池子（不重复计数、不超卖）；
                  分配顺序 = **单款优先**（套装/拆解组件后分，避免套装虚增可得量）

分域（用户明确）：
    款号首位 3（儿童款）  → BE 独占，其它店不参与这个池子
    款号首位 1 / 2        → BD / BAN / BS 按销量自动排优先级

配置：config/shein_pool.json（enabled / clear_days / eta_keep_days / min_keep /
order / clear_mode / domains），改参数不用改代码。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG_PATH = ROOT / "config" / "shein_pool.json"
RULES_PATH = ROOT / "config" / "shein_stock_rules.json"

DEFAULTS = dict(
    enabled=True,
    clear_days=1.0,          # 可售 <= N 天进入「稀缺」分支
    eta_keep_days=7.0,       # 头程 ETA <= N 天 → 视为「货马上到」，不清零（保活分摊）
    min_keep=2,              # 保活底线（件/码），池子不够时自动降到 1 或 0
    min_buffer=5,            # 池子充足时：目标 = v2 需求量 + 保底件数（用户 2026-11-17）
    shein_yield=True,        # ≤1天时非希音渠道优先、SHEIN 让货（用户 2026-11-17）
    priority_hold_days=3.0,  # 先给优先渠道留 N 天的量，剩余才给 SHEIN
    priority_stores=["TM-BEL", "TM-CH", "TM-BH",
                     "TK-TAE", "TK-TSA", "TK-TH", "TK-TC"],
    # 旧键（兼容）：TEMU 三店
    temu_hold_days=3.0,
    temu_stores=["TM-BEL", "TM-CH", "TM-BH"],
    order="single_first",    # single_first（单款优先）| auto（只按销量）
    clear_mode="top1_keepalive",  # 真断货/ETA 太远时：top1_keepalive（保一家）| all_zero（全清）
    domains=[
        {"name": "adult", "stores": ["BD", "BAN", "BS"], "code_prefix": ["1", "2"]},
        {"name": "kids", "stores": ["BE"], "code_prefix": ["3"]},
    ],
)


def load_config(path: str = "") -> dict:
    cfg = dict(DEFAULTS)
    cfg["domains"] = [dict(d) for d in DEFAULTS["domains"]]
    p = Path(path) if path else CFG_PATH
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for k, v in d.items():
                if k.startswith("_"):
                    continue
                cfg[k] = v
        except Exception as e:  # noqa: BLE001
            print("[pool] ⚠ 配置读取失败（用默认值）：%s" % e)
    return cfg


def domain_of(code: str, cfg: dict):
    """款号 → (参与该池的店铺列表, 域名)。没配到 → (None, '') 表示不分区。"""
    head = str(code or "")[:1]
    for d in cfg.get("domains") or []:
        if head in [str(x) for x in (d.get("code_prefix") or [])]:
            return [str(s).upper() for s in (d.get("stores") or [])], d.get("name", "")
    return None, ""


def component_styles(path: str = "") -> set:
    """所有「拆解规则」里的组件款（如 102105-3bhl）——单款优先时它们排在后面。"""
    p = Path(path) if path else RULES_PATH
    out: set[str] = set()
    if not p.exists():
        return out
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for _, sec in (d.get("by_store") or {}).items():
        for r in sec.get("recipes") or []:
            for c in r.get("components") or []:
                if c.get("style"):
                    out.add(str(c["style"]).strip().lower())
    return out


def _style_of(key) -> str:
    try:
        return ("%s-%s" % (key[0], key[1])).lower()
    except Exception:  # noqa: BLE001
        return ""


def allocate(rows_by_store: dict, panel: dict, cfg: dict | None = None,
             components: set | None = None, eta: dict | None = None,
             verbose: bool = True, prio: dict | None = None) -> tuple[dict, list]:
    """把各店的 plan 行，按池子重新分配成一店一配额。

    rows_by_store: {店: [build() 的 decisions 行]}，每行需要
        seller / sku / now / wh / total / target / store_daily / _key
    panel: sync.load_panel() 的原始面板（取 transitAll / daily 用）
    eta: {「款-套系-尺码」: 头程预计上架天数}（sync.load_eta()）；None = 全部当「无 ETA」
    return: ({(店, 平台SKU): {target, action, note, mode}}, 池子明细列表)
    """
    cfg = cfg or load_config()
    comp = components if components is not None else component_styles()
    idx: dict[tuple, dict] = defaultdict(dict)
    meta: dict[tuple, dict] = {}
    for st, rows in rows_by_store.items():
        for r in rows:
            k = r.get("_key")
            if not k:
                continue
            idx[k][st.upper()] = r
            meta.setdefault(k, r)

    out: dict[tuple, dict] = {}
    pool_rows: list[dict] = []

    def put(store, row, target, action, note, mode):
        out[(store, row["sku"])] = dict(target=int(target), action=action, note=note,
                                        mode=mode, now=int(row.get("now") or 0),
                                        wh=row.get("wh"), seller=row.get("seller"))

    for k, per in idx.items():
        code = k[0]
        dstores, dname = domain_of(code, cfg)
        members = {s: r for s, r in per.items() if (dstores is None or s in dstores)}
        if not members:                       # 该店不在这个域（配置漏了）→ 退回全体，别把货丢掉
            members = dict(per)

        p = panel.get(k) or {}
        # 池子取「各店看到的仓内可用」的最大值：build() 里套了替代/拆解规则（apply_rules），
        # 某些款只有靠在规则里才会露出真实可用（如 203804-4SetA-XL 由 4SetB 替发 79 件），
        # 只看原始面板会误判成 0 件 → 全店清零。
        totals = [float(r.get("total") or 0) for r in members.values()]
        panel_total = float(p["total"]) if p.get("total") is not None else 0.0
        pool = max(totals + [panel_total])
        transit = float(p.get("transitAll") or 0)
        daily_all = float(p.get("daily") or 0)
        days = (pool / daily_all) if daily_all > 0 else None

        pins = {s for s, r in members.items() if r.get("pin0")}
        # 优先级：按销量自动排（本店日均降序）；单款优先时套装组件排最后
        ranked = sorted(members, key=lambda s: -_get_daily(members[s]))
        if (cfg.get("order") or "single_first") == "single_first":
            singles = [s for s in ranked if _style_of(k) not in comp]
            comps = [s for s in ranked if _style_of(k) in comp]
            order = singles + comps
        else:
            order = ranked

        mode = "normal"
        notes = []
        keep_days = float(cfg.get("eta_keep_days") or 7.0)
        eta_d = (eta or {}).get(k)
        eta_txt = ("ETA %.0f 天" % eta_d) if eta_d is not None else "无 ETA"
        if pool <= 0:
            # 用户 2026-11-16 口径：SHEIN 半托管**没有预售**，在途不能顶线上库存，
            # 一旦出单仓里没货就是超卖。所以只要仓内可用 = 0 就一律清零，
            # 在途/ETA 只用于算补货预警，不再作为「保活不清零」的依据。
            # （旧口径 inbound_keep=true 才会保留「ETA≤N 天 → 不动等货到」的行为）
            if cfg.get("inbound_keep") and eta_d is not None and eta_d <= keep_days:
                mode = "inbound_hold"
                notes.append("仓内0件、%s≤%g天 → 不清零，等货到" % (eta_txt, keep_days))
                for s in order:
                    now_i = int(members[s].get("now") or 0)
                    put(s, members[s], now_i, "不动", notes[0], mode)
            else:
                # 无 ETA 或 ETA 太远（>7 天）→ 接了单也发不出，清零（用户 2026-11-14 口径）
                mode = "stockout"
                notes.append("仓内0件 → 清零（无预售，在途%g件只算补货预警）" % transit)
                for s in order:
                    # 线上本来就是 0 的行（_action 给「不动」），不要每轮重复提交 OVERWRITE
                    put(s, members[s], 0, _action(0, int(members[s].get("now") or 0)),
                        notes[0], mode)
        elif days is not None and days <= float(cfg.get("clear_days") or 1.0):
            if eta_d is not None and eta_d <= keep_days:
                mode = "keepalive"
                notes.append("%s≤%g天 → 不清零，保活分摊" % (eta_txt, keep_days))
            elif (cfg.get("clear_mode") or "top1_keepalive") == "top1_keepalive":
                mode = "top1"
                notes.append("仓内%.2f天、%s>%g天 → 清零，只保优先级最高的一家"
                             % (days, eta_txt, keep_days))
            else:
                mode = "zero"
                notes.append("仓内%.2f天、%s → 全部清零" % (days, eta_txt))

            # ===== 渠道优先级：①TEMU ②TK ③SHEIN（用户 2026-11-17）=====
            # SHEIN 半托管没有预售，挂出去的单必须现货发，所以排在最后一档：
            # 先把 TEMU / TK 各自 hold_days 天的量按层级顺序从池子里扣掉
            #（扣不下的就少留），剩下的才给 SHEIN 三家保活；扣完 → SHEIN 清零让货。
            avail = float(pool)
            reserved = []
            if cfg.get("shein_yield", True):
                for tier in cfg.get("priority_tiers") or []:
                    name = str(tier.get("name") or "?")
                    d7 = float(((prio or {}).get(name) or {}).get(k) or 0.0)
                    hd = float(tier.get("hold_days") or cfg.get("priority_hold_days") or 0)
                    need = int(math.ceil(d7 * hd)) if hd > 0 else 0
                    take = int(min(need, max(0.0, avail)))
                    if take > 0:
                        reserved.append("%s %d件" % (name, take))
                        avail -= take
                if reserved:
                    notes.append("已按优先级留货：%s" % "、".join(reserved))
            if cfg.get("shein_yield", True) and avail < 1 and reserved:
                mode = "yield"
                notes.insert(0, "非希音渠道优先：池子%g件，留货后无余量 → SHEIN 清零让货"
                             % pool)
                for s in order:
                    put(s, members[s], 0, _action(0, int(members[s].get("now") or 0)),
                        "；".join(notes), mode)
                pool_rows.append(dict(key=k, domain=dname or "-",
                                      stores=",".join(order), pool=pool, transit=transit,
                                      daily_all=round(daily_all, 2), eta=eta_d,
                                      days=None if days is None else round(days, 2),
                                      mode=mode, note="；".join(notes)))
                continue

            if mode == "keepalive":
                quota = _spread(order, members, avail, cfg)
            elif mode == "top1":
                # 「清零，只保优先级最高的一家」：其余店一律 0（不是分摊）
                quota = {s: 0 for s in order}
                top = order[0] if order else None
                if top:
                    quota[top] = int(min(max(1, int(cfg.get("min_keep") or 2)), avail))
            else:
                quota = {s: 0 for s in order}

            if mode in ("keepalive", "top1"):
                if pins:
                    for s in pins:
                        quota[s] = 0
                for s in order:
                    q = quota.get(s, 0)
                    act = _action(q, int(members[s].get("now") or 0))
                    put(s, members[s], q, act,
                        "；".join(notes) + "；本店日均%.2f" % _get_daily(members[s]), mode)
            else:
                for s in order:
                    # 线上本来就是 0 的行（_action 给「不动」），不要每轮重复提交 OVERWRITE
                    put(s, members[s], 0, _action(0, int(members[s].get("now") or 0)),
                        "；".join(notes), mode)
        else:
            # 池子够：按优先级满足各店 v2 目标。**只加不减**（用户一贯口径）：
            # 线上已经挂着的量一律保留，只把「池子里还剩下的」按优先级加给要补的店。
            used = sum(int(members[s].get("now") or 0) for s in members)
            remaining = max(0.0, pool - used)
            over = used > pool
            if over:
                notes.append("⚠ 线上合计 %d 件 > 池子 %g 件（存量超出，本轮只加不减、不再加挂）"
                             % (used, pool))
            for s in order:
                row = members[s]
                now_i = int(row.get("now") or 0)
                if s in pins:
                    put(s, row, 0, _action(0, now_i), "手工保持 0", mode)
                    continue
                demand = float(row.get("target") or 0)
                # 用户 2026-11-17：库存充足时在「需求量」之上再加保底 5 件（min_buffer），
                # 否则像 2038-4SetB-XL 这种本店日均 0.14 的 C 类款只挂 1 件，
                # 池子里明明还剩 90+ 件却不挂。仍受池子余量 remaining 封顶。
                buf = max(0.0, float(cfg.get("min_buffer") or 0))
                want = demand + buf
                if want <= now_i:
                    # 已达/超过目标：不动（历史挂多的不回收）
                    put(s, row, now_i, "不动", "已达目标（只加不减）", mode)
                    continue
                add = int(min(want - now_i, remaining))
                t = now_i + add
                note = "池子%.0f件、线上已挂%d件 → 补%d件（需求%d + 保底%d）" % (
                    pool, used, add, int(demand), int(buf))
                if add < want - now_i:
                    note = "池子余量不足：需求%d+保底%d → 只补%d件" % (int(demand), int(buf), add)
                if over:
                    note = "线上合计已超池子 → 不追加"
                remaining -= add
                put(s, row, t, _action(t, now_i), note, mode)

        pool_rows.append(dict(key=k, domain=dname or "-",
                              stores=",".join(order), pool=pool, transit=transit,
                              daily_all=round(daily_all, 2), eta=eta_d,
                              days=None if days is None else round(days, 2),
                              mode=mode, note="；".join(notes) or
                              "池子充足，按销量分配"))
    if verbose:
        cnt = defaultdict(int)
        for v in out.values():
            cnt[v["action"]] += 1
        print("[pool] 分配 %d 条：%s" % (len(out), dict(cnt)), flush=True)
    return out, pool_rows


def _get_daily(row) -> float:
    try:
        return float(row.get("store_daily") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _action(target: int, now: int) -> str:
    if target == now:
        return "不动"
    if target <= 0:
        return "清零"
    if target > now:
        return "补货"
    return "降量"


def _spread(order: list, members: dict, pool: float, cfg: dict) -> dict:
    """保活分摊：先给每家 min_keep（池子不够就每家有 1 件，再不够就只给头一家），
    余量按该店日均占比分。只有「本店有销量 或 线上还挂着货」的店参与。"""
    active = [s for s in order if _get_daily(members[s]) > 0 or int(members[s].get("now") or 0) > 0]
    if not active:
        active = order[:1]
    quota = {s: 0 for s in order}
    if not active:
        return quota
    want = int(cfg.get("min_keep") or 2)
    if pool >= want * len(active):
        base = want
    elif pool >= len(active):
        base = 1
    else:
        base = 0
    for s in active:
        quota[s] = base
    left = int(pool) - sum(quota.values())
    if left > 0:
        weights = {s: max(_get_daily(members[s]), 0.01) for s in active}
        total_w = sum(weights.values()) or 1.0
        acc = {s: quota[s] + left * weights[s] / total_w for s in active}
        for s in active:
            quota[s] = int(math.floor(acc[s]))
        rem = int(pool) - sum(quota.values())
        for s in sorted(active, key=lambda x: -(acc[x] - math.floor(acc[x])))[:max(0, rem)]:
            quota[s] += 1
    if base == 0 and active:
        top = active[0]
        quota[top] = min(1, int(pool))
    return quota
