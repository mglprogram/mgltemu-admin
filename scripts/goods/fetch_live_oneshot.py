"""一轮内完成：开 KLS 列表页 → 等就绪 → 读在售中 → 抓预售状态（不让中间步骤被清掉）。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402
import fetch_presale_live_onsale as F  # noqa: E402


async def ensure_page(port: int) -> str:
    """确保有一个可用的 goods/list 页，返回 websocket url。"""
    for _ in range(3):
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "agentseller.temu.com" in (x.get("url") or "")]
        pages.sort(key=lambda x: 0 if "goods/list" in (x.get("url") or "") else 1)
        for p in pages:
            try:
                await asyncio.wait_for(
                    cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                      {"expression": "location.href", "returnByValue": True}),
                    timeout=25)
                print("  复用页 %s" % (p.get("url") or "")[:60], flush=True)
                return p["webSocketDebuggerUrl"]
            except Exception:
                continue
        tid = await cdp_net.new_tab(port, "https://agentseller.temu.com/goods/list",
                                    foreground=False)
        print("  新开页", tid[:8], flush=True)
        await asyncio.sleep(25)
    raise RuntimeError("没有可用页")


async def main(port: int, store: str):
    codes = F.onsale_codes(store if store != "KLS" else "KinglamanSport")
    if not codes:
        from fetch_presale_live_onsale import onsale_codes  # noqa
        codes = []
    print("%s：在售中 %d 个货号" % (store, len(codes)), flush=True)
    ws = await ensure_page(port)
    cap = await cdp_net.NetCapture(ws, match=()).__aenter__()
    mallid = ""
    try:
        try:
            await cap.send("Network.enable", {}, timeout=20)
        except Exception:
            pass
        r = await cap.send("Runtime.evaluate", {
            "expression": ("(function(){var parts=document.cookie.split(';');"
                           "for(var i=0;i<parts.length;i++){var p=parts[i].trim();"
                           "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()"),
            "returnByValue": True}, timeout=25)
        mallid = str((r.get("result") or {}).get("value") or "")
        hdr = api.load_headers(mallid)
        print("  mallid=%s anti=%d" % (mallid or "?", len(hdr.get("Anti-Content") or "")),
              flush=True)
        rows = []
        for i, code in enumerate(codes, 1):
            rows += await F.fetch_one(cap, hdr, code)
            if i % 20 == 0:
                print("  %d/%d …%d 行" % (i, len(codes), len(rows)), flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    if not rows:
        print("!! 0 行，不覆盖")
        return 4
    import json
    import time
    d = json.loads(F.LIVE.read_text(encoding="utf-8")) if F.LIVE.exists() else {}
    d.setdefault("stores", {})[store] = {
        "rows": rows, "source": "后台实读（只在售中）", "onsaleCodes": len(codes),
        "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    d["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["source"] = "fetch_presale_live_onsale.py（接口直读，仅 skcStatus=11 & skcSiteStatus=1）"
    F.LIVE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%s：%d 行（预售中 %d）→ 已写 presale-live.json"
          % (store, len(rows), sum(1 for r in rows if r["presaleOn"])), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]), sys.argv[2])))
