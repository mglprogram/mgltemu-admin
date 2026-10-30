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


# ---------------------------------------------------------------- 0 行不导表
def _run_main(monkeypatch, tmp_path, per_account, extra=()):
    """跑一遍 main()，但把网络换成桩：per_account = {tag: rows}。返回 rc。"""
    monkeypatch.setattr(ex, "load_accounts",
                        lambda spec, f: [("LM739", "k1", "s1"), ("14450", "k2", "s2")])

    def fake_scan(tag, key, secret, start, end, stores, page_size=50, workers=6):
        rows = per_account.get(tag) or []
        return {"rows": rows, "shops": {"BEL": len(rows)}, "scanned": 10,
                "abnormal": len(rows), "unknown_store": 0}

    monkeypatch.setattr(ex, "scan_account", fake_scan)
    monkeypatch.setattr(sys, "argv",
                        ["export_abnormal_orders.py", "--out", str(tmp_path),
                         "--date", "2026-10-30"] + list(extra))
    return ex.main()


def test_no_tables_written_when_both_stores_are_clean(monkeypatch, tmp_path):
    """用户 2026-10-30：「两个店铺都没有异常订单就不导表到指定文件夹」。

    不只是「不写空表」——**连日期目录都不该出现**。空目录也会让人以为今天导过。
    """
    rc = _run_main(monkeypatch, tmp_path, {"LM739": [], "14450": []})
    assert rc == 0                                  # 查到了 = 成功，不是失败
    assert list(tmp_path.rglob("*")) == []          # 一个文件一个目录都没有


def test_only_the_account_with_rows_gets_a_file(monkeypatch, tmp_path):
    """一边有、一边没有 → 只写有货的那张；0 行的那边不留空表。"""
    rows = [["14450", "BEL", "OBS1", "PO-1", "R1", "异常", "2026-10-30 10:00:00",
             "LA", "ch", "carrier", "T1", "", "2026-10-28 09:00:00", "地址异常"]]
    rc = _run_main(monkeypatch, tmp_path, {"LM739": [], "14450": rows})
    assert rc == 0
    files = sorted(p.name for p in tmp_path.rglob("*.xlsx"))
    assert files == ["14450-2026-10-30.xlsx"]
    assert (tmp_path / "2026-10-30").is_dir()


def test_write_empty_flag_restores_the_old_behaviour(monkeypatch, tmp_path):
    """排查用：--write-empty 才写只有表头的空表。"""
    rc = _run_main(monkeypatch, tmp_path, {"LM739": [], "14450": []},
                   extra=["--write-empty"])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.rglob("*.xlsx"))
    assert files == ["14450-2026-10-30.xlsx", "LM739-2026-10-30.xlsx"]


# ---------------------------------------------------------------- 推送
def _row(acct="14450", store="BEL", ordno="OBS1", po="PO-1", track="T1"):
    return [acct, store, ordno, po, "R1", "异常", "2026-10-30 10:00:00",
            "LA", "ch", "carrier", track, "", "2026-10-28 09:00:00", "地址异常"]


def test_build_alert_is_silent_when_clean():
    """用户口径：「有异常就推」——两边都没异常、也没失败，就不该有通知。"""
    summary = [{"account": "LM739", "ok": True, "empty": True, "abnormal": 0, "rows": 0},
               {"account": "14450", "ok": True, "empty": True, "abnormal": 0, "rows": 0}]
    assert ex.build_alert(summary, r"D:\x\2026-10-30", "2026-10-30", []) is None


def test_build_alert_carries_the_order_details():
    """有异常时必须把「哪个单」带出来 —— 只说「有 3 条」等于让人再开表。"""
    summary = [{"account": "14450", "ok": True, "rows_detail": [
        _row(ordno="OBS1", po="PO-1", track="T1"),
        _row(ordno="OBS2", po="PO-2", track="T2"),
        _row(acct="LM739", store="BHY", ordno="OBS3", po="PO-3", track="T3"),
    ]}]
    title, body = ex.build_alert(summary, r"D:\x\2026-10-30", "2026-10-30", [])
    assert "TEMU" in title                    # 飞书群机器人关键词校验要它
    assert "3 条" in title
    for token in ("OBS1", "PO-1", "T1", "OBS3", "BHY", "BEL"):
        assert token in body
    assert "D:\\x\\2026-10-30" in body        # 表格位置


def test_build_alert_truncates_long_lists():
    """几十条异常不能把手机通知刷爆：只列前 N 条 + 一句「还有多少条」。"""
    rows = [_row(ordno="OBS%03d" % i, po="PO-%d" % i) for i in range(40)]
    title, body = ex.build_alert([{"account": "14450", "ok": True, "rows_detail": rows}],
                                 r"D:\x", "2026-10-30", [])
    assert "40 条" in title
    assert "OBS000" in body
    assert "OBS039" not in body               # 超出的不列
    assert "还有 %d 条" % (40 - ex.ALERT_MAX_ROWS) in body


def test_build_alert_reports_failures_even_with_no_rows():
    """查询失败必须推 —— 「今天没查成」和「今天没异常」绝不能都变成沉默。"""
    title, body = ex.build_alert([], r"D:\x", "2026-10-30", ["14450"])
    assert "失败" in title
    assert "14450" in body


def test_push_failure_never_breaks_the_export(monkeypatch, tmp_path):
    """推送坏掉只能少一条消息，不能让「表已导出」变成失败。"""
    import notify_push

    def boom(*a, **kw):
        raise RuntimeError("webhook 挂了")

    monkeypatch.setattr(notify_push, "push", boom)
    rows = [_row()]
    rc = _run_main(monkeypatch, tmp_path, {"LM739": [], "14450": rows})
    assert rc == 0                                   # 仍然是成功
    assert (tmp_path / "2026-10-30" / "14450-2026-10-30.xlsx").exists()


# ---------------------------------------------------------------- 输出目录
def test_dest_dir_defaults_to_a_date_subfolder():
    """用户 2026-10-29：「文件都放在 D:\\together\\Last_mile\\失败订单\\日期文件夹」。

    每天一份，别都堆在根目录；--flat 才退回根目录。
    """
    root = r"D:\together\Last_mile\失败订单"
    assert ex.dest_dir_for(root, "2026-10-29") == os.path.join(root, "2026-10-29")
    assert ex.dest_dir_for(root, "2026-10-29", flat=True) == root


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
