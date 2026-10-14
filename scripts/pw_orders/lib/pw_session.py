# -*- coding: utf-8 -*-
"""帕沃尔海外仓(powerwarehouse.net)会话 —— 仅标准库，直连、显式禁系统代理。

为什么要自己写而不用 requests：
  本域其它脚本（scripts/power）走的是紫鸟 CDP，**没有** requests 依赖；
  pw_orders 是全新的数据源（帕沃尔后台），保持「标准库 + 直连」可以整包带走。

两条铁律（与第二大脑 shipping 域踩过的坑一致）：
  1. **只在连接阶段重试**。请求已发出（读响应阶段）失败 → 绝不自动重试，
     「导出/改单」都是有副作用的 POST，重试 = 重复操作。
     查询类接口（query_order_list / order_info）是只读的，由调用方自己重试。
  2. 帕沃尔是 `Microsoft-IIS/7.5`，偶发 `WinError 10060`（连接超时）是**长尾**，
     不是风控；短退避（1s→3s→8s）基本能自愈。

登录（2026-10-14 复测）：
  GET  /login.php?act=login&lan=                     拿初始 cookie
  POST /login.php  username/password/act=act_login   成功后 cookie 里有 ECS[user_id]
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # 被重定向/管道捕获时统一 UTF-8（本机控制台是 cp936，直跑不乱码）
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = "https://www.powerwarehouse.net/"
LOGIN_URL = BASE + "login.php?act=login&lan="
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BACKOFF = (1.0, 3.0, 8.0, 20.0)
LIST_REF = BASE + "deliver.php?act=list&pb=all"

# 账号**不入库**（本仓库禁止提交真实凭证）。
# 解析顺序：环境变量 PW_USER / PW_PASS → <域根>/pw_account.local.json（.gitignore 已挡）
ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "pw_account.local.json")


def resolve_account(user: str | None = None, pwd: str | None = None) -> tuple[str, str]:
    """取帕沃尔账号。三步：显式入参 → 环境变量 → 本地（不入库的）账号文件。"""
    if user and pwd:
        return user, pwd
    eu, ep = os.environ.get("PW_USER"), os.environ.get("PW_PASS")
    if eu and ep:
        return eu, ep
    if os.path.exists(ACCOUNT_FILE):
        try:
            with open(ACCOUNT_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("user") and (d.get("password") or d.get("pass")):
                return d["user"], d.get("password") or d["pass"]
        except Exception as e:  # noqa: BLE001
            raise PWError("账号文件 %s 读不了：%s" % (ACCOUNT_FILE, e))
    raise PWError(
        "没找到帕沃尔账号。二选一：\n"
        "  1) 设环境变量  PW_USER=账号  PW_PASS=密码\n"
        "  2) 写本地文件 %s：{\"user\":\"...\",\"password\":\"...\"}（已在 .gitignore，不会入库）"
        % ACCOUNT_FILE)


def _strip_proxy_env() -> None:
    """显式清掉系统代理。

    本机注入了 HTTP_PROXY/HTTPS_PROXY（v2rayN 10808），走隧道时帕沃尔会间歇性 502；
    帕沃尔在国内直连 0.3~0.5s/次，**必须直连**。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1,powerwarehouse.net"


class PWError(RuntimeError):
    """帕沃尔会话/协议错误。"""


