(() => {
  // 通用快速筛选：把 window.FILTER__ 指定的卡片设为选中态（幂等）
  //
  // 选中态的真实标识（2026-10-10 实测）：
  //   选中卡片的子元素里会多一个 `quick-overdue-filter_clickIcon__*`，
  //   且 inline style 带橙色边框 `rgb(255, 103, 2)`。
  //   **不要**用 `getAttribute('style') !== null` 判断 —— 未选中的卡片
  //   也可能带一个空 style=""（会导致误判为已选中而不去点击，筛选切不过去）。
  const LABEL = window.FILTER;
  const cards = Array.from(document.querySelectorAll('.quick-overdue-filter_card__7x5wV'));
  const isOn = (c) => !!c.querySelector('[class*="clickIcon"]');
  const target = cards.find(c => (c.textContent || '').trim() === LABEL);
  if (!target) {
    return JSON.stringify({ ok: false, msg: 'no card: ' + LABEL,
      seen: cards.map(c => (c.textContent || '').trim()) });
  }
  if (isOn(target)) return JSON.stringify({ ok: true, action: 'already', label: LABEL });

  target.click();
  return JSON.stringify({ ok: true, action: 'clicked', label: LABEL });
})()