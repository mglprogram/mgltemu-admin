# -*- coding: utf-8 -*-
"""领星 OMS 域：美国日窗口 / 全部店铺采集 / 合并进「订单分布」的单测（不碰网络）。"""
import json
import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OMS = os.path.dirname(HERE)
sys.path.insert(0, OMS)
sys.path.insert(0, os.path.join(OMS, "lib"))
sys.path.insert(0, os.path.dirname(OMS) + os.sep + "pw_orders")

from run_oms_orders import (WANT_STORES, last_done_us_day,      # noqa: E402
                            oms_to_pw_rows, us_day_bounds_cn)


def test_us_day_bounds_cn_converts_pacific_day_to_china_window():
    """「美国 2026-10-14 一整天」= 中国 2026-10-14 15:00 ~ 10-15 14:59:59（PDT=UTC-7）。

    这是整个定时任务的时间口径：接口的 orderCreateTime 是**中国时间**，
    但业务要的是「美国前一天」，所以必须在两边换算。算错一小时就会漏/串单。
    """
    lo, hi = us_day_bounds_cn(date(2026, 10, 14))
    assert lo == "2026-10-14 15:00:00", lo
    assert hi == "2026-10-15 14:59:59", hi


def test_us_day_bounds_cn_handles_dst():
    """夏令时/冬令时切换后偏移不同（PDT=UTC-7 → PST=UTC-8），必须跟着系统时区库走。

    2026-11-02 已过 11-01 的切换 ⇒ 00:00 PST = 16:00 中国时间。
    """
    lo, _ = us_day_bounds_cn(date(2026, 11, 2))
    assert lo == "2026-11-02 16:00:00", lo


def test_last_done_us_day_at_the_aligned_run_time():
    """任务在 15:00 中国时间（= 00:00 PT）跑 ⇒ 刚过完的美国日是「昨天(PT)」。

    ⚠ 这里钉的是一个**改对过的 bug**：直觉写法 `(pt - 1秒).date()` 在 PT 当天 05:30
    （= 中国 20:30）会返回「今天」，把还没过完的一天也抓进来。正确口径是 pt.date()-1。
    """
    # 正点触发：2026-10-16 15:00 中国 = 2026-10-16 00:00 PT → 刚过完的是 10-15
    assert last_done_us_day(datetime(2026, 10, 16, 15, 0)) == date(2026, 10, 15)
    # 同一个 PT 日内的任何时刻，答案都必须一样（跑晚了也不能变成"今天"）
    assert last_done_us_day(datetime(2026, 10, 16, 20, 30)) == date(2026, 10, 15)
    assert last_done_us_day(datetime(2026, 10, 17, 10, 0)) == date(2026, 10, 15)
    # 跨到下一个 PT 日才变成 10-16
    assert last_done_us_day(datetime(2026, 10, 17, 15, 0)) == date(2026, 10, 16)
    # 已知边界：提前跑（PT 23:5x）会退回前天 —— 靠 sync --days 2 兜底
    assert last_done_us_day(datetime(2026, 10, 16, 14, 0)) == date(2026, 10, 14)


def test_collects_all_stores_by_default():
    """用户 2026-10-15：「要在全部的页面下」——采集**不再**按店铺过滤。

    接口的列表没有店铺名，只有详情有；所以 collect(stores=None) 才能拿到全量。
    """
    assert "BHY" in WANT_STORES and "BEL" in WANT_STORES


