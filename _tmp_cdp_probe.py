import sys, json
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts")
from goods.ziniao_port import probe_port
from urllib.request import urlopen
import asyncio, websockets

for name in ("TM-BHY-YJF", "TM-BEL-MGL"):
    port = probe_port(name, attempts=1)
    print("==", name, "port", port)
    if not port: continue
    try:
        tg = json.loads(urlopen("http://127.0.0.1:%d/json/list" % port, timeout=5).read().decode("utf-8","replace"))
    except Exception as e:
        print("  list error", e); continue
    print("  targets:", len(tg))
    for t in tg:
        u = (t.get("url") or "")[:70]
        print("   -", t.get("type"), t.get("id")[:8], u)
        if t.get("type") != "page": continue
        ws = t.get("webSocketDebuggerUrl")
        if not ws: print("      no ws url"); continue
        async def probe(ws=ws):
            try:
                async with websockets.connect(ws, open_timeout=6) as c:
                    await c.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":"1+1"}}))
                    r = await asyncio.wait_for(c.recv(), timeout=6)
                    return "OK " + r[:80]
            except Exception as e:
                return "FAIL %s: %s" % (type(e).__name__, str(e)[:100])
        print("     ", asyncio.new_event_loop().run_until_complete(probe()))
