# -*- coding: utf-8 -*-
"""重点关注 SPU 监控：抓取各店限流清单，比对重点 SPU 名单，命中则提醒。

用法（单次检查）：
  python scripts/bel_goods/watch_focus.py

可反复运行；也可由外部调度器/定时任务调用。命中重点 SPU 时退出码为 1，
并把结果追加写入 data/<store>/_watch_history.jsonl，便于面板读取。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:          # 便于 import sync_limited_db
    sys.path.insert(0, str(HERE))

# 店铺 -> (store_id, 紫鸟窗口名)
STORES = {
    "BEL": ("27677489075954", "TM-BEL-MGL"),
    "BHY": ("27697436126374", "TM-BHY-YJF"),
}

# 动态重点 SPU：由 fetch_focus_spus.py 从「销量分析」页抓取
# （近7日 SKU 销量 >= 1 的 SPU），落在 data/focus/<store>_focus.json。
FOCUS_DIR = ROOT / "data" / "focus"

# 兜底名单：仅在动态名单**尚未生成**时使用（首次跑之前）。
# 动态名单存在时**完全以动态名单为准**，不再合并这份写死的清单。
FOCUS_FALLBACK = {
    "BEL": ["7502713672", "5745761504", "3082143968", "1224162395",
            "8118634665", "3740374018", "1249844154"],
    "BHY": ["1289576127", "3248169459", "3775612405", "3194032323",
            "1586673761", "7557422941", "7235257044", "8312381638"],
}


def load_focus(store: str) -> tuple[list[str], str]:
    """读该店的重点 SPU 名单。

    返回 (spu 列表, 来源说明)。动态名单存在就用它；否则退回写死的兜底名单。
    """
    path = FOCUS_DIR / f"{store.lower()}_focus.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            spus = [str(x["spuId"]) for x in payload.get("spus", []) if x.get("spuId")]
            if spus:
                win = payload.get("window", "?")
                return spus, f"动态({win}, {payload.get('generatedAt', '?')})"
        except Exception as e:  # noqa: BLE001
            print(f"[{store}] 动态名单解析失败，退回兜底名单: {str(e)[:80]}")
    return FOCUS_FALLBACK.get(store, []), "兜底(写死名单)"

TRAFFIC_URL = "https://agentseller.temu.com/main/adjust-price-manage/high-price"


def clean(c):
    c = str(c or "").strip()
    if "." in c and c.rsplit(".", 1)[-1].isdigit():
        c = c[:c.rindex(".")]
    return c


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"


def _cli(args, timeout=240):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout


def _page_state(store_id: str) -> tuple[str, int]:
    """读窗口当前 URL + 表内数据行数（行数用于判断数据是否已加载完）。

    计数口径与 extract_traffic.js 一致：tr 有 7 个单元格且含 'SPU ID'。
    """
    js = ("JSON.stringify({url:location.href,"
          "n:Array.from(document.querySelectorAll('tr')).filter(t=>"
          "t.children.length===7&&/SPU\\s*ID/.test(t.innerText||'')).length})")
    _, out = _cli(["page", "exec", "--store-id", store_id, "--script", js])
    try:
        d = json.loads(json.loads(out)["data"]["data"]["result"])
        return d.get("url", ""), int(d.get("n", 0))
    except Exception:  # noqa: BLE001
        return "", 0


def _current_url(store_id: str) -> str:
    return _page_state(store_id)[0]


def _on_traffic_page(store_id: str) -> bool:
    return "/adjust-price-manage/high-price" in _current_url(store_id)


def ensure_page(store_id: str) -> bool:
    """确保店铺窗口停在商品流量视角页**且数据已加载**（否则抓不到数据）。

    2026-10-12 加固。原实现有两处**静默**缺陷，BHY 在 12:30 那次因此失败：
      1. `page visit` 的返回码被丢弃 —— 导航失败也不知情，继续往下走；
      2. 固定 sleep 8s 后只查一次 —— SPA 跳转慢时（该时刻与 PW 迁移任务
         同刻启动、抢资源）会把**尚未落地**误判成**进不去**。

    改法：检查返回码 + 轮询等待落地（最长 ~42s）+ 失败重试一次；
    落地后还要**等表格行数稳定**才返回 —— 只判 URL 会漏掉
    「URL 已变但数据未渲染」的窗口期（那样抓到的就是 0 行）。
    """
    import time

    started = time.time()

    if _on_traffic_page(store_id):
        landed = True
    else:
        landed = False
        last_url = ""
        for attempt in (1, 2):
            rc, out = _cli(["page", "visit", "--store-id", store_id, "--url", TRAFFIC_URL,
                            "--wait-until", "domcontentloaded", "--timeout", "90000"], timeout=180)
            if rc != 0:
                print(f"!! page visit 失败（第 {attempt} 次, rc={rc}）: {(out or '')[-200:]}")

            for _ in range(14):
                time.sleep(3)
                if _on_traffic_page(store_id):
                    landed = True
                    break
            if landed:
                break

            last_url = _current_url(store_id)
            print(f"!! 第 {attempt} 次导航未落到流量页，当前: {last_url}")

        if not landed:
            print(f"!! 未能进入商品流量视角页，当前: {last_url}")
            return False

    # ---- 数据就绪：行数连续两次一致才算稳定；最短仍等满 8s（保留原缓冲）----
    prev, stable = -1, 0
    for _ in range(12):
        time.sleep(2)
        _, n = _page_state(store_id)
        if n == prev and n > 0:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prev = n

    waited = time.time() - started
    if waited < 8:
        time.sleep(8 - waited)
    return True


def fetch_traffic(store_id: str) -> list[dict]:
    """复用 fetch_traffic.py 抓取某店限流清单。"""
    env = dict(os.environ)
    env["BEL_STORE_ID"] = store_id
    env["TRAFFIC_OUT"] = str(ROOT / "data" / "_tmp_traffic.json")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, str(HERE / "fetch_traffic.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if p.returncode != 0:
        raise RuntimeError(f"抓取失败: {p.stderr[-400:]}")
    return json.loads((ROOT / "data" / "_tmp_traffic.json").read_text(encoding="utf-8"))


def check(store: str, store_id: str) -> dict:
    if not ensure_page(store_id):
        raise RuntimeError("店铺窗口不在商品流量视角页")
    rows = fetch_traffic(store_id)
    focus, focus_src = load_focus(store)
    hits, safe = [], []
    fset = set(focus)
    seen = set()
    for r in rows:
        spu = str(r["spu_id"])
        if spu in fset:
            key = (spu, clean(r["sku_code"]))
            if key not in seen:
                seen.add(key)
                hits.append({
                    "spu_id": spu,
                    "sku_code": clean(r["sku_code"]),
                    "title": r["title"],
                    "flow_status": r["flow_status"],
                    "traffic_state": r["status"],
                    "declare_price": r.get("current_price"),
                    "target_price": r.get("target_price"),
                    "activity_price": r.get("activity_price"),
                })
    for s in focus:
        if s not in {h["spu_id"] for h in hits}:
            safe.append(s)

    # 全量限流货号快照（用于历史趋势：哪些款反复被限流）
    limited_codes = []
    seen_c = set()
    for r in rows:
        c = clean(r["sku_code"])
        if c and c not in seen_c:
            seen_c.add(c)
            limited_codes.append({
                "sku_code": c,
                "spu_id": str(r["spu_id"]),
                "flow_status": r["flow_status"],
                "traffic_state": r.get("status", ""),
                "sales7": num(r["sales7"]),
                "declare_price": num(r.get("current_price")),
                "target_price": num(r.get("target_price")),
                "activity_price": num(r.get("activity_price")),
                "title": r.get("title", ""),
                "site": r.get("site", ""),
            })

    return {
        "store": store,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "limited_total": len(rows),
        "focus_total": len(focus),
        "focus_source": focus_src,
        "hit_count": len(hits),
        "hits": hits,
        "safe": safe,
        "limited_codes": limited_codes,
    }


def main():
    results = []
    for store, (sid, _win) in STORES.items():
        try:
            r = check(store, sid)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            results.append({"store": store, "error": str(e)[:300]})

    # 区分三种结局，不能混为一谈（2026-10-12 修复）：
    #   ok     = 至少抓到一个店，且没抓到的店记为 error
    #   fail   = 所有店都抓取失败 -> 绝不能报成功，否则会推出「一切正常」的假消息
    failed = [r["store"] for r in results if "error" in r]
    ok_stores = [r for r in results if "error" not in r]
    all_failed = not ok_stores

    out = ROOT / "data" / "focus_watch_latest.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 只有真的抓到数据才写历史 / 回写数据库，
    # 避免把「抓取失败的空快照」当成「今天没有限流」污染趋势。
    if not all_failed:
        with (ROOT / "data" / "focus_watch_history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(results, ensure_ascii=False) + "\n")

        # 把本次快照写回 SQLite（面板告警卡和 admin 后台读的就是这张表）
        try:
            import sync_limited_db
            for s in sync_limited_db.sync_all(results):
                if "error" in s:
                    print(f"[SYNC] {s['store']} 写库失败: {s['error']}")
                elif "skipped" in s:
                    print(f"[SYNC] {s['store']} 跳过: {s['skipped']}")
                else:
                    print(f"[SYNC] {s['store']} 已回写 {s['table']} —— {s['rows']} 行")
        except Exception as e:  # noqa: BLE001
            print(f"[SYNC] 写库异常（不影响监控结果）: {e}")
    else:
        print("[SYNC] 全部店铺抓取失败 —— 跳过写库与历史，保留上次有效数据")

    hit_any = False
    for r in results:
        if "error" in r:
            print(f"[{r['store']}] 抓取失败: {r['error']}")
            continue
        print(f"[{r['store']}] 限流总数 {r['limited_total']} · 重点关注 {r['focus_total']} 个 · 命中 {r['hit_count']} 个")
        for h in r["hits"]:
            hit_any = True
            print(f"    !! 重点款被限流  SPU {h['spu_id']}  货号 {h['sku_code']}")
            print(f"       {h['title'][:52]}")
            print(f"       {h['flow_status']} | {h['traffic_state']} | 申报价 ${h['declare_price']} → 建议 ${h['target_price']}")
        print(f"    安全: {' '.join(r['safe'])}")
    print()
    print("已写入", out)

    # 退出码：0=全部正常  1=有重点款被限流  2=抓取失败（区别于前两者！）
    if all_failed:
        print()
        print(f"!! 全部店铺抓取失败（{', '.join(failed)}）—— 通常是紫鸟店铺窗口未开或登录态失效")
        print("!! 请检查：店铺窗口是否打开、是否需要重新授权")
        return 2
    if failed:
        print()
        print(f"!! 部分店铺抓取失败: {', '.join(failed)}（其余店铺结果仍有效）")
        return 1 if hit_any else 3
    return 1 if hit_any else 0


if __name__ == "__main__":
    raise SystemExit(main())