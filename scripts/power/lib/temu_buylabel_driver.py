# -*- coding: utf-8 -*-
"""lib/temu_buylabel_driver.py — TEMU 美国站「购买面单」driver（批量在线下单）。

2026-09-12 现场验证固化（9 单实跑成功）。链路：
    ① 订单列表「订单号」框贴多单（空格分隔）→ 查询（确认都在待发货）
       → **自己后台开批量页**：原生 CDP `Target.createTarget(background=true)` + 重连枚举
         online-shipping.html?parent_order_sn_list=[...]&open_type=OnlineShip（P0）
       兜底（P1）：列表全选 → 点「批量在线下单」→ 按 URL 判据找新页 → 找不到就重连一次再枚举
       逃生口（P2）：入口 --reuse-page <URL 子串|序号> 直接复用已开着的批量页，不新开页
    ② 填顶部 重量(=单件重×数量，批量填充前先铺基准单件重) + 体积 + 发货仓库
    ③ 勾选子订单 → 批量填充（顶部自动清空、勾选自动取消）
    ④ 物流服务 一键填充 →（已有服务时先「覆盖填充」）→ 配送成本优先 → 确认填充
    ⑤ 确认下单 → 「共 N 个订单（N 个包裹），确认下单？」→ 确认
       → 上门取件弹窗逐行选最早可选日期 → 已确认，继续下单（成功后标签页自动关闭）
    ⑥ 订单列表：待发货逐单「去手动确认」→ 确认
    ⑦ 成功判据：待发货/已发货该单出现「运单号」

现场踩坑（已固化，勿再踩）：
    - 行内「物流服务」在行内「发货仓库」未选前是 disabled（灰）；顺序必须 仓库→物流服务
    - 仓库下拉项是 li/[role=option]，文本**首行**=仓库名、次行是备注；按整串精确匹配必失败
    - 物流服务下拉项**不是** li/[role=option]，是普通 div（文本前缀=服务名）
    - 上门取件日期框用 JS click 打不开日历，必须**真实鼠标事件**（走原生 CDP
      `Input.dispatchMouseEvent`；用 Playwright 的 page.mouse.click 就得配 bring_to_front，会抢前台）
    - 「确认」按钮在弹窗里，页面上还有「确认下单」；按精确文本取**最后一个**可见按钮
    - 已有物流服务时点「一键填充」会先弹「全部包裹已填写物流商及服务，是否覆盖填充？」

不抢前台（2026-09-14 用户反馈"窗口会置顶"后定）：
    - **禁止 `bring_to_front()`**；**禁止 `ctx.new_page()`**（Playwright 前台开页）→ 一律走
      `new_tab_background()`（原生 CDP background=true + 重连枚举）或复用现存页
    - 坐标点击走 `cdp_click()`（原生 CDP 派发），不依赖前台/焦点
    - `_orders_page_ready()`：已在订单页就不再重复 goto（重复导航＝导航竞态来源）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse

import cdp_transport          # target-level CDP 传输层（替代 Playwright connect_over_cdp，红线 RL-013）
from cdp_transport import ForeignPageError   # 「拒改写复用页」信号（2026-10-27）

# ---------------------------------------------------------------- 常量 / 选择器
# 全部为 2026-09-12 实测值；页面改版时先改这里，再跑 tests + 真机 fill 复验。
_SEL_ORDERS_FILTER_PH = "最多100个"       # 订单号/子订单号输入框 placeholder 片段
_ROW_BLOCK_MARK = "订单号：PO-"            # 批量页每单区块的锚文本
_TXT_UNCHECKED = "未勾选"

#: 域内数据目录（所有口径/登记表都在这里，AI 只改 json）。**必须在文件头定义**：
#: 弹窗登记表（本文件靠前）与路线表（靠后）都要用它。
_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data"))

# ---------------------------------------------------------------- 纯函数（可单测）

def _url_is_orders(u: str) -> bool:
    """是否真落在订单列表页：**host + path** 判（禁止整串子串包含）。

    为什么必须 host+path：认证页/授权页 URL 的 query 里带 `redirectUrl=...%2Fmmsos%2Forders.html`
    （以及本流程自己的 export-order 变体），用「整串里有没有 orders.html」判会把**未登录的认证页**
    当成订单页选中（TEMU 每日导出 2026-09-16 实测同族坑：选页取到残留登录 tab → 后续全部误判）。
    判据同 `auth_temu_driver._url_is_seller_page` 的口径（host + path 在 /mmsos/ 下）。
    """
    try:
        p = urllib.parse.urlparse(str(u or ""))
        return p.netloc == "agentseller-us.temu.com" and "/mmsos/orders" in (p.path or "").lower()
    except Exception:
        return False


def load_config(path: str) -> dict:
    """读口径 json（数据外置；AI 只改 json）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _norm_warehouse(raw: str, cfg: dict) -> str:
    """派送订单表「出库仓库」→ 在线下单页发货仓库选项文本。"""
    key = (raw or "").strip()
    m = cfg.get("warehouse_map", {})
    if key in m:
        return m[key]
    # 兜底：去掉空格/全半角差异后模糊匹配
    for k, v in m.items():
        if k.replace(" ", "") == key.replace(" ", ""):
            return v
    return cfg.get("warehouse_default", key)


def _order_weight(qty, cfg: dict) -> float:
    """重量 = 单件重 × 数量（磅，保留 weight_decimals 位）。

    单件重口径在 `data/temu_buylabel.json` 的 `weight_per_unit_lb`（用户 2026-09-24 更正：默认 **1.1**；多件＝基准×数量）；
    这里只留「配置缺失时的兜底」，值与口径同值，免得静默漂移。
    """
    per = float(cfg.get("weight_per_unit_lb", 1.1))
    nd = int(cfg.get("weight_decimals", 2))
    try:
        q = int(float(str(qty).strip()))
    except Exception:
        q = 1
    return round(per * max(q, 0), nd)


def fmt_num(v, decimals: int = 2) -> str:
    return ("%." + str(decimals) + "f") % float(v)


def _s(v) -> str:
    """单元格 → 干净字符串；NaN/None/nat 一律归空串（否则 pandas 会把「总计」行变成 'nan' 混进订单号）。"""
    if v is None:
        return ""
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def parse_delivery_orders(xlsx_path: str) -> list[dict]:
    """解析「派送订单」xlsx（微信/仓库给的表）→ [{order_no, warehouse_raw, qty, product, sku}]。

    表头不固定列序，按列名定位；「总计」行（参考单号空）自动丢弃。
    """
    import pandas as pd  # 局部导入：让纯逻辑单测不依赖 pandas 之外的东西
    df = pd.read_excel(xlsx_path, sheet_name=0, header=0, dtype=str)
    cols = {str(c).strip(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    c_no = col("参考单号", "订单号", "parentOrderSn")
    c_wh = col("出库仓库", "仓库", "发货仓库")
    c_qty = col("数量", "件数")
    c_prod = col("产品名称", "品名")
    c_sku = col("SKU", "sku")
    if c_no is None:
        raise ValueError("派送订单表缺少「参考单号」列，实际列: %s" % list(df.columns))

    out = []
    # ★ 2026-10-09 修：**单号为空的「附加包裹行」要归并到上一单的数量上**，不能丢。
    #   为什么：多件套/组合装的一单在派送表里会占**多行**——首行写单号+数量，其后若干行
    #   **单号为空**但同样写数量。旧实现（`if not no: continue`）把附加行整行丢弃 ⇒
    #   plan 的 qty 只算了首行 ⇒ 重量少算 ⇒ 平台报「差异过大」。
    #   实证：BEL 2026-10-09 那批，5 单（…07596296207993907 / …07889207114872097 /
    #   …07893416952952134 / …07702300439673275 / …08055036163191045）表里各有 2 行 × 1 件，
    #   旧实现算成 1 件（1.10 lb），正确是 2 件（2.20 lb）。
    #   判据（用户同日口径）：「文件已经把 10件套/4件套/6件套 拆开了，读表格就知道几件套」。
    _last = None
    for _, r in df.iterrows():
        no = _s(r.get(c_no))
        raw_qty = _s(r.get(c_qty)) if c_qty else ""
        if not no or not re.match(r"^[A-Za-z0-9\-]{4,}$", no):
            # 附加包裹行：并入上一单的数量。
            # ⚠ 只认**有效数字**的「数量」——「总计」行（参考单号空、数量也空/None）绝不能被当成 1 件
            #   并进去（2026-10-09 踩过：`_s(x) or "1"` 把 None 变成 1，令末单凭空多 1 件）。
            if _last is not None and re.match(r"^\d+$", str(raw_qty or "").strip()):
                try:
                    _last["qty"] = str(int(_last["qty"]) + int(raw_qty))
                except (TypeError, ValueError):
                    pass
            continue
        rec = {
            "order_no": no,
            "warehouse_raw": _s(r.get(c_wh)) if c_wh else "",
            "qty": raw_qty or "1",
            "product": _s(r.get(c_prod)) if c_prod else "",
            "sku": _s(r.get(c_sku)) if c_sku else "",
        }
        out.append(rec)
        _last = rec
    return out


def build_plan(rows: list[dict], cfg: dict) -> list[dict]:
    """下单计划：每条 = 订单号 + 重量(磅) + 发货仓库(选项文本) + 原始信息。"""
    dec = int(cfg.get("weight_decimals", 2))
    plan = []
    for r in rows:
        w = _order_weight(r.get("qty", 1), cfg)
        plan.append({
            "order_no": r["order_no"],
            "qty": r.get("qty", "1"),
            "weight": w,
            "weight_txt": fmt_num(w, dec),
            "warehouse_raw": r.get("warehouse_raw", ""),
            "warehouse": _norm_warehouse(r.get("warehouse_raw", ""), cfg),
            "sku": r.get("sku", ""),
        })
    return plan


def batch_url(cfg: dict, order_nos: list[str]) -> str:
    """构造「批量在线下单」页 URL。

    现场契约（2026-09-12 从已开出来的批量页实读，勿固化别的参数）：
        https://agentseller-us.temu.com/mmsos/online-shipping.html
            ?parent_order_sn_list=%5B%22PO-...%22%5D&open_type=OnlineShip&...
    其余参数（refer_page_*/_x_sessn_id）为平台追踪参数，自开页不需要。
    """
    q = urllib.parse.quote(json.dumps([str(o) for o in order_nos], separators=(",", ":")), safe="")
    return "%s?parent_order_sn_list=%s&open_type=OnlineShip" % (cfg["shipping_url"], q)


def count_packages_in_url(url: str) -> int:
    """结果页 URL 的 `package_sn_list`（或批量页 `parent_order_sn_list`）里有几个包裹/订单（纯函数，可单测）。

    用途（TB-033）：窗口里可能同时存在**别人的历史结果页**（本机 2026-09-15 实测：另一会话同时在
    买 1 单面单），"取最新结果页"会静默读到**别的任务**的包裹号 ⇒ 拿它当本任务的完成证据就全错了。
    """
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query)
    except Exception:
        return 0
    for k in ("package_sn_list", "parent_order_sn_list"):
        v = (q.get(k) or [""])[0]
        n = len(re.findall(r"PK-\d+|PO-[\d-]+", v))
        if n:
            return n
    return 0


def _url_stamp(u: str) -> int:
    r"""结果页 `refer_page_id=\d+_<ts>_` 里的时间戳（越新越大；没有则 0）。"""
    m = re.search(r"refer_page_id=\d+_(\d+)_", str(u or ""))
    return int(m.group(1)) if m else 0


def _url_same_target(cur: str, want: str) -> bool:
    """目标页判据：**同路径且关键参数一致**（防复用到「共 0 个订单」的旧空页）。

    2026-09-14 实测踩坑：`new_tab_background` 只比路径前缀 → 复用了上一轮遗留的空批量页
    （页头「共 0 个订单」），随后仓库下拉无选项 → fill exit=42。

    2026-09-15 补（TB-032）：`want` **不带 query** 时（订单列表页 / 「在线下单」页这类固定页）
    旧实现 `bool(keys)` 恒 False ⇒ **永远复用不到**，每次动作都新开一个标签页；本机 zclaw 对
    「刚 createTarget 出来的新页」枚举有延迟 ⇒ 新开的页找不到 → record 直接报
    「后台开页后仍未枚举到该页」，还堆积出 3 张重复的在线下单页。故 want 无参数时按路径复用。
    """
    if not cur:
        return False
    cu, wu = urllib.parse.urlparse(cur), urllib.parse.urlparse(want)
    if cu.path != wu.path:
        return False
    cq, wq = urllib.parse.parse_qs(cu.query), urllib.parse.parse_qs(wu.query)
    if not wq:
        return True                     # want 是"固定页"（无参数）→ 同路径即可复用
    keys = [k for k in wq if "sn_list" in k] or list(wq.keys())
    return all(cq.get(k) == wq[k] for k in keys)


def pick_reuse_index(urls: list, spec: str) -> int:
    """`--reuse-page` 选择器 → pages 下标（-1 = 没匹配到）。

    纯数字 = 第 N 个 online-shipping 页（1 起）；否则 = URL 子串匹配（如订单号片段）。
    """
    idx = [i for i, u in enumerate(urls) if "online-shipping" in (u or "")]
    if not idx:
        return -1
    s = (spec or "").strip()
    if s.isdigit():
        k = int(s)
        return idx[k - 1] if 1 <= k <= len(idx) else -1
    if not s:
        return -1
    for i in idx:
        if s in (urls[i] or ""):
            return i
    return -1


def judge_filled(state: dict, order_nos: list, page_url: str = "") -> tuple:
    """纯函数闸门：判断已开的批量页是否「填好了、可确认下单」。返回 (ok, reasons)。

    用途：`action=place_only` 下单前必须过这道闸门（工程控制，不靠 LLM 自觉）——
    页面没填好就点「确认下单」＝白提交或下错单，宁可 HARD STOP。
    """
    st = state or {}
    reasons = []
    try:
        head = int(str(st.get("head") or 0) or 0)
    except Exception:
        head = 0
    want = len(order_nos or [])
    if want and head < want:
        reasons.append("页面订单数 %s < 目标 %d（页头「共 N 个订单」读不到或不对）" % (st.get("head") or "?", want))
    if not (st.get("weights") or []):
        reasons.append("行内重量全为空（未填单或页面已刷新）")
    if len(st.get("picks") or []) < 2:
        reasons.append("发货仓库/物流服务未选全（值为「请选择」的输入项 %d < 2）"
                       % len(st.get("picks") or []))
    if not st.get("confirm_btn"):
        reasons.append("页面上找不到「确认下单」按钮")
    if order_nos:
        url = urllib.parse.unquote(page_url or "")
        # **2026-09-19 修**：URL 里**一个单号都没有**是**正常**的——平台改版后用服务端 key 开页
        # （`online-shipping.html?big_array_key=…&open_type=OnlineShip`），URL 里本就不带单号。
        # 旧判据在这里硬拦 ⇒ 填好的页也被拒（实测 place_only 报"页面 URL 不含这些单号（可能串单）"）。
        # 新判据：**URL 里出现了单号**（任何 `PO-…`）才做串单核对；一个都没有 ⇒ 不拦。
        if re.findall(r"PO-\d+", url):
            miss = [o for o in order_nos if o not in url]
            if miss:
                reasons.append("页面 URL 带了单号但不含这些（可能串单）：%s" % ", ".join(miss))
    return (not reasons), reasons


_RECORD_RE = re.compile(
    r"^(TK-\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\S+)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)")


def parse_record_rows(texts: list) -> list:
    """把「在线下单记录」的行文本解析成结构化记录（纯函数，便于单测）。

    行样（实测）：`TK-0858483830292533163 2026-09-12 20:09:09 稍后发货 1 1 0 0 全部成功 查看详情 打印物流单据`
    列序：任务号 / 下单时间 / 发货类型 / 下单总数 / 成功数 / 失败数 / 转自发货数 / 任务状态
    """
    out, seen = [], set()
    for t in texts or []:
        m = _RECORD_RE.match((t or "").strip())
        if not m:
            continue
        task = m.group(1)
        if task in seen:
            continue
        seen.add(task)
        out.append({"task": task, "at": "%s %s" % (m.group(2), m.group(3)), "ship_type": m.group(4),
                    "total": int(m.group(5)), "success": int(m.group(6)),
                    "fail": int(m.group(7)), "converted": int(m.group(8)), "status": m.group(9)})
    return out


def judge_records(rows: list, latest_only: bool = True) -> tuple:
    """平台侧完成判据（用户 2026-09-12 定）：**下单总数 == 成功数**（且失败数=0）。

    只读「在线下单记录」表即可，**不需要点进详情**。返回 (ok, reasons, checked)。
    latest_only=True 时只判最近一条任务（表按时间倒序，第一行即最近）。
    """
    rows = rows or []
    if not rows:
        return False, ["没读到「在线下单记录」任何任务行（抽屉未打开或选择器失效）"], []
    checked = rows[:1] if latest_only else rows
    reasons = []
    for r in checked:
        if r.get("status") and "成功" not in str(r["status"]):
            reasons.append("任务 %s 状态=%s（非全部成功）" % (r["task"], r["status"]))
        if r.get("total") != r.get("success"):
            reasons.append("任务 %s 下单总数 %s ≠ 成功数 %s" % (r["task"], r["total"], r["success"]))
        if r.get("fail"):
            reasons.append("任务 %s 失败数 %s > 0" % (r["task"], r["fail"]))
    return (not reasons), reasons, checked


_SERVICE_QUOTE_RE = re.compile(r"预估\s*[$￥]?\s*([\d.]+)")


def parse_service_quote(text: str):
    """从物流服务选项文本里取报价（如 `UPS, Ground saver 预估$5.41; 2-6 个工作日送达` → 5.41）。"""
    m = _SERVICE_QUOTE_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def pick_page_size_option(options: list, need: int, cap: int = 500):
    """从订单列表「每页」下拉里挑值 —— **一律取最大（≤cap），不做"够用就好"的判断**。

    2026-09-17 用户定（原话：「直接拉到500，你不需要判断别的」）：每页直接拉满。
    这样任何 ≤cap 的批次都能一页装下，也就不存在"后 N 单落在页外"这回事。
    口径：`batch.page_size`（实测选项 5/10/20/50/100/500）；`need` 保留仅为兼容与留痕，
    **不再参与选择**。取不到数字选项 → None（不猜、不写死）。
    """
    vals = []
    for o in options or []:
        t = str((o or {}).get("text") or "").strip()
        if t.isdigit() and int(t) > 0:
            vals.append(int(t))
    if not vals:
        return None
    vals = sorted(set(vals))
    capped = [v for v in vals if v <= int(cap)]
    return str(max(capped or vals))


def page_size_status_ok(status: str, need: int, default: int = 20) -> tuple:
    """闸门（纯函数）：本批 `need` 单能不能**安全地**一页显示完 → (ok, 说明)。

    2026-09-17 用户定：本批 >20 单时，若「每页」没调成功就**不许硬跑**——否则 `全选`
    只勾当前页 ⇒ 只买 20 单面单（部分下单），而且账面上像是跑完了。
    状态串来自 `set_list_page_size()`：`already:N` / `set:N` / `no-sc:*` / `no-option:*` / `no-viewport:x,y`
    规则：① 能读出有效页大小 → 必须 ≥ need；
          ② 读不出（no-sc / no-option / no-viewport）→ 只有 need ≤ 页面默认值时才放行。
    """
    s = str(status or "")
    eff = None
    if s.startswith("already:") or s.startswith("set:"):
        v = s.split(":", 1)[1].strip()
        if v.isdigit():
            eff = int(v)
    if eff is not None:
        if eff >= int(need):
            return True, "每页=%d ≥ 本批 %d 单" % (eff, need)
        return False, "每页只到 %d，装不下本批 %d 单（页面选项上限不够）→ 请拆表再跑" % (eff, need)
    if int(need) <= int(default):
        return True, "页大小没调到（%s），但本批 %d 单 ≤ 页面默认 %d 单，安全" % (s or "-", need, default)
    return False, ("页大小没调成功（%s），而本批 %d 单 > 页面默认 %d 单 → "
                   "`全选` 只会勾当前页、只买到一页的量，拒绝硬跑" % (s or "-", need, default))


def page_size_suspect(found_rows: int, need: int, status: str, default: int = 20) -> bool:
    """缺单时判断「是不是每页太小导致后 N 单没渲染」（纯函数，可单测）。

    2026-09-17 实测：`no-sc` 有两种截然不同的起因，**必须用行数证据区分**——
      · 列表 0 行  → 登录态失效/页面没就绪（由 `diagnose_missing_orders` 分诊，别抢它的活）；
      · 行数正好 = 页面默认值（20）而本批更多 → **后 N 单落在页外**，这才是页大小问题。
    只有后者才按 page_size_too_small 报，避免把"要重新授权"误报成"页大小不足"。
    """
    fr, nd = int(found_rows), int(need)
    if fr <= 0 or fr >= nd:
        return False
    if fr < int(default):
        return False              # 行数连默认页都没填满 → 不是被截断，是真缺单
    return not page_size_status_ok(status, nd, default)[0]


def pick_service_by_cost(options: list, exclude: str = "") -> tuple:
    """**按报价从低到高**挑可用物流服务（用户 2026-09-12 定：失败单重下时换渠道＝换更便宜的）。

    options: [{"text": "...", "disabled": bool}, ...]（同 `_JS_SERVICE_OPTIONS` 的结构）
    exclude: 本次刚刚失败的那个服务名（前缀匹配则跳过）
    返回 (选中的服务文本, 报价)；没有可用/都没有报价 → (None, None)
    """
    cands = []
    for o in options or []:
        if o.get("disabled"):
            continue
        t = str(o.get("text") or "").strip()
        if not t:
            continue
        if exclude and t.startswith(exclude):
            continue
        price = parse_service_quote(t)
        if price is None:
            continue
        cands.append((price, t))
    if not cands:
        return (None, None)
    cands.sort(key=lambda x: x[0])
    return (cands[0][1], cands[0][0])


def triage_failure(reason: str, cfg: dict) -> dict:
    """按失败行「错误原因」定重下策略（纯函数）→ {id, strategy, why}。

    规则在 `cfg["failure_retry"]["triage"]`（数据）：子串命中，**命中串最长者优先**（与条目顺序无关）；
    空 `match` 那条＝兜底。策略：switch_carrier / warehouse_then_cost / cost_asc。
    """
    fr = cfg.get("failure_retry") or {}
    rules = fr.get("triage") or []
    low = str(reason or "").lower()
    fb = None
    hits = []          # (命中关键词长度, 规则)
    for r in rules:
        m = r.get("match") or []
        if not m:
            fb = r
            continue
        for kw in m:
            k = str(kw).lower()
            if k and k in low:
                hits.append((len(k), r))
    if hits:
        # **最长匹配优先**（2026-09-21）：关键词可能互相包含（如「无法支持该地址」⊃「地址」），
        # 只按条目顺序会让宽关键词抢走具体关键词 ⇒ 用命中串长度定，结果是确定的、与书写顺序无关。
        hits.sort(key=lambda x: -x[0])
        r = hits[0][1]
        return {"id": r.get("id"), "strategy": r.get("strategy") or "cost_asc",
                "why": r.get("_为什么") or ""}
    if fb:
        return {"id": fb.get("id"), "strategy": fb.get("strategy") or "cost_asc",
                "why": fb.get("_为什么") or ""}
    return {"id": "fallback", "strategy": str(fr.get("select") or "cost_asc"), "why": ""}


def choose_reorder_service(options: list, failed_service: str, strategy: str, cfg: dict) -> dict:
    """按策略挑重下用的物流服务（纯函数）→ {service, price, how}；挑不到 service=None。

    switch_carrier＝取 carrier_order 里第一家不等于刚失败那家的可用项；warehouse_then_cost＝按
    no_carrier_fallback.services 顺序；其余/挑不到 → pick_service_by_cost（报价升序、跳过刚失败的）。
    """
    fr = cfg.get("failure_retry") or {}
    avail = [o for o in (options or []) if not o.get("disabled") and str(o.get("text") or "").strip()]

    def _by_cost():
        s, p = pick_service_by_cost(options, exclude=failed_service)
        return {"service": s, "price": p, "how": "cost_asc"}

    def _brand(s):
        return str(s or "").split(",")[0].strip().lower()

    if strategy == "switch_carrier":
        failed_brand = _brand(failed_service)
        for pref in (fr.get("carrier_order") or []):
            if failed_brand and _brand(pref) == failed_brand:
                continue          # 同一家承运商＝必复现，跳过
            for o in avail:
                t = str(o.get("text") or "").strip()
                if t.startswith(str(pref)) or (not failed_brand and True and _brand(t) == _brand(pref)):
                    return {"service": t, "price": parse_service_quote(t), "how": "switch_carrier:%s" % pref}
        return _by_cost()
    if strategy == "warehouse_then_cost":
        for pref in ((cfg.get("no_carrier_fallback") or {}).get("services") or []):
            for o in avail:
                t = str(o.get("text") or "").strip()
                if t.startswith(str(pref)):
                    return {"service": t, "price": parse_service_quote(t),
                            "how": "warehouse_then_cost:%s" % pref}
        return _by_cost()
    return _by_cost()


def parse_service_from_text(text: str, cfg: dict) -> str:
    """从失败行整行文本里认出「本次用的是哪家物流服务」（纯函数）。

    判据：拿 `logistics.carrier_options`（现取）里的物流商名逐个去文本里找，命中即返回其
    「<物流商>, <服务名>」形态串（截到报价/分隔符为止）；认不出返回空串。
    """
    t = str(text or "")
    for brand in ((cfg.get("logistics") or {}).get("carrier_options") or []):
        i = t.find(str(brand))
        if i < 0:
            continue
        seg = t[i:i + 40]
        m = re.match(r"([A-Za-z0-9\u4e00-\u9fa5().\- ]+?)(?:\s*[\$￥]|\s*报价|\s*不可用|$)", seg)
        return (m.group(1).strip() if m else seg.strip())[:40]
    return ""


# 运单号判据（2026-09-15 实测修正，TB-027）：**不是所有承运商都是 UPS**——本批 16 单里
# GOFO=`GFUS01072983618176`、SwiftX=`SWX326200000173528347`、SpeedX=`SPXABE008892609150017649`、
# YANWEN=`YWE00001541157130`；旧正则 `1Z[0-9A-Z]{16}` 会把它们全判成「缺运单号」（false negative）。
# 新判据＝字母数字混合、长度 ≥10、且不是内部号（TK-/PK-/PO-）；显式排除纯数字/纯字母。
# ⚠ token 正则**不能要求首字符是字母**：UPS 运单号是 `1Z…`（首位是数字）。
# 故先粗取「10–32 位字母数字连续串」做候选，再用 is_waybill_token() 判「字母数字混合 + 非内部号」——
# 纯数字（订单/包裹内部号、时间戳）与纯字母（承运商名、服务名）都会被判掉。
_WB_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9-])([A-Z0-9]{10,32})(?![A-Za-z0-9-])")
_PKG_NO_RE = re.compile(r"(PK-\d+)")
_INTERNAL_PREFIXES = ("TK-", "PK-", "PO-")


#: 「这行的物流服务等于**没有**」的判据（纯函数，可单测）。
#: ⚠ 不能只判空字符串：物流商不支持该地址时，平台会在服务框里显示**占位文案**而不是留空。
#:   用户 2026-10-09 原话：「如果弹窗是无物流渠道的字样，那你就要跳到对应的订单改成 Kansas 仓，
#:   物流也会刷新成空，你要重新选择下拉框的第一个物流就可以。」
#:   旧口径 `not (r["service"] or "").strip()` 只认空 ⇒ 占位文案会**漏过**、不去兜底。
_SVC_MISSING_PAT = ("无物流", "无渠道", "暂无可用", "无可用", "不支持", "不可用", "请选择", "-")


def svc_missing(service: str) -> bool:
    """物流服务是否「视为没有」（空 / 占位文案）。纯函数，可单测。"""
    s = str(service or "").strip()
    if not s:
        return True
    if s in ("-", "--"):
        return True
    return any(p in s for p in _SVC_MISSING_PAT)


def is_waybill_token(tok: str) -> bool:
    """单个 token 是不是运单号（纯函数，可单测）：字母数字混合 + 长度 ≥10 + 非内部号。"""
    t = str(tok or "").strip()
    if len(t) < 10 or t.startswith(_INTERNAL_PREFIXES):
        return False
    return any(c.isdigit() for c in t) and any(c.isalpha() for c in t)


def find_waybill(text: str, prefer_before: str = "") -> str:
    """从文本里取运单号（纯函数，可单测）。

    优先只在 `prefer_before` 标签**之前**那一段里找——行文本里运单号总在该行最前面
    （`<运单号> 包裹号：PK-… 订单号：PO-…`），这样可避免把行尾的其它字母数字串当运单号。
    """
    s = str(text or "")
    segs = []
    if prefer_before and prefer_before in s:
        segs.append(s[:s.index(prefer_before)])
    segs.append(s)
    for seg in segs:
        for m in _WB_TOKEN_RE.finditer(seg):
            if is_waybill_token(m.group(1)):
                return m.group(1)
    return ""


