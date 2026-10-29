# -*- coding: utf-8 -*-
"""temu_abnormal_driver.py — 合作对接仓「出库单及异常处理」异常单处置（TEMU，BEL/BHY…）。

做什么：把 `agentseller.temu.com/lgst/auth-warehouse/abnormal-order`（页签「异常」）里的
异常出库单**逐单转为卖家履约** —— 行内「重发」→ 浮层里的「转为卖家履约」→ 二次确认「确认」。

为什么这么处置（用户 2026-09-22 口径）：
    · **一律 `转为卖家履约`**，不按异常原因分类、不挑仓库；
    · **必须逐单**（不走页面上的「批量重发」）；
    · 每单成功＝页面弹一个「操作成功」小提示（`toast_contains`）；
    · 该动作按口径不会失败，故本域不设失败分支矩阵（真出错＝抛错 → 入口 HARD STOP 42）。

对外（唯一门）：
    perform(action, params)  action ∈ open / list / convert / verify
纯函数（可离线单测）：normalize_orders / pick_page_size / diff_remaining / toast_ok。

口径：`scripts/power/data/temu_abnormal.json`（只改那里）。
顺序：`scripts/power/data/temu_abnormal_pipeline.json` 的 steps（只改那里）。
证据：`scripts/power/out/<日期>/abnormal/`。
连接：`lib/cdp_transport.py`（target-level CDP，红线 RL-013 禁 Playwright browser-level）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import cdp_transport  # noqa: E402  (power/lib 同目录单点实现)

# 步骤编排器与购面单域共用（纯编排：只依赖注入的 perform_fn/clock，无 IO）——复用不复制。
# 弹窗登记表的读写也复用同一套（load/match/classify/未知登记），本域只提供**表和判据**。
from temu_buylabel_driver import (  # noqa: E402,F401
    run_steps,
    load_popup_registry,
    match_popup,
    classify_popup,
    bump_popup_hits,
    register_unknown_popup,
    save_popup_registry,
)

ACTIONS = ("auth", "open", "list", "convert", "verify")

#: 兜底用的「隐藏控制台」flags（方法库 lib/quiet 不可用时才用；非 Windows 无意义）
_HIDDEN_FLAGS = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))


def _hidden_si():
    """`STARTUPINFO(wShowWindow=SW_HIDE)`（非 Windows 返回 None）。"""
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _spawn_hidden(cmd, **kw):
    """起子进程：一律「**隐藏控制台**」⇒ 整链不弹 CMD 黑框（用户 2026-09-22 立规）。

    为什么用 hidden 而不是 `quiet.run`（CREATE_NO_WINDOW）：本域这个子进程**自己还会派生**
    （起 `run_temu_auth.py` → 它再起 powershell/netstat）。NO_WINDOW 给的是"**没有**控制台"，
    后代的控制台归属就交给 Windows；hidden 给的是"**存在但隐藏**"的控制台，后代**继承**它
    ⇒ 全链都不会冒出可见窗口（2026-09-14 控制面正是栽在 NO_WINDOW 这条路上：用户报障「弹终端」；
    2026-09-22 本机复测**没复现**"后代新建"，故这里取**保守**口径）。
    三态实测（2026-09-22 本机探针）：`hidden_run` 子进程 hwnd≠0 且 IsWindowVisible=False；
    `quiet.run` 子进程 hwnd=0；裸 `CREATE_NEW_CONSOLE`（不带 SW_HIDE）hwnd≠0 且 **True**＝真弹框。
    """
    try:
        from lib import quiet as _quiet
    except Exception:  # noqa: BLE001
        _quiet = None
    if _quiet is not None and hasattr(_quiet, "hidden_run"):
        return _quiet.hidden_run(cmd, **kw)
    if os.name == "nt":                      # 方法库缺失/偏旧 → 自带 flags（保守），也不许裸 spawn
        return subprocess.run(cmd, startupinfo=_hidden_si(),
                              creationflags=_HIDDEN_FLAGS, **kw)
    return subprocess.run(cmd, creationflags=0, **kw)


#: 本域弹窗登记表（路径现取，别写进 json 免得 CWD 歧义）
_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          os.pardir, "data"))
DEFAULT_POPUPS_PATH = os.path.join(_DATA_DIR, "temu_abnormal_popups.json")
#: 店后缀 → 紫鸟窗口名/storeId 的映射（与入口 `run_temu_abnormal.py` 同源同一份）
STORE_MAP = os.path.join(_DATA_DIR, "ziniao_power_map.json")


def _store_entry(store: str):
    """按店后缀取该店的紫鸟窗口名/storeId（找不到返回 None）。窗口生命周期单点＝`lib/ziniao_store`。"""
    if not store:
        return None
    try:
        with open(STORE_MAP, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    for e in (m.get("mapping") or []):
        if store in (e.get("export_suffix"), e.get("power_name"), e.get("ziniao_account")):
            return e
    return None


def _port_alive(port: int, timeout: float = 3.0) -> bool:
    """该端口还是真 CDP 吗？窗口关窗重开后端口会变，旧端口连不上（别拿死端口往下跑）。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % int(port),
                                    timeout=timeout) as r:
            v = json.loads(r.read().decode("utf-8", "replace"))
        return bool(v.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 纯函数（可离线单测）
def normalize_orders(orders) -> list[str]:
    """订单号列表归一化：去空、去重、保序。"""
    out, seen = [], set()
    for o in orders or []:
        s = str(o or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def pick_page_size(options, want: int) -> int:
    """取 ≤ want 的最大可选页大小；都超了取最小；空列表返回 0。"""
    vals = sorted(int(x) for x in (options or []) if str(x).strip().isdigit())
    if not vals:
        return 0
    ok = [v for v in vals if v <= int(want)]
    return max(ok) if ok else min(vals)


def diff_remaining(targets, rows) -> dict:
    """复核判据：targets（原清单）− rows（现存异常行）→ 已消失 / 仍异常。"""
    tgt = normalize_orders(targets)
    now = {str(r.get("po") or "").strip() for r in (rows or [])}
    gone = [p for p in tgt if p not in now]
    left = [p for p in tgt if p in now]
    return {"targets": tgt, "remaining": left, "gone": gone,
            "ok": bool(tgt) and not left}


def toast_ok(texts, needle: str) -> bool:
    """成功判据（内容断言）：页面上出现了含 `needle` 的提示文本。"""
    n = str(needle or "").strip()
    if not n:
        return False
    return any(n in str(t or "") for t in (texts or []))


# ---------------------------------------------------------------- 页面 JS（选择器集中在此）
_JS_ROWS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const t = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const out = [];
  Array.from(document.querySelectorAll('tbody tr')).forEach(tr => {
    if (!vis(tr)) return;
    const td = Array.from(tr.querySelectorAll('td'));
    if (td.length < 7) return;
    const c = td.map(x => t(x));
    out.push({po: c[2] || '', batch: c[3] || '', order_type: c[4] || '',
              reason: c[5] || '', guide: c[6] || '', qty: c[11] || '',
              sku: c[12] || '', cells: c});
  });
  return out;
}"""

_JS_CLICK_TAB = r"""(name) => {
  const vis = e => e && e.offsetParent !== null;
  const cand = Array.from(document.querySelectorAll('div, span, a, button')).filter(vis)
      .filter(e => {
        const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
        return t === name || new RegExp('^' + name + '\\s*\\d*$').test(t);
      });
  if (!cand.length) return 'NOT_FOUND';
  const el = cand[cand.length - 1];
  (el.tagName === 'A' || el.tagName === 'BUTTON' ? el : el).click();
  return 'CLICKED';
}"""

_JS_OPEN_PAGE_SIZE = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const t = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  // A) 直接是「N 条/页」这种文本盒
  let sel = Array.from(document.querySelectorAll('div, span, button, input')).filter(vis)
      .filter(e => /^\d+\s*条\s*\/?\s*页$/.test(t(e)));
  if (sel.length) { sel[sel.length - 1].click(); return 'OPENED_A'; }
  // ★ C) 2026-10-28 补：BEL 异常页的 size changer 是「每页 [10 ▾]」这种 select，
  //   文案里**没有数字**（innerText = 「每页 条」）⇒ A/B 两条都命中不了，
  //   今天因此返回 NO_SELECTOR、每页停在 10 条，77 条只处理了 10 条。
  //   这里直接点 `[class*=sizeChanger]` 里的 select header。
  const sc = Array.from(document.querySelectorAll('[class*=sizeChanger]')).filter(vis)[0];
  if (sc) {
    const h = sc.querySelector('[data-testid=beast-core-select-header]')
           || sc.querySelector('[class*=ST_head]') || sc.querySelector('[class*=head]')
           || sc.querySelector('input');
    if (h) { h.click(); return 'OPENED_C'; }
  }
  // B) 找不到就定位「每页」标签，再从其上方容器里找显示数字的可点盒子
  const labels = Array.from(document.querySelectorAll('div, span, label')).filter(vis)
      .filter(e => /每页/.test(t(e)) && t(e).length < 16);
  if (!labels.length) return 'NO_SELECTOR';
  let host = labels[labels.length - 1];
  for (let k = 0; k < 3 && host.parentElement; k++) host = host.parentElement;
  const cand = Array.from(host.querySelectorAll('input, div, span, button')).filter(vis)
      .filter(e => e.tagName === 'INPUT' || /^\d+$/.test(t(e)));
  if (!cand.length) return 'NO_BOX';
  const box = cand[cand.length - 1];
  (box.tagName === 'INPUT' ? (box.closest('div, span') || box) : box).click();
  return 'OPENED_B';
}"""

#: 分页总数（`共有 N 条`）。**必须读它**：只看当前页行数会把「77 条里的 10 条」
#: 当成全量（2026-10-28 实测：任务报了「仍异常 0」，其实还剩 67 条）。
_JS_TOTAL = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const t = e => ((e && e.innerText) || '').replace(/\s+/g, ' ').trim();
  const nodes = Array.from(document.querySelectorAll('[class*=totalText],[class*=TotalText],[class*=pgtTotal]'))
      .filter(vis);
  for (const n of nodes) {
    const m = t(n).match(/共\s*有?\s*([0-9,]+)\s*[条个笔]/);
    if (m) return parseInt(m[1].replace(/,/g, ''), 10);
  }
  const body = document.body ? document.body.innerText : '';
  const m = body.match(/共\s*有?\s*([0-9,]+)\s*[条个笔]/);
  return m ? parseInt(m[1].replace(/,/g, ''), 10) : -1;
}"""

#: 点分页页码（1 开始）。返回 CLICKED / SAME / NO_PAGER / NOT_FOUND。
_JS_CLICK_PAGE = r"""(n) => {
  const vis = e => e && e.offsetParent !== null;
  const t = e => ((e && e.innerText) || '').replace(/\s+/g, ' ').trim();
  const want = String(n);
  const host = Array.from(document.querySelectorAll('[class*=pagination],[class*=Pagination],[class*=PGT]'))
      .filter(vis)[0];
  if (!host) return 'NO_PAGER';
  const cand = Array.from(host.querySelectorAll('li, button, a, span, div')).filter(vis)
      .filter(e => t(e) === want && e.children.length === 0);
  if (!cand.length) return 'NOT_FOUND';
  const el = cand[cand.length - 1];
  const cls = (el.className || '').toString();
  if (/active|ACTIVE|current/i.test(cls)) return 'SAME';
  el.click();
  return 'CLICKED';
}"""

_JS_PICK_OPTION = r"""(size) => {
  const vis = e => e && e.offsetParent !== null;
  const want = String(size);
  const cand = Array.from(document.querySelectorAll('li, div, span')).filter(vis)
      .filter(e => (e.innerText || '').trim() === want && e.children.length === 0);
  if (!cand.length) return 'NO_OPTION';
  cand[cand.length - 1].click();
  return 'CLICKED';
}"""

_JS_CLICK_REPUSH = r"""(po) => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const rows = Array.from(document.querySelectorAll('tbody tr')).filter(vis);
  for (const tr of rows) {
    const td = tr.querySelectorAll('td');
    if (td.length < 3) continue;
    if ((td[2].innerText || '').trim() !== po) continue;
    const a = Array.from(tr.querySelectorAll('a, button, span')).filter(vis)
        .find(e => (e.innerText || '').trim() === '重发');
    if (!a) return 'NO_BTN';
    try { tr.scrollIntoView({block: 'center'}); } catch (_) {}   // 行在视口外时浮层可能定位不到
    (a.tagName === 'SPAN' ? (a.closest('a,button') || a.parentElement) : a).click();
    return 'CLICKED';
  }
  return 'NO_ROW';
}"""

_JS_CLICK_IN_POPOVER = r"""(args) => {
  // 可见性用矩形判（portal/浮层常是 fixed/absolute，offsetParent 为 null，不能用它判）
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cls = e => (e && typeof e.className === 'string' ? e.className : '');
  const isPop = e => /popover/i.test(cls(e)) && !/popoverTitle|popoverContent/i.test(cls(e));
  const popText = e => {                       // 沿祖先链走到**最外层**浮层容器，取它的文本
    let n = e, last = null;                    // （标题在外层容器里、按钮在内层 footer 里，必须取最外层）
    while (n) {
      if (isPop(n)) last = n;
      n = n.parentElement;
    }
    return last ? (last.innerText || '').replace(/\s+/g, ' ') : '';
  };
  const t = e => (e.innerText || '').trim();
  let cands = Array.from(document.querySelectorAll('a, button, span'))
      .filter(e => vis(e) && t(e) === args.text);
  if (!cands.length) return 'NOT_FOUND';
  if (args.within && args.within.length) {
    const hit = cands.filter(e => { const pt = popText(e); return args.within.some(w => pt.includes(w)); });
    if (hit.length) {
      cands = hit;
    } else if (args.fallback_self) {
      // 备选文案都没命中：兜底按「目标链接自己所在的浮层」定位（自校验，不依赖标题）
      const h2 = cands.filter(e => popText(e).includes(args.text));
      if (!h2.length) return 'NO_POPOVER';
      cands = h2;
    } else {
      return 'NO_POPOVER';
    }
  }
  const el = cands[cands.length - 1];
  (el.tagName === 'SPAN' ? (el.closest('a,button') || el.parentElement) : el).click();
  return 'CLICKED';
}"""

_JS_HAS_POPOVER = r"""() => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cls = e => (e && typeof e.className === 'string' ? e.className : '');
  const isPop = e => /popover/i.test(cls(e)) && !/popoverTitle|popoverContent/i.test(cls(e));
  let n = 0;
  Array.from(document.querySelectorAll('div')).forEach(d => {
    if (!isPop(d) || !vis(d)) return;
    if (isPop(d.parentElement) && vis(d.parentElement)) return;   // 只数最外层
    n++;
  });
  return n;
}"""

#: 列出页面上**可见的最外层浮层**（弹窗/通知/抽屉），供登记表判据识别
_JS_LAYERS = r"""() => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cls = e => (e && typeof e.className === 'string' ? e.className : '');
  const isPop = e => /popover|modal|dialog|MDL_|drawer/i.test(cls(e));
  const out = [];
  Array.from(document.querySelectorAll('div')).forEach(d => {
    if (!isPop(d) || !vis(d)) return;
    if (isPop(d.parentElement) && vis(d.parentElement)) return;      // 只取最外层
    const t = (d.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 2000) return;
    const btns = Array.from(d.querySelectorAll('button, a, span'))
        .map(x => (x.innerText || '').trim()).filter(Boolean);
    const r = d.getBoundingClientRect();
    out.push({text: t.slice(0, 400), buttons: Array.from(new Set(btns)).slice(0, 20),
              has_close: !!d.querySelector('[data-testid="beast-core-icon-close"], [class*=iconClose], [class*=icon-close]'),
              rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
  });
  return out;
}"""

#: 关掉某个浮层：icon＝点专用关闭图标；text＝点 close_texts 里的文案键
_JS_CLOSE_LAYER = r"""(args) => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cls = e => (e && typeof e.className === 'string' ? e.className : '');
  const isPop = e => /popover|modal|dialog|MDL_|drawer/i.test(cls(e));
  const layers = Array.from(document.querySelectorAll('div')).filter(d => isPop(d) && vis(d)
      && !(isPop(d.parentElement) && vis(d.parentElement)));
  if (!layers.length) return 'NO_LAYER';
  let L = null;
  const anchor = String(args.anchor || '');
  if (anchor) L = layers.find(d => (d.innerText || '').replace(/\s+/g, ' ').includes(anchor));
  if (!L) L = layers[layers.length - 1];
  if (args.mode === 'icon') {
    const ic = L.querySelector('[data-testid="beast-core-icon-close"], [class*=iconClose], [class*=icon-close]');
    if (!ic) return 'NO_ICON';
    ic.click();
    return 'CLICKED';
  }
  const texts = args.texts || ['取消'];
  const cand = Array.from(L.querySelectorAll('button, a, span')).filter(vis)
      .filter(e => texts.indexOf((e.innerText || '').trim()) >= 0);
  if (!cand.length) return 'NO_TEXT';
  const el = cand[cand.length - 1];
  (el.tagName === 'SPAN' ? (el.closest('a,button') || el.parentElement) : el).click();
  return 'CLICKED';
}"""

_JS_TOASTS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const out = [];
  document.querySelectorAll('div, span, p').forEach(e => {
    const c = (typeof e.className === 'string' ? e.className : '');
    if (!/toast|Toast|notification|Notification|message-wrapper|Message_/i.test(c)) return;
    if (!vis(e)) return;
    const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 200) out.push(t);
  });
  return Array.from(new Set(out));
}"""

