(() => {
  const cards = Array.from(document.querySelectorAll('.quick-overdue-filter_card__7x5wV'));
  const out = {
    url: location.href,
    cards: cards.map(c => ({
      label: (c.textContent || '').trim(),
      on: !!c.querySelector('[class*="clickIcon"]'),
    })),
    totalText: (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1] || null,
  };
  const wraps = Array.from(document.querySelectorAll('.TB_tableWrapper_5-120-1'));
  out.wrapRows = wraps.map(w => w.querySelectorAll('tbody tr').length);
  const body = wraps.slice().sort((a, b) =>
    b.querySelectorAll('tbody tr').length - a.querySelectorAll('tbody tr').length)[0];
  if (body) {
    const tr = body.querySelector('tbody tr');
    out.firstRow = tr ? Array.from(tr.querySelectorAll('td'))
      .map(td => (td.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50)).slice(0, 6) : null;
  }
  // 空态提示
  const empty = document.querySelector('[class*=empty],[class*=Empty]');
  out.emptyText = empty ? (empty.innerText || '').replace(/\s+/g, ' ').slice(0, 80) : null;
  return JSON.stringify(out);
})()