# -*- coding: utf-8 -*-
"""TEMU「下载查询结果」官方导出通道 —— 一次导出「在售中」全量表格。

为什么要有它（用户 2026-10-12 指出）：
    商品列表页右上角有官方「下载查询结果N」按钮，点开是勾选字段的弹窗
    （商品标题 / SPU ID / SKC ID / SKU ID / SKC货号 / SKU货号 / 叶子类目 / 经营站点 /
     商品状态 / 申报价格 / 库存 / 创建时间 → 导出）。
    与「翻页抓 pageQuery 响应」（run_goods_on_sale.py）相比，官方导出**一次请求**、
    字段由平台保证，接口内部结构变了也不受影响 —— 更稳的长期通道。

本脚本做三件事（只读 + 一次下载，绝不点任何改商品的写操作）：
    1. 店铺窗口 → 商品列表页 → 切「在售中」；
    2. 点「下载查询结果」→ 弹窗勾「全部商品信息」→ 点「导出」；
    3. 轮询浏览器下载目录，抓到新出现的 xlsx/csv → 复制到 data/goods/。

用法：
    python scripts/goods/run_goods_export.py --store BEL
    python scripts/goods/run_goods_export.py --store all --timeout 420

产物：data/goods/<store>_goods_export.<ext>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zpage  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT_DIR = ROOT / "data" / "goods"

STORES = {"BEL": "27677489075954", "BHY": "27697436126374"}
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
URL = "https://agentseller.temu.com/goods/list"

EXTS = (".xlsx", ".xls", ".csv")
#: 平台导出的文件名（实测就是「商品基础信息.xlsx」，与老的 TEMU 导出同名）
NAME_HINTS = ("商品基础信息", "商品", "goods", "product", "sku", "查询结果", "导出")


def store_dir(store: str) -> Path:
    """紫鸟把该店窗口的下载**直接存进店铺数据目录**（实测 2026-10-12）。

    不是系统 Downloads：`%APPDATA%\\ziniaobrowserdatas\\ziniao browser\\<窗口名>\\`。
    """
    base = Path(os.environ.get("APPDATA", "")) / "ziniaobrowserdatas" / "ziniao browser"
    return base / WINDOWS[store]


def download_dirs(store: str) -> list[Path]:
    """候选下载目录：店铺数据目录优先，其后才是系统 Downloads/Desktop。"""
    cands = [store_dir(store),
             Path(os.environ.get("USERPROFILE", "")) / "Downloads",
             Path(os.environ.get("USERPROFILE", "")) / "Desktop"]
    return [d for d in cands if d.exists()]


def snap(store: str) -> dict:
    """{绝对路径: mtime} —— 只看候选目录里的表格文件。"""
    out = {}
    for d in download_dirs(store):
        for p in d.glob("*"):
            try:
                if p.suffix.lower() in EXTS and not p.name.startswith("~$"):
                    out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


def wait_new_file(store: str, before: dict, timeout: float, log=print):
    """轮询候选目录，返回新出现的导出文件（优先命中文件名含「商品基础信息」的）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        now = snap(store)
        fresh = [(p, m) for p, m in now.items() if before.get(p) != m]
        if fresh:
            hinted = [x for x in fresh if any(h in Path(x[0]).name for h in NAME_HINTS)]
            pool = hinted or fresh
            pool.sort(key=lambda x: x[1], reverse=True)
            return Path(pool[0][0])
    return None


