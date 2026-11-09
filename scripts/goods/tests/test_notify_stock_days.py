"""库存跟踪「可售 ≤N 天」预警的单元测试（不联网、不发消息）。

钉住的三条口径（2026-11-09 用户要求）：
  ① 总库存用 **lxTotal**（恒信可用 + 帕沃尔加州3 + Kansas），不是实际库存、不是领星 totalAmount；
  ② 可售天数 = 总库存 ÷ 7天日均，**等于**阈值也要提醒（≤ 不是 <）；
  ③ 九部/十部同一「款-套系-尺码」只出一条（总库存/日均取较大的，再重算天数）。
另外钉住通道：只读 FEISHU_WEBHOOK_STOCK，绝不回退到业务群的 FEISHU_WEBHOOK。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import notify_push  # noqa: E402
import notify_stock_days as N  # noqa: E402


def _payload():
    return {
        "generatedAt": "2026-11-09 17:31:00",
        "sources": {
            "nine": {
                "short": "九部",
                "styles": [
                    {"code": "204203", "variants": [
                        {"variant": "4SetA", "rows": [
                            # 总库存 70 / 日均 10 = 7.0 天 → 正好等于阈值，要提醒
                            {"code": "204203", "variant": "4SetA", "size": "S",
                             "stock": 999, "daily": 10.0, "lxTotal": 70},
                            # 总库存 800 / 日均 10 = 80 天 → 不提醒
                            {"code": "204203", "variant": "4SetA", "size": "M",
                             "stock": 800, "daily": 10.0, "lxTotal": 800},
                        ]},
                    ]},
                ],
            },
            "ten": {
                "short": "十部",
                "styles": [
                    {"code": "204203", "variants": [
                        {"variant": "4SetA", "rows": [
                            # 十部那边总库存 0 但日均 30 → 合并后 70/30 ≈ 2.3 天
                            {"code": "204203", "variant": "4SetA", "size": "S",
                             "stock": 0, "daily": 30.0, "lxTotal": 0},
                        ]},
                        {"variant": "4SetC", "rows": [
                            # 领星没匹配到（lxTotal=None）→ 退回实际库存 5，日均 2 → 2.5 天
                            {"code": "204203", "variant": "4SetC", "size": "L",
                             "stock": 5, "daily": 2.0, "lxTotal": None},
                            # 日均 0 → 跳过（没有销量就不算"卖得快断货"）
                            {"code": "204203", "variant": "4SetC", "size": "XL",
                             "stock": 0, "daily": 0, "lxTotal": 0},
                        ]},
                    ]},
                ],
            },
        },
    }


@pytest.fixture()
def fake_json(tmp_path, monkeypatch):
    p = tmp_path / "stock-track.json"
    p.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(N, "PUBLIC_JSON", p)
    return p


def test_le_threshold_and_dedup(fake_json):
    res = N.collect(7.0)
    got = {(i["code"], i["variant"], i["size"]): i for i in res["items"]}
    # 等于 7 天要提醒
    assert ("204203", "4SetA", "S") in got
    # 80 天不提醒
    assert ("204203", "4SetA", "M") not in got
    # 九部/十部合并成一条，总库存取较大（70），日均取较大（30）
    s = got[("204203", "4SetA", "S")]
    assert s["total"] == 70, s
    assert s["daily"] == 30.0, s
    assert s["days"] == pytest.approx(2.3, abs=0.05)
    # lxTotal=None 退回 stock
    assert got[("204203", "4SetC", "L")]["total"] == 5
    # 日均 0 不入榜
    assert ("204203", "4SetC", "XL") not in got


def test_sorted_by_days_then_daily(fake_json):
    items = N.collect(7.0)["items"]
    keys = [(i["days"], -i["daily"]) for i in items]
    assert keys == sorted(keys)


def test_render_has_all_rows_and_panel_link(fake_json):
    res = N.collect(7.0)
    title, body = N.render(res, 7.0)
    assert title.startswith("TEMU"), "群机器人关键词校验要求标题含 TEMU"
    for it in res["items"]:
        assert "%s-%s-%s" % (it["codeShow"], it["variant"], it["size"]) in body
    assert N.PANEL_URL in body
    assert "**" not in body, "群机器人 text 不解析 markdown"


def test_columns_align_by_display_width(fake_json):
    """表头与数据行必须等宽对齐。

    Python 的 `%-Ns` 按**字符数**补空格，而中文占 2 个显示宽度，
    所以表头（纯中文）会比数据行宽一截、数字列整体错位（2026-11-09 用户截图反馈）。
    这里按显示宽度校验：每行的「总库存 / 7天日均 / 可售」必须落在同一批列位上。
    """
    import unicodedata

    def w(s):
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    def col_starts(line: str) -> list[int]:
        """每一段连续非空格内容的起始显示列。"""
        out, pos = [], 0
        prev_space = True
        for ch in line:
            if ch != " " and prev_space:
                out.append(pos)
            prev_space = ch == " "
            pos += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        return out

    res = N.collect(7.0)
    _, body = N.render(res, 7.0)
    lines = body.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("货号"))
    head = lines[i]
    data = [ln for ln in lines[i + 1:] if ln[:1].isdigit()]
    assert data, "测试数据里应该有数据行"

    head_cols = col_starts(head)
    assert len(head_cols) == 4, head_cols
    for ln in data:
        cols = col_starts(ln)
        assert len(cols) == 4, (ln, cols)
        # 「行对齐」的定义：本行每个数值的**右端**落在表头同一列的右端 / 下一个间隙里。
        # （表头是左对齐的中文词、数据是右对齐的数字，右端对齐才是视觉上的一列；
        #   固定宽度的等宽字体里，stdout 打印出来是严格对齐的。）
        assert cols[1] >= head_cols[1] and cols[1] < head_cols[2], (ln, cols, head_cols)
        assert cols[2] >= head_cols[2] and cols[2] < head_cols[3], (ln, cols, head_cols)
        # 每行右端（天数）必须和表头右端对齐
        assert w(ln.rstrip()) == w(head.rstrip()), (ln, head)


def test_code_display_3040():
    assert N.code_show("3040") == "3040A"
    assert N.code_show("204203") == "204203"


def test_empty_result_says_so(fake_json):
    """阈值收到负数 → 一条都不该剩（S 合并后是 2.3 天）。"""
    res = N.collect(-1.0)
    assert res["items"] == []
    title, body = N.render(res, -1.0)
    assert title.startswith("TEMU")
    assert "没有" in body


def test_stock_alert_uses_its_own_webhook_only(monkeypatch):
    """库存预警群**不能**回退到业务群 —— 用户要求"只单独发这个"。"""
    used = {}

    def fake_push_feishu(title, msg, url=None, timeout=25, hook=""):
        used["hook"] = hook
        used["title"] = title
        return True, "OK"

    monkeypatch.setattr(notify_push, "push_feishu", fake_push_feishu)
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://hook/business")
    monkeypatch.delenv(notify_push.STOCK_WEBHOOK_ENV, raising=False)
    monkeypatch.setattr(notify_push, "stock_webhook", lambda: "")

    ok, why = notify_push.push_stock_alert("TEMU 库存不足", "x")
    assert not ok and notify_push.STOCK_WEBHOOK_ENV in why
    assert "hook" not in used, "没配预警群时不该往任何群发"

    monkeypatch.setattr(notify_push, "stock_webhook",
                        lambda: "https://hook/stock")
    ok, why = notify_push.push_stock_alert("TEMU 库存不足", "x")
    assert ok and used["hook"] == "https://hook/stock"


def test_stock_webhook_reads_own_env(monkeypatch):
    monkeypatch.setenv(notify_push.STOCK_WEBHOOK_ENV, "https://hook/stock")
    assert notify_push.stock_webhook() == "https://hook/stock"
