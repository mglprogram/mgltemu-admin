(() => {
  // 授权/登录确认按钮：文本因店而异（D3），候选逐个试
  const CANDS = ['确认授权并前往', '授权登录', '授权并前往', '确认授权', '确认登录'];
  const btns = Array.from(document.querySelectorAll('button,[class*=btn],[class*=Btn]'))
    .filter(e => e.offsetParent !== null && (e.innerText || '').trim());
  for (const txt of CANDS) {
    const btn = btns.find(e => (e.innerText || '').trim() === txt);
    if (btn) {
      const r = btn.getBoundingClientRect();
      const o = { bubbles: true, cancelable: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, button: 0, view: window };
      btn.dispatchEvent(new MouseEvent('mousedown', o));
      btn.dispatchEvent(new MouseEvent('mouseup', o));
      btn.dispatchEvent(new MouseEvent('click', o));
      return JSON.stringify({ ok: true, clicked: txt });
    }
  }
  return JSON.stringify({ ok: false, msg: 'no auth button', seen: btns.map(e => (e.innerText || '').trim().slice(0, 20)).slice(0, 10) });
})()