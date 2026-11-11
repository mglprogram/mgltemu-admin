#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hx_oms_ship.py — 恒信（HX）领星 OMS(LM739) 批量发货（按 hx-lingxing-fulfill skill 实现）。

替代 `run_temu_fulfill.py` 里原来那条「恒信份」的 TEMU 窗口链路（`oms_ship_one.py`）。
skill 文档：`~/.agents/skills/hx-lingxing-fulfill/SKILL.md`（2026-11-11 用户定稿的口径）。

链路（顺序不能乱）：
    ① 切「待处理」页签（不在待处理下搜，搜出来的单没有下单意义）
    ② 每页 200 条
    ③ 平台单号直接填主输入框（逗号/换行分隔）→ 回车
    ④ 勾全选 → 分配仓库&物流 → 分配全部数据
       面单设置=普通发货 / 发货仓库=ZOO WAREHOUSE ⑥美西 (USW01)
       / 物流渠道=上传物流面单 (Upload_Shipping_Label) / 承运商=自动匹配
    ⑤ 确定并审核
    ⑥ 「待获取平台面单」重进刷新 → 有「获取异常」→ 全选 → 重新获取 → 确定
    ⑦ 回读：处理中 = 下单成功；点过「重新获取」仍留「获取异常」= 没买到面单 = 失败

用法：
    python scripts/power/hx_oms_ship.py --pos-file <txt> [--out <dir>] [--dry]
    python scripts/power/hx_oms_ship.py --pos A,B,C --dry

退出码：0=全部有终态（处理中/已发货）；42=有失败或未查到；2=参数问题。

⚠ 所有页面动作都走 `ziniao-cli page exec --store-id <店窗口> --script <js>`：
   本 Bridge 的 `page click --selector ':has-text'` / `page input` 带特殊 CSS 时不可靠
   （skill 里写明的真机结论），所以统一用「页面内 JS 自己找元素、自己点」。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: 恒信 OMS 所在紫鸟店铺窗口（skill 写死：TK-Savonire-YJF / 27663658415319）
STORE_ID = "27663658415319"
STORE_NAME = "TK-Savonire-YJF"
OMS_URL = "https://oms.xlwms.com/platform/order/list"

#: 分配仓库&物流的固定口径（用户 2026-11-11 定）
WAREHOUSE = "ZOO WAREHOUSE ⑥美西"
WH_CODE = "USW01"
CHANNEL = "Upload_Shipping_Label"

#: 处理中/已发货 = 成功
OK_STATUS = ("处理中", "已发货", "已签收", "已完成")


def log(s: str) -> None:
    print(str(s), flush=True)


# ---------------------------------------------------------------- 页面桥

def _cli() -> list:
    """ziniao-cli 的命令前缀（list，给 subprocess 用，**不走 shell**）。

    ★ 2026-11-11 真机两条：
      ① npm 只装 `.cmd`/`.ps1` shim，没有 `.exe` ⇒ 直接跑 `ziniao-cli` 会 `WinError 2`；
      ② 走 `.cmd` 时中文/引号会被 cmd.exe 按 GBK 折腾坏 ⇒ `--script` 里的中文
         （页签「待处理」之类）传进去全是乱码，页面 JS 直接匹配不到、返回 null。
      所以这里**直接调 node + cli 的 run.js**，参数按 UTF-8 原样传。
    """
    base = os.path.join(os.environ.get("APPDATA", ""), "npm")
    run_js = os.path.join(base, "node_modules", "@ziniao-open", "cli", "scripts", "run.js")
    node = os.path.join(base, "node.exe")
    if not os.path.exists(node):
        import shutil  # noqa: PLC0415
        node = shutil.which("node") or "node"
    if os.path.exists(run_js):
        return [node, run_js]
    return ["ziniao-cli.cmd"]


