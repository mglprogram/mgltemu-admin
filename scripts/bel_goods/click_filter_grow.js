(() => {
  const LABEL = '流量加速中';
  const cards = Array.from(document.querySelectorAll('.quick-overdue-filter_card__7x5wV'));
  const target = cards.find(c => (c.textContent || '').trim() === LABEL);
  if (!target) return JSON.stringify({ ok: false, msg: 'no card ' + LABEL });
  // 选中态：带 style 属性（未选中为 null）
  const already = target.getAttribute('style') !== null;
  if (already) return JSON.stringify({ ok: true, action: 'already', total: (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1] });
  target.click();
  return JSON.stringify({ ok: true, action: 'clicked' });
})()