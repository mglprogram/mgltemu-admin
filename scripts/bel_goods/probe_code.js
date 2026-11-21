(() => {
  const cards = Array.from(document.querySelectorAll('.lim-card'));
  const code = document.querySelector('.lim-card--code');
  const out = {
    cardOrder: cards.map(c => {
      const t = c.querySelector('.lim-card-title');
      return ((t ? t.innerText : c.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 26);
    }).slice(0, 6),
    hasCode: !!code,
  };
  if (code) {
    out.codeTitle = (code.querySelector('.lim-card-title') || {}).innerText || '';
    out.chips = Array.from(code.querySelectorAll('.lim-code-chip')).map(e => ({
      txt: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60),
      hit: e.className.includes('--hit'),
    }));
  }
  out.charts = document.querySelectorAll('.lim-charts, canvas').length;
  return JSON.stringify(out);
})()