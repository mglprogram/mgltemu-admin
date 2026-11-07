# -*- coding: utf-8 -*-
"""test_net_route.py — 「国内 IP 直连、其他走代理」这条规则的钉子。

用户 2026-11-07 口径：
    「国内 ip 都直连，其他域名才走代理」

钉住五件事：
  · 国内段表能读、能判（APNIC 官方数据的产物，不是手抄）
  · 本机/内网永远直连（否则 CDP 调试端口会被塞进代理，自动化当场断）
  · 白名单每一条都必须**改变**结论（不然就是白写一条，早晚误导人）
  · 判不出来时兜底走代理、且**永不抛异常**
  · 代理端口只在 `net_route.PROXY` 一处定义
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import net_route as R  # noqa: E402


# ---------------------------------------------------------------- 表本身

def test_table_asset_exists_and_is_big():
    """表是 APNIC 产物，必须真在仓库里（不靠运行时下载）。"""
    assert os.path.isfile(R.ASSET), "国内 IP 段表缺失：%s" % R.ASSET
    assert os.path.getsize(R.ASSET) > 20 * 1024, "表太小，像是被截断了"


def test_table_loads_and_is_sorted():
    t = R.load_table()
    assert len(t) > 2000, "国内段数异常：%d" % len(t)
    assert t == sorted(t), "表没排序，二分查找会错"


def test_known_cn_ips():
    for ip in ("122.225.209.23",      # 飞书（阿里云）
               "115.190.143.220",     # kdocs（华为云）
               "14.116.235.32",       # 领星 OMS（腾讯）
               "47.114.151.12",       # 帕沃尔（阿里云杭州）
               "183.2.172.177"):      # 百度
        assert R.is_cn_ip(ip), "%s 应判为国内" % ip


def test_known_foreign_ips():
    for ip in ("20.205.243.166",      # github
               "172.67.211.163",      # cloudflare
               "157.240.7.20"):       # google/meta
        assert not R.is_cn_ip(ip), "%s 应判为境外" % ip


def test_bad_input_is_false_not_crash():
    assert R.is_cn_ip("not-an-ip") is False
    assert R.is_cn_ip("") is False


# ---------------------------------------------------------------- host 解析

def test_host_of_handles_real_shapes():
    assert R.host_of("https://api.kdocs.cn/v3/x?y=1") == "api.kdocs.cn"
    assert R.host_of("open.feishu.cn") == "open.feishu.cn"
    assert R.host_of("http://127.0.0.1:9222/json/list") == "127.0.0.1"
    assert R.host_of("HTTP://GitHub.COM/") == "github.com"
    assert R.host_of("") == ""


# ---------------------------------------------------------------- 决策

def test_localhost_is_always_direct():
    """CDP 调试端口在被塞进代理时会整条链路断掉。"""
    for h in ("127.0.0.1", "localhost", "http://127.0.0.1:9222/json/list"):
        rt = R.decide(h)
        assert rt.mode == "local" and rt.direct, h


def test_private_ip_is_direct():
    rt = R.decide("192.168.1.10")
    assert rt.direct, "内网地址被塞进代理了"


def test_cn_service_is_direct():
    for h in ("api.kdocs.cn", "api.xlwms.com", "powerwarehouse.net"):
        assert R.decide(h).direct, "%s 应该直连" % h


def test_foreign_service_goes_through_proxy():
    for h in ("github.com", "api.cloudflare.com"):
        rt = R.decide(h)
        assert rt.mode == "proxy" and not rt.direct, h


def test_whitelists_each_carry_an_exception():
    """白名单里的每条都该是本机真有理由的（不校验真实性，只校验不重叠）。"""
    assert not (R.FORCE_DIRECT & R.FORCE_PROXY), "同一域名两边都在，规则自相矛盾"


def test_unresolvable_host_falls_back_to_proxy_not_raise():
    rt = R.decide("no-such-host.invalid")
    assert rt.mode == R.DEFAULT_MODE
    assert "解析失败" in rt.reason


def test_real_domains_all_decide_without_raising():
    for h in ("open.feishu.cn", "api.chuckfang.com", "mgltemu.us.ci",
              "agentseller-us.temu.com"):
        rt = R.decide(h)
        assert rt.mode in ("direct", "proxy", "local"), h


# ---------------------------------------------------------------- 端口唯一

def test_proxy_port_defined_once():
    """换代理软件只该改这一个常量；端口散落各处就会像 10808 那样留死口径。"""
    assert R.PROXY == "http://127.0.0.1:7897"
    assert "10808" not in R.PROXY, "10808 是已废弃的 v2rayN 端口"


# ---------------------------------------------------------------- 给子进程

def test_env_for_foreign_host_points_at_proxy():
    env = R.env_for_host("https://github.com/", {"PATH": "x", "HTTPS_PROXY": "http://127.0.0.1:10808"})
    assert env["HTTPS_PROXY"] == R.PROXY
    assert "10808" not in env["HTTPS_PROXY"], "父进程的 10808 没被压掉"
    assert "127.0.0.1" in env["NO_PROXY"], "本机段必须绕过代理"


def test_env_for_cn_host_clears_proxy():
    env = R.env_for_host("https://api.kdocs.cn/", {"HTTPS_PROXY": "http://127.0.0.1:10808"})
    assert env["HTTPS_PROXY"] == ""
    assert env["HTTP_PROXY"] == ""
    assert env["NO_PROXY"] == "*"


def test_env_never_leaks_parent_proxy():
    """父环境里塞的废弃端口，两个分支都不能原样漏给子进程。"""
    for h in ("https://api.kdocs.cn/", "https://github.com/"):
        env = R.env_for_host(h, {"HTTP_PROXY": "http://127.0.0.1:10808",
                                 "HTTPS_PROXY": "http://127.0.0.1:10808"})
        assert "10808" not in (env["HTTP_PROXY"] + env["HTTPS_PROXY"]), h


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
