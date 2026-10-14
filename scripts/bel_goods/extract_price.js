(() => {
  // 抽屉关闭时旧节点不会从 DOM 移除，会同时存在多个；只取可见(visible 且尺寸>0)的那个
  const all = Array.from(document.querySelectorAll('.Drawer_outerWrapper_5-120-1'));
  const vis = all.filter(d => {
    if (!d.className.includes('Drawer_visible')) return false;
    const r = d.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  const dr = vis[vis.length - 1];
  if (!dr) return JSON.stringify({ err: 'no visible drawer', count: all.length,
    classes: all.map(d => String(d.className).slice(0, 60)) });

  function readTable(tb) {
    const grid = [];
    const trs = Array.from(tb.querySelectorAll('tbody tr'));
    const spans = [];
    trs.forEach((tr, r) => {
      if (!grid[r]) grid[r] = [];
      let c = 0;
      spans.forEach(s => { if (s.row >= r && s.col >= 0) { grid[r][s.col] = s.text; } });
      Array.from(tr.querySelectorAll('td')).forEach(td => {
        const rs = parseInt(td.getAttribute('rowspan') || '1', 10);
        const cs = parseInt(td.getAttribute('colspan') || '1', 10);
        const text = (td.innerText || '').trim();
        while (grid[r][c] !== undefined) c++;
        for (let i = 0; i < cs; i++) {
          grid[r][c + i] = text;
          for (let k = 1; k < rs; k++) spans.push({ row: r + k, col: c + i, text: text });
        }
        c += cs;
      });
    });
    return grid;
  }

  const txt = dr.innerText || '';
  const spu = (txt.match(/SPU ID：\s*(\d+)/) || [])[1] || null;
  const titleEl = Array.from(dr.querySelectorAll('div,span,p'))
    .find(e => (e.innerText || '').length > 20 && !e.children.length
               && (e.innerText||'').indexOf('SPU ID') < 0 && (e.innerText||'').indexOf('类目') < 0);
  const period = (txt.match(/流量加速时效：\s*([\d\-]{10} [\d:]{8}) ~ ([\d\-]{10} [\d:]{8})/) || []);
  const state = (txt.match(/流量加速状态：\s*(\S+)/) || [])[1] || null;
  const tb = Array.from(dr.querySelectorAll('table'))
    .find(t => (t.innerText || '').includes('原日常申报价格'));
  const skus = [];
  if (tb) {
    for (const row of readTable(tb)) {
      if (!row) continue;
      const skc = (row[0] || '').split('\n')[0].trim();
      const skuTxt = row[1] || '';
      const size = (skuTxt.match(/尺码：\s*(\S+)/) || [])[1] || null;
      const color = (skuTxt.match(/颜色：\s*(\S+)/) || [])[1] || null;
      const orig = (row[2] || '').trim();
      const after = (row[3] || '').trim();
      if (!orig && !after) continue;
      skus.push({ skc, color, size, orig, after });
    }
  }
  return JSON.stringify({ spu, title: titleEl ? titleEl.innerText.slice(0, 120) : null,
    periodFrom: period[1] || null, periodTo: period[2] || null,
    state, skuCount: skus.length, skus });
})()