# -*- coding: utf-8 -*-
"""zreact.py — 直接驱动 React 组件的辅助层（2026-10-12 现场实测的可靠路径）。

两个踩过的坑（务必按这里的方式调用）
------------------------------------
1. **合成事件打不进 React**：agentseller.temu.com 是 qiankun 微前端，页面里
   `dispatchEvent(new MouseEvent('click'))`、CDP 真实鼠标、CLI page click 都**不会**
   触发按钮的 `onClick`（实测：不报错、不发请求、表格不变）。但节点上挂着 props：

       Object.keys(el) → '__reactProps$xxxx'  →  el[key].onClick(...)

   直接调用它，真实的 `/visage-agent-seller/product/skc/pageQuery` 就会发出去。

2. **不要用 `Runtime.evaluate` 跑带 IIFE 的表达式**：它会再包一层 `(expr)()`，
   返回值被"调用"掉（拿到 `{}` 或报 `not a function`）。
   ⇒ 用 `Runtime.callFunctionOn`：把逻辑写成**函数声明**，用 `objectId=window` 调它，
   返回值的语义是干净的。本模块的 `eval_fn()` 就是干这个的。

用法：
    from zreact import HELPERS, eval_fn
    await eval_fn(cap, "window.__zr.call", [el_js, "onClick"])   # 见 run_stock_topup 的实现
"""
from __future__ import annotations

import json
from typing import Any

#: 注入到页面的辅助函数（**语句**，不是表达式，所以用 evaluate 注入不会触发包装问题）
HELPERS = r"""
window.__zr = {
  findProps: function (el) {
    if (!el) return null;
    var keys = Object.keys(el).filter(function (k) { return k.indexOf('__reactProps') === 0; });
    for (var i = 0; i < keys.length; i++) {
      var p = el[keys[i]];
      if (p && typeof p === 'object') return p;
    }
    return null;
  },
  /* 直接调 React 处理器；没有 React props 就退回 DOM click（普通链接够用） */
  call: function (el, handler) {
    if (!el) return 'no-element';
    var props = window.__zr.findProps(el);
    if (props && typeof props[handler] === 'function') {
      try {
        props[handler]({target: el, currentTarget: el, bubbles: true,
                        stopPropagation: function () {}, preventDefault: function () {},
                        nativeEvent: {}});
        return 'react:' + handler;
      } catch (e) { return 'react-threw:' + String(e).slice(0, 120); }
    }
    if (handler === 'onClick') { el.click(); return 'dom-click'; }
    return 'no-handler:' + handler + ':' +
      (props ? Object.keys(props).slice(0, 8).join(',') : 'no-props');
  },
  /* 写下拉/输入框的受控值：走 React 的 onChange，保证状态真的更新 */
  write: function (el, value) {
    if (!el) return 'no-element';
    var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    set.call(el, value);
    var props = window.__zr.findProps(el);
    if (props && typeof props.onChange === 'function') {
      try {
        props.onChange({target: el, currentTarget: el, bubbles: true,
                        stopPropagation: function () {}, preventDefault: function () {}});
        return 'react:onChange=' + el.value;
      } catch (e) { return 'react-threw:' + String(e).slice(0, 120); }
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    return 'dom-input=' + el.value;
  },
  /* 在展开的浮层里按文本选一项（优先调它的 React onClick） */
  pick: function (openEl, want) {
    if (openEl) window.__zr.call(openEl, 'onClick');
    var all = Array.prototype.slice.call(document.querySelectorAll('div,li,span'));
    var opts = all.filter(function (e) {
      var t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      return t === want && e.getClientRects().length > 0;
    });
    opts = opts.filter(function (e) {
      return !opts.some(function (o) { return o !== e && e.contains(o); });
    });
    if (!opts.length) return 'no-option:' + want;
    return window.__zr.call(opts[opts.length - 1], 'onClick');
  },
  /**
   * 选「叶子节点」（文本完全等于 want 且不含其它文本的最深层元素）。
   *
   * 用于自定义 select 的浮层：选项文字往往和一堆兄弟节点（下拉里其它选项、页面其它
   * 数字）混在一起，`pick` 取「最外层」会选错；这里要求**没有子元素文本**才是叶子。
   * 同时把坐标带回来，方便调用方改用真实鼠标点（有些控件不吃合成事件）。
   */
  pickLeaf: function (openEl, want) {
    if (openEl) window.__zr.call(openEl, 'onClick');
    var all = Array.prototype.slice.call(document.querySelectorAll('div,li,span,button'));
    var o = [], rect = null;
    all.forEach(function (e) {
      var t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      if (t !== want) return;
      if (!e.getClientRects().length) return;
      var r = e.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) return;
      var kids = Array.prototype.slice.call(e.children);
      var hasTextKid = kids.some(function (k) {
        return ((k.innerText || '').trim().length > 0); });
      if (hasTextKid) return;                    /* 不是叶子 */
      o.push(e);
      if (!rect) rect = [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)];
    });
    if (!o.length) return JSON.stringify({ok: false, why: 'no-leaf:' + want});
    var how = window.__zr.call(o[o.length - 1], 'onClick');
    return JSON.stringify({ok: true, how: how, rect: rect, n: o.length,
                           cls: String(o[o.length - 1].className).slice(0, 40)});
  },
  /* 找出「浮层里所有纯数字叶子选项」——判断下拉到底给了哪些值 */
  numLeafs: function () {
    var all = Array.prototype.slice.call(document.querySelectorAll('div,li,span,button'));
    var out = [];
    all.forEach(function (e) {
      if (!e.getClientRects().length) return;
      var t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length > 4) return;
      for (var i = 0; i < t.length; i++) {
        var c = t.charAt(i);
        if (c < '0' || c > '9') return;
      }
      var kids = Array.prototype.slice.call(e.children);
      if (kids.some(function (k) { return ((k.innerText || '').trim().length > 0); })) return;
      var r = e.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) return;
      out.push({t: t, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                cls: String(e.className).slice(0, 34)});
    });
    return JSON.stringify(out.slice(0, 30));
  },
  /* 取可见输入框 / 按钮 */
  inputs: function () {
    return Array.prototype.slice.call(document.querySelectorAll('input'))
      .filter(function (i) { return i.getClientRects().length; });
  },
  btn: function (text) {
    return Array.prototype.slice.call(document.querySelectorAll('button'))
      .filter(function (b) { return (b.innerText || '').trim() === text; })[0] || null;
  }
};
'__zr_injected__'
"""


