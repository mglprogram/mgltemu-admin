# -*- coding: utf-8 -*-
"""领星 WMS/OMS（xlwms）OpenAPI **v1** 客户端。

为什么是 v1 而不是文档站首页的 v2（2026-10-15 实测踩坑）
--------------------------------------------------------
同一个 appKey 打 v2 的路径（`/openapi/v2/...`）一律返回 `11008`。
我一开始把它当成「无接口权限」，其实 **11008 是「路径不存在」**
（恒信域 2026-09-10 的笔记里就记着这条：`11008 = 路径不存在（可用它枚举路径）`）。
换到 **v1** 路径立刻通：
  · `/openapi/v1/warehouse/options`        → 仓库列表
  · `/openapi/v1/outboundOrder/pageList`   → 小包出库单分页（= OMS「平台订单」列表）
  · `/openapi/v1/outboundOrder/detail`     → 订单详情（州/市/邮编 + 产品 + 跟踪号 + 面单）

签名（v1 与 v2 **完全不同**，别混）
----------------------------------
    authcode = HMAC-SHA256(key=appSecret,
                           msg=appKey + compact_json(data, sort_keys) + reqTime)
    放在 **URL query**：POST {API}{path}?authcode=xxx
    请求体：{"appKey":..., "reqTime":..., "data":{...}}
    成功码是 **int 200**（文档写 integer；实测返回 int）。
（v2 是另一套：data/appKey/timestamp/sign 放 body 里、HMAC 的 msg 又是另一串。）

实测硬限制（2026-10-15）
------------------------
  · pageSize **最大 50**（传 100/200/500 → records 空、total 变 null）
  · detail 的 `outboundOrderNoList` **一次最多 50 个**（传 100 → code 10002）
  · pageList 只认 `timeType=orderCreateTime` + `startTime/endTime`；
    `timeType=updateTime` 是**无效的**（传了 total 恒为全量 4108，不会过滤）——
    所以增量同步按**创建时间**切窗口，不要指望"最后变更时间"增量。
  · 列表接口的 `storeName` **恒为空**，只有 **详情**里才有店铺名 —— 想按 BHY/BEL 过滤
    必须下钻详情。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:  # 被重定向/管道捕获时统一 UTF-8
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

API = "https://api.xlwms.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")

# 分页/批量硬限制（实测，别往上调）
PAGE_SIZE_MAX = 50
DETAIL_BATCH_MAX = 50

# ---------------------------------------------------------------------------
# **直连优先，代理只兜底**（2026-10-29 实测）
#
# 本机用户环境有 `HTTP(S)_PROXY=http://127.0.0.1:10808`，而 `urllib` **默认会用它** ——
# 于是每个请求都绕一圈 v2ray 再打回境内服务：
#
#     page 1 走环境代理   2.4 s / 1.9 s / 2.7 s
#     page 1 直连（NoProxy）0.29 s / 0.30 s / 0.21 s      → **快 10 倍**
#
# 7 天窗口有 300+ 页，串行走代理就是 13 分钟（2026-10-29 实测跑了 11 分钟还没完，
# 只能杀掉重做）。直连后同样的量 80 秒级。
# 直连失败（DNS / 连接被拒）才回落到环境代理 —— xlwms 是境内域名，正常情况下永远走不到那条路。
# ---------------------------------------------------------------------------
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ENV_PROXY = urllib.request.build_opener()          # 尊重 HTTP(S)_PROXY 环境变量


def _open(req, timeout):
    """先直连；只有**连接层**失败才用环境代理重试一次。"""
    try:
        return _DIRECT.open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise                                       # 4xx/5xx 是业务/签名问题，别换条路再打一遍
    except Exception:                               # noqa: BLE001
        return _ENV_PROXY.open(req, timeout=timeout)

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_FILE = os.path.join(os.path.dirname(HERE), "oms_account.local.json")

LIST_PATH = "/openapi/v1/outboundOrder/pageList"
DETAIL_PATH = "/openapi/v1/outboundOrder/detail"
WAREHOUSE_PATH = "/openapi/v1/warehouse/options"


class OmsError(RuntimeError):
    """接口返回码非 200，或本地配置缺失。"""


def resolve_account() -> tuple[str, str]:
    """取 appKey/appSecret：环境变量 → <域>/oms_account.local.json（不入库）。"""
    k, s = os.environ.get("OMS_APP_KEY"), os.environ.get("OMS_APP_SECRET")
    if k and s:
        return k, s
    if os.path.exists(ACCOUNT_FILE):
        with open(ACCOUNT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("appKey") and d.get("appSecret"):
            return d["appKey"], d["appSecret"]
    raise OmsError(
        "没找到领星 OMS 的 appKey/appSecret。二选一：\n"
        "  1) 设环境变量 OMS_APP_KEY / OMS_APP_SECRET\n"
        '  2) 写本地文件 %s：{"appKey":"...","appSecret":"..."}（已在 .gitignore）'
        % ACCOUNT_FILE)


def authcode(app_key: str, app_secret: str, data, req_time) -> str:
    """v1 签名。data 用**紧凑 JSON + 键排序**，必须与服务端算法逐字节一致。"""
    ds = json.dumps(data, sort_keys=True, separators=(",", ":")) if data is not None else ""
    msg = (app_key + ds + str(req_time)).encode("utf-8")
    return hmac.new(app_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class OmsClient:
    def __init__(self, app_key: str | None = None, app_secret: str | None = None,
                 timeout: int = 60, verbose: bool = True):
        self.app_key, self.app_secret = resolve_account() if not (app_key and app_secret) \
            else (app_key, app_secret)
        self.timeout = timeout
        self.verbose = verbose
        self.calls = 0

    # ---------------- 底层 ----------------
    def post(self, path: str, data: dict | None = None, retries: int = 3) -> dict:
        data = data or {}
        last = None
        for i in range(retries):
            rt = str(int(time.time()))
            url = "%s%s?authcode=%s" % (API, path, authcode(self.app_key, self.app_secret, data, rt))
            body = json.dumps({"appKey": self.app_key, "reqTime": rt, "data": data}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": UA, "Accept": "application/json",
            })
            try:
                self.calls += 1
                with _open(req, self.timeout) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
            except Exception as e:                                # noqa: BLE001
                last = e
                if i < retries - 1:
                    if self.verbose:
                        print("   [oms] 第 %d 次失败(%s)，重试" % (i + 1, str(e)[:60]), flush=True)
                    time.sleep(1.5 * (i + 1))
                    continue
                raise OmsError("请求 %s 失败：%s" % (path, e))
            code = str(j.get("code"))
            if code == "200":
                return j
            # 业务错误**不重试**（重试也没用，还可能重复下单类副作用）
            raise OmsError("%s -> code=%s msg=%s" % (path, code, j.get("msg")))
        raise OmsError("请求 %s 失败：%s" % (path, last))

    # ---------------- 业务 ----------------
    def warehouses(self) -> list[dict]:
        return self.post(WAREHOUSE_PATH, {}).get("data") or []

    def list_page(self, page: int = 1, page_size: int = PAGE_SIZE_MAX,
                  time_type: str | None = None, start: str | None = None,
                  end: str | None = None) -> dict:
        """小包出库单分页。返回 {total, records, page, pageSize}。"""
        if page_size > PAGE_SIZE_MAX:
            page_size = PAGE_SIZE_MAX
        d = {"page": int(page), "pageSize": int(page_size)}
        if time_type and start and end:
            d.update({"timeType": time_type, "startTime": start, "endTime": end})
        return self.post(LIST_PATH, d).get("data") or {}

    def iter_orders(self, start: str, end: str, time_type: str = "orderCreateTime",
                    max_pages: int = 4000):
        """按 [start, end] 逐页拉出全部订单（列表级字段）。"""
        page, total, got = 1, None, 0
        while page <= max_pages:
            d = self.list_page(page, PAGE_SIZE_MAX, time_type, start, end)
            recs = d.get("records") or []
            if total is None:
                total = int(d.get("total") or 0)
            for r in recs:
                yield r
            got += len(recs)
            if not recs or got >= total:
                break
            page += 1

    def all_pages(self, start: str, end: str, time_type: str = "orderCreateTime",
                  page_size: int = PAGE_SIZE_MAX, workers: int = 6,
                  progress=None) -> list[dict]:
        """把 [start, end] 的记录**一次拉完**（分页并行，页序保持）。

        为什么不直接用 `iter_orders`：7 天窗口 300+ 页，即使直连 0.25 s/页也要 80 秒；
        并行 6 路后 15 秒级。失败页最后**串行重取一次**，仍失败就抛 `OmsError` ——
        绝不静默丢页（丢一页 = 漏掉整页订单，而调用方看不出来）。
        """
        first = self.list_page(1, page_size, time_type, start, end)
        recs = list(first.get("records") or [])
        total = int(first.get("total") or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        if pages <= 1 or not recs:
            return recs

        out = {1: recs}
        errs: dict[int, Exception] = {}
        lock = threading.Lock()
        done = [1]

        def grab(p: int) -> None:
            d = self.list_page(p, page_size, time_type, start, end)
            with lock:
                out[p] = list(d.get("records") or [])
                done.append(p)
                if progress and len(done) % 25 == 0:
                    progress(len(done), pages)

        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            futs = {ex.submit(grab, p): p for p in range(2, pages + 1)}
            for f, p in futs.items():
                try:
                    f.result()
                except Exception as e:                # noqa: BLE001
                    errs[p] = e

        for p in sorted(errs):                        # 失败页串行重取一次
            d = self.list_page(p, page_size, time_type, start, end)
            out[p] = list(d.get("records") or [])
        if progress:
            progress(len(out), pages)

        merged: list[dict] = []
        for p in range(1, pages + 1):
            merged += out.get(p) or []
        return merged

    def details(self, order_nos) -> list[dict]:
        """批量取详情（一次最多 50 个单号，自动分批）。"""
        order_nos = [n for n in order_nos if n]
        out = []
        for i in range(0, len(order_nos), DETAIL_BATCH_MAX):
            chunk = order_nos[i:i + DETAIL_BATCH_MAX]
            out += self.post(DETAIL_PATH, {"outboundOrderNoList": chunk}).get("data") or []
        return out
