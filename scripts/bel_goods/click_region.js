// 用法：`cdp_visit.py <port> <url> click_region.js`（该脚本按 `(<src>)()` 裸调用，不收参数）。
// 需要允许换店时，先在同一页设 `window.__TEMU_ALLOW_STORE_SWITCH = true`
// （对应 Python 侧环境变量 TEMU_ALLOW_STORE_SWITCH=1）。
(() => {
  const allowSwitch = !!window.__TEMU_ALLOW_STORE_SWITCH;
  const out = {};

  // 1) 先找切店弹窗（D2：弹窗覆盖 authentication_goto，不先关会点击失败）
  //
  // ⚠ 2026-11-05 改口径（用户：「执行 BEL 任务时所有页面登录态全部消失，要重新登录」）：
  //   旧实现找的是「确认切换」并**优先点它** —— 那等于让 Seller Central 换一个店铺登录，
  //   本窗口里**原店铺的所有标签页会同时掉登录**。这就是"莫名其妙要重新登录"的直接成因。
  //   ⇒ 默认只点「不切换/取消」把弹窗关掉（同样解除遮挡）；
  //     真要换店必须显式传 allowSwitch=true（对应环境变量 TEMU_ALLOW_STORE_SWITCH=1）。
  //     只有「确认切换」一颗可点时**什么都不点**，交由调用方决定。
  const dlgText = (document.body.innerText || '');
  out.hasSwitchDialog = /确认切换|不切换|当前已有登录中的店铺/.test(dlgText);
  const dlgBtns = Array.from(document.querySelectorAll('button,[class*=btn],[class*=Btn],div'))
    .filter(e => e.offsetParent !== null && /^(确认切换|不切换|确认|取消)$/.test((e.innerText || '').trim()));
  out.dlgBtns = dlgBtns.map(e => (e.innerText || '').trim()).slice(0, 6);

  if (out.hasSwitchDialog && dlgBtns.length) {
    const txt = e => (e.innerText || '').trim();
    const pick = allowSwitch
      ? (dlgBtns.find(e => txt(e) === '确认切换') || dlgBtns[0])
      : (dlgBtns.find(e => txt(e) === '不切换') || dlgBtns.find(e => txt(e) === '取消'));
    if (!pick) {
      return JSON.stringify({ step: 'switch-dialog-unsafe', dlgBtns: out.dlgBtns });
    }
    pick.click();
    return JSON.stringify({ step: 'dismiss-switch-dialog', clicked: txt(pick), switched: allowSwitch });
  }

  // 2) 点「中国地区·商家中心」：用完整指针序列，逐个候选元素试
  const cands = [
    document.querySelector('[class*="authentication_goto"]'),
    ...Array.from(document.querySelectorAll('[class*="authentication_regionItem"]'))
      .filter(e => !String(e.className).includes('disabled')),
    ...Array.from(document.querySelectorAll('[class*="authentication_regionSuffix"]')),
  ].filter(Boolean);

  out.candCount = cands.length;
  if (!cands.length) return JSON.stringify({ step: 'no-candidate', ...out });

  const fire = (el) => {
    const r = el.getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    const o = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window };
    el.dispatchEvent(new PointerEvent('pointerover', { ...o, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mouseover', o));
    el.dispatchEvent(new PointerEvent('pointerdown', { ...o, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mousedown', o));
    el.dispatchEvent(new PointerEvent('pointerup', { ...o, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
  };

  const tried = [];
  for (const el of cands) {
    fire(el);
    tried.push(String(el.className).slice(0, 40) + '|' + (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 20));
    if (!document.querySelector('[class*="authentication_goto"]')) break;   // 页面已跳走
  }
  return JSON.stringify({ step: 'clicked-region', tried, stillAuth: !!document.querySelector('[class*="authentication_goto"]') });
})()