def js(script: str, store_id: str = STORE_ID, timeout_ms: int = 30000) -> dict:
    """在店铺窗口里执行 JS，返回 `{ok, result|err}`（result 已尽力从字符串转对象）。"""
    argv = _cli() + ["page", "exec", "--store-id", store_id,
                     "--script", script, "--timeout", str(timeout_ms)]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=max(60, timeout_ms // 1000 + 30))
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "exec 超时 %dms" % timeout_ms}
    out = (p.stdout or "").strip()
    try:
        doc = json.loads(out)
    except Exception:                                   # noqa: BLE001
        return {"ok": False, "err": (out or (p.stderr or ""))[-300:]}
    data = (doc.get("data") or {})
    inner = data.get("data") or {}
    if inner.get("exceptionDetails"):
        return {"ok": False, "err": str(inner["exceptionDetails"])[:300]}
    res = inner.get("result")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:                               # noqa: BLE001
            pass
    return {"ok": bool(doc.get("ok", True)), "result": res}


def wait_for(fn, timeout: float, interval: float = 1.5, what: str = "") -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(interval)
    if what:
        log("   ⚠ 等待超时：%s（%.0fs）" % (what, timeout))
    return False


# ---------------------------------------------------------------- 各步 JS

#: 找主输入框：标签「平台单号」右侧那个 `.el-input__inner`。
#: 兜底：第一个 placeholder 含「平台单号」或「订单号」的可见 input。
JS_FIND_MAIN_INPUT = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const inputs = [...document.querySelectorAll('input.el-input__inner')].filter(vis);
  let hit = inputs.find(i => /平台单号|平台订单号/.test(i.placeholder || ''));
  if (!hit) hit = inputs.find(i => /订单号/.test(i.placeholder || ''));
  if (!hit) hit = inputs[0];
  if (!hit) return JSON.stringify({err: '找不到主输入框'});
  const r = hit.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                         ph: hit.placeholder || '', n: inputs.length});
})()"""

JS_CLICK_TAB = r"""(() => {
  const want = %s;
  const tabs = [...document.querySelectorAll('.el-tabs__item')].filter(t => t.offsetParent !== null);
  const t = tabs.find(x => (x.innerText || '').trim().indexOf(want) === 0);
  if (!t) return JSON.stringify({err: '找不到页签 ' + want,
                                 tabs: tabs.map(x => x.innerText.trim())});
  t.click();
  return JSON.stringify({clicked: (t.innerText || '').trim()});
})()"""

#: 每页 200 条：点分页里的 select → 选「200条/页」
JS_SET_PAGESIZE = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const sel = [...document.querySelectorAll('.el-pagination .el-select, .el-pagination__sizes .el-select')]
    .filter(vis)[0];
  if (!sel) return JSON.stringify({err: '找不到每页条数选择器'});
  const cur = (sel.innerText || '').trim();
  if (/200/.test(cur)) return JSON.stringify({already: cur});
  const inp = sel.querySelector('input');
  (inp || sel).click();
  return JSON.stringify({opened: true, cur});
})()"""

JS_PICK_PAGESIZE = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const items = [...document.querySelectorAll('.el-select-dropdown__item')].filter(vis);
  const it = items.find(x => /200\s*条/.test(x.innerText || ''));
  if (!it) return JSON.stringify({err: '下拉里没有 200条/页', opts: items.map(x => x.innerText.trim()).slice(0, 10)});
  it.click();
  return JSON.stringify({picked: (it.innerText || '').trim()});
})()"""

#: 写主输入框 + 回车（原生 setter，避免被 Vue 忽略）
JS_FILL_SEARCH = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const inputs = [...document.querySelectorAll('input.el-input__inner')].filter(vis);
  let hit = inputs.find(i => /平台单号|平台订单号/.test(i.placeholder || ''));
  if (!hit) hit = inputs.find(i => /订单号/.test(i.placeholder || ''));
  if (!hit) hit = inputs[0];
  if (!hit) return JSON.stringify({err: '找不到主输入框'});
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  set.call(hit, %s);
  hit.dispatchEvent(new Event('input', {bubbles: true}));
  hit.dispatchEvent(new Event('change', {bubbles: true}));
  hit.focus();
  for (const k of ['keydown', 'keyup']) {
    hit.dispatchEvent(new KeyboardEvent(k, {key: 'Enter', code: 'Enter',
      keyCode: 13, which: 13, bubbles: true}));
  }
  return JSON.stringify({value: hit.value, lines: (hit.value || '').split(/[\n,]/).filter(Boolean).length});
})()"""

