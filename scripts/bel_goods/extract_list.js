(() => {
  // 读取当前筛选结果的全部行，按列序解析成结构化字段。
  // 列序（26 列，index 0 为勾选框）已对照页面表头文本核实：
  //   1 商品信息 | 2 曝光量 3 点击量 4 访客数 5 浏览量 6 加购人数 7 收藏人数
  //   8 订单数 9 件数 10 买家数 11 商详支付买家数 12 商详支付转化率
  //   13 曝光订单转化率 14 点击率 15 点击订单转化率
  //   16-19 搜索(曝光量/点击量/支付订单数/支付件数) 20-23 推荐(同四列)
  //   24 商品优化方式 | 25 操作
  const dr = Array.from(document.querySelectorAll('.Drawer_outerWrapper_5-120-1'))
    .filter(d => d.className.includes('Drawer_visible') && d.getBoundingClientRect().width > 0);
  const tables = Array.from(document.querySelectorAll('.TB_tableWrapper_5-120-1'))
    .filter(tb => !dr.some(d => d.contains(tb)));
  const main = tables.find(tb => /查看效果|查看详情/.test(tb.innerText || '')) || tables[tables.length - 1];
  if (!main) return JSON.stringify({ err: 'no list table', tables: tables.length });

  const num = (s) => {
    const m = String(s || '').replace(/,/g, '').match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  };
  const pct = (s) => {
    const m = String(s || '').match(/([\d.]+)\s*%/);
    return m ? parseFloat(m[1]) : null;
  };

  const rows = [];
  for (const tr of main.querySelectorAll('tbody tr')) {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
    if (cells.length < 20) continue;
    const info = cells[1] || '';
    const m = info.match(/SPU ID：?\s*(\d+)/);
    if (!m) continue;
    const links = Array.from(tr.querySelectorAll('a,button'))
      .map(b => (b.innerText || '').trim()).filter(Boolean);
    const optimize = cells[24] || '';

    rows.push({
      spuId: m[1],
      title: info.split('\n')[0].trim(),
      category: (info.match(/类目：\s*\n?\s*([^\n]+)/) || [])[1] || '',
      site: (info.match(/经营站点：?\s*\n?\s*([^\n]+)/) || [])[1] || '',
      firstListed: (info.match(/首次加入站点日期：?\s*\n?\s*([\d-]+)/) || [])[1] || '',
      // 行内标签（如「商品流量待关注」「已暂时失去Best Seller同款权益」）
      tags: info.split('\n').slice(-1)[0].trim(),
      stats: {
        exposure: num(cells[2]), clicks: num(cells[3]), visitors: num(cells[4]),
        views: num(cells[5]), addCart: num(cells[6]), favorites: num(cells[7]),
        orders: num(cells[8]), pieces: num(cells[9]), buyers: num(cells[10]),
        detailBuyers: num(cells[11]), detailConv: pct(cells[12]),
        exposureOrderConv: pct(cells[13]), ctr: pct(cells[14]),
        clickOrderConv: pct(cells[15]),
        searchExposure: num(cells[16]), searchClicks: num(cells[17]),
        searchOrders: num(cells[18]), searchPieces: num(cells[19]),
        recExposure: num(cells[20]), recClicks: num(cells[21]),
        recOrders: num(cells[22]), recPieces: num(cells[23]),
      },
      optimize: optimize.replace(/\s+/g, ' ').trim(),
      // 只记录读操作入口，绝不记录/点击「立即开启」「报名流量加速器」这类写操作
      readOnlyLinks: links.filter(t => t === '查看效果' || t === '查看详情'),
      writeLinksPresent: links.filter(t => /立即开启|报名流量加速器|一键提效|确认开启/.test(t)),
    });
  }
  const total = (document.body.innerText.match(/共有\s*(\d+)\s*条/) || [])[1];
  return JSON.stringify({ total, count: rows.length, rows });
})()