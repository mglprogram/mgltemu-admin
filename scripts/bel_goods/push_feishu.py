# -*- coding: utf-8 -*-
"""把 TEMU 运营看板推送到飞书群（交互卡片）。

数据来源与 HTML 看板完全一致：
  data/<store>/<store>_products.db       在售 + 限流明细
  data/focus_watch_latest.json           监控最新结果
  data/trend_data.json                   历史趋势

输出：飞书 interactive 卡片 JSON（stdout，或 --out 落文件）

用法：
  python push_feishu.py                 # 打印卡片 JSON
  python push_feishu.py --send          # 真发到 FEISHU_WEBHOOK
  python push_feishu.py --send --test   # 发一条连通性测试消息

环境变量：
  FEISHU_WEBHOOK   群机器人 webhook（必填，--send 时）
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "data"

DB = {
    "BEL": (DATA / "bel" / "bel_products.db", "traffic_limited", "BEL 店"),
    "BHY": (DATA / "bhy" / "bhy_products.db", "traffic_limited_bhy", "BHY 店"),
}
FOCUS_BY_STORE = {
    "BEL": ["7502713672", "5745761504", "3082143968", "1224162395",
            "8118634665", "3740374018", "1249844154"],
    "BHY": ["1289576127", "3248169459", "3775612405", "3194032323",
            "1586673761", "7557422941", "7235257044", "8312381638"],
}

BLUE, RED, GREEN, ORANGE = "blue", "red", "green", "orange"


def md(text, **kw):
    """飞书 lark_md 元素。"""
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}, **kw}


def field(label, value, wide=False):
    return {"is_short": wide, "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"}}


def note(text):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def hr():
    return {"tag": "hr"}


def money(v):
    return f"${v:.2f}" if isinstance(v, (int, float)) else "—"


def load(store):
    path, tl, label = DB[store]
    if not path.exists():
        return None
    con = sqlite3.connect(path)
    q = lambda s, a=(): con.execute(s, a).fetchall()
    d = {
        "label": label,
        "sku": q("SELECT COUNT(*) FROM skus")[0][0],
        "spu": q("SELECT COUNT(*) FROM products")[0][0],
        "limited": q(f"SELECT COUNT(*) FROM {tl}")[0][0],
        "stock": q("SELECT SUM(stock) FROM skus")[0][0] or 0,
        "flow": q(f"SELECT flow_status, COUNT(*) FROM {tl} GROUP BY flow_status ORDER BY COUNT(*) DESC"),
        "top_cut": q(f"""SELECT sku_code, declare_price, daily_target_price FROM {tl}
                         WHERE declare_price IS NOT NULL AND daily_target_price IS NOT NULL
                         ORDER BY (declare_price - daily_target_price) DESC LIMIT 5"""),
    }
    con.close()
    return d


def load_focus(store):
    path, tl, _ = DB[store]
    con = sqlite3.connect(path)
    rows = []
    for s in FOCUS_BY_STORE[store]:
        r = con.execute("SELECT sku_count, total_stock FROM products WHERE spu_id=?", (s,)).fetchone()
        codes = [x[0] for x in con.execute("SELECT DISTINCT skc_code FROM skus WHERE spu_id=?", (s,))]
        lim = con.execute(f"""SELECT flow_status, declare_price, daily_target_price, activity_target_price
                               FROM {tl} WHERE spu_id=?""", (s,)).fetchone()
        note_ = con.execute(f"SELECT match_note FROM {tl} WHERE spu_id=?", (s,)).fetchone()
        rows.append({"spu": s, "codes": codes, "sku": r[0] if r else 0,
                     "stock": r[1] if r else 0,
                     "limited": bool(lim),
                     "flow": lim[0] if lim else "",
                     "cur": lim[1] if lim else None, "daily": lim[2] if lim else None,
                     # match_note 非空 = 该行价格来自「换标题重建」后的另一个 SKC，不可信
                     "unreliable": bool(note_ and note_[0])})
    con.close()
    return rows


def check_freshness(max_hours: float = 6.0):
    """推送前的闸门：数据必须是本次监控刚写入的，且监控本身没整体失败。

    2026-10-12 教训：抓取全失败时旧脚本仍 exit=0，飞书推了一条
    「重点款全部正常」的假消息，而实际是登录态掉了根本没抓到。
    宁可漏推，也不能推错的。返回 (ok, reason)。
    """
    p = DATA / "focus_watch_latest.json"
    if not p.exists():
        return False, "focus_watch_latest.json 不存在（监控未运行过）"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"监控结果无法解析: {e}"

    if not data:
        return False, "监控结果为空"

    ok = [d for d in data if "error" not in d]
    if not ok:
        errs = "; ".join(f"{d.get('store')}: {d.get('error','')}" for d in data)
        return False, f"全部店铺抓取失败（{errs}）"

    ts = max((d.get("checked_at", "") for d in ok), default="")
    if not ts:
        return False, "监控结果缺少 checked_at"
    try:
        age = (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except ValueError:
        return False, f"checked_at 格式异常: {ts}"
    if age > max_hours:
        return False, f"监控数据已过期 {age:.1f} 小时（最后检查 {ts}），疑似定时任务未运行"

    return True, f"数据新鲜（{ts}）"


def build_card():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stores = {k: load(k) for k in DB}
    stores = {k: v for k, v in stores.items() if v}
    focus = load_focus("BEL") + load_focus("BHY")
    hits = [f for f in focus if f["limited"]]
    safe = [f for f in focus if not f["limited"]]

    total_lim = sum(v["limited"] for v in stores.values())
    total_sku = sum(v["sku"] for v in stores.values())

    els = []

    # ---- 概览 ----
    els.append(md(
        f"**两店 SKU {total_sku}**　|　**限流商品 {total_lim}**　|　"
        f"**重点 SPU 被限流 {len(hits)}/{len(focus)}**", is_short=False))

    if hits:
        els.append(hr())
        els.append(md(f"**:warning: 重点款限流告警（{len(hits)} 个，需 24 小时内调价）**"))
        rows = []
        for f in hits:
            store = "BEL" if f["spu"] in FOCUS_BY_STORE["BEL"] else "BHY"
            delta = (f["cur"] - f["daily"]) if (isinstance(f["cur"], (int, float))
                                                and isinstance(f["daily"], (int, float))) else None
            line = (f"**{store} · {f['spu']}**　{f['codes'][0] if f['codes'] else ''}\n"
                    f"申报价 **{money(f['cur'])}** → 建议日常价 **{money(f['daily'])}**"
                    + (f"　（降 {money(delta)}）" if delta is not None else "")
                    + f"　{f.get('flow','')}")
            if f.get("unreliable"):
                line += ("\n<font color='orange'>⚠ 该款存在同名重建，价格取自另一 SPU，"
                         "调价前请先核实</font>")
            rows.append(line)
        els.append(md("\n\n".join(rows)))
        els.append(md(f"**未限流（{len(safe)} 个）**："
                      + " ".join(f["spu"] for f in safe)))

    # ---- 各店概况 ----
    for k, v in stores.items():
        els.append(hr())
        els.append(md(f"**{v['label']}**　SKU {v['sku']}　SPU {v['spu']}　"
                      f"库存 {v['stock']:,}　限流 **{v['limited']}** 个"))
        flow_txt = "　".join(f"{r[0].replace('流量预计','预计').replace('保持流量稳健','稳健')} {r[1]}"
                             for r in v["flow"][:5])
        els.append(md(f"流量状态：{flow_txt}"))
        if v["top_cut"]:
            cut = "　".join(f"`{r[0]}` {money(r[1])}→{money(r[2])}" for r in v["top_cut"][:3])
            els.append(md(f"调价空间最大：{cut}"))

    els.append(hr())
    els.append(note(f"TEMU 店铺运营看板 · 数据快照 {now} · "
                    f"仅读取数据，未执行任何调价操作"))

    template = RED if hits else GREEN
    title = f"TEMU 运营看板 · 重点款限流 {len(hits)} 个" if hits else "TEMU 运营看板 · 重点款全部正常"

    return {"msg_type": "interactive",
            "card": {"config": {"wide_screen_mode": True},
                     "header": {"title": {"tag": "plain_text", "content": title},
                                "template": template},
                     "elements": els}}


def send(payload):
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if not url:
        print("未设置 FEISHU_WEBHOOK，无法发送", file=sys.stderr)
        return 2
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"发送失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if res.get("code") == 0:
        print("已发送成功")
        return 0

    code = res.get("code")
    msg = res.get("msg", "")
    hints = {
        19024: "机器人开启了「关键词」安全设置，内容必须包含配置过的关键词。\n"
               "   → 请在飞书群机器人设置里查看/添加关键词，或临时改为「IP 白名单」/关闭该校验。",
        9499: "机器人不在该群，或已被移出群聊。",
        19021: "签名校验失败（若机器人勾选了「签名校验」）。",
    }
    print(f"发送被拒：code={code} msg={msg}")
    if code in hints:
        print("   " + hints[code])
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真正发送到飞书群")
    ap.add_argument("--test", action="store_true", help="发送连通性测试消息")
    ap.add_argument("--out", help="把卡片 JSON 写到文件")
    a = ap.parse_args()

    if a.test:
        payload = {"msg_type": "text", "content": {"text": "TEMU 看板推送通道自检"}}
        return send(payload)

    # 推送前闸门：数据不新鲜/监控失败 -> 拒发，避免「一切正常」的假消息
    fresh, reason = check_freshness()
    if not fresh:
        print(f"[FEISHU] 拒绝推送：{reason}")
        if a.send:
            return 4
        print("   （如需强制推送，先修好监控再跑）")
    else:
        print(f"[FEISHU] {reason}")

    card = build_card()
    if a.out:
        Path(a.out).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {a.out}")
    if a.send:
        return send(card)
    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())