# -*- coding: utf-8 -*-
"""验证「确认订单」接口：`deliver.php?act=operate` + `order_queren`。

页面证据（2026-11-25）：
    <form method="post" action="deliver.php?act=operate" name="listForm">
      <select name="select_types">  ... <option value="order_queren">确认订单</option>
    参考单号筛选：name="deliver_no"（textarea / input）

用法：
    python pw_receive_api.py PO-211-07055826094713286          # dry-run
    python pw_receive_api.py PO-211-07055826094713286 --apply  # 真确认
"""
import json
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def list_pending(pw, po: str = "") -> str:
    """待确认订单页（可按参考单号过）。"""
    u = "deliver.php?act=list&pb=1"
    if po:
        u += "&deliver_no=%s" % urllib.parse.quote(po)
    return pw.get(u) or ""


def parse_rows(t: str, po: str = "") -> list:
    """从待确认页解析勾选框（`checkboxes[]` / `ids`）与其单号。"""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        if po and po not in tr:
            continue
        m = re.search(r'name="(?:checkboxes\[\]|ids\[\]|checkboxes)"[^>]*value="(\d+)"', tr)
        if not m:
            continue
        txt = clean(tr)
        pom = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", txt)
        out.append({"id": m.group(1), "po": pom.group(1) if pom else "",
                    "text": txt[:120]})
    return out


def do_operate(pw, ids: list, op: str = "order_queren") -> dict:
    """提交 `deliver.php?act=operate`（等价页面勾选 + 选操作 + 提交）。"""
    data = [("act", "batch"), ("pb", "1"), ("pb_str", ""),
            ("ids", "".join("%si" % i for i in ids)),
            ("select_types", op)]
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + "deliver.php?act=operate", data=body,
                                 method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", BASE + "deliver.php?act=list&pb=1")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"error": -1, "msg": clean(txt)[:200], "raw_len": len(txt)}


def main():
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    apply_ = "--apply" in sys.argv
    pw = SS.PW()
    pw.login()

    t = list_pending(pw, po)
    rows = parse_rows(t, po)
    print("待确认页命中 %d 行：" % len(rows))
    for r in rows:
        print("   id=%-9s %-24s %s" % (r["id"], r["po"], r["text"][:70]))
    if not rows:
        print("\n该单不在「待确认订单」")
        return 1
    if not apply_:
        print("\n[dry-run] 将提交 order_queren：ids=%s" % [r["id"] for r in rows])
        return 0

    r = do_operate(pw, [x["id"] for x in rows])
    print("\n确认订单 →", json.dumps(r, ensure_ascii=False)[:300])
    t2 = list_pending(pw, po)
    still = parse_rows(t2, po)
    print("确认后仍在待确认：%d 行" % len(still))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
