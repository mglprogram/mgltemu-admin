# -*- coding: utf-8 -*-
"""真实验证「确认订单」接口：只挑 pb=1 第一单跑一次。

参数已核对（2026-11-25）：
    POST deliver.php?act=operate
      act=batch, pb=1, pb_str=, ids=<id>i, select_types=order_queren
    （页面 checkboxes[] 的 value 用 `i` 拼接，与 pw_hwc_flow.http_set_wh 的格式一致）

验证：
    确认前该 id 在 pb=1 列表里 → 提交 → 回读是否离开 pb=1
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE = "https://www.powerwarehouse.net/"
N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_retry(n=8):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(12)
    raise last


def ids_of(t: str) -> set:
    return set(re.findall(r'name="checkboxes\[\]"[^>]*value="(\d+)"', t))


def row_text(t: str, oid: str) -> str:
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        if 'value="%s"' % oid in tr:
            return clean(tr)[:110]
    return ""


def confirm(pw, ids: list) -> dict:
    data = [("act", "batch"), ("pb", "1"), ("pb_str", ""),
            ("ids", "".join("%si" % i for i in ids)),
            ("select_types", "order_queren")]
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + "deliver.php?act=operate", data=body,
                                 method="POST")
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", BASE + "deliver.php?act=list&pb=1")
    with pw.op.open(req, timeout=pw.timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": clean(txt)[:200], "raw_len": len(txt)}
    return d


def main():
    pw = login_retry()
    print("① 读 pb=1 列表")
    t1 = pw.get("deliver.php?act=list&pb=1&uselastfilter=1")
    t1 = t1 if isinstance(t1, str) else str(t1)
    all_ids = sorted(ids_of(t1))
    if not all_ids:
        print("   pb=1 没有待确认订单")
        return 1
    todo = all_ids[:N]
    print("   共 %d 单，本次取 %d 单：%s" % (len(all_ids), len(todo), todo))
    for oid in todo:
        print("     %s → %s" % (oid, row_text(t1, oid)))

    print("\n② 提交 order_queren：ids=%s" % "".join("%si" % i for i in todo))
    r = confirm(pw, todo)
    print("   →", json.dumps(r, ensure_ascii=False)[:250])

    print("\n③ 回读")
    time.sleep(3)
    t2 = pw.get("deliver.php?act=list&pb=1&uselastfilter=1")
    t2 = t2 if isinstance(t2, str) else str(t2)
    left = ids_of(t2) & set(todo)
    for oid in todo:
        ok = oid not in left
        print("   %-10s %s" % (oid, "✅ 已离开待确认" if ok else "❌ 仍在待确认"))

    # 顺便看 pb=2（待打包）有没有出现
    t3 = pw.get("deliver.php?act=list&pb=2&uselastfilter=1")
    t3 = t3 if isinstance(t3, str) else str(t3)
    print("\n④ pb=2（待打包）里是否出现：")
    for oid in todo:
        print("   %-10s %s" % (oid, "✅ 出现了" if oid in ids_of(t3) else "—"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
