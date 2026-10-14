# -*- coding: utf-8 -*-
"""feishu_bitable.py — 飞书多维表格**只读**客户端（够用就好）。

凭证来源（按优先级）：
  1. 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
  2. `~/.agents/skills/temu-bottom-stock-sync/config.json` 的 `feishu` 段
     （复用那个已授权的应用，避免在本仓库里再存一份密钥）

只用标准库 urllib，并**绕开系统代理**（本机系统代理是给 GitHub 用的 127.0.0.1:10808，
飞书是国内直连；读代理会把请求带进黑洞）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

HOST = "https://open.feishu.cn/open-apis"
SKILL_CFG = Path.home() / ".agents" / "skills" / "temu-bottom-stock-sync" / "config.json"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_TOKEN_CACHE: dict = {}


def _creds() -> tuple:
    app_id = os.environ.get("FEISHU_APP_ID", "")
    secret = os.environ.get("FEISHU_APP_SECRET", "")
    if app_id and secret:
        return app_id, secret
    try:
        cfg = json.loads(SKILL_CFG.read_text(encoding="utf-8-sig"))
        f = cfg.get("feishu") or {}
        return f.get("app_id", ""), f.get("app_secret", "")
    except Exception:  # noqa: BLE001
        return app_id, secret


def _get(url: str, token: str = "", timeout: float = 40.0) -> dict:
    r = urllib.request.Request(url)
    if token:
        r.add_header("Authorization", "Bearer %s" % token)
    with _OPENER.open(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _post(url: str, payload: dict, timeout: float = 40.0) -> dict:
    r = urllib.request.Request(url, data=json.dumps(payload).encode())
    r.add_header("Content-Type", "application/json; charset=utf-8")
    with _OPENER.open(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def token() -> str:
    """tenant_access_token（带内存缓存，提前 5 分钟过期）。"""
    if _TOKEN_CACHE.get("exp", 0) > time.time() + 300:
        return _TOKEN_CACHE["tok"]
    app_id, secret = _creds()
    if not app_id or not secret:
        raise RuntimeError("缺飞书凭证：环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET 或 %s" % SKILL_CFG)
    res = _post(HOST + "/auth/v3/tenant_access_token/internal",
                {"app_id": app_id, "app_secret": secret})
    if res.get("code") != 0:
        raise RuntimeError("取 tenant_access_token 失败：%s %s" % (res.get("code"), res.get("msg")))
    _TOKEN_CACHE.update({"tok": res["tenant_access_token"],
                         "exp": time.time() + float(res.get("expire") or 7200)})
    return _TOKEN_CACHE["tok"]


def list_tables(base: str) -> list:
    """[{name, table_id}, …]"""
    tk = token()
    out, page = [], ""
    while True:
        url = "%s/bitable/v1/apps/%s/tables?page_size=100%s" % (HOST, base, "&page_token=" + page if page else "")
        res = _get(url, tk)
        if res.get("code") != 0:
            raise RuntimeError("列表失败：%s %s" % (res.get("code"), res.get("msg")))
        data = res.get("data") or {}
        out += data.get("items") or []
        if not data.get("has_more"):
            return out
        page = data.get("page_token") or ""


def table_id_by_name(base: str, name: str) -> str:
    for t in list_tables(base):
        if t.get("name") == name:
            return t["table_id"]
    raise RuntimeError("基座 %s 里没有表「%s」" % (base, name))


def list_records(base: str, table_id: str, page_size: int = 500, max_records: int = 20000) -> list:
    """全部记录（自动翻页）。返回 [{field: value, …}, …]。"""
    tk = token()
    out, page = [], ""
    while len(out) < max_records:
        url = ("%s/bitable/v1/apps/%s/tables/%s/records?page_size=%d%s"
               % (HOST, base, table_id, min(page_size, 500), "&page_token=" + page if page else ""))
        res = _get(url, tk)
        if res.get("code") != 0:
            raise RuntimeError("读记录失败：%s %s" % (res.get("code"), res.get("msg")))
        data = res.get("data") or {}
        out += [r.get("fields") or {} for r in (data.get("items") or [])]
        if not data.get("has_more"):
            break
        page = data.get("page_token") or ""
        if not page:
            break
    return out


# ------------------------------------------------------------------ 小工具
def send_text(receive_id: str, text: str, receive_id_type: str = "open_id") -> dict:
    """给某人/某群发一条纯文本（应用机器人）。需要应用有 `im:message:send_as_bot` 且已发布。"""
    tk = token()
    url = "%s/im/v1/messages?receive_id_type=%s" % (HOST, receive_id_type)
    payload = {"receive_id": receive_id, "msg_type": "text",
               "content": json.dumps({"text": text}, ensure_ascii=False)}
    r = urllib.request.Request(url, data=json.dumps(payload).encode())
    r.add_header("Content-Type", "application/json; charset=utf-8")
    r.add_header("Authorization", "Bearer %s" % tk)
    with _OPENER.open(r, timeout=40) as resp:
        res = json.loads(resp.read().decode("utf-8", "replace"))
    if res.get("code") != 0:
        raise RuntimeError("发飞书消息失败：%s %s" % (res.get("code"), res.get("msg")))
    return res


def text_of(v) -> str:
    """飞书文本字段可能是 str / [{'text':…}] / {'text':…} —— 统一成 str。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "".join(text_of(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("value") or "")
    return str(v)


def num_of(v) -> float | None:
    """数字字段（飞书读回来常是字符串）。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = text_of(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def ms_to_day(ms, tz_offset_hours: int = 8) -> str:
    """毫秒时间戳 → 'YYYY-MM-DD'（默认东八区）。"""
    n = num_of(ms)
    if n is None or n <= 0:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(n / 1000.0 + tz_offset_hours * 3600))