_JS_IS_AUTH = r"""() => /auth\/authentication|settle\/seller-login/.test(location.href)"""

_JS_AUTH_KIND = r"""(key) => {
  const u = location.href.toLowerCase();
  if (u.includes('settle/seller-login') || (u.includes('settle') && u.includes('seller-login'))) return 'seller-login';
  if (u.includes('authentication')) return 'authentication';
  if (u.includes('/login')) return 'login';
  if (u.includes(String(key).toLowerCase())) return 'target';
  return 'other';
}"""

_JS_CLICK_AUTH_GOTO = r"""(text) => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cls = e => (e && typeof e.className === 'string' ? e.className : '');
  const t = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  // 首选 class（authentication_goto）——文本可能在导航里重复出现
  let cand = Array.from(document.querySelectorAll('div, a, button')).filter(vis)
      .filter(e => /authentication_goto/.test(cls(e)));
  if (!cand.length) {
    cand = Array.from(document.querySelectorAll('div, a, button, span')).filter(vis)
        .filter(e => t(e) === text);
  }
  if (!cand.length) return 'NOT_FOUND';
  const el = cand[cand.length - 1];
  (el.tagName === 'SPAN' ? (el.closest('a,button,div') || el.parentElement) : el).click();
  return 'CLICKED';
}"""

