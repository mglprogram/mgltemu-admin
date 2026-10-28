# -*- coding: utf-8 -*-
"""按**平台单号**核对领星 OMS 的真实发货状态（**必须查两个账号**）。

用户口径（2026-10-28）：
  「可以去两个领星账号去确认你点击确认的订单是否真的已经发货了……
    如果显示待处理就是未发货，但是你要查两个账号才能确定有没有发货，
    如果两个店铺都显示待处理那就是真的只是买了面单」

为什么要查两个账号：领星的订单按店铺分在不同账号下（实测：同一批 PO 号会
**分散在 LM739 与 14450 两个账号**里，每个单只出现在其中一个）。

用法
----
凭据走环境变量（**不要写进文件**）：

    $env:OMS_APP_KEY="…"; $env:OMS_APP_SECRET="…"
    python scripts\\oms\\check_po_status.py --tag LM739 --pos PO-211-xxx,PO-211-yyy

一次查两个账号（凭据用 `账号名=key:secret` 形式给出）：

    python scripts\\oms\\check_po_status.py `
        --account "LM739=2c1b…:5819…" --account "14450=693e…:0e8e…" `
        --pos PO-211-xxx,PO-211-yyy

退出码：0 已明确（全部找到且状态一致）/ 1 有单在两个账号里都没找到（结论不可下）/ 2 出错
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from oms_client import OmsClient, OmsError  # noqa: E402

# 状态号 → 人话（实测 2026-10-28：2 = 仓库处理中）
STATUS_TEXT = {
    1: "待处理（未确认/未发货）",
    2: "仓库处理中",
    3: "已出库",
    4: "已取消",
    5: "异常",
}


def scan_account(tag: str, key: str, secret: str, pos: list[str],
                 start: str, end: str, page_size: int = 50) -> dict:
    """在**一个**账号里扫窗口内订单，返回 {PO: record}。"""
    c = OmsClient(app_key=key, app_secret=secret, verbose=False)
    found, total, page = {}, None, 1
    while True:
        d = c.list_page(page, page_size, "orderCreateTime", start, end)
        recs = d.get("records") or []
        if total is None:
            total = int(d.get("total") or 0)
        for r in recs:
            p = str(r.get("platformOrderNo") or "").strip()
            if p in pos:
                found[p] = r
        if not recs or page * page_size >= (total or 0):
            break
        page += 1
    # 详情才有 statusName / 店铺名
    det = {}
    outs = [r.get("outboundOrderNo") for r in found.values() if r.get("outboundOrderNo")]
    if outs:
        try:
            for d0 in c.details(outs):
                det[str(d0.get("outboundOrderNo"))] = d0
        except OmsError as e:
            print("   [%s] 详情取失败（不阻断）：%s" % (tag, str(e)[:100]))
    for p, r in found.items():
        d0 = det.get(str(r.get("outboundOrderNo"))) or {}
        r["_statusName"] = d0.get("statusName") or r.get("statusName") or ""
        r["_storeName"] = d0.get("storeName") or r.get("storeName") or ""
    print("  [%s] 窗口内共 %d 单 → 命中 %d / %d" % (tag, total or 0, len(found), len(pos)))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="按平台单号核对领星 OMS 发货状态（双账号）")
    ap.add_argument("--pos", required=True, help="平台单号，逗号分隔")
    ap.add_argument("--account", action="append", default=[],
                    help="账号：名字=appKey:appSecret（可多次给；不给则用环境变量单账号）")
    ap.add_argument("--tag", default="", help="单账号模式下的显示名（缺省 env）")
    ap.add_argument("--start", default="", help="窗口开始日 YYYY-MM-DD（缺省 今天-3）")
    ap.add_argument("--end", default="", help="窗口结束日 YYYY-MM-DD（缺省 今天）")
    ap.add_argument("--json", default="", help="把结果写成 json 到这个路径")
    a = ap.parse_args()

    pos = [x.strip() for x in a.pos.split(",") if x.strip()]
    if not pos:
        print("[FAIL] --pos 为空", file=sys.stderr)
        return 2

    import datetime as _dt
    today = _dt.date.today()
    start = (a.start or (today - _dt.timedelta(days=3)).isoformat()) + " 00:00:00"
    end = (a.end or today.isoformat()) + " 23:59:59"

    accounts: list[tuple[str, str, str]] = []
    for spec in a.account:
        name, _, rest = spec.partition("=")
        k, _, s = rest.partition(":")
        if not (name and k and s):
            print("[FAIL] --account 格式应为 名字=appKey:appSecret，收到：%s" % spec[:40],
                  file=sys.stderr)
            return 2
        accounts.append((name, k, s))
    if not accounts:
        k, s = os.environ.get("OMS_APP_KEY"), os.environ.get("OMS_APP_SECRET")
        if not (k and s):
            print("[FAIL] 没给 --account，也没有 OMS_APP_KEY / OMS_APP_SECRET", file=sys.stderr)
            return 2
        accounts.append((a.tag or "env", k, s))

    print("平台单号 %d 个；账号 %d 个；窗口 %s ~ %s"
          % (len(pos), len(accounts), start[:10], end[:10]))

    per = {}
    for tag, k, s in accounts:
        try:
            per[tag] = scan_account(tag, k, s, pos, start, end)
        except OmsError as e:
            print("[FAIL] 账号 %s：%s" % (tag, e), file=sys.stderr)
            return 2

    # ---- 汇总 ----
    print("\n%s" % ("=" * 104))
    hdr = "%-28s" % "平台单号"
    for tag, _, _ in accounts:
        hdr += " %-26s" % ("[%s] 状态" % tag)
    hdr += " 结论"
    print(hdr)
    print("-" * 104)
    verdict_rows, unfound = [], []
    for p in pos:
        line = "%-28s" % p
        got = []
        for tag, _, _ in accounts:
            r = per[tag].get(p)
            if r:
                sn = r.get("_statusName") or STATUS_TEXT.get(r.get("status"), "?")
                out_t = r.get("outboundTime") or ""
                got.append((tag, r, sn, out_t))
                line += " %-26s" % ("%s%s" % (sn, "" if out_t else "·未出库"))[:26]
            else:
                line += " %-26s" % "—"
        if not got:
            line += " ❓两个账号都没有"
            unfound.append(p)
        else:
            # 判据：只要有一条出库时间非空 ⇒ 已真正出库；否则都只是"买了面单/等仓库"
            shipped = any(o for _, _, _, o in got)
            line += " %s" % ("✅ 已出库" if shipped else "📦 仅已买面单（未出库）")
        print(line)
        verdict_rows.append({"po": p,
                             "accounts": [{"tag": t, "status": r.get("status"),
                                           "statusName": sn,
                                           "track": r.get("logisticsTrackNo"),
                                           "outboundTime": o}
                                          for t, r, sn, o in got]})
    print("=" * 104)

    if unfound:
        print("\n⚠ 有 %d 单在**两个账号里都没找到** ⇒ 不能下结论（可能窗口外/别的店铺）："
              % len(unfound))
        for p in unfound:
            print("   %s" % p)
    else:
        print("\n✅ 全部 %d 单都在某个账号里找到了（每个单只属于其中一个店铺）" % len(pos))

    if a.json:
        import json
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"start": start, "end": end,
                       "accounts": [t for t, _, _ in accounts],
                       "rows": verdict_rows}, f, ensure_ascii=False, indent=2)
        print("明细 -> %s" % a.json)

    return 1 if unfound else 0


if __name__ == "__main__":
    raise SystemExit(main())
