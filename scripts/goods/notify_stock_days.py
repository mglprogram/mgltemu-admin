"""库存跟踪 · 按「款号-尺码」算可售天数，≤7 天的推**独立的库存预警群**。

用户 2026-11-09 要求
--------------------
「根据库存跟踪的 7 天日均的款号-尺码，这个尺码的总库存 / 尺码的 7 天日均
 小于等于 7 天时，推送到我的飞书 —— 但是单独创建一个新的机器群，
 这个只单独发这个库存不足的消息。」

口径（与面板完全一致，别自己另发明一套）
--------------------------------------
    总库存   = 恒信可用 + 帕沃尔 加州3 + 帕沃尔 Kansas      （stock-track.json 的 lxTotal）
    7天日均  = 看板「近7天日均」（件/天，款级字段，按款号-套系-尺码对齐）
    可售天数 = 总库存 / 7天日均
    触发条件 = 7天日均 > 0 且 可售天数 ≤ 7

为什么用 lxTotal 而不是「实际库存」
    · 实际库存（代发库存）只有加州3 + 恒信美西两块，漏掉 Kansas 与其它恒信仓；
    · 用户 2026-11-06 已把「总库存」定成三项相加，这里跟着同一个数走，
      否则群里的数字和面板对不上，还得再解释一遍。

推送通道
--------
只发 `FEISHU_WEBHOOK_STOCK`（库存预警独立群）。**不回退**到业务群
（FEISHU_WEBHOOK）：用户明确要"只单独发这个"，混进业务群反而会被忽略。

用法
----
    python scripts\\goods\\notify_stock_days.py                # 算 + 推（有货就推）
    python scripts\\goods\\notify_stock_days.py --dry-run      # 只打印，不推
    python scripts\\goods\\notify_stock_days.py --days 10      # 阈值改 10 天
    python scripts\\goods\\notify_stock_days.py --json         # 输出结构化结果

退出码：0 = 正常（含"今天没有要提醒的"）；1 = 数据缺失；2 = 推送失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(ROOT / "scripts"),):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                    # noqa: BLE001
    pass

PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"
PANEL_URL = "https://mgltemu.us.ci/#/product/stock-track"

#: 面板上「款号」对 3040 显示成 3040A（见 linxing-view.vue 的 CODE_DISPLAY）
CODE_DISPLAY = {"3040": "3040A"}

DEFAULT_DAYS = 7.0

#: 群里最多列多少行（更急的排在前面）。39 条约 3 KB 还行，但趋势一长就会刷屏，
#: 超过这个数就截断 + 提示看面板。
MAX_ROWS = 60


def code_show(code: str) -> str:
    c = str(code or "")
    return CODE_DISPLAY.get(c, c)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def collect(days: float = DEFAULT_DAYS) -> dict:
    """读 stock-track.json，返回 {items: [...], generatedAt, sources}。"""
    if not PUBLIC_JSON.exists():
        raise FileNotFoundError(str(PUBLIC_JSON))
    data = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))

    items: list[dict] = []
    seen: dict[tuple, dict] = {}
    for key, src in (data.get("sources") or {}).items():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    code = str(r.get("code") or "").strip()
                    size = str(r.get("size") or "").strip()
                    if not code or not size:
                        continue
                    daily = _num(r.get("daily"))
                    if daily <= 0:
                        continue
                    # 总库存：优先 lxTotal（三项相加，页面口径）
                    total = r.get("lxTotal")
                    if total is None:
                        # 领星没匹配到的行（页面显示 —），退回实际库存，避免整行漏掉
                        total = _num(r.get("stock"))
                    total = _num(total)
                    d = round(total / daily, 1)
                    if d > days:
                        continue
                    # 九部/十部是同一批款的两种看板，同一「款-套系-尺码」只留一条。
                    # 合并规则：总库存取**两者中较大的**（一边漏了数据不等于没货），
                    # 日均取较大者（保守估销量），再用合并后的数重算天数。
                    k = (code, str(r.get("variant") or ""), size)
                    prev = seen.get(k)
                    if prev is not None:
                        prev["total"] = max(prev["total"], total)
                        prev["daily"] = max(prev["daily"], daily)
                        prev["days"] = round(prev["total"] / prev["daily"], 1) if prev["daily"] > 0 else 0.0
                        prev["_sources"].add(key)
                        continue
                    item = {
                        "source": key,
                        "sourceLabel": str(src.get("short") or src.get("label") or key),
                        "code": code,
                        "codeShow": code_show(code),
                        "variant": str(r.get("variant") or ""),
                        "size": size,
                        "total": round(total),
                        "daily": round(daily, 2),
                        "days": d,
                        "avail": round(_num(r.get("lxAvail"))),
                        "_sources": {key},
                    }
                    seen[k] = item
                    items.append(item)

    # 合并后可能又有超过阈值的（取较大总库存导致天数变大），这里再筛一遍
    items = [it for it in items if it["days"] <= days]
    for it in items:
        it["sources"] = sorted(it.pop("_sources"))

    # 越紧急的排前面：可售天数升序，同天数按日均销量降序
    items.sort(key=lambda x: (x["days"], -x["daily"], x["codeShow"], x["size"]))
    return {
        "generatedAt": str(data.get("generatedAt") or ""),
        "items": items,
    }


def render(result: dict, days: float = DEFAULT_DAYS) -> tuple[str, str]:
    """(标题, 正文)。群机器人 text 消息不解析 markdown，这里纯文本对齐。"""
    items = result["items"]
    title = "TEMU 库存不足提醒（可售 ≤ %g 天）" % days
    if not items:
        return title, "今天没有可售 ≤ %g 天的款号-尺码。" % days

    # 清单按「可售天数」从急到缓排，太长的清单在群里没人读 —— 截断并注明条数
    shown = items if len(items) <= MAX_ROWS else items[:MAX_ROWS]
    lines = [
        "%d 个「款号-尺码」按 7 天日均算，可售不足 %g 天。" % (len(items), days),
        "总库存 = 恒信可用 + 帕沃尔加州3 + 帕沃尔Kansas；可售天数 = 总库存 ÷ 7天日均。",
        "",
        "%-22s %8s %8s %6s" % ("款号-套系-尺码", "总库存", "7天日均", "可售"),
    ]
    for it in shown:
        name = "%s-%s-%s" % (it["codeShow"], it["variant"], it["size"])
        lines.append("%-22s %8d %8.1f %5.1f天"
                     % (name, int(it["total"]), it["daily"], it["days"]))
    if len(shown) < len(items):
        lines.append("……另有 %d 条，完整清单看面板" % (len(items) - len(shown)))
    lines += ["", "面板：%s" % PANEL_URL]
    return title, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="库存跟踪 可售≤N 天 → 独立飞书预警群")
    ap.add_argument("--days", type=float, default=DEFAULT_DAYS,
                    help="阈值天数（默认 7，含等于）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    ap.add_argument("--json", action="store_true", help="额外打印结构化结果")
    ap.add_argument("--force", action="store_true",
                    help="没有告警也发一条（验证通道用）")
    args = ap.parse_args(argv)

    try:
        result = collect(args.days)
    except FileNotFoundError as e:
        print("[stock-days] 找不到 %s —— 先跑 fetch_stock_track.py" % e)
        return 1

    title, body = render(result, args.days)
    n = len(result["items"])
    print("[stock-days] 数据 %s，可售 ≤%g 天：%d 条" % (result["generatedAt"], args.days, n))
    print(body)

    if args.json:
        print(json.dumps({"generatedAt": result["generatedAt"], "days": args.days,
                          "count": n, "items": result["items"]},
                         ensure_ascii=False, indent=1))

    if args.dry_run:
        print("[stock-days] --dry-run：不推送")
        return 0
    if not n and not args.force:
        print("[stock-days] 没有需要提醒的尺码，不推送")
        return 0

    try:
        import notify_push                        # noqa: PLC0415
    except Exception as e:                        # noqa: BLE001
        print("[stock-days] notify_push 不可用：%s" % str(e)[:160])
        return 2

    ok, why = notify_push.push_stock_alert(title, body)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[stock-days] %s 推送%s：%s" % (stamp, "成功" if ok else "失败", why))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
