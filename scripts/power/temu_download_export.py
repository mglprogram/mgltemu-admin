# -*- coding: utf-8 -*-
"""导出记录下载器（可复用）：等今天那行的状态变「已完成」→ 点「下载」→ 落盘 xlsx。

★ 为什么单独一个脚本：`run_temu_sales.py` 用的是 Playwright 的 `expect_download`，
  而 2026-11-06 起连接改走 `cdp_transport`（没有这个接口）⇒ 每条导出都会卡在
  「等待导出记录完成超时」。紫鸟窗口里 CDP 改下载目录也不生效，文件只会落到
  紫帐默认下载目录：%APPDATA%\\ziniaobrowserdatas\\ziniao browser\\<窗口名>\\。

用法：
    python scripts/power/temu_download_export.py --store BEL --date 2026-11-07
"""
import argparse
import glob
import io
import os
import shutil
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import temu_sales_driver as D  # noqa: E402
from cdp_transport import CdpPage, http_targets  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
EXPORT_URL = "https://agentseller-us.temu.com/mmsos/export-order.html"

JS_ROWS = r"""() => [...document.querySelectorAll('tbody tr')].map(tr =>
  [...tr.children].map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).slice(0, 6))"""


def find_page(port: int):
    ts = [t for t in http_targets(port) if t.get("type") == "page"
          and "export-order" in (t.get("url") or "")]
    if ts:
        return CdpPage(port, ts[0])
    # 没有导出页：拿**本店**的任一 TEMU 页，自己开到导出记录页
    ts = [t for t in http_targets(port) if t.get("type") == "page"
          and "agentseller" in (t.get("url") or "")]
    if not ts:
        return None
    pg = CdpPage(port, ts[0])
    pg.goto(EXPORT_URL)
    time.sleep(8)
    if "export-order" not in (pg.url or ""):
        print("[FAIL] 开导出页被弹回：%s" % (pg.url or "")[:100])
        return None
    return pg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--date", required=True, help="2026-11-07（导出记录里的订单日期）")
    ap.add_argument("--port", type=int, default=58029)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--wait", type=int, default=240, help="最多等多久变成已完成（秒）")
    a = ap.parse_args()

    win_dir = os.path.join(os.environ.get("APPDATA", ""), "ziniaobrowserdatas",
                           "ziniao browser", WINDOWS.get(a.store, a.store))
    out_dir = a.out_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "每日导出销量", a.date, "TEMU")
    dst = os.path.join(out_dir, "%s_订单_%s.xlsx" % (a.store, a.date))

    pg = find_page(a.port)
    if not pg:
        print("[FAIL] 没有可用的 TEMU 页面")
        return 2

    # ① 等今天那行「已完成」
    idx = -1
    dealt = time.time()
    while time.time() - dealt < a.wait:
        rows = pg.evaluate(JS_ROWS) or []
        for i, r in enumerate(rows):
            if len(r) > 4 and a.date in (r[1] or "") and "完成" in (r[4] or ""):
                idx = i
                break
        if idx >= 0:
            break
        time.sleep(4)
        if int(time.time() - dealt) % 20 < 4:
            print("   … 仍在导出中（已等 %ds）" % int(time.time() - dealt))
    if idx < 0:
        print("[FAIL] %ds 内导出记录没有变成「已完成」" % a.wait)
        return 42
    rows = pg.evaluate(JS_ROWS) or []
    print("目标记录：%s | %s" % (rows[idx][0], rows[idx][1]))

    # ② 记下落盘前目录快照
    before = {}
    for f in glob.glob(os.path.join(win_dir, "*.xlsx")) + glob.glob(os.path.join(win_dir, "*.csv")):
        try:
            before[f] = os.path.getmtime(f)
        except OSError:
            pass

    # ③ 真实坐标点「下载」（视口外点击会静默无效）
    box = pg.evaluate(r"""() => {
      const tr = [...document.querySelectorAll('tbody tr')][%d];
      const a = tr.querySelectorAll('td')[5].querySelector('a');
      a.scrollIntoView({block: 'center'});
      const r = a.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
              inView: r.top >= 0 && r.bottom <= innerHeight, text: (a.innerText || '').trim()};
    }""" % idx) or {}
    print("下载链接：", box)
    if not box.get("inView") or box.get("text") != "下载":
        print("[FAIL] 下载链接不在视口或文字不符，拒绝点击")
        return 42
    pg.dispatch_mouse(box["x"], box["y"])

    # ④ 扫紫鸟默认下载目录
    newest = ""
    for i in range(45):
        time.sleep(2)
        cands = []
        for f in glob.glob(os.path.join(win_dir, "*.xlsx")) + glob.glob(os.path.join(win_dir, "*.csv")):
            try:
                mt = os.path.getmtime(f)
            except OSError:
                continue
            if before.get(f) != mt and not os.path.basename(f).startswith("~$"):
                cands.append((mt, f))
        if cands:
            newest = max(cands)[1]
            print("   第 %ds 新文件：%s (%d bytes)" % ((i + 1) * 2, os.path.basename(newest),
                                                      os.path.getsize(newest)))
            break
    if not newest:
        print("[FAIL] 90s 内下载目录没有新文件：%s" % win_dir)
        return 42

    # ⑤ 落盘（CSV → xlsx）
    os.makedirs(out_dir, exist_ok=True)
    if newest.lower().endswith(".csv"):
        ok, n = D._csv_to_xlsx(newest, dst)
        if not ok:
            print("[FAIL] CSV→xlsx 失败")
            return 42
        print("[OK] 落盘 %s（%d 行）" % (dst, n))
    else:
        shutil.copy2(newest, dst)
        print("[OK] 落盘 %s" % dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