async def inject(cap) -> Any:
    """把 HELPERS 注入页面（幂等）。"""
    r = await cap.send("Runtime.evaluate", {"expression": HELPERS, "returnByValue": True},
                       timeout=30)
    return (r.get("result") or {}).get("value")


async def call_fn(cap, fn: str, args: list | None = None, timeout: float = 30.0) -> Any:
    """在页面里执行 `fn.apply(null, args)` 并返回结果（走 callFunctionOn，语义干净）。

    ⚠ `fn` 是**函数表达式**（如 `window.__zr.call`），`args` 是**数据**（JSON 可序列化）。
      想传「页面里算出来的东西」，用 `call_js`，别把 JS 源码当字符串塞进 args
      —— 那会被当成值原样传进去（实测 `el.click is not a function`）。
    """
    return await call_js(cap, "(%s).apply(null, %s)" % (fn, json.dumps(args or [],
                                                                     ensure_ascii=False)),
                         timeout=timeout)


async def call_js(cap, body: str, timeout: float = 30.0) -> Any:
    """在页面里跑一段**函数体** JS 并拿回返回值。

    例：`call_js(cap, "return window.__zr.call(window.__zr.inputs()[0], 'onClick');")`
    """
    r = await cap.send("Runtime.evaluate", {"expression": "window", "returnByValue": False},
                       timeout=timeout)
    oid = (r.get("result") or {}).get("objectId")
    if not oid:
        return "no-window-object"
    r2 = await cap.send(
        "Runtime.callFunctionOn",
        {"objectId": oid,
         "functionDeclaration": "function(){try{%s}catch(e){return 'THREW:' + String(e);}}"
                                % body,
         "returnByValue": True, "userGesture": True},
        timeout=timeout)
    exc = r2.get("exceptionDetails")
    if exc:
        return "EXC:" + json.dumps(exc, ensure_ascii=False)[:200]
    return (r2.get("result") or {}).get("value")
