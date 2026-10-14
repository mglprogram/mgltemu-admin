# -*- coding: utf-8 -*-
"""test_buylabel_store_lock.py — 买面单「同店窗口单飞锁」的离线回归（不碰浏览器、不花钱）。

来历（2026-09-28 复盘）：当天 47 次跑 / 24 次失败，其中 8 次是**同一店窗口被多路作业抢**
（4 次 `attached=true 活连接 N 个` 被守卫拒绝、4 次「批量页开不出来」），还有 15:03 BEL 那轮把
**别人的 206 行列表**当成本批结果 ⇒ 234 单全被判成「不在待发货」。窗口只有一个，双跑必互相踩。

本文件把锁的三条契约钉死：
  ① 拿到锁＝落记事件，跑完＝清干净（不留垃圾锁把自己下一条挡在门外）；
  ② 同一把键上第二条作业**在碰窗口之前**就被拒（StoreBusy），不同店互不影响；
  ③ 陈旧锁（进程没了 / 记事超时）能自动接管——否则崩一次就永久锁死这条链。
"""
import json
import os
import sys
import time

import pytest

_POWER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/power
sys.path.insert(0, _POWER)

import run_temu_buylabel as R                  # noqa: E402

CFG = {"lock": {"enabled": True, "stale_minutes": 60}}


def _lock_file(tmp_path):
    files = os.listdir(tmp_path)
    assert len(files) == 1, files
    return os.path.join(tmp_path, files[0])


def test_lock_writes_record_then_cleans_up(tmp_path, monkeypatch):
    """① 持锁期间有记事件（pid/键/动作/起跑时间），退出后不残留。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    with R._store_lock("BEL", "pipeline", CFG):
        rec = json.loads(open(_lock_file(tmp_path), encoding="utf-8").read())
        assert rec["pid"] == os.getpid() and rec["key"] == "BEL" and rec["action"] == "pipeline"
        assert rec["started_at"] > 0 and rec["started"]
    assert os.listdir(tmp_path) == [], "跑完必须把锁清掉"


def test_second_job_on_same_store_is_refused(tmp_path, monkeypatch):
    """② 同一家店第二条作业被拒（StoreBusy），错误里要说清是谁在跑、怎么解。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    with R._store_lock("BEL", "pipeline", CFG):
        with pytest.raises(R.StoreBusy) as ei:
            with R._store_lock("BEL", "fill", CFG):
                pass
        msg = str(ei.value)
        assert "已有作业在跑" in msg and "BEL" in msg
    assert os.listdir(tmp_path) == [], "被拒的那条不得把别人的锁删掉"


def test_different_store_is_not_blocked(tmp_path, monkeypatch):
    """不同店（BEL / BHY）是两个窗口 → 互不影响。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    with R._store_lock("BEL", "pipeline", CFG):
        with R._store_lock("BHY", "pipeline", CFG):
            assert len(os.listdir(tmp_path)) == 2
    assert os.listdir(tmp_path) == []


def test_stale_lock_of_dead_process_is_taken_over(tmp_path, monkeypatch):
    """③ 记事里的进程已经没了 → 接管（否则崩一次永久锁死整条链）。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "BEL.lock")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999999, "key": "BEL", "action": "pipeline",
                   "started_at": time.time(), "started": "2026-09-28 00:00:00"}, f)
    with R._store_lock("BEL", "screen", CFG):
        assert os.path.exists(path), "接管后应换成本次记事件"
        rec = json.loads(open(path, encoding="utf-8").read())
        assert rec["pid"] == os.getpid()
    assert not os.path.exists(path)


def test_stale_lock_by_age_is_taken_over(tmp_path, monkeypatch):
    """③b 进程还活着但记事超时（stale_minutes）→ 也接管。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "BEL.lock")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "key": "BEL", "action": "pipeline",
                   "started_at": time.time() - 7200, "started": "old"}, f)
    with R._store_lock("BEL", "screen", CFG):
        pass
    assert not os.path.exists(path)


def test_disabled_writes_no_lock(tmp_path, monkeypatch):
    """口径关掉＝退回「只靠 attached 守卫」：不落文件、不拦人。"""
    monkeypatch.setattr(R, "_LOCK_DIR", str(tmp_path))
    with R._store_lock("BEL", "pipeline", {"lock": {"enabled": False}}):
        assert os.listdir(tmp_path) == []


def test_lock_is_stale_pure_function():
    """纯判据：没有记事/记事不是对象/进程不在/超时 → 陈旧；活着且新 → 不陈旧。"""
    now = time.time()
    assert R._lock_is_stale(None, now, 60) is True
    assert R._lock_is_stale({"pid": os.getpid(), "started_at": now}, now, 60) is False
    assert R._lock_is_stale({"pid": os.getpid(), "started_at": now - 9999}, now, 60) is True
    assert R._lock_is_stale({"pid": 999999999, "started_at": now}, now, 60) is True


# ---------------------------------------------------------------- ④ 没得买时自报 [VERDICT]
def test_noop_verdict_line_shape():
    """「这批没得买」必须自报 `[VERDICT]`（作业层认这一行），否则会被判成「成功但缺号」。"""
    line = R._noop_verdict({"noop": True, "route": "nothing_to_buy", "reason": "本批 28 单没有可买的"})
    assert line.startswith("[VERDICT] ")
    d = json.loads(line[len("[VERDICT] "):])
    assert d["complete"] is True and d["missing"] == []
    assert "nothing_to_buy" in d["judged_by"] and "28" in d["summary"]


def test_noop_verdict_empty_for_normal_runs():
    """正常跑（没停、或只是失败）不许乱发自报判据——否则会顶掉作业层的三号齐判据。"""
    for out in (None, {}, {"steps": []}, {"noop": False}, "text"):
        assert R._noop_verdict(out) == ""
