(() => {
  // 诊断限流表结构：行数、每行列数分布、是否有 SPU ID
  const all = Array.from(document.querySelectorAll('tr'));
  const withSpu = all.filter(tr => /SPU\s*ID/.test(tr.innerText || ''));
  const dist = {};
  withSpu.forEach(tr => {
    const n = tr.children.length;
    dist[n] = (dist[n] || 0) + 1;
  });
  const out = {
    url: location.href,
    totalTr: all.length,
    trWithSpu: withSpu.length,
    childCountDist: dist,
    totalText: (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1] || null,
    samples: withSpu.slice(0, 3).map(tr => ({
      n: tr.children.length,
      cells: Array.from(tr.children).map(td =>
        (td.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40)),
    })),
  };
  return JSON.stringify(out);
})()