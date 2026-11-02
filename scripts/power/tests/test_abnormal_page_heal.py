# -*- coding: utf-8 -*-
"""test_abnormal_page_heal.py — 异常单转换「页面卡死后换页继续」的契约。

来历（2026-11-02 实测，BEL）：
    一次跑 5 单，第 1 单成功后页面渲染进程就卡死，后面 4 单**全部**报
    `server rejected WebSocket connection: HTTP 500`，整批失败。

根因（三个叠加，都是"一次性对象"问题）：

  ① `page` 是 `convert_all()` **开头取的一份**，它死了以后所有单都复用它 ⇒ 全废。
  ② `CdpPage.evaluate` 默认超时 **60 秒**：页面卡死时每次调用都空等满 60s，
     一批 5 单白烧 5 分钟，最后还是报 HTTP 500。
  ③ 失败后的重试走「清浮层 → 再点一次」—— 但 HTTP 500 意味着 **targetId 已经死了**，
     再怎么点还是连那个死 id，第二次尝试纯浪费。

修法：
  · 每单前 `heal_page_if_needed()` 体检（`probe_page_alive`），死了就
    `replace_hung_page`（**先开新页再关死页**，否则窗口可能整个消失）换一张；
  · HTTP 500 / WebSocket 类错误**不**清浮层重试，直接换页重来；
  · 换页有上限 `abnormal.max_revive`（默认 3），避免无限重开；
  · `_js` 支持短超时（`abnormal.js_timeout`，默认 15s）快速失败。

实测：修复前 08:30 / 08:34 两次 pipeline 均 exit=42（HTTP 500）；
      修复后 08:48 一次跑完 43 单，全部转出，日志零 500。

运行: python -m pytest scripts/power/tests/test_abnormal_page_heal.py -q
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_abnormal_driver as tad  # noqa: E402

CFG_PATH = os.path.join(_POWER, "data", "temu_abnormal.json")


def _src():
    with open(os.path.join(_POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8") as f:
        return f.read()


def _convert_all_body():
    return _src().split("def convert_all", 1)[1].split("def _convert_one", 1)[0]


# ---------------------------------------------------------------- ① 每单前体检
def test_each_order_checks_page_health_before_converting():
    body = _convert_all_body()
    assert "heal_page_if_needed" in body, "缺换页自愈"
    # handle() 一开头就该体检，而不是只在异常分支里
    handle = body.split("def handle", 1)[1]
    head = handle.split("self._row_present", 1)[0]
    assert "heal_page_if_needed()" in head, "必须在读行之前就体检"


def test_heal_uses_probe_and_safe_replace():
    """判据用 probe_page_alive；替换必须走 replace_hung_page（先开新页再关死页）。"""
    body = _convert_all_body()
    assert "probe_page_alive" in body, "要用两层判据区分卡死/被关"
    assert "replace_hung_page" in body, "必须用安全替换（先开新页再关死页）"


def test_heal_is_bounded():
    """换页必须有上限，否则一个反复卡死的窗口会无限重开。"""
    body = _convert_all_body()
    assert "max_revive" in body
    assert "revived >= max_revive" in body


# ---------------------------------------------------------------- ② HTTP 500 直接换页
def test_http500_triggers_heal_not_popover_retry():
    """HTTP 500 = targetId 已死；清浮层再点还是连死 id，纯浪费。"""
    body = _convert_all_body()
    seg = body.split("except Exception as e:", 1)[1].split("if not err:", 1)[0]
    assert "HTTP 500" in seg, "要专门识别 HTTP 500"
    assert "heal_page_if_needed" in seg, "识别到就换页"


# ---------------------------------------------------------------- ③ JS 短超时
def test_js_has_short_timeout():
    """页面卡死时，60s 默认超时会让每单白等一分钟。"""
    src = _src()
    body = src.split("def _js", 1)[1].split("def shot", 1)[0]
    assert "timeout" in body, "_js 必须支持超时"
    assert "js_timeout" in body, "要能从配置读短超时"


def test_config_declares_heal_and_timeout():
    with open(CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    ab = cfg.get("abnormal") or {}
    assert ab, "配置里必须有 abnormal 块（此前代码读它但配置没有 ⇒ 改配置不生效）"
    for k in ("max_rounds", "max_orders", "max_revive", "js_timeout"):
        assert k in ab, "abnormal 缺 %s" % k
    assert int(ab["max_revive"]) >= 1
    assert 1 <= float(ab["js_timeout"]) <= 30, "短超时应在 1~30s（默认 60s 太久了）"


# ---------------------------------------------------------------- ④ 别退化成不重试
def test_single_failure_does_not_abort_batch():
    """单条失败照旧记账继续（用户 2026-09-22 口径：不能一遇错就停）。"""
    body = _convert_all_body()
    assert "failed.append" in body
    assert "记账后继续" in _src()
