"""钉住「SHEIN QPS 限流不能把任务跑挂」（2026-11-28 用户报障）。

报障原文（飞书告警截图）：
    **SHEIN 在售明细日更** 运行失败
    结果码: 1（脚本自报失败 exit 1）
    原因: RuntimeError: HTTP 429 @ /open-api/goods/query-sku-sales:
          {"code":"832213","msg":"被限流了 - 触发QPS限流 … 速率(80/1000)*100：8个/秒"}

两层修复，各钉一组断言：
  ① `shein_client.signed_post` —— 429 / code=832213 要**退避重试**，不能直接抛；
  ② `fetch_shein_sales` —— 所有出站请求过**全局节流闸门**（并发 5 也会超 8/秒）。
"""

import ast
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT = os.path.join(REPO, "scripts", "goods", "shein_client.py")
SALES = os.path.join(REPO, "scripts", "goods", "fetch_shein_sales.py")

#: 报障里出现过的真实错误码
RATE_CODE = "832213"


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _find_client():
    """shein_client 在 skill 目录里，不在仓库内 —— 两种位置都认。"""
    cands = [
        os.path.join(REPO, "scripts", "goods", "shein_client.py"),
        r"C:\Users\saing\.agents\skills\shein-buylabel\scripts\shein_client.py",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return ""


class TestClientRateLimit:
    @pytest.fixture(scope="class")
    def src(self):
        p = _find_client()
        if not p:
            pytest.skip("找不到 shein_client.py")
        return _src(p)

    def test_rate_code_is_recognised(self, src):
        assert RATE_CODE in src, "没有识别 832213 这个 QPS 限流码"
        assert "_RATE_LIMIT_CODES" in src

    def test_has_bounded_retry(self, src):
        assert "_RATE_RETRY" in src, "缺限流重试上限（不能无限重试）"
        assert "_RATE_BACKOFF" in src, "缺退避表"
        # 必须有次数上限：拿数字比较，别只吃肉眼看
        m = re.search(r"_RATE_RETRY\s*=\s*(\d+)", src)
        assert m, "缺 _RATE_RETRY 常量"
        assert 1 <= int(m.group(1)) <= 10, "重试上限应在 1..10 之间"

    def test_retries_before_raising(self, src):
        """限流分支里必须有 time.sleep + continue，而不是直接 raise。"""
        body = src.split("def signed_post")[1]
        seg = body[: body.find("def ", 10) if body.find("def ", 10) > 0 else len(body)]
        # 找到限流处理段
        idx = seg.find("_RATE_LIMIT_CODES")
        assert idx > 0, "signed_post 里没有用 _RATE_LIMIT_CODES"
        chunk = seg[idx - 200: idx + 2000]
        assert "time.sleep" in chunk, "限流后退避没 sleep"
        assert "continue" in chunk, "限流后没有 continue 重试"

    def test_429_handled_before_whitelist(self, src):
        """429 必须**先于** IP 白名单分支判断，否则会走错分支当作加白问题。"""
        body = src.split("except urllib.error.HTTPError")[1]
        i429 = body.find("429")
        i_ip = body.find("is not in the whitelist")
        assert i429 > 0, "HTTPError 分支里没处理 429"
        assert i_ip > 0, "HTTPError 分支里没有 IP 白名单处理（被误删？）"
        assert i429 < i_ip, "429 判断必须排在 IP 白名单判断之前"

    def test_success_body_also_checked(self, src):
        """SHEIN 也可能 200 + body.code=832213，两条路都要拦。"""
        body = src.split("with _OPENER.open")[1]
        head = body[:1500]
        assert "_RATE_LIMIT_CODES" in head or "QPS" in head, (
            "正常返回分支没有检查 QPS 限流（200 也可能是限流）")


class TestThrottle:
    @pytest.fixture(scope="class")
    def src(self):
        return _src(SALES)

    def test_global_throttle_exists(self, src):
        assert "MIN_INTERVAL" in src
        assert "_throttle" in src
        assert "threading" in src, "跨线程节流必须用锁"

    def test_all_requests_go_through_gate(self, src):
        """除 `_spost` 自身外，不许有裸 signed_post 调用。"""
        calls = re.findall(r"sc\.signed_post\(", src)
        assert len(calls) == 1, (
            "应只有 _spost 内部那 1 处裸调用，实际 %d 处 —— 有请求绕过了节流" % len(calls))
        assert src.count("_spost(") >= 5, "业务调用点没有改走 _spost"

    def test_interval_is_under_qps_limit(self, src):
        """8 次/秒 ⇒ 间隔须 >= 0.125s。要求留余量，实际默认应更大。"""
        m = re.search(r"SHEIN_MIN_INTERVAL[\"']?\s*[^)]*?\)\s*or\s*([0-9.]+)", src)
        assert m, "读不到 MIN_INTERVAL 默认值"
        val = float(m.group(1))
        assert val >= 0.125, "默认间隔 %.3fs 会超过 8 次/秒的限制" % val
        assert val <= 1.0, "默认间隔 %.3fs 太慢（整店要跑很久）" % val

    def test_throttle_uses_monotonic(self, src):
        """节流必须用单调时钟，否则系统时间回调会导致长时间误等。"""
        body = src.split("def _throttle")[1][:800]
        assert "monotonic" in body, "_throttle 用的是 time.time()，系统校时会出问题"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
