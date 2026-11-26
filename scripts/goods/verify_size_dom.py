"""端到端验证：只用接口给草稿挂尺码表，然后开一个新标签页读 DOM 确认后台真的渲染。

不依赖人工点击、也不依赖人工刷新。
"""
import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\goods")
import temu_publish_api as api
import websockets

DRAFT = int(sys.argv[1]) if len(sys.argv) > 1 else 41471051798
PORT = 61407


def new_tab(url: str) -> dict:
    q = urllib.parse.quote(url, safe=":/?=&%")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?{q}", method="PUT")
    return json.loads(urllib.request.urlopen(req).read().decode())


def close_tab(tid: str) -> None:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tid}").read()
    except Exception:
        pass


READ_JS = r"""
(function(){
  var all = document.body.innerText || '';
  var has = all.indexOf('添加尺码表') >= 0;
  return JSON.stringify({
    len: all.length,
    stillAddBtn: has,
    snippet: (function(){
      var i = all.indexOf('尺码表');
      return i >= 0 ? all.slice(i, i + 400) : '';
    })()
  });
})()
"""


async def read_after(url: str, wait: float = 18.0) -> dict:
    tab = new_tab(url)
    tid = tab["id"]
    try:
        async with websockets.connect(tab["webSocketDebuggerUrl"],
                                      max_size=32 * 1024 * 1024,
                                      ping_interval=None, ping_timeout=None) as w:
            await w.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            end = time.time() + wait
            while time.time() < end:
                try:
                    await asyncio.wait_for(w.recv(), 2.0)
                except asyncio.TimeoutError:
                    pass
            await w.send(json.dumps({"id": 99, "method": "Runtime.evaluate",
                                     "params": {"expression": READ_JS,
                                                "returnByValue": True,
                                                "awaitPromise": False}}))
            while True:
                raw = await asyncio.wait_for(w.recv(), 30.0)
                m = json.loads(raw)
                if m.get("id") == 99:
                    return json.loads(m["result"]["result"]["value"])
    finally:
        close_tab(tid)


def main() -> int:
    s = api.TemuSession(PORT)
    s.mallid = s.get_mallid()
    d = s.draft_query(DRAFT)
    print("draft", DRAFT, "|", (d.get("productName") or "")[:50])
    print("  sizeTemplateIds:", d.get("sizeTemplateIds"))

    url = f"https://agentseller.temu.com/goods/edit?productDraftId={DRAFT}&from=productDraftList"
    res = asyncio.run(read_after(url))
    print("  DOM 里还有「添加尺码表」按钮:", res["stillAddBtn"])
    print("  片段:", res["snippet"][:260].replace("\n", " | "))
    return 0


raise SystemExit(main())
