(() => {
  // 列表表格 = 含「查看效果/一键提效」或表头含 SPU 的那张；排除抽屉内的表格
  const dr = document.querySelector('.Drawer_outerWrapper_5-120-1');
  const tables = Array.from(document.querySelectorAll('.TB_tableWrapper_5-120-1'))
    .filter(tb => !dr || !dr.contains(tb));
  let main = tables.find(tb => (tb.innerText || '').includes('查看效果') || (tb.innerText || '').includes('一键提效'));
  if (!main) main = tables[tables.length - 1];
  if (!main) return JSON.stringify({ err: 'no list table', tables: tables.length });
  const out = [];
  for (const tr of main.querySelectorAll('tbody tr')) {
    const txt = tr.innerText || '';
    const m = txt.match(/SPU ID：?\s*(\d+)/);
    if (!m) continue;
    const link = Array.from(tr.querySelectorAll('a,button')).find(b => (b.innerText||'').trim() === '查看效果');
    const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
    out.push({
      spu: m[1],
      title: (cells[1] || '').split('\n')[0].slice(0, 80),
      txt: txt.replace(/\n/g, ' | ').slice(0, 300),
      hasEffect: !!link,
    });
  }
  const total = (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1];
  return JSON.stringify({ total, count: out.length, rows: out });
})()