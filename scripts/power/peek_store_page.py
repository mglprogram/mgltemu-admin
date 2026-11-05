# -*- coding: utf-8 -*-
"""peek_store_page.py — **只读**看一眼紫鸟店铺窗口里的某个页面（绝不点击）。

为什么要有它（2026-11-05 用户「一步一步教我」的现场）：
    教学/排障时每一轮都要先看清"页面现在长什么样"（有哪些按钮、哪些页签、多少行、
    有没有弹窗、红字写了什么）。临时写 `_tmp_*.py` 脚本既啰嗦又容易残留，
    而且一不小心就写进 `page.click(...)` —— 教学现场最忌讳乱点。
    故把这段能力固化成**一个只读**入口：只 `evaluate` 读 DOM、只 `screenshot` 落图，
    全文件**没有**任何 click / dispatchMouseEvent / dispatchKeyEvent / insertText。

口径（与 temu-buylabel 一致）：
    · 端口一律现场探测（`ziniao_store.probe_port_by_window`），**不写死**；
    · 页面按 URL 片段挑（默认订单列表 `/mmsos/orders`），挑不到就如实报，不猜；
    · 可见性一律用 **rect 判**（`getClientRects().length && w/h && visibility`），
      不用 `offsetParent`（fixed 浮层恒为 null，会整片误滤，见 temu-buylabel 真机坑 3）。

用法：
    python scripts/power/peek_store_page.py --store BEL
    python scripts/power/peek_store_page.py --store BEL --shot bel_orders --json
    python scripts/power/peek_store_page.py --store BHY --match "/mmsos/orders" --full
    python scripts/power/peek_store_page.py --stores BEL,BHY --brief
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

_OUT_DIR = os.path.join(_HERE, "out", "_peek")

#: 读页面结构（只读）。返回按钮/页签/行数/弹窗/红字，全是**看**出来的，不带任何动作。
_JS_PEEK = r"""() => {
  const vis = e => {
    try {
      const r = e.getBoundingClientRect();
      return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
          && getComputedStyle(e).visibility !== 'hidden';
    } catch (_) { return false; }
  };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const uniq = a => [...new Set(a)];
  const body = document.body ? (document.body.innerText || '') : '';

  const buttons = uniq([...document.querySelectorAll('button')].filter(vis).map(txt)
      .filter(t => t && t.length <= 24));
  const tabs = uniq([...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
      .filter(t => t && t.length <= 24));
  const links = uniq([...document.querySelectorAll('a')].filter(vis).map(txt)
      .filter(t => t && t.length <= 16));

  // 订单行（PO-…）：订单号 / SKU / 件数 / 状态 / 仓库 / 运单号
  const rows = [];
  [...document.querySelectorAll('tr')].filter(vis).forEach(tr => {
    const t = txt(tr);
    const m = t.match(/PO-\d+-\d+/);
    if (!m) return;
    rows.push({
      ord: m[0],
      sku: (t.match(/SKU 货号[:：]\s*([A-Za-z0-9\-]+)/) || [])[1] || '',
      qty: (t.match(/[×x]\s*(\d+)/) || [])[1] || '',
      st: (t.match(/(待发货|平台处理中|已发货|已签收|已取消)/) || [])[1] || '',
      wh: (t.match(/库存扣减仓库[:：]\s*(\S+)/) || [])[1] || '',
      track: (t.match(/(1Z[0-9A-Z]{16}|[A-Z]{2,6}[0-9]{8,})/) || [])[1] || ''
    });
  });

  // 弹窗：标题 + 按钮（beast-core 的 [class*=MDL_inner]，rect 判可见）
  const modals = [];
  [...document.querySelectorAll('[class*=MDL_inner]')].filter(vis).forEach(md => {
    const inner = md.innerText || '';
    if (!inner.trim()) return;
    const title = (inner.match(/^\s*([^\n]{2,24})/) || [])[1] || '';
    const btn = [...md.querySelectorAll('button')].filter(vis).map(txt).filter(Boolean);
    modals.push({title: title.trim(), buttons: btn.slice(0, 8)});
  });

  // 红字提示（重量/差异类最多）——现场最常要看的判据
  const reds = uniq([...document.querySelectorAll('span,div,p')].filter(vis).map(txt)
      .filter(t => t && t.length <= 90
          && /差异过大|请修改|异常|不能为空|无物流|无渠道|不可用|请选择/.test(t))).slice(0, 12);

  return JSON.stringify({
    url: location.href,
    title: document.title,
    total: (body.match(/共有\s*(\d+)\s*条/) || [])[1] || '',
    page_size: (body.match(/每页\s*(\d+)\s*条/) || [])[1] || '',
    buttons: buttons.slice(0, 30),
    tabs: tabs.slice(0, 24),
    links: links.slice(0, 20),
    rows: rows.slice(0, 60),
    row_count: rows.length,
    modals: modals.slice(0, 4),
    reds: reds,
    body: body.slice(0, 4000)
  });
}"""


def _zs():
    import ziniao_store  # noqa: PLC0415
    return ziniao_store


def _stores() -> dict:
    p = os.path.join(_HERE, "data", "ziniao_power_map.json")
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for it in m.get("mapping") or []:
        for k in (it.get("export_suffix"), it.get("power_name"), it.get("ziniao_account")):
            if k:
                out[str(k)] = it
    return out


def pick_target(page_urls: list, match: str) -> dict:
    """按 URL 片段挑页（纯函数，可单测）：返回命中的 target dict，没有就 `{}`。

    **不含单号筛选参数**的优先（`parentOrderSnList=` 那种是"某批单查过"的残留页，
    读它会把上一批的结果当成当前列表）。
    """
    frag = str(match or "")
    hits = [t for t in (page_urls or []) if frag and frag in (t.get("url") or "")]
    if not hits:
        return {}
    clean = [t for t in hits if "parentOrderSnList=P" not in (t.get("url") or "")]
    return (clean or hits)[0]


def peek(store: str, match: str = "/mmsos/orders", shot: str = "",
         full: bool = False) -> dict:
    """只读看一页。返回 `{"store","port","ok","why","page"}`；`ok=False` 时 `why` 说明原因。"""
    zs = _zs()
    from cdp_transport import CdpPage  # noqa: PLC0415

    ent = _stores().get(store) or {}
    win = ent.get("ziniao_window_name") or ""
    out = {"store": store, "window": win, "port": 0, "ok": False, "why": "", "page": {}}
    if not win:
        out["why"] = "未知店（映射表里没有 %s）" % store
        return out
    port = zs.probe_port_by_window(win)
    if not port:
        out["why"] = ("窗口还在但 CDP 端口未就绪" if zs.window_running(win) else "窗口没开")
        return out
    out["port"] = int(port)
    tgt = pick_target(zs.list_page_targets(int(port)), match)
    if not tgt:
        out["why"] = "窗口里没有 URL 含「%s」的页" % match
        return out
    page = CdpPage(int(port), tgt)
    data = json.loads(page.evaluate(_JS_PEEK) or "{}")
    out["ok"] = True
    if not full:
        data.pop("body", None)
    out["page"] = data
    if shot:
        os.makedirs(_OUT_DIR, exist_ok=True)
        raw = page.screenshot()
        p = os.path.join(_OUT_DIR, "%s.png" % shot)
        with open(p, "wb") as f:
            f.write(base64.b64decode(raw) if isinstance(raw, str) else raw)
        out["shot"] = p
    return out


def _brief(r: dict) -> str:
    if not r.get("ok"):
        return "== %s（%s）：%s" % (r["store"], r.get("window") or "-", r.get("why"))
    d = r["page"]
    head = "== %s（%s, port %s） %s" % (r["store"], r.get("window"), r.get("port"), d.get("title"))
    nums = "行 %s / 共 %s 条 / 每页 %s" % (d.get("row_count"), d.get("total") or "-",
                                         d.get("page_size") or "-")
    tabs = "页签：" + ("、".join(d.get("tabs") or []) or "-")
    btns = "按钮：" + ("、".join(d.get("buttons") or []) or "-")
    lines = [head, nums, tabs, btns]
    if d.get("modals"):
        lines.append("弹窗：" + "；".join(
            "%s[%s]" % (m.get("title"), "|".join(m.get("buttons") or [])) for m in d["modals"]))
    if d.get("reds"):
        lines.append("红字：" + "；".join(d["reds"][:6]))
    if r.get("shot"):
        lines.append("图：" + r["shot"])
    return "\n".join(lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="只读看一眼紫鸟店铺窗口里的页面（不点任何东西）")
    ap.add_argument("--store", default="", help="店后缀，如 BEL / BHY")
    ap.add_argument("--stores", default="", help="多店，逗号分隔（等价于逐个 --store）")
    ap.add_argument("--match", default="/mmsos/orders", help="按 URL 片段挑页（默认订单列表）")
    ap.add_argument("--shot", default="", help="落图名（存 out/_peek/<名>.png；空＝不落图）")
    ap.add_argument("--full", action="store_true", help="带页面正文前 4000 字")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    names = [x.strip() for x in a.stores.split(",") if x.strip()] or \
            ([a.store.strip()] if a.store.strip() else [])
    if not names:
        ap.error("要给 --store 或 --stores")
    res = [peek(s, match=a.match, shot=(a.shot if len(names) == 1 else
                                       ("%s_%s" % (a.shot, s) if a.shot else "")),
                full=a.full) for s in names]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for r in res:
            print(_brief(r))
    return 0 if all(r["ok"] for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
