(() => {
  // 抽销量分析表格：SKU 级行 + 销量列；同时报告行数与分页
  const wraps = Array.from(document.querySelectorAll('.TB_tableWrapper_5-120-1'));
  const body = wraps.slice().sort((a, b) =>
    b.querySelectorAll('tbody tr').length - a.querySelectorAll('tbody tr').length)[0];
  if (!body) return JSON.stringify({ err: 'no body table' });

  const num = (s) => {
    const m = String(s || '').replace(/,/g, '').match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  };

  const rows = [];
  for (const tr of body.querySelectorAll('tbody tr')) {
    const c = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').replace(/\s+/g, ' ').trim());
    if (c.length < 8) continue;
    const info = c[2] || '';
    rows.push({
      title: (c[1] || '').slice(0, 60),
      skuId: (info.match(/SKU ID：\s*(\d+)/) || [])[1] || null,
      skuCode: (info.match(/SKU货号：\s*([^\s]+)/) || [])[1] || null,
      spec: info.split('SKU ID')[0].trim(),
      skcSales: num(c[3]),
      skuSales: num(c[4]),
      f7: num(c[5]),
      f14: num(c[6]),
      stock: num(c[9]),
    });
  }

  // 分页信息
  const bt = (document.body.innerText || '').replace(/\s+/g, ' ');
  const total = (bt.match(/共\s*(\d+)\s*[条个]/) || [])[1] || null;
  const pg = document.querySelector('[class*=pagination],[class*=Pagination]');
  return JSON.stringify({
    rowCount: rows.length, total, pager: pg ? (pg.innerText || '').replace(/\s+/g, ' ').slice(0, 100) : null,
    sold: rows.filter(r => (r.skuSales || 0) >= 1).length,
    rows,
  });
})()