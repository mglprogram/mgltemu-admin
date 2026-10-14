(() => {
  // 点列表分页的第 N 页（window.PAGE），并等待表格刷新
  const PAGE = String(window.PAGE);
  const dr = Array.from(document.querySelectorAll('.Drawer_outerWrapper_5-120-1'))
    .filter(d => d.className.includes('Drawer_visible') && d.getBoundingClientRect().width > 0);
  const pager = Array.from(document.querySelectorAll('[class*=pagination-wrapper],[class*=PGT_outerWrapper]'))
    .find(e => !dr.some(d => d.contains(e)));
  if (!pager) return JSON.stringify({ ok: false, msg: 'no pager' });
  const items = Array.from(pager.querySelectorAll('li,button,a,span'))
    .filter(e => (e.innerText || '').trim() === PAGE);
  if (!items.length) return JSON.stringify({ ok: false, msg: 'no page ' + PAGE,
    pages: Array.from(pager.querySelectorAll('li')).map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 8) });
  items[0].click();
  return JSON.stringify({ ok: true, page: PAGE });
})()