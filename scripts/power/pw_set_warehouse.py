# -*- coding: utf-8 -*-
"""pw_set_warehouse.py — 帕沃尔「修改仓库渠道」批量提交 + 回读。

用户口径（2026-11-06）：
    「先去页面上把仓库改成 kansas/加州3 再下单」
    「仓库=加州3仓 / 指定快递=temu / 出货渠道=temu官方面单」

真机参数（2026-11-06 实测）：
    POST hwc_shop.php?act=operate
      act=batch
      select_types=pl_edit_ck_chqd      # 修改仓库渠道
      select_ck=<仓库id>                 # 22=加州3仓 / 10=kansas仓
      select_zdkd=803                    # 指定快递 = temu
      select_chqd=<渠道id>               # 529=kansas 的 temu官方面单 / 530=加州3仓 的 temu官方面单
      checkboxes[]=<订单id>              # 多单要重复 append（同 skill 的「防压扁」铁律）

渠道 id 由 `deliver.php?act=get_shop_paisongx` 现取，**不写死**
（不同仓的渠道 id 不同：加州3仓 530 / kansas 529，都叫 temu官方面单）。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
import pw_session as S  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\scripts\power\out\2026-11-06"
CK = {"加州3仓": "22", "kansas仓": "10"}
ZDKD_TEMU = "803"

#: 这些状态说明已经进履约流程，改不了了（SKILL 硬性限制）
DONE_STATUS = ("已发货", "已出库", "已取消", "已删除")


def _rows():
    p = os.path.join(OUT, "pw_orders.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def ids_for(cfg: dict, which: str) -> list:
    want = set(cfg.get(which) or [])
    return [r["id"] for r in (cfg.get("rows") or []) if r["po"] in want]


def get_channel(pw, ids: list, ck: str) -> str:
    """现取「temu 官方面单」在该仓的渠道 id（不写死）。"""
    r = pw.post("deliver.php", {"act": "get_shop_paisongx", "ids": "i".join(ids) + "i",
                                "zdkd": ZDKD_TEMU, "select_ck": ck,
                                "select_types": "pl_edit_ck_chqd"},
                referer="hwc_shop.php?act=shop_order&pb=1&uselastfilter=1")
    t = r.decode("utf-8", "replace") if isinstance(r, bytes) else r
    j = json.loads(t)
    for it in (j.get("html") or []):
        if "官方面单" in str(it.get("pname") or ""):
            return str(it["id"])
    raise RuntimeError("该仓没取到「temu官方面单」渠道：%s" % t[:200])


def read_back(pw, pos: list) -> dict:
    import urllib.parse
    q = urllib.parse.quote("\n".join(pos))
    html = pw.get("hwc_shop.php?act=shop_order&pb=1&uselastfilter=1&deliver_no=" + q)
    t = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    out = {}
    for m in re.finditer(r'tr_html_(\d+)\s', t):
        seg = t[m.start():m.start() + 4600]
        po = re.search(r'class="deliver_no_\d+">([^<]+)<', seg)
        wh = re.search(r'发货仓库</span>\s*:([^<]*)<', seg)
        qd = re.search(r'出货渠道</span>\s*:([^<]*)<', seg)
        out[m.group(1)] = {"po": (po.group(1).strip() if po else ""),
                           "wh": (wh.group(1).strip() if wh else ""),
                           "qd": (qd.group(1).strip() if qd else "")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", required=True, choices=["kansas", "jiazhou3"],
                    help="改哪一批（读 pw_orders.json 里的分组）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = _rows()
    ids = ids_for(cfg, a.which)
    if not ids:
        print("[FAIL] pw_orders.json 里没有 %s 的 id" % a.which, file=sys.stderr)
        return 42
    ck_name = "kansas仓" if a.which == "kansas" else "加州3仓"
    ck = CK[ck_name]
    pos = [r["po"] for r in cfg["rows"] if r["id"] in ids]
    print("%s：%d 单，仓库 %s(%s)" % (a.which, len(ids), ck_name, ck))

    pw = S.PW()
    pw.login()
    ch = get_channel(pw, ids, ck)
    print("渠道 id（现取）:", ch)

    before = read_back(pw, pos)
    print("改前:", json.dumps({k: v["wh"] for k, v in before.items()}, ensure_ascii=False)[:260])

    if a.dry_run:
        print("[dry-run] 不提交")
        return 0

    body = [("act", "batch"), ("select_types", "pl_edit_ck_chqd"),
            ("select_ck", ck), ("select_zdkd", ZDKD_TEMU),
            ("select_chqd", ch), ("submit", "提交")]
    for i in ids:
        body.append(("checkboxes[]", i))
    r = pw.post("hwc_shop.php?act=operate", body,
                referer="hwc_shop.php?act=shop_order&pb=1&uselastfilter=1")
    t = r.decode("utf-8", "replace") if isinstance(r, bytes) else r
    m = re.search(r"(操作成功|修改成功|成功|失败|错误|请选择)[^<]{0,50}", t)
    print("提交返回:", len(t), "提示:", (m.group(0) if m else "（无）"))

    time.sleep(2)
    after = read_back(pw, pos)
    for k, v in after.items():
        print("   %-26s 仓库=%-8s 渠道=%s" % (v["po"], v["wh"] or "-", v["qd"] or "-"))
    bad = [v["po"] for v in after.values() if ck_name not in (v["wh"] or "")]
    if bad:
        print("[WARN] 仓库没改上 %d 单: %s" % (len(bad), ", ".join(bad)), file=sys.stderr)
        return 42
    print("[OK] %d 单仓库已改成 %s" % (len(after), ck_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