class PW:
    """一条可复用的 cookie 会话。

    典型用法::

        pw = PW(); pw.login()
        pw.list_page("634418227477419", "2026-01-01", "2026-10-14", page=1)
    """

    def __init__(self, user: str | None = None, pwd: str | None = None,
                 timeout: int = 90, debug_dir: str | None = None) -> None:
        _strip_proxy_env()
        self.user, self.pwd = resolve_account(user, pwd)
        self.timeout = timeout
        self.debug_dir = debug_dir
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}),
        )
        self.op.addheaders = [
            ("User-Agent", UA),
            ("Accept-Language", "zh-CN,zh;q=0.9"),
            ("Accept", "text/html,application/xhtml+xml,*/*;q=0.8"),
        ]

    # ---------------- 底层 ----------------
    @staticmethod
    def _encode_url(url: str) -> str:
        """中文文件名要百分号编码（IIS 只吃编码后的路径），如 `/xls/202610/xxx派送订单.xlsx`。"""
        u = urllib.parse.urljoin(BASE, url)
        sp = urllib.parse.urlsplit(u)
        return urllib.parse.urlunsplit(
            (sp.scheme, sp.netloc, urllib.parse.quote(sp.path), sp.query, sp.fragment))

    def _retry(self, fn, tries: int = 4):
        """只在连接阶段失败时重试（见模块 docstring 铁律 1）。"""
        last: Exception | None = None
        for i in range(tries):
            try:
                return fn()
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                if i < tries - 1:
                    w = BACKOFF[min(i, len(BACKOFF) - 1)]
                    print("   [conn] 重试 %d/%.0fs (%s)" % (i + 1, w, str(e)[:60]), flush=True)
                    time.sleep(w)
        raise PWError("连接失败：%s" % last)

    def _dump(self, name: str, body: bytes) -> None:
        if not self.debug_dir:
            return
        os.makedirs(self.debug_dir, exist_ok=True)
        with open(os.path.join(self.debug_dir, name), "wb") as f:
            f.write(body)

    def get_raw(self, url: str, referer: str | None = None) -> bytes:
        def once() -> bytes:
            req = urllib.request.Request(self._encode_url(url),
                                         headers={"Referer": referer or BASE})
            return self.op.open(req, timeout=self.timeout).read()
        return self._retry(once)

    def post_raw(self, url: str, data, referer: str | None = None,
                 multipart: bool = False) -> bytes:
        def once() -> bytes:
            body, ctype = encode_body(data, multipart)
            req = urllib.request.Request(self._encode_url(url), data=body,
                                         headers={"Referer": referer or BASE,
                                                  "Content-Type": ctype})
            return self.op.open(req, timeout=self.timeout).read()
        return self._retry(once)

    def get(self, url: str, referer: str | None = None, save: str | None = None) -> str:
        raw = self.get_raw(url, referer)
        if save:
            self._dump(save, raw)
        return raw.decode("utf-8", "ignore")

    def post(self, url: str, data, referer: str | None = None, multipart: bool = False,
             save: str | None = None, json_resp: bool = False):
        raw = self.post_raw(url, data, referer, multipart)
        if save:
            self._dump(save, raw)
        txt = raw.decode("utf-8", "ignore")
        if json_resp:
            try:
                return json.loads(txt)
            except Exception as e:  # noqa: BLE001
                raise PWError("响应不是 JSON（可能掉登录）：%s\n%s" % (e, txt[:400]))
        return txt

    # ---------------- 登录 ----------------
    def login(self) -> bool:
        def once() -> None:
            self.op.open(urllib.request.Request(LOGIN_URL), timeout=self.timeout).read()
            body = urllib.parse.urlencode({
                "username": self.user, "password": self.pwd,
                "act": "act_login", "back_act": "./user.php",
            }).encode("utf-8")
            req = urllib.request.Request(BASE + "login.php", data=body, headers={
                "Referer": LOGIN_URL,
                "Content-Type": "application/x-www-form-urlencoded"})
            self.op.open(req, timeout=self.timeout).read()
            if not any("user_id" in c.name for c in self.jar):
                raise PWError("登录失败：没拿到 ECS[user_id] cookie（账号/密码不对？）")
        # 登录是只读语义（无非幂等副作用），整段可重试
        self._retry(lambda: once() or b"")
        return True

    # ---------------- 发货列表 ----------------
    def list_page(self, shop_id: str, start: str, end: str, page: int = 1,
                  page_size: int = 300, extra: dict | None = None) -> dict:
        """deliver.php?act=query_order_list → 原始 JSON。

        ⚠ `page_size` 服务端实测上限约 **300**（传 300/400/500/800 都只回同一批），
          传超过 300 不会报错但也不会多给。
        """
        data = {"act": "query_order_list", "page": str(page), "page_size": str(page_size),
                "shop_id": shop_id, "start_date": start, "end_date": end, "pb": "all",
                "is_xuni": "0", "hcpt": "", "seller_sku": "", "ck_sku": "", "is_cd": "",
                "order_no": "", "waybill": "", "keywords": "", "search_types": "", "ck": "",
                "chqd": "", "sort_by": "hwc_deliver.id", "sort_order": "DESC"}
        if extra:
            data.update(extra)
        return self.post("deliver.php?act=query_order_list", data, referer=LIST_REF,
                         json_resp=True)

    def order_info(self, order_id: str) -> str:
        """deliver.php?act=order_info&id=<出库单id> → 详情 HTML（含「交货信息」收件地址/邮编）。"""
        return self.get("deliver.php?act=order_info&id=%s" % order_id, referer=LIST_REF)

    def order_detail(self, order_id: str) -> str:
        """列表页 [详情] 打开的弹窗：`act=order_info&type=view&id=<出库单id>`。

        含「序号/Fnsku/产品名称/英文名称/平台SKU/数量/货值/图片」产品表。
        ⚠ **`type=view` 不能省** —— 不传时那块的物料表是空的（2026-10-14 踩过）。
        """
        return self.get("deliver.php?act=order_info&type=view&id=%s" % order_id,
                        referer=LIST_REF)

    # ---------------- 自带导出 Excel ----------------
    def create_export(self, shop_id: str, start: str, end: str, max_per_file: int = 10000) -> dict:
        """建导出任务 → {"daochu_id":N,"msg":"...切分为K个文件..."}。

        ⚠ `max_per_file` 用户可填，但实测 **20000 会让服务端生成阶段 500**，
          10000 稳定（10407 行 → 2 个文件）。
        """
        data = {
            "xls_search_types": "", "xls_keywords": "", "xls_ck": "", "xls_pb": "all",
            "xls_chqd": "", "xls_ck_sku": "", "xls_seller_sku": "", "xls_is_xuni": "0",
            "xls_ids": "", "xls_order_types": "0", "xls_is_cd": "", "xls_hcpt": "",
            "ids": "", "xls_start_date": start, "xls_end_date": end, "xls_shop_id": shop_id,
        }
        url = "deliver.php?act=daochu_excel&is_daochu=1&max_daochu_nums=%d" % max_per_file
        return self.post(url, data, referer=LIST_REF, multipart=True, json_resp=True)

    def export_file_url(self, daochu_id: int, page: int, tries: int = 20,
                        wait: float = 8.0) -> str:
        """取导出任务第 page 个文件的下载地址；文件没生成好时服务端会 500，等一会再来。"""
        last = None
        for _ in range(tries):
            try:
                r = self.post("deliver.php?act=daochu_excel",
                              {"daochu_id": str(daochu_id), "pages": str(page)},
                              referer="products.php?act=daochu_ziliao&daochu_id=%s" % daochu_id,
                              json_resp=True)
            except urllib.error.HTTPError as e:
                last = e
                time.sleep(wait)
                continue
            u = (r.get("html") or "").strip()
            if u:
                return u[2:] if u.startswith("./") else u
            last = "html 为空"
            time.sleep(wait)
        raise PWError("导出文件 %s#%d 一直没就绪：%s" % (daochu_id, page, last))

    def download(self, url: str, dest: str) -> int:
        raw = self.get_raw(url, referer=LIST_REF)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(raw)
        return len(raw)


def encode_body(data, multipart: bool = False):
    """表单编码。multipart 用于 daochu_excel（页面用 FormData 提交）。"""
    if not multipart:
        return (urllib.parse.urlencode(data, doseq=True).encode("utf-8"),
                "application/x-www-form-urlencoded")
    boundary = "----pwform%s" % os.urandom(8).hex()
    parts = []
    for k, v in data.items():
        vals = v if isinstance(v, (list, tuple)) else [v]
        for vv in vals:
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, k, vv)).encode("utf-8"))
    body = b"".join(parts) + ("--%s--\r\n" % boundary).encode("utf-8")
    return body, "multipart/form-data; boundary=%s" % boundary


def parse_file_count(msg: str) -> int:
    """从「当前一共有 N 条数据,系统将会切分为 K 个文件,每个文件 M 个导出资料」里取 K。"""
    import re
    m = re.search(r"切分为\s*(\d+)\s*个文件", msg or "")
    return int(m.group(1)) if m else 1
