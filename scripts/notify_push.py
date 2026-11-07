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
2. **选路统一交给 `net_route`**（2026-11-07 用户口径：国内 IP 直连、其他走代理）。
   以前这里写「飞书在墙外、必须尊重 HTTP(S)_PROXY」，而 `meow_notify` 写「必须
   绕开代理」—— 两个相反的口径各自都有实测依据，本质是因为**判据搞错了**：
   该看的是对面 IP 在不在国内，不是「这家的服务应该在哪」。
   实测 `open.feishu.cn` 解析到 122.225.209.x / 61.153.101.x（阿里云国内节点），
   直连 0.17s 就通；而环境变量里那个 `10808` 是**已废弃的 v2rayN 端口**，
   照它连直接 `WinError 10061`。两件事混在一起，就成了「飞书有时通有时不通」。
   ⇒ 现在统一 `net_route.open_url()`，不再各自维护代理口径。
3. **飞书要带关键词**。群机器人开了「关键词」安全校验，命中不了就回
   `19024`（实测）。本机所有已成功的推送都含 `TEMU`，所以标题统一以 `TEMU` 开头。

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

try:                                              # 统一选路（国内直连 / 墙外代理）
    import net_route                              # noqa: E402
except Exception as _e:                           # noqa: BLE001
    net_route = None
    print("[notify] net_route 不可用，回退默认 opener：%s" % _e)

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


def _opener_for(hook: str):
    """按「国内直连 / 墙外代理」选 opener。拿不到就返回 None（调用方回退默认 opener）。

    单独抽成函数是为了**留一个可替换的接缝**：单测可以塞假 opener，
    不必去 mock 整个 `urllib.request.urlopen`（opener 内部不走它）。
    """
    if net_route is None:
        return None
    try:
        return net_route.opener_for(hook)[0]
    except Exception:                              # noqa: BLE001
        return None


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
        # 选路交给 net_route：open.feishu.cn 实测解析在国内段 → 直连；
        # 万一以后解析到境外节点，它会自动改走代理。
        opener = _opener_for(hook)
        if opener is None:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        else:
            with opener.open(req, timeout=timeout) as r:
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


#: 群机器人纯文本消息的**单条上限**。飞书文档写的是 30 KB，这里按字符留足余量：
#: 中文一个字 3 字节，15000 字符就已经 45 KB 了 —— 超了飞书直接回 400，
#: 而这条消息是定时任务唯一的产出，宁可截断+提示看面板，也不能整条发不出去。
GROUP_TEXT_MAX_CHARS = 8000


def to_group_text(msg: str) -> str:
    """把给**应用机器人**写的 markdown-ish 正文改成群机器人能读的纯文本。

    为什么需要：原来的两条推送是发给「应用机器人私聊」的，用的是
    `**粗体**`、`·` 这类排版。群机器人的 `text` 消息**不解析 markdown**
    —— 直接发过去就是满屏星号。这里只去掉标记、不改内容。
    """
    t = str(msg or "")
    t = t.replace("**", "")                       # 粗体标记没有渲染意义，只会干扰阅读
    t = t.replace("`", "")
    if len(t) > GROUP_TEXT_MAX_CHARS:
        t = t[:GROUP_TEXT_MAX_CHARS].rstrip() + "\n……（已截断，完整清单看面板）"
    return t


def push_group(title: str, msg: str, url: str | None = None) -> tuple[bool, str]:
    """推 **TEMU 机器人群**（群机器人 webhook）。业务清单类通知走这条。

    ★ 2026-11-04 用户要求：把「推送到飞书应用」的定时任务全部转到 TEMU 机器人群。
      原来预售提醒和在售款清单走的是**应用机器人私聊**（`im/v1/messages` +
      本人 open_id），只有开发者账号自己能看到；群机器人是团队共用的那条通道，
      也是看门狗告警一直在用的 —— 两套通道并存只会让人漏看一半消息。
    """
    return push_feishu(title, to_group_text(msg), url=url)


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
