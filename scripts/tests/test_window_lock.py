# -*- coding: utf-8 -*-
"""test_window_lock.py — 紫鸟窗口「跨进程单飞锁」的离线回归（不碰浏览器、不花钱）。

来历（2026-10-14）：面板时间轴盘出 6 个任务会用紫鸟店铺窗口，目前实测时刻错开、真冲突 0，
但那是排班凑巧、不是机制保证。本文件把锁的四条契约钉死（与 power 域买面单锁同口径）：
  ① 拿到锁＝落记事件，跑完＝清干净（不留垃圾锁把自己下一条挡在门外）；
  ② 同一把键上第二条作业**在碰窗口之前**就被拒（WindowBusy），不同键互不影响；
  ③ 陈旧锁（进程没了 / 记事超时）能自动接管——否则崩一次就永久锁死这条链；
  ④ 关掉开关＝不落文件、不拦人。
外加一条本模块特有的安全性质：
  ⑤ **不偷别人的锁**：自己的锁若已被判陈旧并被接管，退出时不许把接管者的文件删掉。
"""
import json
import os
import sys
import time

import pytest

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/
sys.path.insert(0, _SCRIPTS)

import window_lock as W                          # noqa: E402


def _lock_file(tmp_path):
    files = os.listdir(tmp_path)
    assert len(files) == 1, files
    return os.path.join(tmp_path, files[0])


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每个用例把锁目录指到临时目录，绝不碰真实的 _logs/window-locks。"""
    monkeypatch.setattr(W, "_LOCK_DIR", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ ① 落记事件 + 跑完清干净
def test_lock_writes_record_then_cleans_up(tmp_path):
    with W.window_lock("BHY+BEL", "flux", log=None):
        files = sorted(os.listdir(tmp_path))
        assert files == ["BEL.lock", "BHY.lock"], files
        for n in files:
            rec = json.loads(open(os.path.join(str(tmp_path), n), encoding="utf-8").read())
            assert rec["pid"] == os.getpid()
            assert rec["key"] == "BHY+BEL"          # 记事里留着**完整**窗口键，便于排查
            assert rec["action"] == "flux"
            assert rec["started_at"] > 0 and rec["started"]
    assert os.listdir(tmp_path) == [], "跑完必须把锁清掉"


# ------------------------------------------------------------------ ② 同键被拒 / 异键放行
def test_second_job_on_same_window_is_refused(tmp_path):
    with W.window_lock("BHY+BEL", "flux"):
        with pytest.raises(W.WindowBusy) as ei:
            with W.window_lock("BHY+BEL", "presale"):
                pass
        msg = str(ei.value)
        assert "已有作业在跑" in msg and "BHY+BEL" in msg
        assert "window_lock.py --release" in msg, "报错要给出解锁办法"
    assert os.listdir(tmp_path) == [], "被拒的那条不得把别人的锁删掉"


def test_bel_only_does_not_block_bhy_only(tmp_path):
    """BEL 窗口 vs BHY 窗口是两个不同的窗口 → 互不干扰（键不同）。"""
    with W.window_lock("BEL", "bel_abnormal"):
        with W.window_lock("BHY", "goods_onsale"):
            assert len(os.listdir(tmp_path)) == 2
    assert os.listdir(tmp_path) == []


def test_shared_window_key_collides_with_single_window_lock(tmp_path):
    """BHY+BEL 与 BEL 都碰 **BEL 这扇窗口** → 必须相撞，不能各锁各的。

    这正是当前注册表的真实形态：5 个任务写 `BHY+BEL`、1 个写 `BEL`（BEL异常出库）。
    若按字符串直比，两者永不冲突，锁就形同虚设。
    """
    with W.window_lock("BHY+BEL", "flux"):
        with pytest.raises(W.WindowBusy):
            with W.window_lock("BEL", "bel_abnormal"):
                pass
    assert os.listdir(tmp_path) == [], "被拒后（含自己已占的 BHY）必须清干净"


def test_partial_acquire_rolls_back(tmp_path):
    """拿不全就全放掉：`BHY+BEL` 先占住 BHY，再发现 BEL 被占 → 不许留下"半占"的 BHY 锁。

    否则下一次只想用 BHY 的任务会被这把半吊子锁挡在门外，而占锁的人其实已经失败退出了。
    """
    with W.window_lock("BEL", "bel_abnormal"):       # 先把 BEL 占掉（排序后 BEL 在前）
        with pytest.raises(W.WindowBusy):
            with W.window_lock("BHY+BEL", "flux"):
                pass
        # 只剩占用者自己的 BEL.lock；失败那条的 BHY 不能残留
        assert os.listdir(tmp_path) == ["BEL.lock"], os.listdir(tmp_path)
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ ③ 陈旧锁接管
def test_stale_lock_of_dead_process_is_taken_over(tmp_path):
    path = os.path.join(str(tmp_path), "BEL.lock")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999999, "key": "BHY+BEL", "action": "flux",
                   "started_at": time.time(), "started": "2026-10-14 00:00:00"}, f)
    with W.window_lock("BHY+BEL", "presale"):
        rec = json.loads(open(path, encoding="utf-8").read())
        assert rec["pid"] == os.getpid(), "接管后应换成本次记事"
    assert not os.path.exists(path)


def test_stale_lock_by_age_is_taken_over(tmp_path):
    path = os.path.join(str(tmp_path), "BEL.lock")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "key": "BHY+BEL", "action": "flux",
                   "started_at": time.time() - 7200, "started": "old"}, f)
    with W.window_lock("BHY+BEL", "presale"):
        pass
    assert not os.path.exists(path)


def test_lock_is_stale_pure_function():
    now = time.time()
    assert W._lock_is_stale(None, now, 60) is True
    assert W._lock_is_stale({"pid": os.getpid(), "started_at": now}, now, 60) is False
    assert W._lock_is_stale({"pid": os.getpid(), "started_at": now - 9999}, now, 60) is True
    assert W._lock_is_stale({"pid": 999999999, "started_at": now}, now, 60) is True


# ------------------------------------------------------------------ ④ 开关
def test_disabled_writes_no_lock(tmp_path):
    with W.window_lock("BHY+BEL", "flux", enabled=False):
        assert os.listdir(tmp_path) == []


def test_empty_key_is_noop(tmp_path):
    with W.window_lock("", "flux"):
        assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ ⑤ 不偷别人的锁（本模块特有）
def test_does_not_steal_lock_taken_over_by_another(tmp_path):
    """持锁期间被别的进程判陈旧并接管 → 退出时**不许**删掉接管者的锁。

    场景：我的作业卡了很久（超 stale），下一个班次接管并继续跑；我这时才退出。
    若退出时无条件 os.remove，就会把**正在跑的那条**的锁删掉 ⇒ 第三条又能进来 ⇒ 双跑。
    """
    path = os.path.join(str(tmp_path), "BHY_BEL.lock")
    with W.window_lock("BHY+BEL", "flux"):
        # 模拟"别人接管了我的锁"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999999, "key": "BHY+BEL", "action": "presale",
                       "started_at": time.time(), "started": "later"}, f)
    assert os.path.exists(path), "接管者的锁必须还在（不许偷走）"
    rec = json.loads(open(path, encoding="utf-8").read())
    assert rec["action"] == "presale"


# ------------------------------------------------------------------ 排查工具
def test_status_and_release(tmp_path):
    assert W.status() == []
    with W.window_lock("BHY+BEL", "flux"):
        rows = W.status()
        assert sorted(r["file"] for r in rows) == ["BEL", "BHY"]
        assert all(r["key"] == "BHY+BEL" for r in rows), "记事里留完整窗口键，便于排查"
        assert all(r["action"] == "flux" for r in rows)
        assert all(r["stale"] is False and r["alive"] is True for r in rows)
        assert W.release("BHY+BEL") is True
        assert W.status() == []
    assert W.release("BHY+BEL") is False, "放不存在的锁应返回 False，不抛"


def test_lock_files_sanitize_window_name():
    assert W.lock_files("BHY+BEL") == [os.path.join(str(W._LOCK_DIR), "BEL.lock"),
                                       os.path.join(str(W._LOCK_DIR), "BHY.lock")]
    assert W.stores_of("bhy+bel") == ("BEL", "BHY"), "大小写无关"