def parse_package_rows(texts: list) -> list:
    """把详情页的包裹行文本解析成**逐包裹**记录 [{waybill, package}, …]（纯函数，可单测）。

    行样（实测）：`1Z16E50BYW67644665 包裹号： PK-0858483556352133163 订单号： PO-… ×1 重量：…`
    **只认真正的"行"**：必须带「包裹号」或「订单号」标签——页面里"物流单号"单元格会单独出现一个裸
    运单号元素，若不滤掉会被误当成"第二个缺号包裹"（2026-09-14 实测踩到）。按出现顺序去重。
    """
    out, seen = [], set()
    for t in texts or []:
        s = " ".join(str(t or "").split())
        if not s or ("包裹号" not in s and "订单号" not in s):
            continue
        w = find_waybill(s, prefer_before="包裹号")
        m_p = _PKG_NO_RE.search(s)
        if not w and not m_p:
            continue
        rec = {"waybill": w, "package": m_p.group(1) if m_p else ""}
        key = (rec["waybill"], rec["package"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


# 详情页「整页文本」兜底（2026-09-15 实测行样；页面按 DOM 逐元素取行会随改版失效）：
# `<运单号> 包裹号： PK-… 订单号： PO-…`；运单号整段可选——认不出号也要**留下这行**（缺号如实报）。
_PKG_BLOCK_RE = re.compile(r"(?:(?<![A-Za-z0-9-])([A-Z0-9]{10,32})\s+)?"
                           r"包裹号：\s*(PK-\d+)\s*订单号：\s*(PO-[\d-]+)")


def parse_packages_from_text(text: str) -> list:
    """从**整页文本**抽逐包裹记录（纯函数，可单测）：三元组 (运单号, 包裹号, 订单号)。

    运单号认不出时**保留该包裹行**（waybill 留空）——缺号要如实报出来，不能把行丢掉当"没有包裹"。
    """
    s = " ".join(str(text or "").split())
    out, seen = [], set()
    for m in _PKG_BLOCK_RE.finditer(s):
        wb = m.group(1) or ""
        if not is_waybill_token(wb):
            wb = ""
        rec = {"waybill": wb, "package": m.group(2), "order_no": m.group(3)}
        key = (rec["waybill"], rec["package"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def check_packages(task: str, pkgs: list) -> tuple:
    """**完成指标（用户 2026-09-14 定，批量同样适用）**：任务号 1 个 + **每个包裹**都要有自己的运单号与包裹号。

    pkgs: [{"waybill": "1Z…", "package": "PK-…"}, …]（至少 1 个）
    返回 (ok, missing, detail)：missing 里逐条写明是哪个包裹缺哪个号；一个包裹缺号即整体未完成。
    """
    missing = []
    if not str(task or "").strip():
        missing.append("任务号 TK-…")
    pkgs = pkgs or []
    if not pkgs:
        missing.append("整批包裹（一个包裹行都没读到）")
        return False, missing, []
    detail = []
    for i, p in enumerate(pkgs, 1):
        w = str((p or {}).get("waybill") or "").strip()
        k = str((p or {}).get("package") or "").strip()
        row = {"index": i, "waybill": w, "package": k, "ok": bool(w and k)}
        if not w:
            missing.append("第 %d 个包裹缺运单号 1Z…" % i)
        if not k:
            missing.append("第 %d 个包裹缺包裹号 PK-…" % i)
        detail.append(row)
    return (not missing), missing, detail


def check_three_numbers(facts: dict) -> tuple:
    """**单包裹**场景的三号判据（任务号 + 运单号 + 包裹号）；**批量请用 `check_packages()`**。

    返回 (ok, missing)，missing 用可读标签（`任务号 TK-…` / `运单号 1Z…` / `包裹号 PK-…`）。
    """
    f = facts or {}
    missing = [label for key, label in (("task", "任务号 TK-…"), ("waybill", "运单号 1Z…"),
                                        ("package", "包裹号 PK-…"))
               if not str(f.get(key) or "").strip()]
    return (not missing), missing


def parse_todo_count(text: str) -> int:
    """从待办卡文案取数字：`即将逾期未发货 11` → 11（取不到给 0）。纯函数。"""
    m = re.search(r"(\d+)\s*$", str(text or ""))
    return int(m.group(1)) if m else 0


def split_overdue_rows(rows: list) -> dict:
    """把「即将逾期未发货」待办列表按**页面事实**分三类（纯函数，2026-09-30 用户指的那条流程）。

    判据只用页面上看得见的（不猜、不查库）：
      · `confirm`    ：该行有运单号 **且** 行内有「去手动确认」→ 直接确认发货
      · `need_label` ：该行没有运单号 → **还没买面单**，得先在线下单；对它点「去手动确认」没有意义
      · `other`      ：其余（有号但按钮不在本行等）→ 报出来，不硬点
    """
    confirm, need_label, other = [], [], []
    for r in rows or []:
        ord_no = str((r or {}).get("ord") or "").strip()
        if not ord_no:
            continue
        track = str((r or {}).get("track") or "").strip()
        if track and (r or {}).get("manual"):
            confirm.append(ord_no)
        elif not track:
            need_label.append(ord_no)
        else:
            other.append(ord_no)
    return {"confirm": confirm, "need_label": need_label, "other": other,
            "counts": {"confirm": len(confirm), "need_label": len(need_label), "other": len(other)}}


def overdue_rows_to_delivery(rows: list) -> tuple:
    """「缺面单」的页面行 → 派送表行（参考单号/出库仓库/数量）；返回 (delivery, skipped)。

    返回的列口径与 `parse_delivery_orders()` 对齐；仓库写**页面原样**文本（如「加州三仓」），
    交给 `warehouse_map` 去映射——映射表里没有的仓库名由上游按 D2 停下报用户，不在这里猜。
    页面没显示「库存扣减仓库」的行**不猜默认仓**，进 `skipped` 让人看一眼。
    """
    delivery, skipped = [], []
    for r in rows or []:
        ord_no = str((r or {}).get("ord") or "").strip()
        if not ord_no:
            continue
        if str((r or {}).get("track") or "").strip():
            continue                       # 有运单号＝已买过面单，不属这一步
        wh = str((r or {}).get("warehouse") or "").strip()
        qty = str((r or {}).get("qty") or "").strip() or "1"
        if not wh:
            skipped.append({"ord": ord_no, "why": "页面没显示「库存扣减仓库」"})
            continue
        delivery.append({"参考单号": ord_no, "出库仓库": wh, "数量": qty})
    return delivery, skipped


def signals_consistent(sig: dict) -> tuple:
    """分诊信号**自洽判据**（纯函数）：这组读数可不可信？返回 (ok, why)。

    2026-09-30 实测踩到的假信号：`want=11` 而 `pending/shipped/cancelled/skippable` 全 0、`not_found` 也 0
    ——「声称 11 单全都见着了，却哪儿都没数到」，自相矛盾 ⇒ 路线被取成 `no_orders`（"没有可买的"）。
    真实成因是**列表读回没生效**（每页控件读不到 `no-sc` + 读到的是别的行），不是订单真的没了。

    不可信形态：
      ① `not_found==0` 且 `pending+skippable==0` 而 `want>0`（自相矛盾，见上）；
      ② `not_found==want` 且三页签合计 0 行（一行都没匹配到，多半是筛选/渲染没生效）。
    """
    s = sig or {}
    want = int(s.get("want") or 0)
    if want <= 0:
        return True, ""
    pend = int(s.get("pending") or 0)
    skip = int(s.get("skippable") or 0)
    nf = int(s.get("not_found") or 0)
    if nf == 0 and (pend + skip) == 0:
        return False, "not_found=0 却 pending+skippable=0（声称都见着了、实际一个没数到）"
    if nf == want and (pend + skip) == 0:
        return False, "三页签一行都没匹配到（筛选/渲染可能没生效）"
    return True, ""


def is_transport_error(ex) -> bool:
    """是不是 **CDP/传输层** 错误（不是页面/业务错误）。

    2026-09-30 实测：`place_only` 点完「已确认，继续下单」后报
    `server rejected WebSocket connection: HTTP 500` —— 工具判失败，而平台侧 **11/11 已下单成功**
    （= 2026-09-28 记过的「断连后假失败」同类，第三次）。这类错误**不能直接下失败结论**，
    必须回读平台状态再判——否则要么误报失败、要么人工重跑造成重复下单。
    """
    t = ("%s %s" % (type(ex).__name__, ex)).lower()
    for k in ("websocket", "http 500", "connectionclosed", "connectionreset", "targetclosed",
              "target closed", "brokenpipe", "connectionaborted", "cdperror"):
        if k in t:
            return True
    return False


def write_delivery_xlsx(rows: list, path: str) -> str:
    """把派送表行写成 xlsx（整链 `pipeline` 的输入）；无行返回 ""。"""
    import pandas as pd
    if not rows:
        return ""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    pd.DataFrame(rows, columns=["参考单号", "出库仓库", "数量"]).to_excel(path, index=False)
    return path


def plan_summary(plan: list[dict]) -> str:
    lines = ["共 %d 单：" % len(plan)]
    for p in plan:
        lines.append("  %s | 数量=%s → 重量=%s lb | 仓库 %s→%s" % (
            p["order_no"], p["qty"], p["weight_txt"], p["warehouse_raw"] or "-", p["warehouse"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- JS 片段
_JS_SET_TOP = r"""([wt, l, w, h]) => {
  const set = (el, v) => { const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
    s.call(el, v); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); };
  const pick = ph => Array.from(document.querySelectorAll("input[placeholder='" + ph + "']")).filter(e => e.offsetParent !== null);
  const W=pick('重量'), L=pick('长'), D=pick('宽'), H=pick('高');
  if (W.length) set(W[0], wt); if (L.length) set(L[0], l); if (D.length) set(D[0], w); if (H.length) set(H[0], h);
  return {n: [W.length, L.length, D.length, H.length]};
}"""

_JS_OPEN_TOP_WAREHOUSE = r"""() => {
  const el = Array.from(document.querySelectorAll("input[placeholder='发货仓库']")).filter(e=>e.offsetParent!==null)[0];
  if (!el) return 'no-input';
  el.scrollIntoView({block:'center'}); el.click(); return 'ok';
}"""

_JS_PICK_OPTION_FIRSTLINE = r"""(text) => {
  const os = Array.from(document.querySelectorAll('[role=option],li')).filter(e => {
    if (e.offsetParent === null) return false;
    return (e.innerText||'').trim().split('\n')[0].trim() === text;
  });
  if (!os.length) return 'no-option:' + text;
  os.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  os[0].click(); return 'ok';
}"""

_JS_CHECK_SUBORDER = r"""() => {
  const hdr = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim() === '子订单信息' && e.children.length === 0);
  if (!hdr.length) return 'no-header';
  let node = hdr[0];
  for (let i = 0; i < 6 && node; i++) {
    const cb = node.querySelector && node.querySelector('label.CBX_outerWrapper_123, input[type=checkbox]');
    if (cb) { cb.click(); return 'ok@' + i; }
    node = node.parentElement;
  }
  return 'no-cb';
}"""

_JS_CHECK_ORDERS_ALL = r"""() => {
  const hdr = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim() === '订单信息' && e.children.length === 0);
  if (!hdr.length) return 'no-header';
  let node = hdr[0];
  for (let i = 0; i < 6 && node; i++) {
    const cb = node.querySelector && node.querySelector('label.CBX_outerWrapper_123, input[type=checkbox]');
    if (cb) { cb.click(); return 'ok@' + i; }
    node = node.parentElement;
  }
  return 'no-cb';
}"""

# ---- 遮挡弹窗检测（2026-09-19 用户：「你要能随时检测弹窗」）----------------------------
# 现场取证（BEL 订单页，2026-09-19）：全屏遮罩 `.MDL_mask_123`（fixed 929x883, z-index 1000）
# + 外壳 `[data-testid="beast-core-modal"]`（`.MDL_outerWrapper_123 MDL_alert_123`），
# 内容是「以下待办任务需要尽快处理」（21笔物流信息异常 / 3个售前售后工单，各带「去处理」）；
# 关闭键 `[data-testid="beast-core-icon-close"]`（**无 class，只能按 testid 认**）。
# **危害**：全屏遮罩盖住页面 ⇒ 筛选框/按钮点不到，表面症状是 `no-input` / 点不到按钮，
# 极易被误诊成"页面没就绪"。故所有交互前都必须先过这道检测。
_JS_POPUP_PROBE = r"""() => {
  const seen = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 40 && r.height > 40 && st.display !== 'none'
        && st.visibility !== 'hidden' && parseFloat(st.opacity || '1') > 0.05;
  };
  const modals = Array.from(document.querySelectorAll('[data-testid="beast-core-modal"]')).filter(seen);
  const modal = modals[0]
             || Array.from(document.querySelectorAll('[class*=MDL_outerWrapper_]')).filter(seen)[0];
  const mask = Array.from(document.querySelectorAll('[class*=MDL_mask_]')).filter(seen)[0];
  if (!modal && !mask) return JSON.stringify({present: false});
  const inner = modal ? (modal.querySelector('[class*=MDL_inner_]') || modal) : null;
  // 取**整段文本**（折叠空白、截断）：判据用"包含"，不依赖"第一行"
  //（2026-09-19 实测：按 innerText 取第一行只拿到「以下」，导致已知弹窗被判成未知 → 起点闸门误拦）
  const text = ((inner ? inner.innerText : '') || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const cb = modal ? modal.querySelector('[data-testid="beast-core-icon-close"]') : null;
  let rect = null;
  if (cb) { const r = cb.getBoundingClientRect(); rect = [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; }
  // **近似关闭点**（2026-09-21 补）：有些**通知类弹窗只有右上角 ✕、没有 testid 关闭键**，
  // 光靠 testid 找不到关闭钮。这里按**内容区右上角**算一个候选点
  // （口径与老的 `_JS_NOTICE_CLOSE_POS` 完全一致：inner.right-26 / inner.top+26）。
  // 注意：它只是**候选点**，只有弹窗登记表里显式写了 `"close": "approx"` 的条目才会用它——
  // 未登记的弹窗绝不会拿这个点乱点（别把这条改成自动生效）。具体是哪些弹窗见登记表。
  let approxAt = null;
  if (!cb && inner) {
    const ir = inner.getBoundingClientRect();
    if (ir.width > 40 && ir.height > 40) approxAt = [Math.round(ir.right - 26), Math.round(ir.top + 26)];
  }
  // 按钮文本（2026-09-21 补）：弹窗登记表要能按「弹窗里有哪些按钮」认人——
  // 光靠标题认不出「以下 N 个订单无法发货，是否剔除并继续？」这类**要决策**的确认框。
  const btns = (modal ? Array.from(modal.querySelectorAll('button,[role=button]')) : [])
      .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 12);
  return JSON.stringify({present: true, text: text, title: text.slice(0, 80),
                         hasClose: !!cb, closeAt: rect, approxAt: approxAt, buttons: btns,
                         maskOnly: !modal, count: modals.length});
}"""

#: 弹窗关闭键**是否落在视口外**：2026-09-21 实测关闭键在 x=1157、视口仅 1074 → 点不到，
#: 弹窗不走还挡住取件日期框。故这一步要能判出来，交给路线 `modal_offscreen` → `widen`。
_JS_POPUP_OFFSCREEN = r"""() => {
  const seen = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 40 && r.height > 40
        && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden'; };
  const modal = Array.from(document.querySelectorAll('[data-testid="beast-core-modal"]')).filter(seen)[0]
             || Array.from(document.querySelectorAll('[class*=MDL_outerWrapper_]')).filter(seen)[0];
  const mask = Array.from(document.querySelectorAll('[class*=MDL_mask_]')).filter(seen)[0];
  if (!modal && !mask) return JSON.stringify({present: false, offscreen: false});
  const cb = modal ? modal.querySelector('[data-testid="beast-core-icon-close"]') : null;
  if (!cb) return JSON.stringify({present: true, hasClose: false, offscreen: false,
                                  innerW: window.innerWidth});
  const r = cb.getBoundingClientRect();
  const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
  return JSON.stringify({present: true, hasClose: true, x: x, y: y, innerW: window.innerWidth,
                         offscreen: (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight)});
}"""

#: **清障步**：按「在视口内的可用控件」关掉挡页面的提示类弹窗（关闭图标 / 安全确认按钮白名单）。
#: 2026-09-21：订单页的提示弹窗会把遮罩铺满，表现成 `no-input`（筛选框读不到），看似页面没就绪。
_JS_CLEAR_BLOCKER = r"""() => {
  const seen = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 40 && r.height > 40
        && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden'; };
  const modal = Array.from(document.querySelectorAll('[data-testid="beast-core-modal"]')).filter(seen)[0]
             || Array.from(document.querySelectorAll('[class*=MDL_outerWrapper_]')).filter(seen)[0];
  const mask = Array.from(document.querySelectorAll('[class*=MDL_mask_]')).filter(seen)[0];
  if (!modal && !mask) return JSON.stringify({done: true, why: 'no-popup'});
  const inView = r => r.left >= 0 && r.top >= 0 && r.right <= window.innerWidth && r.bottom <= window.innerHeight;
  const ASK = ['知悉了', '我已知悉', '知道了', '我知道了', '关闭'];
  const ic = modal ? modal.querySelector('[data-testid="beast-core-icon-close"]') : null;
  if (ic) {
    const r = ic.getBoundingClientRect();
    if (inView(r)) return JSON.stringify({done: false, kind: 'close',
      x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
  }
  const btns = Array.from((modal || document).querySelectorAll('button,[role=button],span,div'))
    .filter(e => e.children.length <= 1 && ASK.indexOf((e.innerText || '').trim()) >= 0);
  for (const b of btns) {
    const r = b.getBoundingClientRect();
    if (inView(r)) return JSON.stringify({done: false, kind: 'ack', text: (b.innerText || '').trim(),
      x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
  }
  return JSON.stringify({done: true, why: 'blocked-no-usable-control'});
}"""

# ---------------------------------------------------------------- 弹窗登记表（数据外置）
# 2026-09-21 用户：「TEMU 总是有弹窗，但之前的经验总是没办法固化」。
# 两个病根：① 弹窗知识**写死在代码里**（原先就一个常量元组，每多认识一个弹窗＝改代码）；
# ② 未知弹窗只 HARD STOP + 截图，**不登记**（下次遇到同一个还要人肉翻日志、再改代码）。
# 落地（经验回流 C1/C2）：现实层遇到的弹窗 → `data/temu_buylabel_popups.json` 一条
# （act＝做什么 / when＝怎么认 / _why＝为什么），未知弹窗由 driver **自动登记**进
# `unknown[]`（按标题去重、累加 hits、留首次/最近时间 + 截图 + 网址 + 所处步骤）。
# ⇒ 「固化经验」变成**改一行数据**，而不是回头考古 / 改代码。
DEFAULT_POPUPS_PATH = os.path.join(_DATA_DIR, "temu_buylabel_popups.json")

#: 登记表缓存（按 path+mtime）：dismiss_popups 在每次点击前都跑，不能每次都读盘。
_REG_CACHE = {"path": "", "mtime": None, "data": None}


def popups_path(path: str | None = None, cfg: dict | None = None) -> str:
    """弹窗登记表路径：`cfg["popups_file"]` 优先（便于测试指向临时文件），否则域内缺省。"""
    raw = str(((cfg or {}).get("popups_file")) or "").strip()
    if raw:
        return raw if os.path.isabs(raw) else os.path.normpath(raw)
    return path or DEFAULT_POPUPS_PATH


def load_popup_registry(path: str | None = None, cfg: dict | None = None) -> dict:
    """读弹窗登记表。缺文件/坏文件 → 返回空表（＝一条都不认识，行为与旧版一致：停下取证）。"""
    p = popups_path(path, cfg)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = -1.0
    c = _REG_CACHE
    if c["data"] is not None and c["path"] == p and c["mtime"] == mt:
        return c["data"]
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("登记表不是对象")
    except Exception:  # noqa: BLE001
        d = {"popups": [], "unknown": []}
    d.setdefault("popups", [])
    d.setdefault("unknown", [])
    _REG_CACHE.update({"path": p, "mtime": mt, "data": d})
    return d


def save_popup_registry(d: dict, path: str | None = None, cfg: dict | None = None) -> bool:
    """写回登记表（先写 .tmp 再 replace：中途被打断也不会留下半截 JSON）。失败只返回 False。"""
    p = popups_path(path, cfg)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        _REG_CACHE.update({"path": "", "mtime": None, "data": None})   # 让下次读到新内容
        return True
    except Exception:  # noqa: BLE001
        return False


def match_popup(title: str, text: str = "", buttons=None, d: dict | None = None) -> dict | None:
    """这个弹窗在不在登记表里。返回那条登记（dict）或 None（＝不认识）。纯函数，可单测。

    判据全在数据里（`when`）：`title_contains[]`（含任一）／`text_regex`／`buttons_have[]`（按钮文本全有）。
    **空判据不许命中**（否则一条空规则会吃掉所有弹窗）。命中按表内顺序取第一条。
    """
    blob = ("%s %s" % (title or "", text or "")).strip()
    btns = [str(b) for b in (buttons or [])]
    for e in (d or {}).get("popups") or []:
        w = e.get("when") or {}
        inc = w.get("title_contains") or []
        need = w.get("buttons_have") or []
        rx = w.get("text_regex")
        if not inc and not need and not rx:
            continue
        if inc and not any(str(k) in blob for k in inc):
            continue
        if need and not all(any(str(k) in b for b in btns) for k in need):
            continue
        if rx:
            try:
                if not re.search(str(rx), blob):
                    continue
            except re.error:
                continue
        return e
    return None


def classify_popup(title: str, text: str = "", buttons=None, d: dict | None = None) -> str:
    """该弹窗该干嘛：`dismiss`（点关闭键）／`click`（点 when.button）／`stop`（停下）／`unknown`。

    兼容旧签名 `classify_popup(title)`（旧行为只认「已知提示 → dismiss，其余 unknown」，本函数是其超集）。
    """
    reg = d if d is not None else load_popup_registry()
    e = match_popup(title, text, buttons, reg)
    if not e:
        return "unknown"
    return str(e.get("act") or "stop")


def bump_popup_hits(entry: dict, d: dict, path: str | None = None, cfg: dict | None = None) -> dict:
    """已登记的弹窗每遇到一次就累加 hits + 记 last_seen（哪些弹窗真在发生，看数据就知道）。"""
    entry["hits"] = int(entry.get("hits") or 0) + 1
    entry["last_seen"] = time.strftime("%Y-%m-%d")
    save_popup_registry(d, path, cfg)
    return entry


def register_unknown_popup(title: str, text: str = "", buttons=None, url: str = "", step: str = "",
                           shot: str = "", path: str | None = None, cfg: dict | None = None) -> dict:
    """**未知弹窗自动登记**（去重按标题、累加 hits）→ 下次它就在表里＝待确认。

    这就是"经验固化"的入口：不用谁记得回来写文档，遇到就落一条；确认动作只要改一行数据。
    """
    d = load_popup_registry(path, cfg)
    t = (title or text or "（无标题弹窗）").strip()[:120]
    today = time.strftime("%Y-%m-%d")
    for u in d.setdefault("unknown", []):
        if str(u.get("title") or "") == t:
            u["hits"] = int(u.get("hits") or 0) + 1
            u["last_seen"] = today
            for k, v in (("url", url), ("step", step), ("shot", shot)):
                if v:
                    u[k] = str(v)[:200]
            save_popup_registry(d, path, cfg)
            return u
    rec = {"title": t, "text": (text or "")[:200],
           "buttons": [str(b)[:40] for b in (buttons or [])][:12],
           "first_seen": today, "last_seen": today, "hits": 1,
           "url": str(url or "")[:200], "step": str(step or ""), "shot": str(shot or ""),
           "_待确认": "确认后挪进 popups[] 并给 act（dismiss/click/stop）与 when；这是**改数据**，不用改代码"}
    d["unknown"].append(rec)
    save_popup_registry(d, path, cfg)
    return rec


_JS_CLICK_BUTTON = r"""(text) => {
  const bs = Array.from(document.querySelectorAll('button')).filter(e => e.offsetParent !== null && (e.innerText||'').replace(/\n/g,'').trim() === text);
  if (!bs.length) return 'no-btn:' + text;
  bs[bs.length-1].scrollIntoView({block:'center'});
  bs[bs.length-1].click(); return 'ok';
}"""

_JS_CLICK_LINK_NEAR = r"""([text, near]) => {
  const links = Array.from(document.querySelectorAll('a')).filter(e => (e.innerText||'').trim() === text
      && e.offsetParent !== null && e.parentElement && (e.parentElement.innerText||'').indexOf(near) >= 0);
  if (!links.length) return 'no-link';
  links[0].click(); return 'ok';
}"""

_JS_CLICK_RADIO = r"""(text) => {
  const rows = Array.from(document.querySelectorAll('[data-testid=beast-core-radio],label,span,div'))
      .filter(e => (e.innerText||'').trim() === text && e.offsetParent !== null);
  if (!rows.length) return 'not-found:' + text;
  rows.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  rows[0].click(); return 'ok';
}"""

_JS_BATCH_HEAD = r"""() => {
  const m = (document.body.innerText||'').match(/在线下单\s*共\s*(\d+)\s*个订单/);
  return m ? m[1] : '';
}"""

_JS_READ_FILLED = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const ins = Array.from(document.querySelectorAll('input')).filter(vis);
  const val = ph => ins.filter(e => ((e.placeholder||'').trim()) === ph).map(e => (e.value||'').trim());
  const body = document.body.innerText || '';
  const head = (body.match(/共\s*(\d+)\s*个订单/)||[])[1] || '';
  const freight = (body.match(/预估总运费：\|?\s*(\$[\d.]+)/)||[])[1] || '';
  const buttons = Array.from(document.querySelectorAll('button')).filter(vis).map(e => (e.innerText||'').trim());
  return {
    head: head,
    freight: freight,
    weights: val('重量').filter(Boolean),
    dims: val('长').filter(Boolean).concat(val('宽').filter(Boolean), val('高').filter(Boolean)),
    picks: val('请选择').filter(Boolean),
    warehouse: (val('请选择')[0] || ''),
    service: (val('请选择')[1] || ''),
    confirm_btn: buttons.indexOf('确认下单') >= 0,
    buttons: buttons.filter(t => t.indexOf('确认下单') >= 0 || t.indexOf('批量填充') >= 0),
  };
}"""

_JS_CLICK_ROW_DETAIL = r"""(task) => {
  // 只读入口：点该任务行的「查看详情」（会新开结果页标签；不进任何"重新下单"）
  // 2026-09-15 修（TB-029）：行定位改判「**最小的**同时含本任务 TK 与「查看详情」的可见元素」。
  // 旧写法按"任一祖先含目标 TK"命中——祖先爬到整张表时，别的任务行的「查看详情」也会被判成命中，
  // 结果点到**别的任务**的结果页（本次实测踩到）。
  const vis = e => e && e.offsetParent !== null;
  let row = null;
  Array.from(document.querySelectorAll('*')).forEach(e => {
    if (!vis(e)) return;
    const t = (e.innerText || '');
    if (t.indexOf(task) < 0 || t.indexOf('查看详情') < 0 || t.length > 400) return;
    if (!row || t.length < (row.innerText || '').length) row = e;
  });
  if (!row) return 'no-row';
  const cand = Array.from(row.querySelectorAll('a,span,button,div'))
    .filter(e => vis(e) && (e.innerText || '').trim() === '查看详情' && e.children.length <= 2);
  if (!cand.length) return 'no-btn';
  cand[0].click();
  return 'ok';
}"""

_JS_DETAIL_VIEW = r"""() => {
  // 读「在线下单结果/详情」页：成功/失败包裹、逐包行、以及页面上有哪些可用按钮（含"重新下单"是否存在）
  const vis = e => e && e.offsetParent !== null;
  const body = (document.body.innerText || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ');
  const btns = new Set();
  document.querySelectorAll('button,a,span,div').forEach(e => {
    if (!vis(e) || e.children.length > 2) return;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 18 && /重新下单|立即预约|打印|取消|申诉|导出|下载|查看全部/.test(t)) btns.add(t);
  });
  const heads = [];
  document.querySelectorAll('th').forEach(th => { const t = (th.innerText || '').trim(); if (t) heads.push(t); });
  const rows = [];
  Array.from(document.querySelectorAll('*')).forEach(e => {
    const t = (e.innerText || '').trim();
    // 2026-09-15 修（TB-027）：不再只认 `1Z…`——GOFO/SwiftX/SpeedX/YANWEN 的运单号都不是 1Z 开头；
    // 而 `1Z…`（UPS）首位是**数字**，故首字符类必须是 [A-Z0-9]，不能只写 [A-Z]。
    if (/^[A-Z0-9]{10,32}\s/.test(t) && vis(e) && t.length < 500
        && (t.indexOf('包裹号') >= 0 || t.indexOf('订单号') >= 0)) rows.push(t.replace(/\s+/g, ' '));
  });
  return {
    summary: (body.match(/\d+\s*个包裹[^，。；]{0,12}/g) || []).slice(0, 4),
    fails: (body.match(/(失败|不可用|无法|异常|需人工)[^，。；]{0,40}/g) || []).slice(0, 10),
    heads: heads.slice(0, 20),
    buttons: Array.from(btns),
    can_reorder: Array.from(btns).some(t => t.indexOf('重新下单') >= 0),
    packages: Array.from(new Set(rows)).slice(0, 40),
    tracks: (body.match(/[A-Z0-9]{10,32}|PK-\d+/g) || []).slice(0, 40),
    // 整页文本兜底：DOM 逐行取不到时用 parse_packages_from_text() 解析（页面改版也不至于取不到号）
    body_text: body.slice(0, 20000),
  };
}"""

#: 结果页的失败包行 + 该行「物流服务」下拉坐标。**首次真机核验点**（2026-09-14 为手工点击）；
#: 认不出时 `reorder_failed()` 报错+落图，不静默跳过。
_JS_RESULT_FAIL_ROWS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const out = [];
  Array.from(document.querySelectorAll('table tr')).forEach((tr, i) => {
    if (!vis(tr)) return;
    const t = (tr.innerText || '').replace(/\s+/g, ' ').trim();
    if (!/PO-\d+-\d+/.test(t)) return;
    if (!/(失败|不可用|无法|异常)/.test(t)) return;
    const sel = tr.querySelector('[data-testid=beast-core-select-header],[class*=ST_head]');
    let at = null;
    if (sel) { const r = sel.getBoundingClientRect();
      at = [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; }
    out.push({index: i, order: (t.match(/PO-\d+-\d+/) || [''])[0], text: t.slice(0, 300), selAt: at});
  });
  return out;
}"""

_JS_OPEN_RESULT_ROW_SERVICE = r"""(ord) => {
  const vis = e => e && e.offsetParent !== null;
  const trs = Array.from(document.querySelectorAll('table tr')).filter(tr => vis(tr)
      && (tr.innerText || '').indexOf(ord) >= 0);
  if (!trs.length) return 'no-row';
  const sel = trs[0].querySelector('[data-testid=beast-core-select-header],[class*=ST_head]');
  if (!sel) return 'no-select';
  const r = sel.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
}"""

_JS_PICKUP_STATE = r"""() => {
  // 读结果页上的取件预约状态与「立即预约」按钮
  const vis = e => e && e.offsetParent !== null;
  const body = (document.body.innerText || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ');
  const m = body.match(/取件服务[^。]{0,80}/);
  const booked = Array.from(document.querySelectorAll('*')).some(e => vis(e) && /已预约|预约成功|预约完成/.test((e.innerText||'').trim()));
  const btn = Array.from(document.querySelectorAll('button,a,span,div'))
    .filter(e => vis(e) && (e.innerText||'').trim() === '立即预约' && e.children.length <= 2);
  return {snippet: m ? m[0] : '', booked: booked, has_book_btn: btn.length > 0,
          tracks: (body.match(/1Z[0-9A-Z]{16}|PK-\d+/g) || []).slice(0, 6)};
}"""

_JS_CLICK_BOOK_PICKUP = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const cand = Array.from(document.querySelectorAll('button,a,span,div'))
    .filter(e => vis(e) && (e.innerText||'').trim() === '立即预约' && e.children.length <= 2);
  if (!cand.length) return 'no-btn';
  cand[cand.length - 1].click();
  return 'ok';
}"""

_JS_DIALOG_DUMP = r"""() => {
  // 只读：把当前弹层/抽屉里的文字、输入框、可点按钮全 dump 出来（用于识别取件预约界面）
  const vis = e => e && e.offsetParent !== null;
  const dlgs = Array.from(document.querySelectorAll('[role=dialog],[class*=modal],[class*=Modal],[class*=drawer],[class*=Drawer]'))
    .filter(vis).map(e => (e.innerText || '').replace(/\s+/g, ' ').slice(0, 1500));
  const inputs = Array.from(document.querySelectorAll('input')).filter(vis)
    .map(e => ({ph: (e.placeholder || '').trim(), val: (e.value || '').trim()}));
  const btns = new Set();
  document.querySelectorAll('button,a,span,div').forEach(e => {
    if (!vis(e) || e.children.length > 2) return;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 18 && /确认|取消|预约|提交|确定|关闭|下一步|选择/.test(t)) btns.add(t);
  });
  const body = (document.body.innerText || '').replace(/\s+/g, ' ');
  return {dialogs: dlgs.slice(0, 3), inputs: inputs.slice(0, 12), buttons: Array.from(btns),
          dates: (body.match(/\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日|星期[一二三四五六日]|非营业日|最早[^，。；]{0,16}/g) || []).slice(0, 14)};
}"""

_JS_PICKUP_RESULT = r"""() => {
  // 读点「立即预约」之后的结果：成功 / 平台拒绝（如"该地址无法进行预约，可前往 UPS 官网"）
  const vis = e => e && e.offsetParent !== null;
  const body = (document.body.innerText || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ');
  const fail = body.match(/预约失败[^。]{0,120}/);
  const okMsg = body.match(/预约成功[^。]{0,80}/);
  const booked = /已预约/.test(body) && !/未预约/.test(body);
  const btns = new Set();
  document.querySelectorAll('button,a,span,div').forEach(e => {
    if (!vis(e) || e.children.length > 2) return;
    const t = (e.innerText || '').trim();
    if (t && t.length <= 10 && /我知道了|确定|关闭|确认/.test(t)) btns.add(t);
  });
  return {fail: fail ? fail[0] : '', success: okMsg ? okMsg[0] : '', booked: booked,
          buttons: Array.from(btns),
          external: /官网|ups\.com|前往/.test(body) ? (body.match(/(可前往[^。]{0,40})/) || [''])[0] : ''};
}"""

_JS_CLICK_TEXT = r"""(t) => {
  const vis = e => e && e.offsetParent !== null;
  const cand = Array.from(document.querySelectorAll('button,a,span,div'))
    .filter(e => vis(e) && (e.innerText || '').trim() === t && e.children.length <= 2);
  if (!cand.length) return 'no-btn';
  cand[cand.length - 1].click();
  return 'ok';
}"""

_JS_SHIPPING_RECORDS = r"""() => {
  // 读「在线下单记录」表的任务行（表头：任务号/下单时间/发货类型/下单总数/成功数/失败数/转自发货数/任务状态）
  const vis = e => e && e.offsetParent !== null;
  const cands = {}; const order = [];
  Array.from(document.querySelectorAll('*')).forEach(el => {
    const t = (el.innerText || '').trim();
    if (!/^TK-\d+/.test(t) || t.length > 300 || !vis(el)) return;
    if (!/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}/.test(t)) return;
    const task = t.split(/\s+/)[0];
    if (!cands[task]) { cands[task] = []; order.push(task); }
    cands[task].push(t.replace(/\s+/g, ' '));
  });
  return order.map(k => cands[k].sort((a, b) => a.length - b.length)[0]);
}"""

_JS_OPEN_RECORD_DRAWER = r"""() => {
  // 只读：点「在线下单 下单成功 查看」那条提示里的「查看」，打开下单记录抽屉（不进任何详情）
  const vis = e => e && e.offsetParent !== null;
  const hits = [];
  Array.from(document.querySelectorAll('*')).forEach(e => {
    if (e.children.length > 2) return;
    if (!/^(查看|查看详情)$/.test((e.innerText || '').trim()) || !vis(e)) return;
    let up = e, ctx = '';
    for (let i = 0; i < 5 && up; i++) {
      ctx = (up.innerText || '').replace(/\s+/g, ' ');
      if (ctx.indexOf('下单成功') >= 0) break;
      up = up.parentElement;
    }
    if (ctx.indexOf('下单成功') >= 0) hits.push(e);
  });
  if (!hits.length) return 'no-toast-link';
  hits[hits.length - 1].click();
  return 'clicked';
}"""

_JS_OPEN_RECORD_DRAWER_BANNER = r"""() => {
  // 第二条入口（TB-028）：**「在线下单」页**（online-shipment.html）顶部横幅
  // 「在线下单 下单中/下单成功/下单失败N 查看」——这条横幅长期在，不像 orders.html 的提示 toast 会消失。
  const vis = e => e && e.offsetParent !== null;
  const RE = /在线下单[\s\S]{0,40}(下单中|下单成功|下单失败)/;
  const cands = [];
  Array.from(document.querySelectorAll('*')).forEach(e => {
    if (e.children.length > 1) return;
    if ((e.innerText || '').trim() !== '查看' || !vis(e)) return;
    let up = e, ctx = '';
    for (let i = 0; i < 6 && up; i++) {
      ctx = (up.innerText || '').replace(/\s+/g, ' ');
      if (RE.test(ctx)) break;
      up = up.parentElement;
    }
    if (RE.test(ctx)) cands.push(e);
  });
  if (!cands.length) return 'no-banner';
  cands[cands.length - 1].click();
  return 'clicked';
}"""

_JS_ROWS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const out = []; const seen = {};
  Array.from(document.querySelectorAll('*')).forEach(el => {
    if (el.children.length > 0) return;
    const t = (el.innerText||'').trim();
    const m = t.match(/^订单号：(PO-\d+-\d+)/);
    if (!m) return;
    const ord = m[1];
    if (seen[ord]) return;
    let node = el, box = null;
    for (let i = 0; i < 10 && node; i++) {
      const ins = node.querySelectorAll ? Array.from(node.querySelectorAll('input[type=text]')).filter(vis) : [];
      if (ins.length >= 5) { box = node; break; }
      node = node.parentElement;
    }
    if (!box) return;
    seen[ord] = 1;
    const ins = Array.from(box.querySelectorAll('input[type=text]')).filter(vis).map(e => e.value || '');
    out.push({ord: ord, weight: ins[0] || '', length: ins[1] || '', width: ins[2] || '', height: ins[3] || '',
              warehouse: ins[4] || '', service: ins[ins.length-1] || ''});
  });
  return out;
}"""

_JS_SET_ROW_WEIGHT = r"""([ord, val]) => {
  const els = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim().indexOf('订单号：' + ord) === 0);
  if (!els.length) return 'not-found';
  els.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  let node = els[0];
  for (let i = 0; i < 8 && node; i++) {
    const ins = node.querySelectorAll ? Array.from(node.querySelectorAll('input[type=text]')) : [];
    if (ins.length >= 4) {
      const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      set.call(ins[0], val);
      ins[0].dispatchEvent(new Event('input',{bubbles:true}));
      ins[0].dispatchEvent(new Event('change',{bubbles:true}));
      return 'ok:' + ins[0].value;
    }
    node = node.parentElement;
  }
  return 'no-group';
}"""


#: 逐单读「件数（行内 ×N）+ 重量」，供 `weight_fix` 判「重量 != 基准 × 件数」。
#: 为什么另起一个而不复用 `_JS_ROWS`：`_JS_ROWS` 不返回件数，而多件单的判据**必须**用件数
#: （2026-10-09 用户口径：重量要和件数成倍数）。
#: 以 `<tbody>` 为单位：一个订单可能含**多个包裹行**，件数取各行 ×N 之和；
#: 该单所有「重量」输入框都要等于 基准 × 件数之和（实测：同单多包裹行重量一致）。
_JS_ROWS_QTY = r"""() => {
  const out = [];
  document.querySelectorAll('tbody').forEach(tb => {
    const t = (tb.innerText || '').replace(/\s+/g, ' ');
    const m = t.match(/PO-\d+-\d+/);
    if (!m) return;
    const qtys = Array.from(t.matchAll(/×\s*(\d+)/g)).map(x => parseInt(x[1], 10));
    const qty = qtys.reduce((a, b) => a + b, 0) || 1;
    const wins = Array.from(tb.querySelectorAll('input'))
      .filter(el => (el.placeholder || '') === '重量');
    // ★ 2026-10-16：红字里的磅数就是**平台要我们填的值**（用户口径「按提醒的数字磅数填上去」）。
    //   文案：「填写包裹重量与包裹内SKU总重量（3.48 磅）差异过大，请修改」
    //   故必须把 3.48 抓出来，供 weight_fix 覆盖「基准×件数」公式（见该函数 +红字N 分支）。
    let hintWeight = '';
    if (/差异过大/.test(t)) {
      const hm = t.match(/（\s*([\d.]+)\s*磅\s*）/);
      if (hm) hintWeight = hm[1];
    }
    out.push({ord: m[0], qty: qty,
              weights: wins.map(el => (el.value || '').trim()),
              mismatchHint: /差异过大/.test(t),
              hintWeight: hintWeight});
  });
  return out;
}"""

_JS_OPEN_ROW_WAREHOUSE = r"""(ord) => {
  const els = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim().indexOf('订单号：' + ord) === 0);
  if (!els.length) return 'not-found';
  els.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  let node = els[0];
  for (let i = 0; i < 8 && node; i++) {
    const sel = node.querySelectorAll ? Array.from(node.querySelectorAll("input[placeholder='请选择']")).filter(e=>e.offsetParent!==null && !e.disabled) : [];
    if (sel.length) { sel[0].scrollIntoView({block:'center'}); sel[0].click(); return 'ok'; }
    node = node.parentElement;
  }
  return 'no-select';
}"""

_JS_OPEN_ROW_SERVICE = r"""(ord) => {
  const els = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim().indexOf('订单号：' + ord) === 0);
  if (!els.length) return 'not-found';
  els.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  let node = els[0];
  for (let i = 0; i < 8 && node; i++) {
    const sel = node.querySelectorAll ? Array.from(node.querySelectorAll("input[placeholder='请选择']")).filter(e=>e.offsetParent!==null && !e.disabled) : [];
    if (sel.length) { const el = sel[sel.length-1]; el.scrollIntoView({block:'center'}); el.click(); return 'ok'; }
    node = node.parentElement;
  }
  return 'no-select';
}"""

_JS_SERVICE_OPTIONS = r"""() => {
  const out = [];
  document.querySelectorAll('*').forEach(e => {
    if (e.offsetParent === null) return;
    const t = (e.innerText||'').replace(/\n/g,' | ').trim();
    if (!/^(USPS|SpeedX|Pilot|GOFO|UPS|YANWEN|FedEx|UniUni|BFE|SwiftX)/.test(t)) return;
    if (t.length > 200 || e.children.length > 6) return;
    const dis = t.indexOf('不可用原因') >= 0;
    out.push({text: t.slice(0, 90), disabled: dis});
  });
  const uniq = []; const seen = {};
  out.forEach(o => { if (!seen[o.text]) { seen[o.text] = 1; uniq.push(o); } });
  return uniq.slice(0, 20);
}"""

_JS_PICK_SERVICE_PREFIX = r"""(prefix) => {
  const cands = Array.from(document.querySelectorAll('*')).filter(e => {
    if (e.offsetParent === null) return false;
    const t = (e.innerText||'').trim();
    if (t.indexOf(prefix) !== 0) return false;
    if (t.length > 120 || e.children.length > 6) return false;
    if (t.indexOf('不可用原因') >= 0) return false;
    return true;
  });
  if (!cands.length) return 'no-option';
  cands.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  cands[0].click();
  return 'ok:' + (cands[0].innerText||'').replace(/\n/g,' | ').slice(0,60);
}"""

_JS_PICK_FIRST_AVAILABLE_SERVICE = r"""() => {
  const cands = Array.from(document.querySelectorAll('*')).filter(e => {
    if (e.offsetParent === null) return false;
    const t = (e.innerText||'').trim();
    if (t.indexOf('不可用原因') >= 0) return false;
    if (t.indexOf('预计') < 0 && t.indexOf('预估') < 0) return false;
    if (t.length > 160 || e.children.length > 8) return false;
    return true;
  });
  if (!cands.length) return 'no-option';
  cands.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  cands[0].click();
  return 'ok:' + (cands[0].innerText||'').replace(/\n/g,' | ').slice(0,80);
}"""

_JS_MODAL_PRESENT = r"""(keyword) => {
  // 可见性判据必须是 rect-based：TEMU 的模态**外层是 position:fixed**，
  // 而 fixed 元素的 `offsetParent` 恒为 null ⇒ 旧的 `offsetParent !== null` 会把
  // 遮罩/外壳整体滤掉（与 TB-039 分页选项同一个坑）。2026-09-19 实测：
  // mask / MDL_outerWrapper / MDL_container 三层的 offsetParent 全是 null。
  const vis = e => {
    const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
        && getComputedStyle(e).visibility !== 'hidden';
  };
  const ms = Array.from(document.querySelectorAll('[data-testid=beast-core-modal],[class*=MDL_]')).filter(vis);
  return ms.some(e => (e.innerText||'').indexOf(keyword) >= 0);
}"""

#: 当前可见模态的文本（诊断用：认不出来时把原文打出来，**不让"没认出来"变成静默 no-op**）。
_JS_MODAL_TEXT = r"""() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
        && getComputedStyle(e).visibility !== 'hidden';
  };
  const ms = Array.from(document.querySelectorAll('[data-testid=beast-core-modal],[class*=MDL_inner_123]')).filter(vis);
  if (!ms.length) return '';
  const t = (ms[ms.length-1].innerText || '').replace(/\s+/g, ' ').trim();
  return t.slice(0, 300);
}"""

_JS_OPEN_CARRIER_SCOPE = r"""() => {
  const cands = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && (e.placeholder||'').indexOf('默认为全部物流商') >= 0);
  if (!cands.length) return 'no-select';
  const box = cands[cands.length-1];
  box.scrollIntoView({block:'center'}); box.click(); return 'ok';
}"""

_JS_SET_CARRIERS = r"""([target]) => {
  function findCb(name){
    const els = Array.from(document.querySelectorAll('*')).filter(e => (e.innerText||'').trim() === name && e.offsetParent !== null);
    for (const el of els) {
      let cb = el.querySelector && el.querySelector('input[type=checkbox]');
      if (cb) return cb;
      let node = el.parentElement;
      for (let i=0;i<5 && node;i++){ cb = node.querySelector && node.querySelector('input[type=checkbox]'); if (cb) return cb; node = node.parentElement; }
    }
    return null;
  }
  const out = [];
  Object.keys(target).forEach(name => {
    const cb = findCb(name);
    if (!cb) { out.push(name + ':?'); return; }
    if (cb.checked !== target[name]) { (cb.closest('label') || cb).click(); out.push(name + ':fixed'); }
    else out.push(name + ':ok');
  });
  return out;
}"""

#: **近似关闭点兜底**（口径与老的 `_JS_NOTICE_CLOSE_POS` 完全一致）：直接在 `[class*=MDL_inner_]`
#: 里按文本找内容区，取右上角。用途：万一某个通知弹窗不在 `_JS_POPUP_PROBE` 认的那层外壳下
#: （探测看不见它），也不至于从"能关掉"退化成"关不掉"。**只有登记表声明 `"close": "approx"`
#: 的条目才会走到这里**（参数＝该条目 when.title_contains 的第一个关键词）。
_JS_INNER_APPROX_BY_TEXT = r"""(kw) => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 40 && r.height > 40
        && getComputedStyle(e).visibility !== 'hidden'; };
  const mod = Array.from(document.querySelectorAll('[class*=MDL_inner_]')).filter(vis)
      .find(e => ((e.innerText || '') + '').indexOf(kw) >= 0);
  if (!mod) return null;
  const r = mod.getBoundingClientRect();
  return [Math.round(r.right - 26), Math.round(r.top + 26)];
}"""

#: 「物流细则」通知弹窗的关闭坐标**已并入** `_JS_POPUP_PROBE`（返回 `approxAt`，口径不变：
#: inner.right-26 / inner.top+26）。2026-09-15 实测：下单链上会出现该通知（USPS/FedEx 政策），
#: 点右上角 ✕ 关掉即可，不影响下单。**谁能不能用这个近似点，由弹窗登记表说了算**
#: （条目写 `"close": "approx"`，现为 PU-003）；未登记的弹窗拿不到它。

_JS_FIND_PICKUP_DATE_FIELD = r"""() => {
  // **2026-09-19 修（"只填了前 2 行就 break"的真因）**：
  // ① 必须取**含「预约上门时间」的那个模态**，不能用 `modals[last]`——点开日期选择器后，
  //    最后一个 `MDL_inner` 会变成**选择器面板本身**（里面没有 tr）→ 旧写法返回 null → 循环提前退出，
  //    剩下的行留着「预约取件时间不能为空」→ **平台校验不过、拒绝提交**（表现成"点了没下单"）。
  // ② 可见性判据改 rect 判（`offsetParent !== null` 对 fixed 外壳恒 false。
  // ③ 只认**日期列**（排除「立即预约/稍后预约」和「请选择时间」那两列）。
  const rectVis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
        && getComputedStyle(e).visibility !== 'hidden'; };
  const mods = [...document.querySelectorAll('[class*=MDL_inner]')].filter(
      e => rectVis(e) && (e.innerText || '').indexOf('预约上门时间') >= 0);
  for (const modal of mods.slice().reverse()) {
    for (const tr of [...modal.querySelectorAll('tr')]) {
      for (const e of [...tr.querySelectorAll('input[type=text]')].filter(rectVis)) {
        if ((e.value || '').trim()) continue;                    // 已填过
        if (e.disabled) continue;                                // 「立即预约」那列
        if ((e.placeholder || '').indexOf('时间') >= 0) continue; // 「请选择时间」那列
        const r = e.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
      }
    }
  }
  return null;
}"""

_JS_PICK_EARLIEST_DATE = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const cands = Array.from(document.querySelectorAll('*')).filter(e => {
    if (!vis(e)) return false;
    const t = (e.innerText||'').trim();
    if (!/^\d{4}-\d{2}-\d{2}/.test(t)) return false;
    if (t.indexOf(':') >= 0) return false;
    if (t.indexOf('非营业日') >= 0) return false;
    if (e.children.length > 2 || t.length > 30) return false;
    if (/disab/i.test((e.className||'').toString())) return false;
    return true;
  });
  if (!cands.length) return 'no-option';
  cands.sort((a,b)=>(a.innerText||'').trim().localeCompare((b.innerText||'').trim()));
  cands[0].click();
  return cands[0].innerText.trim();
}"""

_JS_FILL_ORDER_FILTER = r"""(val) => {
  const ins = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && (e.placeholder||'').indexOf('最多100个') >= 0);
  if (!ins.length) return 'no-input';
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  const put = (el,v) => { set.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); };
  if (ins.length >= 2) put(ins[1], '');
  put(ins[0], val);
  return 'ok:len=' + ins[0].value.length;
}"""

#: 回读「订单号」筛选框当前值（判断筛选到底填进去没有；2026-09-30 加）。
#: 实测症状：`_JS_FILL_ORDER_FILTER` 报 ok，但列表根本没按它筛（读到 192/500/500 行的全量列表）——
#: 光看返回码看不出来，必须回读输入框与列表行数才能判"筛选生效了没有"。
_JS_ORDER_FILTER_VALUE = r"""() => {
  const ins = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && (e.placeholder||'').indexOf('最多100个') >= 0);
  if (!ins.length) return {state: 'no-input'};
  return {state: 'ok', value: ins[0].value || '', tokens: (ins[0].value || '').trim().split(/[\s,]+/).filter(Boolean).length};
}"""

#: 关掉「在线下单记录」抽屉（2026-09-30 实测的读串行真因）。
#: `--action record` 会把这个抽屉打开、**跑完不关**；之后任何「读订单列表」都被它盖住：
#: 实测分诊三个页签读到的都是抽屉里的行（192/500/500 行、本批 0 行）→ 判「读不清」。
#: 判据＝正文含抽屉标题与其提示语；关闭键＝抽屉里最靠右上角的小可点元素。
_JS_CLOSE_RECORD_DRAWER = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const body = document.body.innerText || '';
  if (body.indexOf('关闭本抽屉不会影响进行中的下单任务') < 0) return 'absent';
  let root = null;
  document.querySelectorAll('div').forEach(e => {
    if (!vis(e)) return;
    const t = (e.innerText || '');
    if (t.indexOf('在线下单记录') !== 0) return;
    const r = e.getBoundingClientRect();
    if (r.width < 300 || r.height < 300) return;
    const a = r.width * r.height;
    if (!root || a > root.a) root = {el: e, a: a, r: r};
  });
  if (!root) return 'no-root';
  const cands = Array.from(root.el.querySelectorAll('button,span,i,svg,div')).filter(e => {
    if (!vis(e)) return false;
    const r = e.getBoundingClientRect();
    if (r.width < 8 || r.width > 70 || r.height < 8 || r.height > 70) return false;
    const cls = String(e.className || '');
    const al = String((e.getAttribute && e.getAttribute('aria-label')) || '');
    const tx = (e.innerText || '');
    const near = (r.x > root.r.x + root.r.width - 90) && (r.y < root.r.y + 90);
    return near || /close|✕|✖|×|关闭/i.test(cls + ' ' + al + ' ' + tx);
  });
  if (!cands.length) return 'no-close';
  cands.sort((a, b) => {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    return (rb.x - ra.x) || (ra.y - rb.y);
  });
  cands[0].click();
  return 'clicked';
}"""

