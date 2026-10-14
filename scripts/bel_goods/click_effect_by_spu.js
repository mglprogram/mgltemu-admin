(() => {
  const SPU = String(window.SPU);
  // 只排除**可见**抽屉里的表格；关闭后的旧抽屉节点仍留在 DOM 里，不能按存在与否过滤
  const isVisible = (el) => {
    if (el.className && String(el.className).includes('Drawer_visible')) {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }
    return true;
  };
  const drawers = Array.from(document.querySelectorAll('.Drawer_outerWrapper_5-120-1')).filter(isVisible);
  const tables = Array.from(document.querySelectorAll('.TB_tableWrapper_5-120-1'))
    .filter(tb => !drawers.some(d => d.contains(tb)));
  let main = tables.find(tb => (tb.innerText || '').includes('查看效果'));
  if (!main) return JSON.stringify({ ok: false, msg: 'no list table', drawers: drawers.length, tables: tables.length });
  for (const tr of main.querySelectorAll('tbody tr')) {
    const m = (tr.innerText || '').match(/SPU ID：?\s*(\d+)/);
    if (!m || m[1] !== SPU) continue;
    const link = Array.from(tr.querySelectorAll('a,button')).find(b => (b.innerText||'').trim() === '查看效果');
    if (!link) return JSON.stringify({ ok: false, msg: 'no link' });
    link.scrollIntoView({ block: 'center', inline: 'nearest' });
    const r = link.getBoundingClientRect();
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    const covered = document.elementFromPoint(cx, cy);
    const info = { ok: true, spu: SPU, box: [Math.round(cx), Math.round(cy)],
      coveredBy: covered ? covered.tagName + '.' + String(covered.className).slice(0, 50) : null,
      isLink: covered === link || link.contains(covered) };
    link.click();
    return JSON.stringify(info);
  }
  return JSON.stringify({ ok: false, msg: 'row not found' });
})()