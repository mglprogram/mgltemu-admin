# -*- coding: utf-8 -*-
"""temu_presale.py — TEMU「销量分析」页读取层（预售判定用）。

比 `temu_stock_topup.read_live_rows` 多两件事：
  ① 读**预计未来7日/14日总销量**（每个 SKU 行的预测列）——预售判定要的「需求」
  ② 读**售罄状态**，并且会**自己开/关「快速筛选」chip**把「已售罄 + 即将售罄」两批都抓下来

快速筛选 chip 的行为（2026-10-10 实测，BHY）：
  · 是**独立开关**，不是单选：两个都开 = 两批并集（已售罄 4 + 即将售罄 3 = 7 行）
  · 点第二下 = 取消
  · 卡片 DOM：`div[class*=quick-overdue-filter_card]`，文本形如「即将售罄 3」
  · **没有暴露选中态 class** ⇒ 本模块不猜状态，而是：先读当前页，看已有哪些「售罄状态」，
    只对**缺的那一类**点开 → 读完 → **再点一下还原**。这样永远不会把已经开着的 chip 点关。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

#: 一页 SKU 行的解析（锚点法：不按固定列下标，列会因 rowspan 合并而错位）
_ROWS_JS = r"""
() => {
  const num = t => { const m = String(t||'').replace(/,/g,'').match(/-?\d+(\.\d+)?/); return m ? Number(m[0]) : null; };
  const out = [];
  let prod = '', title = '';
  for (const tr of document.querySelectorAll('tbody tr')) {
    const cells = [...tr.children].map(td => (td.innerText || '').trim().replace(/\s+/g, ' '));
    const joined = cells.join(' | ');
    const pm = joined.match(/SPU[：:]\s*(\d+)[\s\S]*?SKC[：:]\s*(\d+)[\s\S]*?SKC货号[：:]\s*([A-Za-z0-9\-]+)/);
    if (pm) {
      prod = 'SPU ' + pm[1] + ' / SKC ' + pm[2] + ' / SKC货号 ' + pm[3];
      const own = cells.find(c => c.indexOf('SPU') >= 0) || '';
      title = own.split('SPU')[0].trim();
    }
    const cm = joined.match(/SKU ID[：:]\s*(\d+)[\s\S]*?SKU货号[：:]\s*([A-Za-z0-9\-]+)/);
    if (!cm) continue;
    const si = cells.findIndex(c => c.indexOf('SKU货号') >= 0);
    // 「发货模式」单元格（卖家自发货 / 平台发货）是库存那一段的锚点
    const mi = cells.findIndex(c => /发货$/.test(c));
    if (si < 0 || mi < 0 || mi <= si) continue;
    // 发货模式之前、SKU 格之后的数字，顺序是固定的：
    //   今日SKC销量 | 今日SKU销量 | 预计未来7日总销量 | 预计未来14日总销量 |（后面还可能跟别的数字）
    //
    // ⚠ 2026-10-14 修：原来是「取最后两个/三个」，会被末尾那个额外数字顶偏
    //   —— 实测 BHY-203802-4SetA-L 该段是 [9,4,17,34,117]，最后两个是 34/117，
    //   于是「预计未来7日」一直读成 14 日（17 被读成 34），日均/现货可售天数/判定全跟着错。
    //   改成**从锚点往后按固定序号取**：nums[0..3]。
    const nums = cells.slice(si + 1, mi).map(num).filter(v => v !== null);
    const after = cells.slice(mi + 1);
    // 售罄状态格：正常款是「-」，异常款是「已售罄」（可能带尾巴，如「已售罄 连续售罄12天」）
    const raw = after.find(c => /^(已售罄|即将售罄|低库存预警)/.test(c)) || '';
    const sm = raw.match(/^(已售罄|即将售罄|低库存预警)/);
    const statusCell = sm ? sm[1] : '';
    out.push({
      sku_code: cm[2],
      sku_id: cm[1],
      title: title,
      product: prod,
      size: (cm[2].split('-').pop() || ''),
      today_skc: nums.length > 0 ? nums[0] : null,
      today_sku: nums.length > 1 ? nums[1] : null,
      forecast7: nums.length > 2 ? nums[2] : null,
      forecast14: nums.length > 3 ? nums[3] : null,
      available: after.length ? num(after[0]) : null,
      sold_days: after.length > 2 ? after[2] : '',
      status: statusCell,
      ship_mode: cells[mi] || ''
    });
  }
  return out;
}
"""

#: 点某一类快速筛选 chip（开关式）
_CHIP_JS = r"""
(name) => {
  const cards = [...document.querySelectorAll('[class*=quick-overdue-filter_card]')]
    .filter(e => (e.innerText || '').trim().startsWith(name));
  if (!cards.length) return 'notfound';
  cards[0].click();
  return 'clicked';
}
"""

#: 快速筛选卡片上的计数（如 已售罄 4），用于自检
_CHIPS_JS = r"""
() => [...document.querySelectorAll('[class*=quick-overdue-filter_card]')]
  .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())