_JS_CLICK_TAB = r"""(name) => {
  const els = Array.from(document.querySelectorAll('*')).filter(e => e.offsetParent !== null
      && new RegExp('^' + name + '\\s*\\(').test((e.innerText||'').trim()) && e.children.length <= 2);
  if (!els.length) return 'no-tab:' + name;
  els.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
  els[0].click(); return 'ok';
}"""

#: TB-039（2026-09-16 实测）：先把 sizeChanger 滚进视口。
#: 实测（BEL 22 单）：pager 在列表底部，表头中心点 `(979,830)` 落在**视口外**
#: （viewport 929×883，x 超界）→ `document.elementFromPoint` 返回 null →
#: 旧 `cdp_click` 等于点空处 ⇒ 下拉从不展开 ⇒ `_JS_PAGE_SIZE_OPTIONS` 读 0 个 →
#: `no-option:选项=` → 每页停在默认 20 → 后 2 单落页外（"需要分两次"的真因）。
#: `scrollIntoView({block:'center',inline:'center'})` 后中心点 `(723,830)` 命中表头内 INPUT，下拉正常展开。
_JS_PAGE_SIZE_SCROLL = r"""() => {
  const sc = document.querySelector('[class*=sizeChanger]');
  if (!sc) return 'no-sc';
  sc.scrollIntoView({block: 'center', inline: 'center'});
  return 'ok';
}"""

#: 订单列表「每页」下拉：定位**属于订单列表**那一个，返回表头坐标 + 当前值。
#: 2026-09-15 实测：`li.PGT_sizeChanger_123 > div[data-testid=beast-core-select] > div[data-testid=beast-core-select-header]`，
#: 当前值在表头的 input.value 里（表头 innerText 为空）。
#: 2026-09-16 增 `inViewport`：坐标不在视口内时**不放空枪**（见 `_JS_PAGE_SIZE_SCROLL`）。
#:
#: ⚠ 2026-10-28 修（BEL 实测，重要）：原来的判据是「往上找 8 层，看祖先里有没有含 `PO-`
#:   的 table」。实测这个判据**已经失效**：表格里明明有 19 个 `PO-`（在 `table tbody tr`
#:   里），但向上 8 层找不到 —— 于是函数**返回空 `{}`**（注意不是 `no-sc`，所以上层
#:   连原因都报不出来），`set_list_page_size` 直接判 `no-sc` 放弃 ⇒ 每页永远停在默认值
#:   ⇒ 167 单只读到 20 单。这就是「每页拉满」长期静默失败的根因。
#:
#:   现在改为：**不要求 PO- 锚点**，直接取可见的 sizeChanger（页脚那个），
#:   并用「同一容器里有没有 table」做弱校验；取不到才退回按 PO- 找。
_JS_PAGE_SIZE_HEAD = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const scs = Array.from(document.querySelectorAll('[class*=sizeChanger]')).filter(vis);
  if (!scs.length) return {state: 'no-sc'};
  const info = (sc) => {
    const h = sc.querySelector('[data-testid=beast-core-select-header]')
           || sc.querySelector('[class*=ST_head]');
    if (!h) return null;
    const r = h.getBoundingClientRect();
    const inp = h.querySelector('input');
    return {state: 'ok', x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
            inViewport: !!(r.left >= 0 && r.top >= 0
                           && r.right <= window.innerWidth && r.bottom <= window.innerHeight),
            value: ((inp && inp.value) || h.innerText || '').trim()};
  };
  // ① 先按老口径：祖先里含 PO- 行的表（最精确）
  for (const sc of scs) {
    let node = sc;
    for (let k = 0; k < 8 && node; k++) {
      const txt = Array.from(node.querySelectorAll('table')).map(t => t.innerText || '').join('\n');
      if (txt.indexOf('PO-') >= 0) {
        const r = info(sc);
        if (r) return r;
      }
      node = node.parentElement;
    }
  }
  // ② 老口径失效（2026-10-28 BEL 实测就是这种）→ 取**最后一个**可见的 sizeChanger。
  //    列表页脚通常只有一个，且它在 DOM 里靠后；这一条让「每页拉满」不再静默失败。
  for (let i = scs.length - 1; i >= 0; i--) {
    const r = info(scs[i]);
    if (r && r.inViewport) return r;
  }
  // ③ 再退一步：只要有表头就返回（让上层按 inViewport 决定滚不滚）
  for (let i = scs.length - 1; i >= 0; i--) {
    const r = info(scs[i]);
    if (r) return r;
  }
  return {state: 'no-head'};
}"""

#: 「每页」下拉展开后的可见选项（实测 class 含 `ST_itemRender`，文本就是 5/10/20/50/100/500）。
#: ⚠ 可见性判据必须用 `getClientRects()`，**不能**用 `offsetParent !== null`（TB-039）：
#: 「每页」下拉的**可点击容器**坐标（`li.PGT_sizeChanger_123` 本身）。
#:
#: ⚠ 2026-10-28 实测（BEL，关键）：点 **header**（`ST_head`，x=2045）**展不开**下拉；
#:   必须点**整个 sizeChanger 容器**（x=2039，宽 108）才会弹出选项。
#:   同一时刻对照：点 header → 选项 0 个；点容器 → 立刻拿到 5/10/20/50/100/500。
#:   这就是「每页拉满」长期 `no-option:选项=` 的第二个真因（第一个是 HEAD 定位失效）。
_JS_PAGE_SIZE_BOX = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const scs = Array.from(document.querySelectorAll('[class*=sizeChanger]')).filter(vis);
  if (!scs.length) return {state: 'no-sc'};
  const sc = scs[scs.length - 1];
  const r = sc.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return {state: 'no-size'};
  return {state: 'ok',
          x: Math.round(r.left + r.width / 2),
          y: Math.round(r.top + r.height / 2),
          inViewport: !!(r.left >= 0 && r.top >= 0
                         && r.right <= window.innerWidth && r.bottom <= window.innerHeight),
          value: (sc.innerText || '').replace(/[^0-9]/g, '')};
}"""

#: 用 **DOM click** 展开「每页」下拉（最后兜底）。
#:
#: 为什么必须有这条（2026-10-28 BEL 实测）：**新开的标签页布局不同** ——
#:   现有页：header 在 x=2045，坐标点容器可展开 ✅
#:   新开页：header 在 x=1721，而 x=1715 那个位置被 `INPUT.IPT_input_123` 占着；
#:           `document.elementFromPoint(1715, 974)` 实测返回该 INPUT
#:           ⇒ **真实鼠标事件永远打不中下拉**，只有 DOM click 能展开。
#:   实测：`h.click()` → 立刻拿到 5/10/20/50/100/500。
_JS_PAGE_SIZE_DOM_OPEN = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const scs = Array.from(document.querySelectorAll('[class*=sizeChanger]')).filter(vis);
  if (!scs.length) return 'no-sc';
  const sc = scs[scs.length - 1];
  const h = sc.querySelector('[data-testid=beast-core-select-header]')
         || sc.querySelector('[class*=ST_head]');
  if (!h) return 'no-head';
  h.click();
  return 'clicked';
}"""

#: 该下拉渲染在 portal 浮层里（`PT_portalMain_123`，position:fixed）→ offsetParent 恒 null
#: ⇒ 即使下拉已展开也会被误判成「0 个选项」→ `no-option`。（同一个坑 2026-09-15 一次性探针已踩过一次。）
_JS_PAGE_SIZE_OPTIONS = r"""() => {
  const vis = e => (e.getClientRects && e.getClientRects().length > 0)
        && getComputedStyle(e).visibility !== 'hidden';
  return Array.from(document.querySelectorAll('[class*=ST_itemRender],[class*=itemRenderer],[role=option]'))
    .filter(e => vis(e) && /^\d+$/.test((e.innerText || '').trim()))
    .map(e => {
      const r = e.getBoundingClientRect();
      return {text: (e.innerText || '').trim(),
              x: Math.round(r.left + Math.max(r.width / 2, 4)),
              y: Math.round(r.top + Math.max(r.height / 2, 8))};
    });
}"""

_JS_PICK_PAGE_SIZE = r"""(text) => {
  const vis = e => (e.getClientRects && e.getClientRects().length > 0)
        && getComputedStyle(e).visibility !== 'hidden';
  const os = Array.from(document.querySelectorAll('[class*=ST_itemRender],[class*=itemRenderer],[role=option]'))
      .filter(e => vis(e) && (e.innerText || '').trim() === text);
  if (!os.length) return 'no-option:' + text;
  os[0].click(); return 'ok';
}"""

_JS_LIST_ROWS = r"""() => {
  const rows = Array.from(document.querySelectorAll('table tr')).filter(tr => (tr.innerText||'').indexOf('PO-') >= 0);
  return rows.map(tr => {
    const t = tr.innerText || '';
    const ord = (t.match(/PO-\d+-\d+/) || [''])[0];
    const track = (t.match(/运单号：\s*\n?\s*([A-Za-z0-9]+)/) || ['', ''])[1];
    const st = (t.match(/待发货|已发货/) || [''])[0];
    const manual = Array.from(tr.querySelectorAll('a,span,button')).some(e => (e.innerText||'').trim() === '去手动确认');
    return {ord: ord, track: track, status: st, manual: manual};
  });
}"""

_JS_CLICK_MANUAL_FIRST = r"""() => {
  const rows = Array.from(document.querySelectorAll('table tr')).filter(tr => (tr.innerText||'').indexOf('PO-') >= 0);
  for (const tr of rows) {
    const link = Array.from(tr.querySelectorAll('a,span,button')).filter(e => (e.innerText||'').trim() === '去手动确认');
    if (link.length) {
      link[0].scrollIntoView({block:'center'});
      link[0].click();
      const m = (tr.innerText||'').match(/PO-\d+-\d+/);
      return m ? m[0] : 'clicked';
    }
  }
  return 'none';
}"""


# 待办卡「即将逾期未发货 N」（待发货页顶部待办区）。2026-09-30 实测两条关键事实：
#   ① 这张卡**只能用 DOM `el.click()` 触发**——用原生 CDP `Input.dispatchMouseEvent` 点它，
#      页面毫无反应（列表不变、也不开新页）；换成 `el.click()` 立刻筛成「共有 N 条」。
#   ② 点它是**前端筛选**：URL 不变，列表条数变成卡上的数字 ⇒ 取数必须读点完之后的列表。
_JS_OVERDUE_TODO = r"""(text) => {
  const vis = e => e && e.offsetParent !== null;
  const want = String(text || '');
  let card = null;
  document.querySelectorAll('div,button,span,a').forEach(e => {
    if (!vis(e)) return;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.indexOf(want) !== 0) return;
    const rest = t.slice(want.length).trim();
    if (rest && !/^\d+$/.test(rest)) return;      // 卡上只有「标题 + 数字」；别抓整行容器（会点歪）
    const r = e.getBoundingClientRect();
    if (r.width < 120 || r.height < 24) return;
    const a = r.width * r.height;
    if (!card || a < card.a) card = {el: e, a: a, text: t};   // 取"最小够大"＝卡片本体
  });
  if (!card) return 'none';
  card.el.click();
  return card.text;
}"""


#: 待办任务**区域本身**是否存在（只读探测，不点任何东西）。
#: 用途：把 `_JS_OVERDUE_TODO` 返回的 `'none'` 拆成两种截然不同的含义——
#:   · 区域**存在**、只是没有目标那张卡 ⇒ 该待办已清空＝**正常收工**（不该报失败）
#:   · 区域**根本不存在** ⇒ 页面未就绪 / 平台改版 ⇒ 真问题，保留 HARD STOP
#: 2026-10-09 立：BEL/BHY 当天 08:51 清完逾期单后，09:03 再跑就因「卡消失」被报成
#: 失败(42) 并写进 FAILED.log——无人值守下这会**每天误报失败**，把真失败淹没。
_JS_OVERDUE_ZONE = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  let zone = null;
  document.querySelectorAll('div,span,a,button').forEach(e => {
    if (!vis(e)) return;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.indexOf('待办任务') !== 0) return;          // 认准「待办任务」标题
    const r = e.getBoundingClientRect();
    if (r.width < 120 || r.height < 20) return;
    const a = r.width * r.height;
    if (!zone || a < zone.a) zone = {el: e, a: a, text: t};   // 取"最小够大"＝区域本体
  });
  if (!zone) return JSON.stringify({present: false, text: ''});
  return JSON.stringify({present: true, text: zone.text.slice(0, 300)});
}"""


# 待办列表按行读（比 `_JS_LIST_ROWS` 多两项：`warehouse` 页面上的「库存扣减仓库」、`qty` 件数）——
# 这两项就是「缺面单 → 直接生成派送表去在线下单」要的输入（2026-09-30），不必再让人做 xlsx。
_JS_OVERDUE_ROWS = r"""() => {
  const rows = Array.from(document.querySelectorAll('table tr')).filter(tr => (tr.innerText||'').indexOf('PO-') >= 0);
  return rows.map(tr => {
    const x = (tr.innerText||'').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ');
    return {ord: (x.match(/PO-\d+-\d+/) || [''])[0],
            track: (x.match(/\u8fd0\u5355\u53f7\uff1a\s*([A-Za-z0-9]+)/) || ['', ''])[1],
            status: (x.match(/\u5f85\u53d1\u8d27|\u5df2\u53d1\u8d27/) || [''])[0],
            manual: Array.from(tr.querySelectorAll('a,span,button')).some(e => (e.innerText||'').trim() === '\u53bb\u624b\u52a8\u786e\u8ba4'),
            warehouse: (x.match(/\u5e93\u5b58\u6263\u51cf\u4ed3\u5e93\uff1a\s*([^\s]+)/) || ['', ''])[1],
            qty: (x.match(/\u00d7\s*(\d+)/) || ['', '1'])[1]};
  });
}"""


# ---------------------------------------------------------------- 爬虫公用（占用守卫 / 依赖定位）
# 与 daily_sales_driver 同路子（不 fork 业务逻辑，只按本域站点换判据）；spawn 一律走 lib.quiet。
_TEMU_HOST = "agentseller-us.temu.com"


def _find_node() -> str | None:
    """定位 node（raw_cdp.js 是 Node 客户端）。PATH/环境变量/常见位置现取，禁写死用户名路径。"""
    import glob
    import shutil
    n = shutil.which("node")
    if n:
        return n
    for env in ("NODE_EXE", "DSH_NODE"):
        v = os.environ.get(env)
        if v and os.path.exists(v):
            return v
    for cand in (r"C:\Program Files\nodejs\node.exe",):
        if os.path.exists(cand):
            return cand
    for hit in glob.glob(os.path.join(os.path.expanduser("~"), "*", "binaries", "node", "*", "node.exe"))[:1]:
        return hit
    return None


def _rawcdp_path() -> str | None:
    """定位项目 `_scripts/raw_cdp.js`（相对项目根现取，禁写死用户名路径）。"""
    here = os.path.dirname(os.path.abspath(__file__))          # scripts/power/lib
    for cand in (os.path.dirname(os.path.dirname(here)),
                 os.path.dirname(os.path.dirname(os.path.dirname(here)))):
        p = os.path.join(cand, "_scripts", "raw_cdp.js")
        if os.path.exists(p):
            return p
    return None


def temu_tab_occupied(port: int):
    """占用检测：读该窗口 TEMU 商家中心 tab 的 `attached`。

    True=已被其它爬虫/CDP 会话占用 → **不硬插**（登记表 I「不干扰其他爬虫」/ RL-018）；
    False=空闲可领；None=缺 node/raw_cdp.js 或探测失败（**不阻断**，如实提示）。
    """
    node = _find_node()
    raw = _rawcdp_path()
    if not node or not raw:
        return None
    try:
        import json as _json
        import tempfile
        try:
            from lib import quiet as _quiet
        except Exception:  # noqa: BLE001
            _quiet = None
        js = (
            "const r=require(%r);(async()=>{"
            "const{cdp:c}=await r.connectBrowser(%d);"
            "const t=await c.send('Target.getTargets',{});"
            "const s=(t.targetInfos||[]).find(x=>x.type==='page'&&(x.url||'').includes(%r));"
            "console.log(JSON.stringify({occupied:!!(s&&s.attached===true)}));"
            "c.close();process.exit(0);})().catch(e=>{console.log(JSON.stringify({occupied:null}));process.exit(1);});"
        ) % (raw, port, _TEMU_HOST)
        fd, js_path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(js)
            runner = _quiet.run if _quiet else __import__("subprocess").run
            out = runner([node, js_path], capture_output=True, text=True, timeout=25,
                         encoding="utf-8", errors="replace")
        finally:
            try:
                os.unlink(js_path)
            except OSError:
                pass
        line = (out.stdout or "").strip().splitlines()[-1] if (out.stdout or "").strip() else ""
        return _json.loads(line).get("occupied") if line else None
    except Exception:  # noqa: BLE001
        return None


def count_live_clients(netstat_text: str, port: int) -> int:
    """从 netstat 输出数「活 CDP 客户端」（纯函数，可单测）。

    为什么必须按**对端端点去重**：一条 loopback 连接在 netstat 里是**两行**（本端一行、对端一行），
    旧实现按行数统计 ⇒ 1 个客户端被算成 2 个；而本守卫自己跑的探测进程（`raw_cdp.js`）也会占一条，
    于是「其实没有别的爬虫」被误判成「已被占用」→ 自己的守卫把自己的动作 HARD_STOP（2026-09-15 实测踩到）。
    """
    tag = ":%d" % int(port)
    peers = set()
    for ln in str(netstat_text or "").splitlines():
        if "ESTABLISHED" not in ln:
            continue
        p = ln.split()
        if len(p) < 5:
            continue
        local, remote = p[1], p[2]
        if local.endswith(tag):
            peers.add(remote)
        elif remote.endswith(tag):
            peers.add(local)
    return len(peers)


def live_cdp_clients(port: int) -> int:
    """连到该 CDP 端口的活 CDP 客户端数（按对端端点去重；失败返回 -1）。

    为什么要它：2026-09-14 实测——**本机页面会长期残留 `attached=true`**（当时 0 个活连接、
    端口甚至已失效，但 `Target.getTargets` 里三个页面全报 attached）。单靠 attached 判占用会把
    整条通道永久锁死（实测 fill 1 秒即 HARD_STOP）。故占用判据＝`attached 且 有活客户端`。
    """
    try:
        from lib import quiet as _quiet
    except Exception:  # noqa: BLE001
        _quiet = None
    runner = _quiet.run if _quiet else __import__("subprocess").run
    try:
        kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 20}
        if _quiet is None:
            kw["creationflags"] = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
        out = runner(["netstat", "-ano", "-p", "tcp"], **kw).stdout or ""
    except Exception:  # noqa: BLE001
        return -1
    return count_live_clients(out, port)


#: 真无图（反爬减载）：拦图片/字体/媒体。软隐藏不行——仍会下载（登记表 G / ziniao-cdp-port CP-006）
_BLOCKED_URLS = ["*.png*", "*.jpg*", "*.jpeg*", "*.webp*", "*.gif*", "*.svg*", "*.ico*",
                 "*.woff*", "*.woff2*", "*.ttf*", "*.otf*", "*.mp4*", "*.webm*"]

#: 登录态失效的落地页特征（TB-030）。⚠ 只认认证/授权页——`seller.kuajingmaihuo.com/login` 那个
#: 扫码登录页是窗口常驻的无关 tab（TLA-002/TLA-014），拿它判"登录态失效"是误报（2026-09-15 踩到）。
AUTH_URL_HINTS = ("auth/authentication", "settle/seller-login")


def auth_lost_hint_from_urls(urls, store: str = "") -> str:
    """URL 列表里出现认证/授权页 → 返回一句点名真因的提示（纯函数，可单测）；否则空串。"""
    for u in urls or []:
        s = str(u or "")
        if any(h in s for h in AUTH_URL_HINTS):
            st = ("--store %s" % store) if store else "--store <店后缀>"
            return ("；真因：TEMU 登录态失效——窗口停在 %s。"
                    "先跑 `python scripts/power/run_temu_auth.py %s` 授权后再跑本动作"
                    "（SKILL 前置条件：已登录 TEMU 商家中心）" % (s[:100], st))
    return ""


# ---------------------------------------------------------------- ⓪ auth：正常的点击登录（整链第一步）
# 用户 2026-09-24：「要么直接关掉，要么要正常的点击登录，我怀疑你现在走了捷径」。
# 口径澄清＋落点：
#   · **登录＝正常点击**：点区域页「商家中心」→（可能）「确认切换」→ 勾授权条款 → 点「授权登录 / 确认授权并前往」
#     → 落到订单页（唯一实现＝`run_temu_auth.py` / `lib/auth_temu_driver.run_auth`，本函数只调用它，**不另写一套**）；
#   · **本域没有任何"捷径"**：不注入 cookie、不搬 storage_state、不改 URL 绕过登录（静态闸门
#     `tests/test_buylabel_pipeline.py::test_no_login_shortcuts` 守着）；
#   · 遇登录页**不再只是停下上报**：整链第一步就把登录点完再往下走（原来只抛 `no-input` 让人去补授权）。
def _auth_entry_path() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                                         "run_temu_auth.py"))