def test_plan_window_resumes_from_watermark():
    """断档补齐：停跑几天就自动回退到「上次覆盖的次日」。

    这是 2026-10-26 补的坑：10-23~10-25 机器没开、任务三天没跑，
    而窗口只有「当天+昨天」⇒ 那三天的单**永久丢了**（日志里连 oms start 都没有）。
    """
    import shutil
    import tempfile

    from run_oms_orders import MAX_CATCHUP_DAYS, load_state, plan_window, save_state
    today = date(2026, 10, 26)
    d = tempfile.mkdtemp()
    try:
        # ① 没有水位 → 常规窗口（最近 2 天）
        s, e, why = plan_window(d, 2, today)
        assert (s, e) == (date(2026, 10, 25), today), (s, e)
        assert "常规" in why

        # ② 昨天跑过 → 还是常规窗口，不该多抓
        save_state(d, lastCovered="2026-10-25")
        s, e, _ = plan_window(d, 2, today)
        assert (s, e) == (date(2026, 10, 25), today), (s, e)

        # ③ 停在 10-22（缺 23/24/25）→ 从 10-23 接着抓
        save_state(d, lastCovered="2026-10-22")
        s, e, why = plan_window(d, 2, today)
        assert (s, e) == (date(2026, 10, 23), today), (s, e)
        assert "水位补齐" in why and "10-22" in why

        # ④ 手动 --from 覆盖水位
        s, e, why = plan_window(d, 2, today, force_from=date(2026, 10, 16))
        assert (s, e) == (date(2026, 10, 16), today)
        assert "--from" in why

        # ⑤ 停了很久 → 截断到上限，别一次跑几小时
        save_state(d, lastCovered="2026-01-01")
        s, e, why = plan_window(d, 2, today)
        assert s == today - timedelta(days=MAX_CATCHUP_DAYS - 1), s
        assert "上限" in why

        # ⑥ 水位是脏数据也不该崩
        save_state(d, lastCovered="not-a-date")
        s, e, _ = plan_window(d, 2, today)
        assert (s, e) == (date(2026, 10, 25), today)
        assert load_state(d)["lastCovered"] == "not-a-date"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _store_row(**kw):
    r = {"outboundOrderNo": "OBS1", "storeName": "BHY", "provinceName": "Texas",
         "provinceCode": "TX", "cityName": "DALLAS", "postCode": "75217",
         "orderCreateTime": "2026-10-15 12:00:00", "platformOrderNo": "PO-211-1",
         "salesPlatform": "34", "whCode": "USW01", "logisticsCarrier": "ups",
         "logisticsTrackNos": ["T1"], "costTotal": 0.75,
         "products": [{"sku": "D1", "name": "BS2-202504-5SetA-L", "qty": 2}],
         "packs": [{"weight": 0.7, "labelUrl": "https://x/l.pdf"}]}
    r.update(kw)
    return r


def test_oms_to_pw_rows_maps_into_the_order_geo_shape(tmp_path=None):
    """OMS 的 detail → 「订单分布」管线的行形状；店铺过滤生效。"""
    store = {"A": _store_row(),
             "B": _store_row(outboundOrderNo="OBS2", storeName="TK-LA_US")}
    rows = oms_to_pw_rows(store, stores=("BHY", "BEL"))
    assert len(rows) == 1
    r = rows[0]
    assert r["shop"] == "BHY" and r["ref_no"] == "PO-211-1"
    assert r["state"] == "Texas" and r["state_code"] == "TX" and r["zip"] == "75217"
    assert r["qty"] == 2 and r["tracking"] == "T1" and r["source"] == "oms"
    assert r["items"][0]["product"] == "BS2-202504-5SetA-L"
    assert oms_to_pw_rows(store, stores=None) and len(oms_to_pw_rows(store, stores=None)) == 2


def test_load_oms_rows_dedupes_against_power_by_ref_no():
    """去重键 = 参考单号：帕沃尔抓过的 PO-211-… 不再重复计入。

    这是合并安全性的核心 —— 两家仓都可能持有同一个 TEMU 单号，
    重复计入会让州分布和产品榜虚高。
    """
    import importlib.util
    p = os.path.join(os.path.dirname(OMS), "pw_orders", "run_pw_order_geo.py")
    spec = importlib.util.spec_from_file_location("rpog", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    d = os.path.join(OMS, "tests", "_tmp_oms")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, "oms_orders.jsonl")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(json.dumps(_store_row(platformOrderNo="PO-1"), ensure_ascii=False) + "\n")
        f.write(json.dumps(_store_row(outboundOrderNo="OBS2", platformOrderNo="PO-2"),
                           ensure_ascii=False) + "\n")
        f.write(json.dumps(_store_row(outboundOrderNo="OBS3", storeName="TK-LA_US",
                                      platformOrderNo="PO-3"), ensure_ascii=False) + "\n")
    try:
        rows = mod.load_oms_rows(d, {"PO-1"}, stores=("BHY", "BEL"))
        assert [r["ref_no"] for r in rows] == ["PO-2"], rows
        # 不限店铺时，TikTok 那单也进来
        assert len(mod.load_oms_rows(d, {"PO-1"}, stores=None)) == 2
        assert mod.count_oms_other_stores(d, ("BHY", "BEL"), {"PO-1"}) == 1
    finally:
        os.remove(fn)
        os.rmdir(d)
