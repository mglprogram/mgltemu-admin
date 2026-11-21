import base64, json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, create_target, http_targets
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9335
proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
    "--user-data-dir=" + str(Path.home()/".dsh-chk3"), "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--window-size=1500,1100", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(5)
    tid = create_target(PORT, "about:blank", background=False); time.sleep(2)
    ws = next((t.get("webSocketDebuggerUrl") for t in http_targets(PORT) if t.get("id")==tid), None)
    c = Cdp(ws); c.send("Page.enable", {})
    def ev(e, to=40):
        r = c.send("Runtime.evaluate", {"expression": e, "returnByValue": True,
                                        "awaitPromise": True, "userGesture": True}, timeout=to)
        return ((r.get("result") or {}).get("result") or {}).get("value")
    c.send("Page.navigate", {"url": "https://mgltemu.us.ci/#/temu/limited"})
    for _ in range(24):
        time.sleep(1.5)
        if ev("document.readyState") == "complete": break
    time.sleep(6)
    if ev("!!document.getElementById('gp')"):
        c.send("Runtime.evaluate", {"expression": "document.getElementById('gp').value='@m20010414'"})
        c.send("Runtime.evaluate", {"expression": "document.getElementById('gb').click()", "userGesture": True})
        time.sleep(9)
    # 切 KLS
    c.send("Runtime.evaluate", {"expression": """(() => {
      const b=[...document.querySelectorAll('.el-radio-button')].find(x=>/Kinglaman/.test(x.innerText));
      if(b) b.querySelector('input').click(); return 1;})()""", "userGesture": True})
    time.sleep(4)
    print(ev(open(r"D:\AI-workplace\TEMU\scripts\bel_goods\probe_code.js", encoding="utf-8").read().strip()))
    r = c.send("Page.captureScreenshot", {"format": "png"})
    Path(r"D:\AI-workplace\TEMU\data\_cw2.png").write_bytes(base64.b64decode((r.get("result") or {})["data"]))
    print("shot ok")
finally:
    proc.terminate()