def _hidden_spawn(cmd, **kw):
    """隐藏控制台起子进程（用户 2026-09-22 立规；与 abnormal 域同形）。"""
    try:
        from lib import quiet as _quiet
    except Exception:  # noqa: BLE001
        _quiet = None
    if _quiet is not None and hasattr(_quiet, "hidden_run"):
        return _quiet.hidden_run(cmd, **kw)
    import subprocess as _sp
    if os.name == "nt":
        si = _sp.STARTUPINFO()
        si.dwFlags |= _sp.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return _sp.run(cmd, startupinfo=si,
                       creationflags=(getattr(_sp, "CREATE_NEW_CONSOLE", 0x10)
                                      | getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0x200)), **kw)
    return _sp.run(cmd, creationflags=0, **kw)


def ensure_store_window(store: str, log=print) -> int:
    """确保该店紫鸟窗口开着并返回 CDP 端口（0＝拿不到）。窗口生命周期单点＝`lib/ziniao_store`。"""
    try:
        import json as _json
        import ziniao_store as _zs            # 同目录单点实现
        with open(os.path.join(_DATA_DIR, "ziniao_power_map.json"), encoding="utf-8") as f:
            m = _json.load(f)
    except Exception:  # noqa: BLE001
        return 0
    e = None
    for it in (m.get("mapping") or []):
        if store in (it.get("export_suffix"), it.get("power_name"), it.get("ziniao_account")):
            e = it
            break
    if e is None:
        return 0
    win = e.get("ziniao_window_name") or ""
    port = _zs.probe_port_by_window(win)
    if not port:
        log("[授权] 窗口 %s 没开 → 开窗后重探端口" % win)
        _zs.open_store(e.get("ziniao_window_store_id"))
        deadline = time.time() + 90
        while time.time() < deadline:
            time.sleep(3)
            port = _zs.probe_port_by_window(win)
            if port:
                break
    return int(port or 0)


def restart_store_window(store: str, log=print) -> int:
    """**关窗 → 开窗 → 重探端口 → 清障**（返回新端口，0＝失败）。窗口生命周期单点＝`lib/ziniao_store`。

    为什么要有这一步（本域既有口径 + 用户 2026-09-24「要么直接关掉，要么要正常的点击登录」）：
    窗口开着但堆了一摞残留页时，Playwright 的 browser-level attach 会**握手卡死**
    （RL-013：今天实测 `connect_over_cdp: Timeout 15000ms exceeded`，ws 已连、初始化不完成）。
    关掉重开是唯一稳的解法；**开窗后立刻清一遍空白残留页**，别等它把上次会话的 tab 还原回来再拖死 attach。
    """
    try:
        import json as _json
        import ziniao_store as _zs
        with open(os.path.join(_DATA_DIR, "ziniao_power_map.json"), encoding="utf-8") as f:
            m = _json.load(f)
    except Exception:  # noqa: BLE001
        return 0
    e = next((it for it in (m.get("mapping") or [])
              if store in (it.get("export_suffix"), it.get("power_name"), it.get("ziniao_account"))), None)
    if e is None:
        return 0
    win = e.get("ziniao_window_name") or ""
    log("[授权] 关窗 → 重开 → 重探端口（attach 卡死的标准解法；关窗会掉登录态，但此刻本来就是未登录）")
    _zs.close_store(e.get("ziniao_window_store_id"))
    time.sleep(3)
    _zs.open_store(e.get("ziniao_window_store_id"))
    deadline = time.time() + 90
    port = 0
    while time.time() < deadline:
        time.sleep(3)
        port = _zs.probe_port_by_window(win)
        if port:
            break
    if port:
        try:
            _cl = _zs.clean_dead_tabs(port=port, log=log)      # 开窗后先清空白残留页
            log("[授权] 开窗后清障：空页 %d → 关掉 %d" % (len(_cl.get("dead") or []), len(_cl.get("closed") or [])))
        except Exception as ex:  # noqa: BLE001
            log("[授权] 清障跳过（%s）" % str(ex)[:60])
    return int(port or 0)


