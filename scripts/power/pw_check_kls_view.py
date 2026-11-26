# -*- coding: utf-8 -*-
"""KLS 店铺视图检查/切换：必须切到 **local 半托管**。

用户 2026-11-30 口径（沿用 skill 里记录的规则）：
    「默认不是 local 半托管店铺，你需要切换到 local 半托管店铺」
    KLS 的店铺视图必须是 `KinglamanSport local`。

`run_temu_auth.py` 已有 `--check-view` 专门做这件事：
    python run_temu_auth.py --store KinglamanSport --check-view
本脚本先看当前视图是什么，不匹配就调 run_temu_auth 切。

只读 + 一次视图切换（不改业务数据）。
"""
import json
import re
import subprocess
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402
import ziniao_store as zs  # noqa: E402

JS_VIEW = r"""(() => {
  const t = (document.body.innerText || '').replace(/\s+/g, ' ');
  // 店铺切换器通常显示当前店铺名 + 类型
  const m = t.match(/(Elanvoke|KinglamanSport|Temu[-A-Za-z]*|BK[\w-]*)/);
  const local = /local|半托管/i.test(t.slice(0, 600));
  return JSON.stringify({head: t.slice(0, 300), shop: m ? m[1] : '', hasLocal: local});
})()"""


def main():
    port = int(zs.probe_port_by_window("TM-KinglamanSport") or 0)
    print("KLS 端口:", port)
    if not port:
        sys.exit("KLS 窗口未开")

    try:
        ws_url, url = A._page_ws("KinglamanSport")
        raw = asyncio.run(A._ev(ws_url, JS_VIEW))
        d = json.loads(raw or "{}")
        print("当前页:", url[:90])
        print("店铺名:", d.get("shop"), "| 页头含 local/半托管:", d.get("hasLocal"))
        print("页头:", d.get("head", "")[:200])
    except Exception as ex:  # noqa: BLE001
        print("读视图失败:", str(ex)[:120])

    print("\n→ 调 run_temu_auth --check-view 纠正视图…")
    p = subprocess.run([sys.executable, "-X", "utf8",
                        r"D:\AI-workplace\TEMU\scripts\power\run_temu_auth.py",
                        "--store", "KinglamanSport", "--check-view"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    for ln in ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-12:]:
        print("  ", ln[:150])
    print("  rc =", p.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
