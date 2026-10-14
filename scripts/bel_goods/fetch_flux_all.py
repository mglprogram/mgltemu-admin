# -*- coding: utf-8 -*-
"""抓取「商品流量」页的流量加速数据（分店 / 分板块）。

板块：
  accelerating  快速筛选「流量加速中」    —— 逐 SPU 开「查看效果」抽屉，取尺码级申报价
  pending       快速筛选「流量加速器待开启」—— 取列表行数据（流量/转化/优化方式）

只读：只点「查看效果」「查看详情」与分页；**绝不点**「立即开启」「报名流量加速器」
「一键提效」等写操作。

用法：
  python fetch_flux_all.py --store BEL --section accelerating
  python fetch_flux_all.py --store BHY --section pending
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = Path(r"D:\AI-workplace\TEMU")
ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"

STORES = {
    "BEL": {"storeId": "27677489075954", "label": "BEL 店", "window": "TM-BEL-MGL"},
    "BHY": {"storeId": "27697436126374", "label": "BHY 店", "window": "TM-BHY-YJF"},
}

FILTERS = {
    "accelerating": "流量加速中",
    "pending": "流量加速器待开启",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def run(args, timeout=180):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError(f"no output: rc={p.returncode} err={p.stderr[:300]}")
    return json.loads(p.stdout)


class Page:
    def __init__(self, store_id: str, target_id: str):
        self.store_id = store_id
        self.target_id = target_id

    def js(self, script: str, timeout=180):
        args = ["page", "exec", "--store-id", self.store_id]
        if self.target_id:
            args += ["--target-id", self.target_id]
        inner = run(args + ["--script", script], timeout)["data"]["data"]
        if inner.get("exceptionDetails"):
            raise RuntimeError("JS 异常: " +
                               json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:400])
        return inner["result"]

    def j(self, script: str, timeout=180):
        raw = self.js(script, timeout)
        return json.loads(raw) if isinstance(raw, str) else raw

    def file(self, name: str) -> str:
        return (HERE / name).read_text(encoding="utf-8").strip()

    def run_file(self, name: str, **vars):
        for k, v in vars.items():
            self.js("window.%s = %s" % (k, json.dumps(v, ensure_ascii=False)))
        return self.j(self.file(name))

    def url(self) -> str:
        return self.j("(() => JSON.stringify(location.href))()")


def find_target(store_id: str) -> str:
    """列出该店铺窗口的 tab，挑「商品流量」页（或认证页，供后续处理）。"""
    out = run(["page", "exec", "--store-id", store_id,
               "--script", "(() => JSON.stringify({url: location.href}))()"])
    return out["data"]["data"].get("targetId") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--section", required=True, choices=list(FILTERS))
    ap.add_argument("--target", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    st = STORES[a.store]
    sid = st["storeId"]
    target = a.target or find_target(sid)
    page = Page(sid, target)

    log(f"== {a.store} ({st['label']}) / {a.section} {time.strftime('%H:%M:%S')} ==")
    cur = page.url()
    if "flux-analysis" not in cur:
        log(f"!! 当前不在商品流量页: {cur[:110]}")
        return 3
    log(f"   target={target}")

    # 0. 中途掉线检测：TEMU 登录态会**在抓取过程中**失效，页面被重定向到
    #    /auth/authentication，之后所有点击都落空。每轮循环前查一次，
    #    发现掉线立即中止（已抓到的数据照常落盘），由上层重新登录后重跑。
    def still_on_flux() -> bool:
        try:
            return "flux-analysis" in (page.url() or "")
        except Exception:  # noqa: BLE001
            return False

    # 1. 切筛选
    fr = page.run_file("click_filter.js", FILTER=FILTERS[a.section])
    log("  " + json.dumps(fr, ensure_ascii=False))
    # 连快速筛选卡片都找不到 → 这个 tab 根本不是商品流量页，立刻判失败
    if not fr.get("ok") and not fr.get("seen"):
        log("!! 页面上找不到快速筛选卡片 —— 当前 tab 不是商品流量页，放弃（不覆盖产物）")
        return 3
    time.sleep(3)

    aborted = False

    # 2. 逐页遍历（**取价必须在当页做**：分页后 DOM 只剩当前页的行，
    #    跨页再去点上一页的 SPU 会「row not found」）
    all_rows = []
    seen = set()
    page_no = 1
    while page_no <= 10:                          # 上限 10 页，防死循环
        if page_no > 1:
            r = page.run_file("click_page.js", PAGE=str(page_no))
            if not r.get("ok"):
                break
            time.sleep(3.5)
        if not still_on_flux():
            log(f"  !! 翻页时登录态已失效，停止（已抓 {len(all_rows)} 个）")
            aborted = True
            break
        data = page.j(page.file("extract_list.js"))
        rows = [r for r in data.get("rows", []) if r["spuId"] not in seen]
        for r in rows:
            seen.add(r["spuId"])
        all_rows += rows
        total = data.get("total")
        log(f"  第{page_no}页: 新增 {len(rows)}（累计 {len(all_rows)}/{total}）")

        # 本页内立即取价
        if a.section == "accelerating":
            todo = [r for r in rows if "查看效果" in r.get("readOnlyLinks", [])]
            for i, r in enumerate(todo, 1):
                if not still_on_flux():
                    log(f"    !! 第{i}个起登录态失效（页面已离开商品流量页），中止本板块")
                    aborted = True
                    break
                spu = r["spuId"]
                got = None
                for attempt in range(1, 4):
                    try:
                        res = page.run_file("click_effect_by_spu.js", SPU=spu)
                        if not res.get("ok"):
                            time.sleep(2)
                            continue
                        for _ in range(6):
                            time.sleep(2)
                            try:
                                d = page.j(page.file("extract_price.js"))
                            except Exception as e:  # noqa: BLE001
                                log(f"      解析异常 {str(e)[:70]}")
                                break
                            if d.get("spu") == spu and d.get("skus"):
                                got = d
                                break
                    except Exception as e:  # noqa: BLE001
                        log(f"      第{attempt}次异常 {str(e)[:80]}")
                    time.sleep(2)
                if got:
                    r["state"] = got.get("state")
                    r["periodFrom"] = got.get("periodFrom")
                    r["periodTo"] = got.get("periodTo")
                    r["skus"] = got.get("skus", [])
                    s0 = got["skus"][0]
                    log(f"    [p{page_no} {i}/{len(todo)}] {spu} {len(got['skus'])} 尺码 "
                        f"{s0['orig']}->{s0['after']}")
                else:
                    r["skus"] = []
                    log(f"    [p{page_no} {i}/{len(todo)}] {spu} !! 取价失败")

        if str(total) == str(len(all_rows)):
            break
        page_no += 1

    # ---- 与旧产物按 SPU 合并：新数据缺价的，用旧数据里同 SPU 的价补上 ----
    # 登录态中途掉线时，后半段会全缺价；直接覆盖会把上一次的好数据弄丢。
    # 策略：以本次的 SPU 名单为准，价格按 spuId 从旧文件回填（旧值不覆盖新值）。
    out = Path(a.out) if a.out else ROOT / "data" / "flux" / f"{a.store.lower()}_{a.section}.json"
    filled = 0
    if a.section == "accelerating" and out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
            prev_by = {x.get("spuId"): x for x in prev.get("list", [])}
            for r in all_rows:
                if r.get("skus"):
                    continue
                old = prev_by.get(r["spuId"])
                if old and old.get("skus"):
                    r["skus"] = old["skus"]
                    r["state"] = old.get("state")
                    r["periodFrom"] = old.get("periodFrom")
                    r["periodTo"] = old.get("periodTo")
                    r["priceFromPrevRun"] = prev.get("generatedAt")
                    filled += 1
            if filled:
                log(f"  [合并] {filled} 个 SPU 的尺码价沿用上次产物（本次未取到）")
        except Exception as e:  # noqa: BLE001
            log(f"  [合并] 读取旧产物失败，跳过：{str(e)[:80]}")

    priced = sum(1 for r in all_rows if r.get("skus"))

    # **绝不写空结果**：抓取若跑到错误页面（无快速筛选卡片）或一条都没解析到，
    # 直接判失败返回，保留上一次的产物文件。
    # 2026-10-10 踩过：一次「跑到别的页面」的运行把 0 条写进了产物，
    # 合并后把线上的好数据也顶掉了。
    if not all_rows:
        log("!! 未解析到任何 SPU（页面可能不是商品流量页）—— 保留上一次产物，不覆盖")
        return 3

    payload = {
        "store": a.store,
        "storeLabel": st["label"],
        "section": a.section,
        "sectionLabel": "已开启" if a.section == "accelerating" else "待开启",
        "filter": FILTERS[a.section],
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(all_rows),
        "priced": priced,
        "aborted": aborted,
        "filledFromPrev": filled,
        "list": all_rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"完成：{len(all_rows)} 个 SPU（有价 {priced}，回填 {filled}）-> {out}")
    # 退出码：0 正常；2 中途掉线（上层可重新登录后重跑）
    return 2 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())