# -*- coding: utf-8 -*-
"""领星 OMS「异常」出库单导出 —— 每个领星账号一张表，只留 BHY / BEL。

用户口径（2026-10-29）
----------------------
「OMS → 待处理 → 异常 → 筛选店铺 BHY/BEL → 导出，一个账号一张表，
  命名 `<账号>-<日期>`，只筛选 BHY/BEL 的异常订单。」

产物
----
    D:\\together\\Last_mile\\失败订单\\<YYYY-MM-DD>\\<账号>-<YYYY-MM-DD>.xlsx
    sheet 1「异常订单」  —— 逐单明细（只有 BHY/BEL 的行）
    sheet 2「店铺分布」  —— 该账号窗口内**所有**店铺的异常条数（用来发现别的店爆量）

用户口径（2026-10-29 追加）：「文件都放在 D:\\together\\Last_mile\\失败订单\\日期文件夹」
⇒ 默认按**日期子文件夹**归档；`--flat` 可退回直接放根目录。

用户口径（2026-10-30）：「如果两个店铺都没有异常订单就不导表到指定文件夹」
⇒ 某个账号 0 行就**不写表**；两边都 0 行则连日期目录都不建（不留空表、不留空目录）。
   `--write-empty` 可恢复「写只有表头的空表」的旧行为（排查用）。

为什么按 `status == 5` 而不是 `exceptionTime 非空`
--------------------------------------------------
界面那个 `异常(N)` 页签就是 `status == 5`。`exceptionTime` 非空会把**后来已恢复**
的单也带进来（实测 2026-10-29：LM739 43 条 exceptionTime 里只有 8 条还是异常，
其余是已取消/仓库处理中）—— 那份表会把已恢复的单谎报成异常。

为什么必须下钻详情
------------------
列表接口的 `storeName` **恒为空**（见 lib/oms_client.py 的注释），店铺名只在
`/outboundOrder/detail` 里 —— 想按 BHY/BEL 过滤只能按 50 个一批批量取详情。
详情同时带来 `异常原因`。

用法
----
    python scripts\\oms\\export_abnormal_orders.py                    # 默认近 7 天，BHY+BEL
    python scripts\\oms\\export_abnormal_orders.py --days 1           # 只看今天
    python scripts\\oms\\export_abnormal_orders.py --stores BHY       # 只要一个店
    python scripts\\oms\\export_abnormal_orders.py --all-stores       # 不过滤店铺
    python scripts\\oms\\export_abnormal_orders.py --date 2026-10-28  # 指定结束日

退出码
------
    0 全部账号都成功（**含「0 条异常、因此没有导表」**——查到了就算成功，别把安静的一天报成故障）
    1 部分账号失败（其余账号的表已写出，可交付）
    2 全部失败 / 没有可用凭证 / 写盘失败
    3 全部写出，但至少一个目标文件**正被 Excel 占用** → 已另存为
      `<账号>-<日期>-<HHMM>.xlsx`（业务告警，不是故障；占用是常态，见 write_with_fallback）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, os.path.dirname(HERE))          # scripts/ → notify_push

try:  # 被计划任务/管道捕获时统一 UTF-8，避免 GBK 乱码
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from oms_client import OmsClient, OmsError  # noqa: E402

#: 双账号配置（**不入库**，被 .gitignore 的 `*local.json` 挡住）。
ACCOUNTS_FILE = os.path.join(HERE, "oms_accounts.local.json")

#: 默认落盘目录（用户指定的「失败订单」文件夹）
DEFAULT_OUT = r"D:\together\Last_mile\失败订单"

#: 领星 status 号 → 人话
STATUS_TXT = {0: "未知", 1: "待处理", 2: "仓库处理中", 3: "已出库", 4: "已取消", 5: "异常"}
ABNORMAL_STATUS = 5

COLS = ["账号", "店铺", "出库单号", "平台单号", "参考单号", "状态", "异常时间",
        "仓库", "物流渠道", "物流商", "运单号", "出库时间", "创建时间", "异常原因"]
WIDTHS = [9, 9, 22, 26, 22, 12, 20, 12, 26, 12, 26, 20, 20, 46]


# --------------------------------------------------------------------------
# 凭证
# --------------------------------------------------------------------------
def load_accounts(spec_list, accounts_file: str):
    """账号来源：--account 名字=key:secret > 本地 json > 环境变量。返回 [(tag,k,s)]。"""
    out = []
    for spec in spec_list:
        name, _, rest = spec.partition("=")
        k, _, s = rest.partition(":")
        if not (name and k and s):
            raise SystemExit("[FAIL] --account 格式应为 名字=appKey:appSecret，收到：%s"
                             % spec[:40])
        out.append((name, k, s))
    if out:
        return out
    if os.path.exists(accounts_file):
        with open(accounts_file, encoding="utf-8") as f:
            d = json.load(f)
        for it in (d.get("accounts") or []):
            if it.get("tag") and it.get("appKey") and it.get("appSecret"):
                out.append((it["tag"], it["appKey"], it["appSecret"]))
        if out:
            print("[凭证] 从 %s 读入 %d 个账号：%s"
                  % (os.path.basename(accounts_file), len(out),
                     ", ".join(t for t, _, _ in out)))
    if out:
        return out
    k, s = os.environ.get("OMS_APP_KEY"), os.environ.get("OMS_APP_SECRET")
    if k and s:
        return [("env", k, s)]
    raise SystemExit("[FAIL] 没有可用凭证：--account / %s / OMS_APP_KEY 都没有"
                     % accounts_file)


# --------------------------------------------------------------------------
# 单账号扫描
# --------------------------------------------------------------------------
def dest_dir_for(root: str, day: str, flat: bool = False) -> str:
    """实际落盘目录。默认 `<root>\\<YYYY-MM-DD>\\`（用户 2026-10-29 要求按日期归档）。"""
    return root if flat else os.path.join(root, day)


def match_store(shop_raw: str, stores: list[str]) -> str:
    """店铺名 → 命中的标签（'' = 不写进表）。

    领星里同一品牌有多种写法（`BEL` / `Temu-BEL` / `BEL-2`），所以用**子串**匹配，
    大小写不敏感。`stores` 必须已大写。空 `stores` = 全部店铺（--all-stores）。
    """
    if not stores:
        return "ALL"
    up = (shop_raw or "").upper()
    for st in stores:
        if st and st in up:
            return st
    return ""


def _detail_reason(d0: dict) -> str:
    """详情里的异常原因，按候选字段依次取（接口不同版本字段名不一样）。"""
    for k in ("exceptionRemark", "abnormalRemark", "exceptionReason",
              "statusRemark", "remark"):
        v = d0.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def scan_account(tag: str, key: str, secret: str, start: str, end: str,
                 stores: list[str], page_size: int = 50,
                 workers: int = 6) -> dict:
    """拉一个账号窗口内**状态=异常**的单 → 补详情 → 按店铺过滤。"""
    c = OmsClient(app_key=key, app_secret=secret, verbose=False)

    recs = c.all_pages(start, end, "orderCreateTime", page_size=page_size,
                       workers=workers,
                       progress=lambda d, n: print("  [%s] 分页 %d/%d" % (tag, d, n)))

    # ★ 只要「异常」状态（= 界面 异常(N) 页签）。见模块 docstring。
    abn = [r for r in recs if int(r.get("status") or 0) == ABNORMAL_STATUS]
    print("  [%s] 窗口内 %d 单 → status=异常 %d 条" % (tag, len(recs), len(abn)))

    # 详情补 店铺 / 异常原因（列表接口 storeName 恒为空）
    det, nos = {}, [r.get("outboundOrderNo") for r in abn if r.get("outboundOrderNo")]
    for i in range(0, len(nos), 50):
        chunk = nos[i:i + 50]
        try:
            for d0 in c.details(chunk):
                det[str(d0.get("outboundOrderNo"))] = d0
        except OmsError as e:
            # 详情缺一批不该让整张表作废：该批的店铺名查不到 → 会走 r.storeName（空）
            # 于是归到「未知店铺」，日志里明确写出来，别静默丢
            print("  [%s] 详情批 %d/%d 失败（该批店铺名可能缺失）：%s"
                  % (tag, i // 50 + 1, (len(nos) + 49) // 50, str(e)[:100]))

    shops: dict[str, int] = {}
    rows, unknown = [], 0
    for r in abn:
        d0 = det.get(str(r.get("outboundOrderNo"))) or {}
        shop_raw = str(d0.get("storeName") or r.get("storeName") or "").strip()
        shops[shop_raw or "(空)"] = shops.get(shop_raw or "(空)", 0) + 1

        hit = match_store(shop_raw, stores)
        if not hit:
            unknown += 1
            continue
        rows.append([
            tag, hit,
            r.get("outboundOrderNo"), r.get("platformOrderNo"), r.get("referOrderNo"),
            STATUS_TXT.get(int(r.get("status") or 0), str(r.get("status"))),
            r.get("exceptionTime"), r.get("whCode"),
            r.get("logisticsChannel"), r.get("logisticsCarrier"),
            r.get("logisticsTrackNo"), r.get("outboundTime"),
            r.get("orderCreateTime"), _detail_reason(d0),
        ])

    print("  [%s] 详情 %d/%d；店铺分布 = %s" % (tag, len(det), len(nos), shops))
    print("  [%s] → %s 命中 %d 条（其余 %d 条属别店，不写入）"
          % (tag, "/".join(stores) or "全部店铺", len(rows), unknown))
    return {"rows": rows, "shops": shops, "scanned": len(recs),
            "abnormal": len(abn), "unknown_store": unknown}


# --------------------------------------------------------------------------
# 写盘
# --------------------------------------------------------------------------
def write_xlsx(path: str, rows: list, shops: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "异常订单"
    ws.append(COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in sorted(rows, key=lambda x: (x[1], str(x[6] or ""))):
        ws.append(r)
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:%s1" % ws.cell(row=1, column=len(COLS)).column_letter

    ws2 = wb.create_sheet("店铺分布")
    ws2.append(["店铺", "异常条数", "是否 BHY/BEL"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for name, n in sorted(shops.items(), key=lambda kv: -kv[1]):
        up = (name or "").upper()
        ws2.append([name, n, "BHY" if "BHY" in up else ("BEL" if "BEL" in up else "")])
    ws2.column_dimensions["A"].width = 34
    ws2.column_dimensions["B"].width = 10
    ws2.freeze_panes = "A2"

    wb.save(path)


def write_with_fallback(path: str, rows: list, shops: dict) -> tuple[str, str]:
    """写 xlsx；目标被 Excel 打开占用时另存为带时间戳的副本。

    实测（2026-10-29）：用户正开着 `<账号>-<日期>.xlsx` 看昨天的表，任务 17:30 触发
    → openpyxl `save()` 抛 `PermissionError`，整张表就白拉了。占用是**常态**不是异常，
    所以不能判失败：另存为 `<账号>-<日期>-<HHMM>.xlsx`，把锁这件事降级成告警（rc=3）。

    返回 (实际写出的路径, "") / (路径, "locked") / ("", 错误说明)。
    """
    try:
        write_xlsx(path, rows, shops)
        return path, ""
    except PermissionError:
        pass
    except Exception as e:                            # noqa: BLE001
        return "", "%s: %s" % (type(e).__name__, str(e)[:160])
    base, ext = os.path.splitext(path)
    alt = "%s-%s%s" % (base, dt.datetime.now().strftime("%H%M"), ext)
    try:
        write_xlsx(alt, rows, shops)
        return alt, "locked"
    except Exception as e:                            # noqa: BLE001
        return "", "目标被占用，另存 %s 也失败：%s: %s" % (
            os.path.basename(alt), type(e).__name__, str(e)[:120])


#: 面板/消息里最多列多少条明细（手机上刷屏没用，剩下的指到表格）
ALERT_MAX_ROWS = 15

#: 推送时点开跳到面板
PANEL_URL = "https://mgltemu.us.ci/#/automation/index"


def build_alert(summary: list, dest_dir: str, end_day: str,
                failed: list) -> tuple[str, str] | None:
    """把结果压成一条飞书/手机消息。**没有异常也没有失败 → 返回 None（不推）。**

    用户口径（2026-10-30）：「有异常就推飞书/手机」——安静的一天不该有通知。
    但**失败**必须推：那是「今天没查成」，比「今天没异常」重要得多，
    两者绝不能混成一条沉默。
    """
    rows = []
    for it in summary:
        for r in (it.get("rows_detail") or []):
            rows.append(r)
    per_store: dict[str, int] = {}
    for r in rows:
        per_store[r[1]] = per_store.get(r[1], 0) + 1

    if not rows and not failed:
        return None

    head = []
    if rows:
        head.append("BHY/BEL 异常 %d 条（%s）"
                    % (len(rows),
                       "、".join("%s %d" % (k, v) for k, v in sorted(per_store.items()))))
    else:
        head.append("BHY/BEL 无异常")
    if failed:
        head.append("查询失败：%s" % "、".join(failed))

    title = "TEMU 异常单 %d 条（%s）" % (len(rows), end_day) if rows \
        else "TEMU 异常单查询失败（%s）" % end_day

    lines = ["%s %s" % (end_day, head[0])]
    if len(head) > 1:
        lines.append(head[1])

    if rows:
        lines.append("")
        for r in rows[:ALERT_MAX_ROWS]:
            # 账号 / 店铺 / 出库单号 / 平台单号 / 运单号 / 异常时间
            lines.append("[%s·%s] %s\n  平台单 %s\n  运单 %s  异常 %s"
                         % (r[0], r[1], r[2], r[3], r[10] or "-", (r[6] or "")[:16]))
        if len(rows) > ALERT_MAX_ROWS:
            lines.append("\n…还有 %d 条，见表格" % (len(rows) - ALERT_MAX_ROWS))
        lines.append("\n表格：%s" % dest_dir)

    return title, "\n".join(lines)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="领星 OMS 异常出库单导出（每账号一张表，默认只留 BHY/BEL）")
    ap.add_argument("--days", type=int, default=7,
                    help="窗口天数（按**创建时间**切，缺省 7）")
    ap.add_argument("--date", default="",
                    help="窗口结束日 YYYY-MM-DD（缺省今天）")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出根目录（缺省按日期再建一级子文件夹）")
    ap.add_argument("--flat", action="store_true",
                    help="直接写进 --out 根目录，不建日期子文件夹")
    ap.add_argument("--write-empty", action="store_true",
                    help="即使 0 行也写出只有表头的空表（排查用；默认 0 行不导表）")
    ap.add_argument("--stores", default="BHY,BEL",
                    help="店铺名子串（逗号分隔，大小写不敏感；缺省 BHY,BEL）")
    ap.add_argument("--all-stores", action="store_true",
                    help="不过滤店铺（覆盖 --stores），用于排查别店异常")
    ap.add_argument("--account", action="append", default=[],
                    help="账号：名字=appKey:appSecret（可多次给；不给则读本地配置）")
    ap.add_argument("--accounts-file", default=ACCOUNTS_FILE,
                    help="双账号配置（缺省 scripts/oms/oms_accounts.local.json）")
    ap.add_argument("--retries", type=int, default=2,
                    help="单个账号整体失败后的重跑次数（缺省 2，即最多跑 2 轮）")
    ap.add_argument("--workers", type=int, default=6,
                    help="分页并发数（缺省 6；1 = 串行，出问题时可用来降速）")
    ap.add_argument("--retry-wait", type=int, default=20,
                    help="账号重跑前的等待秒数（缺省 20）")
    ap.add_argument("--json", default="", help="把汇总写成 json 到这里")
    ap.add_argument("--notify", dest="notify", action="store_true", default=True,
                    help="有异常/有失败就推飞书+手机（缺省开）")
    ap.add_argument("--no-notify", dest="notify", action="store_false",
                    help="本次不推送（手工重跑时用）")
    a = ap.parse_args()

    end_day = a.date or dt.date.today().isoformat()
    try:
        end_d = dt.date.fromisoformat(end_day)
    except ValueError:
        print("[FAIL] --date 格式应为 YYYY-MM-DD，收到：%s" % end_day, file=sys.stderr)
        return 2
    start = (end_d - dt.timedelta(days=max(0, a.days - 1))).isoformat() + " 00:00:00"
    end = end_day + " 23:59:59"
    stores = [] if a.all_stores else [
        x.strip().upper() for x in a.stores.split(",") if x.strip()]

    accounts = load_accounts(a.account, a.accounts_file)
    # 用户 2026-10-29：文件都放进「失败订单\<日期>\」子文件夹，别都堆根目录
    dest_dir = dest_dir_for(a.out, end_day, a.flat)

    print("窗口 %s ~ %s（%d 天）；账号 %d 个；店铺过滤 %s"
          % (start[:10], end[:10], a.days, len(accounts),
             "/".join(stores) or "（全部）"))
    print("输出：%s（仅在真有异常单时才建目录/写表）" % dest_dir)

    ok, failed, locked, empty, summary = [], [], [], [], []
    for tag, k, s in accounts:
        print("\n=== %s ===" % tag)
        res = None
        last_err = ""
        for attempt in range(1, max(1, a.retries) + 1):
            try:
                res = scan_account(tag, k, s, start, end, stores,
                                   workers=max(1, a.workers))
                break
            except OmsError as e:
                last_err = str(e)[:200]
                print("  [%s] 第 %d 轮失败：%s" % (tag, attempt, last_err))
                if attempt < max(1, a.retries):
                    # 领星偶发 5xx / 限流；等一会儿整账号重来（纯只读，重跑安全）
                    time.sleep(max(0, a.retry_wait))
            except Exception as e:                    # noqa: BLE001
                last_err = "%s: %s" % (type(e).__name__, str(e)[:200])
                print("  [%s] 第 %d 轮异常：%s" % (tag, attempt, last_err))
                break
        if res is None:
            failed.append(tag)
            summary.append({"account": tag, "ok": False, "error": last_err,
                            "rows": 0, "abnormal": 0, "scanned": 0, "shops": {}})
            continue

        # 用户 2026-10-30：「如果两个店铺都没有异常订单就不导表到指定文件夹」——
        # 0 行就**什么都不写**（不建日期目录、不留空表），别让「今天有表」误导成「今天有异常」。
        if not res["rows"] and not a.write_empty:
            empty.append(tag)
            print("  → BHY/BEL 异常 0 条，不写表（也不建目录）")
            summary.append({"account": tag, "ok": True, "file": None, "empty": True,
                            "rows": 0, "abnormal": res["abnormal"],
                            "scanned": res["scanned"], "shops": res["shops"]})
            continue

        path = os.path.join(dest_dir, "%s-%s.xlsx" % (tag, end_day))
        os.makedirs(dest_dir, exist_ok=True)          # 真要写盘了才建日期目录
        real_path, w_err = write_with_fallback(path, res["rows"], res["shops"])
        if not real_path:
            failed.append(tag)
            print("  [FAIL] 写盘失败 %s：%s" % (path, w_err))
            summary.append({"account": tag, "ok": False, "error": "写盘失败：%s" % w_err,
                            "rows": len(res["rows"]), "abnormal": res["abnormal"],
                            "scanned": res["scanned"], "shops": res["shops"]})
            continue
        if w_err == "locked":
            locked.append(tag)
            print("  [WARN] %s 正被别的程序占用（Excel 开着？）→ 另存 %s"
                  % (os.path.basename(path), os.path.basename(real_path)))
        ok.append(tag)
        per = {}
        for r in res["rows"]:
            per[r[1]] = per.get(r[1], 0) + 1
        print("  → 写出 %s（%d 行）分店 %s" % (real_path, len(res["rows"]), per))
        summary.append({"account": tag, "ok": True, "file": real_path,
                        "locked": w_err == "locked",
                        "rows": len(res["rows"]), "per_store": per,
                        "rows_detail": res["rows"],
                        "abnormal": res["abnormal"], "scanned": res["scanned"],
                        "unknown_store": res["unknown_store"], "shops": res["shops"]})

    print("\n" + "=" * 70)
    for it in summary:
        if not it["ok"]:
            print("[FAIL] %-8s %s" % (it["account"], it.get("error", "")[:120]))
        elif it.get("empty"):
            print("[OK]   %-8s 异常 %3d 条 → BHY/BEL 0 行（未导表）"
                  % (it["account"], it["abnormal"]))
        else:
            print("[OK]   %-8s 异常 %3d 条 → BHY/BEL %d 行%s  %s"
                  % (it["account"], it["abnormal"], it["rows"],
                     "（目标被占用，已另存）" if it.get("locked") else "",
                     it.get("file", "")))
    if len(empty) == len(accounts) and not failed:
        # 用户口径：两边都没有异常单 → 不往指定文件夹导表
        print("没有 BHY/BEL 异常单，今天不导表（%s 未创建）" % dest_dir)
    else:
        print("目录：%s" % dest_dir)

    if a.json:
        try:
            os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"start": start, "end": end, "stores": stores or "ALL",
                           "out": a.out, "dest": dest_dir, "ok": ok, "failed": failed,
                           "locked": locked, "empty": empty,
                           "wrote_any": bool(ok),
                           "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "accounts": summary}, f, ensure_ascii=False, indent=2)
            print("汇总 -> %s" % a.json)
        except Exception as e:                        # noqa: BLE001
            print("[WARN] 写 json 失败（不影响表格）：%s" % e)

    # ---- 推送：有异常 or 有失败才发；都干净就安静（用户口径 2026-10-30）----
    if a.notify:
        alert = build_alert(summary, dest_dir, end_day, failed)
        if alert is None:
            print("无异常、无失败 → 不推送")
        else:
            title, body = alert
            try:
                import notify_push                       # scripts/notify_push.py
                res = notify_push.push(title, body, url=PANEL_URL)
                print("推送 %s" % notify_push.brief(res))
            except Exception as e:                        # noqa: BLE001
                # 推送坏掉只能少一条消息，绝不能把「表已导出」搞成失败
                print("[WARN] 推送失败（不影响导表）：%s: %s"
                      % (type(e).__name__, str(e)[:140]))

    # 「查到了、只是没异常单」算成功，不算失败 —— 否则一天没异常就报警
    done = ok + empty
    if failed and not done:
        return 2
    if failed:
        return 1
    return 3 if locked else 0


if __name__ == "__main__":
    raise SystemExit(main())
