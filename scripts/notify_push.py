# -*- coding: utf-8 -*-
"""业务通知：一条消息同时推**飞书群 + 手机 MeoW**。

为什么要单独一层（2026-10-30）
------------------------------
`watch_tasks.py` 里已经有一套「飞书 + MeoW」的推送代码，但那是**任务看门狗**专用的：
它的输入是「哪个定时任务失败了」。而业务侧要推的是「今天有几条异常单」——
消息形状完全不同，硬塞进去只会让看门狗长出第二副骨架。

这里把**通道**抽出来，业务脚本只管「说什么」，不管「怎么发」：

    from notify_push import push
    push("标题", "正文", url=...)          # 飞书 + 手机各一条，返回两个结果

四条硬约束（都来自已经踩过的坑）
--------------------------------
1. **永不抛异常**。调用方都挂在业务收尾路径上，推送失败只能少一条消息，
   绝不能把已经做完的业务（表已导出）搞成失败。
2. **MeoW 必须绕开本机代理**。环境里有 `HTTP(S)_PROXY=127.0.0.1:10808`，
   走代理 MeoW 会回 `429 IP限制`（实测 2026-10-28：直连 5/5 成功、走代理基本全 429）。
   所以手机通道复用 `meow_notify.py`（它自己 `ProxyHandler({})`）。
3. **飞书要带关键词**。群机器人开了「关键词」安全校验，命中不了就回
   `19024`（实测）。本机所有已成功的推送都含 `TEMU`，所以标题统一以 `TEMU` 开头。
4. **飞书走代理**。open.feishu.cn 在墙外，**不能**像 MeoW 那样清空代理；
   这里刻意用默认 opener（尊重环境变量）。

用法
----
    python scripts\\notify_push.py "正文" --title "TEMU xxx" [--url ...] [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:                                              # 手机通道（可能缺失）
    import meow_notify                            # noqa: E402
except Exception as _e:                           # noqa: BLE001
    meow_notify = None
    print("[notify] meow_notify 不可用，手机推送将跳过：%s" % _e)

try:                                              # 被计划任务捕获时统一 UTF-8
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                 # noqa: BLE001
    pass

#: 群机器人「关键词」校验：标题里必须出现这些词之一（本机全部成功案例都含 TEMU）
FEISHU_KEYWORD = "TEMU"
FEISHU_TIMEOUT = 25


def feishu_webhook() -> str:
    """webhook：环境变量 → 用户级环境变量。空串 = 没配。"""
    url = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    if url:
        return url
    if os.name == "nt":
        # 计划任务里继承了用户环境变量，但手工在别处跑时可能没有 —— 兜一层
        try:
            import subprocess
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Environment]::GetEnvironmentVariable('FEISHU_WEBHOOK','User')"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15)
            return (p.stdout or "").strip()
        except Exception:                          # noqa: BLE001
            return ""
    return ""


def push_feishu(title: str, msg: str, url: str | None = None,
                timeout: int = FEISHU_TIMEOUT) -> tuple[bool, str]:
    """推飞书群（text 消息）。**绝不抛异常。**"""
    hook = feishu_webhook()
    if not hook:
        return False, "未设置 FEISHU_WEBHOOK，跳过"
    text = "%s\n%s" % (title, msg) if title else msg
    if url:
        text += "\n%s" % url
    body = json.dumps({"msg_type": "text", "content": {"text": text}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        hook, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        # 飞书在墙外：这里**必须**用默认 opener（尊重 HTTP(S)_PROXY），
        # 和 meow_notify 的清空代理正好相反。
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        res = json.loads(raw)
        code = res.get("code")
        if code == 0:
            return True, "OK"
        if code == 19024:
            return False, ("code=19024 关键词校验未通过（标题须含 %r）" % FEISHU_KEYWORD)
        return False, "code=%s %s" % (code, str(res.get("msg"))[:100])
    except Exception as e:                         # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:140])


def push_meow(title: str, msg: str, url: str | None = None) -> tuple[bool, str]:
    """推手机（MeoW）。**绝不抛异常。**"""
    if meow_notify is None:
        return False, "meow_notify 未导入"
    try:
        return meow_notify.send(title, msg, url=url)
    except Exception as e:                         # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:140])


def push(title: str, msg: str, url: str | None = None,
         channels: tuple[str, ...] = ("feishu", "meow")) -> dict:
    """两个通道各推一条。返回 {'feishu': (ok, detail), 'meow': (ok, detail)}。

    **永不抛异常**，也**永不因为某个通道失败而影响另一个**。
    """
    out: dict = {}
    if "feishu" in channels:
        out["feishu"] = push_feishu(title, msg, url=url)
    if "meow" in channels:
        out["meow"] = push_meow(title, msg, url=url)
    return out


def brief(results: dict) -> str:
    """把 push() 的结果压成一行，给日志用。"""
    parts = []
    for k in ("feishu", "meow"):
        if k in results:
            ok, detail = results[k]
            parts.append("%s:%s%s" % (k, "OK" if ok else "FAIL",
                                      "" if ok else "(%s)" % detail[:80]))
    return " ".join(parts) or "无通道"


def main() -> int:
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    dry = "--dry-run" in args
    title, url, rest = "TEMU 通知", None, []
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
        print("[dry-run] feishu webhook=%s"
              % ("已配置" if feishu_webhook() else "未配置"))
        return 0
    res = push(title, msg, url=url)
    print("[notify] %s" % brief(res))
    return 0 if all(ok for ok, _ in res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