_JS_CHECK_TERMS = r"""() => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const boxes = Array.from(document.querySelectorAll('input[type=checkbox]'));
  for (const box of boxes) {                    // input 尺寸常为 0，点它点不到 → 向上找 LABEL
    let n = box.parentElement;
    for (let k = 0; k < 6 && n; k++) {
      if (n.tagName === 'LABEL' && vis(n)) { n.click(); return 'CLICKED_LABEL'; }
      n = n.parentElement;
    }
  }
  for (const box of boxes) {                    // 兜底：向上找可见的 outerWrapper/CBX 容器
    let n = box;
    for (let k = 0; k < 5 && n; k++) {
      const c = (n && typeof n.className === 'string') ? n.className : '';
      if (/outerWrapper|CBX_|outer/i.test(c) && vis(n)) { n.click(); return 'CLICKED_WRAPPER'; }
      n = n.parentElement;
    }
  }
  return 'NOT_FOUND';
}"""

_JS_CLICK_BY_TEXTS = r"""(texts) => {
  const vis = e => { if (!e || !e.getBoundingClientRect) return false;
                     const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const t = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const cand = Array.from(document.querySelectorAll('button, a, div, span')).filter(vis);
  for (const want of texts) {
    const hit = cand.filter(e => t(e) === want);
    if (hit.length) {
      const el = hit[hit.length - 1];
      (el.tagName === 'SPAN' ? (el.closest('a,button,div') || el.parentElement) : el).click();
      return 'CLICKED:' + want;
    }
  }
  // 正则兜底（照 auth_temu_driver 的 _AUTH_CONFIRM_RE）
  const re = /^授权的?(登录|并前往)|^确认授权|授权登录/;
  const hit2 = cand.filter(e => re.test(t(e)));
  if (hit2.length) {
    const el = hit2[0];
    (el.tagName === 'SPAN' ? (el.closest('a,button,div') || el.parentElement) : el).click();
    return 'CLICKED_RE';
  }
  return 'NOT_FOUND';
}"""

_JS_TITLE = r"""() => document.title"""


