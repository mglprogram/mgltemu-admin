# -*- coding: utf-8 -*-
"""抓取「商品流量 · 流量加速中」SPU 的申报价格明细（原日常申报价 / 调整后申报价，按尺码）。

流程（每个 SPU）：
  1. 在「流量加速中」筛选后的表格里定位该行 → 点「查看效果」打开右侧抽屉
  2. 抽屉默认停在「近期流量加速效果」tab → 解析「流量加速申报价格明细」表（rowspan 展开）
  3. 关抽屉，回列表继续下一个

只读：不点「一键提效」「批量开启」「停止流量加速」等任何写操作。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
STORE_ID = os.environ.get("STORE_ID", "27697436126374")
# 店铺窗口会自己切标签页，必须钉住「商品流量」那个 tab，否则脚本会跑到别的页面去
TARGET_ID = os.environ.get("TARGET_ID", "E23FAD857ABE7424E74B1D8E13F39A0C")
OUT = Path(os.environ.get(
    "FLUX_OUT", r"D:\AI-workplace\TEMU\data\bhy\bhy_flux_boost_price.json"))


def run(args, timeout=180):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError(f"no output: rc={p.returncode} err={p.stderr[:400]}")
    return json.loads(p.stdout)


def exec_js(js, timeout=180):
    args = ["page", "exec", "--store-id", STORE_ID]
    if TARGET_ID:
        args += ["--target-id", TARGET_ID]
    inner = run(args + ["--script", js], timeout)["data"]["data"]
    if inner.get("exceptionDetails"):
        raise RuntimeError("page exception: " + json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:800])
    return inner["result"]


def exec_json(js):
    raw = exec_js(js)
    return json.loads(raw) if isinstance(raw, str) else raw


def js(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8").strip()


CLICK_FILTER = js("click_filter_grow.js")
CLICK_EFFECT_BY_SPU = js("click_effect_by_spu.js")
LIST_ROWS = js("extract_grow_rows.js")
EXTRACT_PRICE = js("extract_price.js")
CLOSE_DRAWER = js("close_drawer.js")


def click_effect(spu: str):
    """点指定 SPU 行的「查看效果」。

    值走 `window.__SPU__` 而不是脚本顶层 `const/var`：`Runtime.evaluate` 在同一上下文
    反复执行，顶层声明会撞上遗留的全局绑定（SyntaxError: Identifier already declared），
    `window` 属性既不声明全局、又可覆盖。
    """
    exec_js("window.__SPU__ = %s" % json.dumps(spu))
    return exec_json(CLICK_EFFECT_BY_SPU)


def close_drawer():
    try:
        exec_js(CLOSE_DRAWER)
    except Exception as e:  # noqa: BLE001
        log("  关抽屉失败:", str(e)[:100])


def log(msg: str):
    """立刻刷出并落盘，避免长跑时看不到进度。"""
    print(msg, flush=True)


def main():
    log(f"== 开始 {time.strftime('%Y-%m-%d %H:%M:%S')} ==")
    here = exec_json("(() => JSON.stringify({url: location.href, title: document.title}))()")
    if "flux-analysis" not in (here.get("url") or ""):
        log("!! 当前标签页不是商品流量页:", here)
        return 3
    # 1. 切到「流量加速中」
    log(exec_js(CLICK_FILTER))
    time.sleep(3)
    listing = exec_json(LIST_ROWS)
    rows = [r for r in listing["rows"] if r.get("spu")]
    todo = [r for r in rows if r["hasEffect"]]
    skip = [r for r in rows if not r["hasEffect"]]
    log(f"流量加速中 {listing.get('total')} 条；可抓 {len(todo)}，无查看效果 {len(skip)}")
    for r in skip:
        log(f"  [跳过-无查看效果] SPU {r['spu']} {r['title'][:40]}")

    records = []
    for i, r in enumerate(todo, 1):
        spu = r["spu"]
        data = None
        # 抽屉关不掉（close icon 不响应脚本点击），但它会**原地换成下一个商品**，
        # 所以不需要关闭；用「抽屉回显 SPU == 目标 SPU」做校验 + 重试即可。
        for attempt in range(1, 4):
            try:
                res = click_effect(spu)
                if not res.get("ok"):
                    log(f"[{i}/{len(todo)}] SPU {spu} 点不到查看效果: {res.get('msg')}")
                    time.sleep(2)
                    continue
                for _ in range(6):          # 最多等约 12s 让抽屉换页
                    time.sleep(2)
                    try:
                        d = exec_json(EXTRACT_PRICE)
                    except Exception as e:  # noqa: BLE001
                        log(f"    解析异常: {str(e)[:100]}")
                        break
                    if d.get("spu") == spu and d.get("skus"):
                        data = d
                        break
            except Exception as e:  # noqa: BLE001
                log(f"    第{attempt}次尝试异常: {str(e)[:120]}")
            time.sleep(2)
            if attempt < 3:
                log(f"    重试 {spu} (第{attempt + 1}次)")
        if not data:
            log(f"[{i}/{len(todo)}] SPU {spu} !! 重试 3 次仍未拿到明细，跳过")
            continue
        records.append({
            "spu": spu,
            "title": data.get("title") or r.get("title"),
            "state": data.get("state"),
            "period_from": data.get("periodFrom"),
            "period_to": data.get("periodTo"),
            "skus": data["skus"],
        })
        s0 = data["skus"][0]
        log(f"[{i}/{len(todo)}] SPU {spu} {len(data['skus'])} 个尺码 "
            f"{s0['orig']} -> {s0['after']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    total_sku = sum(len(x["skus"]) for x in records)
    log(f"\n完成：SPU {len(records)}/{len(todo)}，SKU 明细 {total_sku} 行 -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())