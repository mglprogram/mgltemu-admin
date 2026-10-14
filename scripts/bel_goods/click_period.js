(() => {
  // 点销量分析页的时间胶囊（今日 / 近7日 / 近30日）
  const WANT = window.PERIOD;
  const tabs = Array.from(document.querySelectorAll('[class*=TAB_capsuleLabel]'));
  const t = tabs.find(x => (x.innerText || '').trim() === WANT);
  if (!t) return JSON.stringify({ ok: false, msg: 'no period tab ' + WANT,
    seen: tabs.map(x => (x.innerText || '').trim()) });
  const active = String(t.className).includes('TAB_active');
  if (active) return JSON.stringify({ ok: true, action: 'already', period: WANT });
  t.click();
  return JSON.stringify({ ok: true, action: 'clicked', period: WANT });
})()