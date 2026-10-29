# -*- coding: utf-8 -*-
"""scripts/oms/export_abnormal_orders.py 的离线单测（不碰网络、不写用户的目录）。

跑：python -m pytest scripts/oms/tests -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                       # scripts/oms
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))   # scripts/oms/lib

import export_abnormal_orders as ex                                # noqa: E402


# ---------------------------------------------------------------- 店铺过滤
def test_match_store_is_a_case_insensitive_substring():
    """同一品牌在领星里有多种写法，必须都能命中；别按全等判。"""
    stores = ["BHY", "BEL"]
    assert ex.match_store("BEL", stores) == "BEL"
    assert ex.match_store("Temu-BEL", stores) == "BEL"
    assert ex.match_store("temu-bel_us", stores) == "BEL"
    assert ex.match_store("BHY", stores) == "BHY"
    assert ex.match_store("BE", stores) == ""          # BE ≠ BEL（SHEIN 店，不该被 BEL 吃掉）
    assert ex.match_store("Savonire_US", stores) == ""
    assert ex.match_store("", stores) == ""


def test_match_store_all_stores_returns_everything():
    """--all-stores（stores 为空）不筛店铺，返回 ALL 让调用方照写。"""
    assert ex.match_store("TK-TLA_US", []) == "ALL"
    assert ex.match_store("", []) == "ALL"


# ---------------------------------------------------------------- 异常原因
def test_detail_reason_prefers_the_specific_field():
    """详情里异常原因有多个候选字段名，按优先级取第一个非空。"""
    assert ex._detail_reason({"exceptionRemark": "地址异常", "remark": "x"}) == "地址异常"
    assert ex._detail_reason({"remark": "只有备注"}) == "只有备注"
    assert ex._detail_reason({"exceptionRemark": "   "}) == ""      # 空白不算有值
    assert ex._detail_reason({}) == ""


# ---------------------------------------------------------------- 写盘兜底
def test_write_falls_back_to_timestamped_copy_when_locked(monkeypatch, tmp_path):
    """目标被 Excel 占用时**不能**判失败：另存带时间戳的副本，并回 locked。"""
    target = str(tmp_path / "14450-2026-10-29.xlsx")

    def fake_save(path, rows, shops):
        if path == target:
            raise PermissionError(13, "Permission denied", path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("ok")

    monkeypatch.setattr(ex, "write_xlsx", fake_save)
    real, err = ex.write_with_fallback(target, [], {})
    assert err == "locked"
    assert real != target and real.endswith(".xlsx")
    assert os.path.exists(real)


def test_write_reports_a_real_error(monkeypatch, tmp_path):
    """非占用类错误（磁盘满/目录没了）必须如实回报空路径 + 错误，别伪装成功。"""
    def boom(path, rows, shops):
        raise OSError("disk full")

    monkeypatch.setattr(ex, "write_xlsx", boom)
    real, err = ex.write_with_fallback(str(tmp_path / "a.xlsx"), [], {})
    assert real == ""
    assert "disk full" in err


def test_write_returns_target_when_not_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "write_xlsx", lambda p, r, s: None)
    target = str(tmp_path / "x.xlsx")
    assert ex.write_with_fallback(target, [], {}) == (target, "")


# ---------------------------------------------------------------- 退出码
def test_exit_codes_are_documented_and_distinct():
    """0 成功 / 1 部分账号失败 / 2 全失败 / 3 文件被占用另存 —— 四个码必须都写进 docstring。

    口径要和 tasks.json 的 ok_results [0, 3] 对齐，改一个必须改另一个。
    """
    doc = ex.__doc__ or ""
    for code, what in (("0", "全部账号都成功"), ("1", "部分账号失败"),
                       ("2", "全部失败"), ("3", "被 Excel 占用")):
        assert ("    %s " % code) in doc, "docstring 缺退出码 %s" % code
        assert what in doc