def call_auth_entry(store: str, log=print, timeout: float = 300) -> tuple:
    """调**唯一实现**做正常点击登录，返回 `(返回码, 末行原文)`。

    判成功**看返回码**（`run_temu_auth.py`：0=OK / 42=FAIL），不靠猜文案——
    入口会把结果打成 `[OK] OK: 已登录，无需授权` 这种带前缀的行（拿它 `startswith("OK")` 会误判）。
    子进程隔离：登录用的是 Playwright browser-level attach（RL-013 可能握手卡死），放子进程里超时可控，
    卡死也不会把本进程的 raw-CDP 会话拖下水。
    """
    cmd = [sys.executable, _auth_entry_path(), "--store", store]
    try:
        r = _hidden_spawn(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        tail = out[-1] if out else ""
        return int(getattr(r, "returncode", -1)), (tail[:200] if tail else "(无输出)")
    except Exception as e:  # noqa: BLE001
        return -1, "ERR %s: %s" % (type(e).__name__, str(e)[:120])


#: 「列表读不出来」的分诊路线（2026-09-15 起，唯一决策源见 SKILL 判定表 D12）
ROUTE_AUTH_REQUIRED = "auth_required"      # 登录态失效 → 先授权（可命令化）
ROUTE_PAGE_NOT_READY = "page_not_ready"    # 页面/筛选未就绪（0 行且未见认证页）→ 重试/人工看一眼
ROUTE_ORDERS_MISSING = "orders_missing"    # 列表非空但确实没有这些单 → 人工核对单号

# ---------------------------------------------------------------- 路线表（数据外置）
# 2026-09-21 用户：「我需要的是路线，这些所有的错误都是可以用步骤避免的，不要用负面提示词去规避」。
# 落地：**路线 = 现场信号 → 一组确定性步骤**，表在 data/temu_buylabel_routes.json；
# 脚本现取信号（collect_signals）→ pick_route() 取出该走的那条 → 按 steps 走。
# 本文件只提供「读表 + 匹配 + 取路线」的机制，判据与步骤顺序一律在 json 里改。
DEFAULT_ROUTES_PATH = os.path.join(_DATA_DIR, "temu_buylabel_routes.json")


def routes_path(cfg: dict | None = None) -> str:
    """路线表路径：`cfg["routes_file"]` 优先（相对路径按当前工作目录），否则用域内缺省。"""
    raw = str(((cfg or {}).get("routes_file")) or "").strip()
    if raw:
        return raw if os.path.isabs(raw) else os.path.normpath(raw)
    return DEFAULT_ROUTES_PATH


def load_routes(path: str | None = None, cfg: dict | None = None) -> list[dict]:
    """读路线表 → routes 数组（缺文件/坏文件返回空表，由调用方按"无路线"处理）。"""
    p = path or routes_path(cfg)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    return [r for r in (data.get("routes") or []) if isinstance(r, dict) and r.get("id")]


def _when_ok(signals: dict, when: dict) -> bool:
    """单键判据：表达式字符串（==N / !=N / >N / >=N / <N / <=N / true / false）。纯函数。"""
    if not isinstance(when, dict):
        return False
    for k, expr in when.items():
        if k not in signals:
            return False                     # 信号没采到 → 不命中（不猜）
        got = signals[k]
        s = str(expr).strip()
        if s.lower() in ("true", "false"):
            if bool(got) != (s.lower() == "true"):
                return False
            continue
        try:
            num = float(got)
        except (TypeError, ValueError):
            if str(got) != s:
                return False
            continue
        m = re.match(r"^(==|!=|>=|<=|>|<)?\s*(-?\d+(?:\.\d+)?)$", s)
        if not m:
            return False
        op, val = m.group(1) or "==", float(m.group(2))
        if not {"==": num == val, "!=": num != val, ">": num > val,
                ">=": num >= val, "<": num < val, "<=": num <= val}[op]:
            return False
    return True


def match_route(signals: dict, routes: list[dict] | None = None) -> dict | None:
    """按 routes 顺序取**第一条**全键命中的路线；没有则 None。纯函数，可单测。"""
    for r in (routes or []):
        if _when_ok(signals, r.get("when") or {}):
            return r
    return None


def pick_route(signals: dict, cfg: dict | None = None,
               routes: list[dict] | None = None) -> dict:
    """取本次该走的路线：返回 {id, steps, why}；没有命中就返回 stop_report 兜底。"""
    r = match_route(signals, routes if routes is not None else load_routes(cfg=cfg))
    if not r:
        return {"id": "no_route_matched", "steps": ["stop_report"],
                "_为什么": "信号没命中任何一条路线 ⇒ 停下回报（把 signals 原样带回）"}
    return {"id": r.get("id"), "steps": list(r.get("steps") or []),
            "_为什么": r.get("_为什么") or ""}


def pipeline_route_plan(step_out) -> dict:
    """把 `screen` 现取的**分诊路线真正落到 pipeline 的执行上**（2026-09-28 立，纯函数可单测）。

    为什么要有它（当天 47 次跑、24 次失败，两大类的**假失败**都出在这里）：
      · `nothing_to_buy`（可买 0 单，全已取消/已发货）：routes.json 早定了走 `stop_report`，
        但 pipeline 是**固定九步、不按路线改道** ⇒ 继续 widen→fill，把「这批没得买」这种
        正常结论报成了 HARD STOP 失败（11:03 BEL 28 单全已取消、被当成失败回传并开卡）。
      · `order_shipped` / `order_cancelled`（一部分已发货/已取消）：routes.json 说 `exclude_*`
        之后再继续，实际没剔除 ⇒ fill 拿**全批**去查 → 缺单 → 报「这些单在待发货里没查到」
        （15:33 BHY 172 单里有 72 单已发货，整批白停）。
      · 一个都没查到（`not_found>0`）：该在**分诊步**当场点名停下，别拖到 fill 才报「没查到」
        （15:03 BEL 的 234 单就是这种形态；当天那次真因是窗口被别的会话抢了）。

    返回 `{"verdict": "continue"|"noop"|"stop"|"narrow", "orders": [...], "reason": "…", "route": id}`

    **宁可停下、不许静默少买**：只有「`not_found==0` 且 `0 < pending < want` 且 ready 条数
    正好等于 pending」才允许缩小批量；其余一律原样继续（交给下游的缺单闸门报错停下）。
    """
    if not isinstance(step_out, dict):
        return {"verdict": "continue", "orders": [], "reason": "", "route": ""}
    sig, route = step_out.get("signals"), step_out.get("route")
    if not isinstance(sig, dict) or not isinstance(route, dict):
        return {"verdict": "continue", "orders": [], "reason": "", "route": ""}
    rid = str(route.get("id") or "")
    want = int(sig.get("want") or 0)
    pending = int(sig.get("pending") or 0)
    cancelled = int(sig.get("cancelled") or 0)
    shipped = int(sig.get("shipped") or 0)
    not_found = int(sig.get("not_found") or 0)
    if want <= 0:
        return {"verdict": "continue", "orders": [], "reason": "", "route": rid}
    if not_found > 0:
        return {"verdict": "stop", "orders": [], "route": rid,
                "reason": ("分诊：本批 %d 单里有 %d 单在「待发货/已发货/已取消」三页签都查不到"
                           "（路线=%s）→ 在分诊步停下，不往下填单。先核对单号与店；"
                           "若同店窗口上还有别的作业在跑，等它跑完再重跑一次（同一窗口双跑会互相读错列表）。"
                           % (want, not_found, rid or "-"))}
    if pending == 0:
        return {"verdict": "noop", "orders": [], "route": rid,
                "reason": ("分诊：本批 %d 单没有可买的（已取消 %d / 已发货 %d）→ 按路线 stop_report "
                           "结束：不下单、不算失败" % (want, cancelled, shipped))}
    ready = [str(x) for x in ((step_out.get("detail") or {}).get("ready") or [])]
    if pending < want and len(ready) == pending:
        return {"verdict": "narrow", "orders": ready, "route": rid,
                "reason": ("分诊：按路线剔除已取消 %d / 已发货 %d 单，本批由 %d 单缩到 %d 单继续"
                           % (cancelled, shipped, want, pending))}
    return {"verdict": "continue", "orders": [], "reason": "", "route": rid}


def diagnose_missing_orders(urls, list_rows: int, want: int, store: str = "",
                            query_applied: bool = False) -> dict:
    """把"这些单没查到"分诊成一条**显式路线**（纯函数，可单测）。

    2026-09-17 修（现场踩到，TB-041）：`urls` 必须是**我们实际读列表那个 page 的 URL**，
    不能传"窗口里所有 tab 的 URL"——实测 BEL 窗口里躺着一个残留的 `settle/seller-login` tab，
    而订单页本身登录完全正常（订单列表 37 单、每页下拉可见、无登录字样），结果**每缺单都被
    误诊成 `auth_required`**，把人骗去反复重新授权，真正的「这些单不在待发货」反而看不见。
    另外 0 行还要看**筛选是否已生效**：订单号筛选已应用仍 0 行 ⇒ 是真的没有这些单。
    """
    hint = auth_lost_hint_from_urls(urls, store)      # 只看「读列表那一页」的 URL
    if hint:
        return {"route": ROUTE_AUTH_REQUIRED, "hint": hint, "rows": list_rows, "want": want}
    if list_rows == 0:
        if query_applied:
            return {"route": ROUTE_ORDERS_MISSING, "rows": 0, "want": want,
                    "hint": "；真因：订单号筛选已应用而列表 0 行 ⇒ 这些单**不在「待发货」**"
                            "（多为今天已下过单/已发货）。核对该店订单列表状态，别重复下单。"}
        return {"route": ROUTE_PAGE_NOT_READY, "rows": 0, "want": want,
                "hint": "；真因：列表读到 0 行且筛选未确认生效、窗口里也没有认证页 ⇒ 页面/筛选未就绪"
                        "（多为刚导航完、或无图拦截把首屏挡了）。已重试仍 0 行 → "
                        "请人工看一眼该店窗口是否停在「待发货」列表，或加 --no-evidence 重跑取证。"}
    return {"route": ROUTE_ORDERS_MISSING, "rows": list_rows, "want": want,
            "hint": "；真因：列表非空（%d 行）但确实不含这些单 ⇒ 单号/状态核对（是否已下单、是否在别的店）。"
                    % list_rows}


# ---------------------------------------------------------------- 驱动
class BuylabelDriver:
    """TEMU 购买面单驱动。写操作由调用方（入口脚本）用 --confirm 把关。"""

    def __init__(self, cfg: dict, log=print, evidence_dir: str | None = None):
        self.cfg = cfg
        self.log = log
        self.evidence_dir = evidence_dir
        self._pw = None
        self.browser = None
        self.ctx = None
        self._port = None
        self._last_act = 0.0        # 动作节流时间戳（登记表 G）
        #: 本次运行**自己开**的 targetId（只关这些，绝不动用户/别家的标签页）。
        #: 2026-10-27 立：`new_tab_background` 每开一页就记一笔，`close()` 统一收掉。
        #: 为什么必须记 id 而不是记 page 对象：`/json/list` 每次返回新 dict，
        #: 且 `CdpPage.close()` 只认 targetId —— 早先的实现建完就把 id 丢了，
        #: 想关也关不掉（`CdpBrowser.close()` 本身是空实现 `return None`）。
        self._opened_targets = []
        #: 本次运行开始前就已存在的 targetId 集合（`connect()` 第一次时快照）。
        #: `close()` 只关"不在这个集合里 + 商家中心域"的页。
        #: `None` = 还没快照过（区别于"快照过但窗口是空的"）。
        self._baseline_targets = None
        #: 同上的 URL 基线：Chrome 重载会让同一页换 targetId，
        #: 光比 id 会把用户本来开着的页误判成新开而关掉。
        self._baseline_urls = None

    # ---------- 基础设施 ----------
    def connect(self, port: int):
        self._port = int(port)
        #: 紫鸟窗口名（渲染进程被回收后要按它重探端口）。由入口从 store 映射传入，
        #: 拿不到就留空（重探会失败，但不阻断业务）。
        self._window_title = str((self.cfg or {}).get("_window_title") or "")
        # 前置占用守卫（不干扰其他爬虫，登记表 I / RL-018）：被**活的** CDP 会话占着就不硬插
        if self.cfg.get("guard", {}).get("attached_check", True):
            # ⚠ 顺序要紧（2026-09-15 实测，TB-031）：**先数活客户端，再做 attached 探测**——
            # `temu_tab_occupied()` 自己会起 node raw_cdp.js 连这个端口，先探测就会把"自己"数进去
            # ⇒ 明明没有别的爬虫也会 HARD_STOP（record 动作连吃两次假占用）。
            live = live_cdp_clients(self._port)
            occ = temu_tab_occupied(self._port)
            if occ is True and live > 0:
                raise RuntimeError(
                    "HARD_STOP: TEMU 商家中心 tab 已被其它爬虫/CDP 会话占用"
                    "（attached=true 且活连接 %s 个），按『不干扰其他爬虫』原则拒绝连接；"
                    "请稍后再跑或换空闲 tab" % live)
            if occ is True:
                self.log("[守卫] attached=true 但无活 CDP 客户端（残留 attach，本机实测常见）→ 放行")
            elif occ is None:
                self.log("[守卫] 占用检测不可用（缺 node/raw_cdp.js 或探测失败）→ 跳过，不阻断")
            else:
                self.log("[守卫] 占用检测：空闲（活连接 %s 个）" % live)
        # ★ 连接层：**target-level CDP**（红线 RL-013）——不再用 Playwright `connect_over_cdp`
        #   2026-09-14 实测：`connect_over_cdp` 连紫鸟 45s/180s 超时（ws 都连上、初始化不完成），
        #   同端口原生 CDP 0.1s 即通。传输层见 lib/cdp_transport.py（照 oms_cdp.py 的形态，非自创）。
        self.browser = cdp_transport.connect(self._port)
        self.ctx = self.browser.contexts[0]
        self._pw = None
        # 进来先记下**已存在**的标签页：close() 时只关本次新开的、商家中心域的那些。
        # 用户口径（2026-10-27）：「每触发任务打开的新页面，用完即关，
        # 不能动我正在使用的页面」。这层比 `_opened_targets` 更宽 ——
        # 能连**站点自己弹出来的页**（如确认发货后自动开的 waybill.html）一起收掉。
        #
        # ⚠ 只在**第一次** connect 时快照：`reconnect()` 会再调 connect，
        #   若每次都重设基线，本次已开的页会被算进基线 ⇒ 永远关不掉（2026-10-27 踩到）。
        if getattr(self, "_baseline_targets", None) is None:
            try:
                self._baseline_targets, self._baseline_urls = \
                    cdp_transport.snapshot_tabs(self._port)
            except Exception:  # noqa: BLE001
                self._baseline_targets, self._baseline_urls = set(), set()
        for pg in list(self.ctx.pages):        # 已有的相关页也上无图拦截
            if _TEMU_HOST in (pg.url or ""):
                self._block_heavy(pg)
        return self

    def _block_heavy(self, page):
        """真无图：`Network.setBlockedURLs`（登记表 G）。失败不阻断——降载不是交付条件。"""
        if not self.cfg.get("view", {}).get("block_images", True):
            return
        try:
            cdp = self.cdp(page)
            cdp.send("Network.enable")
            cdp.send("Network.setBlockedURLs", {"urls": list(_BLOCKED_URLS)})
            cdp.detach()
        except Exception as e:  # noqa: BLE001
            self.log("[无图] 拦截失败（不阻断）：%s" % str(e)[:100])

    def _throttle(self):
        """动作节流（登记表 G：请求频率 ≥2-3s），口径 `view.min_action_gap_ms`。"""
        gap = float(self.cfg.get("view", {}).get("min_action_gap_ms", 1500)) / 1000.0
        dt = time.time() - (self._last_act or 0.0)
        if gap > 0 and dt < gap:
            time.sleep(gap - dt)
        self._last_act = time.time()

    def reconnect(self):
        """重连（target-level 下已不需要，仅保留兼容入口）。

        旧注释说的「长连接看不到新标签页」是 **Playwright browser-level** 的老问题；
        现在走 `cdp_transport`（每次调用直接读 `/json/list`），**新标签页天然可见**，无需重连兜底。
        """
        port = self._port
        if not port:
            raise RuntimeError("reconnect 前必须先 connect(port)")
        return self.connect(port)

    def close(self):
        """收尾：**关掉本次新开的商家中心标签页**，再断开连接。

        为什么要关（2026-10-27 用户口径「每触发任务打开的新页面，用完即关，
        不能动我正在使用的页面」）：每个定时任务都会开页（`new_tab_background()`
        或站点自己弹的 waybill 页），旧实现从来没人关。紫鸟店窗口一天被几个任务
        各开 1~3 页，几天后堆成几十个，白占内存、还会拖慢 `/json/list`
        （驱动每次动作都要枚举）。

        两层清理，缺一不可：
          ① `_opened_targets`：驱动**明确知道**自己开的（`new_tab_background`）；
          ② `close_new_seller_pages(baseline)`：**本次新出现的、商家中心域**的页 ——
             这一层能收到"站点自己弹出来"的页（确认发货后的 waybill.html 就是）。

        四条硬约束：
        1. **只关本次新开的**（`connect()` 之前就存在的一律不碰）；
        2. **只关商家中心域**（`SELLER_HOSTS`）。用户的 `www.temu.com` 购物页、
           外部站、扩展页一律不动；
        3. **窗口里至少留 1 个真实页**（否则店铺窗口会整个消失，2026-09-24 事故）；
        4. **关页失败绝不影响业务结果**：这是收尾清理，不是交付条件。
        """
        owned = [tid for tid in (getattr(self, "_opened_targets", []) or []) if tid]
        for tid in owned:
            try:
                cdp_transport.close_target(self._port, tid)
            except Exception as e:  # noqa: BLE001
                try:
                    self.log("[页] 关标签页失败（不阻断）：%s" % str(e)[:100])
                except Exception:
                    pass
        self._opened_targets = []
        # 第二层：连带站点自己弹出来的页一起收（只限本次新开 + 商家中心域 +
        # **能证明是我开的**）。owned 传进去当归属证据。
        try:
            closed = cdp_transport.close_new_seller_pages(
                self._port, getattr(self, "_baseline_targets", set()) or set(),
                keep_min_tabs=1, log=self.log,
                before_urls=getattr(self, "_baseline_urls", set()) or set(),
                owned_ids=set(owned))
            if closed:
                self.log("[页] 收尾共关掉本次新开页 %d 个" % len(closed))
        except Exception:  # noqa: BLE001
            pass
        self._baseline_targets = None
        self._baseline_urls = None
        try:
            if self.browser is not None:
                self.browser.close()
        except Exception:
            pass
        self.browser = None
        self.ctx = None

    def cdp(self, page):
        """该页的 CDP 会话（原生 CDP 通道：后台点击 / 无图拦截都走它）。"""
        return self.ctx.new_cdp_session(page)

    def new_tab_background(self, url: str, tries: int = 8):
        """**后台**新建标签页并导航（不抢前台）。

        为什么不用 `ctx.new_page()`：Playwright 的 new_page 是**前台**开页，会把紫鸟窗口提到最前
        （用户 2026-09-14 实测反馈"窗口会置顶"）。这里改走原生 CDP `Target.createTarget(background=true)`；
        新建 target 本连接枚举不到（见 reconnect 注释），故建完**重连一次**再按 URL 找回。
        先复用同前缀 URL 的现存页；**失败不退回前台开页**（退回＝把老毛病带回来）。
        """
        key = url.split("?")[0]
        try:
            # ★ 2026-10-28：**只复用本次自己开的**同址页（幂等），绝不复用用户自己的页。
            #   旧实现按 URL 前缀复用，命中的是用户的订单页 ⇒ 业务动作全落在用户页上。
            for p in self._own_pages():
                if _url_same_target(self._raw_url(p), url):
                    self._block_heavy(p)
                    return p
        except Exception:  # noqa: BLE001
            pass
        created = False
        tid = ""
        try:
            bs = self.browser.new_browser_cdp_session()
            r = bs.send("Target.createTarget", {"url": url, "background": True})
            # 记下 targetId：close() 靠它把这一页收掉。以前这里把返回値丢了，
            # 于是「任务开过的页」变成无人认领的孤儿（2026-10-27 修）。
            tid = ((r or {}).get("result") or {}).get("targetId") or ""
            try:
                bs.detach()
            except Exception:
                pass
            created = True
        except Exception as e:  # noqa: BLE001
            self.log("[页] CDP 后台开页失败：%s" % str(e)[:140])
        if tid:
            self._opened_targets.append(tid)
        for _ in range(tries if created else 1):
            time.sleep(1.5)
            try:
                self.reconnect()
            except Exception as e:  # noqa: BLE001
                self.log("[页] 重连失败：%s" % str(e)[:120])
                break
            for p in list(self.ctx.pages):
                # ★ 2026-10-28：只按**新 targetId** 认页，不再按 URL 找。
                #   按 URL 找会在新页还没枚举出来时命中「用户本来就开着的那张同址页」
                #   ⇒ 后面所有业务动作（筛选/改每页/确认）全落在用户的页上。
                real = getattr(p, "targetId", "") or ""
                if not real:
                    continue
                if real != tid and real not in (self._opened_targets or []):
                    continue
                if not _url_same_target(self._raw_url(p) or url, url):
                    continue                    # 还没导航到目标页，下一轮再看
                if real and real not in self._opened_targets:
                    self._opened_targets.append(real)
                try:
                    p.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:  # noqa: BLE001
                    pass
                self._block_heavy(p)
                return p
        raise RuntimeError("后台开页后仍未枚举到该页（拒绝回退前台开页）：%s%s"
                           % (url[:120], self._auth_lost_hint()))

    #: 登录态失效时的落地页特征（2026-09-15 实测：窗口重开后跳 auth/authentication，见 TLA-012）。
    #: ⚠ 只认**认证/授权页**，不要把 `seller.kuajingmaihuo.com/login` 算进来——那个扫码登录页是
    #: 窗口里常驻的无关 tab（TLA-002/TLA-014），拿它当"登录态失效"会给出误判提示（本次实测踩到）。
    _AUTH_URL_HINTS = AUTH_URL_HINTS

    def _auth_lost_hint(self) -> str:
        """页面被重定向到登录/认证页时，追加一句**点名真因**的提示（TB-030）。

        旧实现只报「后台开页后仍未枚举到该页」——看着像标签页枚举 bug，真因却是登录态失效，
        本次白跑一轮。这里用原始 CDP（不依赖 Playwright 对象）复核窗口实际落地页。
        """
        try:
            return auth_lost_hint_from_urls([t.get("url") for t in self._cdp_pages()])
        except Exception:  # noqa: BLE001  只是加提示，探测失败不影响原报错
            return ""

    def cdp_click(self, page, x: float, y: float):
        """原生 CDP 派发鼠标点击（不依赖前台/焦点；替代 `page.mouse.click`）。

        ⚠ 2026-10-28 修（BEL 实测，关键）：原来三个事件**都不带 `buttons`**。
          对「每页」下拉这种 beast-core Select，不带 `buttons` 的序列**展不开** ——
          同一时刻同一坐标对照：
            · 不带 buttons   → 选项 0 个（`no-option:选项=`）
            · 带 buttons:1   → 立刻拿到 5/10/20/50/100/500
          ⇒ 补上 `buttons`（moved=0 / pressed=1 / released=0）。这是标准鼠标序列，
          对其他控件等价，不引入副作用。
        """
        self._throttle()
        cdp = self.cdp(page)
        try:
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mouseMoved", "x": x, "y": y, "buttons": 0})
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mousePressed", "x": x, "y": y,
                      "button": "left", "buttons": 1, "clickCount": 1})
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mouseReleased", "x": x, "y": y,
                      "button": "left", "buttons": 0, "clickCount": 1})
        finally:
            try:
                cdp.detach()
            except Exception:
                pass

    def wait(self, key: str, factor: float = 1.0):
        ms = self.cfg.get("batch", {}).get("wait_ms", {}).get(key, 3000)
        time.sleep(max(0.3, ms / 1000.0 * factor))

    def shot(self, page, name: str):
        """截一张证据图。**返回落盘路径**（弹窗登记表要把证据指过去），失败/无证据目录返回 ""。"""
        if not self.evidence_dir:
            return ""
        try:
            os.makedirs(self.evidence_dir, exist_ok=True)
            cdp = self.ctx.new_cdp_session(page)
            r = cdp.send("Page.captureScreenshot", {"format": "png"})
            import base64
            # 2026-09-15 修：`cdp_transport.Cdp.send()` 返回的是**整条 CDP 消息**（`{"id":..,"result":{..}}`），
            # 旧代码取 `r["data"]` → KeyError 'data' → 所有证据截图落盘成 0 字节（截图通道形同失效）。
            data = (r.get("result") or {}).get("data") if isinstance(r, dict) else None
            if not data:
                raise RuntimeError("截图无 data")
            path = os.path.join(self.evidence_dir, name + ".png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            self.log("[证据] " + path)
            return path
        except Exception as e:
            self.log("[证据] 截图失败 %s: %s" % (name, e))
            return ""

    #: 页面导航把执行上下文销毁＝瞬时竞态（2026-09-14 fill 两次 exit=42 的根因），可重试
    _NAV_RACE = ("Execution context was destroyed", "most likely because of a navigation",
                 "Cannot find context with specified id", "Target closed", "Frame was detached")

    def _js(self, page, script, arg=None, tries: int = 3):
        """evaluate + 导航竞态重试。

        2026-09-14 实测：`--action fill` 两次 exit=42，日志尾＝Playwright
        `Execution context was destroyed, most likely because of a navigation`
        （点击后页面 SPA 跳转，evaluate 打在被销毁的上下文上）。竞态是瞬时的 →
        等页面稳定后重试（≤tries 次），别让它把整个动作打成 HARD_STOP。
        """
        last = None
        for i in range(max(1, tries)):
            try:
                return page.evaluate(script, arg) if arg is not None else page.evaluate(script)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "Target page, context or browser has been closed" in msg:
                    return "__CLOSED__"
                if not any(k in msg for k in self._NAV_RACE):
                    raise
                last = e
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                time.sleep(1.0 + i)
        raise last if last else RuntimeError("evaluate 失败")

    def dismiss_popups(self, page, why="", max_rounds=3, strict=False):
        """**动作前随时检测并处理遮挡弹窗**（2026-09-19 用户：「你要能随时检测弹窗」）。

        动作按 `data/temu_buylabel_popups.json` 走（2026-09-21 改：原来是写死在代码里的一份常量白名单）：
          - `dismiss` → 点关闭键关掉，继续干活；
          - `click`   → 点登记表指定的按钮（如平台自己的「剔除并继续」），继续；
          - `stop`    → 取证 + **仅 strict 下** HARD STOP；非严格模式放过不点——流程自己会打开
            正常模态框（如「批量填充物流服务」「共 N 个订单，确认下单？」），一律拦死会把自己
            的流程打断（2026-09-19 实测：`all` 在「物流服务一键填充」处被自己的闸门劝退）；
          - **未登记** → 截图 + **自动登记进 unknown[]**（下次它就在表里＝待确认），strict 下 HARD STOP。
        只有遮罩没有实体窗 → 视为过渡态，**不猜着点**，直接放过。
        返回已处理/已登记的弹窗标题列表（空＝本来就没有，或只有被放过的）。
        """
        closed = []
        for _ in range(int(max_rounds)):
            try:
                raw = self._js(page, _JS_POPUP_PROBE, tries=1)
            except Exception:  # noqa: BLE001  探测本身失败不阻断（按"没弹窗"处理）
                return closed
            try:
                d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:  # noqa: BLE001
                return closed
            if not isinstance(d, dict) or not d.get("present"):
                return closed
            title = str(d.get("text") or d.get("title") or "")
            if d.get("maskOnly"):
                return closed
            url = str(getattr(page, "url", "") or "")
            reg = load_popup_registry(cfg=self.cfg)
            entry = match_popup(title, d.get("text") or "", d.get("buttons"), reg)

            # ★ 2026-10-08 补：**无文本的纯图片推广弹窗**（BEL 订单页实测）。
            #   现象：div[data-testid=beast-core-modal-container].MDL_* → 只有 img.lazy-image，
            #   **无 innerText、无 button** ⇒ title/text/buttons 三者皆空。
            #   而登记表 when 的三条判据（title_contains/text_regex/buttons_have）都表达不了
            #   「什么都没有」的弹窗（硬约束「全空判据不许命中」），故它必然落到「未登记」分支。
            #   原来在 strict 下这里直接 HARD STOP ⇒ BEL 的 record/fill 等步骤每跑必死。
            #   判据收紧到三条同时成立才自动关（其余未登记弹窗一律维持原行为，不放松闸门）：
            #     ① 探测到 testid 关闭键 hasClose（有确定的关闭点，不是"猜着点"）
            #     ② 无任何文本（纯图片，不承载业务语义）
            #     ③ 无任何按钮（没有要人决策的选项）
            #   仍截图+登记留痕（hits 继续累加），便于事后核对。
            #   ⚠ **触发条件（用户 2026-10-08 口径）**：它只在**「登录态首次进入订单页」**时出现，
            #     不是每次必弹——关掉后重载 orders.html 不再复现。所以排查时**复现不出属正常**，
            #     别据此以为上面这段失效；要复现须先让登录态失效再走一次 auth。
            _pic_only = bool(d.get("hasClose")) and not title.strip() and not (d.get("buttons") or [])
            if not entry and _pic_only:
                shot = self.shot(page, "_popup_pic_only")
                rec = register_unknown_popup(title, d.get("text") or "", d.get("buttons"),
                                             url=url, step=why, shot=shot, cfg=self.cfg)
                at = d.get("closeAt")
                self.log("[弹窗] 无文本纯图片弹窗（有 testid 关闭键）→ 自动关掉继续；已登记 hits=%s"
                         % rec.get("hits"))
                if at:
                    self.cdp_click(page, at[0], at[1])
                    self.wait("after_popup", 0.3)
                    closed.append(title or "（无标题弹窗）")
                    continue                    # 关了再探一轮，确认真的没了
                # 拿不到关闭点 → 退回原严格行为，不硬闯
                if strict:
                    raise RuntimeError(
                        "遇到**未知弹窗（未登记）**「无标题」→ HARD STOP：探测到关闭键却取不到坐标。页面 %s" % url)

            if not entry:                       # 未登记：取证 + 自动登记（固化入口）
                # 截图名沿用旧语义（别为了统一改掉既有留痕）：stric 停 = _popup_unknown，放过 = _popup_seen_unknown
                shot = self.shot(page, "_popup_unknown" if strict else "_popup_seen_unknown")
                rec = register_unknown_popup(title, d.get("text") or "", d.get("buttons"),
                                             url=url, step=why, shot=shot, cfg=self.cfg)
                if strict:
                    raise RuntimeError(
                        "遇到**未知弹窗（未登记）**「%s」→ HARD STOP：不连点、不硬闯（SKILL D13）。"
                        "已自动登记进弹窗表（hits=%s）——确认后给它一个 act 就固化了。页面 %s"
                        % (title or "无标题", rec.get("hits"), url))
                if title not in closed:
                    self.log("[弹窗] 未知弹窗（未登记）「%s」（%s）→ 放过不点（非严格模式）；已登记 hits=%s"
                             % (title, why or "-", rec.get("hits")))
                    closed.append(title)        # 只记一次，避免每步重复截图
                return closed

            act = str(entry.get("act") or "stop")
            bump_popup_hits(entry, reg, cfg=self.cfg)

            if act == "stop":
                if strict:
                    self.shot(page, "_popup_stop")
                    raise RuntimeError("弹窗「%s」登记为 stop → HARD STOP（%s）。页面 %s"
                                       % (title, entry.get("_why") or "待人工确认", url))
                if title not in closed:
                    self.shot(page, "_popup_stop_seen")
                    self.log("[弹窗] 「%s」登记为 stop（%s）→ 非严格模式放过不点"
                             % (title, entry.get("_why") or "-"))
                    closed.append(title)
                return closed

            if act == "click":
                btn = str((entry.get("when") or {}).get("button") or entry.get("button") or "").strip()
                if not btn:
                    if strict:
                        raise RuntimeError("弹窗「%s」登记 act=click 却没给 button → HARD STOP（去改数据）" % title)
                    return closed
                r = self._js(page, _JS_CLICK_BUTTON, btn)
                self.log("[弹窗] 点「%s」：%s（%s）" % (btn, r, why or "-"))
                if r != "ok":
                    if strict:
                        raise RuntimeError("弹窗「%s」的按钮「%s」点不到（%s）" % (title, btn, r))
                    return closed
                self.wait("after_popup", 0.3)
                closed.append(title)
                continue

            # 默认 act=dismiss：点关闭键（没有 testid 关闭键时，仅当登记表显式写了
            # `"close": "approx"` 才用探测给出的**近似右上角点**——未登记的弹窗永远拿不到这个点）
            at = d.get("closeAt") if d.get("hasClose") else None
            if not at and str(entry.get("close") or "") == "approx" and d.get("approxAt"):
                at = d.get("approxAt")
                self.log("[弹窗] 「%s」没有 testid 关闭键 → 用登记表声明的近似关闭点 %s" % (title, at))
            if not at:
                if strict:
                    self.shot(page, "_popup_noclose")
                    raise RuntimeError("弹窗「%s」没有关闭键 → HARD STOP（不猜着点「去处理」）" % title)
                return closed
            self.log("[弹窗] 关掉遮挡弹窗：%s（%s）" % (title, why or "-"))
            self.cdp_click(page, at[0], at[1])
            self.wait("after_popup", 0.3)
            closed.append(title)
        return closed

    def _click_button(self, page, text, optional=False):
        self._throttle()
        self.dismiss_popups(page, "点按钮前:%s" % text)
        r = self._js(page, _JS_CLICK_BUTTON, text)
        if r != "ok" and not optional:
            raise RuntimeError("点不到按钮：%s (%s)" % (text, r))
        return r

    # ---------- 订单列表页 ----------
    def _own_targets(self) -> set:
        """本次运行**自己开**的 targetId 集合（`close()` 也是按它收尾的）。"""
        return {t for t in (getattr(self, "_opened_targets", []) or []) if t}

    def _own_pages(self) -> list:
        """本次运行自己开的页（不含用户自己的页）。"""
        mine = self._own_targets()
        out = []
        for p in list(self.ctx.pages or []):
            if (getattr(p, "targetId", "") or "") in mine:
                out.append(p)
        return out

    @staticmethod
    def _raw_url(page) -> str:
        """取页的 URL **不触发** `Runtime.evaluate`（死页上 evaluate 会卡 60s）。"""
        return str(getattr(page, "_url", "") or "")

    def _own_page_by(self, pred):
        """在自己开的页里按 URL 谓词找一个；没有返回 None。"""
        for p in self._own_pages():
            try:
                if pred(self._raw_url(p)):
                    return p
            except Exception:  # noqa: BLE001
                continue
        return None

    def orders_page(self):
        """取订单列表页 —— **只返回我们自己开的那一页**，没有就新开一个。

        ★ 2026-10-28 修（用户投诉「尤其是 BEL，为什么把我之前的订单页面覆盖了」）：
          旧实现是「窗口里有订单页就复用它」，命中的必然**是用户自己开着的那个**。
          虽然 `mark_foreign()` 挡住了 `goto`，但挡不住后续业务动作 ——
          逾期确认会在这张**用户的页**上点「即将逾期未发货」筛选、把「每页」改成 500、
          逐单确认 17 单。跑完页还在（它是用户的页，本来就不该关），但用户看到的
          就是「我的订单页被覆盖了、还没关掉」。

          结论：**复用页只能读、不能当工作区**。工作区一律自己开，跑完由 `close()` 收掉。
          要故意复用某张已填好的批量页，走 `--reuse-page` / `find_reuse_page()`（人工指定）。
        """
        mine = self._own_page_by(_url_is_orders)
        if mine is not None:
            self._block_heavy(mine)
            return mine
        return self.new_tab_background(self.cfg["orders_url"])

    def _heal_hung_orders_page(self) -> str:
        """订单页渲染进程卡死时修复它。返回 `ok`/`healed`/`noport`/`none`。

        为什么需要（2026-10-27 实测）：BEL 的订单页渲染进程会卡死 ——
        浏览器层命令 0.1s 正常，但 `Runtime.evaluate` 一律 60s `TimeoutError`。
        症状是日志只有 `[抽屉] 探测失败（TimeoutError）` 和一句**空消息**的
        `[FAIL]`，然后重试 3 次全撞同一张死页，45 分钟被强杀。
        以前**没有任何检测**，所以只会无脑重试。

        ★ 修复顺序（2026-10-27 实测校正，很重要）：
          ① **优先 `Page.navigate` 重载**（走浏览器进程，不需要死掉的 JS 线程）。
             不关页 ⇒ 不会连带把店铺窗口搞没。
          ② 只有在"窗口里还有别的页"时才允许**关页重开**。
             ⚠ 实测事故：订单页是窗口里**唯一**一页时，关掉它连带整个
               紫鸟店铺窗口消失（端口都没了）⇒ 反而更糟。
        """
        try:
            pages = cdp_transport.list_pages(self._port)
        except Exception:  # noqa: BLE001
            return "noport"
        # ★ 2026-10-28：**只体检/替换我们自己开的订单页**。旧实现取「窗口里第一个订单页」
        #   —— 那正是用户的页；对它做重载/关重开，等于把用户的页动掉。
        mine = self._own_targets()
        target = None
        for x in pages:
            tid = x.get("targetId") or x.get("id")
            if tid in mine and _url_is_orders(x.get("url") or ""):
                target = x
                break
        if target is None:
            return "none"

        # 先体检
        try:
            st = cdp_transport.probe_page_alive(self._port, target)
        except Exception as e:  # noqa: BLE001
            self.log("[页] 卡死检测失败（不阻断）：%s" % str(e)[:90])
            return "none"
        if st != "hung":
            return "ok"

        # ① 安全替换：**先开新页、再关死页**（实测唯一能保住窗口的顺序）
        url = str(target.get("url") or self.cfg.get("orders_url") or "")
        try:
            r = cdp_transport.replace_hung_page(self._port, target, url, log=self.log)
        except Exception as e:  # noqa: BLE001
            self.log("[页] 替换卡死页异常：%s" % str(e)[:90])
            r = "failed"
        if r == "replaced":
            self.log("[页] 订单页卡死已自愈（新页已就绪）")
            return "healed"
        if r == "alive":
            return "ok"
        self.log("[页] 卡死页替换失败（%s）" % r)
        return "none"

    def _orders_page_ready(self):
        """取订单列表页并确保已加载：已在该页就**不重复导航**。

        2026-09-14 实测：`orders_page()` 之后紧接着又 `goto` 同一 URL，连着两次导航 +
        立刻 evaluate = 正好撞上 `Execution context was destroyed`（fill 两次 exit=42 的根因）。

        ★ 2026-10-27 加：**进业务前先给订单页做一次体检**。BEL 的渲染进程会卡死
          （浏览器层正常、JS 线程无响应），旧代码没有任何检测 ⇒ 无脑重试同一张死页，
          重试 3 次全一样，最后 45 分钟被强杀，日志只有一句空消息的 `[FAIL]`。
          现在检测到卡死就关掉重开，最多一轮。

        ★ 2026-10-28：`orders_page()` 现在**只给自己的页**（见那里的说明），所以这里的
          `goto` 是安全的；`is_foreign` 闸门保留给「别处传进来的复用页」兜底。
        """
        healed = self._heal_hung_orders_page()
        if healed == "healed":
            self.log("[页] 订单页卡死已自愈（关掉重开）")
        page = self.orders_page()
        try:
            if not _url_is_orders(page.url or ""):
                # 自己开的页不是订单页（比如还停在 about:blank/首页）→ 导航过去。
                # ⚠ 若哪天这里拿到的是复用页，`goto` 会抛 ForeignPageError，下面兜底开新页。
                page.goto(self.cfg["orders_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except ForeignPageError:
            self.log("[页] 拒绝改写复用页 → 改为新开自己的订单页")
            page, _ = page.open_own_tab(self.cfg["orders_url"])
        except Exception:
            pass
        self.wait("after_goto", 0.5)
        # ★ 2026-09-24 修（no-input 事故）：**先判页面身份，再往下走**。原来这里不判，落到认证页时
        #   下游只会报「订单号筛选框填不进去：no-input」（今天 11:18 BEL / 11:22 BHY 两条就是），
        #   字面看不出"其实没授权/页面没就位"，也没留图。现在当场点名真因 + 落图。
        hint = auth_lost_hint_from_urls([str(page.url or "")], str(self.cfg.get("_store") or ""))
        if hint:
            self.shot(page, "00_auth_required")
            raise RuntimeError("订单列表页未就绪：页面停在授权页（url=%s）%s"
                               % (str(page.url or "")[:90], hint))
        # 起点检查（strict）：页面刚就绪，**不该**有任何弹窗；有未知弹窗＝真异常，停下取证
        # ★ 2026-09-30：同样的道理——`record` 留下的「在线下单记录」抽屉也**不该**在这时候压着列表。
        self.close_record_drawer(page, "订单页就绪")
        self.dismiss_popups(page, "订单页就绪", strict=True)
        return page

    def set_list_page_size(self, page, need: int) -> str:
        """把订单列表「每页」调到够显示**本批全部单**（2026-09-15 用户方案 A）。

        为什么必须有这一步：列表默认每页 20 行；31 单查询后只渲染前 20 行 →
        ① 只读当前页的缺单检查会误报「没查到」（本次 11 单被误判）；
        ② 就算绕过检查，`全选` 也只勾当前页 → 只会买 20 单面单（部分下单）。
        返回状态串：`already:<当前值>` / `set:<新值>` / `no-sc:<原因>`（**不抛错**，
        真有问题由后面的缺单检查兜底——那里会带当前页行数一起报出来）。
        """
        cap = int(self.cfg.get("batch", {}).get("page_size", 500) or 500)
        # ★ 2026-10-28 修（BEL 实测）：**先等列表和下拉都稳定再动**。
        #   调用方刚 `_orders_page_ready()` 回来时，列表还在异步重取；实测同一页：
        #     · 刚就绪时 head.x=1721（DOM 未稳）→ 点下去 `no-option:选项=`
        #     · 稳定后  head.x=2039 → 点下去立刻拿到 5/10/20/50/100/500
        #   ⇒ 判据必须是「行数不变 **且** 下拉坐标不变」，只等行数不够（踩过）。
        #   最多等 10s，超时就用最后一次拿到的坐标（总比不等强）。
        _prev = (-1, -1, -1)
        for _ in range(12):
            try:
                _r = int(page.evaluate(
                    "() => document.querySelectorAll('table tbody tr').length") or 0)
            except Exception:  # noqa: BLE001
                _r = -1
            try:
                _h = self._js(page, _JS_PAGE_SIZE_HEAD) or {}
            except Exception:  # noqa: BLE001
                _h = {}
            _sig = (_r, _h.get("x") if isinstance(_h, dict) else -1,
                    _h.get("y") if isinstance(_h, dict) else -1)
            if _r > 0 and _sig == _prev:
                break
            _prev = _sig
            time.sleep(0.8)
        # TB-039（2026-09-16 实测 BEL 22 单）：pager 在列表底部，表头中心点可能落在**视口外**
        # （elementFromPoint 返回 null）→ `cdp_click` 点空处 → 下拉不展开 → `no-option:选项=`
        # → 每页停在默认 20 → 后 N 单落页外（这正是"22 单被迫拆成 20+2"的真因）。
        # 先滚进视口再量坐标，是本步能成功的**必要条件**。
        self._js(page, _JS_PAGE_SIZE_SCROLL)
        time.sleep(0.8)
        h = self._js(page, _JS_PAGE_SIZE_HEAD)
        if not isinstance(h, dict) or h.get("state") != "ok":
            return "no-sc:" + str((h or {}).get("state") if isinstance(h, dict) else h)
        cur = str(h.get("value") or "").strip()
        # 2026-09-17 用户定：每页**一律拉满**（cap=口径 batch.page_size，实测 500）——
        # 所以只有"已经在 cap 上"才免动；否则即使当前值够本批（如 20 ≥ 7 单）也照样拉满。
        if cur.isdigit() and int(cur) >= int(cap):
            return "already:" + cur
        if h.get("inViewport") is False:
            # 滚了仍不在视口内 → 如实报，不点空枪（点空枪会静默失败成 no-option，看着像选择器坏了）
            return "no-viewport:%s,%s" % (h.get("x"), h.get("y"))
        # ★ 2026-10-28 修（BEL 实测，两层真因）：
        #   ① 点 **sizeChanger 容器**而不是 header（容器才展开）；
        #   ② **新开的页布局不同**：现有页 header 在 x=2045，新页在 x=1721，而
        #      x=1715 那个位置被 `INPUT.IPT_input_123` 占着 —— `elementFromPoint`
        #      实测命中输入框，所以**真实鼠标事件打不中下拉**，只能靠 DOM click。
        #   ⇒ 顺序改为：容器坐标点 → 坐标点 header → **DOM click**（最后兜底，实测有效）。
        def _open_dropdown() -> list:
            # ① 容器坐标（现有页上这条就够）
            box = self._js(page, _JS_PAGE_SIZE_BOX)
            if isinstance(box, dict) and box.get("state") == "ok":
                self.cdp_click(page, box["x"], box["y"])
                self.wait("after_click", 0.6)
                got = self._js(page, _JS_PAGE_SIZE_OPTIONS) or []
                if got:
                    return got
            # ② header 坐标（万一 DOM 结构回退）
            self.cdp_click(page, h["x"], h["y"])
            self.wait("after_click", 0.6)
            got = self._js(page, _JS_PAGE_SIZE_OPTIONS) or []
            if got:
                return got
            # ③ **DOM click**：新页上坐标被输入框盖住，只有这条能展开
            #    （实测：head.click() → 立刻拿到 5/10/20/50/100/500）
            try:
                self._js(page, _JS_PAGE_SIZE_DOM_OPEN)
            except Exception:  # noqa: BLE001
                pass
            self.wait("after_click", 0.8)
            return self._js(page, _JS_PAGE_SIZE_OPTIONS) or []

        opts = _open_dropdown()
        if not opts:                              # 展开失败 → 重滚一次再点一次（瞬时竞态兜底）
            self._js(page, _JS_PAGE_SIZE_SCROLL)
            time.sleep(0.8)
            opts = _open_dropdown()
        pick = pick_page_size_option(opts, int(need), cap=cap)
        if not pick:
            page.keyboard.press("Escape")
            return "no-option:选项=%s" % ",".join(str(o.get("text")) for o in opts)[:60]
        hit = self._js(page, _JS_PICK_PAGE_SIZE, pick)
        if hit != "ok":
            xy = [o for o in opts if str(o.get("text")).strip() == pick]
            if not xy:
                page.keyboard.press("Escape")
                return "no-option:%s" % pick
            self.cdp_click(page, xy[0]["x"], xy[0]["y"])
        self.wait("after_query", 0.5)             # 列表按新页大小重取
        # 读回校验：下拉值 + 实际渲染行数都要等它刷新完（列表重取是异步的）
        got = ""
        rows = 0
        for _ in range(10):
            v = self._js(page, _JS_PAGE_SIZE_HEAD)
            got = str((v or {}).get("value") or "").strip() if isinstance(v, dict) else ""
            try:
                rows = int(page.evaluate(
                    "() => document.querySelectorAll('table tbody tr').length") or 0)
            except Exception:  # noqa: BLE001
                rows = 0
            if got == pick:
                break
            time.sleep(0.6)
        # ⚠ 2026-10-28：原来只读一次 head.value，列表还没重取完就读到**旧值**，
        #   于是明明设成 500 却返回 `set:20`（看着像失败，实际已生效）。
        #   现在轮询到值真的变了才返回，并把行数一起带上便于核对。
        return "set:%s(rows=%d)" % (got or pick, rows)

    def _search_orders(self, page, tab: str, order_nos: list[str]):
        self.dismiss_popups(page, "筛选前")   # 遮罩会盖住筛选框 ⇒ no-input，先清障
        self._js(page, _JS_CLICK_TAB, tab)
        time.sleep(3)
        r = self._js(page, _JS_FILL_ORDER_FILTER, " ".join(order_nos))
        if r != "ok" and not str(r).startswith("ok:"):
            self.dismiss_popups(page, "筛选失败后复检")
            self.clear_blockers(page)          # 清障步：遮罩挡着 ⇒ no-input，按步骤关掉再重填
            r = self._js(page, _JS_FILL_ORDER_FILTER, " ".join(order_nos))   # 清障后重填一次
        if r != "ok" and not str(r).startswith("ok:"):
            # ★ 2026-09-24 修：这条失败原来只抛 `no-input`、**一张图都不留**，事后只能靠推断。
            #   现在先落图，并把页面 URL 与"是否停在授权页"一起写进错误里。
            self.shot(page, "00_filter_no_input")
            _u = str(getattr(page, "url", "") or "")
            _hint = auth_lost_hint_from_urls([_u], str(self.cfg.get("_store") or ""))
            raise RuntimeError(
                "订单号筛选框填不进去：%s（当前页面 url=%s）——多为页面不在订单列表："
                "登录态失效被弹回认证页，或窗口刚打开、页面还没就位%s"
                % (r, _u[:90], _hint))
        time.sleep(1.5)
        self._click_button(page, "查询")
        self.wait("after_query")
        # ★ 2026-09-30 加：**回读筛选框**，确认这批单号真填进去了。实测有过「填单报 ok、列表却没按它筛」
        #   （读到 192/500/500 行的全量列表）——只看返回码看不出来，回读才发现。
        fv = self._js(page, _JS_ORDER_FILTER_VALUE)
        if not (isinstance(fv, dict) and fv.get("state") == "ok" and int(fv.get("tokens") or 0) >= len(order_nos)):
            self.log("[列表] ⚠ 筛选框回读不符（%s）→ 清障后重填一次" % json.dumps(fv, ensure_ascii=False)[:120])
            self.dismiss_popups(page, "筛选回读不符")
            self.clear_blockers(page)
            r2 = self._js(page, _JS_FILL_ORDER_FILTER, " ".join(order_nos))
            time.sleep(1.0)
            self._click_button(page, "查询")
            self.wait("after_query")
            fv2 = self._js(page, _JS_ORDER_FILTER_VALUE)
            self.log("[列表] 重填后筛选框：%s（fill=%s）" % (json.dumps(fv2, ensure_ascii=False)[:100], r2))
        _default = int(self.cfg.get("batch", {}).get("page_default", 20) or 20)
        _st = self.set_list_page_size(page, len(order_nos))
        if not page_size_status_ok(_st, len(order_nos), _default)[0]:
            # 下拉展开是瞬时竞态 → 再试一次（2026-09-17：no-sc 也可能只是列表 0 行，别急着判死）
            self.log("[页大小] 第一次没调成功（%s）→ 重试一次" % _st)
            time.sleep(1.0)
            _st2 = self.set_list_page_size(page, len(order_nos))
            _st = "%s→%s" % (_st, _st2)
        self._page_size_status = _st          # 供缺单分诊按「行数证据」定性（page_size_suspect）
        self.log("[列表] 每页条数：%s（本批 %d 单必须一页显示完）" % (_st, len(order_nos)))

    # ---------- ① 开批量在线下单页 ----------
    def _batch_head(self, page) -> str:
        """批量页页头「在线下单 共 N 个订单」里的 N（读不到 = 空串）。"""
        return self._js(page, _JS_BATCH_HEAD) or ""

    def find_reuse_page(self, spec: str):
        """P2：按 `--reuse-page` 找**已存在**的批量页（不新开页）。"""
        pages = list(self.ctx.pages)
        urls = [p.url or "" for p in pages]
        i = pick_reuse_index(urls, spec)
        if i < 0:
            n = sum(1 for u in urls if "online-shipping" in u)
            raise RuntimeError("--reuse-page %s 没匹配到批量页（窗口里现有 online-shipping 页 %d 个）"
                               % (spec, n))
        return pages[i]                       # 不 bring_to_front（不抢前台）

    # ---------- ①b 只走最后一步（复用已填好的页 → 确认下单）----------
    def read_filled(self, page) -> dict:
        """只读：读当前批量页的填单状态（不点任何按钮、不改值）。"""
        return self._js(page, _JS_READ_FILLED) or {}

    def find_filled_batch_page(self):
        """窗口里唯一「已填好」的批量页；0 个或多个都报错（多个必须用 --reuse-page 指定）。"""
        cands = []
        for p in self.ctx.pages:
            if "online-shipping" not in (p.url or ""):
                continue
            try:
                st = self.read_filled(p)
            except Exception:
                continue
            if (st or {}).get("weights"):
                cands.append((p, st))
        if not cands:
            raise RuntimeError("窗口里没有已填好的批量在线下单页（先用 --action fill 或给 --reuse-page）")
        if len(cands) > 1:
            raise RuntimeError("有 %d 个已填好的批量页，请用 --reuse-page 指定（序号 1,2,3… 或 URL 片段如 _x_sessn_id）"
                               % len(cands))
        return cands[0][0]

    def ready_to_place(self, order_nos: list, reuse_page: str | None = None):
        """定位「确认前」的批量页并过闸门。返回 (page, state, reasons)；reasons 非空＝不可下单。"""
        page = self.find_reuse_page(reuse_page) if reuse_page else self.find_filled_batch_page()
        self.wait("after_goto", 0.5)
        st = self.read_filled(page)
        ok, reasons = judge_filled(st, order_nos, page.url or "")
        return page, st, reasons

    def save_state(self, state: dict, name: str, extra: dict | None = None):
        """把「确认前状态」落盘成 json（页面刷新/关掉后可照着重填，或核对是否还是那页）。"""
        if not self.evidence_dir:
            return None
        try:
            os.makedirs(self.evidence_dir, exist_ok=True)
            path = os.path.join(self.evidence_dir, name + ".json")
            payload = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "state": state}
            payload.update(extra or {})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.log("[状态] " + path)
            return path
        except Exception as e:
            self.log("[状态] 落盘失败 %s: %s" % (name, e))
            return None

    def _find_new_batch_page(self, before_urls: set, order_nos: list[str], timeout_s: int = 20):
        """P1：按 URL 找**新开**的批量页：含 online-shipping、且不在点击前的 URL 集合里。

        **2026-09-19 修（TEMU 改版）**：**不再要求 URL 里含目标单号**。
        平台现在用服务端 key 开页：`online-shipping.html?big_array_key=<uuid>&open_type=OnlineShip`
        ——**URL 里没有单号** ⇒ 旧判据认不出它 ⇒ 误判"没等到批量页" ⇒ 回落自拼 URL 的页；
        而那张自拼页**提交不被平台接受**（没有服务端 key）⇒ 一路"成功"却不下单（静默无效）。
        新判据：**新出现的 online-shipping 页即认**；多张时**优先"平台自己开的"**
        （带 `big_array_key` 或 URL 里没有单号的那种），因为它才带服务端 key。
        """
        t0 = time.time()
        while True:
            cands = [p for p in self.ctx.pages
                     if "online-shipping" in (p.url or "") and (p.url or "") not in before_urls]
            if cands:
                for p in cands:
                    u = p.url or ""
                    if "big_array_key" in u or not any(o in u for o in order_nos):
                        return p          # 平台自己开的页（带服务端 key）优先
                return cands[0]
            if time.time() - t0 >= timeout_s:
                return None
            time.sleep(1)

    def _wait_orders_selected(self, page, need: int, timeout_s: float = 12.0) -> int:
        """等「全选」生效再往下走；返回勾选数（含表头）。2026-09-21：固定 sleep(1.5) 会不开页。
        """
        want = int(need) + 1          # +1 = 表头全选框
        t0 = time.time()
        n = 0
        while time.time() - t0 < float(timeout_s):
            try:
                n = int(self._js(page, "() => Array.from(document.querySelectorAll('input[type=checkbox]')).filter(e => e.checked).length") or 0)
            except Exception:  # noqa: BLE001
                n = 0
            if n >= want:
                return n
            time.sleep(0.8)
        self.log("[①] ⚠ 全选等待超时：勾选数 %d（期望 ≥%d）" % (n, want))
        return n

    def _open_batch_by_click(self, page, order_nos: list[str]):
        """P1 兜底：点「批量在线下单」→ URL 判据找新页 → 找不到就重连一次再枚举。"""
        before_urls = {p.url for p in self.ctx.pages}
        r = self._js(page, _JS_CHECK_ORDERS_ALL)
        if r != "ok" and not str(r).startswith("ok@"):
            raise RuntimeError("订单列表全选失败：%s" % r)
        self._wait_orders_selected(page, len(order_nos))     # 选择渲染是竞态：等够再点（步骤，不靠 sleep 猜）
        self._click_button(page, "批量在线下单")
        batch = self._find_new_batch_page(before_urls, order_nos, timeout_s=20)
        if batch is None:
            self.log("[①] 20s 内没看到新页 → 重连一次（新连接会枚举已存在的标签页）")
            self.reconnect()
            batch = self._find_new_batch_page(before_urls, order_nos, timeout_s=8)
        if batch is None:
            raise RuntimeError("点「批量在线下单」后没等到批量页（检查是否已选中订单）")
        self.log("[①] 点按钮开页成功：共 %s 个订单" % (self._batch_head(batch) or "?"))
        return batch

    def open_batch(self, order_nos: list[str], reuse_page: str | None = None):
        # P2：显式复用已存在的批量页（--reuse-page 给了就不新开页）
        if reuse_page:
            batch = self.find_reuse_page(reuse_page)
            self.log("[①] 复用已存在的批量页：%s" % (batch.url or "")[:110])
            self.log("[①] 批量页：共 %s 个订单" % (self._batch_head(batch) or "?"))
            self.shot(batch, "01_batch_page")
            return batch

        page = self._orders_page_ready()
        self._search_orders(page, "待发货", order_nos)

        # 读列表：**给页面留出就绪时间**（0 行时多读几次）。2026-09-15 实测：BEL 那次 64 单报"0 行"，
        # 旧代码一读就判死 → 分不清"页面没就绪"还是"真的没有"。现在 0 行先重试 3 次（共 ~9s）。
        found = self._js(page, _JS_LIST_ROWS) or []
        for _ in range(3):
            if found:
                break
            time.sleep(3)
            found = self._js(page, _JS_LIST_ROWS) or []
        got = {r["ord"] for r in found}
        missing = [o for o in order_nos if o not in got]
        if missing:
            # ★ 先看「是不是每页太小」——行数正好一页而本批更多 → 后 N 单压根没渲染（2026-09-17 定）
            _default = int(self.cfg.get("batch", {}).get("page_default", 20) or 20)
            _st = str(getattr(self, "_page_size_status", "") or "")
            if page_size_suspect(len(found), len(order_nos), _st, _default):
                # ★ 2026-09-28 加：`no-sc`（「每页」读不到）+ 行数不整，多半是**查询/渲染竞态**
                #   （15:33 BHY 172 单读到 72 行、被判成"页大小不足"，同一份表原样重跑就填成功了）。
                #   先重查一次再判，只为消掉竞态；**不会**因此少买——重查后仍必须「全部单都在」才继续。
                self.log("[缺单] 列表读到 %d/%d 行且「每页」读不到（%s）→ 重查一次再判（竞态）"
                         % (len(found), len(order_nos), _st))
                self._search_orders(page, "待发货", order_nos)
                found = self._js(page, _JS_LIST_ROWS) or []
                got = {r["ord"] for r in found}
                missing = [o for o in order_nos if o not in got]
                _st = str(getattr(self, "_page_size_status", "") or "")
                self.log("[缺单] 重查后：列表读到 %d/%d 行（「每页」%s）"
                         % (len(found), len(order_nos), _st))
            if missing and page_size_suspect(len(found), len(order_nos), _st, _default):
                raise RuntimeError(
                    "缺单：列表只读到 %d 行（本批 %d 单），「每页」状态=%s。两种可能——"
                    "①「每页」没调到/后 %d 单落在页外没渲染；②查询结果本身不完整（渲染/筛选竞态）。"
                    "已自动重查一次仍如此 → 原样重跑一次；仍不行就把本批拆小（≤%d 单）"
                    "（SKILL temu-buylabel / TB-039）。缺单：%s"
                    % (len(found), len(order_nos), _st, len(order_nos) - len(found), _default,
                       ", ".join(missing[:8])))
            urls = []
            try:
                # ★ 只把**我们读列表这一页**的 URL 当证据（2026-09-17 TB-041）：
                #   窗口里常有别人的残留登录 tab（实测 settle/seller-login），
                #   拿它当证据会把「这些单不在待发货」误诊成「登录态失效」。
                urls = [str(getattr(page, "url", "") or "")]
            except Exception:  # noqa: BLE001
                pass
            diag = diagnose_missing_orders(urls, len(found), len(order_nos),
                                           str(self.cfg.get("_store") or ""), query_applied=True)
            raise RuntimeError(
                "这些单在「待发货」里没查到（本批 %d 单，列表读到 %d 行）｜路线=%s：%s；缺单：%s"
                % (len(order_nos), len(found), diag["route"], diag["hint"].lstrip("；"),
                   ", ".join(missing[:8])))
        self.log("[①] 待发货查到 %d/%d 单" % (len(found), len(order_nos)))

        # ★ 首选：**点「批量在线下单」让平台自己开页**（2026-09-14 实测结论）
        #   自拼 URL 用 `Target.createTarget` 开出来的页，URL 里明明带 20 个单号，
        #   前端却渲染成「共 0 个订单」（等渲染 18s、补 refer_page_name 都无效）→ 填不了。
        #   平台自己开的页才认 → 用户口径「只要真实下单」，故这条是主路径，自拼 URL 降为兜底。
        batch, why = None, ""
        try:
            b1 = self._open_batch_by_click(page, order_nos)
            # **2026-09-19 修**：平台页刚开出时还没渲染完，`_batch_head` 会读到空 → 被误判成
            # 「共 0 个订单」→ 又回落自拼 URL 的页（而那张页提交平台不认）⇒ 白跑一轮。
            # 这里**轮询等渲染**（与下面自拼分支同一口径），别一读就判死。
            head1 = ""
            for _ in range(12):
                head1 = self._batch_head(b1) or ""
                if head1 and head1 != "0":
                    break
                time.sleep(1.5)
            if head1 and head1 != "0":
                batch = b1
            else:
                why = "平台开的页也显示「共 0 个订单」（等了 %ds）" % int(12 * 1.5)
        except Exception as e:  # noqa: BLE001
            why = "点「批量在线下单」开页失败: %s" % str(e)[:140]

        # 兜底：自拼 URL 后台开页（不抢前台）
        if batch is None:
            self.log("[①] ⚠ %s → 回落到「自拼 URL 后台开页」" % why)
            try:
                cand = self.new_tab_background(batch_url(self.cfg, order_nos))
                head = ""
                for _ in range(12):
                    head = self._batch_head(cand)
                    if head and head != "0":
                        break
                    time.sleep(1.5)
                if head and head != "0":
                    batch = cand
                    self.log("[①] 自拼 URL 开页成功：共 %s 个订单" % head)
            except Exception as e:  # noqa: BLE001
                why = "自拼 URL 开页失败: %s" % str(e)[:140]

        if batch is None:
            raise RuntimeError("拿不到「有订单」的批量页：%s" % why)

        self.wait("after_goto", 0.5)
        self.shot(batch, "01_batch_page")
        return batch

    # ---------- ②③④ 填单 ----------
    def fill(self, page, plan: list[dict], weight_per_unit: float | None = None,
             stop_after: str = ""):
        cfg = self.cfg
        dim = cfg["dimensions_in"]
        # ★ 2026-10-09：`weight_per_unit` 为**本次调用**的基准单件重（来自入口 --weight-per-unit）；
        #   缺省仍走口径 `weight_per_unit_lb`（1.1），一次调用不污染配置。
        base_w = float(weight_per_unit or cfg.get("weight_per_unit_lb", 1.1))
        if weight_per_unit:
            # plan 是按口径算好的；覆盖基准重时，多件单要按新基准 × 数量 重算，别只改单件单。
            dec2 = int(cfg.get("weight_decimals", 2))
            for it in plan:
                it["weight"] = base_w * float(it.get("qty") or 1)
                it["weight_txt"] = fmt_num(it["weight"], dec2)
        base_w_txt = fmt_num(base_w, int(cfg.get("weight_decimals", 2)))
        wh_default = cfg.get("warehouse_default", "加州三仓")

        # ② 顶部四维 + 顶部发货仓库
        self._js(page, _JS_SET_TOP, [base_w_txt, dim["length"], dim["width"], dim["height"]])
        time.sleep(1.5)
        if self._js(page, _JS_OPEN_TOP_WAREHOUSE) != "ok":
            raise RuntimeError("打不开发货仓库下拉")
        # ★ 2026-10-09 修：下拉**异步渲染**，固定等 2s 不够——94 单规模下实测报
        #   `发货仓库下拉里没有选项 加州3仓（no-option:加州3仓）`，而人工点开时选项明明在
        #   （BEL 真机 2026-10-09：12:45 那次 no-option，13:07 复测同页同 JS 返回 ok）。
        #   改为**轮询重试**：`_JS_PICK_OPTION_FIRSTLINE` 匹配不到时返回 no-option（无副作用），
        #   匹配到即点击 ⇒ 重试是安全的。最多 ~16s。
        r = "no-option:%s" % wh_default
        for _try in range(8):
            r = self._js(page, _JS_PICK_OPTION_FIRSTLINE, wh_default)
            if r == "ok":
                break
            if _try == 0:
                self.log("[②] 仓库下拉还没渲染出「%s」→ 轮询等待（最多 ~16s）" % wh_default)
            time.sleep(2)
        if r != "ok":
            raise RuntimeError("发货仓库下拉里没有选项 %s（%s）" % (wh_default, r))
        time.sleep(1.5)
        self.log("[②] 顶部：重量=%s 体积=%s/%s/%s 仓库=%s" % (base_w_txt, dim["length"], dim["width"], dim["height"], wh_default))

        # ③ 勾选子订单 → 批量填充
        r = self._js(page, _JS_CHECK_SUBORDER)
        if r != "ok" and not str(r).startswith("ok@"):
            raise RuntimeError("勾选子订单失败：%s" % r)
        time.sleep(1.5)
        self._click_button(page, "批量填充")
        time.sleep(3)
        # ★ 2026-10-16 修：点「批量填充」后会弹**保护确认**
        #   「已选 N 个包裹，其中 M 个包裹已填写信息，是否剔除并填充剩余包裹?」
        #   （按钮：忽略并全部填充 / 取消）——已填过信息的包裹（上一轮铺过值、或刚被
        #   ③b 逐单修正过）再批量填充时先问一句。
        #   旧代码只 sleep 3s **从不处理它** ⇒ 弹窗挂着、批量填充实际没生效，
        #   而 `_JS_ROWS_QTY` 看不出这个弹窗 ⇒ weight_fix 报「不匹配 0」**假通过**。
        #   处置：登记表 PU-004（act=click → 忽略并全部填充）自动点；这里只做**兜底复检**，
        #   防止 `_click_button` 自带的 dismiss 因认人判据没命中而漏掉（漏掉就硬失败，不静默）。
        if self._js(page, _JS_MODAL_PRESENT, "是否剔除并填充剩余包裹") is True:
            self.log("[③] ⚠ 批量填充确认框仍在（登记表 PU-004 未命中）→ 兜底点[忽略并全部填充]")
            self._click_button(page, "忽略并全部填充")
            time.sleep(3)
            if self._js(page, _JS_MODAL_PRESENT, "是否剔除并填充剩余包裹") is True:
                raise RuntimeError("批量填充确认框「是否剔除并填充剩余包裹?」点不掉，批量填充未生效")
        self.shot(page, "02_after_batch_fill")
        self.log("[③] 已勾选子订单并批量填充")

        # ③b 逐单修正：重量（多件单）与仓库（映射不同）
        for item in plan:
            if item["weight_txt"] != base_w_txt:
                rr = self._js(page, _JS_SET_ROW_WEIGHT, [item["order_no"], item["weight_txt"]])
                self.log("    重量修正 %s → %s (%s)" % (item["order_no"], item["weight_txt"], rr))
                time.sleep(1)
            if item["warehouse"] != wh_default:
                rr = self._js(page, _JS_OPEN_ROW_WAREHOUSE, item["order_no"])
                if rr == "ok":
                    time.sleep(2)
                    rr2 = self._js(page, _JS_PICK_OPTION_FIRSTLINE, item["warehouse"])
                    self.log("    仓库修正 %s → %s (%s)" % (item["order_no"], item["warehouse"], rr2))
                    time.sleep(1.5)

        # ③c ★ 2026-10-09 用户口径：「填充完重量之后，**先找到出现重量不匹配的订单，修改完之后，
        #    再去填充物流**」。上面 ③b 只按 plan 修（plan 的 qty 来自派送表；**没有派送表、
        #    只给 --orders 时 qty 恒为 1**，多件单修不到）——所以这里必须**按页面现场件数**
        #    再核对一遍：重量必须 = 基准 × 行内 ×N 之和。这是「改重量 → 再填物流」的顺序保证。
        wf = self.weight_fix(page, plan=plan, weight_per_unit=base_w)
        stop_dims = str(stop_after or "") == "dims"
        if stop_dims:
            self.shot(page, "03c_stop_after_dims")
            rows_now = self._js(page, _JS_ROWS) or []
            self.log("[③c] --stop-after dims：已停在此处（**未做**物流服务一键填充、**未**点确认下单）")
            return {"stopped_after": "dims", "rows": rows_now, "base_weight": base_w_txt,
                    "dimensions": dim, "warehouse": wh_default, "count": len(rows_now),
                    "weight_fix": wf}

        # ④ 物流服务：一键填充 →（覆盖填充）→ 策略 → 物流商范围 → 确认填充
        #    （`--stop-after dims` 已在上面 ③c 处提前 return，走不到这里）
        r = self._js(page, _JS_CLICK_LINK_NEAR, ["一键填充", "物流服务"])
        if r != "ok":
            raise RuntimeError("点不到「物流服务 一键填充」")
        time.sleep(3)
        if self._js(page, _JS_MODAL_PRESENT, "批量填充物流服务") is not True:
            self._click_button(page, "覆盖填充", optional=True)
            time.sleep(3.5)
        if self._js(page, _JS_MODAL_PRESENT, "批量填充物流服务") is not True:
            raise RuntimeError("「批量填充物流服务」弹窗没出现")
        self.shot(page, "03_service_modal")

        strategy = cfg.get("logistics", {}).get("strategy", "配送成本优先")
        self._js(page, _JS_CLICK_RADIO, strategy)
        time.sleep(1.5)
        maxq = str(cfg.get("logistics", {}).get("max_quote", "") or "")
        if maxq:
            self._js(page, r"""(v) => {
              const el = Array.from(document.querySelectorAll('input')).filter(e => e.offsetParent !== null && (e.placeholder||'').indexOf('最高报价') >= 0)[0];
              if (!el) return 'no-input';
              const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
              set.call(el, v); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
              return 'ok';
            }""", maxq)

        carriers = cfg.get("logistics", {}).get("carriers", []) or []
        if carriers:
            opts = cfg.get("logistics", {}).get("carrier_options", [])
            target = {name: (name in carriers) for name in opts}
            if self._js(page, _JS_OPEN_CARRIER_SCOPE) == "ok":
                time.sleep(2)
                self.log("    物流商勾选: " + json.dumps(self._js(page, _JS_SET_CARRIERS, [target]), ensure_ascii=False))
                time.sleep(1.5)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(1)

        self._click_button(page, "确认填充")
        self.wait("after_confirm_fill")
        rows = self._js(page, _JS_ROWS) or []
        self.shot(page, "04_filled")

        # ★ 2026-10-09 用户口径：占位文案（如「无物流渠道」）**也算没有服务**，
        #   要走 Kansas 兜底 + 重选下拉框第一个可用物流（见 `_fix_no_carrier`）。
        no_svc = [r["ord"] for r in rows if svc_missing(r.get("service"))]
        if no_svc:
            self.log("[④] ⚠ 这些行物流服务为空/无渠道，尝试 Kansas 兜底：%s" % ", ".join(no_svc))
            self._fix_no_carrier(page, plan, no_svc)
            rows = self._js(page, _JS_ROWS) or []
            no_svc = [r["ord"] for r in rows if svc_missing(r.get("service"))]
            if no_svc:
                self.shot(page, "04b_no_service")
                raise RuntimeError("这些单仍没有可用物流服务，需人工：%s" % ", ".join(no_svc))

        freight = self._js(page, r"""() => {
          const m = (document.body.innerText||'').match(/预估总运费：\|?\s*(\$[\d.]+)/);
          return m ? m[1] : '';
        }""")
        self.log("[④] 物流服务已填；预估总运费 = %s" % (freight or "?"))
        for r in rows:
            self.log("    %s | 重量=%s | 仓库=%s | 物流服务=%s" % (r["ord"], r["weight"], r["warehouse"], r["service"]))
        return {"rows": rows, "freight": freight}

    def weight_fix(self, page, plan: list | None = None, weight_per_unit: float | None = None,
                   stop_on_unfixed: bool = False):
        """★ 2026-10-09 新增（用户口径）：**填完重量后、填物流服务前**，把「重量不匹配」的单改对。

        用户原话：「在填充完重量之后，先找到出现重量不匹配的订单，修改完之后，再去填充物流」。

        判据（口径在 `data/temu_buylabel.json → weight_mismatch`）：
            重量必须 = 基准单件重 × **件数**。
            **件数一律取派送表的「数量」列（经 `plan` 传入）**——用户 2026-10-09：
            「其实在我给你的文件已经把10件套 4件套 6件套 这些已经拆开了，你直接读表格就知道哪些订单是几件套了」。
            ⚠ **不要用页面的 `×N`**：它只是**包裹数**（BEL 94 单实测 25 单显示 ×1 而表格是 2/3）；
            先前按页面 ×N 判会把本该 2.20 的单改成 1.10 —— 那正是平台报「差异过大」的根因。
            页面 ×N 仅在**表里查不到该单**时兜底。
        修法：对该单「重量」输入框用**原生 value setter + input/change**（React 受控组件直接改
            `el.value` 界面不认），逐框写入正确值，写完**回读复核**。

        返回 `{checked, mismatched, fixed, unfixed, rows}`。
        `stop_on_unfixed=True` 时，仍不匹配则抛错；缺省 False＝**用户 2026-10-09 明确要求不加闸门**，
        只记账并继续（但日志逐条留痕，便于事后核对，不静默吞掉）。
        """
        cfg = self.cfg
        wm = cfg.get("weight_mismatch") or {}
        base = float(weight_per_unit or cfg.get("weight_per_unit_lb", 1.1))
        if wm.get("enabled") is False:
            self.log("[重量核对] weight_mismatch.enabled=false → 跳过")
            return {"checked": 0, "mismatched": 0, "fixed": 0, "unfixed": [], "rows": [],
                    "skipped": True}
        dec = int(cfg.get("weight_decimals", 2))

        def _scan():
            """读回逐单件数/重量。

            ⚠ 必须兼容两种返回：`_JS_ROWS_QTY` 内层用了 `JSON.stringify`，而 CDP 的
            `Runtime.evaluate(returnByValue)` 会把结果**原样**返回 ⇒ 实际拿到的是 **JSON 字符串**，
            不是 list。若直接 `for r in raw` 会按字符遍历、`r.get` 立刻 AttributeError
            （2026-10-09 离线用例①抓到）。这里统一解析成 list[dict]，解析不了就当空。
            """
            raw = self._js(page, _JS_ROWS_QTY)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:  # noqa: BLE001
                    return []
            if not isinstance(raw, list):
                return []
            return [r for r in raw if isinstance(r, dict)]

        rows = _scan()
        if not rows:
            self.shot(page, "03c_weight_fix_norows")
            self.log("[重量核对] ⚠ 一行都没读到（批量页未就绪？）——跳过核对，继续后续步骤")
            return {"checked": 0, "mismatched": 0, "fixed": 0, "unfixed": [], "rows": [],
                    "read_empty": True}

        mismatched, fixed, unfixed = [], [], []
        # ★ 2026-10-09 校正（用户口径：「文件里已经把 10件套/4件套/6件套 拆开了，读表格就知道几件」）：
        #   **件数一律以派送表的「数量」列为准（经 plan 传进来），绝不用页面的「×N」**。
        #   实测证据（BEL 94 单，2026-10-09）：页面「×N」只是**包裹数**，25 单显示 ×1 而表格数量是 2 或 3
        #   （例 PO-211-07859559854712400 表=2 / 页面=×1）。先前按页面 ×N 判 ⇒ 把本该 2.20 的单
        #   改成 1.10，正是平台报「差异过大」的根因。页面 ×N 仅在**表里没有该单**时兜底。
        plan_qty = {}
        for it in (plan or []):
            try:
                plan_qty[str(it.get("order_no"))] = int(it.get("qty") or 1)
            except Exception:  # noqa: BLE001
                pass
        for r in rows:
            ordno = r.get("ord")
            if ordno in plan_qty:
                qty = plan_qty[ordno]                 # ← 权威：表格数量
                src = "表"
            else:
                qty = int(r.get("qty") or 1)          # ← 兜底：页面 ×N
                src = "页"
            want = fmt_num(base * qty, dec)
            # ★ 2026-10-09 新增（用户口径）：「**件数本身就是一件，但重量还是不符合，那就改成固定值 1.4**」。
            #   场景：1 件单其实是大件/多件套（例 `BH-102503-5SetA-L` 是 5件套，表数量 1、计费重量 1.80）
            #   ⇒ 按 基准×1=1.10 填会被平台判「差异过大（2.06 磅）」；此时**件数倍数公式不适用**。
            #   判据：件数==1 且 行内已报红字（`mismatchHint`）；值取口径 `weight_single_override.value`。
            #   若平台改成 1.4 后**仍报红字**：按用户同日口径**不拦、只记账留痕**。
            _sov = cfg.get("weight_single_override") or {}
            if (_sov.get("enabled", True) is not False and int(qty) == int(_sov.get("when_qty", 1))
                    and bool(r.get("mismatchHint"))):
                want = fmt_num(float(_sov.get("value", 1.4)), dec)
                src = src + "+1件改值"
            # ★★ 2026-10-16 用户口径（**最高优先级**）：
            #   「遇到重量不匹配的，会在长宽高出现红色字样提醒，**按照提醒的数字磅数填上去**」。
            #   ⇒ 平台红字「填写包裹重量与包裹内SKU总重量（N 磅）差异过大」里的 **N 就是答案**，
            #     直接以 N 为准，**覆盖** 基准×件数 与 1件改值 两个公式。
            #   为什么必须这样：N 是平台按**包裹内 SKU 的实际重量**算出来的（含材质/尺码差异），
            #   我们的 基准×件数 只是近似。实测 PO-211-11022155848310063（qty=2）：
            #   基准×2 = 2.20，而平台要 3.48 ⇒ 按公式填**永远**对不上，且 `weight_fix` 用
            #   `abs(现值-基准×件数)` 判 ⇒ 自己判自己「已匹配」，红字却一直在（2026-10-16 假通过事故）。
            #   仅当**件数公式算出来的值与红字 N 不一致**时才覆盖（一致就不动，少写一次）。
            _n = r.get("hintWeight")
            if r.get("mismatchHint") and _n not in (None, ""):
                try:
                    _nw = fmt_num(float(_n), dec)
                    if _nw != want:
                        if wm.get("hint_wins", True) is not False:
                            src = src + "+红字N"
                            want = _nw
                except (TypeError, ValueError):
                    pass
            ws = [w for w in (r.get("weights") or []) if str(w).strip() != ""]
            # 重量读不出来（空/非数字）就当不匹配处理不了，记账但不猜值
            try:
                bad = [w for w in ws if abs(float(w) - float(want)) > 0.001]
            except (TypeError, ValueError):
                unfixed.append({"ord": ordno, "qty": qty, "want": want,
                                "err": "重量值非数字: %s" % ws})
                continue
            # ★★ 2026-10-16 关键修正：**平台红字在，就必须改**，哪怕公式算出来"已匹配"。
            #   事故复盘（PO-211-11022155848310063，qty=2）：填 2.20＝基准×2 ⇒ `bad` 为空 ⇒
            #   旧代码 `continue` 跳过，日志报「不匹配 0 / 已改对 0」，而页面红字一直在——
            #   **自己判自己通过**。根因：判据只比「现值 vs 基准×件数」，而基准×件数本身可能
            #   就不是平台要的数（平台要 3.48）。
            #   现口径：`mismatchHint` 为真 ⇒ 一定进修复分支（want 已按红字 N 覆盖）；
            #   记 `forced_by_hint` 便于事后区分"公式判不匹配"与"红字逼着改"。
            forced = bool(r.get("mismatchHint")) and not bad
            if not bad and not forced:
                continue
            mismatched.append({"ord": ordno, "qty": qty, "was": ws, "want": want,
                               "src": src, "forced_by_hint": forced})
            res = self._js(page, _JS_SET_ROW_WEIGHT, [ordno, want])
            time.sleep(0.8)
            self.log("[重量核对] %s %s=%d → %s (原 %s) (%s)%s"
                     % (ordno, src, qty, want, "/".join(ws) or "-", res,
                        " [红字逼改]" if forced else ""))
            if str(res).startswith("ok:"):
                fixed.append({"ord": ordno, "qty": qty, "to": want})
            else:
                # 改不动：按用户口径不拦，但要留痕，别静默
                unfixed.append({"ord": ordno, "qty": qty, "want": want, "err": str(res)})

        # 回读复核（不假设写入一定生效）
        if mismatched:
            time.sleep(1.2)
            after = {x["ord"]: x for x in (_scan() or [])}
            still = []
            for m in mismatched:
                a = after.get(m["ord"])
                if not a:
                    still.append(m["ord"])
                    continue
                cur = [w for w in (a.get("weights") or []) if str(w).strip() != ""]
                try:
                    bad_after = any(abs(float(w) - float(m["want"])) > 0.001 for w in cur)
                except (TypeError, ValueError):
                    bad_after = True          # 读不出数字 → 保守当作未改对
                if bad_after:
                    still.append(m["ord"])
            if still:
                for m in mismatched:
                    if m["ord"] in still and not any(u["ord"] == m["ord"] for u in unfixed):
                        unfixed.append({"ord": m["ord"], "qty": m["qty"],
                                        "want": m["want"], "err": "回读仍不匹配"})
        if unfixed:
            self.shot(page, "03c_weight_fix_unfixed")
            self.log("[重量核对] ⚠ %d 单仍未改对（按用户口径不拦，继续）：%s"
                     % (len(unfixed), ", ".join(u["ord"] for u in unfixed)))
            if stop_on_unfixed:
                raise RuntimeError("重量核对未通过（--stop-on-unfixed）：%s"
                                   % ", ".join(u["ord"] for u in unfixed))
        else:
            self.shot(page, "03c_weight_fix_ok")

        self.log("[重量核对] 共 %d 单：不匹配 %d / 已改对 %d / 未改对 %d（基准 %s lb）"
                 % (len(rows), len(mismatched), len(fixed), len(unfixed), fmt_num(base, dec)))
        return {"checked": len(rows), "mismatched": len(mismatched),
                "fixed": len(fixed), "unfixed": unfixed,
                "detail": mismatched, "base": fmt_num(base, dec)}

    def _fix_no_carrier(self, page, plan, orders: list[str]):
        """异常分支：物流服务全置灰 → 该单改 kansas 仓 → 试可用服务。"""
        fb = self.cfg.get("no_carrier_fallback", {})
        wh = fb.get("warehouse", "kansas")
        svcs = fb.get("services", [])
        for ordno in orders:
            r = self._js(page, _JS_OPEN_ROW_WAREHOUSE, ordno)
            if r != "ok":
                self.log("    %s 打开仓库下拉失败(%s)" % (ordno, r)); continue
            time.sleep(2)
            r2 = self._js(page, _JS_PICK_OPTION_FIRSTLINE, wh)
            self.log("    %s 改仓库 → %s (%s)" % (ordno, wh, r2))
            time.sleep(2)
            opened = self._js(page, _JS_OPEN_ROW_SERVICE, ordno)
            if opened != "ok":
                self.log("    %s 打开物流服务失败(%s)" % (ordno, opened)); continue
            time.sleep(2.5)
            opts = self._js(page, _JS_SERVICE_OPTIONS) or []
            avail = [o["text"] for o in opts if not o["disabled"]]
            self.log("    %s 可用服务: %s" % (ordno, json.dumps(avail, ensure_ascii=False)))
            picked = ""
            for pref in svcs:
                if any(a.startswith(pref) for a in avail):
                    picked = pref
                    break
            if picked:
                self.log("    %s 选: %s" % (ordno, self._js(page, _JS_PICK_SERVICE_PREFIX, picked)))
            elif avail:
                self.log("    %s 按预览取第一个可用: %s" % (ordno, self._js(page, _JS_PICK_FIRST_AVAILABLE_SERVICE)))
            else:
                self.log("    %s ⚠ 改成 %s 后仍无可用服务" % (ordno, wh))
            time.sleep(2)

    # ---------- ⑤ 下单 ----------
    #: 下单链上「点一下＝确认并提交」的按钮文本（实测值，改版只改这里）。
    #: 2026-09-15 BHY 31 单实测：点「确认下单」后弹的是**异常单提醒**
    #: （「共 N 个订单…以下订单/包裹存在异常，是否确认无误并继续下单？」），
    #: 按钮文本是「确认无误，继续下单」——不是「确认」→ 旧判据不匹配、静默没点、不报错。
    _PLACE_SUBMIT_BTNS = ("确认无误，继续下单",)
    #: 下单链上的「通知类」弹窗（无按钮、只有 ✕）：关掉即可，不影响下单。
    #: **2026-09-21 起不再写死**（用户：「经验要能固化」）——改从**弹窗登记表**取：
    #: 登记表里标了 `"on_place": true` 的条目就是这条链上的通知弹窗（现为 PU-003「物流细则」）。
    #: 新遇到一个 → 它会先被自动登记进 unknown[]，确认后加一行数据即生效，不用改这里。

    def _place_notice_entries(self) -> list[dict]:
        """下单链上的通知类弹窗条目（读登记表，不写死）。"""
        reg = load_popup_registry(cfg=self.cfg)
        return [e for e in (reg.get("popups") or []) if e.get("on_place")]

    def _popup_close_point(self, page, entry: dict):
        """该弹窗的关闭点：优先 testid 关闭键；没有则**仅当登记表写了 close=approx**用近似点。

        返回 [x, y] 或 None（None＝找不到关闭点，调用方自己决定停下还是放过）。
        """
        try:
            raw = self._js(page, _JS_POPUP_PROBE, tries=1)
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001  探测失败不该连带把"已知通知关不掉"
            d = {}
        if not isinstance(d, dict):
            d = {}
        if d.get("hasClose") and d.get("closeAt"):
            return d.get("closeAt")
        if str((entry or {}).get("close") or "") == "approx":
            kw = str((((entry or {}).get("when") or {}).get("title_contains") or [""])[0])
            if kw:                                  # 兜底：老口径（直接按文本找内容区右上角）
                try:
                    fb = self._js(page, _JS_INNER_APPROX_BY_TEXT, kw)
                except Exception:  # noqa: BLE001
                    fb = None
                if isinstance(fb, list) and len(fb) == 2:
                    return fb
            if d.get("approxAt"):
                return d.get("approxAt")
        return None

    def _place_modal_step(self, page) -> str:
        """处理下单链上的**一个**弹窗：有则处理并返回标签，没有返回空串（不猜、不空转）。"""
        for btn in self._PLACE_SUBMIT_BTNS:
            if self._js(page, _JS_MODAL_PRESENT, btn) is True:
                r = self._click_button(page, btn)
                return "submit:%s(%s)" % (btn, r)
        if self._js(page, _JS_MODAL_PRESENT, "确认下单？") is True:
            r = self._click_button(page, "确认")
            return "submit:确认(%s)" % r
        for e in self._place_notice_entries():
            kw = str(((e.get("when") or {}).get("title_contains") or [""])[0])
            if kw and self._js(page, _JS_MODAL_PRESENT, kw) is True:
                pos = self._popup_close_point(page, e)
                if pos:
                    self.cdp_click(page, pos[0], pos[1])
                    return "close:" + kw
                return "notice:" + kw + "(找不到关闭钮)"
        # 有弹窗但一个都没认出来 → **只留痕**（截图 + 原文），**不返回标签**：
        # 返回标签会让外层继续走"点底层「确认下单」"，那是遮罩下的重复点击＝重复提交风险。
        # 真正的失败判定交给 `place()` 末尾的显式终态闸门。
        txt = self._js(page, _JS_MODAL_TEXT)
        if txt:
            self.shot(page, "05_unknown_modal")
            self.log("[⑤] ⚠ 未识别的下单弹窗（原文，未点任何键）：%s" % str(txt)[:200])
        return ""

    def place(self, page, plan: list[dict]):
        # ⓪ 页面可能**已经停在**某个确认弹窗上（上一轮点了「确认下单」但没处理完的弹窗，
        #    2026-09-15 实测就是这种现场）——先把链上的弹窗处理完，**不重复点**「确认下单」，
        #    否则会在弹窗遮罩下再点一次底层按钮（JS click 不理会遮罩）＝可能重复下单。
        done = []
        for _ in range(3):
            tag = self._place_modal_step(page)
            if not tag:
                break
            done.append(tag)
            self.log("[⑤] 下单弹窗：%s" % tag)
            time.sleep(5)
        if not [t for t in done if t.startswith("submit:")]:
            self._click_button(page, "确认下单")
            time.sleep(4.5)
            # 弹窗渲染会慢：**不能一次探测不到就收工** —— 2026-09-19 实测就是 25s 内没探到，
            # 静默返回"成功"，实际一单没下（BHY 63 单白跑一轮）。这里给足轮次，
            # 且"空探测"要连续 2 次才收工。
            empty = 0
            for _ in range(10):
                tag = self._place_modal_step(page)
                if not tag:
                    empty += 1
                    if empty >= 2:
                        break
                    time.sleep(3)
                    continue
                empty = 0
                done.append(tag)
                self.log("[⑤] 下单弹窗：%s" % tag)
                time.sleep(6)
        # 上门取件预约
        has_pickup = self._js(page, _JS_MODAL_PRESENT, "上门取件") is True
        if has_pickup:
            self.shot(page, "05_pickup")
            # ★ 2026-10-09 修（BEL 94 单实测：**只填了前 2 行就 break** → 剩下 UniUni/SpeedX/YANWEN
            #   三行的「预约取件时间不能为空」→ 平台校验不过、点「已确认，继续下单」不提交，
            #   表现成「点了没下单」——批量页不关、订单记录里没有 TK 任务、94 单仍全在待发货）。
            #   真因：`_JS_PICK_EARLIEST_DATE` 点选完日期后**选择器面板可能仍开着**，而
            #   `_JS_FIND_PICKUP_DATE_FIELD` 取的是 `[class*=MDL_inner]` 反转后的**最后一个**——
            #   面板本身就是个 MDL_inner（里面没有 tr）⇒ 当轮返回 null ⇒ 旧逻辑直接 break。
            #   修法三条：① 找不到时**先按 Esc 关面板再重试**（最多 3 轮），不一次判死；
            #            ② 每轮之间给面板关闭留时间；③ 收尾**按「还剩几个空日期框」核对**，
            #               没填完就如实抛错（对应源码既有口径：绝不静默当成功）。
            _JS_PICKUP_REMAIN = r"""() => {
              const rectVis = e => { const r = e.getBoundingClientRect();
                return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
                    && getComputedStyle(e).visibility !== 'hidden'; };
              const mods = [...document.querySelectorAll('[class*=MDL_inner]')].filter(
                  e => rectVis(e) && (e.innerText || '').indexOf('预约上门时间') >= 0);
              let empty = 0;
              for (const modal of mods) {
                for (const tr of [...modal.querySelectorAll('tr')]) {
                  for (const e of [...tr.querySelectorAll('input[type=text]')].filter(rectVis)) {
                    if (e.disabled) continue;
                    if ((e.placeholder || '').indexOf('时间') >= 0) continue;
                    if (!(e.value || '').trim()) empty++;
                  }
                }
              }
              return empty;
            }"""
            filled_n = 0
            for i in range(1, len(plan) + 3):
                pos = self._js(page, _JS_FIND_PICKUP_DATE_FIELD)
                if not pos:
                    # 面板可能还开着挡住查找 → 关掉再试（≤3 轮），别一次判死
                    for _rt in range(3):
                        try:
                            page.keyboard.press("Escape")
                        except Exception:  # noqa: BLE001
                            pass
                        time.sleep(1.2)
                        pos = self._js(page, _JS_FIND_PICKUP_DATE_FIELD)
                        if pos:
                            retried = True
                            self.log("[⑤] 取件日期：面板没收起 → Esc 后重试成功（第 %d 轮）" % (_rt + 1))
                            break
                    if not pos:
                        break
                self.cdp_click(page, pos["x"], pos["y"])   # 原生 CDP 派发（不依赖前台焦点）
                time.sleep(2.5)
                got = self._js(page, _JS_PICK_EARLIEST_DATE)
                if got and got != "no-option":
                    filled_n += 1
                self.log("[⑤] 取件日期 第%d个 → %s" % (i, got))
                time.sleep(2)
            self.shot(page, "06_pickup_dates")
            # 收尾核对：还有空日期框就不许往下点（否则必然"点了没下单"）
            remain = self._js(page, _JS_PICKUP_REMAIN)
            if remain:
                self.shot(page, "06b_pickup_unfilled")
                raise RuntimeError(
                    "上门取件日期**没填完**：还有 %s 个空白日期框（本次填了 %d 个）。"
                    "继续点「已确认，继续下单」会被平台校验拦下、表现成『点了没下单』——"
                    "已停在此处，截图 06b_pickup_unfilled.png" % (remain, filled_n))
            self.log("[⑤] 取件日期已填齐（本次填了 %d 个）" % filled_n)
            r = self._js(page, _JS_CLICK_BUTTON, "已确认，继续下单")
            if r != "ok":
                raise RuntimeError("点不到「已确认，继续下单」(%s)" % r)
            self.log("[⑤] 已点「已确认，继续下单」")
            self.wait("after_place")
        else:
            self.log("[⑤] 无上门取件弹窗（按物流商规则）")

        # 终态闸门（2026-09-19 加）：**「确认下单？」框还杵在屏幕上 = 没提交成功**。
        # 旧行为是静默返回 {tab_closed:false} 让外层当成功；而 TB-026（is_closed() 恒 False）
        # 使 tab_closed **不可作判据** ⇒ `all` exit 0 却一单没下（BHY 63 单实测）。
        # 这里改成**显式失败**，把"静默 no-op"变成"响亮的错"。
        if self._js(page, _JS_MODAL_PRESENT, "确认下单？") is True:
            self.shot(page, "05_still_confirm_dialog")
            raise RuntimeError(
                "点了「确认下单」但「共 N 个订单，确认下单？」的框仍在（未提交成功）"
                "→ 不得当成功（须按 D6 读回判）。页面 %s" % getattr(page, "url", ""))

        closed = True
        try:
            closed = page.is_closed()
        except Exception:
            closed = True
        if not closed:
            try:
                page.wait_for_timeout(5000)
                closed = page.is_closed()
            except Exception:
                closed = True
        self.log("[⑤] 批量页%s（关闭=下单成功）" % ("已关闭" if closed else "仍开着"))
        return {"tab_closed": closed}

    # ---------- ⑥ 逐单确认发货 ----------
    def confirm_shipping(self, order_nos: list[str], max_rounds: int = 0):
        page = self._orders_page_ready()
        self._search_orders(page, "待发货", order_nos)
        done = []
        limit = max_rounds or (len(order_nos) + 3)
        for i in range(1, limit + 1):
            o = self._js(page, _JS_CLICK_MANUAL_FIRST)
            if o == "none":
                break
            time.sleep(3.5)
            c = self._js(page, _JS_CLICK_BUTTON, "确认")
            self.log("[⑥] %d) %s → 确认 (%s)" % (i, o, c))
            done.append(o)
            time.sleep(4)
        self.shot(page, "07_confirm_shipping")
        return {"confirmed": done}

    def overdue_orders(self, todo_text: str | None = None, do_confirm: bool = True,
                       write_delivery: bool = True) -> dict:
        """待办「即将逾期未发货」处置：待发货页 → 点待办卡取单 → 分流 → 能确认的逐单确认发货。

        （2026-09-30 用户指的那条流程：「点击即将[逾期未发货]，然后点击去手动确认」。）
        两类单必须分开，别混着点：
          · 有运单号 + 行内有「去手动确认」→ 确认发货（复用 `confirm_shipping` 那条已跑通的路径）
          · 没有运单号 → **还没买面单**：本方法按页面行直接生成派送表 xlsx（`delivery_file`），
            交给整链 `pipeline` 去在线下单——人不用再做表
        判据＝纯函数 `split_overdue_rows()` / `overdue_rows_to_delivery()`；卡片文案来自口径 `overdue.todo_text`。
        """
        want = str(todo_text or (self.cfg.get("overdue") or {}).get("todo_text") or "即将逾期未发货")
        page = self._orders_page_ready()
        # ① **先把「每页」拉满，再点待办卡**——顺序不能反：改「每页」会重新取列表，把待办的筛选冲掉。
        #   2026-09-30 实测（12:24 那次）：先点卡（列表筛成 11 条）再调每页 → 列表弹回「共有 256 条」
        #   ＝整个待发货页；不在这里拦住，下游就会把 256 单当"待办单"去确认发货。
        cap = int(self.cfg.get("batch", {}).get("page_size", 500) or 500)
        ps = self.set_list_page_size(page, cap)
        self.log("[待办] 每页先拉满：%s" % ps)
        # ② 点待办卡（**只能是 DOM click**，见 `_JS_OVERDUE_TODO` 注释）→ 前端筛选
        hit = self._js(page, _JS_OVERDUE_TODO, want)
        if hit == "none":
            # ★ 2026-10-09：区分「该待办已清空」与「页面未就绪」——前者是正常收工，不是失败。
            #   判据＝待办任务**区域**是否存在（见 `_JS_OVERDUE_ZONE` 注释）。
            zone = self._js(page, _JS_OVERDUE_ZONE)
            try:
                z = json.loads(zone) if isinstance(zone, str) else (zone or {})
            except Exception:  # noqa: BLE001
                z = {}
            if isinstance(z, dict) and z.get("present"):
                # 区域在、卡不在了 ⇒ 没有逾期单要处理。正常收工（与 `nothing_to_buy` 同口径：
                # 不算失败、不做写动作、自报 [VERDICT] 让上层别开失败卡）。
                self.log("[待办] 待办任务区在，但**没有**「%s」卡 ⇒ 无逾期单，正常收工" % want)
                self.shot(page, "20_overdue_none")
                return {"todo": "", "todo_count": 0, "rows": 0,
                        "split": {"confirm": [], "need_label": [], "other": [],
                                  "counts": {"confirm": 0, "need_label": 0, "other": 0}},
                        "confirm": {"confirmed": []},
                        "noop": True, "route": "overdue_none",
                        "reason": "没有「%s」待办卡（该待办已清空）" % want}
            self.shot(page, "20_overdue_todo_missing")
            raise RuntimeError("找不到待办卡「%s」且**待办任务区也不在**（页面未就绪或平台改版；"
                               "已落图 20_overdue_todo_missing.png）" % want)
        self.log("[待办] 点「%s」→ 列表按该待办筛选" % hit)
        time.sleep(4)
        want_n = parse_todo_count(hit)
        rows = self._js(page, _JS_OVERDUE_ROWS) or []
        # ③ **复核筛选真生效**：列表行数要≈卡上数字；不符就重点一次卡，仍不符 → 落图停下，绝不硬跑。
        if want_n and len(rows) > min(want_n + 2, cap):
            self.log("[待办] ⚠ 筛选复核不符：卡上 %d 单、列表读到 %d 行 → 重点一次卡" % (want_n, len(rows)))
            self._js(page, _JS_OVERDUE_TODO, want)
            time.sleep(4)
            rows = self._js(page, _JS_OVERDUE_ROWS) or []
        if want_n and len(rows) > min(want_n + 2, cap):
            self.shot(page, "20_overdue_filter_mismatch")
            raise RuntimeError("待办筛选没生效：卡上 %d 单、列表读到 %d 单（差太多）——"
                               "不按这份列表确认发货；已落图 20_overdue_filter_mismatch.png"
                               % (want_n, len(rows)))
        split = split_overdue_rows(rows)
        c = split["counts"]
        self.log("[待办] 读到 %d 行（卡上 %d）：可确认 %d / 缺面单 %d / 其它 %d"
                 % (len(rows), want_n, c["confirm"], c["need_label"], c["other"]))
        self.shot(page, "20_overdue_list")
        out = {"todo": hit, "todo_count": want_n, "rows": len(rows), "split": split}
        # ④ 缺面单的那批 → 直接按页面行生成派送表（整链的输入），人不用再做表
        if write_delivery and split["need_label"]:
            bad = set(split["need_label"])
            delivery, skipped = overdue_rows_to_delivery([r for r in rows if (r or {}).get("ord") in bad])
            if skipped:
                out["skipped"] = skipped
                self.log("[待办] ⚠ 有 %d 单页面没给「库存扣减仓库」→ 不进派送表（不猜仓）：%s"
                         % (len(skipped), " ".join(x["ord"] for x in skipped)[:300]))
            if delivery:
                # `evidence_dir` 本身已经是 out/<日期>/buylabel，别再拼一遍日期（2026-09-30 拼重了）
                base_dir = self.evidence_dir or os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..", "out", time.strftime("%Y-%m-%d"), "buylabel"))
                path = os.path.join(base_dir, "overdue_delivery_%s.xlsx" % time.strftime("%Y%m%d-%H%M%S"))
                out["delivery_file"] = write_delivery_xlsx(delivery, path)
                out["delivery_rows"] = len(delivery)
                self.log("[待办] 缺面单 %d 单 → 派送表 %s" % (len(delivery), out["delivery_file"]))
            else:
                self.log("[待办] 缺面单那批一行都没能生成派送表（见上面 ⚠）")
        if do_confirm and split["confirm"]:
            out["confirm"] = self.confirm_shipping(split["confirm"])
        return out

    def open_result_page(self, package_sn: str):
        """打开某包裹的「在线下单结果/详情」页（可直接构造 URL，不必先点记录表）。"""
        url = ("%s?package_sn_list=%%5B%%22%s%%22%%5D&open_type=OnlineShipAsync"
               % (self.cfg["shipping_url"].replace("online-shipping.html", "online-shipping-result.html"),
                  package_sn))
        return self.new_tab_background(url)   # 后台开页（不抢前台）

    def book_pickup(self, package_sn: str, check_only: bool = False):
        """在结果页点「立即预约」→ 选最早可取件日期 → 提交（写操作；check_only 时只 dump 弹层不提交）。"""
        page = self.open_result_page(package_sn)
        st0 = self._js(page, _JS_PICKUP_STATE) or {}
        self.log("[取件] 当前状态：%s | 已预约=%s | 有「立即预约」=%s"
                 % (st0.get("snippet"), st0.get("booked"), st0.get("has_book_btn")))
        self.shot(page, "16_pickup_before")
        if st0.get("booked"):
            return {"already_booked": True, "before": st0}
        if not st0.get("has_book_btn"):
            raise RuntimeError("结果页上找不到「立即预约」按钮（状态：%s）" % st0.get("snippet"))
        r = self._js(page, _JS_CLICK_BOOK_PICKUP)
        self.log("[取件] 点「立即预约」→ %s" % r)
        if r != "ok":
            raise RuntimeError("点不到「立即预约」（%s）" % r)
        time.sleep(3.5)
        dlg = self._js(page, _JS_DIALOG_DUMP) or {}
        self.log("[取件] 弹层文字=%s" % str(dlg.get("dialogs"))[:400])
        self.log("[取件] 输入框=%s | 按钮=%s | 日期线索=%s"
                 % (dlg.get("inputs"), dlg.get("buttons"), dlg.get("dates")))
        self.shot(page, "17_pickup_dialog")
        if check_only:
            return {"dialogs": dlg.get("dialogs"), "inputs": dlg.get("inputs"),
                    "buttons": dlg.get("buttons"), "dates": dlg.get("dates"), "submitted": False}
        # 读点完之后平台给的结果（成功 / 拒绝）
        res = self._js(page, _JS_PICKUP_RESULT) or {}
        self.shot(page, "18_pickup_result")
        if res.get("fail"):
            # 平台拒绝（实测："预约失败：该地址无法进行预约，可前往 UPS 官网进行预约"）→ 记录并关掉弹窗，不重试
            self.log("[取件] 平台拒绝：%s" % res.get("fail"))
            self._js(page, _JS_CLICK_TEXT, "我知道了")
            time.sleep(1)
            return {"booked": False, "reason": res.get("fail"), "external_hint": res.get("external"),
                    "booked_flag": res.get("booked"), "shot": "18_pickup_result.png"}
        if res.get("booked"):
            return {"booked": True, "success": res.get("success"), "shot": "18_pickup_result.png"}
        raise RuntimeError("点了「立即预约」但既没成功也没看到失败提示（结果=%s）" % str(res)[:200])

    def _cdp_pages(self):
        """用原始 CDP `/json/list` 列标签页（**不受长连接看不到新标签的影响**：本项目已知坑）。"""
        import urllib.request
        raw = urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(self._port), timeout=5).read()
        return [{"id": t.get("id"), "url": t.get("url") or "", "title": t.get("title") or ""}
                for t in json.loads(raw) if t.get("type") == "page"]

    # ---------- 路线用：窗口宽度 / 信号采集 / 分诊 ----------
    def widen_window(self, min_width=None) -> dict:
        """把店窗口拉宽到 ≥ `view.window_min_width`（缺省 1400）。路线 `modal_offscreen` 的执行步：
        关闭键在视口外时鼠标点击落不到（2026-09-21 实测 x=1157 / 视口 1074）。
        """
        want = int(min_width or self.cfg.get("view", {}).get("window_min_width", 1400) or 1400)
        # 取一个**有实际网页内容**的 tab 当锚点：刚后台开出来的 tab 可能是 about:blank /
        # 尚未挂 web contents，用它取 windowId 会得到 `No web contents in the target`（实测）。
        # ⚠ target id **从原始 /json/list 现取**（键名是 `id`）：`CdpPage.targetId` 读的是
        #   `target["targetId"]`，而 list_pages 给的是 `id` ⇒ 经 ctx.pages 拿到的 page.targetId
        #   恒为 None（这同时是 TB-026「is_closed() 恒 False」的根因，另案处理）。
        tid = None
        for t in cdp_transport.list_pages(int(self._port)):
            if str(t.get("url") or "").lower().startswith("http"):
                tid = t.get("id") or t.get("targetId")
                break
        ws = cdp_transport.http_version(int(self._port))["webSocketDebuggerUrl"]
        c = cdp_transport.Cdp(ws)

        def _win(t):
            for _ in range(3):
                try:
                    r = c.send("Browser.getWindowForTarget", {"targetId": t} if t else {}) or {}
                    if (r.get("result") or {}).get("windowId") is not None:
                        return r
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1.5)
            return None

        r = (_win(tid) if tid else None) or _win(None)
        if not r:
            return {"ok": False, "reason": "Browser.getWindowForTarget 取不到 windowId", "want": want}
        info = (r.get("result") or {})
        wid = info.get("windowId")
        bounds = info.get("bounds") or {}
        if wid is None:
            return {"ok": False, "reason": "拿不到 windowId（%s）" % str(r)[:120], "want": want}
        before = int(bounds.get("width") or 0)
        height = int(bounds.get("height") or 900)
        target = max(before, want)
        c.send("Browser.setWindowBounds", {"windowId": wid,
                                           "bounds": {"width": int(target), "height": int(height),
                                                      "windowState": "normal"}})
        time.sleep(2)
        inner = None
        try:
            tgt = next((t for t in cdp_transport.list_pages(int(self._port))
                        if (t.get("id") or t.get("targetId")) == tid), None)
            if tgt:
                pg = cdp_transport.CdpPage(int(self._port),
                                           {"targetId": tgt.get("id"),
                                            "webSocketDebuggerUrl": tgt.get("webSocketDebuggerUrl"),
                                            "url": tgt.get("url")})
                inner = pg.evaluate("() => window.innerWidth")
        except Exception:  # noqa: BLE001
            pass
        self.log("[窗口] 宽度 %s → %s（目标 ≥%s，视口 innerWidth=%s）" % (before, target, want, inner))
        return {"ok": True, "windowId": wid, "before": before, "after": target,
                "innerWidth": inner, "want": want}

    def popup_now(self, page) -> dict:
        """当前挡在前面的弹窗是什么（**认表**）：返回 {"present","id","act","title","buttons"}。

        给路线用：`collect_signals()` 会把它折成 `popup`（登记 id / "unknown" / ""）与
        `popup_unknown`（bool）两个信号 —— 于是"遇到哪个弹窗该走哪条路"能在路线表里表达，
        而不是散在代码里的 if。未登记的弹窗**不在这里登记**（登记统一发生在 dismiss_popups，
        那里有截图与所处步骤），本函数只读不写。
        """
        try:
            raw = self._js(page, _JS_POPUP_PROBE, tries=1)
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            return {"present": False, "id": "", "act": "", "title": "", "buttons": []}
        if not isinstance(d, dict) or not d.get("present") or d.get("maskOnly"):
            return {"present": False, "id": "", "act": "", "title": "", "buttons": []}
        title = str(d.get("text") or d.get("title") or "")
        e = match_popup(title, d.get("text") or "", d.get("buttons"), load_popup_registry(cfg=self.cfg))
        return {"present": True, "id": str((e or {}).get("id") or ""),
                "act": str((e or {}).get("act") or ("stop" if e else "")),
                "title": title, "buttons": list(d.get("buttons") or [])}

    def popup_offscreen(self, page) -> bool:
        """当前遮挡弹窗的关闭键是否落在视口外（路线 `modal_offscreen` 的判据）。"""
        try:
            raw = self._js(page, _JS_POPUP_OFFSCREEN, tries=1)
        except Exception:  # noqa: BLE001
            return False
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            return False
        return bool(isinstance(d, dict) and d.get("present") and d.get("offscreen"))

    def clear_blockers(self, page, rounds: int = 3) -> list:
        """清障步：按视口内可用控件关掉挡页面的提示弹窗；返回已点序列，`"stuck"`＝有遮挡但无可用控件。
        """
        out = []
        for _ in range(int(rounds)):
            try:
                raw = self._js(page, _JS_CLEAR_BLOCKER, tries=1)
            except Exception:  # noqa: BLE001
                return out
            try:
                d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:  # noqa: BLE001
                return out
            if not isinstance(d, dict):
                return out
            if d.get("done"):
                if d.get("why") == "blocked-no-usable-control":
                    self.shot(page, "_blocker_stuck")
                    self.log("[清障] 有遮挡但视口内没有可用关闭控件 → 交给路线 modal_offscreen（widen 后再清）")
                    out.append("stuck")
                return out
            self.cdp_click(page, d.get("x"), d.get("y"))
            self.log("[清障] 关掉遮挡：%s%s"
                     % (d.get("kind"), ("/" + str(d.get("text"))) if d.get("text") else ""))
            out.append(d.get("kind"))
            self.wait("after_clear", 0.4)
        return out

    def _orders_page_ready_lenient(self):
        """订单页就绪（宽松版）：能关的弹窗关掉、其余只记不留痕阻断——供路线 `screen` 用。

        与 `_orders_page_ready()`（strict，起点有未知弹窗就 HARD STOP）的区别：分诊步骤的
        职责是**把现场读清楚**，所以它先把可关的关掉（今天实测订单页的「物流公告」就是这类），
        读不出来的部分交给 signals 与路线，而不是当场停下。
        """
        page = self.orders_page()
        try:
            if not _url_is_orders(page.url or ""):
                # ★ 2026-10-27：复用页不许 goto（见 `_orders_page_ready` 同款注释）
                if page.is_foreign():
                    page, _ = page.open_own_tab(self.cfg["orders_url"])
                else:
                    page.goto(self.cfg["orders_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except ForeignPageError:
            self.log("[页] 拒绝改写复用页 → 改为新开自己的订单页")
            page, _ = page.open_own_tab(self.cfg["orders_url"])
        except Exception:  # noqa: BLE001
            pass
        self.wait("after_goto", 0.5)
        # ★ 2026-09-30：先把「在线下单记录」抽屉关掉（`record` 留下的）——它盖住列表会把读数全带偏。
        self.close_record_drawer(page, "读列表前")
        closed = self.dismiss_popups(page, "screen 起点", strict=False)
        closed = list(closed) + self.clear_blockers(page)   # 清障步：挡页面的提示类弹窗按步骤关掉
        return page, closed

    def close_record_drawer(self, page, why: str = "") -> str:
        """关掉「在线下单记录」抽屉（若开着）。返回 `absent/clicked/no-close/no-root`。

        2026-09-30：`--action record` 打开的抽屉**不关**，会盖住订单列表 → 之后所有列表读取都读到抽屉里的行。
        读列表前一律先调本方法；点不动就用 Esc 兜底。
        """
        try:
            r = self._js(page, _JS_CLOSE_RECORD_DRAWER, tries=1)
        except Exception as ex:  # noqa: BLE001
            self.log("[抽屉] 探测失败（%s）" % type(ex).__name__)
            return "error"
        if isinstance(r, dict) or r == "absent":
            return str(r)
        if r == "clicked":
            self.log("[抽屉] 关掉「在线下单记录」抽屉（%s）" % (why or "读列表前"))
            self.wait("after_click", 0.5)
        else:
            self.log("[抽屉] 关闭键没点到（%s）→ 用 Esc 兜底" % r)
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            self.wait("after_click", 0.5)
        return str(r)

    def read_tab_orders(self, page, tab: str, order_nos: list[str]) -> list[dict]:
        """在某状态页签下按单号筛选并读回本批单（只读）。

        2026-09-30 加：**只保留本批的单**（原来把页面上读到的所有行都往下传，
        于是筛选没生效时会拿"别人的行"当自己的结果 —— 2026-09-28 15:03 那次 206 行就是这么来的）。
        """
        self._search_orders(page, tab, order_nos)
        want = {str(x) for x in order_nos}
        out, rows = [], []
        # ★ 2026-09-30：**筛选没生效就重来一次**。判据＝「一行本批都没匹配到、而读到的行数远多于本批」
        #   （读到全量列表的特征）。实测这一天 3 次里就有 1 次是这样——不重试就会把"没读到"当"没有"。
        for attempt in (1, 2):
            rows = self._js(page, _JS_LIST_ROWS) or []
            out = [{"order_no": str(r.get("ord")), "status": r.get("status") or tab,
                    "track": (r.get("track") or "").strip()}
                   for r in rows if str(r.get("ord")) in want]
            if out or len(rows) <= len(order_nos) + 2:
                break
            self.log("[列表] %s：读到 %d 行、本批 0 行（筛选可能没生效）→ 重填后再读一次" % (tab, len(rows)))
            time.sleep(2.0)
            self._search_orders(page, tab, order_nos)
        if not out:
            self.log("[列表] %s 页签：读到 %d 行，其中本批 0 行" % (tab, len(rows)))
        return out

    def collect_signals(self, page, order_nos: list[str], fail: int = 0) -> tuple[dict, dict]:
        """现取路线信号（+ 明细）。返回 (signals, detail)。

        ★ 2026-09-30：**读数不可信时不许当结论**——按 `signals_consistent()` 复核，
        不符就按页签重读一次；仍不符 → 置 `read_unclear=True`（由 `screen` 停下报"读不清"，
        不再取 `no_orders` 这类路线）。理由：读回没生效时给出的假信号会把"没得买"当结论，
        也会把"别人的行"当自己的单。
        """
        u = str(getattr(page, "url", "") or "")
        want = [str(x) for x in order_nos]
        sig = {"page_kind": "orders", "want": len(want), "pending": 0, "shipped": 0,
               "cancelled": 0, "skippable": 0, "not_found": 0, "fail": int(fail or 0),
               "modal_offscreen": False, "popup": "", "popup_unknown": False}
        if auth_lost_hint_from_urls([u], str(self.cfg.get("_store") or "")):
            sig["page_kind"] = "authentication"
            return sig, {}

        def _one_round() -> tuple:
            got = {t: self.read_tab_orders(page, t, want) for t in ("待发货", "已发货", "已取消")}
            ready, skip_c, skip_s, seen = [], [], [], set()
            for r in got["待发货"]:
                if not r["track"]:
                    ready.append(r["order_no"])
                seen.add(r["order_no"])
            for r in got["已取消"]:
                skip_c.append(r["order_no"])
                seen.add(r["order_no"])
            for r in got["已发货"]:
                skip_s.append(r["order_no"])
                seen.add(r["order_no"])
            s = dict(sig)
            s.update({"pending": len(ready), "cancelled": len(skip_c), "shipped": len(skip_s),
                      "skippable": len(skip_c) + len(skip_s),
                      "not_found": len([o for o in want if o not in seen]),
                      "modal_offscreen": self.popup_offscreen(page)})
            d = {"ready": ready, "skip_cancelled": skip_c, "skip_shipped": skip_s,
                 "not_found": [o for o in want if o not in seen]}
            return s, d

        s, detail = _one_round()
        ok, why = signals_consistent(s)
        if not ok:
            self.log("[分诊] ⚠ 信号不自洽（%s）→ 按页签重读一次，再判" % why)
            time.sleep(2.0)
            s, detail = _one_round()
            ok, why = signals_consistent(s)
        if not ok:
            s["read_unclear"] = True
            s["unclear_why"] = why
        sig = s
        # 弹窗信号（2026-09-21）：路线表要能按"挡在前面的是哪个弹窗"分支，而不是靠代码里的 if。
        _pop = self.popup_now(page)
        sig["popup"] = _pop.get("id") or ("unknown" if _pop.get("present") else "")
        sig["popup_unknown"] = bool(_pop.get("present") and not _pop.get("id"))
        return sig, detail

    def screen(self, order_nos: list[str], fail: int = 0) -> dict:
        """路线`分诊`步（只读）：把本批单在 待发货/已发货/已取消 的分布读出来，
        现取信号 → `pick_route()` 取出该走的那条路线与步骤，作为后续动作的唯一依据。
        """
        page, closed = self._orders_page_ready_lenient()
        sig, detail = self.collect_signals(page, order_nos, fail=fail)
        self.log("[分诊] 信号 %s" % json.dumps(sig, ensure_ascii=False))
        if sig.get("read_unclear"):
            # ★ 2026-09-30：读数不自洽时**不许按它取路线**。原来会取到 `no_orders`（"没有可买的"），
            #   把"列表没读出来"伪装成"这批没得买" —— 假结论会把该买的单漏掉，也会误导后续动作。
            self.shot(page, "00_screen_read_unclear")
            raise RuntimeError("分诊读不清：%s（读数不自洽，不按它定路线）——已落图 00_screen_read_unclear.png"
                               % sig.get("unclear_why"))
        route = pick_route(sig, cfg=self.cfg)
        self.log("[分诊] 路线=%s → 步骤 %s（%s）"
                 % (route.get("id"), " → ".join(route.get("steps") or []), route.get("_为什么") or "-"))
        self.shot(page, "00_screen")
        return {"signals": sig, "detail": detail, "route": route, "popups_closed": closed}

    def _read_records_on(self, page):
        return self._js(page, _JS_SHIPPING_RECORDS) or []

    def _ensure_record_drawer(self):
        """打开「在线下单记录」抽屉并返回 `(page, rows_texts)`（TB-028）。

        两条入口（2026-09-15 实测缺一不可）：
          ① orders.html 提示「在线下单 下单成功 查看」toast——**会消失**，旧实现只认它 ⇒ 读不到任务号；
          ② 「在线下单」页（`cfg.shipment_url`）顶部横幅「在线下单 下单中/成功/失败N 查看」——长期在。
        两条都失败 → 返回空列表（调用方按"读不到"如实报，不猜）。
        """
        page = self._orders_page_ready()
        texts = self._read_records_on(page)
        if not texts:
            r = self._js(page, _JS_OPEN_RECORD_DRAWER)
            self.log("[记录] orders.html 抽屉未开 → 点提示里的「查看」(%s)" % r)
            time.sleep(2.5)
            texts = self._read_records_on(page)
        if not texts:
            url = self.cfg.get("shipment_url")
            if not url:
                return page, texts
            try:
                page2 = self.new_tab_background(url)
                self.wait("after_goto", 0.7)
                r2 = self._js(page2, _JS_OPEN_RECORD_DRAWER_BANNER)
                self.log("[记录] 改走「在线下单」页横幅「查看」(%s)" % r2)
                if r2 == "clicked":
                    time.sleep(3.5)
                    t2 = self._read_records_on(page2)
                    if t2:
                        texts, page = t2, page2
            except Exception as e:  # noqa: BLE001  取记录失败不阻断：调用方按"读不到"处理
                self.log("[记录] 在线下单页取记录失败：%s" % str(e)[:120])
        return page, texts

    def _open_record_detail_real(self, task: str, page=None, expected_total: int = 0):
        before = {t["url"] for t in self._cdp_pages()}
        r = self._js(page or self.orders_page(), _JS_CLICK_ROW_DETAIL, task)
        self.log("[详情] 点「查看详情」(%s) → %s" % (task, r))
        if r != "ok":
            raise RuntimeError("没点到任务 %s 的「查看详情」（%s）" % (task, r))
        target, t0 = None, time.time()
        while time.time() - t0 < 20:
            time.sleep(1.5)
            for t in self._cdp_pages():                      # 新开的结果页
                if "online-shipping-result" in t["url"] and t["url"] not in before:
                    target = t["url"]
                    break
            if target:
                break
        if not target:
            # 兜底（TB-033）：窗口里可能有**别人的**结果页（本机实测另一会话同时在下单），
            # 所以绝不能"取最新的"——按本任务的下单总数与结果页 package_sn_list 的包裹数**对上**才认，
            # 对不上/对上多个就如实报错并列出候选（拿别人的包裹号当本任务完成证据＝假达标）。
            all_c = [t["url"] for t in self._cdp_pages() if "online-shipping-result" in t["url"]]
            if not all_c:
                raise RuntimeError("点了「查看详情」但 CDP 里没有 online-shipping-result 页")
            if expected_total:
                hit = [u for u in all_c if count_packages_in_url(u) == int(expected_total)]
            else:
                hit = []
            if len(hit) == 1:
                target = hit[0]
                self.log("[详情] 无新结果页 → 按包裹数与任务行对账（%d 个）选定" % expected_total)
            elif len(hit) > 1:
                target = max(hit, key=_url_stamp)
                self.log("[详情] ⚠ 有多张包裹数=%d 的结果页，取 refer_page_id 最新那张（共 %d 张）"
                         % (expected_total, len(hit)))
            else:
                raise RuntimeError(
                    "点了「查看详情」但没有属于任务 %s 的结果页（本任务应有 %s 个包裹）；"
                    "窗口内现有结果页包裹数=%s → 不猜、不读别人的页，请人工确认"
                    % (task, expected_total or "?", [count_packages_in_url(u) for u in all_c]))
        self.log("[详情] 结果页 = %s" % target[:110])
        self.reconnect()                                      # 重连才能枚举到新标签
        detail = None
        for p in self.ctx.pages:
            u = p.url or ""
            if "online-shipping-result" not in u:
                continue
            detail = p
            if u == target:
                break
        if detail is None:
            raise RuntimeError("重连后仍找不到结果页对象（CDP 里存在，Playwright 未枚举到）")
        self.wait("after_goto", 0.7)
        return detail

    def _open_detail_page(self, task: str):
        """打开该任务的结果页，返回 `(detail_page, expected_total)`。**只读**。"""
        page, texts = self._ensure_record_drawer()
        if not texts:
            raise RuntimeError("「在线下单记录」抽屉打不开（orders.html 提示 toast 已消失且「在线下单」页横幅无「查看」）"
                               "→ 先人工确认页面，不要凭 exit 报成功")
        # 本任务应有几个包裹（用于 TB-033 的"哪张结果页属于本任务"对账）
        expected = 0
        for row in parse_record_rows(texts):
            if row.get("task") == task:
                try:
                    expected = int(row.get("total") or 0)
                except (TypeError, ValueError):
                    expected = 0
                break
        if not expected:
            self.log("[详情] ⚠ 在线下单记录里没读到任务 %s 的下单总数（无法按包裹数对账）" % task)
        return self._open_record_detail_real(task, page=page, expected_total=expected), expected

    def open_record_detail(self, task: str):
        """只读「点详情」：在在线下单记录里点该任务行的「查看详情」，读结果页（失败包/可用按钮）。

        详情页实测 URL：`online-shipping-result.html?package_sn_list=[...]`（点「查看详情」会新开标签页）。
        注意：本方法**只读**；「重新下单 / 更换物流渠道」走 `reorder_failed()`。
        """
        detail, _expected = self._open_detail_page(task)
        info = self._js(detail, _JS_DETAIL_VIEW) or {}
        self.shot(detail, "15_task_detail")
        info["url"] = detail.url or ""
        info["task"] = task
        return info

    def reorder_failed(self, task: str, check_only: bool = False) -> dict:
        """失败单重下（路线 `partial_failure` 的执行步；提交动作，入口须 `--confirm`）。
        check_only=True 只出计划（开页+读行+算渠道），不点键；判据在 `cfg["failure_retry"]`。
        """
        fr = self.cfg.get("failure_retry") or {}
        rounds = int(fr.get("max_rounds", 1) or 1)
        detail, expected = self._open_detail_page(task)
        rows = self._js(detail, _JS_RESULT_FAIL_ROWS) or []
        self.shot(detail, "16_reorder_plan")
        if not rows:
            txt = self._js(detail, _JS_MODAL_TEXT) or ""
            raise RuntimeError("结果页没认出失败包行（`_JS_RESULT_FAIL_ROWS` 首次真机核验点）；"
                               "页面原文：%s" % str(txt)[:200])
        plan = []
        for r in rows:
            reason = str(r.get("text") or "")
            tri = triage_failure(reason, self.cfg)
            failed_svc = parse_service_from_text(reason, self.cfg)
            choice = choose_reorder_service([], failed_svc, tri["strategy"], self.cfg)
            plan.append({"order": r.get("order"), "reason": reason[:160], "failed_service": failed_svc,
                         "triage": tri, "choice": choice})
            self.log("[重下] %s | 策略=%s | 原渠道=%s | 拟换=%s(%s) | 原因=%s"
                     % (r.get("order"), tri["strategy"], failed_svc or "?",
                        choice.get("service") or "?", choice.get("how"), reason[:80]))
        out = {"task": task, "expected_total": expected, "rounds": rounds, "plan": plan,
               "clicked": False}
        if check_only:
            return out
        # 就地换渠道 + 一键重新下单
        for p in plan:
            r = self._js(detail, _JS_OPEN_RESULT_ROW_SERVICE, p["order"])
            if r == "no-row" or r == "no-select":
                raise RuntimeError("打不开失败行 %s 的物流服务下拉（%s）——选择器首次真机核验点"
                                   % (p["order"], r))
            try:
                at = json.loads(r) if isinstance(r, str) else (r or {})
            except Exception:  # noqa: BLE001
                at = {}
            self.cdp_click(detail, at.get("x"), at.get("y"))
            self.wait("after_open_service", 0.3)
            opts = self._js(detail, _JS_SERVICE_OPTIONS) or []
            ch = choose_reorder_service(opts, p.get("failed_service") or "", p["triage"]["strategy"], self.cfg)
            p["choice"] = ch
            if not ch.get("service"):
                raise RuntimeError("失败行 %s 挑不到可用渠道（策略=%s）" % (p["order"], p["triage"]["strategy"]))
            self._js(detail, _JS_PICK_SERVICE_PREFIX, str(ch["service"]).split(",")[0].strip())
            self.wait("after_pick_service", 0.3)
        self._click_button(detail, "一键重新下单，稍后发货", optional=False)
        self.wait("after_reorder", 0.5)
        for btn in ("确认",):
            self._click_button(detail, btn, optional=True)
        out["clicked"] = True
        out["url"] = detail.url or ""
        return out

    def shipping_records(self, reopen_drawer: bool = True):
        """只读「在线下单记录」：返回结构化任务行（不进详情）。

        用户 2026-09-12 定的平台侧判据：**下单总数 == 成功数**（失败数 0）。
        入口见 `_ensure_record_drawer()`（TB-028：orders.html toast + 「在线下单」页横幅两条路）。
        """
        if reopen_drawer:
            page, texts = self._ensure_record_drawer()
        else:
            page = self._orders_page_ready()
            texts = self._read_records_on(page)
        self.shot(page, "12_shipping_records")
        return parse_record_rows(texts)

    # ---------- ⑦ 核验 ----------
    def verify(self, order_nos: list[str]):
        """逐单读运单号（只读，D6 脚本侧核验）。

        ★ 2026-09-30 修两处：
          ① **只收本批的单**——原来把列表里读到的**所有行**都记进结果，筛选没生效时会把自己没问的单
             也写进 rows（实测把别的 11 单当成了本批结果）；
          ② 两个页签都没匹配到本批任何单时**重读一次**；仍没有 → 打 `read_unclear` 标记（供调用方判"读不清"
             而不是当成"这单没运单号"）。
        """
        page = self._orders_page_ready()
        want = [str(x) for x in order_nos]

        def _round() -> dict:
            res = {}
            for tab in ("待发货", "已发货"):
                self._search_orders(page, tab, want)
                for r in (self._js(page, _JS_LIST_ROWS) or []):
                    o = str(r.get("ord") or "")
                    if o not in want:            # ① 只认本批
                        continue
                    res.setdefault(o, {})
                    res[o].update({"status": r.get("status") or tab, "track": r.get("track") or ""})
            return res

        result = _round()
        if want and not any(result.get(o, {}).get("track") for o in want) and not result:
            self.log("[核验] ⚠ 两个页签都没匹配到本批任何单 → 重读一次")
            time.sleep(2.0)
            result = _round()
        self.shot(page, "08_verify")
        ok = [o for o in want if result.get(o, {}).get("track")]
        out = {"rows": result, "ok": ok, "miss": [o for o in want if o not in ok]}
        if want and not result:
            out["read_unclear"] = True
            out["unclear_why"] = "两个页签都没匹配到本批任何单（筛选/渲染可能没生效）"
        return out


# ---------------------------------------------------------------- 统一入口
def apply_store_overrides(cfg: dict, store: str) -> tuple:
    """按店浅覆盖配置（纯函数，可单测）：返回 (新 cfg, 被覆盖的键列表)。

    2026-09-15 用户「BEL 和 BHY 都需要此操作」：`cfg["stores"][<店>]` 里的键覆盖顶层同名键
    （如 `warehouse_default` / `no_carrier_fallback` / `logistics`），并记 `_store` 供日志与授权命令用。
    只读不写：不改动入参 cfg。
    """
    s = str(store or "").strip()
    if not s:
        return cfg, []
    over = (cfg.get("stores") or {}).get(s) or {}
    over = {k: v for k, v in over.items() if not str(k).startswith("_")}
    out = {**cfg, **over, "_store": s}
    return out, sorted(over.keys())


def run_steps(steps, params: dict, perform_fn, log=print, clock=time.time) -> dict:
    """按 `steps` 顺序执行（**纯编排**：只依赖注入的 perform_fn/clock，无 IO，可离线单测）。

    2026-09-16 用户「你有加载和 pipeline 相关的 SKILL 吗？利用它去固化」：步骤顺序从 SKILL 正文
    与代码 if 链下沉到 `data/temu_buylabel_pipeline.json` 的 `steps` 数组（ai-python-methodology
    「流水线定义规定」D1：这次跑什么放数据 / 怎么跑放代码）。

    · `steps[i].name` 必须是 `perform()` 认识的 action 字面量（pipeline_lint PL-002 静态守着）；
    · `steps[i].params` 覆盖本次调用的基础参数（AI 唯一写区，换批次＝改 json 不改代码）；
    · **失败即停**：某步抛错时包一层「步骤 k/N <name> 失败」再抛出（入口按异常 → HARD STOP 42），
      已跑步骤与耗时逐条留痕，便于断点续跑与复盘。
    · **按分诊路线改道**（2026-09-28 加）：某步输出里带 `signals`+`route`（就是 `screen`）时，
      用纯函数 `pipeline_route_plan()` 决定「停下 / 缩小批量 / 照原样继续」——
      没得买就正常结束（返回 `noop=True`，入口据此自报 [VERDICT] 不算失败）、账目对不上就当场停、
      一部分已发货/已取消就只对**剩下能买的**继续。改批量只改本地 `state`，
      **不动调用方传进来的 params**（既有单测 `test_run_steps_merges_step_params_over_base` 钉着这条）。
    · **某步可回写后续步骤的基础参数**（2026-09-28 加）：输出里带 `_params`（dict）就并进本地 `state`
      ——用在 `auth` 上：关窗重开会换 CDP 端口，不带回就会出现"第 3 步起连老端口"。
    """
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("pipeline 的 steps 必须是数组且非空（改 data/temu_buylabel_pipeline.json）")
    done = []
    state = dict(params or {})           # 只在本地改（缩小批量），不外泄给调用方
    for i, st in enumerate(steps, 1):
        if not isinstance(st, dict):
            raise RuntimeError("steps[%d] 不是对象" % i)
        name = str(st.get("name") or "").strip()
        if not name:
            raise RuntimeError("steps[%d] 缺 name" % i)
        over = st.get("params")
        if over is None:
            over = {}
        if not isinstance(over, dict):
            raise RuntimeError("steps[%d].params 不是对象" % i)
        p = dict(state)
        p.update(over)
        log("[步骤 %d/%d] %s …" % (i, len(steps), name))
        t0 = clock()
        try:
            out = perform_fn(name, p)
        except Exception as e:                      # noqa: BLE001  原样带上步骤号，方便分诊
            raise RuntimeError("步骤 %d/%d %s 失败：%s" % (i, len(steps), name, e)) from e
        dt = round(clock() - t0, 1)
        log("[步骤 %d/%d] %s ✅ %.1fs" % (i, len(steps), name, dt))
        done.append({"i": i, "name": name, "seconds": dt, "out": out})
        # ★ 某步可以回写后续步骤要用的基础参数：`out["_params"]`（例：auth 里关窗重开 ⇒ CDP 端口变了，
        #   不带上就会出现"第 3 步起连老端口"——2026-09-28 实测登录后清障那步已被 10061 拒绝连接）。
        if isinstance(out, dict) and isinstance(out.get("_params"), dict):
            state.update(out["_params"])
            log("[参数] 后续步骤更新：%s" % json.dumps(out["_params"], ensure_ascii=False))
        rp = pipeline_route_plan(out)
        if rp["verdict"] == "narrow":
            state["orders"] = rp["orders"]
            log("[路线] %s" % rp["reason"])
        elif rp["verdict"] == "noop":
            log("[路线] %s" % rp["reason"])
            return {"steps": done, "count": len(done), "stopped": True, "noop": True,
                    "route": rp["route"], "reason": rp["reason"]}
        elif rp["verdict"] == "stop":
            log("[路线] %s" % rp["reason"])
            raise RuntimeError("按分诊路线停下（步骤 %d/%d %s）：%s"
                               % (i, len(steps), name, rp["reason"]))
    return {"steps": done, "count": len(done)}


def perform(action: str, params: dict) -> dict:
    """统一入口（AI监管层：入口脚本只解析参数，逻辑都在这里）。

    params: cfg / log / port / file / orders / evidence_dir / confirm
    """
    cfg = params["cfg"]
    log = params.get("log", print)
    ev = params.get("evidence_dir")

    # ── 分店覆盖（2026-09-15 用户：「BEL 和 BHY 都需要此操作」）──
    # 店后缀从入口传进来（`--store BEL`）。`cfg["stores"][<店>]` 里的键**浅覆盖**顶层同名键
    # （如 warehouse_default / no_carrier_fallback / logistics），并记 `_store` 供日志与授权命令用。
    # 以前 driver 收不到 store（B机 问"BEL 用哪份映射"时只能答"全店共用"），现在按店可覆盖。
    store = str(params.get("store") or "").strip()
    cfg, applied = apply_store_overrides(cfg, store)
    if store:
        log("[店] %s：分店覆盖 %s" % (store, ", ".join(applied) if applied else "（无，用全局口径）"))

    if action == "plan":
        rows = parse_delivery_orders(params["file"])
        plan = build_plan(rows, cfg)
        return {"rows": rows, "plan": plan, "summary": plan_summary(plan)}

    if action == "auth":
        # ⓪ **正常的点击登录**（整链第一步；用户 2026-09-24：「要么直接关掉，要么要正常的点击登录」）。
        # 原来整链没有这一步 ⇒ 遇到登录/认证页只能在下游报 `no-input` 让人去补授权（把活推给人）。
        # **实现（2026-09-28 改）＝先走唯一实现 `run_temu_auth.py`，再用目标级 CDP 兜底**：
        #   ① `temu_abnormal_driver.ensure_auth` 在「不在目标页」时会**先调 `run_temu_auth.py --store <店>`**
        #      （`auth.use_existing_login`，默认 true）——那才是本域登录/授权的唯一实现；2026-09-28 才把
        #      BHY 新授权页的三处一起修好：紫鸟自动填的账密**同步进 React state**（否则点按钮空转、
        #      连一条 XHR 都不发）、条款 checkbox 必须**真实鼠标点 LABEL**、按钮文本补「登录」。
        #   ② 目标级 CDP 那一轮点按仍保留作兜底（候选文案来自 `data/temu_buylabel.json → auth.confirm_texts`）。
        # 事故背景（2026-09-28 当天 8 次 auth 失败）：这里曾写 `use_existing_login=False` **且不给 store**
        #   ⇒ 唯一实现被跳过、只剩一套过期的 JS 点击（文案无「登录」→ `点确认=NOT_FOUND`；DOM 点条款
        #   在新授权页上勾不上），于是"登录态明明能脚本点进去"这件事，在整链里被报成「需人工登录」。
        if not store:
            raise RuntimeError("auth 需要 --store <店后缀>（要登录哪家店）")
        port = int(params.get("port") or 0) or ensure_store_window(store, log=log)
        if not port:
            raise RuntimeError("auth：窗口/端口拿不到（先开 %s 的紫鸟窗口）" % store)
        # 跑之前先清障（用户 2026-09-24「跑脚本前先清卡死页」）：入口页/空页先关掉，
        # 免得登录流程挑到残留页、或下游挑到 auth 页导致 no-input。
        # ⚠ 清障内部保证"最后一个真实标签页不关"；这里再兜底：窗口若被清没了就重开后重探端口。
        try:
            import ziniao_store as _zs0
            _c0 = _zs0.clean_dead_tabs(port=port, log=log)
            log("[授权] 登录前清障：真实标签页 %s；关掉 %d 个；端口存活=%s"
                % (_c0.get("real_tabs"), len(_c0.get("closed") or []), _c0.get("window_alive")))
            if _c0.get("window_alive") is False:
                log("[授权] ⚠ 清障后窗口没了 → 重开窗口一次再连（不连死端口）")
                port = ensure_store_window(store, log=log) or port
        except Exception as ex:  # noqa: BLE001
            log("[授权] 登录前清障跳过（%s）" % str(ex)[:60])

        def _try() -> tuple:
            import temu_abnormal_driver as _tad      # 延迟导入：它反向 import 本模块，顶层会成环
            a = cfg.get("auth") or {}
            acfg = {"url": cfg["orders_url"],
                    "store": store,                  # ★ 2026-09-28：带上店 → 唯一实现与关窗自愈才可用
                    "url_key": str(a.get("url_key") or "mmsos"),
                    "auth": dict(a),                 # ★ 2026-09-28：不再压 use_existing_login=False
                    "popups": {"enabled": False},
                    "waits": {"after_goto": float(a.get("wait_after_goto") or 3.5), "after_tab": 1.5}}
            # ⚠ `store` 必须走**构造参数**（`TemuAbnormalDriver(..., store=)`）——它不看 cfg["store"]；
            #   不给的话 `ensure_auth` 会跳过唯一实现、也跳过「关窗重开再点一次」的自愈（2026-09-28 踩到）。
            d = _tad.TemuAbnormalDriver(acfg, log=log, store=store)
            d.connect(int(port))
            try:
                # 关窗重开（≤1 次自愈）会**换 CDP 端口** → 把最终端口带回给后续步骤，
                # 否则第 3 步起还去连老端口（2026-09-28 实测：登录后清障那步就已 WinError 10061 拒绝连接）。
                return d.ensure_auth(), int(getattr(d, "_port", 0) or port)
            finally:
                d.close()

        out, port = _try()
        log("[授权] %s" % json.dumps(out, ensure_ascii=False)[:200])
        if out.get("ok"):
            # 登录成功后再清一次：把刚才用过的 auth/authentication 与旧授权页收掉——
            # 留着它们会把下游「读订单列表」带偏（挑到 auth 页 → no-input；2026-09-24 实测）。
            try:
                import ziniao_store as _zs
                _cl = _zs.clean_dead_tabs(port=port, log=log)
                log("[授权] 登录后清障：关掉 %d 个入口/空页" % len(_cl.get("closed") or []))
            except Exception as ex:  # noqa: BLE001
                log("[授权] 登录后清障跳过（%s）" % str(ex)[:60])
            return {"auth": out, "ok": True, "_params": {"port": port}}
        # ≤1 次自愈：关窗重开 + 清障 → 再点一次（窗口残留页太多时这是唯一稳的解法）
        new_port = restart_store_window(store, log=log)
        if new_port:
            port = new_port
            out, port = _try()
            log("[授权] 重开窗口后重试：%s" % json.dumps(out, ensure_ascii=False)[:200])
        if not out.get("ok"):
            raise RuntimeError("授权未完成（整链第一步，%s）：%s"
                               % (out.get("kind"), str(out.get("url"))[:90]))
        return {"auth": out, "ok": True, "_params": {"port": port}}

    if action == "record":
        # 平台侧只读核验（用户 2026-09-12 口径）：在线下单记录里 下单总数 == 成功数，不进详情
        drv0 = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv0.connect(int(params["port"]))
            rows = drv0.shipping_records()
            ok, reasons, checked = judge_records(rows, latest_only=bool(params.get("latest_only", True)))
            for r in checked:
                log("[记录] %s | %s | %s | 下单总数=%s 成功数=%s 失败数=%s | %s"
                    % (r["task"], r["at"], r["ship_type"], r["total"], r["success"], r["fail"], r["status"]))
            if not rows:
                log("[记录] ⚠ 一条任务行都没读到（抽屉未开/选择器失效）")
            return {"rows": rows[:10], "checked": checked, "consistent": ok, "reasons": reasons,
                    "verdict": "一致（下单总数=成功数）" if ok else "不一致"}
        finally:
            drv0.close()

    if action == "record_detail":
        # 用户口径里的「点击详情」：只读打开该任务的详情页，报成功/失败包与可用按钮（含能否「重新下单」）
        if not params.get("task"):
            raise RuntimeError("action=record_detail 需要 --task TK-…")
        drv1 = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv1.connect(int(params["port"]))
            info = drv1.open_record_detail(str(params["task"]))
            log("[详情] URL=%s" % info.get("url", "")[:120])
            log("[详情] 包裹概览=%s | 失败线索=%s" % (info.get("summary"), info.get("fails")))
            log("[详情] 可用按钮=%s | 有「重新下单」=%s" % (info.get("buttons"), info.get("can_reorder")))
            retry_cfg = cfg.get("failure_retry") or {}
            # 完成指标（用户 2026-09-14 定）：任务号 + **每个包裹**的运单号/包裹号都要有
            pkgs = parse_package_rows(info.get("packages") or [])
            if not pkgs:
                # 兜底（TB-027）：整页文本按「<运单号> 包裹号：PK-… 订单号：PO-…」三元组解析
                pkgs = parse_packages_from_text(info.get("body_text") or "")
            task = str(params.get("task") or info.get("task") or "")
            if len(pkgs) <= 1:
                # 单包裹：用三号判据（缺号措辞更直白）
                one = pkgs[0] if pkgs else {"waybill": "", "package": ""}
                ok3, missing = check_three_numbers({"task": task, "waybill": one.get("waybill"),
                                                    "package": one.get("package")})
                detail = [{"index": 1, "waybill": one.get("waybill", ""),
                           "package": one.get("package", ""),
                           "ok": bool(one.get("waybill") and one.get("package"))}]
            else:
                ok3, missing, detail = check_packages(task, pkgs)
            info["numbers"] = {"task": task,
                               "waybill": detail[0]["waybill"] if detail else "",
                               "package": detail[0]["package"] if detail else ""}
            info["per_package"] = detail
            info["complete"] = ok3
            info["missing"] = missing
            log("[详情] 逐包裹核号：任务号=%s | %d 个包裹" % (task or "-", len(detail)))
            for row in detail:
                log("       #%d 运单号=%s 包裹号=%s %s"
                    % (row["index"], row["waybill"] or "（缺）", row["package"] or "（缺）",
                       "✅" if row["ok"] else "⚠️"))
            log("[详情] 完成指标：%s" % ("✅ 完成（任务号 + 每个包裹双号齐）" if ok3
                                        else "⚠️ 未完成（缺 %s）" % "；".join(missing)))
            info["next_step"] = ("发现失败包 → 按 D8：该页点「重新下单」→ 在批量页**更换物流渠道**"
                                 "（顺序：%s；最多重试 %s 轮）"
                                 % (" → ".join(retry_cfg.get("carrier_order") or []),
                                    retry_cfg.get("max_rounds", 1)))
            return info
        finally:
            drv1.close()

    if action == "reorder":
        # 失败单重下（路线 partial_failure 的执行步）：判据在 cfg["failure_retry"]，步骤在 reorder_failed()
        if not params.get("task"):
            raise RuntimeError("action=reorder 需要 --task TK-…")
        drv_r = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv_r.connect(int(params["port"]))
            return drv_r.reorder_failed(str(params["task"]),
                                        check_only=bool(params.get("check_only")))
        finally:
            drv_r.close()

    if action == "pickup":
        # 写操作（改平台状态：预约上门取件）。check_only=True 时只打开弹层 dump，不提交。
        pkg = params.get("package")
        if not pkg:
            raise RuntimeError("action=pickup 需要 --package PK-…（包裹号）")
        drv2 = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv2.connect(int(params["port"]))
            return drv2.book_pickup(str(pkg), check_only=bool(params.get("check_only")))
        finally:
            drv2.close()

    if action == "widen":
        # 路线 `modal_offscreen` 的执行步：不需要单号，先把窗口拉到 ≥ view.window_min_width
        drv_w = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv_w.connect(int(params["port"]))
            return drv_w.widen_window(params.get("min_width"))
        finally:
            drv_w.close()

    if action == "overdue":
        # 待办「即将逾期未发货」整条（2026-09-30 用户指的流程）：单子由**页面待办现取**，外部不用给单号。
        drv_o = BuylabelDriver(cfg, log=log, evidence_dir=ev)
        try:
            drv_o.connect(int(params["port"]))
            return drv_o.overdue_orders(params.get("todo_text"))
        finally:
            drv_o.close()

    order_nos = params.get("orders") or [p["order_no"] for p in params.get("plan", [])]
    if not order_nos:
        raise RuntimeError("action=%s 需要 --file 或 orders" % action)

    drv = BuylabelDriver(cfg, log=log, evidence_dir=ev)
    try:
        drv.connect(int(params["port"]))
        if action == "screen":
            # 路线分诊（只读）：读三页签 → 现取信号 → pick_route() 决定后续步骤
            return drv.screen(order_nos, fail=int(params.get("fail") or 0))
        if action == "verify":
            return drv.verify(order_nos)
        if action == "confirm":
            return drv.confirm_shipping(order_nos)
        if action == "place_only":
            # 只走最后一步：不新开页、不重填，直接在「确认前」那页点确认下单。
            # check_only=True → 只读+过闸门+存状态（不点）。
            page, st, reasons = drv.ready_to_place(order_nos, params.get("reuse_page"))
            log("[⑤] 确认前状态：" + json.dumps(st, ensure_ascii=False))
            drv.shot(page, "09_ready_to_place")
            drv.save_state(st, "09_ready_to_place",
                           extra={"url": page.url or "", "orders": order_nos,
                                  "action": "place_only",
                                  "hint": "随时可下单：--action place_only --store <店> --orders <%s> --reuse-page <本页URL片段> --confirm"
                                          % ",".join(order_nos)})
            if reasons:
                raise RuntimeError("页面未就绪，拒绝确认下单：%s" % "；".join(reasons))
            if params.get("check_only"):
                log("[⑤] 已过闸门：可确认下单（本次只检查，未点任何按钮）")
                return {"ready": True, "placed": False, "state": st}
            rows = parse_delivery_orders(params["file"]) if params.get("file") else []
            plan = build_plan(rows, cfg) if rows else []
            try:
                out = {"ready": True, "state": st, "place": drv.place(page, plan)}
            except Exception as ex:  # noqa: BLE001
                # ★ 2026-09-30：「提交之后传输层断连」≠ 没下成。实测（BHY 11 单）点完
                #   「已确认，继续下单」后报 `server rejected WebSocket connection: HTTP 500`，
                #   工具判失败，而平台侧 11/11 已下单成功（2026-09-28 同类第三次）。
                #   处理：**回读平台状态再判**——全部有运单号＝成功（不重复下单）；否则如实报未完成。
                if not is_transport_error(ex):
                    raise
                log("[⑤] ⚠ 提交后传输层断了（%s：%s）→ 回读平台状态再判（不重复下单）"
                    % (type(ex).__name__, str(ex)[:120]))
                rb = {}
                try:
                    rb = drv.verify(order_nos)
                except Exception as ex2:  # noqa: BLE001
                    log("[⑤] 回读也失败（%s）→ 如实报失败，绝不重试提交" % type(ex2).__name__)
                ok = list(rb.get("ok") or [])
                out = {"ready": True, "state": st, "place_error": str(ex)[:200], "readback": rb}
                if order_nos and len(ok) == len(order_nos):
                    out["place"] = {"submitted": "回读确认已下单（运单号齐 %d 单）" % len(ok)}
                    log("[⑤] 回读确认：%d/%d 单已下单成功（本次提交其实成了）" % (len(ok), len(order_nos)))
                else:
                    raise RuntimeError(
                        "提交后断连，回读 %d/%d 单有运单号 → 未完成，不自动重下单；"
                        "人工/重跑前先看 out/…/buylabel/ 的证据：%s"
                        % (len(ok), len(order_nos), str(ex)[:120]))
            return out
        # fill / place / all 需要计划
        rows = parse_delivery_orders(params["file"]) if params.get("file") else []
        plan = build_plan(rows, cfg) if rows else params.get("plan") or []
        if not plan and order_nos:
            # ★ 2026-10-09：**没有派送订单表**、只给了 --orders 的场景（用户当天口径：
            #   「重新打开这 15 单的批量页 → 执行到填充长宽高」）。此时按订单号直接建 plan：
            #   数量未知按 1 计（重量 = 基准 × 1），仓库用该店默认仓。
            #   这不是猜数：顶部「批量填充」本就把基准重/仓库铺给所有行，逐单修正只在
            #   「多件」或「仓库不同」时才需要；没有表就没有这些差异信息。
            plan = [{"order_no": o, "qty": "1", "weight": 0.0, "weight_txt": "",
                     "warehouse_raw": "", "warehouse": cfg.get("warehouse_default", "加州三仓"),
                     "sku": ""} for o in order_nos]
            log("[计划] 无 --file → 按 %d 个订单号直接建 plan（数量按 1、仓库用默认仓）" % len(plan))
        if not plan:
            raise RuntimeError("action=%s 需要 --file（派送订单表）或 --orders" % action)
        batch = drv.open_batch(order_nos, reuse_page=params.get("reuse_page"))

        # ★ 2026-10-09 用户口径：**单跑** weight_fix（不重填、只核对并改重量）。
        #   适用于「页已填好、只差把多件单重量改对」的场景；也便于分步排查。
        if action == "weight_fix":
            wf = drv.weight_fix(batch, plan=plan, weight_per_unit=params.get("weight_per_unit"),
                                stop_on_unfixed=bool(params.get("stop_on_unfixed")))
            return {"weight_fix": wf}

        filled = drv.fill(batch, plan,
                          weight_per_unit=params.get("weight_per_unit"),
                          stop_after=params.get("stop_after") or "")
        out = {"fill": filled}
        # `--stop-after dims` ＝ 有意停在物流服务之前 → 不做 place/confirm/verify（那是后续人工接手的事）
        if str(params.get("stop_after") or "") == "dims":
            return out
        if action in ("place", "all"):
            out["place"] = drv.place(batch, plan)
            if action == "all":
                out["confirm"] = drv.confirm_shipping(order_nos)
                out["verify"] = drv.verify(order_nos)
        return out
    finally:
        drv.close()


# ---------------------------------------------------------------- 维护约定
# 1) **页面选择器与交互坑全部集中在文件头注释 + `_JS_*` 常量区**：TEMU 页面改版时只改这里。
#    改完必须跑：`python -m py_compile <本文件>` + `python -m pytest scripts/power/tests/test_temu_buylabel.py -q`
#    + 真机 `--action fill` 复验，确认无误再考虑 place。
# 2) **口径一律改 `data/temu_buylabel.json`**（尺寸/单件重/仓库映射/物流商范围/取件策略/异常分支/等待），
#    不改本文件结构；要新能力＝加 action/参数，不 fork 脚本。
# 3) **写操作闸门在入口**（`run_temu_buylabel.py` 的 `MONEY_ACTIONS` + `--confirm`）。本驱动内的
#    `place()` / `confirm_shipping()` 只负责执行，不自行判断"该不该提交"。
# 4) **交付达标判据是内容不是进程返回值**：成功 = 该单在订单列表出现「运单号」，
#    故 `verify()` 读运单号做断言，不采信 `place()` 返回的 `tab_closed`（经验飞轮 EF-018）。
# 5) 本驱动被 tests import，**禁止在模块顶层包装 sys.stdout**（夺走 pytest 捕获流，见 BL-007）。

# ---------------------------------------------------------------- 变更记录
# 格式见 experience-flywheel「写回契约」；每条必须带 `→ 修订/红线/附注` 锚点。
#
# - BL-001 批量在线下单＝先进批量页再统一填；行内「物流服务」在行内「发货仓库」未选前 disabled，
#   顺序必须 仓库→物流服务，否则确认填充后该行服务为空 → 修订: fill() 步骤顺序（②仓库 → ③批量填充 → ④物流服务）
# - BL-002 仓库下拉项是 li/[role=option]，仓库名在**首行**、次行是备注（如"库存扣减仓"），
#   整串精确匹配必失败 → 修订: _JS_PICK_OPTION_FIRSTLINE（按首行匹配）
# - BL-003 物流服务下拉项**不是** li/[role=option] 而是普通 div，只查 li 永远取空 →
#   修订: _JS_SERVICE_OPTIONS / _JS_PICK_SERVICE_PREFIX（文本前缀 + 长度/子节点数过滤）
# - BL-004 上门取件日期框用 JS click 打不开日历，必须真实鼠标点击（坐标由 getBoundingClientRect
#   现取、非写死，故不受窗口尺寸影响） → 修订: place() 日期选择（_JS_FIND_PICKUP_DATE_FIELD + page.mouse.click）
# - BL-005 弹窗里的「确认」若按模糊/包含匹配，会点到页面上的「确认下单」，造成弹窗叠窗且订单未下 →
#   修订: _JS_CLICK_BUTTON（精确文本 + 取**最后一个**可见按钮）
# - BL-006 pandas 读 xlsx 时「总计」行参考单号为 NaN，`str(v or "")` 得到的 'nan' 会被当订单号留下 →
#   修订: _s() 统一 NaN/None→空串 + parse_delivery_orders 过滤
# - BL-007 入口在模块顶层包装 sys.stdout，被 tests import 时夺走 pytest 捕获流
#   （I/O operation on closed file） → 修订: run_temu_buylabel._wrap_stdout() 由 main() 调用；
#   域规则见 scripts/power/README.md §五（先批量修 46 个顶层写法，才谈做成红线，否则闸门恒红）
# - BL-008 [用户 2026-09-12 定] 成功判据＝待发货该单出现「运单号」即下单成功，**不要求**进「已发货」 →
#   修订: verify() 断言口径；附注: 同批 9 单另经 confirm_shipping() 才转「已发货」
# - BL-009 [用户 2026-09-12 拍板改] 紫鸟这条 CDP 长连接**收不到新开标签页**：实测新建 target 后
#   原始 CDP `Target.getTargets` 已能看到（page target 6→7），但同一连接的 `ctx.pages` 恒为 6；
#   故原「点批量在线下单 → 等新页出现」在 fill/place/all 上必然超时（当日两次 all 均卡死于此，
#   未提交）。同一实验证明：`ctx.new_page()` 自己开的页可见（6→7→6），**重连**后也能枚举到旧连接
#   看不到的标签页 → 修订: open_batch 改为 P0 自开批量页（构造 URL，不再依赖弹窗检测）；
#   P1 兜底＝URL 判据（换掉 id 判据）+ 20s 超时后 reconnect 再枚举；P2 = --reuse-page 复用已存在页。
#   附注: 判定键一律用 URL，不用 id(p)（实测 id 稳定，但 URL 可解释、可跨重连比较）。
# - BL-010 [用户 2026-09-12 定：确认前状态要留住、随时可只点最后一步] 原 `all/place` 必须自开页+重填，
#   无法在"已经填好的页"上只补最后一下 → 修订: 新增 `action=place_only`（复用已填页 → 过 judge_filled
#   闸门 → 只点确认下单，不重填）+ `--check-only`（只读校验并在 out 目录落 09_ready_to_place.png/.json
#   存"确认前状态"）。闸门纯函数 judge_filled：行内重量为空 / 仓库与服务未选全 / 无「确认下单」按钮 /
#   URL 不含目标单号 → 一律拒绝下单（防误提交与串单）。
# - BL-011 [用户 2026-09-14 报障「窗口会置顶」] 驱动原有 5 处 `bring_to_front()` + 3 处 `ctx.new_page()`
#   （Playwright 前台开页）＝把紫鸟窗口提到最前的根因 → 修订: 新增
#   `new_tab_background()`（原生 CDP Target.createTarget background=true + 重连枚举；失败**不回退前台**）/
#   `cdp_click()`（原生 CDP Input.dispatchMouseEvent 代替 page.mouse.click）/ `_orders_page_ready()`
#   （已在订单页就不重复 goto）；全部 bring_to_front/new_page 调点已清除，并由
#   tests/test_temu_buylabel_gates.py 静态拦死（T1）
# - BL-012 [同日] fill 两次 exit=42，日志尾＝Playwright `Execution context was destroyed,
#   most likely because of a navigation`；根因是"orders_page() 之后又 goto 同一 URL"的双重导航 →
#   修订: `_js()` 对导航竞态等页面稳定后重试 ≤3 次；`_orders_page_ready()` 去掉重复导航
# - BL-013 [同日，登记表 F/G/I 段] 驱动缺爬虫及格线三项：占用守卫 / 真无图 / 动作节流 →
#   修订: `temu_tab_occupied(port)`（raw_cdp.js 读 attached，被占即 HARD_STOP 不硬插，探测不可用不阻断）+
#   `_block_heavy(page)`（Network.setBlockedURLs 拦图片/字体/媒体）+ `_throttle()`（口径 view.min_action_gap_ms）；
#   connect() 里跑占用守卫，新页/复用页都上无图
# - BL-014 [同日实测] 入口 `run_temu_buylabel.py` 被写成带 **UTF-8 BOM**（`py_compile` 能过、
#   `ast.parse` 报 `invalid non-printable character U+FEFF`），系并发会话用 PowerShell 文本命令改写所致 →
#   修订: 已去 BOM；闸门 tests/test_temu_buylabel_gates.py 第③条
#   附注: 印证项目铁律「禁止用 PowerShell 文本 cmdlet 往返改写含中文源码」
# - BL-015 [用户 2026-09-15 选方案 A] 31 单批量买面单失败，根因＝**订单列表每页只渲染 20 行**，
#   而 `_JS_LIST_ROWS` 只读当前页：① 缺单检查误报「这些单在待发货里没查到」（后 11 单被误判）；
#   ② 就算绕过该检查，`_JS_CHECK_ORDERS_ALL` 的「全选」也只勾当前页 ⇒ 只会买 20 单面单（**部分下单**）。
#   实测：筛选框 31 个单号全填进去了（tokens=31）、查询 URL 的 parentOrderSnList 也带全，
#   但表格 rowCount=20；URL 参数 pageSize/size/pageLimit/page_size **全部无效**（仍 20）。
#   页面「每页」控件实测：`li.PGT_sizeChanger_123 > div[data-testid=beast-core-select]
#   > div[data-testid=beast-core-select-header]`（当前值在表头 input.value），表头要**真实鼠标**点才展开，
#   选项是 `[class*=ST_itemRender]`，值为 5/10/20/50/100/500 → 修订: 新增 `set_list_page_size()`
#   （在 `_search_orders` 查询后调用，纯函数 `pick_page_size_option` 选值）+ 口径 `batch.page_size`
#   + 缺单报错带上「本批 N 单/列表读到 M 行」+ 单测
# - BL-016 [用户 2026-09-15 选方案 A 后真下单] 分页修好后**订单仍没下成、且不报错**：点「确认下单」后
#   平台弹的不是「确认下单？/确认」，而是**异常单提醒**「共 N 个订单…以下订单/包裹存在异常，
#   是否确认无误并继续下单？」，按钮是**「确认无误，继续下单」**（BHY 31 单实测：5 个订单因
#   「订单货值过高，建议选择需要签名服务」被点名）→ 旧 `place()` 判据不匹配 ⇒ 静默没点、
#   只返回 `tab_closed:false`（**"以为下单了其实没下"**） → 修订: 新增 `_place_modal_step()` +
#   `_PLACE_SUBMIT_BTNS=("确认无误，继续下单",)` + `_PLACE_NOTICES=("物流细则",)`；
#   `place()` 先清**已开着**的弹窗再决定要不要点「确认下单」（防在遮罩下重复点底层按钮），
#   把「异常提醒 → 确认下单？→ 上门取件 → 已确认，继续下单」整条链走通。
#   实测成功链（10:27:30）：`submit:确认无误，继续下单(ok)` → `submit:确认(ok)` → 上门取件弹窗
#   5 组（GOFO 8/GOFO 6/SwiftX 4/SpeedX 2/YANWEN 1 = 21 包裹）逐行选最早日期 2026-09-15 →
#   「已确认，继续下单」→ 平台侧 TK-5080605052108853163（下单总数 31 / 成功 31 / 失败 0 / 全部成功）
#   → 逐单运单号+包裹号齐（check_packages complete=True）
# - BL-017 [同日实测] 证据截图**全部 0 字节**（01/02/03/04/07/08 都失败在 `KeyError 'data'`）：
#   `cdp_transport.Cdp.send()` 返回的是**整条 CDP 消息**（`{"id":..,"result":{"data":..}}`），
#   而 `shot()` 取的是 `r["data"]` → 取不到 → 抛错被吞 → 落盘 0 字节空文件
#   → 修订: `shot()` 改取 `r["result"]["data"]`（无 data 则显式抛错，不再静默写空文件）

# - BL-018 [用户 2026-09-16「你有加载和 pipeline 相关的 SKILL 吗？利用它去固化」] 步骤顺序此前**硬编码在
#   本文件 perform() 的 if 链**（all = open_batch→fill→place→confirm→verify），改顺序＝改代码，SKILL 正文
#   又写一份 → 与 ai-python-methodology「流水线定义规定」D1（这次跑什么放数据 / 怎么跑放代码）冲突，
#   `pipeline_lint` 对 temu-buylabel SKILL 报 `PL-004 顺序写在正文`（红） → 修订: 新增**纯函数
#   `run_steps(steps, params, perform_fn, log, clock)`**（按序驱动 perform、**失败即停**并把异常包成
#   「步骤 k/N <name> 失败：<原文>」，已跑步骤与耗时逐条留痕），顺序下沉到 `data/temu_buylabel_pipeline.json`
#   的 `steps`；入口加 `--action pipeline` / `--steps-config`。**all 未动**（向后兼容，B机 远端链不受影响）。
# - BL-019 [2026-09-28 复盘：当天 47 次跑 / 24 次失败] `screen` 早把路线判出来了（routes.json 是唯一权威），
#   但 pipeline **固定九步照跑** ⇒ 三类假失败：①「可买 0 单」（全已取消/已发货，BEL 28 单那次）该走
#   `stop_report` 却继续 fill → 报 HARD STOP 失败；②「一部分已发货/已取消」（BHY 172 单里 72 单已发货）该
#   `exclude_*` 后继续，实际拿全批去查 → 缺单白停；③ `not_found>0`（BEL 234 单一个都没查到）该在分诊步
#   当场点名，却拖到 fill 才报「这些单在待发货里没查到」 → 修订: 新增**纯函数 `pipeline_route_plan(step_out)`**
#   （continue / noop / stop / narrow 四态）+ `run_steps` 里按它改道：noop＝正常结束（入口自报 [VERDICT]
#   不算失败）、stop＝分诊步停下并点名、narrow＝只对「剩下来能买的」继续（**只有账目自洽才允许**：
#   not_found==0 且 0<pending<want 且 ready 条数==pending；否则原样继续——宁可停下，不许静默少买）。
#   附注: 改批量只改 run_steps 本地 state，不动调用方 params（既有单测钉着这条）。
# - BL-020 [同日同批复盘] 15:33 BHY 172 单报「页大小不足…后 100 单落在页外」，实为「每页」读不到
#   （`no-sc`）+ 列表只渲染 72 行，同一份表原样重跑 `fill` 就成功（53.1s） ⇒ 修订: 缺单且
#   `page_size_suspect` 命中时**先重查一次再判**（消渲染/筛选竞态；重查后仍要求「全部单都在」才继续，
#   不会少买），并把报错改成如实列两种可能（页大小 / 查询结果不完整），不再一口断定"落在页外"。
# - BL-021 [2026-09-28 用户「登录态是可以脚本点击的」当场实测] **auth 步把唯一实现关掉了**：代码里写
#   `use_existing_login=False` **且不给 store** ⇒ `ensure_auth` 跳过 `run_temu_auth.py`（本域登录/授权的
#   唯一实现；它 2026-09-28 才修好 BHY 新授权页三处：账密**同步进 React state**、条款 checkbox 必须
#   **真实鼠标点 LABEL**、按钮文本补「登录」），只剩一套过期的 JS 点击 ⇒ 当天 8 次 auth 失败全报
#   「需人工登录」。而 `data/temu_buylabel_pipeline.json` 的 auth 步说明**本来就写着"调唯一实现
#   run_temu_auth.py"**——文档与代码静默漂移。 → 修订: ① `TemuAbnormalDriver(..., store=store)`
#   要走**构造参数**（它不看 cfg["store"]）、② auth 配置不再压 `use_existing_login=False`（恢复默认 true）
#   ⇒ 先走唯一实现，目标级 CDP 那轮点按降为兜底；③ `data/temu_buylabel.json → auth.confirm_texts`
#   末位补「登录」（兜底那轮的整串精确匹配用）；④ **端口回写**：auth 里「关窗重开」会换 CDP 端口，
#   现在 auth 步返回 `_params={"port": <最终端口>}`，`run_steps` 并进后续步骤的基础参数（否则第 3 步起
#   连老端口，2026-09-28 实测登录后清障那步已 `WinError 10061` 拒绝连接）。
#   实测（2026-09-28 16:2x，BEL 从 `settle/seller-login` 起步、**真机**）：关窗重开后 1 轮点过 →
#   `{"ok": true, "kind": "target", "url": ".../mmsos/orders.html"}`，exit 0；紧接着只读 `screen`
#   同一批 234 单读出 `pending=0 / shipped=42 / not_found=0`（15:03 那次同批是 not_found=234 报失败）。