"""

#: 分页器上的「共有 N 条」——用来确认当前页是不是把整批都拿到了
_PAGER_JS = r"""
() => {
  const m = (document.body.innerText || '').replace(/\s+/g, ' ').match(/共有\s*(\d+)\s*条/);
  return m ? Number(m[1]) : -1;
}
"""

DEFAULT_TARGETS = ("已售罄", "即将售罄")
DEFAULT_URL = "https://agentseller.temu.com/main/data-center/goods-data"


def _chip_counts(chips: list) -> dict:
    """`['已售罄 4', '即将售罄 3', …]` → `{'已售罄': 4, '即将售罄': 3}`（chip 上的计数）。"""
    out: dict[str, int] = {}
    for c in chips or []:
        parts = str(c).split()
        if len(parts) >= 2 and parts[-1].isdigit():
            out[parts[0]] = int(parts[-1])
        elif parts:
            out.setdefault(parts[0], 0)
    return out


class NeedAuth(RuntimeError):
    """窗口的商家中心会话掉了（被弹回认证页）—— 要先跑授权，不是脚本能自愈的。"""


def _close_target(port: int, tid: str) -> None:
    """按 targetId 关掉自己开的那个标签页（不依赖 CdpPage 对象，异常路径也能收干净）。

    走 `cdp_transport.close_target`：它会**同时把登记表里那条记录删掉**
    （登记表是抗强杀的，见 `sweep_leaked_scratch`），并且幂等、永不抛。
    """
    if not tid:
        return
    try:
        from cdp_transport import close_target  # noqa: WPS433
        close_target(port, tid)
    except Exception:  # noqa: BLE001
        pass


def _scratch_page(port: int, url: str, log=print, timeout_s: float = 25.0):
    """**新开一个后台标签页**去读，读完关掉 —— 用户自己那个标签页的查询/筛选一点都不动。

    为什么不能直接在用户的页面上点（2026-10-12 踩的坑）：
      · 「快速筛选」是与页面已有的「商品查询」**求交集**的 —— 用户页面上留着 `202504`，
        点「即将售罄」只剩 2 行，不点又是 30 行，读数完全不可信；
      · 在别人页面上点来点去还会留下状态。

    ★ 必须**按 targetId 认自己新开的那一页**：窗口里本来就可能有用户自己开着的
    goods-data 标签页，按 URL 匹配会选中它 —— 2026-10-12 BEL 就这么卡住 + 动了用户的页。
    掉登录立刻抛 NeedAuth，并把临时页关掉。

    返回 (page, targetId)。
    """
    from cdp_transport import connect, create_target  # noqa: WPS433

    tid = create_target(port, url)
    page = None
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                for p in connect(port).contexts[0].pages:
                    if getattr(p, "targetId", None) == tid:
                        page = p
                        break
            except Exception:  # noqa: BLE001
                continue
            if page is not None:
                u = page.url or ""
                if "auth/authentication" in u or "settle/seller-login" in u:
                    raise NeedAuth("窗口会话掉了（落在 %s）→ 需要重新授权" % u[:80])
                try:
                    if (page.evaluate("() => document.querySelectorAll('tbody tr').length") or 0) > 0:
                        return page, tid
                except Exception:  # noqa: BLE001
                    pass
    except NeedAuth:
        _close_target(port, tid)
        raise
    if page is not None:
        log("   [TEMU] 临时页打开了但一直没出数据行（该区无权限 / 没加载完）")
        return page, tid
    _close_target(port, tid)
    raise RuntimeError("开不出 %s 的临时标签页" % url)


def _wait_rows(page, target: str = "", want: int = -1,
               timeout: float = 30.0, log=print) -> list:
    """等表格刷成「我们要的那一批」再读。

    为什么必须轮询：点完 chip 后表格**先保留旧行再异步换**（实测约 2 秒），
    固定 sleep 会读到上一批。判据（满足任一即算刷新完成）：
      · 表里**所有**行都是目标状态；
      · 行数正好等于 chip 上的计数，且其中有目标状态的行。
    """
    end = time.time() + timeout
    last: list = []
    while time.time() < end:
        try:
            rows = page.evaluate(_ROWS_JS) or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            last = rows
            statuses = {r.get("status") for r in rows}
            if target and statuses == {target}:
                return rows
            if target and want > 0 and len(rows) == want and target in statuses:
                return rows
        time.sleep(1.0)
    return last


def read_presale_rows(port: int, targets=DEFAULT_TARGETS, wait_s: float = 6.0,
                      url: str = DEFAULT_URL, log=print) -> dict:
    """读某店「已售罄 + 即将售罄」的全部 SKU 行。

    **每个筛选各开一个临时标签页、只点一次 chip，读完就关** —— 用户自己的页一点不动。
    2026-10-12 实测：
      · 干净页点一次 chip，约 2 秒后表格就换成那批（`已售罄` 5 行 / `即将售罄` 2 行）；
      · 但两个 chip 是**单选**，同一页上点第二个会把第一个顶掉、还会出现「点了没反应」的滞后假象
        ⇒ 所以一个页只服务一个筛选，读不到就重开一页，绝不连点。
    """
    collected: dict[str, dict] = {}
    chips: list = []
    clicked: list = []
    # 开工先清障：上一次被强杀时残留的临时页（计划任务超时 0x41306 时 finally 跑不到）。
    # 只清登记表里**我们自己**建的页，用户的页永远不碰。
    try:
        from cdp_transport import sweep_leaked_scratch  # noqa: WPS433
        for _tid, _u in (sweep_leaked_scratch(port, log=log) or []):
            log("   [TEMU] 清掉上次残留的临时页：%s" % str(_u)[:60])
    except Exception as e:  # noqa: BLE001
        log("   [TEMU] [warn] 清理残留临时页失败（不阻断）：%s" % str(e)[:60])
    for t in targets:
        page, tid = _scratch_page(port, url, log=log)
        try:
            chips = page.evaluate(_CHIPS_JS) or chips
            counts = _chip_counts(chips)
            want = counts.get(t, -1)          # 只用于日志与等待判据，不作为「要不要点」的依据
            base = page.evaluate(_ROWS_JS) or []
            hit = [r for r in base if r.get("status") == t]
            # 干净页的未筛选列表里也会夹着个别该状态的行（BHY 实测：30 行里有 1 行已售罄），
            # 所以不能「有命中就不点」—— 要拿 chip 计数比：不够就点开这个筛选。
            if want <= 0 or len(hit) < want:
                if page.evaluate(_CHIP_JS, t) != "clicked":
                    log("   [TEMU] 没找到「%s」chip，跳过" % t)
                    continue
                got = _wait_rows(page, target=t, want=want, timeout=max(wait_s * 4, 30.0), log=log)
                hit = [r for r in got if r.get("status") == t]
            log("   [TEMU] 「%s」→ %d 行（chip 计数 %s）" % (t, len(hit), want))
            total = page.evaluate(_PAGER_JS)
            if isinstance(total, int) and total > len(hit) >= 0:
                log("   [TEMU] ⚠ 该筛选分页里「共有 %s 条」，只读到 %d 行 —— 可能有翻页，数会少"
                    % (total, len(hit)))
            chips = page.evaluate(_CHIPS_JS) or chips      # 页面加载后 chip 计数才准，重读一次
            if hit:
                clicked.append(t)
                for r in hit:
                    collected[r["sku_code"]] = r
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                _close_target(port, tid)
    log("   [TEMU] 临时标签页已关，合计 %d 个 SKU" % len(collected))
    return {"rows": list(collected.values()), "chips": chips, "url": url, "clicked": clicked}