# ---------------------------------------------------------------- 驱动
class TemuAbnormalDriver:
    """合作仓异常出库单页的门面（只做这一页的读写，不碰下单链）。"""

    def __init__(self, cfg: dict, log=print, evidence_dir: str | None = None,
                 confirm: bool = False, store: str = ""):
        self.cfg = cfg or {}
        self.log = log
        self.ev = evidence_dir
        self.confirm = bool(confirm)
        self.store = str(store or "")
        self.browser = None
        self._port = 0
        #: 本次运行**自己开**的 targetId；close() 只关这些，绝不动用户的标签页。
        #: 2026-10-27 用户口径「每执行一次定时任务，打开的新页面任务完成自己关闭」。
        self._opened_targets = []

    # ---- 基础设施
    def connect(self, port: int):
        self._port = int(port)
        self.browser = cdp_transport.connect(self._port)
        # 记下已存在的标签页：close() 只关本次新开的、商家中心域的那些
        # （用户口径 2026-10-27：用完即关，不能动我正在使用的页面）。
        try:
            self._baseline_targets, self._baseline_urls = \
                cdp_transport.snapshot_tabs(self._port)
        except Exception:  # noqa: BLE001
            self._baseline_targets, self._baseline_urls = set(), set()
        return self

    def close(self):
        """收尾：关掉本次新开的商家中心标签页（复用的不关），再断开。

        以前这里是 `return None` —— 什么都没做，异常处理页留在了紫鸟窗口里。
        `_find()` 会复用已开着的页，所以只有**新开**的才记进 `_opened_targets`；
        另有一层 `close_new_seller_pages` 收"站点自己弹出来的页"。

        硬约束：只关本次新开的 + 商家中心域；用户的 `www.temu.com` / 外部站
        / 扩展页一律不碰；窗口里至少留 1 个真实页。关页失败不阻断。

        ⚠ 2026-10-28：`_opened_targets` 先留底再关，作为第二层的归属证据
          （站点弹出的页 openerId 指向这些 id）。见 `close_new_seller_pages`。
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
        self._baseline_targets = set()
        self._baseline_urls = set()
        self.browser = None

    def _pages(self):
        return cdp_transport.list_pages(self._port)

    def _own_targets(self) -> set:
        """本次运行**自己开**的 targetId 集合（`close()` 就是按它收尾的）。"""
        return {t for t in (getattr(self, "_opened_targets", []) or []) if t}

    def _page_by_tid(self, tid: str):
        """按 targetId 取页对象（不靠 URL 猜，避免命中用户那张同址页）。"""
        for t in self._pages():
            if (t.get("id") or t.get("targetId")) == tid:
                return cdp_transport.CdpPage(self._port, {
                    "targetId": t.get("id"),
                    "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
                    "url": t.get("url")})
        return None

    def _find(self):
        """定位**我们自己开的**本域页；没有返回 None。

        ★ 2026-10-28 用户口径「自己新开页，跑完即关」：
          旧实现是「窗口里有本域页就用」，命中的是**用户自己开着的异常处理页**；
          `mark_foreign` 只挡 `goto`，挡不住「切页签 / 改每页条数 / 转卖家履约」这些动作 ——
          等于把用户的页面当工作区改了一遍（与 2026-10-28 订单页那次同一类问题）。
          现在只认 `_opened_targets` 里的页，要干活就自己开，跑完由 `close()` 收掉。
        """
        key = self.cfg.get("url_key", "abnormal-order")
        mine = self._own_targets()
        for t in self._pages():
            tid = t.get("id") or t.get("targetId")
            if tid not in mine:
                continue                      # 不是本次开的 → 用户的页，连碰都不碰
            if key in (t.get("url") or ""):
                return cdp_transport.CdpPage(self._port, {
                    "targetId": t.get("id"),
                    "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
                    "url": t.get("url")})
        return None

    def _open_own_page(self, wait_key: str = "after_goto", wait: float = 7.0):
        """**自己开**一个本域页并等它加载完；返回页对象（记录 targetId，`close()` 会关）。

        与购面单域 `new_tab_background` 同一口径：走原生 CDP `Target.createTarget`
        （`background=true`，不抢前台），按新 targetId 认页。
        """
        tid = cdp_transport.create_target(self._port, self.cfg["url"], background=True)
        if not tid:
            raise RuntimeError("打开异常处理页失败（createTarget 没返回 targetId）")
        self._opened_targets.append(tid)
        self.wait(wait_key, wait)
        deadline = time.time() + 20.0
        while True:
            page = self._page_by_tid(tid)
            if page is not None:
                return page
            if time.time() >= deadline:
                raise RuntimeError("打开异常处理页失败（target 未出现：%s）" % tid)
            time.sleep(0.5)

    def _js(self, page, script, arg=None):
        return page.evaluate(script) if arg is None else page.evaluate(script, arg)

    def shot(self, page, name: str, force: bool = False):
        """截图存证。**默认只在失败路径上拍**（`evidence.screenshot: fail_only`）。

        为什么（2026-09-22 实测）：这张页上一次 `Page.captureScreenshot` **稳定要 45 秒**
        （返回 280KB，不是超时；同页普通 JS 调用 0.0s）——整链有 5 处截图 ⇒ 白白多花约 225 秒，
        而真正的证据是那几个 JSON 落盘件。所以正常路径不拍；失败/异常路径 `force=True` 才拍，
        且给硬预算（`evidence.screenshot_timeout_sec`），拍不到也不拖慢（只记一行日志）。
        """
        if not self.ev:
            return None
        mode = str((self.cfg.get("evidence") or {}).get("screenshot", "fail_only"))
        if mode == "off" or (mode == "fail_only" and not force):
            return None
        budget = float((self.cfg.get("evidence") or {}).get("screenshot_timeout_sec", 20))
        try:
            os.makedirs(self.ev, exist_ok=True)
            fp = os.path.join(self.ev, name + ".png")
            with open(fp, "wb") as f:
                f.write(page.screenshot(timeout=budget))
            return fp
        except Exception as e:  # noqa: BLE001
            why = str(e)[:80] or type(e).__name__      # `str(TimeoutError())` 是空的，别让日志变成「截图失败 : 」
            self.log("[证据] 截图失败 %s: %s" % (name, why))
            return None

    def save(self, obj, name: str):
        if not self.ev:
            return None
        try:
            os.makedirs(self.ev, exist_ok=True)
            fp = os.path.join(self.ev, name + ".json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            self.log("[落盘] " + fp)
            return fp
        except Exception as e:  # noqa: BLE001
            self.log("[落盘] 失败 %s: %s" % (name, str(e)[:80]))
            return None

    def wait(self, key: str, default: float = 1.0):
        time.sleep(float((self.cfg.get("waits") or {}).get(key, default)))

    # ---- 页面定位（单步动作自愈：页面没开就自己开）
    def _page_or_open(self):
        """取本域页面；**没开就自己 open 一遍**。

        为什么必须自愈（2026-09-22 实测）：MCP 作业一次只跑**一个** action
        （如 `--action list`），没有前一步 `open` 帮它开页 ⇒ 单步技能全部 42。
        """
        page = getattr(self, "_page", None) or self._find()
        if page is None:
            self.log("[页] 异常处理页没开，自动先 open 一遍")
            self.open_page()
            page = getattr(self, "_page", None)
        if page is None:
            raise RuntimeError("异常处理页打不开（看 agentseller.temu.com 是否已授权）")
        return page

    # ---- ⓪ auth：把该店窗口的合作仓侧授权态过掉（整链第一段）
    def _call_existing_auth(self) -> str:
        """**先走现有登录入口**（`run_temu_auth.py --store <店>`），再补合作仓那一层。

        用户 2026-09-22 口径：「就使用现在正在使用的登录方式就行，完全一致」
        ⇒ 登录这段**不另写一套**，直接调那个已经跑熟的入口；本域只负责它管不到的合作仓侧
        （`agentseller.temu.com` 的 auth/authentication → 商家中心 → 勾条款 → 确认授权并前往）。
        """
        if not self.store:
            return "SKIP(没给 --store)"
        entry = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), os.pardir, "run_temu_auth.py"))
        cmd = [sys.executable, entry, "--store", self.store]
        try:
            # 子进程（run_temu_auth.py）自己还会派生 powershell/netstat ⇒ 必须**隐藏控制台**
            # 起（`quiet.run` 的 NO_WINDOW 会让孙子进程新建可见控制台＝黑框）。
            r = _spawn_hidden(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
            out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            tail = out[-1] if out else ""
            return "%s (rc=%s)" % (tail[:140], getattr(r, "returncode", "?"))
        except Exception as e:  # noqa: BLE001
            return "ERR %s" % str(e)[:120]

    # ---- ⓪-1 窗口 / 清障 / 取活页（仿面单域 `temu_buylabel_driver` 的 ⓪ auth 同套做法，2026-09-25 移植）
    def _clean_tabs(self, stage: str) -> None:
        """清障：关掉入口页/空白授权页（单点实现＝`lib/ziniao_store.clean_dead_tabs`，保留最后一个真实页）。"""
        try:
            import ziniao_store as _zs
            cl = _zs.clean_dead_tabs(port=self._port, log=self.log)
            self.log("[授权] %s清障：真实标签页 %s；关掉 %d 个；端口存活=%s"
                     % (stage, cl.get("real_tabs"), len(cl.get("closed") or []),
                        cl.get("window_alive")))
        except Exception as e:  # noqa: BLE001
            self.log("[授权] %s清障跳过（%s）" % (stage, str(e)[:60]))

    def _light_heal(self) -> None:
        """轻量自愈：只关「无响应页」+ 入口/空白页，**不关窗、不动用户的页**。

        关窗重开是破坏性的（会把用户自己打开的标签页全杀掉），只能在窗口进程确实不在时用；
        窗口还在时先把"会拖死 attach"的那类页清掉，通常就够了。
        """
        try:
            import ziniao_store as _zs
            # 端口可能已经变了（例如登录子进程按规则重开过窗口）⇒ 现场重探，别拿死端口清障
            if self.store:
                _win = (_store_entry(self.store) or {}).get("ziniao_window_name") or ""
                _p = _zs.probe_port_by_window(_win) if _win else 0
                if _p and _p != self._port:
                    self.log("[自愈] 端口变了 %s → %s（窗口被重开过），改用新端口" % (self._port, _p))
                    self._port = int(_p)
            r = _zs.close_hung_tabs(self._port, log=self.log)
            if r.get("closed"):
                self.log("[自愈] 关掉无响应页 %d 个（不碰其它页）" % len(r["closed"]))
        except Exception as e:  # noqa: BLE001
            self.log("[自愈] 清无响应页跳过（%s）" % str(e)[:60])
        self._clean_tabs("自愈")
        self._page = None                       # 旧 page 可能指着已死 target，下一步重新取活页

    def _restart_store_window(self) -> int:
        """关窗 → 开窗 → 重探端口 → 清障；返回新端口（0＝失败）。

        ★ 2026-10-29 用户事故后加硬闸（「触发定时任务去两个店铺开新标签页，把我之前开的页面也杀掉」）：
          **窗口进程还活着就绝不关窗**。旧实现只要授权没点过就 close→open，用户开着的商品页/订单页
          随之全没（当天 BEL 被连续重开 6 次，窗口只剩一张登录页）。
          只有窗口确实不在（进程都没了，已经无可损失）时，重开才是无损的。
        """
        e = _store_entry(self.store)
        try:
            import ziniao_store as _zs
        except Exception:  # noqa: BLE001
            return 0
        if not e:
            return 0
        win, sid = e.get("ziniao_window_name") or "", e.get("ziniao_window_store_id")
        if _zs.window_running(win):
            self.log("[授权] 窗口 %s 还在 → **不关窗重开**（那会杀掉你打开的页面）；改为轻量自愈" % win)
            return 0
        self.log("[授权] 窗口不在 → 关窗 → 重开 → 重探端口（已无可损失的页面）")
        _zs.close_store(sid)
        time.sleep(3)
        _zs.open_store(sid)
        deadline, port = time.time() + 90, 0
        while time.time() < deadline:
            time.sleep(3)
            port = _zs.probe_port_by_window(win)
            if port:
                break
        if port:
            self._port = int(port)
            self.log("[授权] 重开后新端口=%s" % port)
            self._clean_tabs("重开后")
        return int(port or 0)

    def _page_live(self):
        """取一个**活着**的本域页；没有就重开一个（绝不复用已死 target）。

        为什么必须这样（2026-09-25 实测把整链打断在这里）：调 `run_temu_auth.py` 补登录时，
        它第一步就是清障，会把本域刚建的、还空着/正跳授权的标签页当"空白授权页"关掉；
        旧 page 对象还带着已死的 targetId 去连 `/devtools/page/<死id>`，
        Chrome 一律回 **HTTP 500**（`server rejected WebSocket connection: HTTP 500`）。
        """
        page = self._find()
        if page is None:
            page = self._open_own_page()
        return page

    def _ensure_auth_once(self) -> dict:
        """走一遍授权（区域页点「商家中心」→ 勾条款 → 点确认）；成功＝落在合作仓目标页。"""
        a = self.cfg.get("auth") or {}
        rounds = int(a.get("max_rounds") or 4)
        key = self.cfg.get("url_key", "abnormal-order")

        def kind_of(page):
            """读授权态；页被关掉/连接失效 → 换一个活页再读，返回 `(page, kind)`。"""
            try:
                return page, self._js(page, _JS_AUTH_KIND, key)
            except Exception:  # noqa: BLE001
                fresh = self._page_live()
                if fresh is None:
                    raise
                return fresh, self._js(fresh, _JS_AUTH_KIND, key)

        page = self._page_live()
        if page is None:
            raise RuntimeError("开不出合作仓页，无法判断授权态")
        # ★ 先判、再决定要不要跑登录（2026-09-22 B 机实测）：原来**无条件**先跑
        #   `run_temu_auth.py`（≈60s），跑完才发现"已登录、无需授权"。已在目标页就直接跳过。
        try:
            page, kind0 = kind_of(page)
            if kind0 == "target":
                self.log("[授权] 已在合作仓目标页 → 跳过登录子进程（省 ~60s）")
                self.shot(page, "00_after_auth")
                return {"ok": True, "rounds": 0, "kind": "target", "url": str(page.url),
                        "skipped_login": True}
        except Exception:  # noqa: BLE001
            pass
        if a.get("use_existing_login", True) and self.store:
            self.log("[授权] 不在目标页 → 先走现有登录入口 run_temu_auth.py --store %s" % self.store)
            self.log("[授权] 现有登录入口结果：%s" % self._call_existing_auth())
            # ★ 那个子进程自带清障，会把本域刚建的页当"空白授权页"关掉 ⇒ 之后必须重新取活页
            page = self._page_live() or page
        kind = "?"
        for rnd in range(1, rounds + 1):
            page, kind = kind_of(page)
            self.log("[授权] 第 %d/%d 轮：状态=%s url=%s" % (rnd, rounds, kind, str(page.url)[:90]))
            if kind == "target":
                self.log("[授权] 已过授权（落在合作仓目标页）")
                self.shot(page, "00_after_auth")
                return {"ok": True, "rounds": rnd, "kind": kind, "url": str(page.url)}
            if kind in ("login", "other"):
                # ★ 2026-10-27：复用页不许 goto（会覆盖用户的标签）。
                #   改为导航到订单页触发登录校验 —— 但必须确保是在**自己的页**上做。
                try:
                    if page.is_foreign():
                        page, _ = page.open_own_tab(self.cfg["url"])
                    else:
                        page.goto(self.cfg["url"])
                except Exception:  # noqa: BLE001
                    pass
                self.wait("after_goto", 7.0)
                page = self._page_live() or page
                continue
            if kind == "authentication":
                r = self._js(page, _JS_CLICK_AUTH_GOTO, a.get("region_goto_text", "商家中心"))
                self.log("[授权] 点「%s」→ %s" % (a.get("region_goto_text", "商家中心"), r))
                time.sleep(float(a.get("wait_after_goto", 3.5)))
                try:                       # 可能弹「确认切换吗」
                    self._js(page, _JS_CLICK_BY_TEXTS,
                             a.get("switch_confirm_texts") or ["确认切换", "确认"])
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1.0)
                page = self._page_live() or page
                continue
            if kind == "seller-login":
                r1 = self._js(page, _JS_CHECK_TERMS)
                r2 = self._js(page, _JS_CLICK_BY_TEXTS, a.get("confirm_texts") or ["授权登录"])
                self.log("[授权] 勾条款=%s 点确认=%s" % (r1, r2))
                time.sleep(float(a.get("wait_after_confirm", 4.0)))
                page = self._page_live() or page
                try:                       # 确认后若落到别处，拉回目标页再判
                    if self._js(page, _JS_AUTH_KIND, key) not in ("target", "authentication", "seller-login"):
                        # ★ 复用页不许 goto：开自己的页再导航
                        if page.is_foreign():
                            page, _ = page.open_own_tab(self.cfg["url"])
                        else:
                            page.goto(self.cfg["url"])
                        self.wait("after_goto", 7.0)
                        page = self._page_live() or page
                except Exception:  # noqa: BLE001
                    pass
                continue
        raise RuntimeError("授权未完成（%d 轮后状态=%s，url=%s）" % (rounds, kind, str(page.url)[:90]))

    def ensure_auth(self) -> dict:
        """整链第一段：**登录前清障 → 正常点击登录 →（失败时）轻量自愈再点一次 → 只有窗口真的不在才重开**。

        仿照面单域的 ⓪ auth（`temu_buylabel_driver` 的 `action == "auth"` 分支），三件事对齐：
          ① **登录前清障**：先把入口页/空页收掉（单点实现＝`lib/ziniao_store.clean_dead_tabs`），
             免得后面 `run_temu_auth.py` 自带的清障把本域刚建的页当"空白授权页"一起关掉；
          ② **页没了就重建**：每一步都重新取活页（`_page_live`），绝不复用已死 target——
             旧 page 对象连 `/devtools/page/<死id>` 会被 Chrome 回 HTTP 500（2026-09-25 实测断链处）；
          ③ **≤1 次自愈**：失败先「清无响应页 + 入口/空页 → 原窗口再点一次」。
             ★ 2026-10-29 改：**绝不为了重试而关窗**。旧实现是无条件「关窗 → 重开 → 再点」，
             而关窗＝把用户自己打开的标签页全杀掉（当天 BEL 被连开 6 次的事故）。
             现在只有 `window_running()` 说窗口进程不在了，才允许重开（那时已无可损失）。
        """
        self._clean_tabs("登录前")
        try:
            return self._ensure_auth_once()
        except Exception as first:  # noqa: BLE001
            self.log("[授权] 第 1 次未过：%s" % str(first)[:160])
            self._light_heal()
            try:
                return self._ensure_auth_once()
            except Exception as second:  # noqa: BLE001
                self.log("[授权] 轻量自愈后再点仍未过：%s" % str(second)[:160])
                if not self.store or not self._restart_store_window():
                    raise RuntimeError(
                        "授权未完成（%s）：%s；按规则**没有关窗重开**"
                        "（关窗会杀掉你打开的页面）。请人工看一眼窗口里的授权页。"
                        % (self.store or "?", str(second)[:160]))
                self.log("[授权] 窗口本来就不在 → 重开后再点一次")
                return self._ensure_auth_once()

    def _apply_list_view(self, page) -> int:
        """回到「异常」页签 + 把每页条数拉大；返回列表行数。**刷新后必须重做这两步**。

        ★ 2026-10-28：每页条数设不上时**必须报警**。旧代码在 `NO_SELECTOR` 时只打一行
          「每页条数 → 30（open=NO_SELECTOR pick=SKIP）」就继续，于是 77 条的清单
          只处理了第 1 页的 10 条（还报成功）。现在失败就明说是"截断风险"。
        """
        tab = self.cfg.get("tab_abnormal", "异常")
        self.log("[页] 切页签：%s → %s" % (tab, self._js(page, _JS_CLICK_TAB, tab)))
        self.wait("after_tab", 2.0)
        want = int(self.cfg.get("page_size") or 0)
        size = pick_page_size(self.cfg.get("page_size_options") or [], want)
        if size:
            r1 = self._js(page, _JS_OPEN_PAGE_SIZE)
            r2 = self._js(page, _JS_PICK_OPTION, size) if r1.startswith("OPENED") else "SKIP"
            ok = str(r2) == "CLICKED"
            self.log("[页] 每页条数 → %s（open=%s pick=%s）%s"
                     % (size, r1, r2, "" if ok else "  ⚠ 没设上，本页可能被截断"))
            if not ok:
                self.log("[页] ⚠ 每页条数设置失败 → 列表只能按页面默认值分页；"
                         "读取会尽量翻页，但处理量可能受限（先看[②]的 total/truncated）")
            self.wait("after_tab", 2.0)
        rows = self._js(page, _JS_ROWS) or []
        total = self.list_total(page)
        if total > 0 and len(rows) < total:
            self.log("[页] 列表本页 %d 行 / 共 %d 条（每页 %s）→ 需要翻页"
                     % (len(rows), total, size or "?"))
        return len(rows)

    def _refresh_page(self) -> dict:
        """**刷新页面** —— 遇到 TEMU 弹窗的统一处置（用户 2026-09-22 规则：「下次遇到 TEMU 的弹窗就刷新」）。

        为什么刷新比"找关闭键"稳：TEMU 弹窗的关闭键时有时无、还常在视口外（历史坑：✕ 在 x=1157、
        视口只有 1074）；整页重载一定能把遮挡层清掉，列表数据也从服务端重取，不丢业务状态。
        代价＝回到异常页签 + 每页条数要重设，故刷新后自动重做这两步。
        """
        page = getattr(self, "_page", None) or self._find()
        if page is None:
            return {"refreshed": False}
        try:
            # ★ 2026-10-27：刷新/重导航**只在自己的页上做**。复用页（用户的）
            #   不许 goto —— 那会把用户正在看的异常页重置掉。
            if page.is_foreign():
                page, _ = page.open_own_tab(self.cfg["url"])
            else:
                page.goto(self.cfg["url"])
        except Exception:  # noqa: BLE001
            pass
        self.wait("after_goto", 7.0)
        page = self._find() or page
        self._page = page
        rows = self._apply_list_view(page)
        self.log("[弹窗] 已刷新页面并重设列表视图（读到 %d 行）" % rows)
        return {"refreshed": True, "rows": rows}

    # ---- ① open
    def open_page(self) -> dict:
        """打开（或复用自己已开的）异常处理页，并把列表视图调好。

        ★ 2026-10-28：**只用自己的页**。旧实现「窗口里有就复用」＝在用户开着的
          异常处理页上切页签、改每页条数、转卖家履约 —— 用户看到的就是"我的页被动了"。
          现在没有自己的页就自己开一个，`close()` 负责关掉。
        """
        page = self._find()
        created = page is None
        if created:
            page = self._open_own_page()
        if self._js(page, _JS_IS_AUTH):
            raise RuntimeError("合作仓侧未授权（页面=%s）：先在窗口内完成 agentseller.temu.com 授权"
                               % str(page.url)[:80])
        if not created:
            # 自己上一轮已经开过、直接复用：不必再等 7s（2026-09-22 实测的空等）
            self.wait("after_tab", 1.0)
        # 清残留浮层：上一次中断可能把「请选择重发方式」留在页面上，会挡住后续点击。
        # 实测 Esc 关不掉（按完浮层还在），得点浮层里的「取消」。
        self._clear_popovers(page)
        self.sweep_popups("open")               # 关掉会挡列表的提示类弹窗（登记表驱动）
        rows = self._apply_list_view(page)
        self.log("[①] 异常列表读到 %d 行；标题=%s" % (rows, self._js(page, _JS_TITLE)))
        self.shot(page, "01_abnormal_page")
        self._page = page
        return {"url": str(page.url), "rows": rows}

    # ---- ② list
    def _blocking_layers(self, page) -> list:
        """页面上**仍会挡事**的浮层（登记为 `keep` 的常驻面板不算）。判据与 sweep_popups 同源。"""
        try:
            layers = self._js(page, _JS_LAYERS) or []
        except Exception:  # noqa: BLE001
            return []
        try:
            reg = load_popup_registry(path=self._popups_path())
        except Exception:  # noqa: BLE001
            reg = {"popups": [], "unknown": []}
        out = []
        for L in layers:
            txt = str(L.get("text") or "")
            if not txt.strip():
                continue
            e = match_popup(txt, txt, L.get("buttons") or [], reg)
            if str((e or {}).get("act") or "unknown") == "keep":
                continue                      # 常驻面板（如「全部消息」）不算遮挡
            out.append(L)
        return out

    def _empty_guard(self, page, swept: dict, stage: str) -> list:
        """读到 0 行时的防线：**先证明"真的空"，才允许报空**（2026-09-24 假空事故固化）。

        事故现场（2026-09-24 11:52:55 → 11:53:13）：`list` 先读到 10 单，18 秒后一次读到 0 单，
        被当成「异常清单为空」上报；而同一批单 11:57:12 复查**仍是 10 单**。当时页面上叠着未登记弹窗
        （「请先开通智能轨迹助手」+ 文件打开提示 + 「全部消息」面板），而未登记弹窗的处置动作是
        **刷新页面**——刷新后列表会短暂/真实地变成 0 行。⇒ **单次读到的 0 行不能当"清单已清空"**。

        判据顺序（任一关不过就不给"空"这个结论）：
          ① 页面身份：还在异常单页吗（url 含 `url_key`）；
          ② 重设列表视图（切「异常」页签 + 页大小）后**重读一次**；
          ③ 仍 0 行，且场上还有**非 keep** 浮层（或本轮弹窗处置刷新过页面）→ 抛「读不清」。
        真·空（身份对、重读仍 0、无遮挡）才返回 []。
        """
        key = str(self.cfg.get("url_key") or "")
        url = str(getattr(page, "url", "") or "")
        if key and key not in url:
            self.shot(page, "02_list_wrong_page", force=True)
            raise RuntimeError("异常单页身份不符（url=%s 不含 %s）→ 不判「清单为空」" % (url[:90], key))
        self.log("[②] 读到 0 行 → 重设列表视图后重读一次（不直接判空）")
        try:
            self.sweep_popups(stage + "-0")
        except Exception as e:  # noqa: BLE001
            self.log("[弹窗] 复检时处置失败（继续重读）：%s" % str(e)[:80])
        try:
            self._apply_list_view(page)
        except Exception as e:  # noqa: BLE001
            self.log("[页] 重设列表视图失败（继续重读）：%s" % str(e)[:80])
        rows = self._js(page, _JS_ROWS) or []
        if rows:
            self.log("[②] 重读拿到 %d 行（首次 0 行是瞬时/遮挡，非空）" % len(rows))
            return rows
        blockers = self._blocking_layers(page)
        refreshed = any(str(h.get("close") or "").startswith("refreshed")
                        for h in ((swept or {}).get("handled") or []))
        if blockers or refreshed:
            self.shot(page, "02_list_unclear", force=True)
            raise RuntimeError(
                "异常清单读不出来：两次都是 0 行，但页面仍有 %d 个未登记/未处置浮层%s —— "
                "**不判「清单为空」**（2026-09-24 假空事故口径）。请先清障或人工看一眼页面再跑。"
                % (len(blockers), "（本轮弹窗处置触发了刷新）" if refreshed else ""))
        return rows

    def list_total(self, page) -> int:
        """分页总数（`共有 N 条`）；读不到返回 -1。

        ★ 2026-10-28：只看 `_JS_ROWS` 的行数会把「77 条里的首页 10 条」当成全量 ——
          实测那天任务报「10 单 → 仍异常 0」，而页面写着「共有 77 条」。
        """
        try:
            n = self._js(page, _JS_TOTAL)
        except Exception:  # noqa: BLE001
            return -1
        try:
            return int(n)
        except Exception:  # noqa: BLE001
            return -1

    def read_list(self) -> dict:
        """读异常清单（**翻完所有页**）。返回里带 `total` / `pages` 供上层判完整度。"""
        page = self._page_or_open()
        swept = self.sweep_popups("list")
        total = self.list_total(page)
        rows = self._js(page, _JS_ROWS) or []
        if not rows:
            rows = self._empty_guard(page, swept, "list") or []
        seen, allrows = set(), []

        def take(batch):
            for r in batch or []:
                po = str(r.get("po") or "").strip()
                key = po or "|".join(str(r.get(k) or "") for k in ("qty", "sku", "reason"))
                if not key or key in seen:
                    continue
                seen.add(key)
                allrows.append(r)

        take(rows)
        page_no, pages = 1, 1
        if total > 0:
            per = max(1, len(rows))
            pages = (total + per - 1) // per
            # 逐页翻（上限保护：最多 40 页）
            while len(allrows) < total and page_no < min(pages, 40):
                page_no += 1
                r = self._js(page, _JS_CLICK_PAGE, page_no)
                if r != "CLICKED":
                    self.log("[②] ⚠ 翻到第 %d 页失败（%s）→ 只读到 %d 行"
                             % (page_no, r, len(allrows)))
                    break
                self.wait("after_tab", 2.0)
                before = len(allrows)
                take(self._js(page, _JS_ROWS))
                if len(allrows) == before:
                    self.log("[②] ⚠ 第 %d 页没读到新行 → 停止翻页" % page_no)
                    break
            if page_no > 1:                    # 翻完回到第 1 页，别把页面停在中间
                self._js(page, _JS_CLICK_PAGE, 1)
                self.wait("after_tab", 1.5)

        clean = [{k: r.get(k) for k in ("po", "batch", "order_type", "reason",
                                        "guide", "qty", "sku")} for r in allrows]
        out = {"count": len(clean), "rows": clean, "total": total, "pages": pages,
               "truncated": bool(total > 0 and len(clean) < total),
               "pos": [r["po"] for r in clean if r.get("po")]}
        self.log("[②] 异常清单 %d 单（页面共 %s 条，翻了 %d 页）：%s"
                 % (out["count"], total if total >= 0 else "?", page_no,
                    ", ".join(out["pos"][:6]) or "-"))
        if out["truncated"]:
            self.log("[②] ⚠ 只读到 %d / 共 %d 条 —— 剩下的这轮处理不到" % (len(clean), total))
        self.shot(page, "02_list")
        self.save(out, "02_abnormal_list")
        self._page = page
        return out

    # ---- ③ convert（写操作）
    def convert_all(self, orders=None) -> dict:
        """逐单转卖家履约：**单条失败只记账、继续下一单**，最后一次性把结果报全。

        为什么要 continue 而不是 fail-fast（2026-09-22 用户口径：「AI 是监管失败的情况……一遇到问题
        就停止，然后也不回消息」）：作业要么给出**整批结论**，要么给不出结论——中途抛错会让已完成的部分
        也变成"没有答案"。所以这里不做"停下等人"的判断：每单最多试 2 次（第 2 次先清残留浮层），
        仍失败就记进 `failed[]` 并继续；`failed` 非空时由 `[VERDICT]` 如实标未达标（不伪装成功）。

        ★ 2026-10-28 加**分批推进**（修「77 条只处理了 10 条」）：一份异常单可能横跨多页
          （页面每页最多 40 条、默认 10 条）。旧实现只拿第 1 页的 PO、剩下的永远不动，
          还报「仍异常 0」。现在：每轮处理当前页能看到的单 → 转掉的单会从列表消失 →
          重新取「现在还剩多少」，直到列表清空 / 没有进展 / 达到轮数上限。
          上限可配 `abnormal.max_rounds`（默认 8）、`abnormal.max_orders`（默认 200 单/次）。
        """
        page = self._page_or_open()
        explicit = normalize_orders(orders)
        cap_orders = int((self.cfg.get("abnormal") or {}).get("max_orders", 200) or 200)
        max_rounds = int((self.cfg.get("abnormal") or {}).get("max_rounds", 8) or 8)
        converted, failed, skipped, targets = [], [], [], []
        seen = set()

        def handle(po: str) -> bool:
            """返回 True=本单已解决（转掉或已在列表外）。"""
            if not self._row_present(page, po):
                self.log("[③] %s → 已在异常列表外，跳过" % po)
                skipped.append(po)
                return True
            err = ""
            for attempt in (1, 2):                      # 每单最多两次；第 2 次先清浮层重来
                try:
                    self._convert_one(page, po)
                    err = ""
                    break
                except Exception as e:  # noqa: BLE001
                    err = str(e)[:200]
                    self._clear_popovers(page)
                    if attempt == 1:
                        self.log("[③] %s 第 1 次未成（%s）→ 清浮层重试一次" % (po, err[:90]))
            if not err:
                converted.append(po)
                self.log("[③] %s → 已转卖家履约" % po)
                return True
            failed.append({"po": po, "error": err})
            self.log("[③] %s → 失败：%s（记账后继续）" % (po, err[:120]))
            self.shot(page, "03_fail_%s" % po, force=True)
            return False

        if explicit:
            for po in explicit:
                targets.append(po)
                handle(po)
                self.wait("between_orders", 1.5)
            left = None
        else:
            for rnd in range(1, max_rounds + 1):
                total = self.list_total(page)
                batch = [r["po"] for r in (self._js(page, _JS_ROWS) or []) if r.get("po")]
                batch = [p for p in batch if p not in seen]
                if not batch:
                    self.log("[③] 第 %d 轮：当前页没有可处理的单（total=%s）"
                             % (rnd, total if total >= 0 else "?"))
                    break
                if len(targets) >= cap_orders:
                    self.log("[③] 已达单次上限 %d 单，剩余留到下一轮任务" % cap_orders)
                    break
                batch = batch[: max(1, cap_orders - len(targets))]
                self.log("[③] 第 %d 轮：处理 %d 单（页面共 %s 条）"
                         % (rnd, len(batch), total if total >= 0 else "?"))
                progressed = 0
                for po in batch:
                    seen.add(po)
                    targets.append(po)
                    if handle(po):
                        progressed += 1
                    self.wait("between_orders", 1.5)
                # 一轮下来一条都没推动 → 别死循环（多单都在失败，留给次日/人工）
                if progressed == 0:
                    self.log("[③] 本轮没有任何进展 → 停止（避免死循环）")
                    break
                # 转掉的单会消失，重新载入 page1 再取下一批。
                # 这是**自己的页**（orders_page/_find 只给自己的），可以重载；
                # 仍保留 is_foreign 兜底，万一哪天传进来的是复用页就另开自己的。
                try:
                    if page.is_foreign():
                        page, _ = page.open_own_tab(self.cfg["url"])
                    else:
                        page.goto(self.cfg["url"])
                except Exception:  # noqa: BLE001
                    pass
                self.wait("after_goto", 7.0)
                self._apply_list_view(page)
            left = self.list_total(page)

        out = {"targets": targets, "converted": converted, "skipped": skipped,
               "failed": failed, "remaining_total": left,
               "ok": (not failed) and (left in (None, 0))
                     and (len(converted) + len(skipped) == len(targets))}
        self.log("[③] 结束：成功 %d / 跳过 %d / 失败 %d（共 %d）；页面还剩 %s 条"
                 % (len(converted), len(skipped), len(failed), len(targets),
                    left if left is not None and left >= 0 else "?"))
        self.shot(page, "03_converted")
        self.save(out, "03_convert_result")
        return out

    def _convert_one(self, page, po: str) -> None:
        """把一单转卖家履约；任一环节不成即抛错（由 `convert_all` 记账并继续下一单）。"""
        # 点前先清残留浮层：否则这一下「重发」只会把旧浮层关掉、新浮层根本不开
        self._clear_popovers(page)
        self.sweep_popups("before_convert")     # 再清扫会挡「重发」的通知类弹窗
        r1 = self._js(page, _JS_CLICK_REPUSH, po)
        if r1 != "CLICKED":
            raise RuntimeError("点「重发」失败：%s" % r1)
        if not self._wait_popover(page, 6.0):
            raise RuntimeError("点「重发」后浮层未出现")
        self.wait("after_repush", 1.0)
        r2 = self._js(page, _JS_CLICK_IN_POPOVER,
                      {"text": self.cfg["labels"]["to_seller"],
                       "within": (self.cfg.get("popover") or {}).get("layer1_any") or [],
                       "fallback_self": True})
        if r2 != "CLICKED":
            raise RuntimeError("点「%s」失败：%s" % (self.cfg["labels"]["to_seller"], r2))
        self.wait("after_to_seller", 2.5)
        r3 = self._js(page, _JS_CLICK_IN_POPOVER,
                      {"text": self.cfg["labels"]["confirm"],
                       "within": (self.cfg.get("popover") or {}).get("layer2_any") or []})
        if r3 != "CLICKED":
            raise RuntimeError("点二次「确认」失败：%s" % r3)
        ok, _seen = self._wait_row_gone(page, po)
        if not ok:
            raise RuntimeError("点了确认但该单仍在异常列表（%ds 内未消失）"
                               % int((self.cfg.get("waits") or {}).get("row_gone_timeout", 20)))

    def _row_present(self, page, po: str) -> bool:
        try:
            rows = self._js(page, _JS_ROWS) or []
        except Exception:  # noqa: BLE001
            return True
        return any(str(r.get("po") or "").strip() == po for r in rows)

    def _clear_popovers(self, page) -> None:
        """把页面上残留的浮层点「取消」关掉（Esc 实测关不掉）。"""
        for _ in range(3):
            try:
                if not int(self._js(page, _JS_HAS_POPOVER) or 0):
                    return
                r = self._js(page, _JS_CLICK_IN_POPOVER,
                             {"text": self.cfg["labels"].get("cancel", "取消"), "within": ""})
            except Exception:  # noqa: BLE001
                return
            if r != "CLICKED":
                return
            self.wait("after_tab", 0.8)

    def _wait_popover(self, page, timeout: float) -> bool:
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            try:
                if int(self._js(page, _JS_HAS_POPOVER) or 0) > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.6)
        return False

    # ---- 弹窗处置（登记表驱动：识别 → 关掉/点掉/放过）
    def _popups_path(self) -> str:
        raw = str(self.cfg.get("popups_file") or "").strip()
        if raw and os.path.isabs(raw):
            return raw
        return DEFAULT_POPUPS_PATH

    def sweep_popups(self, stage: str = "") -> dict:
        """清扫页面上**会挡操作的弹窗**：登记表认得→按 `act` 处置；不认得→登记+试关+继续。

        用户 2026-09-22：「脚本能够走完完整的一条路，并且针对不同弹窗要能够检测并关闭」。
        · `keep`＝流程弹层（如「请选择重发方式」），**一律不碰**（关了就等于取消本单）；
        · `dismiss`＝关掉继续（关闭图标 → 文案键 → 近似右上角点三层兜底）；
        · `click`＝点 `when.button`；`stop`＝停下取证；
        · 未知＝截图 + 写进登记表 `unknown[]`（下次就被认出来）+ **试关后继续**（本域口径见表的
          `_unknown_policy`：不停批，因为动作都是文本精确匹配，最多让当前这单失败并记账）。
        登记表与读写实现都复用 `lib/temu_buylabel_driver.py` 那套 —— 本域只提供**表和判据**。
        """
        pcfg = self.cfg.get("popups") or {}
        if pcfg.get("enabled", True) is False:
            return {"swept": 0, "handled": []}
        page = getattr(self, "_page", None) or self._find()
        if page is None:
            return {"swept": 0, "handled": []}
        try:
            reg = load_popup_registry(path=self._popups_path())
        except Exception:  # noqa: BLE001
            reg = {"popups": [], "unknown": []}
        handled = []
        tried = {}                                   # 同一弹窗在本阶段最多处置一次（防"刷了还在"的空转）
        for _ in range(int(pcfg.get("max_sweeps") or 3)):
            try:
                layers = self._js(page, _JS_LAYERS) or []
            except Exception:  # noqa: BLE001
                break
            hit = None
            for L in layers:
                txt = str(L.get("text") or "")
                e = match_popup(txt, txt, L.get("buttons") or [], reg)
                act = str((e or {}).get("act") or "unknown")
                if act == "keep":
                    continue                     # 流程弹层：放过
                hit = (L, e, act)
                break
            if hit is None:
                break
            L, e, act = hit
            anchor = str(L.get("text") or "")[:40]
            # ★ 防抖（2026-09-22 B 机实测）：同一个弹窗**处置过一次还在**，说明它不吃这一套
            #   （常驻面板刷不掉 / 刷新后自动复现）——再刷只是白烧 11-13s。停手并留痕。
            key_txt = anchor[:30]
            if tried.get(key_txt):
                self.log("[弹窗] 「%s」处置后仍在 → 判定为非遮挡（不再重复处置）" % key_txt)
                break
            tried[key_txt] = True
            shot = ""
            if act == "dismiss":
                res = self._close_layer(page, L, anchor, pcfg)
                if e:
                    bump_popup_hits(e, reg, path=self._popups_path())
            elif act == "click":
                btn = str(((e or {}).get("when") or {}).get("button") or "")
                res = (self._js(page, _JS_CLICK_IN_POPOVER, {"text": btn, "within": []})
                       if btn else "NO_BUTTON")
                if e:
                    bump_popup_hits(e, reg, path=self._popups_path())
            elif act == "refresh":
                # 用户 2026-09-22 规则：**遇到 TEMU 弹窗就刷新**（比找关闭键稳）
                if e:
                    bump_popup_hits(e, reg, path=self._popups_path())
                self._refresh_page()
                page = getattr(self, "_page", None) or page
                res = "refreshed"
            elif act == "stop":
                self.shot(page, "02_popup_stop", force=True)
                raise RuntimeError("弹窗登记为 stop（需人工确认，未自动点）：%s" % anchor)
            else:                                # unknown
                shot = self.shot(page, "_popup_seen_unknown", force=True) or ""
                register_unknown_popup(anchor, str(L.get("text") or ""), L.get("buttons") or [],
                                       url=str(page.url), step=stage, shot=shot,
                                       path=self._popups_path())
                if str(pcfg.get("unknown_act") or "refresh") == "refresh":
                    # 规则统一：没登记的也**刷新**（同时已登记进 unknown[]，下次能被认出来）
                    self._refresh_page()
                    page = getattr(self, "_page", None) or page
                    res = "refreshed(unknown)"
                else:
                    res = self._close_layer(page, L, anchor, pcfg)
            handled.append({"id": (e or {}).get("id"), "act": act,
                            "text": anchor[:28], "close": res})
            self.wait("after_tab", 0.8)
        if handled:
            self.log("[弹窗] %s 处置 %d 个：%s" % (
                stage or "-", len(handled),
                "; ".join("%s:%s/%s" % (h["act"], h["id"] or "未登记", h["text"]) for h in handled)))
        return {"swept": len(handled), "handled": handled}

    def _close_layer(self, page, layer: dict, anchor: str, pcfg: dict) -> str:
        """关掉一个浮层：关闭图标 → 文案键（取消/关闭/我知道了）→ 近似右上角点。"""
        if layer.get("has_close"):
            if self._js(page, _JS_CLOSE_LAYER, {"anchor": anchor, "mode": "icon"}) == "CLICKED":
                return "icon"
        texts = pcfg.get("close_texts") or ["取消", "关闭", "我知道了"]
        if self._js(page, _JS_CLOSE_LAYER,
                    {"anchor": anchor, "mode": "text", "texts": texts}) == "CLICKED":
            return "text"
        rect = layer.get("rect") or []
        if len(rect) == 4:
            try:
                page.mouse.click(float(rect[0]) + float(rect[2]) - 26, float(rect[1]) + 26)
                return "approx"
            except Exception:  # noqa: BLE001
                pass
        return "FAILED"

    def _wait_row_gone(self, page, po: str) -> tuple:
        """成功判据：该 PO 从异常列表消失（内容断言）。返回 (ok, 期间看到的提示文本)。"""
        tmo = float((self.cfg.get("waits") or {}).get("row_gone_timeout", 20.0))
        deadline = time.time() + tmo
        seen = ""
        while time.time() < deadline:
            try:
                texts = self._js(page, _JS_TOASTS) or []
            except Exception:  # noqa: BLE001
                texts = []
            if not seen:
                hit = [x for x in texts if str(self.cfg.get("toast_contains", "成功")) in str(x)]
                if hit:
                    seen = str(hit[0])[:60]
            if not self._row_present(page, po):
                return True, seen
            time.sleep(1.0)
        return False, seen

    # ---- ④ verify（只读）
    def _targets_from_evidence(self) -> list:
        """不给 --orders 时，用本轮 `list` 落盘的清单当基准（整链里 verify 要拿它比对）。"""
        if not self.ev:
            return []
        fp = os.path.join(self.ev, "02_abnormal_list.json")
        try:
            with open(fp, encoding="utf-8") as f:
                return normalize_orders(json.load(f).get("pos") or [])
        except Exception:  # noqa: BLE001
            return []

    def verify(self, orders=None) -> dict:
        page = self._page_or_open()
        targets = normalize_orders(orders) or self._targets_from_evidence()
        try:
            # ★ 复用页不许 goto（会覆盖用户的标签）→ 开自己的页
            if page.is_foreign():
                page, _ = page.open_own_tab(self.cfg["url"])
            else:
                page.goto(self.cfg["url"])
        except Exception:  # noqa: BLE001
            pass
        self.wait("after_goto", 7.0)
        swept = self.sweep_popups("verify")
        now = self._js(page, _JS_ROWS) or []
        if not now:
            # 同 `read_list`：verify 的 0 行也会直接决定 [VERDICT] 是否 complete ⇒ 同样不许误判空
            now = self._empty_guard(page, swept, "verify")
        res = diff_remaining(targets, now)
        # ★ 2026-10-28：**必须以页面总数复核**。原来只比"我处理过的那批单还在不在"，
        #   于是「77 条里处理了 10 条」也会报「已消失 10 / 仍异常 0」= 假成功。
        total = self.list_total(page)
        res["total"] = total
        res["now_count"] = len(now)
        res["now_pos"] = [r.get("po") for r in now if r.get("po")]
        if total > 0:
            res["ok"] = False
            res["left_on_page"] = total
            self.log("[④] ⚠ 页面上还有 %d 条异常单（本轮只处理了 %d 条）→ 判未完成"
                     % (total, len(res["targets"])))
        else:
            res["left_on_page"] = 0
        self.log("[④] 复核：原清单 %d 单 → 已消失 %d / 仍异常 %d；页面剩余 %s 条"
                 % (len(res["targets"]), len(res["gone"]), len(res["remaining"]),
                    total if total >= 0 else "?"))
        self.shot(page, "04_verify")
        self.save(res, "04_verify")
        return res


# ---------------------------------------------------------------- 唯一门
def perform(action: str, params: dict) -> dict:
    """统一入口：action ∈ ACTIONS；params 由入口/步骤覆盖提供。"""
    if action not in ACTIONS:
        raise RuntimeError("未知 action=%s（可选：%s）" % (action, "/".join(ACTIONS)))
    cfg = params.get("cfg") or {}
    store = str(params.get("store") or "")
    # 端口现场校验：auth 遇卡死会「关窗 → 重开」，**端口会变**（旧端口随即失效）；
    # 后面 open/list/convert/verify 各步拿的还是同一份 params["port"] ⇒ 死端口必须现探一次再往下跑。
    port = int(params.get("port") or 0)
    if not _port_alive(port) and store:
        e = _store_entry(store)
        if e:
            try:
                import ziniao_store as _zs
                fresh = _zs.probe_port_by_window(e.get("ziniao_window_name") or "")
            except Exception:  # noqa: BLE001
                fresh = 0
            if fresh:
                (params.get("log") or print)("[步骤] 端口 %s 已失效 → 现场重探 = %s" % (port, fresh))
                port = int(fresh)
    drv = TemuAbnormalDriver(cfg, log=params.get("log") or print,
                             evidence_dir=params.get("evidence_dir"),
                             confirm=bool(params.get("confirm")),
                             store=store)
    try:
        drv.connect(port)
        if action == "auth":
            return drv.ensure_auth()
        if action == "open":
            return drv.open_page()
        if action == "list":
            return drv.read_list()
        if action == "convert":
            return drv.convert_all(params.get("orders"))
        return drv.verify(params.get("orders"))
    finally:
        drv.close()


# ---------------------------------------------------------------- 维护约定
# 1) 选择器与交互坑集中在上面 `_JS_*` 常量区：平台改版只改这里，改完跑
#    `python -m py_compile` + `python -m pytest scripts/power/tests/test_temu_abnormal.py -q`
#    + 真机 `--action open --action list`（只读）复验，**再**谈 convert。
# 2) 口径一律改 `data/temu_abnormal.json`，顺序一律改 `data/temu_abnormal_pipeline.json`，
#    不改本文件结构；要新能力＝加 action，不 fork 脚本。
# 3) 本域**不设失败分支矩阵**：用户 2026-09-22 口径＝按这条动作走不会出错；真出错即抛错，
#    由入口转成 HARD STOP(42)，不连环重试。
# 4) 本驱动被 tests import，禁止在模块顶层包装 sys.stdout。