#: 表格当前页行数 / 单号（vxe-table）
JS_ROWS = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const tb = document.querySelector('.vxe-table--body-wrapper') ||
             document.querySelector('.vxe-table');
  if (!tb) return JSON.stringify({err: '找不到表格'});
  const rows = [...document.querySelectorAll('.vxe-body--row')].filter(vis);
  const out = rows.map(tr => {
    const cells = [...tr.querySelectorAll('.vxe-cell')].map(c => (c.innerText || '').trim());
    return cells;
  });
  return JSON.stringify({n: out.length, sample: out.slice(0, 3)});
})()"""

JS_SELECT_ALL = r"""(() => {
  const vis = e => e && e.offsetParent !== null;
  const un = [...document.querySelectorAll('.vxe-table--header-wrapper .vxe-checkbox--unchecked-icon')]
    .filter(vis);
  if (!un.length) {
    const ck = [...document.querySelectorAll('.vxe-table--header-wrapper .vxe-checkbox--checked-icon')]
      .filter(vis).length;
    return JSON.stringify({already: true, checked: ck});
  }
  const el = un[0];
  const r = el.getBoundingClientRect();
  const t = document.elementFromPoint(Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2));
  (t || el).click();
  return JSON.stringify({clicked: 1});
})()"""

#: 藏掉挡按钮的 el-popover 浮层（skill 里的坑）
JS_HIDE_POPOVER = r"""(() => {
  let n = 0;
  document.querySelectorAll('.el-popover').forEach(p => {
    if (p.style.display !== 'none') { p.style.display = 'none'; n++; }
  });
  return JSON.stringify({hidden: n});
})()"""

JS_CLICK_BUTTON = r"""(() => {
  const want = %s;
  const vis = e => e && e.offsetParent !== null;
  const btns = [...document.querySelectorAll('button, .el-button, a, span')].filter(vis);
  const b = btns.find(x => (x.innerText || '').replace(/\s+/g, '').indexOf(want) === 0);
  if (!b) return JSON.stringify({err: '找不到按钮 ' + want});
  b.scrollIntoView({block: 'center'});
  b.click();
  return JSON.stringify({clicked: (b.innerText || '').trim()});
})()"""

JS_CLICK_TEXT = r"""(() => {
  const want = %s;
  const vis = e => e && e.offsetParent !== null;
  const cands = [...document.querySelectorAll(
    '.el-select-dropdown__item, .el-radio, .el-cascader-node, li, div.el-radio__label, span')]
    .filter(vis);
  const hit = cands.find(x => (x.innerText || '').replace(/\s+/g, '').indexOf(want) === 0);
  if (!hit) return JSON.stringify({err: '找不到选项 ' + want, n: cands.length});
  hit.click();
  return JSON.stringify({clicked: (hit.innerText || '').trim().slice(0, 40)});
})()"""

#: 打开弹窗里的 el-select（按 label 文字定位那一行的 select）
JS_OPEN_SELECT_BY_LABEL = r"""(() => {
  const label = %s;
  const vis = e => e && e.offsetParent !== null;
  const forms = [...document.querySelectorAll('.el-form-item')].filter(vis);
  const f = forms.find(x => (x.innerText || '').replace(/\s+/g, '').indexOf(label) >= 0);
  if (!f) return JSON.stringify({err: '找不到表单项 ' + label});
  const inp = f.querySelector('input.el-input__inner');
  if (!inp) return JSON.stringify({err: '该表单项没有 select'});
  inp.click();
  return JSON.stringify({opened: true});
})()"""

JS_DIALOG_TEXT = r"""(() => {
  const d = [...document.querySelectorAll('.el-dialog, .el-message-box')]
    .filter(x => x.offsetParent !== null).pop();
  return JSON.stringify({text: d ? (d.innerText || '').replace(/\s+/g, ' ').slice(0, 300) : ''});
})()"""

JS_DUMP = r"""(() => {
  const SEP_R = String.fromCharCode(30), SEP_C = String.fromCharCode(31);
  const vis = e => e && e.offsetParent !== null;
  const rows = [...document.querySelectorAll('.vxe-body--row')].filter(vis);
  const lines = rows.map(tr => [...tr.querySelectorAll('.vxe-cell')]
    .map(c => (c.innerText || '').trim()).join(SEP_C));
  return lines.join(SEP_R);
})()"""


# ---------------------------------------------------------------- 主流程

def ensure_page(dry: bool = False) -> None:
    """确保窗口在销售订单页；不在就导航过去。"""
    r = js("(() => JSON.stringify({url: location.href}))()")
    url = str((r.get("result") or {}).get("url") or "")
    log("   当前页：%s" % (url or "?"))
    if "oms.xlwms.com" not in url or "/platform/order/list" not in url:
        log("   → 导航到 %s" % OMS_URL)
        subprocess.run(_cli() + ["page", "visit", "--store-id", STORE_ID, "--url", OMS_URL],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        time.sleep(6)


def step_tab_pending() -> bool:
    r = js(JS_CLICK_TAB % json.dumps("待处理", ensure_ascii=False))
    log("   ① 切「待处理」：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:160])
    time.sleep(2.5)
    return bool((r.get("result") or {}).get("clicked"))


def step_page_size() -> None:
    r = js(JS_SET_PAGESIZE)
    cur = (r.get("result") or {})
    if cur.get("already"):
        log("   ② 每页已是 200（%s）" % cur["already"])
        return
    time.sleep(1.2)
    r2 = js(JS_PICK_PAGESIZE)
    log("   ② 每页设 200：%s" % json.dumps(r2.get("result"), ensure_ascii=False)[:160])
    time.sleep(2.0)


def step_search(pos: list) -> int:
    text = ",".join(pos)
    r = js(JS_FILL_SEARCH % json.dumps(text, ensure_ascii=False))
    got = (r.get("result") or {})
    log("   ③ 贴单号 %d 个 → 输入框回读 %s 个" % (len(pos), got.get("lines")))
    if got.get("err"):
        raise SystemExit("[FATAL] 主输入框写值失败：%s" % got["err"])
    ok = wait_for(lambda: int((js(JS_ROWS).get("result") or {}).get("n") or 0) > 0,
                  timeout=40, what="搜索结果出现")
    n = int((js(JS_ROWS).get("result") or {}).get("n") or 0)
    log("   搜索结果行数：%d（%s）" % (n, "OK" if ok else "可能 0 行/分页"))
    return n


def step_select_all() -> None:
    js(JS_HIDE_POPOVER)
    r = js(JS_SELECT_ALL)
    log("   ④ 勾全选：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:120])
    time.sleep(1.0)


def step_allocate(dry: bool) -> dict:
    js(JS_HIDE_POPOVER)
    r = js(JS_CLICK_BUTTON % json.dumps("分配仓库&物流", ensure_ascii=False))
    log("   ④ 点「分配仓库&物流」：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:160])
    if (r.get("result") or {}).get("err"):
        # 兜底：直接调页面自己的方法（skill 里写明）
        r = js("(() => { try { PlatformOrder.handleAllotAll(); return 'called'; }"
               " catch(e) { return JSON.stringify({err:String(e).slice(0,120)}); } })()")
        log("      兜底 handleAllotAll：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:120])
    time.sleep(2.5)
    txt = str((js(JS_DIALOG_TEXT).get("result") or {}).get("text") or "")
    log("   弹窗：%s" % txt[:160])
    if not txt:
        return {"ok": False, "why": "分配弹窗没打开"}

    # 分配全部数据
    r = js(JS_CLICK_TEXT % json.dumps("分配全部数据", ensure_ascii=False))
    log("   · 分配全部数据：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:120])
    time.sleep(1.0)

    # 发货仓库
    js(JS_OPEN_SELECT_BY_LABEL % json.dumps("发货仓库", ensure_ascii=False))
    time.sleep(1.2)
    r = js(JS_CLICK_TEXT % json.dumps(WAREHOUSE, ensure_ascii=False))
    log("   · 发货仓库 %s：%s" % (WAREHOUSE, json.dumps(r.get("result"), ensure_ascii=False)[:140]))
    time.sleep(1.0)

    # 物流渠道（cascader）
    js(JS_OPEN_SELECT_BY_LABEL % json.dumps("物流渠道", ensure_ascii=False))
    time.sleep(1.2)
    r = js(JS_CLICK_TEXT % json.dumps(CHANNEL, ensure_ascii=False))
    log("   · 物流渠道 %s：%s" % (CHANNEL, json.dumps(r.get("result"), ensure_ascii=False)[:140]))
    time.sleep(1.0)

    if dry:
        return {"ok": True, "dry": True, "dialog": txt[:200]}

    r = js(JS_CLICK_BUTTON % json.dumps("确定并审核", ensure_ascii=False))
    log("   ⑤ 确定并审核：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:140])
    time.sleep(4)
    return {"ok": not (r.get("result") or {}).get("err"), "dialog": txt[:200]}


def step_wait_track(pos: list) -> dict:
    """⑥ 待获取平台面单 → 重进刷新 → 获取异常 全选 → 重新获取 → 确定。"""
    want = "待获取平台面单"
    js(JS_CLICK_TAB % json.dumps(want, ensure_ascii=False))
    time.sleep(3)
    js(JS_CLICK_TAB % json.dumps(want, ensure_ascii=False))     # 重进一次刷新状态（skill 口径）
    time.sleep(3)
    n = int((js(JS_ROWS).get("result") or {}).get("n") or 0)
    log("   ⑥ 「待获取平台面单」%d 行" % n)
    if n == 0:
        return {"retry": 0}
    js(JS_HIDE_POPOVER)
    js(JS_SELECT_ALL)
    time.sleep(1)
    r = js(JS_CLICK_BUTTON % json.dumps("重新获取", ensure_ascii=False))
    log("   · 重新获取：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:140])
    time.sleep(2.5)
    r = js(JS_CLICK_BUTTON % json.dumps("确定", ensure_ascii=False))
    log("   · 二次确认「确定」：%s" % json.dumps(r.get("result"), ensure_ascii=False)[:140])
    time.sleep(4)
    js(JS_CLICK_TAB % json.dumps(want, ensure_ascii=False))
    time.sleep(3)
    return {"retry": n, "left": int((js(JS_ROWS).get("result") or {}).get("n") or 0)}


#: dump 每行 → [{po, status, wh, channel, track, err}]（列顺序按 OMS 列表页）
COLS = ("platformOrderNo", "orderNo", "status", "sendWhName", "logisticsChannelName",
        "logisticsCarrierName", "trackNo", "auditCause")


def dump_rows(pos: list) -> list:
    """读回本批每单状态。先试 `PlatformOrder.tableData`，再退 DOM 文本。"""
    jsx = r"""(() => {
      const SEP_R = String.fromCharCode(30), SEP_C = String.fromCharCode(31);
      try {
        const t = (window.PlatformOrder && PlatformOrder.tableData) || [];
        if (t && t.length) {
          return t.map(r => [r.platformOrderNo, r.orderNo, r.status, r.sendWhName,
            r.logisticsChannelName, r.logisticsCarrierName, r.trackNo, r.auditCause]
            .map(x => (x === undefined || x === null) ? '' : String(x)).join(SEP_C)).join(SEP_R);
        }
      } catch (e) {}
      const vis = e => e && e.offsetParent !== null;
      const rows = [...document.querySelectorAll('.vxe-body--row')].filter(vis);
      return rows.map(tr => [...tr.querySelectorAll('.vxe-cell')]
        .map(c => (c.innerText || '').trim()).join(SEP_C)).join(SEP_R);
    })()"""
    r = js(jsx)
    raw = r.get("result")
    if not isinstance(raw, str):
        return []
    out = []
    want = set(pos)
    for rec in raw.split(chr(30)):
        cells = rec.split(chr(31))
        if not cells or not cells[0]:
            continue
        row = dict(zip(COLS, cells))
        po = str(row.get("platformOrderNo") or "").strip()
        if po not in want:
            # DOM 兜底时列顺序可能不同：按单号列模糊匹配
            po = next((c.strip() for c in cells if c.strip() in want), "")
            if not po:
                continue
            row = {"platformOrderNo": po, "_cells": cells}
        out.append(row)
    return out


def run(pos: list, out_dir: str, dry: bool = False) -> dict:
    log("[恒信OMS][%s] %d 单%s" % (STORE_NAME, len(pos), "（dry）" if dry else ""))
    ensure_page(dry=dry)
    step_tab_pending()
    step_page_size()
    n = step_search(pos)
    if n == 0:
        return {"total": len(pos), "found": 0, "ok": [], "fail": [],
                "why": "搜索结果 0 行（单号可能不在待处理页签 / 尚未同步）"}
    step_select_all()
    alloc = step_allocate(dry=dry)
    if dry:
        return {"total": len(pos), "found": n, "dry": True, "alloc": alloc}

    retry = {}
    if alloc.get("ok"):
        retry = step_wait_track(pos)

    rows = dump_rows(pos)
    ok, bad = [], []
    for r in rows:
        po = str(r.get("platformOrderNo") or "").strip()
        st = str(r.get("status") or "").strip()
        if any(s in st for s in OK_STATUS):
            ok.append({"po": po, "status": st, "wh": r.get("sendWhName"),
                       "channel": r.get("logisticsChannelName"),
                       "track": r.get("trackNo")})
        else:
            bad.append({"po": po, "status": st, "err": (r.get("auditCause") or "")[:200]})
    found = {str(r.get("platformOrderNo") or "").strip() for r in rows}
    missing = [p for p in pos if p not in found]

    os.makedirs(out_dir, exist_ok=True)
    rep = {"store": "HX", "total": len(pos), "found": len(found),
           "ok": ok, "fail": bad, "missing": missing, "retry": retry}
    p = os.path.join(out_dir, "hx_oms_ship.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    log("   [结果] 处理中/已发货 %d｜失败 %d｜未查到 %d → %s"
        % (len(ok), len(bad), len(missing), p))
    for b in bad[:10]:
        log("     ✗ %-24s %-10s %s" % (b["po"], b["status"], b["err"][:80]))
    return rep


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                   # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="恒信 领星OMS 批量发货（LM739）")
    ap.add_argument("--pos", default="", help="平台单号，逗号分隔")
    ap.add_argument("--pos-file", default="", help="单号 txt（一行一个）")
    ap.add_argument("--out", default="", help="产物目录")
    ap.add_argument("--dry", action="store_true", help="只搜+勾选+打开分配弹窗，不审核")
    a = ap.parse_args()

    pos = []
    if a.pos:
        pos += [x.strip() for x in a.pos.replace("\n", ",").split(",") if x.strip()]
    if a.pos_file:
        with open(a.pos_file, encoding="utf-8") as f:
            pos += [x.strip() for x in f if x.strip()]
    pos = list(dict.fromkeys(pos))
    if not pos:
        print("[FAIL] 没给单号（--pos / --pos-file）", file=sys.stderr)
        return 2
    out = a.out or os.path.join(HERE, "out", time.strftime("%Y-%m-%d"), "hx_oms")
    rep = run(pos, out, dry=a.dry)
    if a.dry:
        return 0
    bad = len(rep.get("fail") or []) + len(rep.get("missing") or [])
    return 0 if bad == 0 else 42


if __name__ == "__main__":
    raise SystemExit(main())