# ---------------------------------------------------------------- 页面动作
JS_CLICK_TEXT = r"""
(function (want) {
  try {
    var nodes = Array.prototype.slice.call(document.querySelectorAll('*')).filter(function (e) {
      return (e.innerText || '').trim() === want && e.getClientRects().length > 0;
    });
    nodes = nodes.filter(function (e) {
      return !nodes.some(function (o) { return o !== e && e.contains(o); });
    });
    if (!nodes.length) return JSON.stringify({ok: false, reason: 'not-found', want: want});

    var target = nodes[0];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i], p = n.parentElement;
      if (n.tagName === 'BUTTON' || n.tagName === 'LI' ||
          (p && (p.tagName === 'LI' || p.tagName === 'BUTTON'))) { target = n; break; }
    }
    target.click();
    return JSON.stringify({ok: true, tag: target.tagName,
      cls: String(target.className).slice(0, 60), count: nodes.length});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

#: 点「在售中 116」这类带数字的状态 tab（页面上的 tab 文本带数量，精确匹配会落空）
JS_CLICK_STATUS_TAB = r"""
(function () {
  try {
    var hit = [];
    Array.prototype.slice.call(document.querySelectorAll('*')).forEach(function (e) {
      var t = (e.innerText || '').trim();
      if (/^在售中\s*\d*$/.test(t) && e.getClientRects().length > 0) hit.push(e);
    });
    hit = hit.filter(function (e) {
      return !hit.some(function (o) { return o !== e && e.contains(o); });
    });
    if (!hit.length) return JSON.stringify({ok: false, reason: 'no-status-tab'});
    hit[0].click();
    return JSON.stringify({ok: true, text: (hit[0].innerText || '').trim().slice(0, 20),
      cls: String(hit[0].className).slice(0, 50)});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

#: 点「下载查询结果88」这类带数字的按钮（文本以它开头即命中）
JS_CLICK_DOWNLOAD = r"""
(function () {
  try {
    var hit = [];
    Array.prototype.slice.call(document.querySelectorAll('button,a,span,div')).forEach(function (e) {
      var t = (e.innerText || '').trim();
      if (t.indexOf('下载查询结果') === 0 && t.length < 24 && e.getClientRects().length > 0) {
        hit.push(e);
      }
    });
    hit = hit.filter(function (e) {
      return !hit.some(function (o) { return o !== e && e.contains(o); });
    });
    if (!hit.length) return JSON.stringify({ok: false, reason: 'no-download-button'});
    hit[0].click();
    return JSON.stringify({ok: true, text: (hit[0].innerText || '').trim()});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

#: 弹窗里「全部商品信息」勾选框 → 全选字段
JS_CHECK_ALL = r"""
(function () {
  try {
    var boxes = Array.prototype.slice.call(document.querySelectorAll('input[type=checkbox]'));
    if (!boxes.length) return JSON.stringify({ok: false, reason: 'no-checkbox'});
    var done = 0;
    boxes.forEach(function (b) {
      var isAll = false;
      try {
        var label = b.closest('label') || b.parentElement;
        isAll = /全部商品信息/.test(label ? (label.innerText || '') : '');
      } catch (e) {}
      if (!isAll && !b.checked) { b.click(); done++; }
    });
    var labels = boxes.map(function (b) {
      var l = b.closest('label') || b.parentElement;
      return l ? (l.innerText || '').trim().slice(0, 12) : '';
    }).filter(Boolean);
    return JSON.stringify({ok: true, checked: done, total: boxes.length, labels: labels.slice(0, 20)});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

#: 弹窗底部「导出」按钮：先锚定弹窗容器，再取其中的**主按钮**点击。
#: 2026-10-12 实测结构：span(导出) → button.BTN_primary → div.download-product-info_footer__
#:   → div.download-product-info_container__ → div.MDL_body/MDL_inner/MDL_container。
#: 直接按文本找「导出」会命中页面上别处的同名元素，所以用**容器类名锚定**，
#: 兜底再走「离标题最近的含 button 的祖先」。
JS_CLICK_DIALOG_OK = r"""
(function () {
  try {
    var box = document.querySelector('[class*=download-product-info_container]');
    if (!box) {
      // 兜底：找文本为「共查询到 N 个 SKC」的节点，往上 4 层拿容器
      var marker = null;
      Array.prototype.slice.call(document.querySelectorAll('*')).forEach(function (e) {
        if (marker) return;
        var t = (e.innerText || '').trim();
        if (/^共查询到\s*\d+\s*个\s*SKC/.test(t) && t.length < 40) marker = e;
      });
      var n = marker;
      for (var i = 0; i < 4 && n; i++) n = n.parentElement;
      box = n;
    }
    if (!box) return JSON.stringify({ok: false, reason: 'no-dialog-box'});

    var btns = Array.prototype.slice.call(box.querySelectorAll('button')).filter(function (b) {
      return (b.innerText || '').trim() === '导出' && b.getClientRects().length > 0;
    });
    if (!btns.length) return JSON.stringify({ok: false, reason: 'no-ok-button'});
    var target = btns[btns.length - 1];
    target.click();
    return JSON.stringify({ok: true, tag: target.tagName,
      cls: String(target.className).slice(0, 60)});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_DIALOG_TEXT = r"""
(function () {
  try {
    // ⚠ 不要用「下载查询结果」当锚点：列表页那个按钮同样含这段文字，
    //   而且它排在弹窗前面，会截到按钮周围的页面文本（2026-10-12 实际误判过一次）。
    //   弹窗唯一可信锚点是字段勾选区里的「全部商品信息」。
    var t = (document.body.innerText || '').replace(/\s+/g, ' ');
    var boxes = document.querySelectorAll('input[type=checkbox]').length;
    var i = t.indexOf('全部商品信息');
    return JSON.stringify({
      open: i >= 0, checkboxes: boxes,
      seg: i >= 0 ? t.slice(Math.max(0, i - 60), i + 240) : t.slice(0, 160),
    });
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


def click_text(store_id: str, text: str) -> dict:
    raw = zpage.page_exec_str(JS_CLICK_TEXT, store_id, timeout=90)
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": str(raw)[:200]}


def page_dialog(store_id: str) -> dict:
    raw = zpage.page_exec_str(JS_DIALOG_TEXT, store_id, timeout=90)
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": str(raw)[:200]}


def download_dir() -> Path:
    """兼容保留：系统 Downloads 兜底目录。"""
    cands = [Path(os.environ.get("USERPROFILE", "")) / "Downloads",
             Path(os.environ.get("USERPROFILE", "")) / "Desktop"]
    for d in cands:
        if d.exists():
            return d
    return cands[0]


def snap_legacy(d: Path) -> dict:
    out = {}
    for p in d.glob("*"):
        try:
            if p.suffix.lower() in EXTS:
                out[p.name] = p.stat().st_mtime
        except OSError:
            pass
    return out


def run_store(store: str, timeout: float, log=print) -> dict:
    sid = STORES[store]
    log(f"[{store}] 打开商品列表页 …")
    rc, out = zpage.page_visit(sid, URL, timeout_ms=90000)
    log(f"[{store}] 导航 rc={rc} {str(out)[:80]}")
    for _ in range(12):
        time.sleep(3)
        if "goods/list" in zpage.current_url(sid):
            break
    time.sleep(8)

    log(f"[{store}] 切到「在售中」…")
    log(f"   {zpage.page_exec_str(JS_CLICK_STATUS_TAB, sid, timeout=90)}")
    time.sleep(6)

    dirs = download_dirs(store)
    before = snap(store)
    log(f"[{store}] 下载目录：{' | '.join(str(d) for d in dirs)}（原有 {len(before)} 个表格）")

    log(f"[{store}] 点「下载查询结果」…")
    log(f"   {zpage.page_exec_str(JS_CLICK_DOWNLOAD, sid, timeout=90)}")
    time.sleep(3)
    log(f"   弹窗：{str(page_dialog(sid).get('seg'))[:160]}")

    log(f"[{store}] 勾选字段…")
    log(f"   {zpage.page_exec_str(JS_CHECK_ALL, sid, timeout=90)}")
    time.sleep(1)

    log(f"[{store}] 点「导出」…")
    log(f"   {zpage.page_exec_str(JS_CLICK_DIALOG_OK, sid, timeout=90)}")

    log(f"[{store}] 等文件落地（最多 {int(timeout)}s）…")
    f = wait_new_file(store, before, timeout, log)
    if not f:
        return {"store": store, "error": "超时未拿到导出文件"}

    ext = f.suffix.lower()
    dest = OUT_DIR / f"{store.lower()}_goods_export{ext}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, dest)
    log(f"[{store}] 已保存 {dest}  ({dest.stat().st_size/1024:.0f} KB)  来源 {f}")
    return {"store": store, "file": str(dest), "size": dest.stat().st_size,
            "source": str(f), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def check_only(stores: list[str]) -> int:
    """端到端自检：走到「导出」按钮可见为止，不真的导出（不产生下载记录）。"""
    bad = 0
    for store in stores:
        sid = STORES[store]
        print(f"[{store}] 自检 …")
        rc, out = zpage.page_visit(sid, URL, timeout_ms=90000)
        print(f"   导航 rc={rc}")
        for _ in range(12):
            time.sleep(3)
            if "goods/list" in zpage.current_url(sid):
                break
        time.sleep(8)
        tab = zpage.page_exec_str(JS_CLICK_STATUS_TAB, sid, timeout=90)
        print(f"   在售中 tab：{tab}")
        time.sleep(4)
        dlg = zpage.page_exec_str(JS_CLICK_DOWNLOAD, sid, timeout=90)
        print(f"   下载查询结果按钮：{dlg}")
        time.sleep(3)
        seg = page_dialog(sid)
        seg_txt = str(seg.get("seg") or "")
        ok1 = bool(seg.get("open")) and int(seg.get("checkboxes") or 0) >= 10
        print(f"   弹窗出现：{ok1}（勾选框 {seg.get('checkboxes')} 个）")
        chk = zpage.page_exec_str(JS_CHECK_ALL, sid, timeout=90)
        print(f"   字段勾选：{chk}")
        try:
            chk_ok = int(json.loads(chk or "{}").get("total") or 0) >= 10
        except Exception:  # noqa: BLE001
            chk_ok = False
        if not (ok1 and chk_ok and '"ok":true' in str(dlg)):
            bad += 1
    print()
    print("自检结论：" + ("全部通过（导出按钮链路可达）" if not bad else f"{bad} 个店铺自检不通过"))
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 官方「下载查询结果」导出在售中")
    ap.add_argument("--store", default="BEL", help="BEL / BHY / all")
    ap.add_argument("--timeout", type=float, default=300, help="等文件落地的秒数")
    ap.add_argument("--check", action="store_true",
                    help="只做端到端自检：导航→切在售中→开弹窗→勾字段，到「导出」前停手")
    a = ap.parse_args()

    stores = list(STORES) if a.store.lower() == "all" else [a.store.upper()]
    if a.check:
        return check_only(stores)

    results = []
    for s in stores:
        if s not in STORES:
            print(f"!! 未知店铺 {s}")
            return 2
        try:
            results.append(run_store(s, a.timeout))
        except Exception as e:  # noqa: BLE001
            print(f"!! [{s}] 导出失败: {str(e)[:300]}")
            results.append({"store": s, "error": str(e)[:300]})

    print()
    for r in results:
        if "error" in r:
            print(f"  [{r['store']}] 失败：{r['error']}")
        else:
            print(f"  [{r['store']}] OK  {r['file']}")
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
