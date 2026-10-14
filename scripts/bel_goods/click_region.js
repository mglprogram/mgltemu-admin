(() => {
  const out = {};

  // 1) 先找切店弹窗（D2：弹窗覆盖 authentication_goto，不先关会点击失败）
  const dlgText = (document.body.innerText || '');
  out.hasSwitchDialog = /确认切换|不切换|当前已有登录中的店铺/.test(dlgText);
  const dlgBtns = Array.from(document.querySelectorAll('button,[class*=btn],[class*=Btn],div'))
    .filter(e => e.offsetParent !== null && /^(确认切换|不切换|确认|取消)$/.test((e.innerText || '').trim()));
  out.dlgBtns = dlgBtns.map(e => (e.innerText || '').trim()).slice(0, 6);

  if (out.hasSwitchDialog && dlgBtns.length) {
    const btn = dlgBtns.find(e => (e.innerText || '').trim() === '确认切换') || dlgBtns[0];
    btn.click();
    return JSON.stringify({ step: 'dismiss-switch-dialog', clicked: (btn.innerText || '').trim() });
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