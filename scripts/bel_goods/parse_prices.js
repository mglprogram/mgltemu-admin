(() => {
  const rows = Array.from(document.querySelectorAll('tr'));
  const grab = (t, re) => {
    const m = (t || '').match(re);
    return (m && m[1] != null) ? String(m[1]).trim() : '';
  };

  const out = [];
  let cur = null;

  for (const tr of rows) {
    const tds = Array.from(tr.children);
    const cell = t => (t || '').trim();
    // 站点申报价/处理方式单元格是嵌套表，textContent 才连续
    const full = t => (t && t.textContent ? t.textContent : '');

    if (tds.length >= 10) {
      // 商品主行：cell4 = SKC 属性（SKC ID / 申报价格 / 货号）
      const skcCell = cell(tds[4] && tds[4].innerText);
      const infoCell = cell(tds[1] && tds[1].innerText);
      const priceCell = full(tds[6]);

      const skcId = grab(skcCell, /(\d{9,})/);
      if (!skcId) continue;

      const title = (infoCell.split('\n').find(l => l.length > 25) || '').trim();
      const spuId = grab(infoCell, /SPU[：:]\s*(\d+)/) || grab(infoCell, /(\d{10})/);
      const declarePrice = grab(skcCell, /申报价格[：:]\s*([\d.]+)\s*US/);
      const code = grab(skcCell, /货号[：:]\s*(\S+)/);
      const sitePrice = grab(priceCell, /([\d.]+)美元/);
      const siteStatus = grab(priceCell, /(已生效|待确认|未生效|已失效|审核中)/);
      // 日常申报价 / 活动申报价（cell6 文本连续，形如 "...日常申报价调整为22.21美元调价提效"）
      const daily = grab(priceCell, /日常申报价调整为([\d.]+)美元/);
      const activity = (priceCell.match(/活动申报价[^调]*?([\d.]+)美元/) || [])[1] || '';
      const state = grab(priceCell, /(商品流量待关注|商品流量近期有下降|商品流量预测将下降)/);

      cur = {
        skc_id: skcId, spu_id: spuId, title: title, code: code,
        declare_price: declarePrice, site_price: sitePrice,
        site_status: siteStatus, daily_target: daily,
        activity_target: activity, traffic_state: state,
        skus: []
      };

      // 主行 cell5 内嵌了第一个 SKU（其余在下方 2 列子行里）
      const firstSkuCell = cell(tds[5] && tds[5].innerText);
      if (firstSkuCell && /货号/.test(firstSkuCell)) {
        cur.skus.push({
          sku_id: grab(firstSkuCell, /(\d{9,})/),
          sku_code: grab(firstSkuCell, /货号[：:]\s*(\S+)/),
          spec: grab(firstSkuCell, /\n([^\d货\n]+)\n?货号/),
          site_price: sitePrice, site_status: siteStatus,
          daily_target: daily, activity_target: activity
        });
      }
      out.push(cur);
    } else if (tds.length === 2 && cur) {
      // SKU 子行：cell0 = SKU ID / 规格 / 货号；cell1 = 站点申报价 + 处理方式
      const skuCell = cell(tds[0] && tds[0].innerText);
      const priceCell = cell(tds[1] && tds[1].innerText);
      const skuId = grab(skuCell, /(\d{9,})/);
      const skuCode = grab(skuCell, /货号[：:]\s*(\S+)/);
      if (!skuId) continue;
      cur.skus.push({
        sku_id: skuId,
        sku_code: skuCode,
        spec: grab(skuCell, /\n([^\d\n]+)\n货号/),
        site_price: grab(priceCell, /([\d.]+)美元/),
        site_status: grab(priceCell, /(已生效|待确认|未生效|已失效|审核中)/),
        daily_target: grab(priceCell, /日常申报价调整为([\d.]+)美元/),
        activity_target: (priceCell.match(/活动申报价[^调]*?([\d.]+)美元/) || [])[1] || ''
      });
    }
  }
  return JSON.stringify({count: out.length, rows: out});
})()