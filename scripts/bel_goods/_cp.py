import os as _os
__PW__ = _os.environ.get('DASH_PASSWORD') or ''
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, create_target, http_targets
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9339
proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
    "--user-data-dir=" + str(Path.home()/".dsh-chk9"), "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--window-size=1500,1000", "about:blank"],
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
    time.sleep(7)
    if ev("!!document.getElementById('gp')"):
        c.send("Runtime.evaluate", {"expression": "document.getElementById('gp').value=__PW__"})
        c.send("Runtime.evaluate", {"expression": "document.getElementById('gb').click()", "userGesture": True})
        time.sleep(9)
    print("COPY SPANS:", ev("document.querySelectorAll('.lim-copy').length"))
    # 模拟双击第一个可复制元素
    print("DBLCLICK:", ev("""(() => {
      const el = document.querySelector('.lim-copy');
      if (!el) return 'none';
      const t = el.innerText.trim();
      el.dispatchEvent(new MouseEvent('dblclick', {bubbles:true}));
      return 'fired on ' + t;
    })()"""))
    time.sleep(2)
    print("MSG:", ev("(() => { const m=document.querySelector('.el-message'); return m?m.innerText.trim():'(no message)'; })()"))
finally:
    proc.terminate()

