# -*- coding: utf-8 -*-
"""BEL 批量改库存：一次弹窗里把所有尺码都改掉（不再逐尺码开关弹窗）。

    python scripts/goods/bel_stock_batch.py --spu 43933794478 --mode add --value 20          # dry-run
    python scripts/goods/bel_stock_batch.py --spu 43933794478 --mode add --value 20 --apply

口径：
  · mode=add   每个尺码「增加 value」（弹窗里全选，批量区「增加 + value」）
  · mode=set   每个尺码「设为目标值」（逐行算 增加/减少 |差|）
  · 只填「库存在途/预售」等其它输入框之外的那一个「请输入」，逐行回读核对后才提交
  · 提交后重开弹窗，按服务端「当前库存」逐行判定成功
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


async def rows_of(tab) -> dict:
    """读弹窗里每个尺码行：属性集 / 当前库存 / 方向 / 已填数量 / 修改后库存 / 是否勾选。"""
    return await B.ev_json(tab, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var rows = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).map(function (tr, i) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var attr = '';
  for (var k = 0; k < cells.length; k++) {
    if (/^[^\s]+-(S|M|L|XL|XXL|3XL|4XL)$/.test(cells[k])) { attr = cells[k]; break; }
  }
  var ai = cells.indexOf(attr);
  var cur = '', after = '';
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var n = ins.filter(function (x) { return x.placeholder === '请输入'; })[0];
  var qtyTd = n ? n.closest('td') : null;
  if (ai >= 0) {
    for (var j = ai + 1; j < cells.length; j++) { if (/^\d+$/.test(cells[j])) { cur = cells[j]; break; } }
    // 修改后库存 = qty 输入框所在 td 的右邻（「库存过低，存在风险」提示会插在前面，不能按数字数）
    if (qtyTd && qtyTd.nextElementSibling) after = t(qtyTd.nextElementSibling);
    if (!after || after === '请输入') {
      after = '';
      for (var q = ai + 2; q < cells.length; q++) {
        if (/^\d+$/.test(cells[q]) || cells[q] === '-') { after = cells[q]; break; }
      }
    }
  }
  var d = ins.filter(function (x) { return x.type === 'text' && x.placeholder !== '请输入'; })[0];
  var cb = tr.querySelector('input[type=checkbox]');
  return {i: i, attr: attr, cur: cur, dir: d ? d.value : '', qty: n ? n.value : '',
          after: after, checked: cb ? cb.checked : null};
});
return JSON.stringify({ok: true, rows: rows});""")


async def fill_all_rows(tab, plan: dict) -> str:
    """一行一次：每行点「增减」箭头选方向、写数量（同一弹窗内，不开关弹窗）。

    实测：顶部「批量增减库存」区填了不会套到每一行（填 20 后各行「修改后库存」仍是 0），
    所以必须逐行填；勾选框默认全选，保持不动。plan = {属性集: (方向, 数量)}。
    """
    done, miss = [], []
    for attr, (direction, qty) in plan.items():
        if qty == 0:
            continue
        r = await B.set_row(tab, attr, direction, str(qty))
        (miss if "❌" in r else done).append("%s→%s%d" % (attr, direction, qty))
    return "填好 %d 行 / 失败 %d 行；失败：%s" % (len(done), len(miss), ",".join(miss) or "无")


async def run(store: str, spu: str, mode: str, value: int, apply: bool) -> int:
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 窗口" % store)
        return 3
    tid = await cdp_net.new_tab(port, B.URL, foreground=True)  # noqa: tab-safety 前台必需，跑完关
    tab = B.OwnTab(port, tid)
    print("本次自建页 target:", str(tid)[:16])
    try:
        if not await tab.open():
            print("!! 新页没枚举出来（不 fallback 到用户的页）")
            return 3
        await asyncio.sleep(12)
        await zreact.inject(tab.session)
        if not await B.page_ready(tab):
            print("!! 列表没加载出来")
            return 5
        print("① 查询条件 → SPU:", await B.pick_query_type(tab, "SPU"))
        print("   查询:", await B.search(tab, spu))
        print("② 打开「修改库存」:", await B.open_stock_dialog(tab, spu))
        d = await rows_of(tab)
        if not d.get("ok"):
            print("!! 弹窗没打开")
            return 4
        base = {r["attr"]: int(r["cur"] or 0) for r in d["rows"]}
        print("   当前：", base)

        if mode == "add":
            want = {a: c + value for a, c in base.items()}
            plan = {a: ("增加", value) for a in base}
        else:
            want = {a: value for a in base}
            plan = {a: ("减少" if value < c else "增加", abs(value - c))
                    for a, c in base.items() if c != value}
        print("③ 逐行填写：", await fill_all_rows(tab, plan))

        d2 = await rows_of(tab)
        print("④ 提交前回读：")
        bad = 0
        for r in d2.get("rows", []):
            w = want.get(r["attr"])
            ok = (r["after"] == str(w))
            bad += 0 if ok else 1
            print("   %-10s 当前=%-4s 增减=%-4s 填=%-4s 修改后=%-4s 应=%-4s %s"
                  % (r["attr"], r["cur"], r["dir"], r["qty"], r["after"], w, "OK" if ok else "**不一致**"))
        if bad:
            print("\n!! %d 行与计划不一致 ⇒ 不提交" % bad)
            print("   取消:", await B.close_dialog(tab))
            return 6
        if not apply:
            print("   dry-run 截图:", await B.shot(tab, "bel_batch_%s_dry.png" % spu))
            print("⑤ (dry-run：不点确认) 取消:", await B.close_dialog(tab))
            return 0

        print("⑤ 提交:", await B.confirm(tab))
        await asyncio.sleep(3)
        if not await B.dialog_open(tab):
            await B.open_stock_dialog(tab, spu)
        d3 = await rows_of(tab)
        ok_all = True
        for r in d3.get("rows", []):
            w = want.get(r["attr"])
            ok = str(r["cur"]) == str(w)
            ok_all = ok_all and ok
            print("   %-10s 服务端当前库存=%-4s 期望=%-4s %s"
                  % (r["attr"], r["cur"], w, "✅" if ok else "❌"))
        print("\n结论:", "✅ 全部生效" if ok_all else "❌ 有尺码没生效")
        return 0 if ok_all else 7
    finally:
        await tab.close()
        print("已关掉本次开的页")


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 弹窗内批量改库存（默认 dry-run）")
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--spu", required=True)
    ap.add_argument("--mode", choices=("add", "set"), default="add")
    ap.add_argument("--value", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(a.store.upper(), a.spu.strip(), a.mode, a.value, a.apply))


if __name__ == "__main__":
    raise SystemExit(main())
