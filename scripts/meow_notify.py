# -*- coding: utf-8 -*-
"""meow_notify.py — 把消息推到用户手机（MeoW / api.chuckfang.com）。

为什么单独一层：
  * **必须绕过代理**。本机用户环境里有 HTTP(S)_PROXY=http://127.0.0.1:10808，
    urllib 默认会用它 —— 而 MeoW 服务端对代理出口 IP 回 `429 IP限制，请联系开发者`
    （2026-10-28 实测：同一分钟交替，直连 5/5 成功、走代理基本全 429）。
    所以这里用 `ProxyHandler({})` 显式清空代理，而不是靠环境变量。
  * **永不抛异常**。调用方都是告警/收尾路径（watch_tasks.py、日报），
    推送失败只该安静地记一行日志，不能把告警本身搞崩。
  * **限频**。非会员 3 秒 1 条，这里用一个小状态文件做跨进程节流。

用法：

    from meow_notify import send
    ok, detail = send("标题", "正文", url="https://...")   # -> (True, "status=200 发送成功")

命令行自测：

    python meow_notify.py "正文" --title "测试" [--url ...] [--html] [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NICK = os.environ.get("MEOW_NICK") or "小缪"
BASE = "https://api.chuckfang.com/"
UA = "MeoW-Notify/1.0 (+TEMU-scheduled-tasks)"

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / "_logs" / "meow-push" / "last_send.txt"
MIN_GAP_SEC = 3.2          # 服务端 3 秒 1 条，留一点余量
MAX_MSG_CHARS = 1800       # 单条太长在通知里没法看，截断并提示


def _throttle() -> None:
    """跨进程节流：距离上次发送不足 3.2 秒就等一等。失败一律忽略。"""
    try:
        last = float(STATE.read_text(encoding="utf-8").strip() or 0)
    except Exception:                                          # noqa: BLE001
        last = 0.0
    gap = time.time() - last
    if 0 <= gap < MIN_GAP_SEC:
        time.sleep(MIN_GAP_SEC - gap)
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text("%.3f" % time.time(), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass


def _clip(msg: str) -> str:
    msg = (msg or "").strip()
    if len(msg) <= MAX_MSG_CHARS:
        return msg
    return msg[:MAX_MSG_CHARS] + "\n…（已截断，共 %d 字）" % len(msg)


def send(title: str, msg: str, url: str | None = None,
         html: bool = False, height: int = 200,
         timeout: int = 20) -> tuple[bool, str]:
    """推一条消息。返回 (是否成功, 说明)。**绝不抛异常。**"""
    try:
        msg = _clip(msg)
        if not msg:
            return False, "正文为空，未发送"
        q = {}
        if html:
            q["msgType"] = "html"
            q["htmlHeight"] = str(height)
        endpoint = BASE + urllib.parse.quote(NICK)
        if q:
            endpoint += "?" + urllib.parse.urlencode(q)
        payload = {"title": title or "MeoW", "msg": msg}
        if url:
            payload["url"] = url
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        # 显式清空代理（本机 HTTP(S)_PROXY 指向 10808，用它会 429）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": UA})
        _throttle()
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        res = json.loads(raw)
        ok = res.get("status") == 200 and res.get("data") is not False
        return ok, "status=%s %s" % (res.get("status"), res.get("msg"))
    except urllib.error.HTTPError as e:                          # noqa: BLE001
        return False, "HTTP %s（多为路径/格式问题）" % e.code
    except Exception as e:                                       # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:160])


def main() -> int:
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    dry = "--dry-run" in args
    html = "--html" in args
    title, url, msg = "MeoW", None, None
    rest = []
    it = iter(args)
    for a in it:
        if a == "--title":
            title = next(it, title)
        elif a == "--url":
            url = next(it, None)
        elif a.startswith("--"):
            continue
        else:
            rest.append(a)
    msg = " ".join(rest)
    if dry:
        print("[dry-run] title=%r url=%r msg=%r" % (title, url, msg))
        return 0
    ok, detail = send(title, msg, url=url, html=html)
    print("[meow] %s %s" % ("OK" if ok else "FAIL", detail))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
